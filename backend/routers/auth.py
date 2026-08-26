from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr
from typing import Optional
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from services.supabase_client import supabase
from services import storage
from services.email import send_email, render_email_html
from services import email_templates
from services.client_ip import public_client_ip
from services import credentials, passwords
from config import settings
import io
import base64
import time
import traceback
import uuid
import pyotp
import qrcode
import qrcode.image.svg
from collections import defaultdict, deque

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()

#: Validité, en jours, du lien envoyé à un compte HÉRITÉ (migration Supabase).
#: Sans rapport avec l'heure d'un « mot de passe oublié » ordinaire : celui-là
#: est demandé à l'instant, celui-ci s'adresse à quelqu'un qui découvre qu'il ne
#: peut plus entrer et qui traitera peut-être le sujet le lendemain. Aligné sur
#: scripts/migrer_identifiants.JOURS_PAR_DEFAUT.
MIGRATION_LIEN_JOURS = 7

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
    """IP DE CONFIANCE de l'appelant — anti-abus (throttling) uniquement.

    On privilégie X-Real-IP, posé par NOTRE nginx (= $remote_addr, non
    falsifiable de l'extérieur), puis X-Forwarded-For en repli. À ne PAS
    utiliser pour afficher « d'où s'est connecté l'utilisateur » : derrière la
    réécriture Vercel, cette valeur est l'IP de sortie de Vercel, identique pour
    tous les utilisateurs (cf. services.client_ip.public_client_ip)."""
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


def _parse_db_error(error_msg: str) -> tuple[int, str]:
    """
    Traduit une erreur PostgREST/PostgreSQL en (code HTTP, message utilisateur).

    Avant la migration, cette fonction décodait des messages GoTrue (« User
    already registered », « Signups not allowed », et une aide qui renvoyait vers
    le tableau de bord Supabase). Ces chaînes n'existent plus : la création de
    compte ne fait plus qu'insérer deux lignes, donc les seules erreurs possibles
    sont celles de la base — contrainte violée, table absente, base injoignable.
    """
    msg = (error_msg or "").lower()

    # 23505 = unique_violation. PostgREST renvoie le code ET le libellé
    # « duplicate key value violates unique constraint » ; on teste les deux
    # formes pour ne pas dépendre du format d'un message d'erreur.
    if "23505" in msg or "duplicate key" in msg or "violates unique constraint" in msg:
        return 409, "Un compte existe déjà avec cet email."

    # 23503 = foreign_key_violation : la ligne d'identifiants référence un profil
    # qui n'existe pas (ou plus). Anomalie interne, pas une faute de l'appelant.
    if "23503" in msg or "violates foreign key" in msg:
        return 500, "Erreur de cohérence en base : le profil n'a pas pu être rattaché."

    if "23514" in msg or "violates check constraint" in msg:
        return 422, "Valeur refusée par la base (email ou rôle invalide)."

    if "relation" in msg and "does not exist" in msg:
        return 500, (
            "Table absente en base. Appliquez les migrations "
            "(backend/migrations/) avant de créer des comptes."
        )

    if "permission denied" in msg or "42501" in msg:
        return 500, "Permission refusée par la base : vérifiez le rôle utilisé par PostgREST."

    if "connection" in msg or "timeout" in msg or "could not connect" in msg:
        return 503, "Base de données injoignable. Réessayez dans un instant."

    # ── Repli — générique côté client ; le message brut reste dans les journaux ──
    print(f"[AUTH] erreur base non cartographiée: {error_msg}")
    return 400, "Inscription impossible pour le moment. Réessayez ou contactez le support."


