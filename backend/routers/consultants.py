from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, Literal
from services.supabase_client import supabase
from services.email import send_email, render_email_html
from services.ratelimit import rate_limit
from services.geocoding import geocode
from services import storage
from routers.auth import get_current_user, require_staff, is_staff

router = APIRouter(prefix="/consultants", tags=["consultants"])


# Disponibilité STRUCTURÉE (requêtable) en plus de la note libre `availability` :
#   - availability_status : état courant (dispo / bientôt / en mission / inconnu) ;
#   - available_from       : date à partir de laquelle le profil est libre.
AvailabilityStatus = Literal["available", "soon", "on_mission", "unknown"]


class ConsultantCreate(BaseModel):
    name: str
    skills: str
    tjm: Optional[int] = Field(default=None, ge=0, le=100_000)
    experience_years: Optional[int] = Field(default=None, ge=0, le=70)
    availability: Optional[str] = None  # note libre (précision)
    availability_status: Optional[AvailabilityStatus] = None
    available_from: Optional[date] = None
    employment_type: Optional[Literal["independant", "salarie"]] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None  # ville de résidence (géocodée pour la carte)
    consent: Optional[bool] = None  # consentement RGPD au traitement (→ consent_at)


class ConsultantUpdate(BaseModel):
    name: Optional[str] = None
    skills: Optional[str] = None
    tjm: Optional[int] = Field(default=None, ge=0, le=100_000)
    experience_years: Optional[int] = Field(default=None, ge=0, le=70)
    availability: Optional[str] = None
    availability_status: Optional[AvailabilityStatus] = None
    available_from: Optional[date] = None
    employment_type: Optional[Literal["independant", "salarie"]] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None


# Colonnes optionnelles susceptibles de ne pas être migrées : on retente sans
# elles plutôt que d'échouer (géo/ville + disponibilité structurée).
_OPTIONAL_COLS = ("city", "latitude", "longitude", "availability_status", "available_from", "consent_at")


def _insert_with_geo_fallback(table: str, record: dict):
    """Insert tolérant : retente sans les colonnes optionnelles si non migrées."""
    try:
        return supabase.table(table).insert(record).execute()
    except Exception:
        slim = {k: v for k, v in record.items() if k not in _OPTIONAL_COLS}
        return supabase.table(table).insert(slim).execute()


def _update_with_geo_fallback(table: str, data: dict, id_: str):
    try:
        return supabase.table(table).update(data).eq("id", id_).execute()
    except Exception:
        slim = {k: v for k, v in data.items() if k not in _OPTIONAL_COLS}
        return supabase.table(table).update(slim).eq("id", id_).execute()


@router.post("")
async def create_consultant(body: ConsultantCreate, user: dict = Depends(get_current_user)):
    """
    Create a consultant in the partner's vivier (talent pool).
    CV upload is no longer here — CVs are attached to specific AO submissions.
    """
    try:
        record = {
            "name": body.name,
            "skills": body.skills,
            "tjm": body.tjm,
            "experience_years": body.experience_years,
            "availability": body.availability,
            "availability_status": body.availability_status,
            "available_from": body.available_from.isoformat() if body.available_from else None,
            "employment_type": body.employment_type,
            "email": body.email,
            "phone": body.phone,
            "city": body.city,
            "consent_at": datetime.now(timezone.utc).isoformat() if body.consent else None,
            "created_by": user["sub"],
        }
        geo = await geocode(body.city)
        if geo:
            record["latitude"] = geo["latitude"]
            record["longitude"] = geo["longitude"]
        response = _insert_with_geo_fallback("consultants", record)
        return response.data[0]
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("")
async def list_consultants(user: dict = Depends(get_current_user)):
    """
    Staff (admin/commerce): all consultants, with the owning partner joined so
    the UI can show who carries each profile and offer a direct contact.
    Partner: only their own vivier.
    """
    try:
        if is_staff(user):
            select = "*, owner:profiles!created_by(id, name, email, role)"
        else:
            select = "*"
        query = supabase.table("consultants").select(select).order("created_at", desc=True)
        if user["role"] == "ao":
            query = query.eq("created_by", user["sub"])
        return query.execute().data
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.post("/{consultant_id}/extract-skills")
async def extract_consultant_skills(consultant_id: str, user: dict = Depends(get_current_user)):
    """Déduit les compétences du CV le plus récent du consultant et les fusionne
    dans son champ `skills` (sans écraser la saisie manuelle). Staff ou porteur."""
    from services.consultant_skills import extract_and_store_skills
    try:
        consultant = supabase.table("consultants").select("id, created_by").eq(
            "id", consultant_id
        ).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Consultant introuvable")
    if not is_staff(user) and consultant.get("created_by") != user["sub"]:
        raise HTTPException(status_code=403, detail="Accès interdit")
    try:
        skills = await extract_and_store_skills(consultant_id)
        return {"skills": skills}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction impossible : {e}")


