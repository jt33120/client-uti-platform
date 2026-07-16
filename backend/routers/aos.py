import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional
from services.supabase_client import supabase
from services.cv_parser import extract_text_from_pdf, extract_text_from_docx, extract_text_from_xlsx
from services import ao_drafter, storage, notifications, ai_ledger
from services.app_settings import get_notification_settings
from services.matching_runner import run_vivier_matching
from services.ratelimit import rate_limit
from routers.auth import get_current_user, require_staff, is_staff
from routers.scoring_config import AOScoringOverrides

router = APIRouter(prefix="/aos", tags=["appels_offres"])

AO_SOURCES_BUCKET = "ao-sources"  # pièces jointes d'origine d'un AO (privé)

# Issue de clôture d'un AO (bilan) — vocabulaire partagé écriture (bilan) / lecture
# (pipeline, supervision). winning_partner_id porte QUI a gagné (NULL = pourvu hors
# plateforme ou non pourvu).
AO_OUTCOMES = ("pourvu", "non_pourvu", "sans_suite")

# Étapes du pipeline (avancement des AO), dans l'ordre d'affichage. Dérivées :
# backlog (créé, pas diffusé) -> diffusion -> réponses reçues -> présenté client
# -> gagné / perdu (issue ao_outcome, repli agrégation deal_status).
PIPELINE_STAGES = [
    {"key": "backlog", "label": "Créé / non diffusé"},
    {"key": "diffusion", "label": "Diffusion"},
    {"key": "reponses_recues", "label": "Réponses reçues"},
    {"key": "presente_client", "label": "Présenté client"},
    {"key": "gagne", "label": "Gagné"},
    {"key": "perdu", "label": "Perdu / Sans suite"},
]


def _sources_with_urls(items: Optional[list]) -> list:
    """Ajoute une URL signée (temporaire) à chaque pièce jointe stockée."""
    out = []
    for it in items or []:
        url = None
        try:
            url = storage.signed_url(AO_SOURCES_BUCKET, it.get("path"), 3600)
        except Exception:
            pass
        out.append({**it, "url": url})
    return out


async def _generate_and_store_summary(ao_id: str):
    """Tâche de fond : génère le résumé IA d'un AO et le stocke (best-effort)."""
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
        if not ao:
            return
        summary = await ao_drafter.summarize_ao(ao)
        if not summary:
            return
        try:
            supabase.table("appels_offres").update({"ai_summary": summary}).eq("id", ao_id).execute()
        except Exception:
            pass  # colonne ai_summary pas encore migrée
    except Exception as e:
        print(f"[AO] résumé IA échoué pour {ao_id}: {e}")


async def _geocode_and_store_ao(ao_id: str, location: Optional[str], work_mode: Optional[str]):
    """Tâche de fond : géocode la localisation d'un AO (sauf full remote) et la stocke."""
    if work_mode == "remote" or not location:
        return
    try:
        from services.geocoding import geocode
        geo = await geocode(location)
        if not geo:
            return
        try:
            supabase.table("appels_offres").update(
                {"latitude": geo["latitude"], "longitude": geo["longitude"]}
            ).eq("id", ao_id).execute()
        except Exception:
            pass  # colonnes géo pas encore migrées
    except Exception as e:
        print(f"[AO] géocodage échoué pour {ao_id}: {e}")


def _overrides_for_storage(ov: Optional[AOScoringOverrides]) -> Optional[dict]:
    """Valide la cohérence des seuils d'un override d'AO et renvoie le dict à stocker."""
    if ov is None:
        return None
    if ov.reco_fort_min is not None and ov.reco_moyen_min is not None \
            and ov.reco_fort_min <= ov.reco_moyen_min:
        raise HTTPException(
            status_code=422,
            detail="Le seuil FORT doit être strictement supérieur au seuil MOYEN.",
        )
    return ov.to_storage()


AO_TYPES = [
    "Assurance",
    "Banque / Finance",
    "IT / Dev",
    "Énergie",
    "Retail",
    "Public",
    "Santé",
    "Autre",
]

# Champs affichés sur la carte AO (AOCard, front) : requis pour PUBLIER (pas
# pour un brouillon) — sinon les cartes affichent une information partielle et
# des hauteurs différentes selon les AO. Un brouillon reste volontairement
# incomplet le temps d'être complété ; rien ne doit rester à moitié rempli une
# fois publié (visible des partenaires).
_PUBLISH_REQUIRED_FIELDS = {
    "reference": "Référence",
    "ao_type": "Type d'AO",
    "deadline": "Date limite de réponse",
    "budget_max": "Budget max",
    "location": "Localisation",
    "duration": "Durée",
}


def _missing_publish_fields(record: dict) -> list[str]:
    return [label for key, label in _PUBLISH_REQUIRED_FIELDS.items() if not record.get(key)]


