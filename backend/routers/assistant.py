"""
In-app AI assistant ("cowork" copilot) — agentic.

Design principles:
  * NEVER acts for the user: no form submit, no delete, no send. It can answer
    questions, route to a page (+pre-fill), point to where something is
    (highlight), and draw a small chart in the chat. The user always confirms.
  * Permission-aware: pages/actions are filtered by role (admin vs partner),
    enforced server-side (defense in depth on top of frontend route guards).
  * Data-aware: a role-scoped snapshot of the WHOLE platform (including pages
    the user is not currently on — PACs, supervision, partenaires, soumissions)
    plus exact pre-computed aggregates is injected, so the assistant answers
    real questions ("combien d'AOs ouverts ?") with real numbers.
  * Honest: when the answer is not in the data, it says so instead of
    deflecting to a list of pages.

Uses a dedicated OpenRouter key/model when configured (assistant_openrouter_key
/ assistant_model), otherwise falls back to the shared key. If no key or the
model errors, a deterministic intent parser computes answers (counts, moyennes,
répartitions) directly from the same snapshot, so the assistant stays useful.
"""
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from typing import Callable, Optional, Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from openai import AsyncOpenAI

from config import settings
from mip_rum_ai import record_ai_call, session_id_from_tracestate
from services import ai_ledger
from services.supabase_client import supabase
from services.ratelimit import rate_limit
from routers.auth import get_current_user

router = APIRouter(prefix="/assistant", tags=["assistant"])

_key = settings.assistant_openrouter_key or settings.openrouter_key
_client: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=_key, base_url="https://openrouter.ai/api/v1") if _key else None
)
MODEL = settings.assistant_model


# ── Capability catalog ─────────────────────────────────────────────
CAPABILITIES = [
    {"path": "/dashboard", "label": "Tableau de bord", "roles": ["admin", "commerce", "ao"], "prefill": []},
    {"path": "/aos", "label": "Liste des appels d'offres", "roles": ["admin", "commerce", "ao"], "prefill": []},
    {"path": "/consultants", "label": "Vivier de consultants", "roles": ["admin", "commerce", "ao"], "prefill": []},
    {"path": "/clients", "label": "Clients", "roles": ["admin", "commerce", "ao"], "prefill": []},
    {
        "path": "/consultants/new",
        "label": "Ajouter un consultant au vivier",
        "roles": ["admin", "ao"],
        "prefill": ["name", "skills", "tjm", "experience_years", "employment_type", "availability", "email", "phone"],
    },
    {
        "path": "/aos/new",
        "label": "Créer un appel d'offres",
        "roles": ["admin", "commerce"],
        "prefill": ["title", "description", "skills_required", "budget_max", "location", "duration", "context", "ao_type", "deadline"],
    },
    {"path": "/clients/new", "label": "Créer un client", "roles": ["admin"], "prefill": []},
    {"path": "/partners", "label": "Partenaires", "roles": ["admin", "commerce"], "prefill": []},
    {"path": "/partners-access", "label": "Accès partenaires", "roles": ["admin", "commerce"], "prefill": []},
    {"path": "/graph", "label": "Cartographie", "roles": ["admin", "commerce"], "prefill": []},
    {"path": "/pacs", "label": "PACs", "roles": ["admin"], "prefill": []},
    {"path": "/admin", "label": "Admin comptes Utilisateurs", "roles": ["admin"], "prefill": []},
    {"path": "/tickets", "label": "Tickets support", "roles": ["admin"], "prefill": []},
]


def _allowed(role: str) -> list[dict]:
    return [c for c in CAPABILITIES if role in c["roles"]]


def _page_label(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    for c in CAPABILITIES:
        if c["path"] == path:
            return c["label"]
    return None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    page: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict] = []


# ── Snapshot (role-scoped, whole platform) ─────────────────────────
def _norm(s: str) -> str:
    """Lowercase + strip accents, so 'Fermés' matches 'fermes'."""
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _q(table: str, cols: str, modify: Optional[Callable] = None, limit: int = 200) -> tuple[list, int]:
    """SELECT with an exact server-side count, so totals stay right even when
    the row sample is truncated by `limit`."""
    q = supabase.table(table).select(cols, count="exact")
    if modify:
        q = modify(q)
    r = q.limit(limit).execute()
    rows = r.data or []
    total = r.count if r.count is not None else len(rows)
    return rows, total


# Tiny TTL cache: the widget refetches on every message; the data barely moves.
_SNAP_TTL = 30
_snap_cache: dict[str, tuple[float, dict]] = {}


