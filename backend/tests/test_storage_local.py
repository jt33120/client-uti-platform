"""
Garde-fous du stockage LOCAL : ce qui protège les fichiers quand plus personne
ne les protège à notre place.

Les fichiers ont quitté Supabase Storage pour le disque du VPS. Trois propriétés
que personne d'autre n'assure désormais, et qui se dégradent toutes en silence :

  1. **le chemin ne sort jamais du dépôt.** `path` vient de la base, et la base
     a connu des valeurs de formes diverses (services/storage.py:_object_path).
     Un `..` hérité d'un import ou une ligne trafiquée ne doit pas pouvoir lire
     /etc/shadow ni le .env qui porte vingt secrets.
  2. **la signature couvre le chemin, et elle expire.** L'URL est ouverte par le
     navigateur SANS en-tête Authorization : la preuve d'autorisation tient dans
     l'URL. Une signature qui ne lierait pas le chemin serait un jeton d'accès
     arbitraire — un lien de CV ouvrirait n'importe quelle attestation URSSAF.
  3. **un jeton de session n'est pas un jeton de fichier.** Les deux sont des
     JWT HS256 dans le même processus ; c'est exactement le montage dont
     .env.example:60-65 explique le danger à propos de PostgREST.

POURQUOI CE FICHIER EXÉCUTE LE CODE, ALORS QUE test_storage_acl.py LE LIT
test_storage_acl.py vérifie une RÈGLE écrite (la liste blanche d'ACL) : la lire
suffit. Ici on vérifie des COMPORTEMENTS — une traversée refusée, une signature
périmée — et un comportement ne se lit pas dans un texte. Aucune E/S réseau
n'est en jeu : les branches locales de services/storage.py ne parlent qu'au
disque, sur un `tmp_path` fourni par pytest.
"""
import ast
import os
import pathlib
import re
import sys
import time
import types

import pytest
from jose import ExpiredSignatureError, JWTError, jwt

BACKEND = pathlib.Path(__file__).resolve().parents[1]
RACINE = BACKEND.parent

# services/storage.py importe services/supabase_client.py, qui construit un
# client au CHARGEMENT — et refuse une clé de test. Ce n'est pas une raison de
# mettre ces vérifications en `skip` : le stockage local ne parle jamais à
# Supabase. On pose donc un bouchon UNIQUEMENT si le vrai module refuse de se
# charger, exactement comme tests/conftest.py:16-24 le fait pour le paquet.
try:  # pragma: no cover - dépend de l'environnement
    import services.supabase_client  # noqa: F401
except Exception:  # pragma: no cover
    _bouchon = types.ModuleType("services.supabase_client")
    _bouchon.supabase = None
    _bouchon.get_supabase = lambda: None
    sys.modules["services.supabase_client"] = _bouchon

from config import settings  # noqa: E402
from services import storage  # noqa: E402

SECRET_DE_TEST = "secret-de-test-0123456789abcdef0123456789abcdef"