@router.post("/register")
async def register(body: RegisterRequest, request: Request):
    # ── L'invitation est OBLIGATOIRE ──────────────────────────────
    # Le front l'impose déjà (RegisterPage n'affiche aucun formulaire sans
    # jeton, et son commentaire dit « the role always comes from the invitation
    # server-side »), mais le serveur, lui, ne l'exigeait pas : sans
    # `invite_token`, le bloc ci-dessous était sauté et `body.role` — envoyé par
    # l'appelant — était retenu tel quel. Le seul contrôle restant étant
    # « le rôle fait-il partie des rôles connus ? », un simple
    #
    #     POST /auth/register {"email":…, "password":…, "name":…, "role":"admin"}
    #
    # créait un administrateur de la plateforme, sur une route publique et sans
    # limitation de débit. Une règle qui n'existe que dans le navigateur n'est
    # pas une règle : elle ne protège que les gens qui utilisent le navigateur.
    _throttle(f"register:ip:{_client_ip(request)}", 10, 600)
    if not body.invite_token:
        raise HTTPException(
            status_code=403,
            detail="La création de compte se fait uniquement sur invitation.",
        )

    # ── Validate and consume invite token ─────────────────────────
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

    try:
        passwords.check_password(body.password)
    except passwords.PasswordRejected as e:
        raise HTTPException(status_code=422, detail=str(e))

    if len(body.name.strip()) < 2:
        raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")

    # ── Étape 1 : hacher le mot de passe ──────────────────────────
    # Argon2id coûte ~44 ms de CPU : hors de la boucle d'événements, sinon un
    # uvicorn mono-worker (cf. uti-backend.service) se fige le temps du calcul.
    password_hash = await run_in_threadpool(passwords.hash_password, body.password)

    # ── Étape 2 : créer le profil ─────────────────────────────────
    # L'identifiant est tiré ICI, alors qu'il venait de la réponse de GoTrue.
    # C'est le pendant du retrait de la clé étrangère profiles.id → auth.users
    # (migration 0018) : plus personne d'autre que nous ne fabrique cet UUID.
    user_id = str(uuid.uuid4())
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
            supabase.table("profiles").insert(profile_row).execute()
        except Exception:
            # 'org' column not migrated yet — retry without it.
            profile_row.pop("org", None)
            supabase.table("profiles").insert(profile_row).execute()
    except Exception as e:
        print(f"[AUTH] insertion du profil échouée pour {user_id}:\n{traceback.format_exc()}")
        status, detail = _parse_db_error(str(e))
        raise HTTPException(status_code=status, detail=detail)

    # ── Étape 3 : créer les identifiants ──────────────────────────
    # Table SÉPARÉE de profiles : plusieurs endpoints font select("*") sur
    # profiles et renvoient la ligne au navigateur — un hachage posé là partirait
    # avec. Voir services/credentials.py.
    try:
        credentials.create(user_id, body.email, password_hash)
    except Exception as e:
        print(f"[AUTH] insertion des identifiants échouée pour {user_id}:\n{traceback.format_exc()}")
        # Le profil vient d'être créé et n'a pas de mot de passe : il serait
        # inutilisable ET bloquerait toute nouvelle tentative (email UNIQUE).
        # Même rôle que la suppression de l'utilisateur GoTrue orphelin d'avant.
        try:
            supabase.table("profiles").delete().eq("id", user_id).execute()
            print(f"[AUTH] profil orphelin {user_id} supprimé")
        except Exception as cleanup_err:  # noqa: BLE001
            print(f"[AUTH] nettoyage du profil orphelin impossible: {cleanup_err}")
        status, detail = _parse_db_error(str(e))
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


def _locked_out(seconds: int) -> HTTPException:
    """429 assorti de Retry-After, quand le verrou PERSISTANT est encore actif.

    429 plutôt que 423 : la page de connexion affiche `detail` quel que soit le
    code, et lib/api.js:32 ne détourne l'utilisateur que sur 401 — un 429 laisse
    donc le message à l'écran, ce qui est le comportement voulu ici.
    """
    minutes = max(1, round(seconds / 60))
    return HTTPException(
        status_code=429,
        detail=(
            f"Trop de tentatives infructueuses. Compte bloqué pendant encore "
            f"{minutes} minute{'s' if minutes > 1 else ''}. Vous pouvez aussi "
            "réinitialiser votre mot de passe, ce qui débloque immédiatement l'accès."
        ),
        headers={"Retry-After": str(seconds)},
    )


