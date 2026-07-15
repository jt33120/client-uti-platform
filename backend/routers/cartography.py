"""
Cartographie géographique (staff UTI) : points consultants & AO pour la carte.

Les coordonnées sont géocodées et mises en cache à la création/màj (BAN).
Best-effort : si les colonnes géo n'existent pas encore, on renvoie des listes
vides plutôt que d'échouer.
"""
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from services.supabase_client import supabase
from routers.auth import require_staff, require_admin

router = APIRouter(prefix="/map", tags=["cartography"])


@router.get("/points")
async def map_points(user: dict = Depends(require_staff)):
    """Consultants + AO + clients géolocalisés pour la carte.

    Les clients n'ont pas toujours de ville renseignée : à défaut, on les
    positionne au CENTROÏDE de leurs AO géolocalisés (centre de gravité de leur
    activité) — utile immédiatement, sans ressaisie."""
    consultants = []
    try:
        rows = supabase.table("consultants").select(
            "id, name, city, latitude, longitude, skills, tjm, availability_status"
        ).execute().data or []
        consultants = [r for r in rows if r.get("latitude") is not None and r.get("longitude") is not None]
    except Exception:
        # Repli si availability_status pas migrée.
        try:
            rows = supabase.table("consultants").select(
                "id, name, city, latitude, longitude, skills, tjm"
            ).execute().data or []
            consultants = [r for r in rows if r.get("latitude") is not None and r.get("longitude") is not None]
        except Exception:
            pass

    aos = []
    try:
        aos = supabase.table("appels_offres").select(
            "id, title, location, work_mode, latitude, longitude, status, client_id, clients(name)"
        ).execute().data or []
    except Exception:
        # Repli si `client_id` n'est pas sélectionnable : on garde les AO sur la
        # carte (les clients perdent juste leur position par centroïde d'AO).
        try:
            aos = supabase.table("appels_offres").select(
                "id, title, location, work_mode, latitude, longitude, status, clients(name)"
            ).execute().data or []
        except Exception:
            pass

    # Centroïde des AO géolocalisés par client (repli de position pour les clients).
    ao_centroids = defaultdict(list)
    for a in aos:
        if a.get("latitude") is not None and a.get("longitude") is not None and a.get("client_id"):
            ao_centroids[a["client_id"]].append((a["latitude"], a["longitude"]))

    clients = []
    try:
        crows = supabase.table("clients").select(
            "id, name, sector, city, latitude, longitude"
        ).execute().data or []
    except Exception:
        # Colonnes géo pas encore migrées : on garde le repli par centroïde AO.
        try:
            crows = supabase.table("clients").select("id, name, sector").execute().data or []
        except Exception:
            crows = []
    for c in crows:
        lat, lon, by = c.get("latitude"), c.get("longitude"), None
        pts = ao_centroids.get(c["id"]) or []
        if lat is not None and lon is not None:
            by = "city"
        elif pts:
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            by = "aos"
        if lat is not None and lon is not None:
            clients.append({
                "id": c["id"], "name": c.get("name"), "sector": c.get("sector"),
                "city": c.get("city"), "latitude": lat, "longitude": lon,
                "positioned_by": by, "ao_count": len(pts),
            })

    return {"consultants": consultants, "aos": aos, "clients": clients}


@router.get("/geocode")
async def geocode_place(q: str, user: dict = Depends(require_staff)):
    """Géocode un lieu libre (ville/adresse FR) → centre de recherche par périmètre."""
    from services.geocoding import geocode
    res = await geocode(q)
    if not res:
        raise HTTPException(status_code=404, detail="Lieu introuvable")
    return res


@router.post("/backfill")
async def backfill_geocoding(user: dict = Depends(require_admin)):
    """
    Géocode (a posteriori) les fiches qui ont une localisation mais pas encore de
    coordonnées — typiquement les AO/consultants créés avant l'ajout de la carte.
    Idempotent : ne retouche que les fiches sans coordonnées. Best-effort par fiche.
    """
    from services.geocoding import geocode

    ao_done = 0
    try:
        aos = supabase.table("appels_offres").select(
            "id, location, work_mode, latitude, longitude"
        ).execute().data or []
        for a in aos:
            if a.get("latitude") is not None and a.get("longitude") is not None:
                continue
            if not a.get("location") or a.get("work_mode") == "remote":
                continue
            geo = await geocode(a["location"])
            if not geo:
                continue
            try:
                supabase.table("appels_offres").update(
                    {"latitude": geo["latitude"], "longitude": geo["longitude"]}
                ).eq("id", a["id"]).execute()
                ao_done += 1
            except Exception:
                pass
    except Exception:
        pass

    co_done = 0
    try:
        cons = supabase.table("consultants").select(
            "id, city, latitude, longitude"
        ).execute().data or []
        for c in cons:
            if c.get("latitude") is not None and c.get("longitude") is not None:
                continue
            if not c.get("city"):
                continue
            geo = await geocode(c["city"])
            if not geo:
                continue
            try:
                supabase.table("consultants").update(
                    {"latitude": geo["latitude"], "longitude": geo["longitude"]}
                ).eq("id", c["id"]).execute()
                co_done += 1
            except Exception:
                pass
    except Exception:
        pass

    # Clients : géocode ceux qui ont une ville renseignée mais pas de coordonnées.
    cl_done = 0
    try:
        cls = supabase.table("clients").select("id, city, latitude, longitude").execute().data or []
        for c in cls:
            if c.get("latitude") is not None and c.get("longitude") is not None:
                continue
            if not c.get("city"):
                continue
            geo = await geocode(c["city"])
            if not geo:
                continue
            try:
                supabase.table("clients").update(
                    {"latitude": geo["latitude"], "longitude": geo["longitude"]}
                ).eq("id", c["id"]).execute()
                cl_done += 1
            except Exception:
                pass
    except Exception:
        # Colonnes géo clients pas encore migrées : on ignore.
        pass

    return {"aos_geocoded": ao_done, "consultants_geocoded": co_done, "clients_geocoded": cl_done}