def _build_snapshot(user: dict) -> Optional[dict]:
    role = user.get("role", "ao")
    uid = user.get("sub", "")
    key = f"{role}:{uid}"
    hit = _snap_cache.get(key)
    if hit and time.monotonic() - hit[0] < _SNAP_TTL:
        return hit[1]

    try:
        snap: dict = {"role": role, "counts": {}}
        c = snap["counts"]

        if role in ("admin", "commerce"):
            snap["consultants"], c["consultants"] = _q(
                "consultants", "name, skills, experience_years, tjm, employment_type, availability")
            snap["aos"], c["aos"] = _q(
                "appels_offres", "title, status, ao_type, budget_max, client_id, deadline")
            _, c["aos_open"] = _q("appels_offres", "id", lambda q: q.eq("status", "open"), limit=1)
            snap["clients"], c["clients"] = _q("clients", "id, name, sector")
            snap["partners"], c["partners"] = _q("profiles", "id, name", lambda q: q.eq("role", "ao"))
            snap["access"], _ = _q("partner_clients", "partner_id, client_id, tier", limit=600)
            _, c["submissions"] = _q("submissions", "id", limit=1)
            try:
                m_rows, c["matchings"] = _q("matchings", "cost_usd", limit=1000)
                snap["matching_cost"] = round(sum(float(m.get("cost_usd") or 0) for m in m_rows), 2)
            except Exception:
                c["matchings"], snap["matching_cost"] = 0, 0.0
        else:
            snap["access"], _ = _q(
                "partner_clients", "partner_id, client_id, tier", lambda q: q.eq("partner_id", uid), limit=400)
            cids = [r["client_id"] for r in snap["access"] if r["tier"] in ("list_1", "list_2")]
            snap["consultants"], c["consultants"] = _q(
                "consultants", "name, skills, experience_years, tjm, employment_type, availability",
                lambda q: q.eq("created_by", uid))
            snap["clients"], c["clients"] = (
                _q("clients", "id, name, sector", lambda q: q.in_("id", cids)) if cids else ([], 0))
            snap["aos"], c["aos"] = (
                _q("appels_offres", "title, status, ao_type, budget_max, client_id, deadline",
                   lambda q: q.in_("client_id", cids)) if cids else ([], 0))
            c["aos_open"] = sum(1 for a in snap["aos"] if a.get("status") == "open")
            snap["partners"], c["partners"] = [], 0
            _, c["submissions"] = _q("submissions", "id", lambda q: q.eq("submitted_by", uid), limit=1)
            snap["matching_cost"] = None

        # Admin-only "hidden pages" (PACs, supervision). Each is best-effort:
        # a missing table must not take the whole snapshot down.
        if role == "admin":
            try:
                _, c["pacs"] = _q("pacs", "id", limit=1)
            except Exception:
                pass
            try:
                _, c["tickets_open"] = _q("support_messages", "id", lambda q: q.eq("status", "open"), limit=1)
            except Exception:
                pass
            try:
                profs, _ = _q("profiles", "role", limit=1000)
                by_role = {}
                for p in profs:
                    by_role[p.get("role") or "?"] = by_role.get(p.get("role") or "?", 0) + 1
                snap["accounts_by_role"] = by_role
            except Exception:
                pass
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                _, c["invitations_pending"] = _q(
                    "invitations", "id", lambda q: q.is_("used_at", "null").gt("expires_at", now_iso), limit=1)
            except Exception:
                pass

        _snap_cache[key] = (time.monotonic(), snap)
        return snap
    except Exception as e:
        print(f"[ASSISTANT] snapshot error: {e}")
        return None


def _parse_skills(s: Optional[str]) -> list[str]:
    return [x.strip() for x in re.split(r"[,;/]+", s or "") if x.strip()]