async def _check_password(row: Optional[dict], password: str, now: datetime) -> bool:
    """Vérifie un mot de passe et tient le compteur d'échecs persistant à jour.

    `row` peut être None (adresse inconnue) : `credentials.verify` paie quand
    même un hachage complet, pour que le temps de réponse n'énumère pas les
    comptes.
    """
    ok = await run_in_threadpool(credentials.verify, row, password)
    if not ok:
        if row:
            credentials.record_failure(row, now)
        return False
    credentials.record_success(row["user_id"])
    # Remontée de coût transparente : si les paramètres Argon2id ont été relevés
    # depuis, c'est le seul moment où le mot de passe est en clair. Un seul
    # algorithme de bout en bout — on ne relit jamais un format étranger.
    if passwords.needs_rehash(row.get("password_hash") or ""):
        try:
            neuf = await run_in_threadpool(passwords.hash_password, password)
            credentials.set_password(row["user_id"], neuf, now)
        except Exception as e:  # noqa: BLE001
            print(f"[AUTH] re-hachage impossible pour {row['user_id']}: {e}")
    return True


async def _reauthenticate(user_id: str, password: str) -> bool:
    """Re-vérifie le mot de passe d'un utilisateur DÉJÀ authentifié.

    Utilisé par PATCH /auth/me (changer d'e-mail ou de mot de passe) et par
    POST /auth/me/mfa/disable. Le verrou d'échecs s'applique aussi ici : sans
    cela, une session volée offrirait un oracle de devinette du mot de passe
    sans aucune limite persistante.

    Lève 429 si le compte est verrouillé, renvoie False si le mot de passe est
    faux, True sinon.
    """
    now = datetime.now(timezone.utc)
    try:
        cred = credentials.by_user_id(user_id)
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] lecture des identifiants impossible pour {user_id}: {e}")
        raise HTTPException(status_code=503, detail="Vérification du mot de passe indisponible. Réessayez.")
    if cred is None:
        # Profil sans ligne d'identifiants : anomalie, mais surtout pas un
        # laissez-passer. On refuse.
        print(f"[AUTH] aucun identifiant en base pour {user_id}")
        return False
    attente = credentials.lock_seconds_remaining(cred, now)
    if attente:
        raise _locked_out(attente)
    return await _check_password(cred, password, now)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    # Deux freins COMPLÉMENTAIRES, et pas redondants :
    #   • `_throttle` (mémoire du processus) refuse la requête AVANT les ~44 ms
    #     d'Argon2 : c'est lui qui protège le CPU. Mais il disparaît à chaque
    #     `systemctl restart` (donc à chaque déploiement).
    #   • le verrou en base (ci-dessous) survit aux redémarrages et serait vu par
    #     tous les workers si l'on en ajoutait un jour.
    _throttle(f"login:ip:{_client_ip(request)}", 15, 300)
    _throttle(f"login:email:{body.email.lower()}", 8, 300)

    now = datetime.now(timezone.utc)
    try:
        cred = credentials.by_email(body.email)
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] lecture des identifiants impossible: {e}")
        raise HTTPException(status_code=503, detail="Service d'authentification indisponible. Réessayez dans un instant.")

    # Verrou vérifié AVANT le hachage : un compte bloqué ne doit pas coûter de CPU.
    if cred:
        attente = credentials.lock_seconds_remaining(cred, now)
        if attente:
            raise _locked_out(attente)

    if not await _check_password(cred, body.password, now):
        # Message unique pour « adresse inconnue » et « mot de passe faux » :
        # sinon la page de connexion devient un annuaire des comptes existants.
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")

    user_id = cred["user_id"]

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
    return _finalize_login(user_id, body.email, profile, public_client_ip(request))


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
    return _finalize_login(user_id, email, profile, public_client_ip(request))


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
    return _finalize_login(user_id, email, profile, public_client_ip(request))


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
    user_id = user["sub"]
    _throttle(f"mfa:self-disable:{user_id}", 5, 300)
    if not body.current_password:
        raise HTTPException(status_code=422, detail="Mot de passe actuel requis pour désactiver la double authentification.")
    # Recherche par IDENTIFIANT, pas par e-mail : le jeton de session porte
    # l'adresse figée à la connexion, or l'utilisateur a pu en changer depuis
    # (PATCH /auth/me). GoTrue ne voyait pas la différence — il recevait
    # l'adresse ; nous, si.
    if not await _reauthenticate(user_id, body.current_password):
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


