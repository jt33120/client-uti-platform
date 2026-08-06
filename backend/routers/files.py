"""
Service des fichiers stockés sur le DISQUE DU VPS (STORAGE_BACKEND=local).

POURQUOI CE ROUTEUR EXISTE
Supabase et S3 signaient une URL que le navigateur ouvrait chez eux. En local,
personne ne signe et personne ne sert : c'est ici que ça se passe. Deux entrées,
et deux seulement, parce qu'il y a exactement deux besoins :

  GET /files/d/{jeton}                 objets PRIVÉS — CV, pièces jointes d'AO,
                                       attestations URSSAF et KBIS. Le bucket ET
                                       le chemin sont À L'INTÉRIEUR du jeton
                                       signé ; il n'existe aucun paramètre de
                                       chemin hors signature.
  GET /files/public/{bucket}/{chemin}  objets PUBLICS — avatars et images de
                                       modèles d'e-mail. URL stable, sans jeton :
                                       une balise <img> et un client de
                                       messagerie ne savent pas renouveler un
                                       lien expiré.

PAS D'AUTHENTIFICATION SUR /files/d, ET C'EST VOULU
Ces URLs sont ouvertes par le NAVIGATEUR sans en-tête Authorization : nouvel
onglet depuis le frontend Vercel, balise <img>, téléchargement, et — pour le CV
transmis au client final (services/cv_notifications.py:114) — clic depuis une
boîte mail où personne n'est connecté à la plateforme. Le modèle est donc celui
d'une CAPACITÉ à durée courte, exactement comme l'URL présignée S3 qu'elle
remplace : détenir le lien vaut autorisation, pendant une heure (sept jours pour
le lien client, plafonné par storage.MAX_SIGNED_URL_TTL). Ce qui change par
rapport à un jeton d'accès général, c'est que la signature couvre le chemin
demandé : le lien d'un CV n'ouvre que ce CV.

CE ROUTEUR NE SERT RIEN QUAND LE BACKEND N'EST PAS « local »
Sur un déploiement Supabase ou S3, le répertoire n'existe pas. Répondre 404
plutôt que 500 évite qu'une configuration à moitié basculée ressemble à une
panne de disque.
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from jose import ExpiredSignatureError, JWTError

from config import settings
from routers.auth import require_staff
from services import storage

router = APIRouter(prefix="/files", tags=["files"])

#: Durée de cache des objets PUBLICS. Volontairement courte : l'avatar est écrit
#: sous un chemin STABLE (`{user_id}/avatar.png`, routers/auth.py:1058) et
#: écrasé sur place. Un `immutable` d'un an y afficherait l'ancienne photo
#: jusqu'à vidage du cache. Cinq minutes suffisent à absorber une page qui
#: affiche vingt fois le même avatar.
CACHE_PUBLIC = "public, max-age=300"

#: Objets privés : jamais de cache partagé. `no-store` évite qu'un proxy
#: d'entreprise conserve une attestation URSSAF après expiration du lien.
CACHE_PRIVE = "private, no-store"


def _reponse_fichier(bucket: str, chemin: str, cache: str) -> FileResponse:
    """Sert le fichier après avoir revalidé le chemin. Lève 404 sinon.

    La validation est refaite ICI même si sign_file_token l'a déjà faite au
    moment d'émettre le lien : c'est ce contrôle-ci qui est opposable, l'autre
    n'est qu'une politesse envers l'appelant. Un jeton peut avoir été signé
    avant qu'une valeur trafiquée n'arrive en base.
    """
    try:
        cible = storage._safe_join(bucket, chemin)
    except storage.StoragePathError:
        # 404 et non 400 : distinguer « chemin refusé » de « fichier absent »
        # apprendrait à un curieux où s'arrête le répertoire de stockage.
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    if not cible.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    nom = os.path.basename(chemin)
    type_contenu, disposition = storage.content_disposition_for(nom)
    return FileResponse(
        path=cible,
        media_type=type_contenu,
        headers={
            # Le nom du fichier vient de `f.filename`, donc de l'utilisateur, et
            # _safe_join ne contrôle QUE la traversée — pas le jeu de caractères.
            # Deux conséquences si on l'interpole brut :
            #
            #   * un caractère hors latin-1 fait lever UnicodeEncodeError à
            #     l'encodage de l'en-tête. « CV d’Alice.pdf » — l'apostrophe
            #     typographique que produisent Word et macOS — rendrait le
            #     fichier DÉFINITIVEMENT inouvrable, alors qu'il est bien stocké
            #     et bien référencé en base ;
            #   * un guillemet double referme la valeur en cours et permet
            #     d'afficher au téléchargement un nom différent du nom réel.
            #
            # D'où les deux formes de la RFC 6266 : `filename=` en repli ASCII
            # nettoyé pour les clients anciens, `filename*=` en UTF-8 percent-
            # encodé pour le nom fidèle. Les navigateurs actuels préfèrent la
            # seconde.
            "Content-Disposition": storage.entete_disposition(disposition, nom),
            "Cache-Control": cache,
            # Ceinture et bretelles avec la liste blanche de types : sans
            # nosniff, un navigateur peut décider tout seul qu'un
            # « application/octet-stream » qui commence par « <html> » est du HTML.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/d/{token}")
def telecharger_objet_prive(token: str):
    """Sert un objet privé sur présentation d'un jeton signé non expiré."""
    if not storage._use_local():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    try:
        bucket, chemin = storage.verify_file_token(token)
    except ExpiredSignatureError:
        # 410 et non 403 : le lien A ÉTÉ valide. C'est le cas d'un CV transmis à
        # un client huit jours plus tôt, et le message doit dire quoi faire
        # plutôt que laisser croire à un refus de droits.
        raise HTTPException(
            status_code=410,
            detail="Ce lien a expiré. Redemandez-le depuis la plateforme.",
        )
    except JWTError:
        raise HTTPException(status_code=403, detail="Lien invalide.")
    return _reponse_fichier(bucket, chemin, CACHE_PRIVE)


