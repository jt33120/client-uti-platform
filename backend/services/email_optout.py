"""
Désabonnement par TYPE de notification.

Chaque email de notification porte en pied : « Vous recevez cet email car vous
êtes abonné aux notifications « X ». Ne plus recevoir ce type de notification »,
le dernier fragment étant un lien signé qui désabonne sans connexion.

Trois décisions qui expliquent le reste du fichier.

**Le désabonnement est indexé sur l'ADRESSE, pas sur le compte.** Une partie des
destinataires n'a pas de compte (annonce à une adresse générique) et un
partenaire peut changer de compte sans changer d'adresse. C'est l'adresse qui
reçoit, c'est donc elle qui se désabonne.

**Le lien n'expire pas.** Un lien de désabonnement qui a expiré est pire que pas
de lien : le destinataire clique, échoue, et signale l'email en spam — ce qui
coûte au domaine d'envoi bien plus que le risque couvert. Le jeton ne donne
d'ailleurs qu'un seul pouvoir, réversible et sans valeur pour un attaquant :
cesser d'envoyer des emails à une adresse.

**Tous les emails n'en reçoivent pas.** Un lien de désabonnement sur une
réinitialisation de mot de passe ou sur une invitation, c'est un moyen de se
couper soi-même l'accès à son compte. Ces clés sont absentes de CATEGORIES :
elles gardent le pied de page explicatif du modèle, sans lien.
"""
import hashlib
import hmac
from typing import Optional

import jwt

from config import settings
from services.supabase_client import supabase
from services.error_log import record as _record_err

TABLE = "email_optouts"

#: Audience du jeton. Vérifiée au décodage : même avec la même clé, un jeton de
#: session ou d'URL de fichier est refusé ici (cf. storage._cle_de_signature).
AUDIENCE = "uti/email-optout/v1"
ALG = "HS256"

#: Clé de template → catégorie de désabonnement.
#:
#: Les six notifications de suivi de CV partagent UNE catégorie : se désabonner
#: de « CV retenu » en restant abonné à « CV non retenu » n'a pas de sens, et
#: six cases pour un seul besoin fait renoncer le destinataire.
#:
#: Une clé ABSENTE d'ici est transactionnelle : aucun lien, aucun filtrage.
#: C'est le cas de `invite`, `password_reset`, `password_migration` (couper
#: l'accès à son propre compte) et de `cv_client` (envoi nominatif déclenché à
#: la main par un commercial, pas un abonnement).
CATEGORIES: dict[str, str] = {
    "ao_new": "ao_new",
    "ao_relance": "ao_relance",
    "cv_retenu": "cv_suivi",
    "cv_non_retenu": "cv_suivi",
    "cv_envoye_client": "cv_suivi",
    "echange_commercial": "cv_suivi",
    "affaire_gagnee": "cv_suivi",
    "affaire_perdue": "cv_suivi",
    "annonce_pilote": "annonces",
}

#: Catégorie → libellé affiché entre guillemets dans le pied de page. C'est le
#: nom que le destinataire lit ; il doit décrire ce qu'il cesse de recevoir, pas
#: le nom technique du modèle.
LABELS: dict[str, str] = {
    "ao_new": "Nouvel appel d'offres",
    "ao_relance": "Relance d'appel d'offres",
    "cv_suivi": "Suivi des CV proposés",
    "annonces": "Annonces de la plateforme",
}


def category_for(key: Optional[str]) -> Optional[str]:
    """Catégorie de désabonnement d'une clé de template, ou None si transactionnelle."""
    return CATEGORIES.get(key or "")


def label_for(key: Optional[str]) -> Optional[str]:
    """Libellé lisible de la catégorie d'une clé, ou None."""
    cat = category_for(key)
    return LABELS.get(cat) if cat else None


def normalize(email: Optional[str]) -> str:
    """Forme de comparaison d'une adresse : espaces retirés, minuscules.

    La casse de la partie locale est théoriquement significative en SMTP ; en
    pratique aucun fournisseur ne la distingue, et un désabonnement qui ne
    prendrait pas parce que l'adresse a été saisie en capitales serait un
    désabonnement qui ne marche pas.
    """
    return (email or "").strip().lower()