def _aggregates(snap: dict) -> dict:
    """Exact, pre-computed figures the assistant can quote directly."""
    aos, cons, clients = snap["aos"], snap["consultants"], snap["clients"]
    c = snap["counts"]
    agg: dict = {}

    agg["ao_total"] = c.get("aos", len(aos))
    agg["ao_open"] = c.get("aos_open", sum(1 for a in aos if a.get("status") == "open"))
    agg["ao_closed"] = max(agg["ao_total"] - agg["ao_open"], 0)
    by_type: dict[str, int] = {}
    for a in aos:
        t = a.get("ao_type") or "Non typé"
        by_type[t] = by_type.get(t, 0) + 1
    agg["ao_by_type"] = dict(sorted(by_type.items(), key=lambda kv: -kv[1]))
    budgets = [a["budget_max"] for a in aos if a.get("budget_max")]
    agg["ao_budget_avg"] = round(sum(budgets) / len(budgets)) if budgets else None
    today = date.today().isoformat()
    upcoming = sorted(
        (a["deadline"], a["title"]) for a in aos
        if a.get("deadline") and a.get("status") == "open" and str(a["deadline"]) >= today)
    agg["ao_next_deadline"] = upcoming[0] if upcoming else None

    agg["cons_total"] = c.get("consultants", len(cons))
    tjms = [x["tjm"] for x in cons if x.get("tjm")]
    agg["tjm_avg"] = round(sum(tjms) / len(tjms)) if tjms else None
    emp = {"independant": 0, "salarie": 0}
    for x in cons:
        if x.get("employment_type") in emp:
            emp[x["employment_type"]] += 1
    agg["cons_by_employment"] = emp
    buckets = {"0-2 ans": 0, "3-5 ans": 0, "6-9 ans": 0, "10+ ans": 0}
    for x in cons:
        y = x.get("experience_years") or 0
        k = "0-2 ans" if y <= 2 else "3-5 ans" if y <= 5 else "6-9 ans" if y <= 9 else "10+ ans"
        buckets[k] += 1
    agg["seniority"] = buckets
    skills: dict[str, int] = {}
    for x in cons:
        for s in _parse_skills(x.get("skills")):
            skills[s] = skills.get(s, 0) + 1
    for a in aos:
        for s in _parse_skills(a.get("skills_required")):
            skills[s] = skills.get(s, 0) + 1
    agg["top_skills"] = dict(sorted(skills.items(), key=lambda kv: -kv[1])[:8])

    agg["clients_total"] = c.get("clients", len(clients))
    by_sector: dict[str, int] = {}
    for cl in clients:
        s = cl.get("sector") or "Autre"
        by_sector[s] = by_sector.get(s, 0) + 1
    agg["clients_by_sector"] = dict(sorted(by_sector.items(), key=lambda kv: -kv[1]))

    agg["partners_total"] = c.get("partners", 0)
    agg["submissions_total"] = c.get("submissions", 0)
    agg["matchings_total"] = c.get("matchings")
    agg["matching_cost"] = snap.get("matching_cost")
    agg["pacs_total"] = c.get("pacs")
    agg["tickets_open"] = c.get("tickets_open")
    agg["accounts_by_role"] = snap.get("accounts_by_role")
    agg["invitations_pending"] = c.get("invitations_pending")
    return agg


def _fmt_dist(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in d.items()) if d else "—"


def _render_context(snap: dict, agg: dict) -> str:
    """Compact text context for the LLM: exact aggregates first, then a
    detail sample for entity-level questions."""
    role = snap["role"]
    parts = ["AGRÉGATS EXACTS (pré-calculés sur TOUTE la base — fiables, couvre aussi les pages non ouvertes) :"]
    line = (f"- Appels d'offres : {agg['ao_total']} au total · {agg['ao_open']} ouverts · "
            f"{agg['ao_closed']} fermés · par type : {_fmt_dist(agg['ao_by_type'])}")
    if agg["ao_budget_avg"]:
        line += f" · budget (TJM max) moyen {agg['ao_budget_avg']} €/j"
    if agg["ao_next_deadline"]:
        line += f" · prochaine deadline {agg['ao_next_deadline'][0]} (« {agg['ao_next_deadline'][1]} »)"
    parts.append(line)
    line = f"- Vivier : {agg['cons_total']} consultant(s)"
    if agg["tjm_avg"]:
        line += f" · TJM moyen {agg['tjm_avg']} €/j"
    emp = agg["cons_by_employment"]
    line += (f" · {emp['independant']} indépendant(s) / {emp['salarie']} salarié(s)"
             f" · séniorité : {_fmt_dist(agg['seniority'])}")
    parts.append(line)
    parts.append(f"- Top compétences (vivier + AOs) : {_fmt_dist(agg['top_skills'])}")
    parts.append(f"- Clients : {agg['clients_total']} · par secteur : {_fmt_dist(agg['clients_by_sector'])}")
    if role in ("admin", "commerce"):
        parts.append(f"- Partenaires : {agg['partners_total']} · Soumissions de CV : {agg['submissions_total']}")
        if agg["matchings_total"] is not None:
            parts.append(f"- Matching IA : {agg['matchings_total']} analyse(s) · coût total ${agg['matching_cost']}")
    else:
        parts.append(f"- Mes soumissions de CV : {agg['submissions_total']}")
    if role == "admin":
        extra = []
        if agg["pacs_total"] is not None:
            extra.append(f"PACs : {agg['pacs_total']}")
        if agg["tickets_open"] is not None:
            extra.append(f"tickets support ouverts : {agg['tickets_open']}")
        if agg["accounts_by_role"]:
            extra.append(f"comptes : {_fmt_dist(agg['accounts_by_role'])}")
        if agg["invitations_pending"] is not None:
            extra.append(f"invitations en attente : {agg['invitations_pending']}")
        if extra:
            parts.append("- Supervision : " + " · ".join(extra))

    cmap = {cl["id"]: cl["name"] for cl in snap["clients"]}

    def cons_line(x):
        sk = (x.get("skills") or "").strip()
        return f"{x.get('name')} [{sk[:60]} · {x.get('experience_years') or 0} ans · {x.get('tjm') or '?'}€/j · {x.get('employment_type') or '?'}]"

    def ao_line(a):
        st = "ouvert" if a.get("status") == "open" else "fermé"
        cl = cmap.get(a.get("client_id"), "?")
        dl = f" · deadline {a['deadline']}" if a.get("deadline") else ""
        return f"\"{a.get('title')}\" [{st} · {a.get('ao_type') or 'non typé'} · {a.get('budget_max') or '?'}€/j · {cl}{dl}]"

    parts.append("\nDÉTAIL (échantillon, max 40 par liste) :")
    parts.append(f"Consultants : " + ("; ".join(cons_line(x) for x in snap["consultants"][:40]) or "—"))
    parts.append(f"Appels d'offres : " + ("; ".join(ao_line(a) for a in snap["aos"][:40]) or "—"))
    parts.append("Clients : " + ("; ".join(f"{cl['name']} [{cl.get('sector') or '?'}]" for cl in snap["clients"][:40]) or "—"))
    if snap["partners"]:
        acc_count: dict[str, int] = {}
        for r in snap["access"]:
            if r["tier"] in ("list_1", "list_2"):
                acc_count[r["partner_id"]] = acc_count.get(r["partner_id"], 0) + 1
        parts.append("Partenaires : " + "; ".join(
            f"{p['name']} [{acc_count.get(p['id'], 0)} client(s)]" for p in snap["partners"][:40]))
    return "\n".join(parts)


