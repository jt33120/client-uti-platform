#!/usr/bin/env python3
"""
Compare le schéma RÉEL d'une base au schéma que les fichiers SQL du repo savent
reconstruire. Répond à une seule question, mais elle est décisive :

    « Si je perds cette base, est-ce que le repo suffit à la recréer ? »

COMMENT ÇA MARCHE

Le script rejoue, sur une base jetable, tout le SQL versionné dans l'ordre :

    supabase_schema.sql → supabase_migration_*.sql → backend/migrations/0*.sql

puis compare table par table et colonne par colonne avec la base de référence.
Deux passes sur les migrations : les fichiers `supabase_migration_*.sql` sont
joués par ordre alphabétique, qui n'est pas l'ordre de dépendance — certains
ALTER portent sur une table créée par un fichier situé plus loin dans l'alphabet.
La seconde passe les rattrape.

CE QU'IL TROUVE

Une colonne ajoutée à la main en production et jamais écrite dans une migration.
C'est invisible tant que la base tourne, et ça ne se paie qu'au moment où l'on
reconstruit ailleurs : environnement de test, reprise après sinistre, ou
changement d'hébergeur. La fonctionnalité qui en dépend tombe alors sans erreur
visible — c'est exactement ce qui était arrivé à la cartographie et à la purge
RGPD avant la migration 0016.

PRÉREQUIS

  * `psql` et un serveur PostgreSQL accessible pour la base jetable ;
  * `psycopg2` ou `psycopg` pour lire la base de référence.

USAGE

    cd backend
    python scripts/check_schema_drift.py \\
        --live "postgresql://user:pass@hote:5432/postgres"

    # base jetable ailleurs que sur le Postgres local :
    python scripts/check_schema_drift.py --live "$DSN" --scratch "$DSN_TEST"

Code de sortie : 0 si aucune colonne de la base de référence ne manque au repo,
1 sinon. Utilisable tel quel dans un contrôle avant bascule.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

COLS_SQL = """
select table_name || '|' || column_name
from information_schema.columns
where table_schema = 'public'
order by 1
"""


def _connect(dsn):
    try:
        import psycopg  # psycopg 3
        return psycopg.connect(dsn)
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2.connect(dsn)
    except ImportError:
        sys.exit("Il faut psycopg (v3) ou psycopg2 pour lire la base de référence.")


def read_columns(dsn: str) -> set[str]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(COLS_SQL)
        return {r[0] for r in cur.fetchall()}


def _with_dbname(dsn: str, dbname: str) -> str:
    """Même DSN, autre base. Ne remplace QUE le chemin.

    Un découpage naïf sur « / » perdrait la query string, où vivent souvent
    `host=` et `port=` quand on passe par une socket Unix — et la reconstruction
    se ferait alors silencieusement sur la mauvaise instance, ce qui rendrait
    l'outil pire qu'inutile : il signalerait une dérive massive et fausse.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(dsn)
    if not parts.scheme:  # forme clé=valeur : « host=... dbname=... »
        garde = [kv for kv in dsn.split() if not kv.startswith("dbname=")]
        return " ".join(garde + [f"dbname={dbname}"])
    return urlunsplit(parts._replace(path="/" + dbname))


SCHEMA = REPO / "backend" / "migrations" / "schema.sql"


def sql_files() -> list[Path]:
    """Le SQL à rejouer pour reconstruire le schéma.

    Depuis la consolidation, c'est UN fichier. Auparavant il fallait rejouer
    ~46 fichiers dans un ordre que personne n'avait écrit, puis recommencer une
    seconde fois pour rattraper les dépendances que l'ordre alphabétique
    inversait — deux migrations altèrent `ao_consultant_state`, créée par un
    fichier situé plus loin dans l'alphabet. Une reconstruction qui a besoin
    d'être jouée deux fois pour être juste n'est pas une reconstruction fiable.

    Les anciens fichiers restent dans le dépôt pour l'historique ; ils ne sont
    plus la source de vérité et ne sont plus rejoués ici.
    """
    if not SCHEMA.exists():
        sys.exit(f"Fichier de schéma introuvable : {SCHEMA}")
    return [SCHEMA]


