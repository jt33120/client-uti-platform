"""
Aucun hachage de mot de passe ne doit pouvoir sortir par l'API.

C'est la raison d'être de la table séparée. Le rappel du danger : plusieurs
endpoints font `select("*")` sur `profiles` et renvoient la ligne telle quelle
au navigateur (routers/auth.py, `GET /auth/me` et `PATCH /auth/me`). Le seul
rempart pour `mfa_secret` — la dernière donnée sensible restée dans `profiles` —
est un `data.pop("mfa_secret", None)` que chaque nouvel endpoint doit penser à
écrire. Ce fichier vérifie qu'on ne reproduit PAS ce montage pour les mots de
passe, et qu'on ne l'abîme pas.

Comme test_schema_versioned.py et test_register_requires_invite.py, il lit la
SOURCE plutôt que d'importer : un test qui se met en `skip` faute de dépendance
ne garde rien, et c'est justement dans un environnement dépouillé qu'on aimerait
qu'il parle.
"""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ROUTERS = BACKEND / "routers"
FRONTEND = REPO / "frontend" / "src"

#: Colonnes de `user_credentials` qui ne doivent JAMAIS transiter vers un client.
SECRETES = ("password_hash", "reset_token_hash")

#: Le seul module autorisé à parler à la table. Tout le reste passe par lui.
PROPRIETAIRE = BACKEND / "services" / "credentials.py"

#: Fonctions de services/credentials.py qui renvoient la LIGNE ENTIÈRE, hachage
#: compris — c'est nécessaire pour vérifier le mot de passe et lire le compteur
#: d'échecs, mais cette ligne ne doit jamais ressortir telle quelle.
LECTEURS = {"by_email", "by_user_id", "by_reset_token_hash", "create"}


def _sources_backend():
    for chemin in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in chemin.parts or "tests" in chemin.parts:
            continue
        yield chemin, chemin.read_text(encoding="utf-8")


def _routeurs():
    for chemin in sorted(ROUTERS.glob("*.py")):
        yield chemin, ast.parse(chemin.read_text(encoding="utf-8"))


def test_only_one_module_touches_the_credentials_table():
    """Un point d'accès unique : c'est ce qui rend la revue possible.

    Si dix fichiers interrogeaient `user_credentials`, garantir qu'aucun ne
    renvoie sa ligne demanderait de tous les relire à chaque modification.
    """
    coupables = [
        str(c.relative_to(REPO)) for c, src in _sources_backend()
        if 'table("user_credentials")' in src and c != PROPRIETAIRE
    ]
    assert not coupables, (
        "ces fichiers interrogent user_credentials directement au lieu de passer "
        f"par services/credentials.py : {coupables}"
    )


def _chaines_de_select(arbre):
    """Toutes les chaînes littérales passées à un `.select(...)`."""
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr == "select"):
            for arg in noeud.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg.value


def test_no_select_can_reach_the_credentials_table():
    """PostgREST ne joint que ce qu'on lui demande.

    `select("*")` sur `profiles` ne peut PAS atteindre `user_credentials` : il
    faudrait écrire `select("*, user_credentials(*)")`. C'est exactement ce qu'on
    interdit ici — et c'est ce qui rend la fuite impossible par construction,
    plutôt que dépendante d'un `pop()` qu'on pourrait oublier.
    """
    coupables = []
    for chemin, arbre in _routeurs():
        for chaine in _chaines_de_select(arbre):
            if "user_credentials" in chaine or any(c in chaine for c in SECRETES):
                coupables.append(f"{chemin.name}: select({chaine!r})")
    assert not coupables, f"un select peut ramener un secret d'authentification : {coupables}"


def _renvoie_tel_quel(noeud, noms):
    """La variable est-elle renvoyée ENTIÈRE (et non un seul de ses champs) ?

    `return cred` et `return {"user": cred}` sont des fuites.
    `return {"email": cred["email"]}` n'en est pas une — on extrait un champ.
    `return await _verifier(cred, ...)` non plus — la ligne est un argument,
    elle ne sort pas.
    """
    if isinstance(noeud, ast.Name):
        return noeud.id in noms
    if isinstance(noeud, ast.Dict):  # couvre aussi {**cred} : clé None, valeur = cred
        return any(v is not None and _renvoie_tel_quel(v, noms) for v in noeud.values)
    if isinstance(noeud, (ast.List, ast.Tuple, ast.Set)):
        return any(_renvoie_tel_quel(e, noms) for e in noeud.elts)
    if isinstance(noeud, ast.IfExp):
        return _renvoie_tel_quel(noeud.body, noms) or _renvoie_tel_quel(noeud.orelse, noms)
    if (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
            and noeud.func.id in ("dict", "list", "tuple", "set")):
        return any(_renvoie_tel_quel(a, noms) for a in noeud.args)
    return False