@router.get("/{consultant_id}")
async def get_consultant(consultant_id: str, user: dict = Depends(get_current_user)):
    try:
        response = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute()
        consultant = response.data
        if user["role"] == "ao" and consultant["created_by"] != user["sub"]:
            raise HTTPException(status_code=403, detail="Accès interdit")
        return consultant
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Consultant introuvable")


@router.get("/{consultant_id}/export")
async def gdpr_export_consultant(consultant_id: str, user: dict = Depends(get_current_user)):
    """
    Portabilité RGPD (art. 20) : export JSON de TOUTES les données détenues sur un
    consultant — fiche vivier, soumissions (métadonnées), scores de matching. Le
    contenu brut des CV (fichier / texte) n'est pas ré-empaqueté ici : il reste
    accessible via la fiche. Accès : staff, ou le partenaire propriétaire du profil.
    """
    from fastapi.responses import JSONResponse

    try:
        c = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Consultant introuvable")
    if user["role"] == "ao" and c.get("created_by") != user["sub"]:
        raise HTTPException(status_code=403, detail="Accès interdit")

    subs = supabase.table("submissions").select(
        "id, ao_id, submitted_by, submitted_at, cv_filename"
    ).eq("consultant_id", consultant_id).execute().data or []
    matchings = supabase.table("matchings").select(
        "ao_id, rank, score_total, score_hybride"
    ).eq("consultant_id", consultant_id).execute().data or []

    bundle = {
        "export_type": "rgpd_portabilite_art20",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "consultant": c,
        "submissions": subs,
        "matchings": matchings,
    }
    filename = f"export-consultant-{consultant_id}.json"
    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{consultant_id}/history")
async def consultant_history(consultant_id: str, user: dict = Depends(get_current_user)):
    """Profil enrichi d'un consultant : parcours sur les AO (soumissions, score,
    classement humain / retenu, contact), historique des CV et partenaire porteur.

    Accès : staff voit tout ; un partenaire ne voit que ses propres consultants.
    """
    # 1) Consultant + contrôle d'accès
    try:
        consultant = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Consultant introuvable")
    if user["role"] == "ao" and consultant.get("created_by") != user["sub"]:
        raise HTTPException(status_code=403, detail="Accès interdit")

    # 2) Partenaire porteur
    owner = None
    if consultant.get("created_by"):
        try:
            owner = supabase.table("profiles").select("id, name, email, role").eq(
                "id", consultant["created_by"]
            ).single().execute().data
        except Exception:
            owner = None

    # 3) Soumissions (CV) du consultant
    try:
        subs = supabase.table("submissions").select(
            "id, ao_id, submitted_at, cv_url, cv_filename"
        ).eq("consultant_id", consultant_id).order("submitted_at", desc=True).execute().data or []
    except Exception:
        subs = []

    # 4) État humain (classement / retenu / contact) par AO
    state_by = {}
    try:
        for s in supabase.table("ao_consultant_state").select(
            "ao_id, human_rank, contact_status, contacted_at"
        ).eq("consultant_id", consultant_id).execute().data or []:
            state_by[s["ao_id"]] = s
    except Exception:
        pass

    # 5) Scores de matching par AO (consultant_id est stocké en TEXT)
    match_by = {}
    try:
        for m in supabase.table("matchings").select(
            "ao_id, score_total, score_hybride, rank, recommandation, created_at"
        ).eq("consultant_id", str(consultant_id)).order("created_at", desc=True).execute().data or []:
            match_by.setdefault(m["ao_id"], m)  # garde le plus récent
    except Exception:
        pass

    # 6) Métadonnées des AO concernés (par soumission, état ou matching)
    ao_ids = {s["ao_id"] for s in subs} | set(state_by) | set(match_by)
    ao_by = {}
    if ao_ids:
        try:
            rows = supabase.table("appels_offres").select(
                "id, title, status, reference, client_id, clients(name)"
            ).in_("id", list(ao_ids)).execute().data or []
        except Exception:
            try:
                rows = supabase.table("appels_offres").select(
                    "id, title, status, client_id"
                ).in_("id", list(ao_ids)).execute().data or []
            except Exception:
                rows = []
        ao_by = {r["id"]: r for r in rows}

    # 7) Construction de l'historique : une entrée par AO
    history = []
    for aid in ao_ids:
        ao = ao_by.get(aid) or {}
        st = state_by.get(aid) or {}
        mt = match_by.get(aid) or {}
        sub = next((x for x in subs if x["ao_id"] == aid), None)
        cv_url = None
        if sub and sub.get("cv_url"):
            try:
                cv_url = storage.signed_cv_url(sub["cv_url"])
            except Exception:
                cv_url = None
        history.append({
            "ao_id": aid,
            "ao_title": ao.get("title") or "Appel d'offres",
            "ao_status": ao.get("status"),
            "ao_reference": ao.get("reference"),
            "client_name": (ao.get("clients") or {}).get("name") if isinstance(ao.get("clients"), dict) else None,
            "submitted": sub is not None,
            "submitted_at": sub.get("submitted_at") if sub else None,
            "cv_url": cv_url,
            "cv_filename": sub.get("cv_filename") if sub else None,
            "score_total": mt.get("score_total"),
            "score_hybride": mt.get("score_hybride"),
            "rank": mt.get("rank"),
            "human_rank": st.get("human_rank"),
            "retained": st.get("human_rank") is not None,
            "contact_status": st.get("contact_status") or "none",
            "contacted_at": st.get("contacted_at"),
        })

    # Tri : retenus d'abord (par classement humain), puis les autres du + récent
    retained = sorted([h for h in history if h["retained"]],
                      key=lambda h: h["human_rank"] if h["human_rank"] is not None else 9999)
    others = sorted([h for h in history if not h["retained"]],
                    key=lambda h: h.get("submitted_at") or "", reverse=True)
    history = retained + others

    stats = {
        "participations": len([h for h in history if h["submitted"]]),
        "ao_total": len(history),
        "retained": len([h for h in history if h["retained"]]),
        "contacted": len([h for h in history if h["contact_status"] in ("contacted", "proposed")]),
        "cv_count": len([s for s in subs if s.get("cv_url")]),
    }

    return {"consultant": consultant, "owner": owner, "history": history, "stats": stats}


