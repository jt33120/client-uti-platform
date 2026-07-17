from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from services.supabase_client import supabase
from services import storage
from services.email import send_email, render_email_html
from services import email_templates
from config import settings
import io
import base64
import time
import traceback
import httpx
import pyotp
import qrcode
import qrcode.image.svg
from collections import defaultdict, deque

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()

# ── Anti brute-force (mono-worker, en mémoire) ──────────────────────────────
# Fenêtre glissante par clé (IP ou user). Protège login / MFA / reset : sans
# elle, un code TOTP (10^6 possibilités, fenêtre de 10 min) se brute-force.
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
_ATTEMPTS_MAX_KEYS = 10_000


def _throttle(key: str, max_calls: int, per_seconds: int) -> None:
    """Lève 429 si `key` a dépassé `max_calls` sur la fenêtre `per_seconds`."""
    now = time.time()
    if len(_ATTEMPTS) > _ATTEMPTS_MAX_KEYS:  # garde-fou mémoire
        for k in [k for k, d in list(_ATTEMPTS.items()) if not d]:
            _ATTEMPTS.pop(k, None)
    hits = _ATTEMPTS[key]
    cutoff = now - per_seconds
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= max_calls:
        retry = int(hits[0] + per_seconds - now) + 1
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives — patientez avant de réessayer.",
            headers={"Retry-After": str(max(retry, 1))},
        )
    hits.append(now)

ALGORITHM = "HS256"
# Déconnexion automatique après 3 h d'utilisation (durée de vie du jeton de
# session). Le front applique en plus une coupure côté client (minuteur).
ACCESS_TOKEN_EXPIRE_HOURS = 3

# MFA (TOTP) — jeton de défi court entre la saisie du mot de passe et la
# validation du second facteur.
MFA_CHALLENGE_EXPIRE_MIN = 10
MFA_ISSUER = "Groupement-IT"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: str  # "admin", "commerce" (UTI sales) or "ao" (partner)
    invite_token: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def create_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")


# Cache court des profils (id -> (ts, row|None)) : permet de re-vérifier le
# statut/rôle en base à chaque requête sans doubler la charge DB. TTL 60 s =
# une suspension/suppression de compte prend effet en ≤ 1 min au lieu de
# rester valide jusqu'à l'expiration du jeton (3 h).
_PROFILE_STATE_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_PROFILE_STATE_TTL = 60.0
_PROFILE_STATE_MAX = 5_000


def _live_profile_state(user_id: str) -> Optional[dict]:
    """État live du compte ({role, status} | None si supprimé).
    Best-effort : si la base est injoignable, renvoie {"_unverified": True}
    (on ne déconnecte pas tout le monde pour un hoquet DB)."""
    now = time.time()
    hit = _PROFILE_STATE_CACHE.get(user_id)
    if hit and now - hit[0] < _PROFILE_STATE_TTL:
        return hit[1]
    try:
        rows = supabase.table("profiles").select("id, role, status").eq("id", user_id).execute().data
    except Exception:
        try:  # colonne status pas migrée — on vérifie au moins l'existence + le rôle
            rows = supabase.table("profiles").select("id, role").eq("id", user_id).execute().data
        except Exception:
            return hit[1] if hit else {"_unverified": True}
    state = rows[0] if rows else None
    if len(_PROFILE_STATE_CACHE) > _PROFILE_STATE_MAX:
        _PROFILE_STATE_CACHE.clear()
    _PROFILE_STATE_CACHE[user_id] = (now, state)
    return state


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    payload = decode_token(credentials.credentials)
    # Un jeton de défi MFA (stage présent) n'est PAS une session ouverte :
    # il ne doit jamais authentifier un appel API.
    if payload.get("stage"):
        raise HTTPException(status_code=401, detail="Validation en deux étapes requise")
    # Le JWT (3 h) n'est pas révocable : on re-vérifie l'état du compte en base
    # (cache 60 s) pour qu'une suspension/suppression/rétrogradation prenne
    # effet immédiatement, pas au prochain login.
    state = _live_profile_state(payload.get("sub"))
    if state is None:
        raise HTTPException(status_code=401, detail="Compte introuvable ou supprimé.")
    if not state.get("_unverified"):
        if (state.get("status") or "active") in ("suspended", "disabled"):
            raise HTTPException(status_code=403, detail="Compte suspendu ou désactivé. Contactez un administrateur.")
        if state.get("role") in VALID_ROLES:
            payload["role"] = state["role"]  # le rôle live prime sur celui figé dans le jeton
    return payload