def _cle_de_signature() -> str:
    """Clé HMAC des liens de désabonnement.

    Dérivée de `jwt_secret` par HMAC, comme les URLs de fichiers, et avec une
    chaîne de domaine DIFFÉRENTE : un jeton de fichier ne doit pas pouvoir
    servir de jeton de désabonnement, ni l'inverse. La dérivation donne cette
    séparation sans ajouter un secret de plus à gérer.
    """
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), b"uti/email-optout/v1", hashlib.sha256
    ).hexdigest()


def sign(email: str, category: str) -> str:
    """Jeton liant EXACTEMENT une adresse et une catégorie.

    Les deux sont signés, pas seulement transportés : sans quoi le porteur d'un
    lien pourrait désabonner n'importe qui de n'importe quoi.
    """
    return jwt.encode(
        {"e": normalize(email), "c": category, "aud": AUDIENCE},
        _cle_de_signature(),
        algorithm=ALG,
    )


def verify(token: str) -> tuple[str, str]:
    """(adresse, catégorie) d'un jeton valide. Lève ValueError sinon."""
    try:
        payload = jwt.decode(
            token, _cle_de_signature(), algorithms=[ALG], audience=AUDIENCE
        )
    except Exception as e:  # noqa: BLE001 - signature, audience, format
        raise ValueError("Lien de désabonnement invalide ou expiré") from e
    email, category = normalize(payload.get("e")), payload.get("c")
    if not email or category not in LABELS:
        raise ValueError("Lien de désabonnement invalide")
    return email, category


def unsubscribe_url(email: Optional[str], key: Optional[str]) -> Optional[str]:
    """URL publique de désabonnement, ou None si la clé n'est pas désabonnable.

    Sur le BACKEND (`PUBLIC_BASE_URL`) et **jamais** sur le frontend : la route
    est servie par FastAPI, alors que le frontend est un site statique sur
    Vercel dont seul `/api/*` est relayé. Un repli sur `frontend_url` donnerait
    donc un lien qui rend 404 — pire qu'une absence de lien, parce qu'il promet
    un recours qui n'existe pas et pousse au bouton « Spam ».

    Sans `PUBLIC_BASE_URL`, on renvoie None : pas de lien plutôt qu'un lien
    mort. La variable est déjà obligatoire dans le mode de stockage courant
    (config.py), donc ce cas ne se produit pas en production.
    """
    category = category_for(key)
    if not category or not normalize(email):
        return None
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/emails/unsubscribe?token={sign(email, category)}"


def is_blocked(email: Optional[str], key: Optional[str]) -> bool:
    """Cette adresse s'est-elle désabonnée du type d'email correspondant à `key` ?

    Répond False quand la clé est transactionnelle : un mot de passe oublié
    part toujours.

    **En cas d'erreur de lecture, répond False (on envoie).** La table peut ne
    pas exister (migration backend/migrations/0021_email_optouts.sql non jouée),
    ou PostgREST peut répondre 403 si elle a été créée sans GRANT vers
    service_role. La dégradation correcte est « aucun désabonnement
    enregistré », pas « plus aucune notification ne part », qui transformerait
    une migration oubliée en panne silencieuse de tout le canal email.
    """
    category = category_for(key)
    addr = normalize(email)
    if not category or not addr:
        return False
    try:
        rows = supabase.table(TABLE).select("email").eq("email", addr).eq(
            "category", category
        ).limit(1).execute().data
        return bool(rows)
    except Exception as e:  # noqa: BLE001
        _record_err("email", f"Lecture des désabonnements impossible ({addr})", exc=e)
        return False


def record(email: str, category: str, source: str = "lien") -> bool:
    """Enregistre le désabonnement. Renvoie True si l'adresse est bien désabonnée.

    Contrairement à la lecture, un échec est signalé à l'appelant : afficher
    « c'est fait » sur une écriture qui a échoué garantit que la personne ne
    réessaiera pas, et continuera de recevoir les emails.
    """
    addr = normalize(email)
    if not addr or category not in LABELS:
        return False
    try:
        supabase.table(TABLE).insert(
            {"email": addr, "category": category, "source": source}
        ).execute()
        return True
    except Exception as e:  # noqa: BLE001
        # Déjà désabonné : c'est exactement le résultat voulu, pas une erreur.
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return True
        _record_err("email", f"Désabonnement non enregistré ({addr}/{category})", exc=e)
        return False