class AOCreate(BaseModel):
    client_id: str
    title: str
    description: str
    skills_required: str
    reference: Optional[str] = None  # référence client / de la consultation
    budget_max: Optional[int] = Field(default=None, ge=0, le=100_000)  # TJM max €/j
    location: Optional[str] = None
    duration: Optional[str] = None
    context: Optional[str] = None
    ao_type: Optional[str] = None
    deadline: Optional[str] = None  # date limite de réponse (YYYY-MM-DD)
    work_mode: Optional[str] = None  # onsite | hybrid | remote
    langue_requise: Optional[str] = None  # langue exigée par le client (ex. "Anglais courant")
    scoring_overrides: Optional[AOScoringOverrides] = None  # priorités de matching propres à l'AO
    is_draft: bool = False  # brouillon : invisible partenaires + non matché jusqu'à publication


class AOUpdate(BaseModel):
    client_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    skills_required: Optional[str] = None
    reference: Optional[str] = None  # référence client / de la consultation
    budget_max: Optional[int] = Field(default=None, ge=0, le=100_000)
    location: Optional[str] = None
    duration: Optional[str] = None
    context: Optional[str] = None
    ao_type: Optional[str] = None
    deadline: Optional[str] = None  # date limite de réponse (YYYY-MM-DD)
    status: Optional[str] = None
    work_mode: Optional[str] = None
    langue_requise: Optional[str] = None
    scoring_overrides: Optional[AOScoringOverrides] = None


def _accessible_client_ids(user: dict) -> Optional[list[str]]:
    """
    Returns the list of client_ids a partner can see, or None for admin (= all).
    Suspended access is excluded.
    """
    if is_staff(user):
        return None
    access = supabase.table("partner_clients").select("client_id").eq(
        "partner_id", user["sub"]
    ).in_("tier", ["list_1", "list_2"]).execute()
    return [row["client_id"] for row in (access.data or [])]


def _looks_like_missing_archive(err: Exception) -> bool:
    """Colonne `archived` absente (migration 0003 non appliquée) — sert à donner
    un message d'erreur clair sur les actions explicites archive/désarchive."""
    s = str(err).lower()
    return "archived" in s and any(
        k in s for k in ("column", "42703", "does not exist", "schema cache", "pgrst204")
    )


def _responded_ao_ids(uid: str) -> list[str]:
    """ao_id distincts auxquels ce partenaire a soumis au moins un CV (historique)."""
    try:
        rows = supabase.table("submissions").select("ao_id").eq(
            "submitted_by", uid).execute().data or []
    except Exception:  # noqa: BLE001
        return []
    return list({r["ao_id"] for r in rows if r.get("ao_id")})


def _looks_like_missing_outcome(err: Exception) -> bool:
    """Colonnes d'issue (migration 0004) absentes — message d'erreur clair."""
    s = str(err).lower()
    return any(k in s for k in ("ao_outcome", "winning_partner_id", "outcome_at")) and any(
        k in s for k in ("column", "42703", "does not exist", "schema cache", "pgrst204")
    )


def _looks_like_missing_draft(err: Exception) -> bool:
    """Colonne is_draft (migration 0005) absente."""
    s = str(err).lower()
    return "is_draft" in s and any(
        k in s for k in ("column", "42703", "does not exist", "schema cache", "pgrst204")
    )


def _winner_submitters(ao_id: str) -> set:
    """profiles.id des partenaires ayant répondu à cet AO (= submitted_by valides)."""
    try:
        rows = supabase.table("submissions").select("submitted_by").eq("ao_id", ao_id).execute().data or []
    except Exception:  # noqa: BLE001
        return set()
    return {r["submitted_by"] for r in rows if r.get("submitted_by")}


def _derive_stage(ao: dict, sub_count: int, st: dict) -> str:
    """Étape pipeline d'un AO. L'issue explicite (ao_outcome) prime ; repli sur
    l'agrégation des deal_status candidats tant qu'aucun bilan n'est posé.

    Le repli « perdu » exige que TOUS les candidats soumis soient décidés et
    perdants (len(deals) >= sub_count) — sinon un seul « perdue » parmi des
    candidats encore en lice basculerait à tort l'AO en terminal."""
    outcome = ao.get("ao_outcome")
    deals = st.get("deals", [])
    if outcome == "pourvu" or (outcome is None and any(d == "gagnee" for d in deals)):
        return "gagne"
    if outcome in ("non_pourvu", "sans_suite") or (
        outcome is None and deals and all(d == "perdue" for d in deals)
        and sub_count > 0 and len(deals) >= sub_count
    ):
        return "perdu"
    if st.get("has_sent"):
        return "presente_client"
    if sub_count > 0:
        return "reponses_recues"
    if ao.get("notified_at"):
        return "diffusion"
    return "backlog"


@router.get("/types")
async def get_ao_types():
    return AO_TYPES


