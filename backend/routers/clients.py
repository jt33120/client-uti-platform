from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.supabase_client import supabase
from routers.auth import get_current_user, require_admin, require_staff, is_staff

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sector: Optional[str] = None
    logo_url: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    parent_client_id: Optional[str] = None
    perimetre: Optional[str] = None


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sector: Optional[str] = None
    logo_url: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    parent_client_id: Optional[str] = None
    perimetre: Optional[str] = None


@router.post("")
async def create_client(body: ClientCreate, user: dict = Depends(require_admin)):
    try:
        normalized_name = body.name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="Le nom du client est requis")
        # Case-insensitive duplicate check
        existing = supabase.table("clients").select("id, name").ilike("name", normalized_name).execute()
        if existing.data:
            raise HTTPException(
                status_code=400,
                detail=f"Un client nommé « {existing.data[0]['name']} » existe déjà"
            )
        response = supabase.table("clients").insert({
            "name": normalized_name,
            "description": body.description,
            "sector": body.sector,
            "logo_url": body.logo_url,
            "contact_name": body.contact_name,
            "contact_email": body.contact_email,
            "parent_client_id": body.parent_client_id,
            "perimetre": (body.perimetre or "").strip() or None,
            "created_by": user["sub"],
        }).execute()
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("")
async def list_clients(user: dict = Depends(get_current_user)):
    """
    Staff (admin/commerce): sees all clients.
    Partner (ao): sees only clients they have list_1 or list_2 access to.
    """
    try:
        if is_staff(user):
            response = supabase.table("clients").select("*").order("name").execute()
            return response.data

        # Partner: filter by access
        access = supabase.table("partner_clients").select("client_id, tier").eq(
            "partner_id", user["sub"]
        ).in_("tier", ["list_1", "list_2"]).execute()

        if not access.data:
            return []

        client_ids = [row["client_id"] for row in access.data]
        tiers = {row["client_id"]: row["tier"] for row in access.data}

        clients = supabase.table("clients").select("*").in_("id", client_ids).order("name").execute()
        # Annotate each client with the partner's tier
        for c in clients.data:
            c["tier"] = tiers.get(c["id"])
        return clients.data
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/{client_id}/partners")
async def list_partners_for_client(client_id: str, user: dict = Depends(require_staff)):
    """
    Returns all partners (role='ao') with their access tier for this client.
    Partners without any row in partner_clients get tier=None.
    """
    try:
        partners = supabase.table("profiles").select(
            "id, email, name, created_at"
        ).eq("role", "ao").order("name").execute().data

        access_rows = supabase.table("partner_clients").select("partner_id, tier").eq(
            "client_id", client_id
        ).execute().data

        tiers = {row["partner_id"]: row["tier"] for row in access_rows}
        for p in partners:
            p["tier"] = tiers.get(p["id"])
        return partners
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/{client_id}")
async def get_client(client_id: str, user: dict = Depends(get_current_user)):
    # Un partenaire ne peut lire que les clients auxquels il a accès
    # (même règle que list_clients) — sinon l'UUID suffirait à lire la fiche
    # (nom, contacts) de n'importe quel client du groupement.
    tier = None
    if not is_staff(user):
        access = supabase.table("partner_clients").select("tier").eq(
            "partner_id", user["sub"]
        ).eq("client_id", client_id).in_("tier", ["list_1", "list_2"]).execute().data
        if not access:
            # 404 (et non 403) : ne pas confirmer l'existence de l'UUID.
            raise HTTPException(status_code=404, detail="Client introuvable")
        tier = access[0]["tier"]

    response = supabase.table("clients").select("*").eq("id", client_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Client introuvable")
    client = response.data[0]
    if tier:
        client["tier"] = tier
    return client


@router.patch("/{client_id}")
async def update_client(client_id: str, body: ClientUpdate, user: dict = Depends(require_admin)):
    try:
        update_data = body.model_dump(exclude_none=True)
        if "perimetre" in update_data:
            update_data["perimetre"] = (update_data["perimetre"] or "").strip() or None
        if "name" in update_data:
            update_data["name"] = update_data["name"].strip()
            # Case-insensitive duplicate check, excluding this client
            existing = supabase.table("clients").select("id, name").ilike(
                "name", update_data["name"]
            ).neq("id", client_id).execute()
            if existing.data:
                raise HTTPException(
                    status_code=400,
                    detail=f"Un client nommé « {existing.data[0]['name']} » existe déjà"
                )
        response = supabase.table("clients").update(update_data).eq("id", client_id).execute()
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.delete("/{client_id}")
async def delete_client(client_id: str, user: dict = Depends(require_admin)):
    try:
        supabase.table("clients").delete().eq("id", client_id).execute()
        return {"message": "Client supprimé"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