@pytest.fixture
def depot(tmp_path, monkeypatch):
    """Bascule le module en mode « local » sur un répertoire jetable."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "public_base_url", "https://exemple.test")
    monkeypatch.setattr(settings, "jwt_secret", SECRET_DE_TEST)
    monkeypatch.setattr(settings, "file_url_secret", None)
    return tmp_path


# ── 1. Le chemin ne sort jamais du dépôt ───────────────────────────────────

@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "/etc/passwd",
    "ao-1/../../../home/julian.talou/app/backend/.env",
    "..\\..\\etc\\passwd",   # antislash : pas un séparateur sous Linux, mais une
                             # valeur héritée peut en contenir
    "./../secrets",
    "..",
    "",
    "   ",
    "a\x00b.pdf",            # l'octet nul tronque le nom au niveau du noyau
])
def test_la_traversee_de_chemin_est_refusee(depot, hostile):
    """Un chemin qui sortirait du dépôt doit lever, pas lire.

    Le dépôt est voisin de /home/julian.talou/app/backend/.env, qui porte
    JWT_SECRET, SMTP_PASSWORD et les clés LLM. Un `..` qui passerait ne serait
    pas un incident de stockage : ce serait la plateforme entière.
    """
    with pytest.raises(storage.StoragePathError):
        storage._safe_join("cvs", hostile)


def test_un_lien_symbolique_sortant_est_refuse(depot):
    """Le contrôle syntaxique des segments ne voit PAS un lien symbolique.

    Seule la résolution (`Path.resolve`, qui suit les liens) l'attrape. C'est la
    raison d'être du second contrôle, et sans ce test il serait supprimé un jour
    comme « redondant ».
    """
    (depot / "cvs").mkdir()
    (depot / "cvs" / "piege").symlink_to("/etc")
    with pytest.raises(storage.StoragePathError):
        storage._safe_join("cvs", "piege/passwd")


def test_un_nom_de_bucket_ne_peut_pas_traverser(depot):
    """Le bucket est lui aussi un composant de chemin.

    Verrouiller le `path` en laissant passer « ../secrets » comme bucket
    reviendrait à fermer la porte et laisser la fenêtre ouverte.
    """
    with pytest.raises(storage.StoragePathError):
        storage._safe_join("../secrets", "a.pdf")


# ── 2. La signature couvre le chemin, et elle expire ───────────────────────

def test_une_signature_expiree_est_refusee(depot):
    """Sans expiration effective, une URL de CV transmise à un client resterait
    ouverte indéfiniment — y compris après la fin de la mission."""
    perime = jwt.encode(
        {"b": "cvs", "p": "ao/cv.pdf", "aud": storage.FILE_TOKEN_AUDIENCE,
         "iat": int(time.time()) - 120, "exp": int(time.time()) - 1},
        storage._cle_de_signature(), algorithm=storage.FILE_TOKEN_ALG,
    )
    with pytest.raises(ExpiredSignatureError):
        storage.verify_file_token(perime)


def test_la_signature_lie_exactement_le_chemin(depot):
    """Changer le chemin dans un jeton valide doit invalider la signature.

    C'est la propriété qui distingue « ce porteur peut lire CE CV » de « ce
    porteur peut lire ». Sans elle, le lien d'un CV ouvrirait une attestation
    de vigilance URSSAF en changeant un mot dans l'URL.
    """
    jeton = storage.sign_file_token("cvs", "ao-1/cv-1.pdf", 600)
    assert storage.verify_file_token(jeton) == ("cvs", "ao-1/cv-1.pdf")

    entete, charge, signature = jeton.split(".")
    autre = jwt.encode(
        {"b": "compliance", "p": "p1/urssaf.pdf", "aud": storage.FILE_TOKEN_AUDIENCE,
         "iat": int(time.time()), "exp": int(time.time()) + 600},
        "une-autre-cle-que-la-notre", algorithm=storage.FILE_TOKEN_ALG,
    )
    # Charge utile d'un jeton légitime + signature d'un autre : le montage
    # classique. Les deux moitiés sont valides séparément, l'assemblage non.
    forge = f"{entete}.{autre.split('.')[1]}.{signature}"
    with pytest.raises(JWTError):
        storage.verify_file_token(forge)


def test_la_duree_de_vie_est_plafonnee(depot):
    """Un appelant distrait ne doit pas pouvoir fabriquer une URL éternelle.

    Le plus long usage légitime est le lien de CV envoyé au client final
    (services/cv_notifications.py:114, 7 jours). Au-delà, ce n'est plus une URL
    signée, c'est une publication.
    """
    jeton = storage.sign_file_token("cvs", "ao/cv.pdf", 10 ** 9)
    claims = jwt.decode(jeton, storage._cle_de_signature(),
                        algorithms=[storage.FILE_TOKEN_ALG],
                        audience=storage.FILE_TOKEN_AUDIENCE)
    assert claims["exp"] - claims["iat"] == storage.MAX_SIGNED_URL_TTL


# ── 3. Un jeton de session n'est pas un jeton de fichier ───────────────────

def test_un_jeton_de_session_ne_vaut_pas_url_de_fichier(depot):
    """Le jeton émis par routers/auth.py:create_token doit être REFUSÉ ici.

    Les deux sont des JWT HS256 signés dans le même processus. Sans séparation,
    un jeton de session collé dans une URL deviendrait un droit de lecture sur
    n'importe quel fichier, et il voyagerait alors dans les journaux nginx et
    l'historique du navigateur — là où une session n'a rien à faire.
    """
    session = jwt.encode(
        {"sub": "u1", "email": "x@y.z", "role": "admin", "exp": int(time.time()) + 3600},
        settings.jwt_secret, algorithm="HS256",
    )
    with pytest.raises(JWTError):
        storage.verify_file_token(session)


def test_la_cle_des_urls_differe_du_secret_de_session(depot):
    """Séparation de domaine : deux usages, deux clés.

    Par défaut la clé d'URL est DÉRIVÉE de jwt_secret — ce qui donne la
    séparation sans un secret de plus à faire tourner — mais elle ne doit jamais
    LUI ÊTRE ÉGALE. Le jour où quelqu'un « simplifiera » en réutilisant
    settings.jwt_secret directement, ce test parlera.
    """
    assert storage._cle_de_signature() != settings.jwt_secret


# ── 4. Ce qui est privé le reste, y compris dans l'URL rendue ──────────────

def test_un_bucket_prive_ne_recoit_pas_durl_publique(depot):
    """upload() renvoie une URL PUBLIQUE stable pour un avatar, et un CHEMIN NU
    pour un CV.

    Rendre une URL publique pour un CV serait un mensonge stocké en base :
    routers/partners.py:369 et routers/email_templates.py:226 écrivent cette
    valeur telle quelle, et un écran finirait par l'afficher comme un lien.
    """
    prive = storage.upload("cvs", "ao-1/cv.pdf", b"%PDF", "application/pdf")
    public = storage.upload("avatars", "u1/avatar.png", b"\x89PNG", "image/png")
    assert prive == "ao-1/cv.pdf"
    assert public == "https://exemple.test/files/public/avatars/u1/avatar.png"


def test_ensure_bucket_ne_rend_rien_public_sur_demande(depot):
    """`ensure_bucket(..., public=True)` ne doit RIEN rendre public.

    routers/email_templates.py:224 passe public=True. Si l'appelant décidait,
    il suffirait d'un copier-coller de cette ligne dans routers/partners.py pour
    publier des KBIS. C'est PUBLIC_BUCKETS qui décide, et elle seule.
    """
    storage.ensure_bucket("compliance", public=True)
    assert "compliance" not in storage.PUBLIC_BUCKETS


def test_les_trois_formes_heritees_donnent_le_meme_chemin(depot):
    """submissions.cv_url a porté trois formes successives : URL publique
    Supabase, URL S3, chemin nu. Les trois doivent produire le même objet.

    C'est ce qui permet de basculer sans réécrire la base dans la seconde — et
    donc de revenir en arrière si la bascule se passe mal.
    """
    formes = [
        "https://p.supabase.co/storage/v1/object/public/cvs/ao-1/cv.pdf",
        "https://uti-files.s3.gra.io.cloud.ovh.net/cvs/ao-1/cv.pdf",
        "ao-1/cv.pdf",
    ]
    for stockee in formes:
        lien = storage.signed_cv_url(stockee, 600)
        assert storage.verify_file_token(lien.rsplit("/", 1)[1]) == ("cvs", "ao-1/cv.pdf")


# ── 5. Droits UNIX et contenu servi ────────────────────────────────────────

def test_les_droits_ne_dependent_pas_du_umask(depot):
    """0600 / 0700, quel que soit le umask du processus.

    Le umask est un réglage d'environnement (0022 par défaut) : s'y fier
    donnerait des CV en 0644, lisibles par TOUT compte local du VPS. Le test
    force volontairement un umask permissif, qui est le cas où le défaut se
    produirait.
    """
    ancien = os.umask(0o000)
    try:
        storage.upload("compliance", "p1/urssaf.pdf", b"%PDF", "application/pdf")
    finally:
        os.umask(ancien)
    fichier = depot / "compliance" / "p1" / "urssaf.pdf"
    assert fichier.stat().st_mode & 0o777 == 0o600
    assert fichier.parent.stat().st_mode & 0o777 == 0o700
    assert (depot / "compliance").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("nom", ["piege.html", "piege.htm", "piege.svg", "piege.xhtml", "piege.js"])
def test_aucun_fichier_depose_par_un_tiers_nest_rendu_en_html(nom):
    """Servis par notre propre domaine, ces fichiers deviennent SAME-ORIGIN.

    Chez Supabase et chez OVH, ils étaient sur une autre origine : un .svg
    piégé n'y valait rien. Servis par le backend, ils s'exécuteraient dans
    l'origine de la plateforme. routers/partners.py:365 n'impose aucune
    extension aux pièces de conformité : la liste blanche de types est la seule
    chose qui ferme ce trou.
    """
    type_contenu, disposition = storage.content_disposition_for(nom)
    assert type_contenu == "application/octet-stream"
    assert disposition == "attachment"


# ── 6. Ce qui se vérifie mieux en lisant la source ─────────────────────────

def test_le_service_des_fichiers_nest_pas_delegue_a_nginx():
    """Décision assumée : le backend sert, nginx ne fait que relayer.

    X-Accel-Redirect obligerait à rendre le dépôt LISIBLE PAR www-data — un
    second lecteur pour des CV, des attestations URSSAF et des KBIS — et
    scinderait la décision « qui a le droit de lire » entre deux systèmes. C'est
    exactement le montage qui avait laissé les CV en public-read
    (tests/test_storage_acl.py:4-11). Le jour où quelqu'un l'ajoutera « pour la
    performance », ce test lui demandera d'abord d'écrire pourquoi.
    """
    nginx = (BACKEND / "nginx.conf").read_text(encoding="utf-8")
    code = "\n".join(l for l in nginx.splitlines() if not l.lstrip().startswith("#"))
    assert "X-Accel-Redirect" not in code and "internal;" not in code, (
        "nginx.conf sert les fichiers lui-même : www-data doit alors pouvoir "
        "lire le dépôt, et l'autorisation vit désormais à deux endroits."
    )
    assert "SEUIL DE BASCULE" in nginx, (
        "Le seuil au-delà duquel X-Accel-Redirect deviendrait justifié n'est "
        "plus écrit : la question se retranchera à l'instinct."
    )


def test_le_routeur_ne_sert_sans_jeton_que_la_liste_blanche():
    """L'entrée publique doit se refermer sur PUBLIC_BUCKETS, pas sur une
    liste noire de buckets sensibles.

    Une liste noire protège ce qu'on a pensé à y mettre ; une liste blanche
    protège tout le reste, y compris le bucket que quelqu'un ajoutera demain.
    """
    source = (BACKEND / "routers" / "files.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
    assert "storage.PUBLIC_BUCKETS" in code, (
        "routers/files.py ne consulte plus la liste blanche : l'entrée publique "
        "servirait les CV et les attestations URSSAF."
    )
    for interdit in ('"cvs"', '"compliance"', '"ao-sources"'):
        assert interdit not in code, (
            f"routers/files.py nomme {interdit} dans son code : une règle par "
            f"énumération de ce qu'il faut protéger est une liste noire."
        )


def test_la_configuration_refuse_un_backend_de_stockage_inconnu():
    """Avant, toute valeur autre que « s3 » retombait en silence sur Supabase.

    Avec un troisième backend, « STORAGE_BACKEND=locale » écrirait les fichiers
    dans le projet Supabase — celui qu'on s'apprête à supprimer. La faute de
    frappe doit empêcher le démarrage, pas produire une perte différée.
    """
    source = (BACKEND / "config.py").read_text(encoding="utf-8")
    arbre = ast.parse(source)
    valeurs = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Assign) and any(
            isinstance(c, ast.Name) and c.id == "BACKENDS_STOCKAGE" for c in noeud.targets
        ):
            valeurs = [e.value for e in noeud.value.elts if isinstance(e, ast.Constant)]
    assert sorted(valeurs) == ["local", "s3", "supabase"], (
        "config.py ne déclare plus la liste des backends acceptés."
    )
    assert re.search(r"storage_backend not in BACKENDS_STOCKAGE", source), (
        "La garde sur une valeur inconnue de STORAGE_BACKEND a disparu."
    )
    assert re.search(r'storage_backend == "local" and not settings\.public_base_url', source), (
        "Le backend démarre en mode local sans PUBLIC_BASE_URL : les liens de CV "
        "seraient relatifs, donc résolus sur le domaine Vercel du frontend."
    )