@router.post("/draft", dependencies=[Depends(rate_limit(10, 60))])
async def draft_ao(
    pasted_text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_staff),
):
    """
    Generate editable AO fields from raw source material: pasted email text and/or
    attachments (PDF, DOCX, TXT). The staff member reviews and edits the result
    before saving — nothing is persisted here.
    """
    if not ao_drafter.is_available():
        raise HTTPException(status_code=503, detail="Génération IA indisponible (clé OpenRouter non configurée).")

    ai_ledger.set_context(user_id=user.get("sub"), user_email=user.get("email"), entity_type="ao")

    parts: list[str] = []
    if pasted_text and pasted_text.strip():
        parts.append(pasted_text.strip())

    for f in files:
        data = await f.read()
        if not data:
            continue
        name = (f.filename or "").lower()
        try:
            if name.endswith(".pdf"):
                parts.append(extract_text_from_pdf(data))
            elif name.endswith(".docx"):
                parts.append(extract_text_from_docx(data))
            elif name.endswith(".xlsx"):
                parts.append(extract_text_from_xlsx(data))
            elif name.endswith((".txt", ".csv")):
                parts.append(data.decode("utf-8", errors="ignore"))
            # other formats are silently skipped
        except Exception:
            # unreadable file → skip rather than fail the whole request
            continue

    source = "\n\n".join(p for p in parts if p and p.strip())
    if not source.strip():
        raise HTTPException(
            status_code=422,
            detail="Aucun contenu exploitable. Collez le texte de l'email ou ajoutez un PDF, DOCX ou XLSX.",
        )

    try:
        fields = await ao_drafter.draft_ao_fields(source, AO_TYPES)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "User not found" in msg or "invalid api key" in msg.lower():
            detail = (
                "Le fournisseur d'IA a refusé la requête (clé API invalide ou expirée). "
                "Vérifiez la clé OpenRouter du serveur — ou configurez une clé Mistral de repli — "
                "puis redémarrez le backend."
            )
        else:
            detail = f"Erreur de génération IA : {e}"
        raise HTTPException(status_code=502, detail=detail)

    if fields is None:
        raise HTTPException(status_code=502, detail="L'IA n'a pas renvoyé de résultat exploitable. Réessayez.")
    return fields


@router.post("")
async def create_ao(body: AOCreate, background_tasks: BackgroundTasks, user: dict = Depends(require_staff)):
    if not body.is_draft:
        missing = _missing_publish_fields(body.model_dump())
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Champ(s) requis pour publier : {', '.join(missing)}. Enregistrez en brouillon pour les compléter plus tard.",
            )
    try:
        record = {
            "client_id": body.client_id,
            "title": body.title,
            "description": body.description,
            "skills_required": body.skills_required,
            "reference": body.reference,
            "budget_max": body.budget_max,
            "location": body.location,
            "duration": body.duration,
            "context": body.context,
            "ao_type": body.ao_type,
            "deadline": body.deadline,
            "work_mode": body.work_mode,
            "langue_requise": body.langue_requise,
            "status": "open",
            "is_draft": bool(body.is_draft),
            "created_by": user["sub"],
        }
        overrides = _overrides_for_storage(body.scoring_overrides)
        try:
            response = supabase.table("appels_offres").insert(
                {**record, "scoring_overrides": overrides}
            ).execute()
        except Exception:
            # Colonnes récentes (scoring_overrides / work_mode / reference / langue_requise
            # / is_draft) pas migrées : on retire les optionnelles et on réessaie.
            slim = {k: v for k, v in record.items() if k not in ("work_mode", "reference", "langue_requise", "is_draft")}
            response = supabase.table("appels_offres").insert(slim).execute()
        ao = response.data[0]
        # Matching (recommandations vivier) SEULEMENT pour un AO publié — un
        # brouillon n'est pas matché tant qu'il n'est pas publié.
        if not body.is_draft:
            background_tasks.add_task(run_vivier_matching, ao["id"], user["sub"])
        # Résumé IA en 1 phrase (accroche de la fiche AO), généré en fond.
        background_tasks.add_task(_generate_and_store_summary, ao["id"])
        # Géocodage de la localisation pour la carte (sauf full remote).
        background_tasks.add_task(_geocode_and_store_ao, ao["id"], body.location, body.work_mode)
        return ao
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("")
async def list_aos(view: str = "active", user: dict = Depends(get_current_user)):
    """
    Liste des AO par onglet (`view`), avec client + nombre de CV.
      * active   : AO actifs (non archivés). Partenaire = ses clients habilités.
      * mine      : partenaire -> AO auxquels il a répondu (historique, archivés
                    inclus) ; commercial/admin -> AO qu'il a créés (actifs).
      * archived  : équipe UTI -> AO archivés (commercial : les siens ; admin :
                    tous). Partenaire -> jamais (liste vide).
    Annoté du tier partenaire quand applicable (regroupement par tier côté front).
    """
    role = user["role"]
    uid = user["sub"]
    view = view if view in ("active", "mine", "archived", "draft") else "active"

    # Les partenaires ne voient jamais les archivés NI les brouillons.
    if view in ("archived", "draft") and role == "ao":
        return []

    q = supabase.table("appels_offres").select(
        "*, clients(id, name, sector, logo_url), submissions(count)"
    ).order("created_at", desc=True)

    # NB: on filtre `archived` / `is_draft` DIRECTEMENT (fail-closed). Pas de repli
    # qui couperait le filtre en cas d'erreur — PostgREST ne sait pas distinguer
    # « colonne absente » d'un « cache périmé » transitoire, et un tel repli
    # exposerait archivés/brouillons aux partenaires. Si les migrations 0003/0005
    # ne sont pas appliquées, l'endpoint renvoie 500 (le temps de les appliquer),
    # jamais une liste dé-filtrée. Même convention que les autres colonnes du repo.
    if view == "draft":
        q = q.eq("is_draft", True)
        if role == "commerce":
            q = q.eq("created_by", uid)          # commercial : ses propres brouillons
        # admin : tous les brouillons
    elif view == "archived":
        q = q.eq("is_draft", False).eq("archived", True)
        if role == "commerce":
            q = q.eq("created_by", uid)          # commercial : ses propres archivés
        # admin : tous les archivés
    elif view == "mine":
        if role == "ao":
            ids = _responded_ao_ids(uid)          # historique (un partenaire ne peut pas répondre à un brouillon)
            if not ids:
                return []
            q = q.in_("id", ids)
        else:
            q = q.eq("created_by", uid).eq("is_draft", False).eq("archived", False)  # commercial / admin : mes AO actifs
    else:  # active
        q = q.eq("is_draft", False).eq("archived", False)
        accessible = _accessible_client_ids(user)
        if accessible is not None:
            if not accessible:
                return []
            q = q.in_("client_id", accessible)

    aos = q.execute().data

    # Flatten the submissions count into `submission_count`
    for ao in aos:
        subs = ao.get("submissions")
        ao["submission_count"] = subs[0].get("count", 0) if isinstance(subs, list) and subs else 0
        ao.pop("submissions", None)

    # `potential_count` : nombre de CV dont le score dépasse le seuil « à considérer »
    # (signal ABSOLU « il existe des CV qui pourraient correspondre », par-delà le
    # simple nombre de CV reçus). Une seule requête pour toute la page.
    POTENTIAL_THRESHOLD = 50
    try:
        ao_ids = [a["id"] for a in aos if a.get("id")]
        potential: dict = {}
        if ao_ids:
            rows = supabase.table("matchings").select(
                "ao_id, score_total, score_hybride"
            ).in_("ao_id", ao_ids).execute().data or []
            for r in rows:
                sc = r.get("score_hybride")
                if sc is None:
                    sc = r.get("score_total")
                if (sc or 0) >= POTENTIAL_THRESHOLD:
                    potential[r["ao_id"]] = potential.get(r["ao_id"], 0) + 1
        for a in aos:
            a["potential_count"] = potential.get(a["id"], 0)
    except Exception:
        for a in aos:
            a.setdefault("potential_count", 0)

    # Attach the partner's tier per client
    if role == "ao":
        access = supabase.table("partner_clients").select("client_id, tier").eq(
            "partner_id", uid
        ).execute().data or []
        tiers = {row["client_id"]: row["tier"] for row in access}
        for ao in aos:
            ao["tier"] = tiers.get(ao["client_id"])

    return aos