@router.get("/me/ai-literacy")
async def get_ai_literacy(user: dict = Depends(get_current_user)):
    """État de sensibilisation IA de l'utilisateur courant (AI Act, art. 4)."""
    from services import ai_literacy
    try:
        row = supabase.table("profiles").select(
            "ai_literacy_ack_at, ai_literacy_version"
        ).eq("id", user["sub"]).single().execute().data or {}
    except Exception:  # noqa: BLE001 - colonnes non migrées, base injoignable…
        # Ne jamais bloquer l'app sur une sonde de conformité : on répond
        # « jamais attesté », l'utilisateur pourra régulariser.
        row = {}
    return ai_literacy.status(row)


@router.post("/me/ai-literacy")
async def ack_ai_literacy(user: dict = Depends(get_current_user)):
    """Enregistre l'attestation de lecture du module de sensibilisation IA.

    Volontairement sans corps de requête : l'utilisateur atteste de la version
    COURANTE, celle que le serveur connaît. Laisser le client choisir la version
    permettrait d'attester d'un contenu qu'il n'a pas vu.
    """
    from services import ai_literacy
    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("profiles").update({
            "ai_literacy_ack_at": now,
            "ai_literacy_version": ai_literacy.VERSION,
        }).eq("id", user["sub"]).execute()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Enregistrement de l'attestation impossible.")
    return ai_literacy.status({
        "ai_literacy_ack_at": now,
        "ai_literacy_version": ai_literacy.VERSION,
    })


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    # `token` remplace `access_token` : ce n'est plus un JWT Supabase déchiffrable
    # par le navigateur, mais une valeur OPAQUE de 256 bits dont seule l'empreinte
    # est connue du serveur. Le champ change de nom exprès — un `access_token`
    # envoyé par un vieil onglet doit échouer en 422, pas être interprété.
    token: str
    new_password: str


class ResetTokenRequest(BaseModel):
    token: str


def _send_reset_email(to_email: str, reset_url: str, cle: str = "password_reset",
                      contexte_sup: Optional[dict] = None) -> tuple[bool, Optional[str]]:
    """
    Send the password-reset email via our own SMTP, branded as
    Groupement-IT — instead of letting Supabase send it from
    "Supabase Auth <noreply@mail.app.supabase.io>", which alarms users and
    trips spam filters. Returns (success, error); never raises.

    `cle` sélectionne le modèle. « password_migration » pour un compte venu de
    Supabase qui n'avait encore aucun mot de passe chez nous : lui envoyer
    « vous avez demandé à réinitialiser » serait faux, et surtout inquiétant —
    il n'a jamais eu de mot de passe à réinitialiser ici.
    """
    # Sujet + corps + coquille via la source unique (= aperçu admin fidèle).
    context = {"link": reset_url}
    context.update(contexte_sup or {})
    subject, html, text = email_templates.build_email(cle, context)
    # Via la file : un échec SMTP transitoire perdait définitivement le lien, et
    # l'utilisateur restait bloqué sans recours. La file réessaie, et l'envoyeur
    # tourne toutes les 20 s — le délai reste sous le seuil de perception d'un
    # « consultez votre boîte mail ».
    from services import email_outbox
    row = email_outbox.enqueue(
        to_email=to_email, subject=subject, html=html, text=text,
        category=cle, template_key=cle,
    )
    return (row is not None), None if row else "Dépôt en file impossible"