def rebuild(scratch_dsn: str, dbname: str) -> set[str]:
    """Reconstruit le schéma dans une base jetable et renvoie ses colonnes."""
    base = subprocess.run(
        ["psql", scratch_dsn, "-tAc", "select 1"],
        capture_output=True, text=True,
    )
    if base.returncode != 0:
        sys.exit(f"Base jetable injoignable : {base.stderr.strip()}")

    run = lambda dsn, *a: subprocess.run(["psql", dsn, "-q", *a],
                                         capture_output=True, text=True)
    run(scratch_dsn, "-c", f'drop database if exists "{dbname}"')
    run(scratch_dsn, "-c", f'create database "{dbname}"')
    target = _with_dbname(scratch_dsn, dbname)

    # ON_ERROR_STOP=1, désormais : schema.sql est censé passer intégralement.
    # Auparavant on tolérait les erreurs — c'était nécessaire (les fichiers
    # référençaient auth.users et storage.buckets, absents d'un Postgres nu),
    # mais ça masquait aussi les vraies. Une erreur ici doit maintenant faire
    # échouer bruyamment : un schéma qui ne se rejoue pas d'un bloc n'est pas
    # une sauvegarde de la structure, c'est une illusion de sauvegarde.
    for path in sql_files():
        res = subprocess.run(["psql", target, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(path)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"Échec du rejeu de {path.name} :\n{res.stderr.strip()}")

    cols = subprocess.run(["psql", target, "-tAc", COLS_SQL], capture_output=True, text=True)
    return {l.strip() for l in cols.stdout.splitlines() if l.strip()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", default=os.environ.get("LIVE_DSN"),
                   help="DSN de la base de référence (ou variable LIVE_DSN)")
    p.add_argument("--scratch", default=os.environ.get("SCRATCH_DSN", "postgresql:///postgres"),
                   help="DSN d'un Postgres où créer la base jetable")
    p.add_argument("--keep", action="store_true", help="ne pas supprimer la base jetable")
    args = p.parse_args()
    if not args.live:
        p.error("--live est requis (ou la variable d'environnement LIVE_DSN)")

    dbname = "schema_drift_" + Path(tempfile.mktemp()).name[-8:]
    print("Reconstruction du schéma depuis les fichiers SQL du repo…")
    repo_cols = rebuild(args.scratch, dbname)
    print(f"  {len({c.split('|')[0] for c in repo_cols})} tables, {len(repo_cols)} colonnes")

    print("Lecture de la base de référence…")
    live_cols = read_columns(args.live)
    print(f"  {len({c.split('|')[0] for c in live_cols})} tables, {len(live_cols)} colonnes")

    live_t, repo_t = {c.split("|")[0] for c in live_cols}, {c.split("|")[0] for c in repo_cols}
    tables_manquantes = sorted(live_t - repo_t)
    # On ne signale que le sens qui fait perdre des données : une colonne
    # présente dans le repo mais pas en base est au pire du code mort.
    cols_manquantes = sorted(c for c in live_cols - repo_cols if c.split("|")[0] in repo_t)

    if not args.keep:
        subprocess.run(["psql", args.scratch, "-q", "-c", f'drop database if exists "{dbname}"'],
                       capture_output=True, text=True)

    print()
    if not tables_manquantes and not cols_manquantes:
        print("✅ Aucune dérive : le repo sait recréer tout ce que la base contient.")
        return 0

    print("❌ DÉRIVE DÉTECTÉE — ces objets existent en base et dans AUCUN fichier SQL du repo :")
    for t in tables_manquantes:
        print(f"   table    {t}")
    for c in cols_manquantes:
        table, col = c.split("|")
        print(f"   colonne  {table}.{col}")
    print("\nÉcris une migration dans backend/migrations/ avant toute reconstruction,")
    print("sinon ces colonnes disparaîtront et le code qui les lit échouera en silence.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
