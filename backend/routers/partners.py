from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Literal
from services.supabase_client import supabase
from routers.auth import get_current_user, require_admin, require_staff
from config import settings
import httpx

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
    Permanently delete a partner: removes their profile row (which cascades
    to partner_clients) and their Supabase Auth account.
    """
    try:
        # Delete profile row — partner_clients FK cascades automatically
        supabase.table("profiles").delete().eq("id", partner_id).eq("role", "ao").execute()
        # Delete Supabase Auth user via direct HTTP (bypasses gotrue-py header bug)
        with httpx.Client(timeout=10) as client:
            client.delete(
                f"{settings.supabase_url}/auth/v1/admin/users/{partner_id}",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
        return {"message": "Partenaire supprimé"}
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
