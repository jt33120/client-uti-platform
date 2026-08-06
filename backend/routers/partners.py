from datetime import date, datetime, timezone
import uuid

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, Literal
from services.supabase_client import supabase
from services import storage, partner_compliance
from routers.auth import get_current_user, require_admin, require_staff

router = APIRouter(prefix="/partners", tags=["partners"])


class AccessUpsert(BaseModel):
    partner_id: str
    client_id: str
    tier: Literal["list_1", "list_2", "suspended"]


class PartnerUpdate(BaseModel):
    name: str


@router.get("")
async def list_partners(user: dict = Depends(require_staff)):
    """List all users with role='ao' (partners)."""
    try:
        try:
            response = supabase.table("profiles").select(
                "id, email, name, role, status, created_at"
            ).eq("role", "ao").order("name").execute()
        except Exception:
            # 'status' column not migrated yet — degrade gracefully.
            response = supabase.table("profiles").select(
                "id, email, name, role, created_at"
            ).eq("role", "ao").order("name").execute()
        return response.data
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/access")
async def list_all_access(user: dict = Depends(require_staff)):
    """Return all partner_clients rows. Used to build the access matrix UI."""
    try:
        response = supabase.table("partner_clients").select("*").execute()
        return response.data
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/{partner_id}/clients")
async def list_clients_for_partner(partner_id: str, user: dict = Depends(require_staff)):
    """
    Returns all clients with this partner's tier for each.
    Clients without any row in partner_clients get tier=None.
    Mirror of GET /clients/{client_id}/partners.
    """
    try:
        # Verify partner exists
        try:
            partner = supabase.table("profiles").select("id, email, name, status, created_at").eq(
                "id", partner_id
            ).eq("role", "ao").single().execute()
        except Exception:
            partner = supabase.table("profiles").select("id, email, name, created_at").eq(
                "id", partner_id
            ).eq("role", "ao").single().execute()
        if not partner.data:
            raise HTTPException(status_code=404, detail="Partenaire introuvable")

        clients = supabase.table("clients").select(
            "id, name, sector, logo_url, created_at"
        ).order("name").execute().data

        access_rows = supabase.table("partner_clients").select("client_id, tier").eq(
            "partner_id", partner_id
        ).execute().data

        tiers = {row["client_id"]: row["tier"] for row in access_rows}
        for c in clients:
            c["tier"] = tiers.get(c["id"])
        return {"partner": partner.data, "clients": clients}
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.put("/access")
async def upsert_access(body: AccessUpsert, user: dict = Depends(require_admin)):
    """
    Set or update a partner's tier for a client.
    Tier values: 'list_1', 'list_2', 'suspended'.
    """
    try:
        # Check if row exists
        existing = supabase.table("partner_clients").select("id").eq(
            "partner_id", body.partner_id
        ).eq("client_id", body.client_id).execute()

        if existing.data:
            response = supabase.table("partner_clients").update({
                "tier": body.tier,
                "assigned_by": user["sub"],
            }).eq("partner_id", body.partner_id).eq("client_id", body.client_id).execute()
        else:
            response = supabase.table("partner_clients").insert({
                "partner_id": body.partner_id,
                "client_id": body.client_id,
                "tier": body.tier,
                "assigned_by": user["sub"],
            }).execute()

        return response.data[0] if response.data else {"status": "ok"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.post("/{partner_id}/apply-pac/{pac_id}")
async def apply_pac_to_partner(partner_id: str, pac_id: str, user: dict = Depends(require_admin)):
    """
    Apply a PAC to a partner: batch upsert partner_clients rows.
    If a partner already has a tier for a client in the PAC, it is overwritten.
    Clients not in the PAC are left untouched.
    """
    try:
        # Verify partner exists and is an AO
        partner = supabase.table("profiles").select("id").eq(
            "id", partner_id
        ).eq("role", "ao").execute()
        if not partner.data:
            raise HTTPException(status_code=404, detail="Partenaire introuvable")

        # Verify PAC exists
        pac = supabase.table("pacs").select("id, name").eq("id", pac_id).execute()
        if not pac.data:
            raise HTTPException(status_code=404, detail="PAC introuvable")

        # Get PAC client rows
        pac_clients = supabase.table("pac_clients").select("client_id, tier").eq(
            "pac_id", pac_id
        ).execute().data

        if not pac_clients:
            return {"message": "PAC vide, aucune affectation appliquée", "count": 0}

        # Get existing partner_clients rows for the clients in the PAC
        client_ids = [r["client_id"] for r in pac_clients]
        existing = supabase.table("partner_clients").select("client_id").eq(
            "partner_id", partner_id
        ).in_("client_id", client_ids).execute().data
        existing_set = {r["client_id"] for r in existing}

        # Split into updates and inserts
        to_insert = []
        for row in pac_clients:
            if row["client_id"] in existing_set:
                supabase.table("partner_clients").update({
                    "tier": row["tier"],
                    "assigned_by": user["sub"],
                }).eq("partner_id", partner_id).eq("client_id", row["client_id"]).execute()
            else:
                to_insert.append({
                    "partner_id": partner_id,
                    "client_id": row["client_id"],
                    "tier": row["tier"],
                    "assigned_by": user["sub"],
                })

        if to_insert:
            supabase.table("partner_clients").insert(to_insert).execute()

        return {
            "message": f"PAC « {pac.data[0]['name']} » appliqué",
            "count": len(pac_clients),
        }
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.post("/{partner_id}/suspend")
async def suspend_partner_globally(partner_id: str, user: dict = Depends(require_admin)):
    """Set all existing partner_clients rows for this partner to 'suspended'."""
    try:
        supabase.table("partner_clients").update({
            "tier": "suspended",
            "assigned_by": user["sub"],
        }).eq("partner_id", partner_id).execute()
        return {"message": "Partenaire suspendu sur tous les clients"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.delete("/access")
async def remove_access(partner_id: str, client_id: str, user: dict = Depends(require_admin)):
    """Remove a partner's access to a client entirely."""
    try:
        supabase.table("partner_clients").delete().eq(
            "partner_id", partner_id
        ).eq("client_id", client_id).execute()
        return {"message": "Accès retiré"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.patch("/{partner_id}")
async def update_partner(partner_id: str, body: PartnerUpdate, user: dict = Depends(require_admin)):
    """Update a partner's display name."""
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
    try:
        response = supabase.table("profiles").update({"name": name}).eq(
            "id", partner_id
        ).eq("role", "ao").execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Partenaire introuvable.")
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.delete("/{partner_id}")
async def delete_partner(partner_id: str, user: dict = Depends(require_admin)):
    """
    Supprime définitivement un partenaire : la suppression du profil entraîne en
    cascade `partner_clients` (clé étrangère historique) ET `user_credentials`
    (migration 0018). Il n'y a plus d'utilisateur GoTrue à supprimer à part.

    Le filtre `.eq("role", "ao")` protège l'endpoint contre un identifiant de
    compte interne : il ne supprime que des partenaires. Comme l'ancien appel
    GoTrue, lui, ne filtrait rien, un identifiant d'administrateur passé ici
    laissait le profil intact mais détruisait son compte d'authentification —
    le compte devenait inutilisable sans que rien ne le signale. Ce n'est plus
    possible : il n'y a qu'une seule suppression, et elle est filtrée.
    """
    try:
        supabase.table("profiles").delete().eq("id", partner_id).eq("role", "ao").execute()
        return {"message": "Partenaire supprimé"}
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


# ── Conformité partenaire (obligation de vigilance, art. L.8222-1) ──────────
#
# Mode ALERTE, jamais blocage. L'obligation se rattache au CONTRAT de prestation
# (≥ 5 000 € HT par opération), pas à la présentation d'une candidature : bloquer
# l'envoi d'un CV serait juridiquement inutile et commercialement absurde sur une
# plateforme qui cherche encore du volume. Le blocage viendra avec le bon de
# commande, quand il existera.
#
# Historique conservé : chaque dépôt crée une ligne, jamais de mise à jour en
# place. L'attestation d'il y a huit mois doit rester consultable pour démontrer
# qu'on la demandait bien à l'époque.

_COMPLIANCE_BUCKET = "compliance"
_MAX_DOC_BYTES = 10 * 1024 * 1024


def _load_docs(partner_id: str) -> list[dict]:
    try:
        return supabase.table("partner_compliance_docs").select("*").eq(
            "partner_id", partner_id
        ).order("issued_at", desc=True).execute().data or []
    except Exception:
        # Table non migrée : l'écran partenaire doit rester utilisable.
        return []


@router.get("/{partner_id}/compliance")
async def get_partner_compliance(partner_id: str, user: dict = Depends(require_staff)):
    """Pièces de conformité d'un partenaire et leur état."""
    docs = _load_docs(partner_id)
    return {"docs": docs, **partner_compliance.partner_status(docs)}


@router.get("/compliance/overview")
async def compliance_overview(user: dict = Depends(require_staff)):
    """État de conformité de TOUS les partenaires actifs, pour la vue d'ensemble.

    Une pièce manquante ne se voit pas en ouvrant les fiches une par une : c'est
    précisément ce qu'on oublie de faire. D'où une vue agrégée.
    """
    try:
        partners = supabase.table("profiles").select("id, name, email").eq(
            "role", "ao"
        ).eq("status", "active").execute().data or []
    except Exception:
        partners = []
    try:
        all_docs = supabase.table("partner_compliance_docs").select("*").execute().data or []
    except Exception:
        all_docs = []

    by_partner: dict[str, list[dict]] = {}
    for d in all_docs:
        by_partner.setdefault(d.get("partner_id"), []).append(d)

    rows = []
    for p in partners:
        st = partner_compliance.partner_status(by_partner.get(p["id"], []))
        rows.append({
            "partner_id": p["id"], "name": p.get("name"), "email": p.get("email"),
            "overall": st["overall"], "ok": st["ok"],
            "by_type": {t: v["state"] for t, v in st["by_type"].items()},
        })
    order = {"missing": 0, "expired": 1, "unverified": 2, "expiring": 3, "valid": 4}
    rows.sort(key=lambda r: (order.get(r["overall"], 9), (r["name"] or "").lower()))
    return {"partners": rows, "at_risk": sum(1 for r in rows if not r["ok"])}


@router.post("/{partner_id}/compliance")
async def upload_compliance_doc(
    partner_id: str,
    doc_type: str = Form(...),
    issued_at: str = Form(...),
    file: UploadFile = File(None),
    user: dict = Depends(require_staff),
):
    """Dépose une pièce de conformité.

    `issued_at` est la date d'ÉMISSION de la pièce, pas celle du dépôt : c'est
    elle qui fait courir la validité. Une attestation URSSAF vieille de cinq mois
    déposée aujourd'hui n'est plus valable qu'un mois.
    """
    if doc_type not in partner_compliance.DOC_TYPES:
        raise HTTPException(status_code=422, detail="Type de pièce inconnu.")
    try:
        issued = date.fromisoformat(issued_at)
    except ValueError:
        raise HTTPException(status_code=422, detail="Date d'émission invalide (AAAA-MM-JJ).")
    if issued > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=422, detail="La date d'émission ne peut pas être dans le futur.")

    record = {
        "partner_id": partner_id,
        "doc_type": doc_type,
        "issued_at": issued.isoformat(),
        "uploaded_by": user["sub"],
    }

    if file is not None:
        content = await file.read()
        if len(content) > _MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail="Fichier trop volumineux (10 Mo maximum).")
        if content:
            storage.ensure_bucket(_COMPLIANCE_BUCKET, public=False)
            safe = (file.filename or "piece").replace("/", "_")[-120:]
            path = f"{partner_id}/{uuid.uuid4().hex}-{safe}"
            try:
                url = storage.upload(
                    _COMPLIANCE_BUCKET, path, content,
                    file.content_type or "application/octet-stream",
                )
            except Exception:
                raise HTTPException(status_code=500, detail="Dépôt du fichier impossible.")
            record["file_url"] = url
            record["filename"] = safe

    try:
        created = supabase.table("partner_compliance_docs").insert(record).execute().data
    except Exception:
        raise HTTPException(status_code=500, detail="Enregistrement de la pièce impossible.")
    return (created or [{}])[0]


class AuthenticityCheck(BaseModel):
    authenticity_ref: Optional[str] = None


@router.post("/{partner_id}/compliance/{doc_id}/verify")
async def verify_compliance_doc(
    partner_id: str, doc_id: str,
    body: AuthenticityCheck,
    user: dict = Depends(require_staff),
):
    """Consigne la vérification d'authenticité auprès de l'URSSAF.

    Détenir l'attestation ne suffit pas : le texte impose de s'assurer de son
    authenticité. Tant que cette vérification n'est pas consignée, la pièce est
    comptée « non vérifiée » et le partenaire n'est pas en règle.
    """
    patch = {
        "authenticity_checked_at": datetime.now(timezone.utc).isoformat(),
        "checked_by": user["sub"],
    }
    if body.authenticity_ref:
        patch["authenticity_ref"] = body.authenticity_ref.strip()[:64]
    try:
        supabase.table("partner_compliance_docs").update(patch).eq(
            "id", doc_id
        ).eq("partner_id", partner_id).execute()
    except Exception:
        raise HTTPException(status_code=500, detail="Enregistrement de la vérification impossible.")
    return {"ok": True, **patch}


@router.get("/{partner_id}/compliance/{doc_id}/file")
async def get_compliance_file(partner_id: str, doc_id: str, user: dict = Depends(require_staff)):
    """Octets de la pièce, servis par le backend (bucket privé)."""
    try:
        doc = supabase.table("partner_compliance_docs").select(
            "file_url, filename"
        ).eq("id", doc_id).eq("partner_id", partner_id).single().execute().data
    except Exception:
        doc = None
    stored = (doc or {}).get("file_url")
    if not stored:
        raise HTTPException(status_code=404, detail="Pièce introuvable.")
    try:
        data = storage.download(_COMPLIANCE_BUCKET, storage._object_path(_COMPLIANCE_BUCKET, stored))
    except Exception:
        raise HTTPException(status_code=404, detail="Pièce indisponible.")
    fname = (doc or {}).get("filename") or "piece"
    media = "application/pdf" if fname.lower().endswith(".pdf") else "application/octet-stream"
    return Response(
        content=bytes(data),
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )
