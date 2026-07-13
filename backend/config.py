from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    openai_api_key: Optional[str] = None
    openrouter_key: Optional[str] = None
    jwt_secret: str = "change-me-in-production"
    # Deployment environment: "production" (default, hardened) or "dev"/"local".
    # Drives /docs exposure, security headers and the JWT-secret guard below.
    app_env: str = "production"
    frontend_url: str = "https://git-alpha-hazel.vercel.app"
    admin_email: Optional[str] = None  # recipient for support/contact notifications

    # SMTP (Infomaniak) — transactional email delivery
    smtp_host: str = "mail.infomaniak.com"
    smtp_port: int = 587  # STARTTLS
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None  # defaults to smtp_user when unset
    smtp_from_name: str = "Plateforme GRP-IT"

    # File storage backend: "supabase" (default) or "s3" (OVH Object Storage)
    storage_backend: str = "supabase"
    s3_endpoint_url: Optional[str] = None  # e.g. https://s3.gra.io.cloud.ovh.net
    s3_region: str = "gra"
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket: Optional[str] = None  # single OVH bucket; "cvs"/"avatars" become key prefixes
    s3_public_base_url: Optional[str] = None  # public base URL for stored objects

    # ── Modèles LLM (tous via OpenRouter) — dimensionnés par usage ──
    # Configurables par .env pour qu'un retrait de modèle upstream soit une
    # simple variable d'env, pas un redéploiement de code.
    #   * extraction : gros volume, structuré → Haiku (rapide + économique)
    #   * summary    : une phrase, trivial    → Haiku
    #   * draft      : génération de fiche AO  → Sonnet (qualité rédactionnelle)
    #   * assistant  : conversationnel         → Sonnet
    extraction_model: str = "anthropic/claude-haiku-4.5"   # ai_matching (features CV)
    summary_model: str = "anthropic/claude-haiku-4.5"      # résumé d'AO 1 phrase
    scoring_model: str = "anthropic/claude-haiku-4.5"      # 2e avis IA sur le score (hybride)
    draft_model: str = "anthropic/claude-sonnet-4.5"       # génération de fiche AO

    # Mistral — fallback LLM when OpenRouter is unavailable
    mistral_key: Optional[str] = None
    mistral_model: str = "mistral-small-latest"  # free tier fallback

    # In-app AI assistant — optional dedicated OpenRouter key + model
    # (falls back to openrouter_key when unset)
    assistant_openrouter_key: Optional[str] = None
    assistant_model: str = "anthropic/claude-sonnet-4.5"

    # OpenRouter — clé de PROVISIONING (facultative), pour la supervision IA :
    # elle donne accès à l'API compte (/credits, /activity) → miroir fidèle du
    # dashboard OpenRouter (dépenses, requêtes, tokens, coût par modèle). À défaut,
    # la supervision se rabat sur /credits via la clé runtime + le registre interne.
    openrouter_provisioning_key: Optional[str] = None
    # Supervision IA — noms (ou fragments de nom) des clés OpenRouter à considérer
    # comme « clés de la plateforme » dans le miroir compte. Les autres clés du
    # compte (autres apps : CV MANAGER, Achatinfo…) sont masquées de la supervision
    # UTI. Liste séparée par virgules ; vide = préfixe « plateforme » par défaut.
    openrouter_supervised_keys: Optional[str] = None

    # MIP RUM — distributed tracing (optional; unset = middleware inactive).
    # Read here because pydantic-settings loads .env without exporting to
    # os.environ, which the middleware would otherwise rely on.
    mip_rum_endpoint: Optional[str] = None
    mip_rum_app_id: Optional[str] = None
    mip_rum_api_key: Optional[str] = None
    # ── MIP RUM — API de LECTURE (supervision côté UTI) ──────────────
    # Base de l'API propriétaire MIP RUM + token d'accès UTI. Le backend UTI
    # proxifie cette API (le token reste serveur, jamais exposé au navigateur).
    # Tant que non renseigné, l'onglet RUM affiche « en attente de l'API MIP ».
    mip_rum_read_url: Optional[str] = None    # ex. https://mip-rum-console.vercel.app/api
    mip_rum_read_token: Optional[str] = None  # token d'accès délivré à UTI par MIP
    # ── MIP RUM — API console v1 (séries fines : LCP dans le temps, heatmap…) ──
    # Base = .../api/v1. Jeton CONSOLE, DISTINCT du token /rum/summary.
    # ⚠️ Ici on met le secret NU (ex. "mip_xxx"), SANS "@app". Le périmètre
    # (@gip-plateforme) vit côté MIP dans la variable d'env CONSOLE_API_TOKENS
    # (entrée "<secret>@gip-plateforme") — pas dans le token envoyé par le client.
    mip_rum_console_url: Optional[str] = None    # ex. https://mip-rum-console.vercel.app/api/v1
    mip_rum_console_token: Optional[str] = None  # secret nu, ex. "mip_xxx"

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }

settings = Settings()

# ── Security guards ────────────────────────────────────────────────
INSECURE_JWT_DEFAULT = "change-me-in-production"


def is_prod() -> bool:
    return settings.app_env.lower() in ("prod", "production")


# Fail-closed: a known signing secret means anyone can forge admin tokens.
# Refuse to boot in production with the default; warn loudly in dev.
if settings.jwt_secret == INSECURE_JWT_DEFAULT:
    if is_prod():
        raise RuntimeError(
            "JWT_SECRET non configuré (valeur par défaut détectée). "
            "Générez un secret fort et placez-le dans backend/.env :\n"
            "    JWT_SECRET=$(openssl rand -hex 32)\n"
            "Sans cela, n'importe qui peut forger des jetons d'authentification "
            "(y compris admin). Pour le développement local : APP_ENV=dev."
        )
    print("[CONFIG] ⚠️  JWT_SECRET par défaut — OK en dev, à NE JAMAIS utiliser en prod.")