def _build_system_prompt(role: str) -> str:
    role_label = {"admin": "administrateur", "commerce": "commercial UTI"}.get(role, "partenaire")
    caps = _allowed(role)
    catalog = "\n".join(
        f"- {c['path']} : {c['label']}" + (f" (pré-remplissable : {', '.join(c['prefill'])})" if c["prefill"] else "")
        for c in caps
    )
    return f"""Tu es l'assistant intégré de la plateforme partenaires UTI Group (matching IA consultants ↔ appels d'offres).
L'utilisateur est un {role_label}.

RÈGLES ABSOLUES :
1. Tu n'agis JAMAIS à sa place : aucune soumission de formulaire, aucun envoi, aucune suppression. Tu peux répondre, l'amener sur une page (+pré-remplir), lui MONTRER où se trouve une fonction, ou afficher un petit graphique. Il valide toujours lui-même.
2. Tu ne proposes QUE des chemins de la liste ci-dessous, autorisés pour son rôle.
3. RÉPONDS D'ABORD, NAVIGUE ENSUITE : pour toute question chiffrée ou factuelle, commence "reply" par la réponse elle-même (le chiffre, le nom, la date), calculée depuis les AGRÉGATS EXACTS et le DÉTAIL. Ne renvoie JAMAIS l'utilisateur vers une page à la place d'une réponse que tu possèdes. Les données couvrent TOUTE la plateforme, y compris les pages qu'il n'a pas ouvertes.
4. Comprends les questions de suivi grâce à l'historique : « et donc combien ? » se réfère au sujet du message précédent.
5. Sois honnête : si la réponse n'est pas dans les données, dis-le franchement (« Je n'ai pas cette information ») et propose au mieux. N'invente aucun chiffre.
6. Réponds en français, de façon concise et utile.

PAGES DISPONIBLES :
{catalog}

TU RÉPONDS UNIQUEMENT avec un objet JSON valide (sans markdown) :
{{"reply": "<message court>", "actions": [<0 à 3 actions>]}}

Types d'action possibles dans "actions" :
- {{"type": "navigate", "path": "<chemin>", "prefill": {{<champ: valeur>}} | null, "cta": "<libellé bouton>"}} → emmène l'utilisateur (quand il veut FAIRE quelque chose).
- {{"type": "highlight", "path": "<chemin>", "cta": "<libellé>"}} → met en évidence l'entrée de menu (quand il demande OÙ / COMMENT accéder à quelque chose, sans le déplacer).
- {{"type": "chart", "kind": "bar", "title": "<titre>", "data": [{{"name": "...", "value": <nombre>}}]}} → un graphique (max 8 entrées) quand il demande une répartition / comparaison / classement, calculé depuis les données.

- "actions" peut être vide [] pour une simple réponse.
- "prefill" : seulement les champs listés pour la page, et seulement si l'info est fournie. deadline = AAAA-MM-JJ ; employment_type = "independant" ou "salarie".
- Rappelle dans "reply" que l'utilisateur garde la main quand tu proposes une action."""


