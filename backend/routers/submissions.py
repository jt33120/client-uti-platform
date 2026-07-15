from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from typing import Optional
from services.supabase_client import supabase
from services import storage
from services.cv_parser import (
    extract_text_from_pdf, extract_text_from_docx, extract_text_from_xlsx, guess_extension,
)
from services.matching_runner import auto_rescore_ao
from services.consultant_skills import auto_extract_skills
from services.cv_structured import build_structured_bg
from routers.auth import get_current_user, is_staff
import uuid

router = APIRouter(prefix="/submissions", tags=["submissions"])

# Extension → (content-type de stockage, extracteur de texte). Le format réel
# est déterminé par l'extension du nom de fichier (le content-type envoyé par
# le navigateur est peu fiable pour docx/xlsx), même approche que la
# génération d'AO à partir de pièces jointes (routers/aos.py).
ALLOWED_CV_EXTENSIONS = {
    "pdf": ("application/pdf", extract_text_from_pdf),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        extract_text_from_docx,
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extract_text_from_xlsx,
    ),
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _check_ao_access(ao_id: str, user: dict) -> dict:
    """
    Ensure the user can access this AO.
    Admin → ok. Partner → must have list_1/list_2 access to the AO's client.
    Returns the AO row.
    """
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="AO introuvable")

    if is_staff(user):
        return ao

    access = supabase.table("partner_clients").select("tier").eq(
        "partner_id", user["sub"]
    ).eq("client_id", ao["client_id"]).in_("tier", ["list_1", "list_2"]).execute()

    if not access.data:
        raise HTTPException(status_code=403, detail="Vous n'avez pas accès à cet AO")

    return ao


