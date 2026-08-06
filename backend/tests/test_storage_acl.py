"""
Garde-fou : les CV ne deviennent jamais publics sur S3.

Deux endroits du code posent une ACL sur les objets envoyés vers OVH Object
Storage : le chemin d'exécution (services/storage.py) et le script de migration
des fichiers existants (scripts/migrate_storage_to_ovh.py). Le premier gardait
les CV privés, le second écrivait ACL="public-read" sans distinction. Une seule
exécution du script aurait rendu tous les CV lisibles par quiconque connaît
l'URL — un CV, c'est un nom, un téléphone, un parcours professionnel.

Le défaut n'était visible d'aucun des deux fichiers pris isolément : chacun était
cohérent avec lui-même. C'est pour ce genre de règle — vraie à deux endroits qui
ne se lisent pas l'un l'autre — qu'un test vaut mieux qu'un commentaire.

Lecture par `ast`, sans import : le script tire boto3 et la configuration
Supabase, dont l'absence mettrait le test en skip précisément là où on voudrait
qu'il parle.
"""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "migrate_storage_to_ovh.py"
SERVICE = BACKEND / "services" / "storage.py"


def _module(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _put_object_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "put_object"
    ]


def test_migration_script_never_hardcodes_public_read():
    """Aucun put_object du script ne doit passer ACL='public-read' en dur.

    L'ACL doit être conditionnée au bucket, pas appliquée à tout le monde.
    """
    for call in _put_object_calls(_module(SCRIPT)):
        for kw in call.keywords:
            if kw.arg == "ACL":
                valeur = getattr(kw.value, "value", None)
                assert valeur != "public-read", (
                    "scripts/migrate_storage_to_ovh.py repose une ACL public-read "
                    "inconditionnelle : les CV redeviendraient publics à la "
                    "première exécution."
                )


def test_cvs_bucket_is_declared_private_in_both_places():
    """« cvs » doit être traité comme privé par le script ET par le runtime."""
    src_script = SCRIPT.read_text(encoding="utf-8")
    assert "PRIVATE_BUCKETS" in src_script, (
        "Le script de migration ne distingue plus les buckets privés des publics."
    )
    assert '"cvs"' in src_script.split("PRIVATE_BUCKETS", 1)[1][:200], (
        "« cvs » ne figure plus dans PRIVATE_BUCKETS."
    )

    src_service = SERVICE.read_text(encoding="utf-8")
    assert 'bucket == "cvs"' in src_service, (
        "services/storage.py ne réserve plus un traitement privé au bucket cvs."
    )


def test_migration_script_covers_every_populated_bucket():
    """Les trois buckets qui contiennent des objets doivent être copiés.

    « ao-sources » manquait : les fichiers sources des appels d'offres seraient
    restés derrière, et leurs liens auraient cessé de fonctionner à la fermeture
    du projet Supabase — après la bascule, donc trop tard pour s'en apercevoir.
    """
    tree = _module(SCRIPT)
    buckets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BUCKETS" for t in node.targets
        ):
            buckets = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    assert buckets, "BUCKETS introuvable dans le script de migration."
    for attendu in ("cvs", "avatars", "ao-sources"):
        assert attendu in buckets, f"Le bucket « {attendu} » ne serait pas migré."
