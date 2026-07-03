from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Literal
from services.supabase_client import supabase
from routers.auth import require_admin

router = APIRouter(prefix="/pacs", tags=["pacs"])


Tier = Literal["list_1", "list_2", "suspended"]


class PacClientItem(BaseModel):
    client_id: str
    tier: Tier


class PacCreate(BaseModel):
    name: str
    description: Optional[str] = None
    clients: List[PacClientItem] = []


class PacUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class PacClientUpsert(BaseModel):
    client_id: str
    tier: Tier


@router.get("")
async def list_pacs(user: dict = Depends(require_admin)):
    """List all PACs with their client counts."""
    try:
        pacs = supabase.table("pacs").select("*").order("created_at", desc=True).execute().data
        # Get all pac_clients rows in a single query and aggregate
        all_rows = supabase.table("pac_clients").select("pac_id").execute().data
        counts = {}
        for row in all_rows:
            counts[row["pac_id"]] = counts.get(row["pac_id"], 0) + 1
        for p in pacs:
            p["client_count"] = counts.get(p["id"], 0)
        return pacs
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.post("")
async def create_pac(body: PacCreate, user: dict = Depends(require_admin)):
    """Create a new PAC, optionally with initial clients."""
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
    try:
        # Case-insensitive duplicate check
        existing = supabase.table("pacs").select("id, name").ilike("name", name).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail=f"Un PAC nommé « {existing.data[0]['name']} » existe déjà")

        pac = supabase.table("pacs").insert({
            "name": name,
            "description": body.description,
            "created_by": user["sub"],
        }).execute().data[0]

        if body.clients:
            rows = [
                {"pac_id": pac["id"], "client_id": c.client_id, "tier": c.tier}
                for c in body.clients
            ]
            supabase.table("pac_clients").insert(rows).execute()

        pac["client_count"] = len(body.clients)
        return pac
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.get("/{pac_id}")
async def get_pac(pac_id: str, user: dict = Depends(require_admin)):
    """Get a PAC with its full client list and tiers."""
    try:
        pac = supabase.table("pacs").select("*").eq("id", pac_id).single().execute().data
        if not pac:
            raise HTTPException(status_code=404, detail="PAC introuvable")

        rows = supabase.table("pac_clients").select("client_id, tier").eq(
            "pac_id", pac_id
        ).execute().data

        # Hydrate with client info
        client_ids = [r["client_id"] for r in rows]
        clients_map = {}
        if client_ids:
            clients_data = supabase.table("clients").select(
                "id, name, sector, logo_url"
            ).in_("id", client_ids).execute().data
            clients_map = {c["id"]: c for c in clients_data}

        pac["clients"] = [
            {**clients_map.get(r["client_id"], {"id": r["client_id"]}), "tier": r["tier"]}
            for r in rows
            if r["client_id"] in clients_map
        ]
        return pac
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="PAC introuvable")


@router.patch("/{pac_id}")
async def update_pac(pac_id: str, body: PacUpdate, user: dict = Depends(require_admin)):
    """Rename / update description."""
    update = body.model_dump(exclude_none=True)
    if "name" in update:
        update["name"] = update["name"].strip()
        if len(update["name"]) < 2:
            raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
        existing = supabase.table("pacs").select("id, name").ilike(
            "name", update["name"]
        ).neq("id", pac_id).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail=f"Un PAC nommé « {existing.data[0]['name']} » existe déjà")
    try:
        response = supabase.table("pacs").update(update).eq("id", pac_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="PAC introuvable")
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.delete("/{pac_id}")
async def delete_pac(pac_id: str, user: dict = Depends(require_admin)):
    """Delete a PAC (cascades to pac_clients). Does NOT touch partner_clients."""
    try:
        supabase.table("pacs").delete().eq("id", pac_id).execute()
        return {"message": "PAC supprimé"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.put("/{pac_id}/clients")
async def upsert_pac_client(pac_id: str, body: PacClientUpsert, user: dict = Depends(require_admin)):
    """Add a client to a PAC or update its tier."""
    try:
        existing = supabase.table("pac_clients").select("id").eq(
            "pac_id", pac_id
        ).eq("client_id", body.client_id).execute()

        if existing.data:
            response = supabase.table("pac_clients").update({
                "tier": body.tier,
            }).eq("pac_id", pac_id).eq("client_id", body.client_id).execute()
        else:
            response = supabase.table("pac_clients").insert({
                "pac_id": pac_id,
                "client_id": body.client_id,
                "tier": body.tier,
            }).execute()
        return response.data[0] if response.data else {"status": "ok"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


@router.delete("/{pac_id}/clients/{client_id}")
async def remove_pac_client(pac_id: str, client_id: str, user: dict = Depends(require_admin)):
    """Remove a client from a PAC."""
    try:
        supabase.table("pac_clients").delete().eq("pac_id", pac_id).eq(
            "client_id", client_id
        ).execute()
        return {"message": "Client retiré du PAC"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