# ── MFA (TOTP) ─────────────────────────────────────────────────────────────
def create_mfa_challenge(user_id: str, email: str, role: str, stage: str, secret: str = None) -> str:
    """Jeton court signé reliant l'étape mot de passe à l'étape second facteur.

    `stage` vaut 'verify' (compte déjà enrôlé) ou 'enroll' (premier enrôlement,
    le secret est embarqué le temps de la confirmation, jamais stocké avant)."""
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "stage": stage,
        "exp": datetime.utcnow() + timedelta(minutes=MFA_CHALLENGE_EXPIRE_MIN),
    }
    if secret:
        payload["mfa_secret"] = secret
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def _decode_mfa_challenge(token: str, expected_stage: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Session de connexion expirée. Reconnectez-vous.")
    if payload.get("stage") != expected_stage:
        raise HTTPException(status_code=400, detail="Jeton de validation invalide.")
    return payload


def _qr_data_uri(data: str) -> str:
    """Génère le QR code (SVG, sans dépendance image native) en data URI."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{b64}"


def _clean_code(code: str) -> str:
    return "".join(c for c in (code or "") if c.isdigit())


def _client_ip(request: Optional[Request]) -> Optional[str]:
    """IP publique de l'appelant. On privilégie X-Real-IP, posé par NOTRE nginx
    (non falsifiable de l'extérieur), puis X-Forwarded-For en repli — sa
    première IP est déclarative et peut être forgée par le client."""
    if request is None:
        return None
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _finalize_login(user_id: str, email: str, profile: dict, ip: Optional[str] = None) -> dict:
    """Ouvre la session : met à jour la dernière connexion (date + IP) et émet le jeton."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("profiles").update(
            {"last_login_at": now, "last_login_ip": ip} if ip else {"last_login_at": now}
        ).eq("id", user_id).execute()
    except Exception:
        # Colonne last_login_ip pas encore migrée : on enregistre au moins la date.
        try:
            supabase.table("profiles").update({"last_login_at": now}).eq("id", user_id).execute()
        except Exception:
            pass
    token = create_token(user_id, email, profile["role"])
    return {
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": profile["name"],
            "role": profile["role"],
            "org": profile.get("org"),
            "avatar_url": profile.get("avatar_url"),
        },
    }


VALID_ROLES = ("admin", "commerce", "ao")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    return user


async def require_staff(user: dict = Depends(get_current_user)) -> dict:
    """UTI internal staff: admin or commerce. Commerce drives AOs + matching
    but stays read-only on clients/partners governance (those keep require_admin)."""
    if user.get("role") not in ("admin", "commerce"):
        raise HTTPException(status_code=403, detail="Accès réservé à l'équipe UTI")
    return user


def is_staff(user: dict) -> bool:
    return user.get("role") in ("admin", "commerce")


def _parse_supabase_error(error_msg: str) -> tuple[int, str]:
    """
    Map raw Supabase / GoTrue error strings to human-readable French messages.
    Returns (http_status_code, user_facing_message).
    """
    msg = error_msg.lower()

    # ── Auth errors ───────────────────────────────────────────────
    if "user already registered" in msg or "already been registered" in msg or "already exists" in msg:
        return 409, "Un compte existe déjà avec cet email."

    if "password should be at least" in msg or "password is too short" in msg:
        return 422, "Le mot de passe est trop court (minimum 8 caractères)."

    if "unable to validate email address" in msg or "invalid email" in msg:
        return 422, "L'adresse email semble invalide."

    if "email rate limit exceeded" in msg or "too many requests" in msg or "rate limit" in msg:
        return 429, "Trop de tentatives. Veuillez patienter quelques minutes avant de réessayer."

    if "user not allowed" in msg or "signups not allowed" in msg or "signup is disabled" in msg:
        return 403, (
            "Les inscriptions sont désactivées sur ce projet Supabase. "
            "Activez-les dans : Supabase Dashboard → Authentication → Providers → Email → "
            "cochez « Enable Email provider » et désactivez « Confirm email » pour le POC."
        )

    if "email not confirmed" in msg:
        return 403, "Email non confirmé. Vérifiez votre boîte mail ou désactivez la confirmation email dans Supabase."

    if "invalid api key" in msg or "apikey" in msg:
        return 500, "Clé API Supabase invalide : vérifiez SUPABASE_SERVICE_KEY dans votre .env."

    if "relation" in msg and "does not exist" in msg:
        return 500, "La table 'profiles' n'existe pas en base. Avez-vous exécuté supabase_schema.sql ?"

    if "violates foreign key" in msg:
        return 500, "Erreur de contrainte base de données : l'utilisateur Auth n'a pas été créé avant le profil."

    if "violates unique constraint" in msg:
        return 409, "Un compte existe déjà avec cet email."

    if "permission denied" in msg or "not authorized" in msg:
        return 403, "Permission refusée par Supabase. Vérifiez que vous utilisez la clé service_role (pas la clé anon)."

    if "connection" in msg or "timeout" in msg or "could not connect" in msg:
        return 503, "Impossible de joindre Supabase. Vérifiez votre SUPABASE_URL et votre connexion réseau."

    # ── Fallback — generic to the client; the raw message stays in logs ──
    print(f"[AUTH] unmapped registration error: {error_msg}")
    return 400, "Inscription impossible pour le moment. Réessayez ou contactez le support."


@router.post("/register")
async def register(body: RegisterRequest):
    # ── Validate and consume invite token (if provided) ───────────
    invitation = None
    if body.invite_token:
        try:
            inv_result = supabase.table("invitations").select("*") \
                .eq("token", body.invite_token).single().execute()
            invitation = inv_result.data
        except Exception:
            invitation = None

        if not invitation or invitation.get("used_at"):
            raise HTTPException(status_code=400, detail="Lien d'invitation invalide ou déjà utilisé.")

        inv_expires = datetime.fromisoformat(invitation["expires_at"].replace("Z", "+00:00"))
        if inv_expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Ce lien d'invitation a expiré.")

        if invitation["email"].lower() != body.email.lower():
            raise HTTPException(status_code=400, detail="L'email ne correspond pas à l'invitation.")

        # Force role from invitation — prevents privilege escalation
        body.role = invitation["role"]
        # Force name from invitation — admin sets the partner display name
        if invitation.get("name"):
            body.name = invitation["name"]

    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Rôle invalide. Utilisez 'admin', 'commerce' ou 'ao'.")

    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 8 caractères.")

    if len(body.name.strip()) < 2:
        raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")

    # ── Step 1: Create Supabase Auth user ─────────────────────────
    # Using direct HTTP instead of supabase.auth.admin to ensure the
    # service_role key is sent in BOTH the apikey and Authorization headers,
    # which some versions of gotrue-py fail to do correctly.
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{settings.supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "email": body.email,
                    "password": body.password,
                    "email_confirm": True,
                },
            )
        if resp.status_code >= 400:
            raw_error = resp.text
            print(f"[AUTH] create_user HTTP {resp.status_code}: {raw_error}")
            status, detail = _parse_supabase_error(raw_error)
            raise HTTPException(status_code=status, detail=detail)
        user_data = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        raw_error = str(e)
        print(f"[AUTH] create_user failed — raw error: {raw_error}\n{tb}")
        status, detail = _parse_supabase_error(raw_error)
        raise HTTPException(status_code=status, detail=detail)

    # ── Validate response ─────────────────────────────────────────
    user_id = user_data.get("id")
    if not user_id:
        print(f"[AUTH] Unexpected Supabase response (no id): {user_data}")
        raise HTTPException(
            status_code=500,
            detail="Supabase n'a pas retourné d'utilisateur. Vérifiez vos logs serveur."
        )

    # ── Step 2: Insert profile row ────────────────────────────────
    # Carry the commercial entity from the invitation (UTI vs Groupement-IT).
    org = invitation.get("org") if invitation else None
    profile_row = {
        "id": user_id,
        "email": body.email,
        "name": body.name.strip(),
        "role": body.role,
        "org": org,
    }
    try:
        try:
            profile_resp = supabase.table("profiles").insert(profile_row).execute()
        except Exception:
            # 'org' column not migrated yet — retry without it.
            profile_row.pop("org", None)
            profile_resp = supabase.table("profiles").insert(profile_row).execute()
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[AUTH] profiles insert failed for user {user_id}:\n{tb}")
        # Auth user was created — attempt cleanup to avoid orphan
        try:
            with httpx.Client(timeout=10) as client:
                client.delete(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                    },
                )
            print(f"[AUTH] Cleaned up orphan auth user {user_id}")
        except Exception as cleanup_err:
            print(f"[AUTH] Cleanup failed: {cleanup_err}")
        status, detail = _parse_supabase_error(str(e))
        raise HTTPException(status_code=status, detail=detail)

    if hasattr(profile_resp, 'error') and profile_resp.error:
        status, detail = _parse_supabase_error(profile_resp.error.message)
        raise HTTPException(status_code=status, detail=detail)

    # ── Consume invitation token ──────────────────────────────────
    if invitation:
        try:
            supabase.table("invitations").update({
                "used_at": datetime.now(timezone.utc).isoformat(),
                "used_by": user_id,
            }).eq("token", body.invite_token).execute()
        except Exception as e:
            print(f"[AUTH] Warning: could not mark invitation as used: {e}")

    # ⚠️ SÉCURITÉ : on N'ouvre PAS de session ici (pas de jeton renvoyé).
    # Renvoyer un token connecterait l'utilisateur en contournant la MFA
    # obligatoire, qui n'est appliquée qu'au /login. L'utilisateur est donc
    # renvoyé vers /login, où l'enrôlement/vérification MFA s'applique.
    return {
        "registered": True,
        "email": body.email,
        "role": body.role,
    }


def _verify_credentials(email: str, password: str) -> Optional[dict]:
    """
    Vérifie un couple email/mot de passe auprès de GoTrue SANS passer par
    ``supabase.auth.sign_in_with_password()``.

    Pourquoi : supabase-py lie la session de l'utilisateur connecté au client
    ``supabase`` partagé. Du coup, toutes les requêtes ``.table()`` suivantes
    s'exécutent en tant que cet utilisateur (role ``authenticated``) au lieu de
    ``service_role`` — et se font refuser par la RLS deny-all (« profil
    introuvable »). En tapant directement l'endpoint REST GoTrue, le singleton
    reste un pur ``service_role`` qui contourne la RLS.

    Retourne le dict user GoTrue si OK, None si identifiants invalides.
    Lève HTTPException pour les cas email non confirmé / rate limit.
    """
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{settings.supabase_url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={
                "apikey": settings.supabase_service_key,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
        )
    if resp.status_code == 200:
        return resp.json().get("user")
    msg = resp.text.lower()
    if "email not confirmed" in msg:
        raise HTTPException(status_code=403, detail="Email non confirmé. Vérifiez votre boîte mail.")
    if resp.status_code == 429 or "rate limit" in msg or "too many requests" in msg:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans quelques minutes.")
    return None


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    # Anti brute-force : par IP (large) + par compte visé (plus strict).
    _throttle(f"login:ip:{_client_ip(request)}", 15, 300)
    _throttle(f"login:email:{body.email.lower()}", 8, 300)
    # ── Step 1: vérifier les identifiants via GoTrue ──────────────
    # NB: on N'utilise PAS supabase.auth.sign_in_with_password (cela lierait la
    # session au client service_role partagé → lectures suivantes en role
    # 'authenticated' → bloquées par la RLS deny-all). Voir _verify_credentials.
    try:
        auth_user = _verify_credentials(body.email, body.password)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH] login failed: {e}")
        raise HTTPException(status_code=503, detail="Service d'authentification indisponible. Réessayez dans un instant.")

    if not auth_user or not auth_user.get("id"):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")

    user_id = auth_user["id"]

    # ── Step 2: Fetch profile ─────────────────────────────────────
    try:
        profile_response = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
        profile = profile_response.data
    except Exception as e:
        print(f"[AUTH] profiles fetch failed for {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Compte Auth trouvé mais profil introuvable en base. La table 'profiles' existe-t-elle ?"
        )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profil utilisateur introuvable. Votre compte est peut-être incomplet, réinscrivez-vous."
        )

    # Block suspended / disabled accounts (admin-managed status).
    status = profile.get("status") or "active"
    if status == "suspended":
        raise HTTPException(status_code=403, detail="Votre compte est suspendu. Contactez un administrateur.")
    if status == "disabled":
        raise HTTPException(status_code=403, detail="Votre compte a été désactivé. Contactez un administrateur.")

    # ── MFA obligatoire (TOTP) ────────────────────────────────────
    # Si les colonnes MFA existent, un second facteur est exigé :
    #   - compte déjà enrôlé  -> on demande un code de vérification ;
    #   - pas encore enrôlé   -> enrôlement forcé (QR code) avant la session.
    # mfa_required (défaut true) : un admin peut exonérer un compte précis.
    # Colonne absente → True (MFA obligatoire, comportement par défaut conservé).
    if "mfa_enabled" in profile and profile.get("mfa_required", True):
        if profile.get("mfa_enabled") and profile.get("mfa_secret"):
            return {
                "mfa": "verify",
                "challenge_token": create_mfa_challenge(user_id, body.email, profile["role"], "verify"),
            }
        secret = pyotp.random_base32()
        otpauth = pyotp.TOTP(secret).provisioning_uri(name=body.email, issuer_name=MFA_ISSUER)
        return {
            "mfa": "enroll",
            "challenge_token": create_mfa_challenge(user_id, body.email, profile["role"], "enroll", secret=secret),
            "qr": _qr_data_uri(otpauth),
            "secret": secret,
        }

    # Colonnes MFA absentes (migration non encore appliquée) : connexion classique.
    # Signalé à l'admin : en prod, la MFA est censée être active — si ce chemin
    # s'exécute, c'est que la migration MFA manque (2e facteur silencieusement absent).
    from services.error_log import record as _record_err
    _record_err("auth", "Connexion SANS MFA : colonnes MFA absentes de profiles (migration non appliquée)", level="warning")
    return _finalize_login(user_id, body.email, profile, _client_ip(request))


class MfaCodeRequest(BaseModel):
    challenge_token: str
    code: str


@router.post("/mfa/verify")
async def mfa_verify(body: MfaCodeRequest, request: Request):
    """Étape 2 (compte enrôlé) : valide le code TOTP et ouvre la session."""
    payload = _decode_mfa_challenge(body.challenge_token, "verify")
    user_id, email = payload["sub"], payload["email"]
    # Anti brute-force du code TOTP : 5 essais / 5 min par compte. Sans cette
    # borne, un attaquant qui a le mot de passe peut rejouer le challenge
    # (10 min de validité) et énumérer des codes à haute cadence.
    _throttle(f"mfa:verify:{user_id}", 5, 300)
    try:
        profile = supabase.table("profiles").select("*").eq("id", user_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    secret = (profile or {}).get("mfa_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="MFA non configurée pour ce compte.")
    if not pyotp.TOTP(secret).verify(_clean_code(body.code), valid_window=1):
        raise HTTPException(status_code=401, detail="Code de vérification invalide.")
    return _finalize_login(user_id, email, profile, _client_ip(request))


@router.post("/mfa/enroll")
async def mfa_enroll(body: MfaCodeRequest, request: Request):
    """Premier enrôlement : confirme le QR scanné puis active la MFA."""
    payload = _decode_mfa_challenge(body.challenge_token, "enroll")
    user_id, email = payload["sub"], payload["email"]
    _throttle(f"mfa:enroll:{user_id}", 5, 300)
    secret = payload.get("mfa_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="Session d'enrôlement invalide. Reconnectez-vous.")
    if not pyotp.TOTP(secret).verify(_clean_code(body.code), valid_window=1):
        raise HTTPException(status_code=401, detail="Code invalide. Vérifiez l'heure de votre téléphone et réessayez.")
    try:
        supabase.table("profiles").update({"mfa_secret": secret, "mfa_enabled": True}).eq("id", user_id).execute()
    except Exception as e:
        print(f"[AUTH] activation MFA échouée pour {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Impossible d'activer la MFA (colonnes MFA migrées ?). Réessayez ou contactez un administrateur.")
    try:
        profile = supabase.table("profiles").select("*").eq("id", user_id).single().execute().data
    except Exception:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    return _finalize_login(user_id, email, profile, _client_ip(request))


@router.post("/mfa/reset/{user_id}")
async def mfa_reset(user_id: str, admin: dict = Depends(require_admin)):
    """Réinitialise la MFA d'un utilisateur (perte de téléphone). Il devra la
    reconfigurer à sa prochaine connexion."""
    try:
        supabase.table("profiles").update({"mfa_enabled": False, "mfa_secret": None}).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Échec de la réinitialisation MFA : {e}")
    return {"message": "MFA réinitialisée. L'utilisateur devra la reconfigurer à sa prochaine connexion."}


class MfaRequiredRequest(BaseModel):
    required: bool


@router.post("/mfa/require/{user_id}")
async def mfa_set_required(user_id: str, body: MfaRequiredRequest, admin: dict = Depends(require_admin)):
    """Active/désactive l'obligation de MFA pour un compte (active par défaut).

    Désactiver n'efface pas un éventuel secret déjà enrôlé : si on réactive plus
    tard, l'utilisateur reprend sa MFA existante. Si on désactive, sa prochaine
    connexion se fait sans second facteur.
    """
    try:
        supabase.table("profiles").update({"mfa_required": body.required}).eq("id", user_id).execute()
    except Exception:
        raise HTTPException(
            status_code=501,
            detail="Colonne mfa_required absente : appliquez la migration supabase_migration_mfa_toggle.sql.",
        )
    return {"ok": True, "user_id": user_id, "mfa_required": body.required}


# ── MFA en self-service (depuis une session déjà authentifiée) ──────────────
# Distinct du flux de connexion : ici l'utilisateur est déjà connecté et gère
# lui-même son second facteur depuis « Paramètres du profil ». Comme à
# l'enrôlement au login, le secret n'est stocké qu'à la confirmation.

@router.post("/me/mfa/start")
async def mfa_self_start(user: dict = Depends(get_current_user)):
    """Démarre l'activation 2FA : génère un secret + QR. Le secret est embarqué
    dans un jeton signé court (jamais stocké tant que non confirmé)."""
    user_id, email = user["sub"], user["email"]
    _throttle(f"mfa:self-start:{user_id}", 10, 300)
    secret = pyotp.random_base32()
    otpauth = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=MFA_ISSUER)
    return {
        "challenge_token": create_mfa_challenge(user_id, email, user.get("role", ""), "self_enroll", secret=secret),
        "qr": _qr_data_uri(otpauth),
        "secret": secret,
    }


class MfaSelfConfirmRequest(BaseModel):
    challenge_token: str
    code: str


@router.post("/me/mfa/confirm")
async def mfa_self_confirm(body: MfaSelfConfirmRequest, user: dict = Depends(get_current_user)):
    """Confirme l'activation 2FA : valide le code TOTP scanné puis active la MFA."""
    user_id = user["sub"]
    _throttle(f"mfa:self-confirm:{user_id}", 5, 300)
    payload = _decode_mfa_challenge(body.challenge_token, "self_enroll")
    if payload.get("sub") != user_id:
        raise HTTPException(status_code=403, detail="Jeton d'activation invalide.")
    secret = payload.get("mfa_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="Session d'activation invalide. Relancez l'activation.")
    if not pyotp.TOTP(secret).verify(_clean_code(body.code), valid_window=1):
        raise HTTPException(status_code=401, detail="Code invalide. Vérifiez l'heure de votre téléphone et réessayez.")
    try:
        supabase.table("profiles").update({"mfa_secret": secret, "mfa_enabled": True}).eq("id", user_id).execute()
    except Exception as e:
        print(f"[AUTH] activation MFA self-service échouée pour {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Impossible d'activer la double authentification (colonnes MFA migrées ?).")
    return {"mfa_enabled": True}


class MfaSelfDisableRequest(BaseModel):
    current_password: str


@router.post("/me/mfa/disable")
async def mfa_self_disable(body: MfaSelfDisableRequest, user: dict = Depends(get_current_user)):
    """Désactive la 2FA. Re-authentification par mot de passe requise : une
    session volée ne doit pas pouvoir retirer le second facteur en silence."""
    user_id, email = user["sub"], user["email"]
    _throttle(f"mfa:self-disable:{user_id}", 5, 300)
    if not body.current_password:
        raise HTTPException(status_code=422, detail="Mot de passe actuel requis pour désactiver la double authentification.")
    try:
        verified = _verify_credentials(email, body.current_password)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Erreur de vérification du mot de passe.")
    if not verified:
        raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
    # 2FA obligatoire (défaut) : la désactivation serait illusoire — ré-enrôlement
    # forcé à la prochaine connexion. On refuse proprement et on renvoie vers l'admin.
    try:
        prof = supabase.table("profiles").select("mfa_required").eq("id", user_id).single().execute().data or {}
    except Exception:
        prof = {}
    if prof.get("mfa_required", True):
        raise HTTPException(status_code=403, detail="La double authentification est obligatoire sur votre compte. Un administrateur doit l'exonérer avant que vous puissiez la désactiver.")
    try:
        supabase.table("profiles").update({"mfa_enabled": False, "mfa_secret": None}).eq("id", user_id).execute()
    except Exception:
        raise HTTPException(status_code=500, detail="Impossible de désactiver la double authentification.")
    return {"mfa_enabled": False}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    try:
        profile = supabase.table("profiles").select("*").eq("id", user["sub"]).single().execute()
        data = dict(profile.data or {})
        # Ne jamais exposer le secret TOTP au client.
        data.pop("mfa_secret", None)
        return data
    except Exception:
        raise HTTPException(status_code=404, detail="Profil introuvable")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    access_token: str
    new_password: str


def _send_reset_email(to_email: str, reset_url: str) -> tuple[bool, Optional[str]]:
    """
    Send the password-reset email via our own SMTP (Infomaniak), branded as
    Groupement-IT — instead of letting Supabase send it from
    "Supabase Auth <noreply@mail.app.supabase.io>", which alarms users and
    trips spam filters. Returns (success, error); never raises.
    """
    # Sujet + corps + coquille via la source unique (= aperçu admin fidèle).
    context = {"link": reset_url}
    subject, html, text = email_templates.build_email("password_reset", context)
    return send_email(to_email, subject, html, text=text)


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """
    Generates a Supabase recovery link (admin generate_link, which does NOT send
    any email) and delivers it ourselves via Infomaniak SMTP — so the message
    is branded "Groupement-IT" instead of "Supabase Auth <noreply@mail.app.supabase.io>".

    Always returns 200 to avoid leaking whether the email exists in the system.
    """
    # Anti-abus : cet endpoint public déclenche des e-mails sortants.
    _throttle(f"fp:ip:{_client_ip(request)}", 5, 900)
    _throttle(f"fp:email:{body.email.lower()}", 3, 900)
    try:
        with httpx.Client(timeout=10) as client:
            link_resp = client.post(
                f"{settings.supabase_url}/auth/v1/admin/generate_link",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "type": "recovery",
                    "email": body.email,
                    "redirect_to": f"{settings.frontend_url}/reset-password",
                },
            )
        # 200 only when the user exists; otherwise stay silent (anti-enumeration).
        if link_resp.status_code == 200:
            action_link = link_resp.json().get("action_link")
            if action_link:
                sent, err = _send_reset_email(body.email, action_link)
                if not sent:
                    print(f"[AUTH] reset email not sent to {body.email}: {err}")
            else:
                print(f"[AUTH] generate_link returned no action_link for {body.email}")
        else:
            # Non-existent email, rate limit, etc. — log for ops, reveal nothing.
            print(f"[AUTH] forgot-password generate_link HTTP {link_resp.status_code} (non-fatal)")
    except Exception as e:
        print(f"[AUTH] forgot-password error (non-fatal): {e}")
    return {"message": "Si un compte existe pour cet email, un lien de réinitialisation a été envoyé."}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request):
    """
    Validates the Supabase recovery token and updates the password.
    The access_token comes from the URL hash after the user clicks the reset link.
    """
    _throttle(f"rp:ip:{_client_ip(request)}", 10, 900)
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 8 caractères.")
    try:
        # Verify the recovery token by calling Supabase /auth/v1/user with it
        with httpx.Client(timeout=10) as client:
            user_resp = client.get(
                f"{settings.supabase_url}/auth/v1/user",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {body.access_token}",
                },
            )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")
        user_id = user_resp.json().get("id")
        if not user_id:
            raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")
        # Update the password via admin API
        with httpx.Client(timeout=10) as client:
            upd = client.put(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json={"password": body.new_password},
            )
        if upd.status_code >= 400:
            raise HTTPException(status_code=400, detail="Impossible de mettre à jour le mot de passe.")
        return {"message": "Mot de passe mis à jour avec succès."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH] reset-password error: {e}")
        raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None
    # Champs profil (migration 0009) — tous facultatifs : on ne met à jour que
    # ce qui est présent dans la requête.
    title: Optional[str] = None            # fonction / poste
    phone: Optional[str] = None
    preferred_language: Optional[str] = None  # 'fr' | 'en'
    notif_deadline_alerts: Optional[bool] = None
    notif_missing_info: Optional[bool] = None


