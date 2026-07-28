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


def _tables_used_by_code() -> set[str]:
    found: set[str] = set()
    for directory in ("routers", "services"):
        for path in (BACKEND / directory).rglob("*.py"):
            found |= set(_TABLE_CALL.findall(path.read_text(encoding="utf-8")))
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