@router.get("/pipeline")
async def ao_pipeline(user: dict = Depends(require_staff)):
    """Vue pipeline (équipe UTI) : les AO actifs (non archivés) répartis par étape
    d'avancement. Chaque AO est tagué d'un `stage` (voir PIPELINE_STAGES) dérivé
    de l'issue explicite (ao_outcome) puis, à défaut, de l'état des candidats."""
    _cols_full = ("id, title, reference, client_id, clients(id, name, logo_url), deadline, "
                  "notified_at, relance_count, ao_outcome, winning_partner_id, ao_type, status, created_at, submissions(count)")
    _cols_slim = ("id, title, reference, client_id, clients(id, name, logo_url), deadline, "
                  "notified_at, relance_count, ao_type, status, created_at, submissions(count)")
    try:
        # Publiés uniquement (is_draft=false), et : actifs + tout AO CLÔTURÉ (bilan
        # posé) même archivé — sinon les colonnes terminales Gagné/Perdu resteraient
        # vides, l'issue étant souvent posée APRÈS l'auto-archivage à l'échéance.
        aos = supabase.table("appels_offres").select(_cols_full).eq("is_draft", False).or_(
            "archived.eq.false,ao_outcome.not.is.null").order("created_at", desc=True).execute().data or []
    except Exception as e:  # noqa: BLE001
        # Migration 0004/0005 non appliquée : dégradation propre -> AO actifs seuls
        # (issue/brouillon traités comme absents). Jamais de 500.
        if _looks_like_missing_outcome(e) or _looks_like_missing_draft(e):
            aos = supabase.table("appels_offres").select(_cols_slim).eq(
                "archived", False).order("created_at", desc=True).execute().data or []
        else:
            raise

    ao_ids = [a["id"] for a in aos]
    # Agrégat ao_consultant_state par AO : présenté client ? deal_status connus ?
    states: dict[str, dict] = {}
    if ao_ids:
        try:
            rows = supabase.table("ao_consultant_state").select(
                "ao_id, sent_to_client_at, deal_status").in_("ao_id", ao_ids).execute().data or []
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows:
            st = states.setdefault(r["ao_id"], {"has_sent": False, "deals": []})
            if r.get("sent_to_client_at"):
                st["has_sent"] = True
            if r.get("deal_status"):
                st["deals"].append(r["deal_status"])

    # Noms des partenaires gagnants (colonnes Gagné) — résolus en un appel.
    winner_ids = {a.get("winning_partner_id") for a in aos if a.get("winning_partner_id")}
    winner_names: dict = {}
    if winner_ids:
        try:
            for p in supabase.table("profiles").select("id, name, email").in_(
                    "id", list(winner_ids)).execute().data or []:
                winner_names[p["id"]] = p.get("name") or p.get("email")
        except Exception:  # noqa: BLE001
            pass

    out = []
    for a in aos:
        subs = a.get("submissions")
        cnt = subs[0].get("count", 0) if isinstance(subs, list) and subs else 0
        a["submission_count"] = cnt
        a.pop("submissions", None)
        a["stage"] = _derive_stage(a, cnt, states.get(a["id"], {}))
        a["winner_name"] = winner_names.get(a.get("winning_partner_id"))
        out.append(a)
    return {"stages": PIPELINE_STAGES, "aos": out}