def _profil_a_migrer(email: str) -> Optional[dict]:
    """Profil existant SANS identifiants — un compte hérité de Supabase.

    `ilike` sans joker : `profiles.email` conserve la casse d'origine, une
    égalité stricte raterait « Julian.Talou@… ». Les comptes suspendus ou
    désactivés sont exclus : la suspension est une décision d'administration, et
    ce n'est pas à un formulaire public de la défaire.
    """
    lignes = supabase.table("profiles").select("id, email, name, status").ilike(
        "email", (email or "").strip().lower()
    ).limit(2).execute().data or []
    if len(lignes) != 1:
        return None
    profil = lignes[0]
    if (profil.get("status") or "active") in ("suspended", "disabled"):
        return None
    return profil


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """
    Émet un lien de réinitialisation MAISON et l'envoie par notre SMTP.

    Ce qui change par rapport à Supabase : le jeton n'est plus un JWT de
    récupération posé dans le FRAGMENT de l'URL (que le backend ne voyait jamais,
    et ne pouvait donc ni révoquer ni limiter à un seul usage), mais une valeur
    opaque de 256 bits dont seule l'EMPREINTE SHA-256 est écrite en base. Une
    copie de `user_credentials` ne contient donc aucun lien exploitable.

    Réponse 200 systématique : ne jamais révéler si l'adresse existe.
    """
    # Anti-abus : cet endpoint public déclenche des e-mails sortants.
    _throttle(f"fp:ip:{_client_ip(request)}", 5, 900)
    _throttle(f"fp:email:{body.email.lower()}", 3, 900)
    try:
        cred = credentials.by_email(body.email)

        # ── Compte hérité de Supabase, encore sans identifiants ──────────
        #
        # Sans ce rattrapage, ce point d'entrée est une IMPASSE pour exactement
        # les gens qu'il devrait servir : la migration 0019 ne reprend aucun
        # hachage, donc les comptes existants n'ont pas de ligne
        # `user_credentials`, donc `by_email` renvoie None, donc l'endpoint
        # répond « un lien a été envoyé » et n'envoie rien. La personne attend
        # un e-mail qui n'arrivera jamais, et le seul recours restant est
        # d'appeler quelqu'un.
        #
        # On provisionne donc la ligne à la demande, avec un hachage que
        # personne ne connaît : aucun compte n'est créé, aucun accès n'est
        # ouvert — on rend simplement joignable un compte qui existe déjà.
        profil = None
        if not cred:
            profil = _profil_a_migrer(body.email)
            if profil:
                credentials.provision_for_migration(
                    profil["id"], (profil.get("email") or body.email).strip().lower()
                )
                cred = credentials.by_email(body.email)

        # « EN MIGRATION » NE VEUT PAS DIRE « LIGNE ABSENTE ».
        #
        # La première demande CRÉE la ligne. Se fier à son absence faisait donc
        # disparaître le signe distinctif au premier clic : la deuxième demande —
        # celle qu'on fait quand le premier e-mail tarde, le geste le plus
        # naturel qui soit — repartait en « vous avez demandé à réinitialiser »,
        # valable 1 heure au lieu de 7 jours, ET invalidait le lien du premier
        # e-mail. Deux messages contradictoires, le seul valide étant le mauvais.
        # Constaté en production le 11 août, sur deux appels à 1,5 seconde
        # d'intervalle.
        #
        # La colonne `password_defini` (migration 0020) répond à la vraie
        # question : cette personne a-t-elle déjà choisi un mot de passe ? Elle
        # ne bouge qu'au moment où quelqu'un en choisit un, donc elle reste
        # stable quel que soit le nombre de clics.
        #
        # Absente (0020 pas encore appliquée) → True, ce qui est vrai de toutes
        # les lignes créées avant elle : on retombe sur l'ancien comportement au
        # lieu de traiter tout le monde comme un compte migré.
        migre = bool(cred) and not cred.get("password_defini", True)
        if migre and profil is None:
            profil = _profil_a_migrer(body.email) or {}

        if cred:
            clear, token_hash = passwords.new_reset_token()
            # Un compte migré reçoit une validité plus longue : il ne « demande »
            # pas un lien au sens habituel — il découvre, souvent au pire moment,
            # qu'il ne peut plus entrer. Une heure suffit à qui vient de cliquer
            # sur « mot de passe oublié » ; elle ne suffit pas à qui s'y prend
            # depuis un téléphone en réunion.
            echeance = (
                datetime.now(timezone.utc) + timedelta(days=MIGRATION_LIEN_JOURS)
                if migre else passwords.reset_token_expiry()
            )
            if credentials.issue_reset(cred["user_id"], token_hash, echeance):
                # Le clair ne va QUE dans l'e-mail. Il n'est ni journalisé, ni
                # stocké — le journaliser reviendrait à écrire un mot de passe
                # temporaire dans journalctl.
                reset_url = f"{settings.frontend_url}/reset-password?token={clear}"
                if migre:
                    sent, err = _send_reset_email(
                        cred["email"], reset_url, cle="password_migration",
                        contexte_sup={
                            "name": (profil.get("name") or "").split(" ")[0] or "bonjour",
                            "validite": f"{MIGRATION_LIEN_JOURS} jours",
                            # Permet au destinataire de vérifier sans cliquer :
                            # ouvrir lui-même la plateforme aboutit au même lien.
                            "plateforme": settings.frontend_url.rstrip("/"),
                        },
                    )
                else:
                    sent, err = _send_reset_email(cred["email"], reset_url)
                if not sent:
                    print(f"[AUTH] lien de réinitialisation non déposé en file: {err}")
            else:
                print("[AUTH] impossible d'armer le jeton de réinitialisation (0 ligne mise à jour)")
        else:
            # Adresse inconnue : on journalise pour l'exploitation, on ne révèle rien.
            print("[AUTH] forgot-password sur une adresse sans compte (non fatal)")
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] forgot-password error (non-fatal): {e}")
    return {"message": "Si un compte existe pour cet email, un lien de réinitialisation a été envoyé."}