@router.patch("/{consultant_id}")
async def update_consultant(consultant_id: str, body: ConsultantUpdate, user: dict = Depends(get_current_user)):
    try:
        consultant = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute().data
        # Owner or admin only — commerce has read-only access to the vivier
        if user["role"] != "admin" and consultant["created_by"] != user["sub"]:
            raise HTTPException(status_code=403, detail="Accès interdit")
        # mode="json" : sérialise `available_from` (date) en "YYYY-MM-DD" pour PostgREST.
        update_data = body.model_dump(exclude_none=True, mode="json")
        # Ville modifiée → re-géocoder (best-effort).
        if "city" in update_data:
            geo = await geocode(update_data["city"])
            if geo:
                update_data["latitude"] = geo["latitude"]
                update_data["longitude"] = geo["longitude"]
        response = _update_with_geo_fallback("consultants", update_data, consultant_id)
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


class ContactPartnerRequest(BaseModel):
    subject: str
    message: str


@router.post("/{consultant_id}/contact-partner", dependencies=[Depends(rate_limit(10, 300))])
async def contact_partner(consultant_id: str, body: ContactPartnerRequest, user: dict = Depends(require_staff)):
    """
    Email the partner who carries this consultant. The message is written by
    UTI staff in a pre-filled modal; Reply-To points back at the sender.
    """
    if not body.subject.strip() or not body.message.strip():
        raise HTTPException(status_code=422, detail="Sujet et message requis.")

    try:
        consultant = supabase.table("consultants").select("id, name, created_by").eq(
            "id", consultant_id
        ).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Consultant introuvable")

    if not consultant.get("created_by"):
        raise HTTPException(status_code=422, detail="Ce consultant n'a pas de partenaire porteur.")

    try:
        owner = supabase.table("profiles").select("name, email").eq(
            "id", consultant["created_by"]
        ).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Partenaire porteur introuvable")

    sender_email = user["email"]
    try:
        sender = supabase.table("profiles").select("name").eq("id", user["sub"]).single().execute().data
        sender_name = sender.get("name") or sender_email
    except Exception:
        sender_name = sender_email

    import html as _html
    html = render_email_html(
        title=f"À propos de votre consultant {consultant['name']}",
        body_html=(
            f'<div style="background:#f5f5f7;border-radius:8px;padding:16px;font-size:14px;'
            f'line-height:1.6;color:#1d1d1f;white-space:pre-wrap;">{_html.escape(body.message.strip())}</div>'
        ),
        footer_note=f"Message envoyé par {sender_name} ({sender_email}). Répondez directement à cet email.",
    )
    text = (
        f"À propos de votre consultant {consultant['name']}\n\n"
        f"{body.message.strip()}\n\n"
        f"{sender_name} ({sender_email}), Groupement-IT. Répondez directement à cet email."
    )

    ok, err = send_email(owner["email"], body.subject.strip(), html, text=text, reply_to=sender_email)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Échec d'envoi de l'email : {err}")

    return {"message": "Email envoyé au partenaire.", "partner_email": owner["email"]}


@router.delete("/{consultant_id}")
async def delete_consultant(consultant_id: str, user: dict = Depends(get_current_user)):
    try:
        consultant = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute().data
        if user["role"] != "admin" and consultant["created_by"] != user["sub"]:
            raise HTTPException(status_code=403, detail="Accès interdit")
        supabase.table("consultants").delete().eq("id", consultant_id).execute()
        return {"message": "Consultant supprimé"}
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
