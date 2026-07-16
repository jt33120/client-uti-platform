"""
Retour client — page PUBLIQUE de review par lien sécurisé (SANS compte).

Le client, via un lien porteur d'un token (table client_reviews), consulte les
profils qui lui ont été présentés pour un AO et donne son avis par candidat
(intéressé / refusé / à revoir). AUCUNE authentification : la sécurité repose
sur le token (unguessable, révocable, expirant) + un rate-limit par IP. Le
PÉRIMÈTRE (ao_id + liste des profils présentés) est TOUJOURS dérivé DU TOKEN,
jamais des paramètres fournis par le client.

⚠️ Cloisonnement : cette surface publique n'expose JAMAIS de donnée staff-only
(tjm_achat / tjm_vente / marge, notes internes, données d'autres partenaires).
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from services.supabase_client import supabase
from services import storage, audit
from services.ratelimit import rate_limit_public

router = APIRouter(prefix="/client-review", tags=["client-review"])

VALID_CLIENT_DECISION = ("interesse", "refuse", "a_revoir")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(ts: Optional[str]) -> bool:
    """True si l'horodatage ISO est renseigné ET déjà passé (UTC-aware).
    None/absent → non expiré (pas de date limite)."""
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)


def _load_review(token: str) -> dict:
    """Charge un lien de retour client valide. 404 si token inconnu, révoqué
    (revoked_at) ou expiré (expires_at passé). C'est ce lien qui définit le
    périmètre (ao_id) — jamais les paramètres du client."""
    try:
        rows = supabase.table("client_reviews").select("*").eq("token", token).limit(1).execute().data or []
    except Exception:
        rows = []
    review = rows[0] if rows else None
    if not review or review.get("revoked_at") or _is_expired(review.get("expires_at")):
        raise HTTPException(status_code=404, detail="Lien de retour client invalide ou expiré.")
    return review


def _presented_states(ao_id: str) -> list[dict]:
    """Profils PRÉSENTÉS au client pour cet AO : lignes ao_consultant_state dont
    sent_to_client_at est renseigné. C'est l'allowlist du token. Best-effort :
    repli sans les colonnes de retour client si elles ne sont pas encore migrées."""
    try:
        rows = supabase.table("ao_consultant_state").select(
            "consultant_id, sent_to_client_at, client_decision, client_decision_note"
        ).eq("ao_id", ao_id).execute().data or []
    except Exception:
        try:
            rows = supabase.table("ao_consultant_state").select(
                "consultant_id, sent_to_client_at"
            ).eq("ao_id", ao_id).execute().data or []
        except Exception:
            return []
    return [r for r in rows if r.get("sent_to_client_at") and r.get("consultant_id")]


@router.get("/{token}", dependencies=[Depends(rate_limit_public(30, 60))])
async def get_client_review(token: str):
    """Page publique : métadonnées de l'AO + profils présentés (nom, CV signé,
    décision déjà saisie). SANS auth — périmètre dérivé du token."""
    review = _load_review(token)
    ao_id = review["ao_id"]

    # Métadonnées AO (titre / référence / nom du client). Best-effort.
    ao_meta = {"title": None, "reference": None, "client_name": None}
    try:
        ao = (supabase.table("appels_offres").select(
            "title, reference, clients(name)"
        ).eq("id", ao_id).limit(1).execute().data or [None])[0]
        if ao:
            ao_meta["title"] = ao.get("title")
            ao_meta["reference"] = ao.get("reference")
            ao_meta["client_name"] = (ao.get("clients") or {}).get("name")
    except Exception:
        pass

    states = _presented_states(ao_id)
    cids = [s["consultant_id"] for s in states]

    # Noms des consultants (aucune autre PII ; rien côté partenaire/staff).
    names: dict = {}
    if cids:
        try:
            for c in supabase.table("consultants").select("id, name").in_("id", cids).execute().data or []:
                names[c["id"]] = c.get("name")
        except Exception:
            names = {}

    profiles = []
    for s in states:
        cid = s["consultant_id"]
        # URL signée FRAÎCHE (900s) du CV de la soumission la plus récente. Best-effort → null.
        cv_url = None
        try:
            sub = (supabase.table("submissions").select("cv_url").eq(
                "ao_id", ao_id
            ).eq("consultant_id", cid).order("submitted_at", desc=True).limit(1).execute().data or [None])[0]
            if sub and sub.get("cv_url"):
                cv_url = storage.signed_cv_url(sub["cv_url"], expires_in=900)
        except Exception:
            cv_url = None
        profiles.append({
            "consultant_id": cid,
            "name": names.get(cid),
            "cv_url": cv_url,
            "decision": s.get("client_decision"),
            "note": s.get("client_decision_note"),
        })

    return {"ao": ao_meta, "profiles": profiles, "expired": False}


class ClientDecisionRequest(BaseModel):
    consultant_id: str
    decision: str             # 'interesse' | 'refuse' | 'a_revoir'
    note: Optional[str] = None


@router.post("/{token}/respond", dependencies=[Depends(rate_limit_public(60, 60))])
async def respond_client_review(token: str, body: ClientDecisionRequest):
    """Le client enregistre son avis sur un profil présenté. SANS auth : l'allowlist
    (profils autorisés) est dérivée du token, jamais du client. 404 si le consultant
    n'a pas été présenté via ce lien (anti-forge de consultant_id)."""
    review = _load_review(token)
    ao_id = review["ao_id"]

    if body.decision not in VALID_CLIENT_DECISION:
        raise HTTPException(status_code=422, detail=f"decision doit être l'un de {VALID_CLIENT_DECISION}")

    # Allowlist = profils réellement présentés au client sur CET AO (scope du token).
    allowed = {s["consultant_id"] for s in _presented_states(ao_id)}
    if body.consultant_id not in allowed:
        raise HTTPException(status_code=404, detail="Profil introuvable pour ce lien.")

    now = _now_iso()
    note = (body.note or "").strip()[:1000] or None
    # Upsert PARTIEL : on ne touche qu'au retour client — sent_to_client_at et la
    # validation GRP-IT (absents du payload) restent intacts.
    payload = {
        "ao_id": ao_id,
        "consultant_id": body.consultant_id,
        "client_decision": body.decision,
        "client_decision_at": now,
        "client_decision_note": note,
        "updated_at": now,
    }
    try:
        supabase.table("ao_consultant_state").upsert(
            payload, on_conflict="ao_id,consultant_id"
        ).execute()
    except Exception as e:
        # Colonnes de retour client non encore migrées : dégrader (503, pas de 500).
        msg = str(e).lower()
        looks_missing = any(
            k in msg for k in ("does not exist", "42703", "schema cache", "pgrst204", "could not find")
        ) and any(c in msg for c in ("client_decision", "ao_consultant_state"))
        if looks_missing:
            raise HTTPException(status_code=503, detail="Retour client indisponible : migration en attente.")
        raise HTTPException(status_code=500, detail="Erreur enregistrement du retour client.")

    # Audit best-effort (pas d'actor_id : action publique, non authentifiée).
    audit.log_event(
        "client_decision", audit.new_run_id(), ao_id=ao_id,
        payload={"consultant_id": body.consultant_id, "decision": body.decision},
    )
    return {"ok": True, "decision": body.decision}