@router.patch("/me")
async def update_profile(body: UpdateProfileRequest, user: dict = Depends(get_current_user)):
    user_id = user["sub"]
    current_email = user["email"]

    # Email or password change requires current password verification
    if body.email or body.new_password:
        if not body.current_password:
            raise HTTPException(status_code=422, detail="Mot de passe actuel requis pour changer l'email ou le mot de passe.")
        try:
            verified = _verify_credentials(current_email, body.current_password)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Erreur de vérification du mot de passe.")
        if not verified:
            raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")

        if body.new_password and len(body.new_password) < 8:
            raise HTTPException(status_code=422, detail="Le nouveau mot de passe doit contenir au moins 8 caractères.")

        auth_update: dict = {}
        if body.email:
            auth_update["email"] = body.email
        if body.new_password:
            auth_update["password"] = body.new_password

        with httpx.Client(timeout=10) as client:
            resp = client.put(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json=auth_update,
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail="Impossible de mettre à jour les identifiants.")

    profile_update: dict = {}
    if body.name and body.name.strip():
        if len(body.name.strip()) < 2:
            raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
        profile_update["name"] = body.name.strip()
    if body.email:
        profile_update["email"] = body.email
    # Champs profil (migration 0009). `is not None` : distingue « champ absent »
    # de « champ vidé » (chaîne vide → NULL) et gère le bool False.
    if body.title is not None:
        t = body.title.strip()
        profile_update["title"] = t or None
    if body.phone is not None:
        p = body.phone.strip()
        profile_update["phone"] = p or None
    if body.preferred_language is not None:
        lang = body.preferred_language.strip().lower()
        if lang not in ("fr", "en"):
            raise HTTPException(status_code=422, detail="Langue non supportée (fr ou en).")
        profile_update["preferred_language"] = lang
    if body.notif_deadline_alerts is not None:
        profile_update["notif_deadline_alerts"] = bool(body.notif_deadline_alerts)
    if body.notif_missing_info is not None:
        profile_update["notif_missing_info"] = bool(body.notif_missing_info)

    if profile_update:
        try:
            supabase.table("profiles").update(profile_update).eq("id", user_id).execute()
        except Exception as e:
            # Colonnes 0009 non encore migrées : ne bloque pas la mise à jour des
            # champs historiques (name/email), retente sans les nouveaux champs.
            legacy = {k: v for k, v in profile_update.items() if k in ("name", "email")}
            if legacy and legacy != profile_update:
                supabase.table("profiles").update(legacy).eq("id", user_id).execute()
                raise HTTPException(status_code=501, detail="Certains champs de profil ne sont pas encore disponibles : appliquez la migration 0009_profile_fields.sql.")
            raise HTTPException(status_code=500, detail=f"Mise à jour du profil impossible : {e}")

    profile = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
    data = dict(profile.data or {})
    data.pop("mfa_secret", None)  # ne jamais exposer le secret TOTP
    return data


_AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
_AVATAR_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


@router.post("/me/avatar")
async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    user_id = user["sub"]

    if file.content_type not in _AVATAR_ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail="Format non supporté. Utilisez JPEG, PNG ou WebP.")

    file_bytes = await file.read()
    if len(file_bytes) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=422, detail="Image trop lourde (max 2 Mo).")

    ext = _AVATAR_EXT[file.content_type]
    storage_path = f"{user_id}/avatar.{ext}"

    # Remove any existing avatar files for this user
    try:
        existing = storage.list("avatars", user_id)
        if existing:
            storage.remove("avatars", [f"{user_id}/{f['name']}" for f in existing])
    except Exception:
        pass

    try:
        avatar_url = storage.upload(
            "avatars",
            storage_path,
            file_bytes,
            file.content_type,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur upload avatar: {str(e)}")

    supabase.table("profiles").update({"avatar_url": avatar_url}).eq("id", user_id).execute()
    return {"avatar_url": avatar_url}


@router.delete("/me/avatar")
async def delete_avatar(user: dict = Depends(get_current_user)):
    user_id = user["sub"]
    try:
        existing = storage.list("avatars", user_id)
        if existing:
            storage.remove("avatars", [f"{user_id}/{f['name']}" for f in existing])
    except Exception:
        pass
    supabase.table("profiles").update({"avatar_url": None}).eq("id", user_id).execute()
    return {"avatar_url": None}