class OutcomeRequest(BaseModel):
    ao_outcome: Optional[str] = None          # None = efface le bilan
    winning_partner_id: Optional[str] = None
    outcome_note: Optional[str] = None


@router.patch("/{ao_id}/outcome")
async def set_ao_outcome(ao_id: str, body: OutcomeRequest, user: dict = Depends(require_staff)):
    """Bilan de clôture (équipe UTI) : pose l'issue au niveau AO. Source de vérité
    pour le pipeline et la supervision. `winning_partner_id` est validé (doit avoir
    répondu à l'AO) et pré-rempli depuis le candidat gagnant si absent."""
    outcome = body.ao_outcome
    if outcome is not None and outcome not in AO_OUTCOMES:
        raise HTTPException(status_code=422, detail="Issue invalide.")
    try:
        ao = supabase.table("appels_offres").select("id").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")
    if not ao:
        raise HTTPException(status_code=404, detail="AO introuvable")

    winner = (body.winning_partner_id or "").strip() or None
    if outcome == "pourvu":
        # winner facultatif (« pourvu hors plateforme »). On NE devine PAS à
        # l'enregistrement : le choix du client fait foi (le pré-remplissage se
        # fait côté formulaire). S'il est fourni, il doit avoir répondu à l'AO.
        if winner is not None and winner not in _winner_submitters(ao_id):
            raise HTTPException(status_code=422, detail="Le partenaire gagnant doit avoir répondu à cet AO.")
    else:
        winner = None                                 # pas de gagnant hors « pourvu »

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "ao_outcome": outcome,
        "winning_partner_id": winner,
        "outcome_note": (body.outcome_note or "").strip() or None,
        "outcome_at": now if outcome is not None else None,
        "outcome_by": user["sub"] if outcome is not None else None,
    }
    try:
        row = supabase.table("appels_offres").update(payload).eq("id", ao_id).execute().data
    except Exception as e:  # noqa: BLE001
        if _looks_like_missing_outcome(e):
            raise HTTPException(status_code=501,
                                detail="Bilan indisponible : appliquez migrations/0004_ao_outcome.sql.")
        raise
    if not row:
        raise HTTPException(status_code=404, detail="AO introuvable")
    return {"ok": True, **payload}


@router.get("/{ao_id}")
async def get_ao(ao_id: str, user: dict = Depends(get_current_user)):
    try:
        response = supabase.table("appels_offres").select(
            "*, clients(id, name, sector, description, logo_url), submissions(count)"
        ).eq("id", ao_id).single().execute()
        ao = response.data

        # Un brouillon est invisible des partenaires, même via l'URL directe.
        if user["role"] == "ao" and ao.get("is_draft"):
            raise HTTPException(status_code=404, detail="AO introuvable")

        # Access check for partners
        if user["role"] == "ao":
            access = supabase.table("partner_clients").select("tier").eq(
                "partner_id", user["sub"]
            ).eq("client_id", ao["client_id"]).in_("tier", ["list_1", "list_2"]).execute()
            if not access.data:
                raise HTTPException(status_code=403, detail="Accès refusé à cet AO")
            ao["tier"] = access.data[0]["tier"]

        subs = ao.get("submissions")
        if isinstance(subs, list) and subs:
            ao["submission_count"] = subs[0].get("count", 0)
        else:
            ao["submission_count"] = 0
        ao.pop("submissions", None)

        return ao
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")


@router.post("/{ao_id}/sources")
async def add_ao_sources(
    ao_id: str,
    files: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_staff),
):
    """Stocke les pièces jointes d'origine d'un AO (email/PDF/DOCX) pour pouvoir
    les retrouver à l'édition. Best-effort : ne casse pas si le stockage échoue."""
    try:
        ao = supabase.table("appels_offres").select("id, source_files").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")

    # Bornes alignées sur les autres uploads (CV 10 Mo) : sans elles, n'importe
    # quel compte staff peut saturer le bucket avec des fichiers arbitraires.
    MAX_SOURCE_BYTES = 10 * 1024 * 1024
    MAX_SOURCE_FILES = 10
    ALLOWED_SOURCE_EXT = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
                          ".txt", ".eml", ".msg", ".png", ".jpg", ".jpeg")
    if len(files) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=422, detail=f"Maximum {MAX_SOURCE_FILES} pièces jointes par envoi.")

    storage.ensure_bucket(AO_SOURCES_BUCKET, public=False)
    current = list(ao.get("source_files") or [])
    for f in files:
        name = f.filename or "fichier"
        if not name.lower().endswith(ALLOWED_SOURCE_EXT):
            raise HTTPException(status_code=422, detail=f"Type de fichier non autorisé : {name}")
        data = await f.read()
        if not data:
            continue
        if len(data) > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=422, detail=f"Fichier trop volumineux (max 10 Mo) : {name}")
        path = f"{ao_id}/{secrets.token_hex(8)}-{name}"
        try:
            storage.upload(AO_SOURCES_BUCKET, path, data, f.content_type or "application/octet-stream")
        except Exception as e:
            print(f"[AO] upload pièce jointe échoué ({name}): {e}")
            raise HTTPException(status_code=502, detail="Échec d'upload de la pièce jointe. Réessayez.")
        current.append({"name": name, "path": path, "content_type": f.content_type, "size": len(data)})

    try:
        supabase.table("appels_offres").update({"source_files": current}).eq("id", ao_id).execute()
    except Exception:
        pass  # colonne source_files pas encore migrée
    return {"source_files": _sources_with_urls(current)}