def _strip_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def _parse_json(text: str) -> Optional[dict]:
    raw = _strip_json(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _sanitize_actions(raw, role: str) -> list[dict]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    allowed = {c["path"]: c for c in _allowed(role)}
    out = []
    for a in raw[:3]:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t == "navigate":
            cap = allowed.get(a.get("path"))
            if not cap:
                continue
            prefill = a.get("prefill")
            cp = None
            if isinstance(prefill, dict) and cap["prefill"]:
                cp = {k: v for k, v in prefill.items() if k in cap["prefill"] and v not in (None, "")} or None
            out.append({"type": "navigate", "path": cap["path"], "prefill": cp, "cta": a.get("cta") or f"Ouvrir « {cap['label']} »"})
        elif t == "highlight":
            cap = allowed.get(a.get("path"))
            if not cap:
                continue
            out.append({"type": "highlight", "path": cap["path"], "cta": a.get("cta") or f"Voir « {cap['label']} » dans le menu"})
        elif t == "chart":
            kind = a.get("kind") if a.get("kind") in ("bar", "donut") else "bar"
            clean = []
            for d in (a.get("data") or [])[:8]:
                if isinstance(d, dict) and d.get("name") is not None:
                    try:
                        clean.append({"name": str(d["name"])[:40], "value": float(d.get("value"))})
                    except (TypeError, ValueError):
                        continue
            if clean:
                out.append({"type": "chart", "kind": kind, "title": str(a.get("title") or "")[:80], "data": clean})
    return out


# ── Deterministic data-aware engine (no LLM needed) ────────────────
_T_AO = re.compile(r"\baos?\b")
_COUNT_WORDS = ("combien", "nombre de", "nombre d", "nb de", "how many", "total de", "total d")
_AVG_WORDS = ("moyen", "moyenne", "average")
_CHART_WORDS = ("par type", "par secteur", "repartition", "distribution", "graphique", "diagramme",
                "chart", "classement", "statut des", "seniorite", "anciennete", "top competence",
                "competences demandees")
_LIST_WORDS = ("quels", "quelles", "lesquels", "lesquelles", "liste", "lister", "cite", "nomme")


def _topics(q: str) -> set[str]:
    t = set()
    if "appel" in q or "offre" in q or _T_AO.search(q):
        t.add("ao")
    if "consultant" in q or "vivier" in q or "profil" in q:
        t.add("cons")
    if "client" in q:
        t.add("client")
    if "partenaire" in q:
        t.add("partner")
    if "soumission" in q or "cv soumis" in q or "candidature" in q or "cvs" in q:
        t.add("subs")
    if re.search(r"\bpacs?\b", q):
        t.add("pacs")
    if "ticket" in q or "support" in q:
        t.add("tickets")
    if "matching" in q or "scoring" in q:
        t.add("matchings")
    if "compte" in q or "utilisateur" in q:
        t.add("accounts")
    if "invitation" in q:
        t.add("invitations")
    if "tjm" in q or "taux journalier" in q:
        t.add("tjm")
    if "budget" in q:
        t.add("budget")
    if "deadline" in q or "date limite" in q or "echeance" in q:
        t.add("deadline")
    if "secteur" in q:
        t.add("sector")
    if "competence" in q or "skill" in q:
        t.add("skills")
    return t


def _status_q(q: str) -> Optional[str]:
    has_open = "ouvert" in q or "open" in q or "en cours" in q
    has_closed = "ferme" in q or "clos" in q or "closed" in q
    if has_open and not has_closed:
        return "open"
    if has_closed and not has_open:
        return "closed"
    return None


def _chart_action(kind: str, title: str, dist: dict) -> dict:
    data = [{"name": k, "value": v} for k, v in list(dist.items())[:8]]
    return {"type": "chart", "kind": kind, "title": title, "data": data}


def _plural(n, sing, plur=None) -> str:
    return sing if n in (0, 1) else (plur or sing + "s")


def _data_answer(q_last: str, q_full: str, snap: dict, agg: dict, role: str) -> Optional[ChatResponse]:
    """Answer count / average / distribution / list questions from the
    snapshot. `q_last` is the user's last message (normalized); `q_full`
    includes previous turns for follow-ups like « et donc combien ? »."""
    topics = _topics(q_last) or _topics(q_full)
    q = q_last if _topics(q_last) else q_full
    status = _status_q(q_last) or _status_q(q_full)
    caps = {c["path"]: c for c in _allowed(role)}

    def nav(path, cta):
        return [{"type": "navigate", "path": path, "prefill": None, "cta": cta}] if path in caps else []

    wants_count = any(w in q_last for w in _COUNT_WORDS) or any(w in q_full for w in _COUNT_WORDS)
    wants_avg = any(w in q_last for w in _AVG_WORDS)
    wants_chart = any(w in q_last for w in _CHART_WORDS)
    wants_list = any(re.search(rf"\b{w}", q_last) for w in _LIST_WORDS)

    # Averages first ("TJM moyen", "budget moyen")
    if wants_avg or "tjm" in topics:
        if "tjm" in topics or ("cons" in topics and wants_avg):
            if agg["tjm_avg"] is None:
                return ChatResponse(reply="Aucun TJM renseigné dans le vivier pour le moment : je ne peux pas calculer de moyenne.")
            n = agg["cons_total"]
            return ChatResponse(
                reply=f"Le TJM moyen du vivier est de {agg['tjm_avg']} €/jour ({n} {_plural(n, 'consultant')}).",
                actions=nav("/consultants", "Voir le vivier"))
        if "budget" in topics and "ao" in topics:
            if agg["ao_budget_avg"] is None:
                return ChatResponse(reply="Aucun budget renseigné sur les appels d'offres : je ne peux pas calculer de moyenne.")
            return ChatResponse(
                reply=f"Le budget (TJM max) moyen des appels d'offres est de {agg['ao_budget_avg']} €/jour, sur {agg['ao_total']} AO.",
                actions=nav("/aos", "Voir les appels d'offres"))

    # Distributions → chart in the chat
    if wants_chart:
        if "ao" in topics and ("par type" in q or "type" in q):
            if not agg["ao_by_type"]:
                return ChatResponse(reply="Pas encore d'appel d'offres à répartir par type.")
            return ChatResponse(
                reply=f"Répartition des {agg['ao_total']} appels d'offres par type :",
                actions=[_chart_action("bar", "AOs par type", agg["ao_by_type"])])
        if "ao" in topics and ("statut" in q or status):
            return ChatResponse(
                reply=f"Statut des AO : {agg['ao_open']} ouverts, {agg['ao_closed']} fermés.",
                actions=[_chart_action("donut", "Statut des AO", {"Ouverts": agg["ao_open"], "Fermés": agg["ao_closed"]})])
        if "sector" in topics or ("client" in topics and "secteur" in q):
            if not agg["clients_by_sector"]:
                return ChatResponse(reply="Pas encore de clients à répartir par secteur.")
            return ChatResponse(
                reply=f"Répartition des {agg['clients_total']} clients par secteur :",
                actions=[_chart_action("donut", "Clients par secteur", agg["clients_by_sector"])])
        if "seniorite" in q or "anciennete" in q or "experience" in q:
            return ChatResponse(
                reply=f"Séniorité du vivier ({agg['cons_total']} {_plural(agg['cons_total'], 'consultant')}) :",
                actions=[_chart_action("bar", "Séniorité du vivier", agg["seniority"])])
        if "skills" in topics:
            if not agg["top_skills"]:
                return ChatResponse(reply="Pas encore de compétences renseignées.")
            return ChatResponse(
                reply="Top compétences (vivier + AOs) :",
                actions=[_chart_action("bar", "Top compétences", agg["top_skills"])])

    # Counts
    if wants_count and topics:
        if "ao" in topics:
            if status == "open":
                n = agg["ao_open"]
                word = "appel d'offres ouvert" if n in (0, 1) else "appels d'offres ouverts"
                return ChatResponse(
                    reply=f"Vous avez {n} {word} (sur {agg['ao_total']} au total).",
                    actions=nav("/aos", "Voir les appels d'offres"))
            if status == "closed":
                n = agg["ao_closed"]
                word = "appel d'offres est fermé" if n in (0, 1) else "appels d'offres sont fermés"
                return ChatResponse(
                    reply=f"{n} {word} (sur {agg['ao_total']} au total).",
                    actions=nav("/aos", "Voir les appels d'offres"))
            n = agg["ao_total"]
            word = "appel d'offres" if n in (0, 1) else "appels d'offres"
            return ChatResponse(
                reply=f"{n} {word} au total : {agg['ao_open']} ouverts, {agg['ao_closed']} fermés.",
                actions=nav("/aos", "Voir les appels d'offres"))
        if "cons" in topics:
            n = agg["cons_total"]
            extra = f" TJM moyen : {agg['tjm_avg']} €/j." if agg["tjm_avg"] else ""
            return ChatResponse(
                reply=f"Le vivier compte {n} {_plural(n, 'consultant')}.{extra}",
                actions=nav("/consultants", "Voir le vivier"))
        if "client" in topics:
            n, ns = agg["clients_total"], len(agg["clients_by_sector"])
            return ChatResponse(
                reply=f"Vous avez {n} {_plural(n, 'client')}" + (f", répartis sur {ns} {_plural(ns, 'secteur')}." if ns else "."),
                actions=nav("/clients", "Voir les clients"))
        if "partner" in topics and role in ("admin", "commerce"):
            n = agg["partners_total"]
            return ChatResponse(
                reply=f"{n} {_plural(n, 'partenaire')} {_plural(n, 'est enregistré', 'sont enregistrés')} sur la plateforme.",
                actions=nav("/partners", "Voir les partenaires"))
        if "subs" in topics:
            n = agg["submissions_total"]
            who = "ont été soumises" if role in ("admin", "commerce") else "avez soumises"
            prefix = "" if role in ("admin", "commerce") else "Vous "
            return ChatResponse(reply=f"{prefix}{n} {_plural(n, 'candidature (CV)', 'candidatures (CVs)')} {who}.")
        if "pacs" in topics and agg["pacs_total"] is not None:
            return ChatResponse(reply=f"{agg['pacs_total']} {_plural(agg['pacs_total'], 'PAC')} sur la plateforme.",
                                actions=nav("/pacs", "Voir les PACs"))
        if "tickets" in topics and agg["tickets_open"] is not None:
            return ChatResponse(reply=f"{agg['tickets_open']} {_plural(agg['tickets_open'], 'ticket support ouvert', 'tickets support ouverts')}.",
                                actions=nav("/admin", "Ouvrir la supervision"))
        if "matchings" in topics and agg["matchings_total"] is not None:
            return ChatResponse(reply=f"{agg['matchings_total']} {_plural(agg['matchings_total'], 'analyse de matching IA', 'analyses de matching IA')} (coût total ${agg['matching_cost']}).")
        if "accounts" in topics and agg["accounts_by_role"]:
            total = sum(agg["accounts_by_role"].values())
            return ChatResponse(
                reply=f"{total} comptes : {_fmt_dist(agg['accounts_by_role'])}.",
                actions=nav("/admin", "Ouvrir la supervision"))
        if "invitations" in topics and agg["invitations_pending"] is not None:
            return ChatResponse(reply=f"{agg['invitations_pending']} {_plural(agg['invitations_pending'], 'invitation en attente', 'invitations en attente')}.")

    # Next deadline
    if "deadline" in topics and "ao" in topics or ("deadline" in topics and not topics - {"deadline"}):
        if agg["ao_next_deadline"]:
            d, title = agg["ao_next_deadline"]
            return ChatResponse(reply=f"Prochaine deadline : {d} pour « {title} ».", actions=nav("/aos", "Voir les appels d'offres"))
        return ChatResponse(reply="Aucune deadline à venir sur les AOs ouverts.")

    # Lists ("quels AOs ouverts ?", "liste des clients")
    if wants_list:
        if "ao" in topics:
            rows = snap["aos"]
            if status:
                rows = [a for a in rows if (a.get("status") == "open") == (status == "open")]
            if not rows:
                return ChatResponse(reply="Aucun appel d'offres ne correspond.", actions=nav("/aos", "Voir les appels d'offres"))
            names = "; ".join(f"« {a['title']} »" for a in rows[:8])
            suffix = f" (+{len(rows) - 8} autres)" if len(rows) > 8 else ""
            label = {"open": "ouverts", "closed": "fermés"}.get(status, "")
            return ChatResponse(reply=f"AOs {label} ({len(rows)}) : {names}{suffix}.".replace("  ", " "),
                                actions=nav("/aos", "Voir les appels d'offres"))
        if "client" in topics:
            rows = snap["clients"]
            if not rows:
                return ChatResponse(reply="Aucun client pour le moment.")
            names = ", ".join(cl["name"] for cl in rows[:10])
            suffix = f" (+{len(rows) - 10} autres)" if len(rows) > 10 else ""
            return ChatResponse(reply=f"Clients ({agg['clients_total']}) : {names}{suffix}.", actions=nav("/clients", "Voir les clients"))
        if "cons" in topics:
            rows = snap["consultants"]
            if not rows:
                return ChatResponse(reply="Le vivier est vide pour le moment.", actions=nav("/consultants/new", "Ajouter un consultant"))
            names = ", ".join(x["name"] for x in rows[:10])
            suffix = f" (+{len(rows) - 10} autres)" if len(rows) > 10 else ""
            return ChatResponse(reply=f"Consultants ({agg['cons_total']}) : {names}{suffix}.", actions=nav("/consultants", "Voir le vivier"))
        if "partner" in topics and snap["partners"]:
            names = ", ".join(p["name"] for p in snap["partners"][:10])
            return ChatResponse(reply=f"Partenaires ({agg['partners_total']}) : {names}.", actions=nav("/partners", "Voir les partenaires"))

    # Bare count question with no recognizable topic
    if wants_count and not topics:
        return ChatResponse(reply="Combien de quoi ? Je peux compter vos AOs (ouverts/fermés), consultants, clients"
                                  + (", partenaires, soumissions, PACs ou tickets" if role == "admin"
                                     else (", partenaires ou soumissions" if role == "commerce" else " ou soumissions")) + ".")
    return None


# ── Deterministic fallback (no LLM key / model error) ──────────────
def _fallback(messages: list[ChatMessage], role: str, snap: Optional[dict]) -> ChatResponse:
    users = [m.content for m in messages if m.role == "user"]
    last_raw = users[-1] if users else ""
    q_last = _norm(last_raw)
    q_full = _norm(" ".join(users[-3:]))
    caps = {c["path"]: c for c in _allowed(role)}

    def nav(path, cta):
        return [{"type": "navigate", "path": path, "prefill": None, "cta": cta}] if path in caps else []

    # 1) Explicit create intents
    if role in ("admin", "commerce") and any(w in q_last for w in ["creer un ao", "nouvel ao", "nouvel appel", "creer un appel"]):
        return ChatResponse(reply="Je vous amène au formulaire d'appel d'offres. Vous validez vous-même.", actions=nav("/aos/new", "Ouvrir le formulaire d'AO"))
    if any(w in q_last for w in ["ajouter un consultant", "nouveau consultant", "ajouter au vivier"]):
        return ChatResponse(reply="Direction l'ajout de consultant. Vérifiez puis validez vous-même.", actions=nav("/consultants/new", "Ouvrir le formulaire consultant"))

    # 2) Data questions — answered with real numbers from the snapshot
    if snap is not None:
        try:
            ans = _data_answer(q_last, q_full, snap, _aggregates(snap), role)
            if ans is not None:
                return ans
        except Exception as e:
            print(f"[ASSISTANT] data answer error: {e}")

    # 3) "Où / comment" → highlight in the menu
    if any(w in q_last for w in ["ou est", "ou sont", "ou trouver", "ou puis-je", "ou creer", "ou ajouter", "comment acceder", "comment trouver"]):
        for keys, path in [(["appel", "ao", "offre"], "/aos/new" if "creer" in q_last else "/aos"),
                           (["consultant", "vivier"], "/consultants/new" if "ajouter" in q_last else "/consultants"),
                           (["client"], "/clients"), (["partenaire"], "/partners"),
                           (["carto", "graph"], "/graph"), (["pac"], "/pacs"), (["supervision", "ticket", "compte"], "/admin")]:
            if path in caps and any(k in q_last for k in keys):
                cap = caps[path]
                return ChatResponse(reply=f"C'est ici : « {cap['label']} ». Je vous montre l'entrée dans le menu.",
                                    actions=[{"type": "highlight", "path": path, "cta": f"Voir « {cap['label']} »"}])

    # 4) Plain navigation keywords
    nav_map = [
        (["appel", "offre"], "/aos", "Voir les appels d'offres"),
        (["vivier", "consultant"], "/consultants", "Voir le vivier"),
        (["client"], "/clients", "Voir les clients"),
        (["carto", "graph", "reseau"], "/graph", "Ouvrir la cartographie"),
        (["partenaire", "acces"], "/partners", "Voir les partenaires"),
        (["tableau", "dashboard", "accueil"], "/dashboard", "Aller au tableau de bord"),
        (["pac"], "/pacs", "Voir les PACs"),
        (["supervision", "ticket", "admin"], "/admin", "Ouvrir la supervision"),
    ]
    for keys, path, cta in nav_map:
        if path in caps and (any(k in q_last for k in keys) or _T_AO.search(q_last) and path == "/aos"):
            return ChatResponse(reply=f"Je peux vous y emmener. {cta} ?", actions=nav(path, cta))

    # 5) Honest default — say what we can actually do instead of deflecting
    if snap is None:
        return ChatResponse(reply="Je n'arrive pas à accéder aux données en ce moment. Je peux quand même vous guider vers une page : dites-moi laquelle.")
    return ChatResponse(
        reply="Je n'ai pas trouvé la réponse dans vos données, désolé. Je sais compter vos AOs, consultants, clients"
              + (", partenaires, PACs et tickets" if role == "admin" else (" et partenaires" if role == "commerce" else ""))
              + ", calculer des moyennes (TJM, budget), afficher des répartitions, ou vous emmener sur une page. Reformulez ou précisez ?",
        actions=[],
    )


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit(20, 60))])
async def chat(body: ChatRequest, request: Request, user: dict = Depends(get_current_user)):
    role = user.get("role", "ao")
    session_id = session_id_from_tracestate(request.headers.get("tracestate"))
    snap = _build_snapshot(user)
    if _client is None or not body.messages:
        return _fallback(body.messages, role, snap)

    convo = [{"role": "system", "content": _build_system_prompt(role)}]
    if snap is not None:
        convo.append({"role": "system", "content": _render_context(snap, _aggregates(snap))})
    page_label = _page_label(body.page)
    if body.page:
        convo.append({"role": "system", "content": f"Page actuelle de l'utilisateur : {body.page}" + (f" ({page_label})" if page_label else "")})
    convo += [{"role": m.role, "content": m.content} for m in body.messages[-10:]]

    try:
        with record_ai_call(provider="openrouter", model=MODEL, route="assistant/chat", session_id=session_id) as _call:
            resp = await _client.chat.completions.create(model=MODEL, messages=convo, temperature=0.3, max_tokens=900,
                                                         extra_body=ai_ledger.OR_USAGE)
            _u = getattr(resp, "usage", None)
            if _u:
                _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                            output_tokens=getattr(_u, "completion_tokens", None),
                            cost=getattr(_u, "cost", None))
        ai_ledger.record(provider="openrouter", model=MODEL, operation="assistant", resp=resp,
                         user_id=user.get("sub"), user_email=user.get("email"),
                         entity_type="assistant")
        data = _parse_json(resp.choices[0].message.content or "")
        if data is None:
            print("[ASSISTANT] model output was not valid JSON — falling back")
            return _fallback(body.messages, role, snap)
        reply = (data.get("reply") or "").strip() or "Comment puis-je vous aider ?"
        actions = _sanitize_actions(data.get("actions", data.get("action")), role)
        return ChatResponse(reply=reply, actions=actions)
    except Exception as e:
        print(f"[ASSISTANT] falling back (LLM error): {e}")
        return _fallback(body.messages, role, snap)