@router.post("")
async def create_submission(
    background_tasks: BackgroundTasks,
    ao_id: str = Form(...),
    consultant_id: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    tjm: Optional[int] = Form(None, ge=0, le=100_000),
    skills: Optional[str] = Form(None),
    experience_years: Optional[int] = Form(None, ge=0, le=70),
    employment_type: Optional[str] = Form(None),
    availability: Optional[str] = Form(None),
    worked_at_client: Optional[bool] = Form(None),
    worked_at_client_exit_date: Optional[str] = Form(None),
    points_forts: Optional[str] = Form(None),
    elements_differenciants: Optional[str] = Form(None),
    consent: bool = Form(False),
    cv_file: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    """
    Submit a CV to an AO.

    Two modes:
    - Pass `consultant_id` to reuse an existing vivier consultant. Le CV est
      alors FACULTATIF : si aucun PDF n'est joint, on réutilise le dernier CV
      déjà présent au vivier pour ce consultant (copié vers la nouvelle
      soumission pour rester indépendant de l'original).
    - Pass consultant fields (name, skills, ...) to create + submit in one shot
      (un CV est requis dans ce cas).
    """
    ao_row = _check_ao_access(ao_id, user)
    # Un AO archivé est clos : on refuse toute nouvelle réponse (l'historique
    # « Mes réponses » peut l'ouvrir, mais plus y soumettre). .get() tolère
    # l'absence de la colonne (migration 0003 non appliquée -> None -> autorisé).
    if ao_row.get("archived"):
        raise HTTPException(status_code=409, detail="Cet appel d'offres est archivé : les réponses sont closes.")

    # RGPD — explicit consent is mandatory before any CV (personal data) is
    # uploaded, parsed, stored and sent to the AI matching model.
    if not consent:
        raise HTTPException(
            status_code=422,
            detail="Le consentement RGPD est requis pour soumettre un CV.",
        )

    # Validate the uploaded file up-front (when one is provided). Le format est
    # déterminé par l'extension du nom de fichier (PDF, Word .docx, Excel .xlsx).
    file_bytes = None
    cv_ext = None
    if cv_file is not None:
        cv_ext = guess_extension(cv_file.filename, default="")
        if cv_ext not in ALLOWED_CV_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail="Seuls les fichiers PDF, Word (.docx) et Excel (.xlsx) sont acceptés",
            )
        file_bytes = await cv_file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="Fichier trop volumineux (max 10MB)")

    # Resolve consultant (create-on-the-fly or reuse)
    if consultant_id:
        try:
            consultant = supabase.table("consultants").select("*").eq("id", consultant_id).single().execute().data
        except Exception:
            raise HTTPException(status_code=404, detail="Consultant introuvable")
        if user["role"] == "ao" and consultant["created_by"] != user["sub"]:
            raise HTTPException(status_code=403, detail="Ce consultant ne vous appartient pas")
    else:
        # Création d'un nouveau consultant : un CV est obligatoire.
        if file_bytes is None:
            raise HTTPException(status_code=422, detail="Un CV (PDF, Word ou Excel) est requis pour un nouveau consultant.")
        if not name or not skills:
            raise HTTPException(status_code=400, detail="Nom et compétences requis pour créer un consultant")
        if employment_type and employment_type not in ("independant", "salarie"):
            raise HTTPException(status_code=400, detail="employment_type doit être 'independant' ou 'salarie'")
        consultant = supabase.table("consultants").insert({
            "name": name,
            "tjm": tjm,
            "skills": skills,
            "experience_years": experience_years,
            "employment_type": employment_type,
            "availability": availability,
            "created_by": user["sub"],
        }).execute().data[0]
        consultant_id = consultant["id"]

    # Refuse duplicate submission
    existing = supabase.table("submissions").select("id").eq(
        "ao_id", ao_id
    ).eq("consultant_id", consultant_id).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Ce consultant a déjà été soumis à cet AO")

    submission_uuid = str(uuid.uuid4())

    if file_bytes is not None:
        # Nouveau fichier fourni : extraction (selon l'extension) + upload.
        content_type, extractor = ALLOWED_CV_EXTENSIONS[cv_ext]
        try:
            cv_text = extractor(file_bytes)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Impossible de lire le fichier : {str(e)}")
        if not cv_text or len(cv_text) < 50:
            raise HTTPException(status_code=422, detail="Le fichier semble vide ou illisible")
        cv_filename = cv_file.filename
        storage_path = f"{ao_id}/{submission_uuid}.{cv_ext}"
        try:
            cv_url = storage.upload("cvs", storage_path, file_bytes, content_type)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur upload CV: {str(e)}")
    else:
        # Pas de fichier : on réutilise le dernier CV du vivier pour ce consultant.
        prior = supabase.table("submissions").select(
            "cv_url, cv_text, cv_filename"
        ).eq("consultant_id", consultant_id).order("submitted_at", desc=True).limit(5).execute().data or []
        src = next((p for p in prior if p.get("cv_url") and p.get("cv_text")), None)
        if not src:
            raise HTTPException(
                status_code=422,
                detail="Aucun CV existant pour ce consultant au vivier : veuillez joindre un fichier.",
            )
        cv_text = src["cv_text"]
        cv_filename = src.get("cv_filename") or "CV.pdf"
        prior_ext = guess_extension(cv_filename)
        if prior_ext not in ALLOWED_CV_EXTENSIONS:
            prior_ext = "pdf"
        content_type = ALLOWED_CV_EXTENSIONS[prior_ext][0]
        storage_path = f"{ao_id}/{submission_uuid}.{prior_ext}"
        # Copie du fichier vers la nouvelle soumission (indépendant de l'original).
        try:
            data = storage.download("cvs", storage._object_path("cvs", src["cv_url"]))
            cv_url = storage.upload("cvs", storage_path, data, content_type)
        except Exception:
            # Repli : on référence l'objet existant (best-effort).
            cv_url = src["cv_url"]

    # Insert submission
    try:
        sub = supabase.table("submissions").insert({
            "id": submission_uuid,
            "ao_id": ao_id,
            "consultant_id": consultant_id,
            "cv_url": cv_url,
            "cv_text": cv_text,
            "cv_filename": cv_filename,
            "submitted_by": user["sub"],
            # Historique d'intervention chez le client (demande Sullyvan)
            "worked_at_client": worked_at_client,
            "worked_at_client_exit_date": (worked_at_client_exit_date or None),
            # Évaluation renseignée par le partenaire à la soumission
            "points_forts": (points_forts or "").strip() or None,
            "elements_differenciants": (elements_differenciants or "").strip() or None,
        }).execute().data[0]
    except Exception as e:
        try:
            storage.remove("cvs", [storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Erreur création soumission: {str(e)}")

    # Auto-pipeline: every new CV triggers a re-score of the AO so the
    # ranking stays current without anyone pressing a button.
    # CV structuré canonique (format GRP-IT) AVANT le re-score : ainsi le tout
    # premier scoring cite déjà des extraits du CV structuré (surlignage exact).
    # Best-effort ; en cas d'échec le scoring retombe sur le CV brut.
    background_tasks.add_task(build_structured_bg, submission_uuid)
    # Auto-pipeline : chaque nouveau CV déclenche un re-score de l'AO.
    background_tasks.add_task(auto_rescore_ao, ao_id, user["sub"])
    # Enrichissement du vivier : déduire les compétences du CV si le consultant
    # n'en a pas encore (best-effort, ne bloque pas la soumission).
    background_tasks.add_task(auto_extract_skills, consultant_id)

    sub["consultant"] = consultant
    return sub


@router.get("/ao/{ao_id}")
async def list_submissions_for_ao(ao_id: str, user: dict = Depends(get_current_user)):
    """
    Admin: sees every submission for this AO.
    Partner: sees only their own submissions for this AO.
    """
    _check_ao_access(ao_id, user)
    try:
        # Staff get submitter profile; partners only see their own submissions
        if is_staff(user):
            select = (
                "*, "
                "consultants(id, name, tjm, skills, experience_years, employment_type, availability), "
                "submitter:profiles!submitted_by(id, name, email)"
            )
        else:
            select = "*, consultants(id, name, tjm, skills, experience_years, employment_type, availability)"

        query = supabase.table("submissions").select(select).eq(
            "ao_id", ao_id
        ).order("submitted_at", desc=True)

        if user["role"] == "ao":
            query = query.eq("submitted_by", user["sub"])

        rows = query.execute().data or []
        # Serve CVs via short-lived signed URLs (the 'cvs' bucket is private).
        for row in rows:
            if row.get("cv_url"):
                row["cv_url"] = storage.signed_cv_url(row["cv_url"])
        return rows
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/mine")
async def list_my_submissions(user: dict = Depends(get_current_user)):
    """Return all submissions made by the current user, with AO title."""
    try:
        return supabase.table("submissions").select(
            "id, ao_id, submitted_at, appels_offres(title)"
        ).eq("submitted_by", user["sub"]).order("submitted_at", desc=True).execute().data
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


def _partner_outcome(st: dict) -> str:
    """Issue ABSOLUE d'un candidat (label partenaire), sans jamais exposer le rang
    (qui trahirait le nombre de concurrents devant). Champs déjà communiqués au
    partenaire par e-mail (deal_status, validation, présenté client)."""
    if not st:
        return "soumis"
    if st.get("deal_status") == "gagnee":
        return "gagne"
    if st.get("deal_status") == "perdue":
        return "perdu"
    if st.get("validation") == "non_retenu":
        return "ecarte"
    if st.get("sent_to_client_at"):
        return "presente"
    if st.get("validation") == "retenu":
        return "retenu"
    if st.get("contact_status") in ("contacted", "proposed"):
        return "contacte"
    return "soumis"


@router.get("/mine/outcomes")
async def my_response_outcomes(user: dict = Depends(get_current_user)):
    """« Mes réponses » enrichi : un bloc par AO où l'utilisateur a proposé un CV,
    avec l'issue de SES candidats (score + label retenu / écarté / présenté /
    gagné / perdu). Cloisonné : filtre propriété d'abord, puis les jointures
    (état, score) sont bornées à SES paires (ao, consultant) — jamais de données
    concurrentes, et le rang inter-candidats n'est pas exposé."""
    uid = user["sub"]
    try:
        subs = supabase.table("submissions").select(
            "ao_id, consultant_id, submitted_at, consultants(name)"
        ).eq("submitted_by", uid).order("submitted_at", desc=True).execute().data or []
    except Exception:
        subs = []
    if not subs:
        return {"aos": []}

    ao_ids = list({s["ao_id"] for s in subs if s.get("ao_id")})
    cids = list({s["consultant_id"] for s in subs if s.get("consultant_id")})

    # Métadonnées AO (repli si colonnes récentes absentes).
    ao_by: dict = {}
    if ao_ids:
        try:
            rows = supabase.table("appels_offres").select(
                "id, title, reference, status, archived, ao_outcome, winning_partner_id, client_id, clients(name)"
            ).in_("id", ao_ids).execute().data or []
        except Exception:
            try:
                rows = supabase.table("appels_offres").select(
                    "id, title, reference, status, client_id"
                ).in_("id", ao_ids).execute().data or []
            except Exception:
                rows = []
        ao_by = {r["id"]: r for r in rows}

    # État candidat — SEULEMENT mes paires (ao, consultant). On ne sélectionne PAS
    # les notes internes (eval_*), ni partenaire/contact cible (staff only).
    state_by: dict = {}
    if ao_ids and cids:
        try:
            for r in supabase.table("ao_consultant_state").select(
                "ao_id, consultant_id, contact_status, contacted_at, validation, "
                "deal_status, sent_to_client_at, commercial_exchange"
            ).in_("ao_id", ao_ids).in_("consultant_id", cids).execute().data or []:
                state_by[(r["ao_id"], r["consultant_id"])] = r
        except Exception:
            pass

    # Score (matchings.consultant_id est TEXT -> cast str pour ne pas perdre les lignes).
    score_by: dict = {}
    if ao_ids and cids:
        try:
            for m in supabase.table("matchings").select(
                "ao_id, consultant_id, score_total"
            ).in_("ao_id", ao_ids).in_("consultant_id", [str(c) for c in cids]).execute().data or []:
                score_by[(m["ao_id"], str(m["consultant_id"]))] = m.get("score_total")
        except Exception:
            pass

    by_ao: dict = {}
    for s in subs:
        aid, cid = s.get("ao_id"), s.get("consultant_id")
        if not aid:
            continue
        st = state_by.get((aid, cid), {})
        # Réconciliation avec le bilan AO : le deal_status candidat ne doit pas
        # contredire l'issue posée (jamais « gagné » si l'AO est non pourvu, ou
        # pourvu par un AUTRE partenaire).
        ao_meta = ao_by.get(aid) or {}
        ao_outcome = ao_meta.get("ao_outcome")
        is_winner = ao_meta.get("winning_partner_id") == uid
        oc = _partner_outcome(st)
        if oc == "gagne" and (ao_outcome in ("non_pourvu", "sans_suite")
                              or (ao_outcome == "pourvu" and not is_winner)):
            oc = "perdu"
        entry = by_ao.setdefault(aid, {"ao_id": aid, "candidates": []})
        entry["candidates"].append({
            "consultant_name": (s.get("consultants") or {}).get("name"),
            "submitted_at": s.get("submitted_at"),
            "score": score_by.get((aid, str(cid))),
            "outcome": oc,
            "contacted": st.get("contact_status") in ("contacted", "proposed"),
        })

    out = []
    for aid, entry in by_ao.items():
        ao = ao_by.get(aid) or {}
        last = max((c["submitted_at"] for c in entry["candidates"] if c.get("submitted_at")), default=None)
        out.append({
            **entry,
            "ao_title": ao.get("title") or "Appel d'offres",
            "ao_reference": ao.get("reference"),
            "ao_status": ao.get("status"),
            "ao_archived": bool(ao.get("archived")),
            "client_name": (ao.get("clients") or {}).get("name") if isinstance(ao.get("clients"), dict) else None,
            "submitted_at": last,
        })
    out.sort(key=lambda x: x.get("submitted_at") or "", reverse=True)
    return {"aos": out}


@router.delete("/{submission_id}")
async def delete_submission(submission_id: str, user: dict = Depends(get_current_user)):
    try:
        sub = supabase.table("submissions").select("*").eq("id", submission_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Soumission introuvable")

    if user["role"] != "admin" and sub["submitted_by"] != user["sub"]:
        raise HTTPException(status_code=403, detail="Accès interdit")

    # Attempt to delete the file from storage (best-effort)
    try:
        path = f"{sub['ao_id']}/{submission_id}.{guess_extension(sub.get('cv_filename'))}"
        storage.remove("cvs", [path])
    except Exception:
        pass

    supabase.table("submissions").delete().eq("id", submission_id).execute()
    return {"message": "Soumission supprimée"}
