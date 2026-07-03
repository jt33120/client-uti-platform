from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from config import settings, is_prod
from routers import auth, consultants, aos, matching, clients, partners, submissions, invitations, pacs, support, assistant, admin, gdpr, decisions, scoring_config, cartography, notifications, email_templates, cv
from mip_rum_middleware import MIPRumMiddleware

IS_PROD = is_prod()

app = FastAPI(
    title="G-IT Plateforme Partenaires — POC",
    description="API de matching IA entre consultants et Appels d'Offres",
    version="0.1.0",
    # Schéma d'API et UI interactives coupés en prod (ne pas exposer la surface).
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# ── MIP RUM — tracing distribué (inactif sans MIP_RUM_ENDPOINT/MIP_RUM_APP_ID) ──
# Lit le traceparent posé par le snippet RUM du frontend et expédie un span
# http.server (route template + durée + statut, rien d'autre) vers MIP RUM.
# Config via settings : le .env est chargé par pydantic-settings, pas exporté
# dans os.environ.
app.add_middleware(
    MIPRumMiddleware,
    endpoint=settings.mip_rum_endpoint,
    app_id=settings.mip_rum_app_id,
    api_key=settings.mip_rum_api_key,
)

# Vercel previews for THIS project/account only. Anchored regex — a substring
# check ("julian-talou" in origin) was bypassable by registering e.g.
# x-julian-talou.vercel.app on a free Vercel account.
import re
_VERCEL_PREVIEW_RE = re.compile(
    r"^https://(utiplatform|uti-platform)-[a-z0-9-]+-julian-talous-projects\.vercel\.app$"
)


def is_allowed_origin(origin: str) -> bool:
    """Allow the prod frontend, localhost, and this account's Vercel previews."""
    if not origin:
        return False
    allowed = [
        settings.frontend_url,
        "https://git-alpha-hazel.vercel.app",
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    if origin in allowed:
        return True
    return bool(_VERCEL_PREVIEW_RE.match(origin))


def _apply_security_headers(response: Response) -> None:
    """Hardened response headers — applied to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    # API renvoie du JSON : un CSP verrouillé n'a aucun effet de bord ici.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if IS_PROD:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    # Starlette MutableHeaders n'a pas .pop() — suppression sûre du header Server.
    if "server" in response.headers:
        del response.headers["server"]


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """En prod : message générique, jamais de stack trace au client."""
    import traceback
    from services.error_log import record
    print(f"[ERROR] {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}")
    # Rend le 500 visible dans la console admin (GET /admin/errors).
    record("http", f"{request.method} {request.url.path}", exc=exc, path=request.url.path)
    resp = JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur."})
    _apply_security_headers(resp)
    return resp

# ── CORS middleware with dynamic origin checking ──────────────
@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    
    # Handle preflight OPTIONS requests
    if request.method == "OPTIONS":
        if origin and is_allowed_origin(origin):
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Origin, traceparent, tracestate",
                    "Access-Control-Max-Age": "600",
                },
            )
        return Response(status_code=403)
    
    # Handle actual requests
    response = await call_next(request)
    if origin and is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    _apply_security_headers(response)
    return response

# ── Routers ───────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(invitations.router)
app.include_router(clients.router)
app.include_router(partners.router)
app.include_router(pacs.router)
app.include_router(consultants.router)
app.include_router(aos.router)
app.include_router(submissions.router)
app.include_router(matching.router)
app.include_router(decisions.router)
app.include_router(scoring_config.router)
app.include_router(support.router)
app.include_router(assistant.router)
app.include_router(admin.router)
app.include_router(gdpr.router)
app.include_router(cartography.router)
app.include_router(notifications.router)
app.include_router(email_templates.router)

@app.get("/")
def root():
    return {"status": "running", "docs": None if IS_PROD else "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Contrôle de connexion à la base au démarrage (non bloquant) ───
async def _check_db_ready() -> None:
    """Sonde Supabase au boot avec quelques essais. NE bloque JAMAIS le démarrage :
    en cas d'échec réseau transitoire au reboot du VPS, l'app démarre quand même
    (elle sert /health pour le proxy), et systemd/nginx n'interprètent pas ça
    comme un crash. On journalise juste l'état pour le diagnostic."""
    import asyncio
    from services.supabase_client import supabase
    for attempt in range(1, 6):
        try:
            supabase.table("profiles").select("id").limit(1).execute()
            print("[STARTUP] connexion Supabase OK")
            return
        except Exception as e:  # noqa: BLE001
            print(f"[STARTUP] Supabase injoignable (essai {attempt}/5): {e}")
            await asyncio.sleep(min(2 ** attempt, 20))
    print("[STARTUP] ⚠️  Supabase toujours injoignable — l'app démarre quand "
          "même ; les requêtes échoueront proprement (500) tant que la base ne "
          "répond pas.")


# ── Planificateur de notifications (mono-worker uvicorn) ──────────
@app.on_event("startup")
async def _startup():
    import asyncio
    from services.scheduler import run_scheduler
    # Contrôle DB en tâche de fond : ne retarde pas la disponibilité de /health.
    asyncio.create_task(_check_db_ready())
    asyncio.create_task(run_scheduler())