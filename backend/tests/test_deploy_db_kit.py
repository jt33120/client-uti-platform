"""
Garde-fous du kit de déploiement PostgreSQL + PostgREST.

Ces règles ne se voient dans aucun fichier pris isolément, et se paient très
cher : une ligne manquante dans un fichier de configuration expose la base
entière à Internet, une autre fait tomber toutes les requêtes en 401 sans
message exploitable. Les vérifier ici coûte une seconde de CI ; les découvrir en
production coûte une soirée — et pour la première, une fuite de données.

Le test de signature, lui, est fonctionnel : il vérifie que le JWT fabriqué à la
main par make_service_key.py (bibliothèque standard uniquement, pour tourner
avant que le venv n'existe) est bien celui qu'une implémentation de référence
produirait.
"""
import importlib.util
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = BACKEND / "deploy"
INSTALL = DEPLOY / "install_db.sh"
ROLES = DEPLOY / "roles_postgrest.sql"
NGINX = DEPLOY / "nginx-postgrest.conf"
UNIT = DEPLOY / "postgrest.service"
MAKE_KEY = BACKEND / "scripts" / "make_service_key.py"


def _charger_make_key():
    """Importe le script par chemin : il vit dans scripts/, pas dans un paquet."""
    spec = importlib.util.spec_from_file_location("make_service_key", MAKE_KEY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── PostgREST : les deux réglages dont l'absence est catastrophique ─────────

def test_postgrest_ecoute_uniquement_en_local():
    """Sans `server-host`, PostgREST écoute sur 0.0.0.0 (défaut « !4 »).

    Le service tourne avec un rôle qui contourne la RLS : sur un VPS sans
    pare-feu, l'oubli de cette ligne publie la base entière sur Internet.
    """
    conf = INSTALL.read_text(encoding="utf-8")
    assert 'server-host = "127.0.0.1"' in conf, (
        "install_db.sh ne fixe plus server-host : PostgREST écouterait sur "
        "toutes les interfaces du VPS."
    )


def test_aucun_role_anonyme_configure():
    """`db-anon-role` non défini = 401 sur toute requête sans jeton.

    Le frontend Vercel ne parle jamais à la base (aucune dépendance @supabase/*),
    donc aucun accès anonyme n'a de raison d'exister. Définir ce réglage
    ouvrirait un chemin de lecture sans jeton.
    """
    lignes = [
        ligne for ligne in INSTALL.read_text(encoding="utf-8").splitlines()
        if re.match(r'^\s*db-anon-role\s*=', ligne)
    ]
    assert not lignes, f"db-anon-role est défini dans install_db.sh : {lignes}"


def test_binaire_postgrest_verifie_par_empreinte():
    """Le binaire est lancé en service permanent : il doit être vérifié."""
    conf = INSTALL.read_text(encoding="utf-8")
    assert re.search(r'PGRST_SHA256="[0-9a-f]{64}"', conf), (
        "L'empreinte SHA-256 du binaire PostgREST a disparu de install_db.sh."
    )
    assert "sha256sum -c" in conf, "L'empreinte n'est plus vérifiée au téléchargement."


# ── nginx : la façade qui traduit /rest/v1/ ────────────────────────────────

def test_facade_nginx_est_locale_et_reecrit_le_chemin():
    """supabase-py appelle {SUPABASE_URL}/rest/v1/<table>.

    La barre oblique finale de proxy_pass est ce qui retire le préfixe : sans
    elle PostgREST reçoit /rest/v1/consultants et cherche une table « rest ».
    Et l'adresse d'écoute doit être explicite, sinon nginx prend toutes les
    interfaces.
    """
    conf = NGINX.read_text(encoding="utf-8")
    assert re.search(r"^\s*listen\s+127\.0\.0\.1:\d+;", conf, re.M), (
        "Le bloc nginx n'écoute plus explicitement sur 127.0.0.1."
    )
    assert re.search(r"location\s+/rest/v1/\s*\{", conf), "location /rest/v1/ absent."
    assert re.search(r"proxy_pass\s+http://127\.0\.0\.1:\d+/;", conf), (
        "proxy_pass a perdu sa barre oblique finale : le préfixe /rest/v1 ne "
        "serait plus retiré."
    )
    assert "proxy_intercept_errors off" in conf, (
        "Sans cette ligne, une page d'erreur nginx remplacerait le JSON de "
        "PostgREST, que le backend lit pour reconnaître PGRST116 ou 23505."
    )


# ── Rôles PostgreSQL ───────────────────────────────────────────────────────

def test_service_role_contourne_la_rls_et_authenticator_non():
    """Les 22 tables ont la RLS activée sans aucune policy : sans BYPASSRLS,
    service_role ne verrait AUCUNE ligne, quels que soient les GRANT."""
    sql = ROLES.read_text(encoding="utf-8").lower()
    assert "alter role service_role bypassrls" in sql
    assert "alter role authenticator noinherit nobypassrls" in sql, (
        "authenticator doit rester sans privilège propre : c'est le SET ROLE "
        "commandé par le jeton qui décide, pas l'union des rôles hérités."
    )


def test_privileges_par_defaut_pour_les_futures_tables():
    """Un GRANT ... ON ALL TABLES ne couvre que l'existant.

    Sans ALTER DEFAULT PRIVILEGES, la première table créée par une migration
    répondrait 403 « permission denied » sur cette seule table, longtemps après
    le déploiement.
    """
    sql = ROLES.read_text(encoding="utf-8").lower()
    assert "alter default privileges for role" in sql
    assert "grant all privileges on tables to service_role" in sql


def test_les_roles_ne_sont_pas_dans_les_migrations():
    """scripts/check_schema_drift.py rejoue backend/migrations/0*.sql sur une
    base jetable (check_schema_drift.py:103). Les rôles sont des objets de
    CLUSTER : y placer ce fichier ferait modifier par un simple contrôle de
    dérive des rôles partagés avec la production."""
    for fichier in (BACKEND / "migrations").glob("0*.sql"):
        contenu = fichier.read_text(encoding="utf-8").lower()
        assert "create role" not in contenu, f"{fichier.name} crée un rôle de cluster."
        assert "bypassrls" not in contenu, f"{fichier.name} touche à BYPASSRLS."


# ── Unité systemd ──────────────────────────────────────────────────────────

def test_unite_postgrest_recharge_sans_se_tuer():
    """PostgREST ne gère PAS SIGHUP : le signal par défaut le TUE. ExecReload
    doit donc envoyer SIGUSR2 (rechargement de configuration)."""
    unit = UNIT.read_text(encoding="utf-8")
    assert "ExecReload=/bin/kill -USR2 $MAINPID" in unit
    assert "User=postgrest" in unit, "Le service ne doit jamais tourner en root."
    assert "ProtectHome=true" in unit, (
        "PostgREST n'a rien à lire dans /home — ni le .env du backend, ni les CV."
    )


# ── Signature de la clé de service ─────────────────────────────────────────

SECRET = "0" * 64  # 64 caractères : au-dessus du minimum HS256 de 32 octets


def test_jwt_maison_identique_a_une_implementation_de_reference():
    """La signature HS256 écrite en bibliothèque standard doit être exacte.

    make_service_key.py n'utilise ni PyJWT ni python-jose parce qu'il tourne
    pendant l'installation du VPS, avant l'existence du venv. En contrepartie,
    sa correction se prouve ici, contre python-jose (déjà dans requirements.txt).
    """
    jose_jwt = pytest.importorskip("jose.jwt")
    module = _charger_make_key()
    charge = {"role": "service_role", "iat": 1_700_000_000}

    jeton = module.sign_hs256(charge, SECRET)
    assert jose_jwt.decode(jeton, SECRET, algorithms=["HS256"]) == charge


def test_cle_produite_acceptee_par_la_validation_de_supabase_py():
    """supabase-py refuse à la construction toute clé qui n'a pas la forme d'un
    JWT (supabase/_sync/client.py:60-64). Une clé invalide empêcherait le
    backend de démarrer, avec un message sans rapport avec ce script."""
    module = _charger_make_key()
    jeton = module.sign_hs256({"role": "service_role"}, SECRET)
    assert module.SUPABASE_KEY_RE.match(jeton)


def test_secret_lu_sans_le_saut_de_ligne(tmp_path):
    """PostgREST retire les blancs de fin des secrets chargés par « @fichier ».

    Signer avec le contenu brut d'un fichier produit par « openssl rand -hex 32 >»
    donnerait une clé rejetée en 401, sans autre indice.
    """
    module = _charger_make_key()
    fichier = tmp_path / "jwt.secret"
    fichier.write_text(SECRET + "\n", encoding="utf-8")
    assert module.read_secret(fichier) == SECRET


def test_secret_trop_court_refuse(tmp_path):
    """En dessous de 32 octets, PostgREST répond 401 « JWSInvalidSignature » à
    TOUTES les requêtes — un symptôme qui n'évoque en rien la longueur du
    secret. Mieux vaut échouer ici, avec un message clair."""
    module = _charger_make_key()
    fichier = tmp_path / "jwt.secret"
    fichier.write_text("trop-court", encoding="utf-8")
    with pytest.raises(SystemExit):
        module.read_secret(fichier)


# ── Ajouté après un incident réel ───────────────────────────────────────────

def test_le_script_ne_touche_pas_au_env_dune_prod_encore_sur_supabase():
    """L'URL et la clé forment un couple : jamais l'une sans l'autre.

    CE QUI S'EST PASSÉ. Une version antérieure écrivait la nouvelle clé de
    service dans le .env de production en laissant SUPABASE_URL pointer sur
    Supabase, au motif — juste mais incomplet — que basculer l'URL couperait la
    connexion des utilisateurs. Or la clé produite ici est signée par NOTRE
    secret : Supabase la rejette. Le couple (URL Supabase, clé locale) est aussi
    cassé que l'inverse.

    Et il l'est SILENCIEUSEMENT : pydantic-settings lit le .env au démarrage,
    donc le processus en cours continuait de tourner avec l'ancienne valeur en
    mémoire. La plateforme ne serait tombée qu'au redémarrage suivant — un
    déploiement, un reboot — c'est-à-dire au pire moment et sans lien apparent
    avec l'installation faite des heures plus tôt.

    Le script doit donc vérifier que l'URL désigne DÉJÀ la façade locale avant
    d'écrire quoi que ce soit.
    """
    src = INSTALL.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

    ecrit = [l for l in code.splitlines() if "SUPABASE_SERVICE_KEY=${KEY}" in l]
    assert ecrit, "Le script n'écrit plus la clé nulle part — vérifier ce test."

    assert "URL_ACTUELLE" in code, (
        "Le script n'inspecte plus SUPABASE_URL avant d'écrire dans .env. Il "
        "peut donc de nouveau laisser une production dans un état qui ne se "
        "voit qu'au prochain redémarrage."
    )
    garde = re.search(
        r'if printf .*URL_ACTUELLE.*grep -q "127\.0\.0\.1:\$\{REST_PORT\}', code
    )
    assert garde, (
        "La garde qui vérifie que l'URL désigne la façade locale a disparu ou "
        "changé de forme."
    )


def test_letape_la_plus_longue_nest_pas_muette():
    """apt en -qq sur plusieurs minutes pousse à interrompre l'installation.

    Et une interruption pendant `apt-get install` laisse dpkg à moitié
    configuré, ce qui coûte bien plus cher que le bruit qu'on économisait.
    """
    src = INSTALL.read_text(encoding="utf-8")
    installs = [l for l in src.splitlines()
                if "apt-get install" in l and "postgresql-${PG_VERSION}" in l
                and not l.lstrip().startswith("#")]
    assert installs, "Plus aucune installation de PostgreSQL dans le script."
    for ligne in installs:
        assert "-qq" not in ligne, (
            f"L'installation de PostgreSQL est repassée en -qq : {ligne.strip()}"
        )


def test_les_regles_ufw_visent_le_port_ssh_reel():
    """Jamais le profil « OpenSSH », qui vaut 22.

    CE QUI S'EST PASSÉ. Le script imprimait `sudo ufw allow OpenSSH`. Sur ce VPS
    sshd écoute sur 1622 : la commande a ouvert un port où rien n'écoute et
    laissé le vrai fermé, avec une politique par défaut « deny ».

    Le piège est que RIEN NE SE VOIT sur le moment. ufw laisse passer les
    connexions déjà ÉTABLIES, donc la session en cours continue de fonctionner et
    valide faussement l'opération. C'est à la reconnexion suivante qu'on découvre
    qu'on est dehors — et comme ufw JETTE les paquets au lieu de les rejeter, le
    ssh reste suspendu sans message : on soupçonne le réseau bien avant le
    pare-feu. Sur un VPS distant dont on n'a pas la console, ça se paie en accès
    perdu.

    Le script doit donc LIRE les ports d'écoute de sshd, pas les supposer.
    """
    src = INSTALL.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

    assert "ufw allow OpenSSH" not in code, (
        "Le script imprime de nouveau `ufw allow OpenSSH`, qui ouvre le port 22 "
        "quel que soit le port réellement en écoute."
    )
    assert "SSH_PORTS" in code, (
        "Le script ne détecte plus le port SSH : il ne peut donc que le supposer."
    )
    assert re.search(r"ss -ltnp.*sshd", code), (
        "La détection par `ss -ltnp` sur sshd a disparu."
    )
    assert "sshd_config" in code, (
        "Le repli par lecture de sshd_config a disparu — `ss` peut manquer sur "
        "une image minimale, et supposer 22 est précisément le défaut à éviter."
    )


def test_le_script_avertit_que_la_session_en_cours_ne_prouve_rien():
    """Le garde-fou humain, sans lequel la règle technique ne suffit pas."""
    src = INSTALL.read_text(encoding="utf-8")
    assert "SECONDE CONNEXION" in src, (
        "L'avertissement qui dit d'ouvrir une seconde connexion AVANT de fermer "
        "l'actuelle a disparu. Sans lui, une règle fausse est validée par une "
        "session qui survivait de toute façon."
    )