def _fonctions(arbre):
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield noeud


def test_no_endpoint_returns_a_credentials_row():
    """Une ligne d'identifiants lue par un routeur ne doit jamais être renvoyée."""
    coupables = []
    for chemin, arbre in _routeurs():
        for fn in _fonctions(arbre):
            porteuses = set()
            for noeud in ast.walk(fn):
                if not isinstance(noeud, ast.Assign) or not isinstance(noeud.value, ast.Call):
                    continue
                appele = noeud.value.func
                if isinstance(appele, ast.Attribute) and appele.attr in LECTEURS:
                    porteuses |= {c.id for c in noeud.targets if isinstance(c, ast.Name)}
            if not porteuses:
                continue
            for noeud in ast.walk(fn):
                if isinstance(noeud, ast.Return) and noeud.value is not None:
                    if _renvoie_tel_quel(noeud.value, porteuses):
                        coupables.append(f"{chemin.name}:{fn.name}")
    assert not coupables, (
        "une ligne d'identifiants (hachage compris) part vers le client : " + str(coupables)
    )


def test_no_return_ever_names_a_secret_column():
    """Complément du test précédent : l'extraction ciblée d'un champ secret.

    `return {"h": cred["password_hash"]}` ne renvoie pas la ligne entière — le
    contrôle ci-dessus ne le verrait pas. Ici on refuse le NOM de la colonne dans
    toute expression de retour d'un routeur, ce qui couvre le sous-scriptage
    comme le `.get()`.
    """
    coupables = []
    for chemin, arbre in _routeurs():
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Return) or noeud.value is None:
                continue
            for sous in ast.walk(noeud.value):
                if isinstance(sous, ast.Constant) and sous.value in SECRETES:
                    coupables.append(f"{chemin.name}: return … {sous.value!r}")
    assert not coupables, f"nom de colonne secrète dans un retour de routeur : {coupables}"


def test_frontend_never_sees_these_names():
    """Le navigateur n'a aucune raison de connaître ces champs.

    Leur apparition dans le front signalerait qu'un endpoint les renvoie.
    """
    if not FRONTEND.exists():  # backend seul (image de production)
        return
    coupables = []
    for chemin in sorted(FRONTEND.rglob("*.jsx")) + sorted(FRONTEND.rglob("*.js")):
        src = chemin.read_text(encoding="utf-8")
        for terme in SECRETES + ("user_credentials",):
            if terme in src:
                coupables.append(f"{chemin.name}: {terme}")
    assert not coupables, f"le frontend référence un champ d'identifiants : {coupables}"


def test_no_sql_file_adds_a_password_column_to_profiles():
    """`profiles` doit rester exempte de tout secret d'authentification.

    Une migration future qui ajouterait `password_hash` à `profiles` remettrait
    le hachage sur le chemin de `select("*")` — et donc dans le navigateur.
    """
    coupables = []
    fichiers = sorted(REPO.glob("*.sql")) + sorted((BACKEND / "migrations").glob("*.sql"))
    for chemin in fichiers:
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            nu = ligne.strip().lower()
            if nu.startswith("--"):
                continue
            if "profiles" in nu and any(c in nu for c in SECRETES):
                coupables.append(f"{chemin.name}: {ligne.strip()}")
    assert not coupables, f"colonne secrète ajoutée à profiles : {coupables}"


def test_credentials_table_is_declared_and_locked_down():
    """La migration doit créer la table ET l'isoler comme le reste du schéma.

    RLS activée sans aucune policy = refus total pour les rôles `anon` et
    `authenticated` ; seul le rôle utilisé par notre backend la lit. C'est la
    convention de tout le schéma (pg_policies est vide en production).
    """
    migration = BACKEND / "migrations" / "0019_auth_maison.sql"
    assert migration.exists(), "migration 0019_auth_maison.sql absente"
    sql = migration.read_text(encoding="utf-8").lower()
    assert "create table if not exists public.user_credentials" in sql
    assert "alter table public.user_credentials enable row level security" in sql
    assert "references public.profiles(id) on delete cascade" in sql, (
        "sans cascade, supprimer un compte laisserait ses identifiants en base"
    )


def test_profile_endpoints_still_strip_the_totp_secret():
    """Garde-fou existant, à ne pas perdre au passage.

    `profiles.mfa_secret` reste stocké en clair (chantier distinct : le chiffrer
    suppose de décider où vit la clé et comment on la fait tourner). Tant qu'il
    en est ainsi, les deux endpoints qui renvoient le profil entier doivent
    continuer de le retirer.
    """
    src = (ROUTERS / "auth.py").read_text(encoding="utf-8")
    assert src.count('pop("mfa_secret", None)') >= 2, (
        "GET /auth/me et PATCH /auth/me doivent tous deux retirer mfa_secret "
        "avant de renvoyer le profil."
    )
