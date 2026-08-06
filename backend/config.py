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

    # File storage backend: "supabase", "s3" (OVH Object Storage) ou "local"
    # (disque du VPS). Voir services/storage.py.
    storage_backend: str = "supabase"
    s3_endpoint_url: Optional[str] = None  # e.g. https://s3.gra.io.cloud.ovh.net
    s3_region: str = "gra"
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket: Optional[str] = None  # single OVH bucket; "cvs"/"avatars" become key prefixes
    s3_public_base_url: Optional[str] = None  # public base URL for stored objects

    # ── Stockage LOCAL (STORAGE_BACKEND=local) ──────────────────────
    # Racine des fichiers sur le disque du VPS. HORS de ~/app : ce répertoire
    # est le dépôt git redéployé par deploy.sh, et des données de production
    # n'ont rien à faire dans un arbre que l'on remplace à chaque mise à jour.
    # Hors de /home aussi : /var/lib est l'emplacement prévu par la FHS pour
    # l'état applicatif persistant, et c'est ce que les sauvegardes visent.
    local_storage_dir: str = "/var/lib/uti/files"
    # Origine publique du BACKEND (façade nginx en HTTPS), pas celle du frontend.
    # Les URLs de fichiers sont ouvertes depuis Vercel et depuis des clients de
    # messagerie : une URL relative n'y veut rien dire. Obligatoire en mode local
    # (garde ci-dessous), inutile ailleurs.
    public_base_url: Optional[str] = None
    # Secret DÉDIÉ à la signature des URLs de fichiers. Laisser vide : la clé est
    # alors dérivée de jwt_secret par HMAC (services/storage.py:_cle_de_signature),
    # ce qui donne la séparation de domaine sans un secret de plus à faire tourner.
    # Le renseigner permet d'invalider d'un coup toutes les URLs de fichiers
    # émises sans déconnecter personne.
    file_url_secret: Optional[str] = None

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
    # Analyse VISION du CV : lit les pages RENDUES EN IMAGE → voit ce que le texte
    # seul manque (jauges/étoiles de compétences, graphiques, CV scannés, mises en
    # page multi-colonnes). Modèle multimodal (Claude via OpenRouter). Repli propre
    # sur l'extraction texte si absent/indisponible.
    vision_model: str = "anthropic/claude-sonnet-4.5"      # cv_vision (analyse visuelle du CV)
    # Interrupteur de l'analyse VISION. Activée par défaut (le produit la demande),
    # mais débrayable par déploiement : la vision envoie les PAGES DU CV EN IMAGE
    # (donc photo + identité visibles) au fournisseur LLM — un flux de données
    # personnelles nouveau vs le texte. À couper (VISION_ENABLED=false) si la DPA /
    # le consentement ne le couvrent pas encore. À off → repli extraction texte.
    vision_enabled: bool = True

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
    # ── xSOM AI Guard — ingestion des spans gen_ai (observabilité IA) ────
    # Dual-emit : les mêmes spans gen_ai partent aussi vers xSOM (en plus de
    # MIP RUM), qui devient la source des métriques IA (sens 2). Auth par un
    # gateway token xSOM (xsg_…) dans l'en-tête X-Gateway-Token. Inactif tant
    # que l'URL ou le token ne sont pas renseignés (aucun envoi xSOM).
    xsom_ai_url: Optional[str] = None          # base incluant /v1, ex. https://xsom…up.railway.app/v1
    xsom_gateway_token: Optional[str] = None   # xsg_… (jamais exposé au navigateur)
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

# ── Stockage : refuser une configuration à moitié posée ────────────
BACKENDS_STOCKAGE = ("supabase", "s3", "local")

# Fail-closed n°1 : jusqu'ici, toute valeur autre que "s3" retombait
# silencieusement sur Supabase. Avec un TROISIÈME backend, une faute de frappe
# (« locale », « Local ») écrirait les fichiers dans le projet Supabase — celui
# qu'on s'apprête justement à supprimer. On préfère ne pas démarrer.
if settings.storage_backend not in BACKENDS_STOCKAGE:
    raise RuntimeError(
        f"STORAGE_BACKEND={settings.storage_backend!r} inconnu. "
        f"Valeurs acceptées : {', '.join(BACKENDS_STOCKAGE)}.\n"
        "Une valeur non reconnue retombait autrefois sur Supabase sans le dire : "
        "les fichiers seraient écrits dans le projet destiné à la suppression."
    )

# Fail-closed n°2 : sans origine publique, services/storage.py fabriquerait des
# URLs commençant par « /files/… ». Le frontend est sur Vercel et les liens de CV
# partent par e-mail : ces URLs seraient résolues sur le mauvais domaine, et la
# panne se verrait au premier clic d'un CLIENT, pas au démarrage.
if settings.storage_backend == "local" and not settings.public_base_url:
    raise RuntimeError(
        "STORAGE_BACKEND=local exige PUBLIC_BASE_URL (origine HTTPS publique du "
        "backend, ex. https://vps-cc93f2a8.vps.ovh.net).\n"
        "Sans elle, les liens de CV et d'avatars seraient relatifs — donc résolus "
        "sur le domaine Vercel du frontend, où rien ne répond."
    )


# Fail-closed n°3 : la clé qui signe les URLs de FICHIERS ne doit jamais être
# celle qui signe les SESSIONS.
#
# Les deux jetons n'ont pas la même exposition. Un jeton de session voyage dans
# un en-tête Authorization ; un jeton de fichier voyage DANS L'URL — donc dans
# les journaux, l'historique du navigateur, et, pour le lien de CV transmis au
# client final (services/cv_notifications.py, 7 jours de validité), dans une
# boîte mail que nous ne maîtrisons pas.
#
# Laisser FILE_URL_SECRET vide est le cas NORMAL : la clé est alors dérivée de
# jwt_secret par HMAC, ce qui donne la séparation sans un secret de plus à faire
# tourner. Ce garde-fou ne vise que le geste « pour simplifier, je mets la même
# valeur », qui annulerait la séparation sans que rien ne le signale.
if settings.file_url_secret and settings.file_url_secret == settings.jwt_secret:
    raise RuntimeError(
        "FILE_URL_SECRET est identique à JWT_SECRET. Ces deux jetons n'ont pas "
        "la même exposition : celui des fichiers circule dans des URLs, donc "
        "dans des journaux et des boîtes mail.\n"
        "Laissez FILE_URL_SECRET vide (la clé sera dérivée de JWT_SECRET par "
        "HMAC), ou donnez-lui une valeur distincte : openssl rand -hex 32"
    )