@router.post("/reset-password/verify")
async def verify_reset_token(body: ResetTokenRequest, request: Request):
    """Vérifie un jeton SANS le consommer, et renvoie l'adresse qu'il concerne.

    Sert uniquement à pré-remplir le champ « username » masqué de la page, pour
    que le trousseau du navigateur associe le nouveau mot de passe au bon compte
    (ResetPasswordPage.jsx). Auparavant, le front obtenait cette adresse en
    décodant le JWT Supabase avec `atob` ; un jeton opaque ne se décode pas.

    Divulgation acceptable : qui détient le jeton peut déjà prendre le contrôle
    du compte, apprendre l'adresse ne lui donne rien de plus. En revanche il faut
    limiter le débit, sinon l'endpoint devient un oracle de validité de jetons.
    """
    _throttle(f"rpv:ip:{_client_ip(request)}", 20, 900)
    row = credentials.by_reset_token_hash(passwords.hash_reset_token(body.token))
    if not row or passwords.reset_token_is_expired(row.get("reset_token_expires_at")):
        raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")
    return {"valid": True, "email": row["email"]}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request):
    """Consomme le jeton de réinitialisation et pose le nouveau mot de passe."""
    _throttle(f"rp:ip:{_client_ip(request)}", 10, 900)
    try:
        passwords.check_password(body.new_password)
    except passwords.PasswordRejected as e:
        raise HTTPException(status_code=422, detail=str(e))

    token_hash = passwords.hash_reset_token(body.token)
    now = datetime.now(timezone.utc)
    try:
        row = credentials.by_reset_token_hash(token_hash)
        # Message identique pour « jeton inconnu », « jeton déjà utilisé » et
        # « jeton périmé » : distinguer les trois indiquerait à un attaquant
        # qu'il a trouvé un jeton valide mais consommé.
        if not row or passwords.reset_token_is_expired(row.get("reset_token_expires_at"), now):
            raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")

        new_hash = await run_in_threadpool(passwords.hash_password, body.new_password)
        # L'usage unique se joue ICI : l'UPDATE filtre sur l'empreinte encore
        # présente et l'efface dans le même ordre. Un second appel ne met à jour
        # aucune ligne et PostgREST renvoie une liste vide — pas besoin de verrou
        # applicatif. Même motif que le claim de services/email_outbox.py.
        if not credentials.consume_reset(token_hash, new_hash, now):
            raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] reset-password error: {e}")
        raise HTTPException(status_code=400, detail="Lien de réinitialisation invalide ou expiré.")
    return {"message": "Mot de passe mis à jour avec succès."}


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
    ancien_email: Optional[str] = None

    # Email or password change requires current password verification
    if body.email or body.new_password:
        if not body.current_password:
            raise HTTPException(status_code=422, detail="Mot de passe actuel requis pour changer l'email ou le mot de passe.")
        # Cet endpoint vérifie un mot de passe : c'est donc un oracle de
        # devinette pour qui détiendrait une session volée. Il n'avait aucune
        # limite propre — GoTrue appliquait la sienne, et elle part avec lui.
        # Même cadence que les autres re-authentifications (mfa/disable).
        _throttle(f"me:reauth:{user_id}", 5, 300)
        if not await _reauthenticate(user_id, body.current_password):
            raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")

        # L'adresse D'ABORD, le mot de passe ensuite. L'ordre compte : « adresse
        # déjà utilisée » est l'échec le plus probable ici (une faute de saisie
        # suffit), et il est refusé par la contrainte UNIQUE. En le traitant en
        # premier, on ressort en 409 sans avoir touché au mot de passe ; dans
        # l'ordre inverse, l'utilisateur verrait une erreur alors que son mot de
        # passe aurait DÉJÀ changé — et il ne saurait plus lequel utiliser.
        if body.email:
            # `ancien_email` est retenu pour pouvoir revenir en arrière si la
            # mise à jour du profil échoue plus bas : sinon les deux tables se
            # contrediraient et l'utilisateur se connecterait avec une adresse
            # que son profil n'affiche pas.
            existant = credentials.by_user_id(user_id)
            ancien_email = (existant or {}).get("email")
            try:
                credentials.set_email(user_id, body.email)
            except Exception as e:  # noqa: BLE001
                status, detail = _parse_db_error(str(e))
                raise HTTPException(status_code=status, detail=detail)

        if body.new_password:
            try:
                passwords.check_password(body.new_password)
            except passwords.PasswordRejected as e:
                raise HTTPException(status_code=422, detail=str(e))
            new_hash = await run_in_threadpool(passwords.hash_password, body.new_password)
            if not credentials.set_password(user_id, new_hash):
                raise HTTPException(status_code=500, detail="Impossible de mettre à jour le mot de passe.")

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
            # Remise en cohérence : l'adresse de connexion a déjà changé, mais le
            # profil non. Sans ce retour arrière, l'utilisateur se connecterait
            # avec une adresse introuvable dans l'écran « Comptes » de l'admin.
            if ancien_email:
                try:
                    credentials.set_email(user_id, ancien_email)
                except Exception as revert_err:  # noqa: BLE001
                    print(f"[AUTH] retour arrière de l'email de connexion impossible pour {user_id}: {revert_err}")
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