@router.get("/{ao_id}/sources")
async def list_ao_sources(ao_id: str, user: dict = Depends(require_staff)):
    """Pièces jointes d'origine d'un AO, avec URLs signées temporaires."""
    try:
        ao = supabase.table("appels_offres").select("source_files").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")
    return {"source_files": _sources_with_urls(ao.get("source_files") or [])}


class DeleteSourceRequest(BaseModel):
    path: str


@router.post("/{ao_id}/sources/delete")
async def delete_ao_source(ao_id: str, body: DeleteSourceRequest, user: dict = Depends(require_staff)):
    """Supprime une pièce jointe source (objet stocké + métadonnée)."""
    try:
        ao = supabase.table("appels_offres").select("source_files").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")
    remaining = [f for f in (ao.get("source_files") or []) if f.get("path") != body.path]
    try:
        storage.remove(AO_SOURCES_BUCKET, [body.path])
    except Exception:
        pass
    try:
        supabase.table("appels_offres").update({"source_files": remaining}).eq("id", ao_id).execute()
    except Exception:
        pass
    return {"source_files": _sources_with_urls(remaining)}


@router.post("/{ao_id}/summary")
async def regenerate_summary(ao_id: str, user: dict = Depends(require_staff)):
    """(Re)génère le résumé IA d'un AO et le renvoie. Best-effort de persistance."""
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")
    summary = await ao_drafter.summarize_ao(ao)
    if not summary:
        raise HTTPException(status_code=503, detail="Résumé indisponible (IA non configurée ou contenu insuffisant).")
    try:
        supabase.table("appels_offres").update({"ai_summary": summary}).eq("id", ao_id).execute()
    except Exception:
        pass  # colonne pas encore migrée — on renvoie quand même le résumé
    return {"ai_summary": summary}


@router.get("/{ao_id}/stats")
async def get_ao_stats(ao_id: str, user: dict = Depends(require_staff)):
    """
    Funnel analytics for an AO (UTI staff).

    Returns, for this AO:
    - partners who *could* answer it (list_1/list_2 access to the AO's client)
    - partners who *actually* answered it (submitted at least one CV)
    - consultants that have been proposed (distinct consultants submitted)
    - consultants that *match the criteria* but haven't been proposed yet
      (skill overlap with the AO, owned by an eligible partner, not yet submitted)
    """
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")

    client_id = ao.get("client_id")

    # ── Partners who could answer (eligible access on this client) ──
    eligible_rows = []
    if client_id:
        eligible_rows = supabase.table("partner_clients").select("partner_id, tier").eq(
            "client_id", client_id
        ).in_("tier", ["list_1", "list_2"]).execute().data or []
    eligible_partner_ids = {r["partner_id"] for r in eligible_rows}
    partners_list_1 = sum(1 for r in eligible_rows if r["tier"] == "list_1")
    partners_list_2 = sum(1 for r in eligible_rows if r["tier"] == "list_2")

    # ── Submissions for this AO ────────────────────────────────────
    subs = supabase.table("submissions").select(
        "id, submitted_by, consultant_id"
    ).eq("ao_id", ao_id).execute().data or []
    responded_partner_ids = {s["submitted_by"] for s in subs if s.get("submitted_by")}
    proposed_consultant_ids = {s["consultant_id"] for s in subs if s.get("consultant_id")}

    # ── Consultants matching the AO criteria, owned by eligible partners ──
    ao_skills = [s.strip().lower() for s in (ao.get("skills_required") or "").split(",") if s.strip()]
    pool_eligible = 0
    eligible_not_proposed = 0
    if eligible_partner_ids:
        consultants = supabase.table("consultants").select(
            "id, skills, created_by"
        ).in_("created_by", list(eligible_partner_ids)).execute().data or []
        for c in consultants:
            c_skills = [s.strip().lower() for s in (c.get("skills") or "").split(",") if s.strip()]
            matches = (
                any(any(a in cs or cs in a for cs in c_skills) for a in ao_skills)
                if ao_skills else True
            )
            if matches:
                pool_eligible += 1
                if c["id"] not in proposed_consultant_ids:
                    eligible_not_proposed += 1

    return {
        "partners_eligible": len(eligible_partner_ids),
        "partners_list_1": partners_list_1,
        "partners_list_2": partners_list_2,
        "partners_responded": len(responded_partner_ids),
        "consultants_proposed": len(proposed_consultant_ids),
        "consultants_pool_eligible": pool_eligible,
        "consultants_eligible_not_proposed": eligible_not_proposed,
        "submissions_total": len(subs),
    }


