"""
Toute table utilisée par le code doit avoir son DDL dans le dépôt.

Motivation : `pacs` et `pac_clients` ont vécu des mois en production sans être
versionnées (créées à la main dans l'éditeur SQL Supabase). Un environnement
reconstruit depuis le dépôt — staging, reprise après incident, nouveau
déploiement — aurait démarré avec un schéma incomplet, et le repli silencieux de
`_OPTIONAL_COLS` aurait masqué la perte de `consultants.consent_at`, qui porte la
preuve du consentement RGPD.

Ce test rend l'écart impossible à reformer sans que la CI le dise.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

# supabase.table("nom") / .table('nom')
_TABLE_CALL = re.compile(r"""\.table\(\s*["']([a-z_][a-z0-9_]*)["']\s*\)""")
# CREATE TABLE [IF NOT EXISTS] [public.]nom
_CREATE_TABLE = re.compile(
    r"""create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?["']?([a-z_][a-z0-9_]*)""",
    re.IGNORECASE,
)


def _tables_via_constante(source: str) -> set[str]:
    """`TABLE = "email_optouts"` en tête de module, puis `supabase.table(TABLE)`.

    Le motif littéral ci-dessus rate ces appels — et ce sont exactement ceux des
    modules qui n'exploitent QU'UNE table et lui donnent une constante :
    `credentials.py` (user_credentials), `email_optout.py` (email_optouts). Donc
    précisément les tables qu'un test censé garantir « tout est versionné »
    laissait passer sans rien dire.
    """
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return set()
    # Constantes de MODULE seulement : une variable locale nommée `table` dans
    # une fonction ne désigne pas une table au sens où on l'entend ici.
    constantes = {
        cible.id: noeud.value.value
        for noeud in arbre.body
        if isinstance(noeud, ast.Assign)
        and isinstance(noeud.value, ast.Constant)
        and isinstance(noeud.value.value, str)
        for cible in noeud.targets
        if isinstance(cible, ast.Name)
    }
    return {
        constantes[noeud.args[0].id]
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Call)
        and isinstance(noeud.func, ast.Attribute) and noeud.func.attr == "table"
        and len(noeud.args) == 1 and isinstance(noeud.args[0], ast.Name)
        and noeud.args[0].id in constantes
    }


def _tables_used_by_code() -> set[str]:
    found: set[str] = set()
    for directory in ("routers", "services"):
        for path in (BACKEND / directory).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            found |= set(_TABLE_CALL.findall(source))
            found |= _tables_via_constante(source)
    return found


def _tables_with_versioned_ddl() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.sql"):
        if ".git" in path.parts:
            continue
        found |= {m.lower() for m in _CREATE_TABLE.findall(path.read_text(encoding="utf-8"))}
    return found


def test_every_table_used_by_the_code_has_versioned_ddl():
    used = _tables_used_by_code()
    assert used, "aucun appel .table(...) trouvé — le motif de détection a dû changer"

    missing = sorted(used - _tables_with_versioned_ddl())
    assert not missing, (
        "Ces tables sont utilisées par le backend mais n'ont aucun CREATE TABLE "
        f"dans le dépôt : {missing}. Ajoute une migration dans backend/migrations/ "
        "— sinon un environnement reconstruit depuis le dépôt démarrera avec un "
        "schéma incomplet."
    )


def test_consent_at_is_never_silently_dropped():
    """`consent_at` est une preuve RGPD : elle ne doit pas pouvoir être dégradée.

    `_insert_with_geo_fallback` rejoue l'insertion sans les colonnes de
    `_OPTIONAL_COLS` quand elle échoue. Y remettre `consent_at` ferait créer des
    consultants sans consentement en renvoyant 200.

    Lecture par AST plutôt qu'import : le garde-fou doit tourner partout, y
    compris là où l'arbre de dépendances du backend n'est pas installé. Un test
    qui se met en `skip` ne garde rien.
    """
    source = (BACKEND / "routers" / "consultants.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    optional_cols = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_OPTIONAL_COLS" for t in node.targets
        ):
            optional_cols = ast.literal_eval(node.value)

    assert optional_cols is not None, "_OPTIONAL_COLS introuvable dans routers/consultants.py"
    assert "consent_at" not in optional_cols, (
        "consent_at est de retour dans _OPTIONAL_COLS : l'insertion serait rejouée "
        "sans lui en cas d'échec, créant des consultants sans preuve de consentement "
        "tout en renvoyant 200. Voir backend/migrations/0011."
    )


# ── Ce que la reconstruction rejoue RÉELLEMENT ─────────────────────────────
#
# Le test ci-dessus se contente de « il existe un CREATE TABLE quelque part dans
# le dépôt ». C'est nécessaire et insuffisant : `check_schema_drift.py` ne
# rejoue pas tout le dépôt, il rejoue une LISTE. Un DDL versionné mais absent de
# cette liste ne reconstruit rien, et le contrôle de dérive le signale à tort
# comme un objet créé à la main en production.
#
# C'est arrivé : `sql_files()` ne rendait que `schema.sql`, dont l'en-tête dit
# lui-même qu'il ne crée aucune table d'identifiants. Toute l'authentification
# maison (migration 0019) était donc portée disparue par le contrôle, depuis son
# écriture. Un contrôle qui signale à tort finit par ne plus être lu.

def _rejeu():
    """Charge check_schema_drift.py par chemin — `scripts/` n'est pas un paquet."""
    import importlib.util
    chemin = BACKEND / "scripts" / "check_schema_drift.py"
    spec = importlib.util.spec_from_file_location("_drift", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_le_rejeu_inclut_toutes_les_migrations_posterieures_au_schema():
    drift = _rejeu()
    rejouees = {f.name for f in drift.sql_files()}
    attendues = {
        f.name for f in (BACKEND / "migrations").glob("0*.sql")
        if drift._numero(f) >= drift.PREMIERE_MIGRATION_HORS_SCHEMA
    }
    assert attendues, "aucune migration postérieure au schéma — le seuil a dû dériver"
    manquantes = sorted(attendues - rejouees)
    assert not manquantes, (
        f"Ces migrations ne sont pas rejouées par la reconstruction : {manquantes}. "
        "Une migration ajoutée après la consolidation doit l'être automatiquement."
    )
    assert "schema.sql" in rejouees


def test_toute_table_du_code_est_reconstruite_par_le_rejeu():
    """L'invariant que le contrôle de dérive est censé garantir.

    « Si je perds cette base, le dépôt suffit-il à la recréer ? » — la réponse
    ne dépend pas de ce qui traîne dans le dépôt, mais de ce que la
    reconstruction joue.
    """
    drift = _rejeu()
    creees: set[str] = set()
    for chemin in drift.sql_files():
        creees |= {m.lower() for m in _CREATE_TABLE.findall(chemin.read_text(encoding="utf-8"))}

    manquantes = sorted(_tables_used_by_code() - creees)
    assert not manquantes, (
        "Ces tables sont utilisées par le backend mais AUCUN fichier rejoué par "
        f"check_schema_drift.py ne les crée : {manquantes}. Une base reconstruite "
        "depuis le dépôt démarrerait sans elles."
    )