@router.get("/public/{bucket}/{chemin:path}")
def telecharger_objet_public(bucket: str, chemin: str):
    """Sert un objet d'un bucket PUBLIC, sans jeton.

    La liste blanche storage.PUBLIC_BUCKETS est la seule autorité : « cvs »,
    « compliance » et « ao-sources » tombent en 404 ici quel que soit le chemin
    demandé, et un bucket ajouté demain y tombe aussi tant que personne ne l'a
    explicitement déclaré public.
    """
    if not storage._use_local():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    if bucket not in storage.PUBLIC_BUCKETS:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return _reponse_fichier(bucket, chemin, CACHE_PUBLIC)


@router.get("/_diagnostic")
def diagnostic_stockage(user: dict = Depends(require_staff)):
    """État du dépôt local, sans exposer aucun contenu ni aucun nom de fichier.

    « Le backend est en mode local et voit N fichiers » est ce qu'on veut lire à
    9 h 05 le jour de la bascule, plutôt que de découvrir à 11 h qu'un CV ne
    s'ouvre pas. Réservé au staff : le chemin du dépôt et le volume stocké
    n'apprennent rien d'utile à un visiteur, et tout ce qui est gratuit à fermer
    doit l'être.
    """
    if not storage._use_local():
        return {"backend": settings.storage_backend, "local": False}
    racine = storage._racine_locale()
    fichiers, octets = 0, 0
    if racine.is_dir():
        for dossier, _, noms in os.walk(racine):
            for nom in noms:
                try:
                    octets += os.path.getsize(os.path.join(dossier, nom))
                    fichiers += 1
                except OSError:
                    pass
    return {
        "backend": "local",
        "local": True,
        "racine": str(racine),
        "existe": racine.is_dir(),
        "fichiers": fichiers,
        "octets": octets,
    }