@router.patch("/{ao_id}")
async def update_ao(ao_id: str, body: AOUpdate, background_tasks: BackgroundTasks, user: dict = Depends(require_staff)):
    try:
        update_data = body.model_dump(exclude_none=True)
        if "scoring_overrides" in update_data:
            # Revalide la cohérence des seuils et normalise pour le stockage.
            update_data["scoring_overrides"] = _overrides_for_storage(body.scoring_overrides)
        try:
            response = supabase.table("appels_offres").update(update_data).eq("id", ao_id).execute()
        except Exception:
            # Colonnes récentes pas encore migrées → on met à jour le reste.
            for k in ("scoring_overrides", "work_mode", "langue_requise"):
                update_data.pop(k, None)
            response = supabase.table("appels_offres").update(update_data).eq("id", ao_id).execute()
        # Localisation ou mode de travail modifié → re-géocoder pour la carte.
        if "location" in update_data or "work_mode" in update_data:
            ao = response.data[0] if response.data else {}
            background_tasks.add_task(
                _geocode_and_store_ao, ao_id,
                update_data.get("location", ao.get("location")),
                update_data.get("work_mode", ao.get("work_mode")),
            )
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


def _fetch_ao_for_notify(ao_id: str) -> dict:
    try:
        ao = supabase.table("appels_offres").select(
            "*, clients(name)"
        ).eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")
    if not ao:
        raise HTTPException(status_code=404, detail="AO introuvable")
    return ao


@router.post("/{ao_id}/notify", dependencies=[Depends(rate_limit(20, 60))])
async def notify_partners(ao_id: str, user: dict = Depends(require_staff)):
    """
    Envoi MANUEL (commercial) de la notification d'ouverture aux partenaires :
    liste 1 immédiatement, liste 2 planifiée selon le délai configuré (réglages
    admin). Relance le compteur — chaque clic relance une campagne propre.
    """
    cfg = get_notification_settings()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=400, detail="Les notifications sont désactivées dans les réglages admin.")
    ao = _fetch_ao_for_notify(ao_id)

    now = datetime.now(timezone.utc)
    sent_1 = notifications.notify_tier(ao, "list_1", user["sub"])

    delay = cfg["list2_delay_days"]
    list2_at = now + timedelta(days=delay)
    sent_2 = 0
    update = {
        "notified_at": now.isoformat(),
        "list2_scheduled_at": list2_at.isoformat(),
        "list2_notified_at": None,
        "relance_count": 0,
        "last_relance_at": None,
    }
    if delay <= 0:
        # Pas de délai → liste 2 tout de suite, sans attendre le planificateur.
        sent_2 = notifications.notify_tier(ao, "list_2", user["sub"])
        update["list2_notified_at"] = now.isoformat()

    try:
        supabase.table("appels_offres").update(update).eq("id", ao_id).execute()
    except Exception as e:
        # Colonnes de notification pas encore migrées : l'envoi liste 1 a tout de
        # même eu lieu, on signale sans planifier la liste 2.
        print(f"[AO] maj notification {ao_id} échouée (migration ?): {e}")
        return {"sent_list_1": sent_1, "sent_list_2": sent_2, "list2_scheduled_at": None, "delay_days": delay}

    return {
        "sent_list_1": sent_1,
        "sent_list_2": sent_2,
        "list2_scheduled_at": None if delay <= 0 else list2_at.isoformat(),
        "delay_days": delay,
    }


@router.post("/{ao_id}/relance", dependencies=[Depends(rate_limit(20, 60))])
async def relance_partners(ao_id: str, user: dict = Depends(require_staff)):
    """Relance MANUELLE des partenaires n'ayant pas encore proposé de CV."""
    ao = _fetch_ao_for_notify(ao_id)
    now = datetime.now(timezone.utc)
    sent = notifications.relance(ao, only_pending=True, actor_id=user["sub"])
    try:
        supabase.table("appels_offres").update({
            "last_relance_at": now.isoformat(),
            "relance_count": (ao.get("relance_count") or 0) + 1,
        }).eq("id", ao_id).execute()
    except Exception as e:
        print(f"[AO] maj relance {ao_id} échouée (migration ?): {e}")
    return {"relance_sent": sent}


@router.get("/{ao_id}/eligible-partners")
async def ao_eligible_partners(ao_id: str, user: dict = Depends(require_staff)):
    """Partenaires (liste 1/2) du client de l'AO, pour le renvoi ciblé."""
    ao = _fetch_ao_for_notify(ao_id)
    return {"partners": notifications.eligible_partners(ao)}


class NotifySelectedRequest(BaseModel):
    partner_ids: list[str]


