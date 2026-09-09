"""
L'URI de connexion Supabase : le découpage manuel doit décoder les %XX.

POURQUOI CE TEST EXISTE

`bascule.sh` et `export_supabase_archive.sh` ne passent plus l'URI en argument à
psql (elle porterait le mot de passe dans `ps`) : ils la découpent en variables
libpq. Mais libpq, lui, DÉCODE les séquences percent quand on lui donne une URI
entière — et le découpage manuel ne le faisait pas.

Conséquence mesurée en production le 9 septembre 2026 : la console Supabase
distribue un mot de passe encodé (29 caractères, 23 une fois décodé), la bascule
s'arrêtait sur « impossible de joindre Supabase », et le message de libpq disait
« password authentication failed » — ce qui envoie chercher un mot de passe faux
là où il y a un problème d'analyse d'URI. Une demi-heure de diagnostic.

Ces tests exécutent la VRAIE fonction, extraite des scripts, pas une copie.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPTS = [RACINE / "scripts" / "bascule.sh",
           RACINE / "scripts" / "export_supabase_archive.sh"]


def _extraire_decode(script: Path) -> str:
    """Le corps de _decode_pct tel qu'il est écrit dans le script."""
    texte = script.read_text()
    m = re.search(r"^_decode_pct\(\) \{.*?^\}", texte, re.S | re.M)
    assert m, f"_decode_pct introuvable dans {script.name}"
    return m.group(0)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize(
    "encode, attendu",
    [
        ("MotDePasseSimple123", "MotDePasseSimple123"),   # rien à décoder
        ("p%40ss", "p@ss"),                               # @ — le cas réel
        ("a%3Ab", "a:b"),                                 # : casserait le découpage
        ("a%2Fb", "a/b"),                                 # / aussi
        ("a%3Fb", "a?b"),
        ("a%23b", "a#b"),
        ("a%25b", "a%b"),                                 # % littéral
        ("x%40y%3Az%2Fw", "x@y:z/w"),                     # plusieurs
        (r"anti\slash", r"anti\slash"),                   # printf %b ne doit PAS l'interpréter
    ],
)
def test_le_decodage_reproduit_celui_de_libpq(script, encode, attendu):
    corps = _extraire_decode(script)
    out = subprocess.run(
        ["bash", "-c", f'{corps}\n_decode_pct "$1"', "_", encode],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout == attendu, (
        f"{script.name} : {encode!r} devrait donner {attendu!r}, "
        f"pas {out.stdout!r}"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_le_decodage_est_bien_applique_au_mot_de_passe(script):
    """Vérifier que la fonction existe ne suffit pas : elle doit être APPELÉE.

    Le défaut d'origine n'était pas une fonction fausse — il n'y avait pas de
    fonction du tout. Un correctif qui définirait _decode_pct sans l'appeler
    passerait le test précédent et laisserait la panne intacte.
    """
    texte = script.read_text()
    lignes = [l for l in texte.splitlines()
              if "PGPASSWORD" in l and "=" in l and not l.lstrip().startswith("#")]
    affectations = [l for l in lignes if re.search(r"PGPASSWORD\w*=", l)]
    assert affectations, f"aucune affectation de PGPASSWORD dans {script.name}"
    assert any("_decode_pct" in l for l in affectations), (
        f"{script.name} : PGPASSWORD est affecté sans passer par _decode_pct — "
        f"un mot de passe encodé (%40, %3A…) serait refusé. Lignes vues : {affectations}"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_l_uri_ne_passe_jamais_en_argument(script):
    """Contre-épreuve du correctif précédent : le mot de passe hors de `ps`.

    `psql "$PGURI"` inscrit l'URI dans /proc/<pid>/cmdline, mode 0444, lisible
    par tout compte de la machine. C'est ce que l'en-tête de
    export_supabase_archive.sh promet de ne jamais faire.
    """
    for i, ligne in enumerate(script.read_text().splitlines(), 1):
        nu = ligne.lstrip()
        if nu.startswith("#"):
            continue
        assert not re.search(r"\b(psql|pg_dump|pg_restore)\s+[\"']?\$\{?(PGURI|uri|_uri)\b", nu), (
            f"{script.name}:{i} passe l'URI en argument — le mot de passe "
            f"apparaîtrait dans ps : {nu}"
        )
