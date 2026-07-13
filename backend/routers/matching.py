from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from services.supabase_client import supabase
from services.matching_runner import run_submission_matching
from services import storage, audit
from routers.auth import get_current_user, require_staff
from services.ratelimit import rate_limit

router = APIRouter(prefix="/matching", tags=["matching"])

VALID_CONTACT_STATUS = ("none", "contacted", "proposed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_states(ao_id: str) -> dict:
    """État humain (classement + contact) par consultant. Best-effort (table absente → {})."""
    try:
        rows = supabase.table("ao_consultant_state").select("*").eq("ao_id", ao_id).execute().data or []
        return {r["consultant_id"]: r for r in rows}
    except Exception:
        return {}


class MatchRequest(BaseModel):
    ao_id: str
    top_n: int = 5


@router.post("/run", dependencies=[Depends(rate_limit(10, 60))])
async def run_matching(body: MatchRequest, user: dict = Depends(require_staff)):
    """
    Score all consultants who have submitted a CV to this AO.
    Returns the top N scored submissions with breakdown + explanation.
    UTI staff (admin or commerce). Also runs automatically when a new CV
    is submitted — this endpoint remains for manual re-runs.
    """
    try:
        return await run_submission_matching(body.ao_id, user["sub"], body.top_n)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/stats")
async def get_matching_stats(user: dict = Depends(require_staff)):
    """Get AI matching statistics: total matchings, model used, total cost."""
    try:
        # Try with cost_usd column; fall back if column doesn't exist yet
        try:
            matchings = supabase.table("matchings").select("id, cost_usd").execute().data or []
            total_cost = sum(float(m.get("cost_usd") or 0) for m in matchings)
        except Exception:
            matchings = supabase.table("matchings").select("id").execute().data or []
            total_cost = 0.0

        from services.ai_matching import EXTRACTION_MODEL
        from services.scoring import GRID_VERSION

        # AOs « traités » : ceux qui ont au moins un profil potentiel (score ≥ 50,
        # le seuil « à considérer »). C'est la métrique métier affichée sur le
        # tableau de bord (« X AOs ayant trouvé un consultant potentiel »).
        POTENTIAL_THRESHOLD = 50
        try:
            scored = supabase.table("matchings").select("ao_id, score_total").execute().data or []
            matched_ao_ids = sorted({
                r["ao_id"] for r in scored
                if r.get("ao_id") and (r.get("score_total") or 0) >= POTENTIAL_THRESHOLD
            })
            analyzed_ao_ids = {r["ao_id"] for r in scored if r.get("ao_id")}
        except Exception:
            matched_ao_ids, analyzed_ao_ids = [], set()

        return {
            "total_matchings": len(matchings),
            # AOs ayant trouvé au moins un consultant potentiel
            "aos_matched": len(matched_ao_ids),
            "matched_ao_ids": matched_ao_ids,
            "aos_analyzed": len(analyzed_ao_ids),
            "potential_threshold": POTENTIAL_THRESHOLD,
            # Architecture hybride : le LLM extrait, le score est déterministe.
            "extraction_model": EXTRACTION_MODEL,
            "scoring": "déterministe",
            "grid_version": GRID_VERSION,
            "total_cost_usd": round(total_cost, 2),
            "status": "active",
        }
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


def _contact_targets(results: list[dict]) -> dict:
    """
    consultant_id → {email, name, kind} : à QUI envoyer le mail de proposition.
    Chaîne de fallback (du plus pertinent au dernier recours) :
      1. 'partner'    — le partenaire qui a SOUMIS le CV ;
      2. 'partner'    — sinon le propriétaire du profil au vivier s'il est partenaire ;
      3. 'consultant' — sinon l'email du consultant lui-même (profil vivier staff) ;
      4. 'owner'      — sinon le propriétaire (staff) à défaut de tout le reste.
    """
    out: dict = {}

    # 1. Partenaire soumetteur (par consultant), si une soumission existe.
    sub_ids = [r["submission_id"] for r in results if r.get("submission_id")]
    if sub_ids:
        try:
            subs = supabase.table("submissions").select("id, consultant_id, submitted_by").in_("id", sub_ids).execute().data or []
            pids = [s["submitted_by"] for s in subs if s.get("submitted_by")]
            profs = {p["id"]: p for p in (supabase.table("profiles").select("id, name, email, role").in_("id", pids).execute().data or [])} if pids else {}
            for s in subs:
                p = profs.get(s.get("submitted_by"))
                if p and p.get("email"):
                    out[s["consultant_id"]] = {"email": p["email"], "name": p.get("name"), "kind": "partner"}
        except Exception:
            pass

    # 2-4. Consultant (email propre) + propriétaire au vivier (rôle).
    cons_ids = [r.get("consultant_id") for r in results if r.get("consultant_id") and r.get("consultant_id") not in out]
    if cons_ids:
        try:
            rows = supabase.table("consultants").select(
                "id, name, email, owner:profiles!created_by(name, email, role)"
            ).in_("id", cons_ids).execute().data or []
            for c in rows:
                owner = c.get("owner") or {}
                if owner.get("email") and owner.get("role") == "ao":      # propriétaire partenaire
                    out[c["id"]] = {"email": owner["email"], "name": owner.get("name"), "kind": "partner"}
                elif c.get("email"):                                       # email du consultant
                    out[c["id"]] = {"email": c["email"], "name": c.get("name"), "kind": "consultant"}
                elif owner.get("email"):                                   # dernier recours : propriétaire staff
                    out[c["id"]] = {"email": owner["email"], "name": owner.get("name"), "kind": "owner"}
        except Exception:
            pass
    return out


class CvSourceRequest(BaseModel):
    submission_id: str
    consultant_id: Optional[str] = None


@router.post("/cv-source")
async def get_cv_source(body: CvSourceRequest, user: dict = Depends(require_staff)):
    """Texte du CV tel que l'IA l'a LU au scoring : brut, pseudonymisé (PII retirées).
    C'est la source fidèle des extraits cités dans les justifications → la vue
    « Transparence » peut surligner de façon fiable. Staff only (admin/commerce)."""
    try:
        sub = supabase.table("submissions").select("cv_text").eq(
            "id", body.submission_id).single().execute().data
    except Exception:
        sub = None
    if not sub or not (sub.get("cv_text") or "").strip():
        raise HTTPException(status_code=404, detail="Texte du CV indisponible pour cette soumission")
    name = None
    if body.consultant_id:
        try:
            c = supabase.table("consultants").select("name").eq(
                "id", body.consultant_id).single().execute().data
            name = (c or {}).get("name")
        except Exception:
            name = None
    from services.pseudonymize import strip_pii
    return {"text": strip_pii(sub["cv_text"], name)}


@router.get("/results/{ao_id}")
async def get_matching_results(ao_id: str, user: dict = Depends(get_current_user)):
    try:
        query = supabase.table("matchings").select(
            "*, consultants(name, tjm, skills, employment_type), submissions(cv_url, cv_filename)"
        ).eq("ao_id", ao_id).order("rank")

        is_partner = user["role"] == "ao"
        if is_partner:
            # Partners only see results for their own submissions
            own_subs = supabase.table("submissions").select("id").eq(
                "ao_id", ao_id
            ).eq("submitted_by", user["sub"]).execute().data or []
            own_ids = [s["id"] for s in own_subs]
            if not own_ids:
                return {"ao_id": ao_id, "results": []}
            query = query.in_("submission_id", own_ids)

        response = query.execute()
        results = response.data or []
        states = _fetch_states(ao_id)
        # Cible de contact : seulement côté staff (le partenaire n'a personne à contacter ici).
        targets = {} if is_partner else _contact_targets(results)

        for r in results:
            c = r.get("consultants") or {}
            s = r.get("submissions") or {}
            r["consultant_name"] = c.get("name")
            r["consultant_tjm"] = c.get("tjm")
            r["consultant_skills"] = c.get("skills")
            r["employment_type"] = c.get("employment_type")
            r["cv_url"] = storage.signed_cv_url(s.get("cv_url"))
            r["cv_filename"] = s.get("cv_filename")
            # État humain : classement choisi par l'opérateur + suivi de contact.
            st = states.get(r.get("consultant_id")) or {}
            r["human_rank"] = st.get("human_rank")
            r["contact_status"] = st.get("contact_status") or "none"
            r["contacted_at"] = st.get("contacted_at")
            if not is_partner:
                t = targets.get(r.get("consultant_id")) or {}
                r["partner_name"] = t.get("name")
                r["partner_email"] = t.get("email")
                r["contact_kind"] = t.get("kind")  # 'partner' | 'consultant' | 'owner'
                # Cycle de vie « Validation CV » (interne GRP-IT, staff only).
                r["validation"] = st.get("validation")
                r["sent_to_client_at"] = st.get("sent_to_client_at")
                r["commercial_exchange"] = bool(st.get("commercial_exchange"))
                r["deal_status"] = st.get("deal_status")

        # L'humain a le dernier mot : son classement prime, sinon le rang IA.
        results.sort(key=lambda r: (r.get("human_rank") is None, r.get("human_rank") or 0, r.get("rank") or 0))
        return {"ao_id": ao_id, "results": results}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


class RankRequest(BaseModel):
    order: list[str]  # consultant_ids dans l'ordre voulu par l'opérateur


@router.post("/{ao_id}/rank")
async def set_human_rank(ao_id: str, body: RankRequest, user: dict = Depends(require_staff)):
    """Enregistre le classement humain (AI Act Art. 14 — l'humain a le dernier mot)."""
    now = _now_iso()
    try:
        for idx, cid in enumerate(body.order, start=1):
            supabase.table("ao_consultant_state").upsert({
                "ao_id": ao_id,
                "consultant_id": cid,
                "human_rank": idx,
                "decided_by": user["sub"],
                "updated_at": now,
            }, on_conflict="ao_id,consultant_id").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur enregistrement classement: {e}")
    audit.log_event(
        "human_rank", audit.new_run_id(), ao_id=ao_id, actor_id=user["sub"],
        payload={"order": body.order},
    )
    return {"ok": True, "order": body.order}


class ContactRequest(BaseModel):
    consultant_id: str
    submission_id: str | None = None
    status: str  # 'none' | 'contacted' | 'proposed'


@router.post("/{ao_id}/contact")
async def set_contact_status(ao_id: str, body: ContactRequest, user: dict = Depends(require_staff)):
    """Marque un consultant comme contacté / proposé (suivi de diffusion)."""
    if body.status not in VALID_CONTACT_STATUS:
        raise HTTPException(status_code=422, detail=f"status doit être l'un de {VALID_CONTACT_STATUS}")
    now = _now_iso()
    payload = {
        "ao_id": ao_id,
        "consultant_id": body.consultant_id,
        "contact_status": body.status,
        "decided_by": user["sub"],
        "updated_at": now,
    }
    if body.status in ("contacted", "proposed"):
        payload["contacted_at"] = now
    try:
        row = supabase.table("ao_consultant_state").upsert(
            payload, on_conflict="ao_id,consultant_id"
        ).execute().data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur mise à jour contact: {e}")
    audit.log_event(
        "contact", audit.new_run_id(), ao_id=ao_id, actor_id=user["sub"],
        payload={"consultant_id": body.consultant_id, "status": body.status},
    )
    return row


# ── Cycle de vie « Validation CV » (demande Sullyvan) ────────────────────────
VALID_VALIDATION = ("retenu", "non_retenu", "none")
VALID_DEAL = ("gagnee", "perdue", "none")


class ValidationRequest(BaseModel):
    consultant_id: str
    # Chaque champ est optionnel : on ne met à jour que ce qui est fourni.
    validation: Optional[str] = None           # 'retenu' | 'non_retenu' | 'none'
    sent_to_client: Optional[bool] = None      # True → horodate l'envoi client
    commercial_exchange: Optional[bool] = None  # échange commercial Oui/Non
    deal_status: Optional[str] = None          # 'gagnee' | 'perdue' | 'none'
    eval_points_forts: Optional[str] = None     # commentaire libre « Points forts »
    eval_differenciants: Optional[str] = None   # commentaire libre « Éléments différenciants »
    notify: bool = False                        # True → notifie le partenaire par email


def _notify_events_bg(ao_id: str, consultant_id: str, events: list, actor: str) -> None:
    """Envoi des notifications partenaire en tâche de fond (jamais bloquant, jamais
    d'erreur remontée à l'utilisateur ; journalisé dans partner_email_log)."""
    from services import cv_notifications
    for ev in events:
        try:
            cv_notifications.notify_event(ao_id, consultant_id, ev, sent_by=actor)
        except Exception as e:  # noqa: BLE001
            print(f"[MATCHING] notif {ev} échouée (AO {ao_id}, {consultant_id}): {e}")


@router.post("/{ao_id}/validation")
async def set_cv_validation(ao_id: str, body: ValidationRequest, background_tasks: BackgroundTasks, user: dict = Depends(require_staff)):
    """Met à jour le cycle de vie d'un CV sur un AO : retenu / non retenu GRP-IT,
    envoi au client, échange commercial, affaire gagnée / perdue.

    Mise à jour partielle : seuls les champs fournis sont modifiés. Les valeurs
    « none » remettent le champ à NULL.
    """
    now = _now_iso()
    payload = {
        "ao_id": ao_id,
        "consultant_id": body.consultant_id,
        "decided_by": user["sub"],
        "updated_at": now,
    }
    changed = {}

    if body.validation is not None:
        if body.validation not in VALID_VALIDATION:
            raise HTTPException(status_code=422, detail=f"validation doit être l'un de {VALID_VALIDATION}")
        payload["validation"] = None if body.validation == "none" else body.validation
        changed["validation"] = payload["validation"]

    if body.sent_to_client is not None:
        payload["sent_to_client_at"] = now if body.sent_to_client else None
        changed["sent_to_client"] = body.sent_to_client

    if body.commercial_exchange is not None:
        payload["commercial_exchange"] = bool(body.commercial_exchange)
        changed["commercial_exchange"] = payload["commercial_exchange"]

    if body.deal_status is not None:
        if body.deal_status not in VALID_DEAL:
            raise HTTPException(status_code=422, detail=f"deal_status doit être l'un de {VALID_DEAL}")
        payload["deal_status"] = None if body.deal_status == "none" else body.deal_status
        changed["deal_status"] = payload["deal_status"]

    # Commentaires d'évaluation libres (aucune notification).
    if body.eval_points_forts is not None:
        payload["eval_points_forts"] = body.eval_points_forts.strip() or None
        changed["eval_points_forts"] = True
    if body.eval_differenciants is not None:
        payload["eval_differenciants"] = body.eval_differenciants.strip() or None
        changed["eval_differenciants"] = True

    if not changed:
        raise HTTPException(status_code=422, detail="Aucun champ à mettre à jour.")

    try:
        row = supabase.table("ao_consultant_state").upsert(
            payload, on_conflict="ao_id,consultant_id"
        ).execute().data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur mise à jour validation: {e}")

    try:
        audit.log_event(
            "cv_validation", audit.new_run_id(), ao_id=ao_id, actor_id=user["sub"],
            payload={"consultant_id": body.consultant_id, **changed},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[MATCHING] audit cv_validation échoué: {e}")

    # Notifications au partenaire (auto, après confirmation côté UI) → tâche de
    # fond : jamais bloquant, la réponse ne dépend pas de l'envoi d'emails.
    if body.notify:
        events = []
        if changed.get("validation") in ("retenu", "non_retenu"):
            events.append(changed["validation"])
        if changed.get("commercial_exchange") is True:
            events.append("echange_commercial")
        if changed.get("deal_status") in ("gagnee", "perdue"):
            events.append(changed["deal_status"])
        if events:
            background_tasks.add_task(_notify_events_bg, ao_id, body.consultant_id, events, user["sub"])

    return row


class SendCvClientRequest(BaseModel):
    consultant_id: str
    to_email: str
    message: Optional[str] = None


@router.post("/{ao_id}/send-cv-to-client")
async def send_cv_to_client(ao_id: str, body: SendCvClientRequest, user: dict = Depends(require_staff)):
    """Envoi RÉEL du CV au client (lien sécurisé) + notif partenaire, puis marque
    le CV comme « envoyé au client » (date + traçabilité)."""
    to = (body.to_email or "").strip()
    if "@" not in to:
        raise HTTPException(status_code=422, detail="Email du client invalide.")

    from services import cv_notifications
    ok, err = cv_notifications.send_cv_to_client(ao_id, body.consultant_id, to, body.message, sent_by=user["sub"])
    if not ok:
        raise HTTPException(status_code=502, detail=f"Échec d'envoi au client : {err}")

    now = _now_iso()
    try:
        row = supabase.table("ao_consultant_state").upsert({
            "ao_id": ao_id,
            "consultant_id": body.consultant_id,
            "sent_to_client_at": now,
            "decided_by": user["sub"],
            "updated_at": now,
        }, on_conflict="ao_id,consultant_id").execute().data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email envoyé mais état non mis à jour: {e}")

    audit.log_event(
        "cv_sent_client", audit.new_run_id(), ao_id=ao_id, actor_id=user["sub"],
        payload={"consultant_id": body.consultant_id, "to": to},
    )
    return {**row, "sent": True}


@router.get("/{ao_id}/states")
async def get_ao_states(ao_id: str, user: dict = Depends(require_staff)):
    """État par consultant pour un AO (classement humain, contact et cycle de
    vie « Validation CV »). Renvoie une map consultant_id → état pour que l'onglet
    Validation CV puisse afficher tous les CV reçus avec leur statut."""
    try:
        rows = supabase.table("ao_consultant_state").select(
            "consultant_id, human_rank, contact_status, validation, "
            "sent_to_client_at, commercial_exchange, deal_status, "
            "eval_points_forts, eval_differenciants"
        ).eq("ao_id", ao_id).execute().data or []
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
    return {"states": {r["consultant_id"]: r for r in rows if r.get("consultant_id")}}


class BulkValidationRequest(BaseModel):
    consultant_ids: list[str]
    validation: str            # 'retenu' | 'non_retenu' | 'none'
    notify: bool = False


def _bulk_notify_bg(ao_id: str, consultant_ids: list, event: str, actor: str) -> None:
    from services import cv_notifications
    for cid in consultant_ids:
        try:
            cv_notifications.notify_event(ao_id, cid, event, sent_by=actor)
        except Exception as e:  # noqa: BLE001
            print(f"[MATCHING] notif bulk {event} échouée (AO {ao_id}, {cid}): {e}")


@router.post("/{ao_id}/validation-bulk")
async def set_cv_validation_bulk(ao_id: str, body: BulkValidationRequest, background_tasks: BackgroundTasks, user: dict = Depends(require_staff)):
    """Marque plusieurs CV d'un coup (ex. « Non retenu » en masse). Les
    notifications partenaires partent en tâche de fond (jamais bloquant)."""
    if body.validation not in VALID_VALIDATION:
        raise HTTPException(status_code=422, detail=f"validation doit être l'un de {VALID_VALIDATION}")
    ids = [c for c in (body.consultant_ids or []) if c]
    if not ids:
        raise HTTPException(status_code=422, detail="Aucun consultant sélectionné.")

    val = None if body.validation == "none" else body.validation
    now = _now_iso()
    updated = 0
    for cid in ids:
        try:
            supabase.table("ao_consultant_state").upsert({
                "ao_id": ao_id, "consultant_id": cid, "validation": val,
                "decided_by": user["sub"], "updated_at": now,
            }, on_conflict="ao_id,consultant_id").execute()
            updated += 1
        except Exception:
            pass

    if body.notify and val in ("retenu", "non_retenu"):
        background_tasks.add_task(_bulk_notify_bg, ao_id, ids, val, user["sub"])

    try:
        audit.log_event(
            "cv_validation_bulk", audit.new_run_id(), ao_id=ao_id, actor_id=user["sub"],
            payload={"count": updated, "validation": val},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[MATCHING] audit cv_validation_bulk échoué: {e}")

    return {"updated": updated, "validation": val}