@router.post("/{ao_id}/notify-partners", dependencies=[Depends(rate_limit(30, 60))])
async def notify_selected_partners(ao_id: str, body: NotifySelectedRequest, user: dict = Depends(require_staff)):
    """Renvoi MANUEL ciblé d'un AO à des partenaires précis (sans toucher les autres)."""
    if not body.partner_ids:
        raise HTTPException(status_code=422, detail="Aucun partenaire sélectionné.")
    ao = _fetch_ao_for_notify(ao_id)
    sent = notifications.notify_selected(ao, body.partner_ids, user["sub"])
    return {"sent": sent}


class BulkDeleteRequest(BaseModel):
    ids: list[str]


@router.post("/bulk-delete")
async def bulk_delete_aos(body: BulkDeleteRequest, user: dict = Depends(require_staff)):
    """Delete several AOs in one shot (multi-select on the AO list)."""
    if not body.ids:
        raise HTTPException(status_code=422, detail="Aucun AO sélectionné")
    try:
        supabase.table("appels_offres").delete().in_("id", body.ids).execute()
        return {"message": f"{len(body.ids)} AO(s) supprimé(s)", "count": len(body.ids)}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.post("/{ao_id}/archive")
async def archive_ao(ao_id: str, user: dict = Depends(require_staff)):
    """Archive un AO à la main (équipe UTI). Il sort des vues partenaires.

    Passe aussi `status='closed'` (comme l'auto-archivage) : sans ça, un AO
    archivé mais resté « ouvert » continuerait de recevoir list 2 / relances
    automatiques (le planificateur ne filtre que sur status='open')."""
    try:
        row = supabase.table("appels_offres").update(
            {"archived": True, "archived_at": datetime.now(timezone.utc).isoformat(), "status": "closed"}
        ).eq("id", ao_id).execute().data
    except Exception as e:  # noqa: BLE001
        if _looks_like_missing_archive(e):
            raise HTTPException(status_code=501,
                                detail="Archivage indisponible : appliquez migrations/0003_ao_archive.sql.")
        raise
    if not row:
        raise HTTPException(status_code=404, detail="AO introuvable")
    return {"ok": True, "archived": True}


@router.post("/{ao_id}/unarchive")
async def unarchive_ao(ao_id: str, user: dict = Depends(require_staff)):
    """Désarchive un AO. On NE remet PAS archived_at à null : ce marqueur empêche
    l'auto-archivage de le reprendre au prochain tick (voir migration 0003)."""
    try:
        row = supabase.table("appels_offres").update(
            {"archived": False}
        ).eq("id", ao_id).execute().data
    except Exception as e:  # noqa: BLE001
        if _looks_like_missing_archive(e):
            raise HTTPException(status_code=501,
                                detail="Désarchivage indisponible : appliquez migrations/0003_ao_archive.sql.")
        raise
    if not row:
        raise HTTPException(status_code=404, detail="AO introuvable")
    return {"ok": True, "archived": False}


@router.post("/{ao_id}/publish")
async def publish_ao(ao_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_staff)):
    """Publie un brouillon (is_draft -> false) : il devient visible des partenaires
    habilités et déclenche le matching. Sens unique (pas de dé-publication)."""
    try:
        draft = supabase.table("appels_offres").select(
            "id, is_draft, reference, ao_type, deadline, budget_max, location, duration"
        ).eq("id", ao_id).eq("is_draft", True).maybe_single().execute().data
    except Exception as e:  # noqa: BLE001
        if _looks_like_missing_draft(e):
            raise HTTPException(status_code=501,
                                detail="Publication indisponible : appliquez migrations/0005_ao_draft.sql.")
        raise
    if not draft:
        # Aucun brouillon avec cet id (déjà publié, ou introuvable).
        raise HTTPException(status_code=404, detail="Brouillon introuvable (déjà publié ?)")
    missing = _missing_publish_fields(draft)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Champ(s) requis pour publier : {', '.join(missing)}. Modifiez le brouillon pour les compléter.",
        )
    try:
        row = supabase.table("appels_offres").update({"is_draft": False}).eq(
            "id", ao_id).eq("is_draft", True).execute().data
    except Exception as e:  # noqa: BLE001
        if _looks_like_missing_draft(e):
            raise HTTPException(status_code=501,
                                detail="Publication indisponible : appliquez migrations/0005_ao_draft.sql.")
        raise
    if not row:
        # Publié entre-temps par un autre onglet/utilisateur.
        raise HTTPException(status_code=404, detail="Brouillon introuvable (déjà publié ?)")
    # Publier = lancer le matching (recommandations vivier), comme à la création.
    background_tasks.add_task(run_vivier_matching, ao_id, user["sub"])
    return {"ok": True, "is_draft": False}


@router.delete("/{ao_id}")
async def delete_ao(ao_id: str, user: dict = Depends(require_staff)):
    try:
        # Nettoyage best-effort des pièces jointes sources stockées.
        try:
            ao = supabase.table("appels_offres").select("source_files").eq("id", ao_id).single().execute().data
            paths = [f["path"] for f in (ao.get("source_files") or []) if f.get("path")]
            if paths:
                storage.remove(AO_SOURCES_BUCKET, paths)
        except Exception:
            pass
        supabase.table("appels_offres").delete().eq("id", ao_id).execute()
        return {"message": "AO supprimé"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
