"""
Garde-fou : rien de personnel ne devient public sur S3.

Deux défauts successifs, de la même famille.

Le premier : deux endroits posaient une ACL — le chemin d'exécution
(services/storage.py) et le script de migration — et ils ne disaient pas la même
chose. Le premier gardait les CV privés, le second écrivait « public-read » sur
tout. Chacun était cohérent avec lui-même ; c'est pour ça que l'écart ne se
voyait pas. Corrigé en supprimant la seconde définition : le script IMPORTE
désormais la règle.

Le second, plus large : la règle elle-même était une liste NOIRE
(`bucket == "cvs"`). Tout ce qui n'était pas un CV partait en public-read — les
pièces jointes d'appel d'offres, et les documents de conformité des partenaires,
c'est-à-dire des attestations de vigilance URSSAF et des KBIS. Corrigé en liste
BLANCHE : un bucket ajouté demain naît privé.

Lecture par `ast`, sans import : le script tire boto3 et la configuration
Supabase, dont l'absence mettrait le test en skip précisément là où on voudrait
qu'il parle.
"""
import ast
import pathlib
import re

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


def test_la_regle_dacl_a_une_seule_source():
    """Le script IMPORTE la règle, il ne la recopie pas.

    C'est la correction de fond du défaut d'origine : le script portait sa
    propre copie de la règle, et les deux ont divergé — le code d'exécution
    gardait les CV privés, le script écrivait « public-read » sur tout. Chacun
    était cohérent avec lui-même, c'est pour ça que l'écart ne se voyait pas.
    """
    src_script = SCRIPT.read_text(encoding="utf-8")
    assert "from services.storage import PUBLIC_BUCKETS" in src_script, (
        "Le script de migration ne tire plus la règle d'ACL de services/storage.py. "
        "Une seconde définition finira par diverger de la première."
    )
    assert not re.search(r"^PRIVATE_BUCKETS\s*=", src_script, re.M), (
        "Une liste locale est réapparue dans le script : c'est exactement le "
        "montage qui avait laissé passer les CV en public-read."
    )


def test_les_buckets_publics_sont_une_liste_blanche():
    """Un bucket ajouté demain doit naître PRIVÉ.

    L'ancien test était `bucket == "cvs"` : tout ce qui n'était pas un CV
    partait en public-read, y compris les pièces jointes d'appel d'offres et les
    attestations de vigilance URSSAF des partenaires. Une liste noire protège ce
    qu'on a pensé à y mettre ; une liste blanche protège tout le reste.
    """
    src = SERVICE.read_text(encoding="utf-8")
    assert re.search(r"^PUBLIC_BUCKETS\s*=\s*\{", src, re.M), (
        "services/storage.py ne déclare plus de liste blanche PUBLIC_BUCKETS."
    )
    bloc = re.search(r"PUBLIC_BUCKETS\s*=\s*\{([^}]*)\}", src).group(1)
    for interdit in ("cvs", "compliance", "ao-sources"):
        assert f'"{interdit}"' not in bloc, (
            f"« {interdit} » est passé dans les buckets PUBLICS. "
            f"Ce sont des CV, des attestations URSSAF ou des pièces jointes d'AO : "
            f"ils ne s'ouvrent que par URL signée."
        )
    # Sur le CODE seul : le commentaire qui explique ce que la liste blanche
    # remplace cite forcément l'ancien test, et un test qui échoue sur sa propre
    # documentation apprend surtout à ne plus rien documenter.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'bucket == "cvs"' not in code, (
        "Le test par liste noire est réapparu dans le code de services/storage.py."
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
    for attendu in ("cvs", "avatars", "ao-sources", "compliance", "email-assets"):
        assert attendu in buckets, (
            f"Le bucket « {attendu} » ne serait pas migré : ses objets resteraient "
            f"sur Supabase et leurs liens mourraient à la fermeture du projet — "
            f"c'est-à-dire APRÈS la bascule, quand plus personne ne regarde."
        )
