"""
Les contrôles doivent dire vrai — surtout quand ils disent « rouge ».

POURQUOI CE FICHIER EXISTE

Le 9 septembre 2026, la bascule vers le VPS a réussi : la plateforme servait ses
CV et ses avatars depuis le disque, sur la base locale. `post_bascule_check.sh`
a pourtant annoncé HUIT contrôles rouges. Six étaient FAUX :

  * trois parce qu'il ne pouvait pas lire /etc/uti-backup.env (0600 root) et
    annonçait l'échec au lieu de « non vérifié » — dont « la clé S3 du VPS PEUT
    supprimer », une faille affirmée sans avoir jamais été testée ;
  * un parce que sa garde anti-gabarit cherchait « REMPLACER » dans TOUT le
    fichier d'unité, sans distinction de casse, et tombait sur le commentaire
    « Remplacer par la sortie de age-keygen » — ce contrôle ne pouvait donc
    JAMAIS être vert ;
  * deux parce qu'il cherchait sur dix minutes des marqueurs écrits UNE FOIS au
    démarrage, dont l'un appartient à une boucle délibérément silencieuse.

Un rouge faux coûte plus cher qu'un contrôle absent : il envoie corriger un
problème qui n'existe pas, et il apprend à ignorer les rouges suivants. Ces
tests exécutent les gardes RÉELLES, extraites des scripts.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
CONTROLE = RACINE / "scripts" / "post_bascule_check.sh"
BASCULE = RACINE / "scripts" / "bascule.sh"
SAUVEGARDE = RACINE / "deploy" / "backup_db.sh"


def _bash(corps: str, *args: str) -> str:
    out = subprocess.run(["bash", "-c", corps, "_", *args],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


# ── La garde anti-gabarit de la clé de chiffrement ──────────────────────────
def _garde_age() -> str:
    texte = CONTROLE.read_text()
    m = re.search(r'^_age_ligne=.*?^esac', texte, re.S | re.M)
    assert m, "la garde AGE_RECIPIENT n'a pas la forme attendue (case…esac)"
    # On ne garde que le `case`, en injectant la valeur à éprouver — et on
    # fournit les fonctions ok/ko que la garde appelle, sans quoi bash sort en
    # 127 et le test échouerait pour la mauvaise raison.
    corps = re.sub(r'^_age_ligne=.*?\n(?=case )', '_age_ligne="$1"\n',
                   m.group(0), flags=re.S)
    return 'ok() { echo "✓"; }\nko() { echo "✗"; }\n' + corps


@pytest.mark.parametrize("ligne, doit_etre_vert", [
    ("Environment=AGE_RECIPIENT=age1k0zm2p0lhejl8xtla2tvx5nxngqave780tsccw03svpe9s6yqy6sgckpqf", True),
    ("Environment=AGE_RECIPIENT=age1REMPLACER_PAR_LA_CLE_PUBLIQUE_PRODUITE_PAR_setup.sh", False),
    ("", False),
    ("Environment=AGE_RECIPIENT=coucou", False),
])
def test_la_garde_age_juge_sa_ligne_et_pas_le_fichier(ligne, doit_etre_vert):
    sortie = _bash(_garde_age(), ligne)
    vert = "✓" in sortie
    assert vert is doit_etre_vert, f"{ligne!r} → {sortie!r}"


def test_la_garde_age_ne_lit_plus_le_fichier_entier():
    """Le défaut d'origine : `! grep -qi REMPLACER <unité>` attrapait le commentaire.

    L'unité PORTE ce mot, deux lignes au-dessus de la variable :
    « Remplacer par la sortie de `age-keygen` ». Insensible à la casse et non
    ancrée, la garde ne pouvait jamais passer.
    """
    unite = (RACINE / "deploy" / "uti-backup.service").read_text()
    assert re.search(r'remplacer', unite, re.I), (
        "l'unité ne contient plus le mot qui piégeait la garde — le test perd "
        "son sens, vérifier que la garde reste ancrée sur sa ligne"
    )
    texte = CONTROLE.read_text()
    assert "grep -qi 'REMPLACER'" not in texte, (
        "la garde cherche de nouveau le gabarit dans TOUT le fichier d'unité"
    )


# ── Le répertoire courant des heredocs python ──────────────────────────────
def test_le_controle_se_place_dans_backend():
    """config.py résout `env_file: ".env"` relativement au répertoire COURANT.

    Lancé depuis ~/app, chaque heredoc python échouait sur « supabase_url Field
    required », et le script concluait « un comportement PostgREST diffère ».
    """
    texte = CONTROLE.read_text()
    i_cd = texte.find('cd "$BACKEND"')
    assert i_cd != -1, "post_bascule_check.sh ne se place pas dans $BACKEND"
    i_py = texte.find('venv/bin/python')
    assert i_cd < i_py, "le `cd` doit précéder le premier appel python"


# ── « Non vérifié » n'est pas « en échec » ─────────────────────────────────
def test_un_controle_qui_na_pas_pu_tourner_ne_dit_pas_echec():
    texte = CONTROLE.read_text()
    assert re.search(r'^nv\(\)', texte, re.M), (
        "aucun état « non vérifié » : un contrôle empêché se déclare en échec"
    )
    assert "-r /etc/uti-backup.env" in texte, (
        "le fichier de secrets est testé avec -f (existence) et non -r "
        "(lisibilité) : trois contrôles se déclareront rouges sans rien tester"
    )


# ── Les marqueurs de démarrage ─────────────────────────────────────────────
@pytest.mark.parametrize("marqueur", ["SCHED", "OUTBOX"])
def test_les_marqueurs_de_boucle_ne_sont_pas_cherches_sur_dix_minutes(marqueur):
    """Ils sont écrits UNE FOIS au démarrage ; l'un d'eux appartient même à une
    boucle qui n'écrit rien quand la file est vide (services/scheduler.py)."""
    texte = CONTROLE.read_text()
    for ligne in texte.splitlines():
        if f"[{marqueur}]" in ligne and "--since" in ligne:
            assert '"-10 min"' not in ligne, (
                f"[{marqueur}] est encore cherché sur une fenêtre de 10 minutes"
            )
    assert "ActiveEnterTimestamp" in texte, (
        "la fenêtre n'est pas ancrée sur le démarrage réel du service"
    )


# ── Les préalables de la bascule ───────────────────────────────────────────
def test_letape_0_verifie_que_le_repertoire_des_fichiers_est_inscriptible():
    """L'étape 3 mourait sur « Permission denied » APRÈS la sauvegarde, l'archive
    et deux exports — /var/lib appartient à root."""
    texte = BASCULE.read_text()
    assert '[ -w "$FICHIERS" ]' in texte, (
        "l'étape 0 ne vérifie pas que le répertoire des fichiers est inscriptible"
    )


def test_letape_9_eprouve_la_cle_de_service_contre_postgrest():
    """SUPABASE_SERVICE_KEY est signée par Supabase ; PostgREST local la rejette.

    Le backend démarre, /health est vert, et tout répond 401 — panne totale et
    muette. Le script l'annonçait en commentaire sans jamais le vérifier.
    """
    texte = BASCULE.read_text()
    assert "make_service_key.py" in texte, (
        "l'étape 9 ne mentionne pas l'outil qui signe la clé locale"
    )
    i9 = texte.find('etape 9 "Bascule des trois lignes de .env"')
    i10 = texte.find('etape 10 "Redémarrage du backend')
    assert 0 < i9 < i10, "ancres des étapes 9 et 10 introuvables"
    bloc = texte[i9:i10]
    assert "rest/v1/profiles" in bloc, (
        "la clé de service n'est pas éprouvée contre PostgREST avant le redémarrage"
    )


def test_letape_11_ne_bloque_plus_sur_des_criteres_de_suppression():
    """post_bascule_check.sh vérifie les 12 critères de SUPPRESSION de Supabase.

    Aucun n'est censé être vert le jour de la bascule. En faire une porte
    terminait en rouge une bascule réussie.
    """
    texte = BASCULE.read_text()
    i11 = texte.find('etape 11 "Contrôle de bascule"')
    assert i11 > 0, "ancre de l'étape 11 introuvable"
    bloc = texte[i11:]
    assert "post_bascule_check.sh" in bloc
    # Seulement le CODE : le commentaire qui explique le retrait cite forcément
    # « mort 11 », et le chercher partout ferait échouer le test sur sa propre
    # justification.
    code = [l for l in bloc.splitlines() if not l.lstrip().startswith("#")]
    assert not any("mort 11" in l for l in code), (
        "l'étape 11 arrête encore la bascule sur des critères de suppression"
    )


# ── Le volume du dump ──────────────────────────────────────────────────────
def test_la_sauvegarde_refuse_un_dump_qui_perd_la_moitie_de_son_volume():
    """Le plancher de 50 Ko laissait passer 79 Ko de schéma sans données.

    Constaté le 9 septembre : sauvegarde parfaitement verte d'une base vide.
    """
    texte = SAUVEGARDE.read_text()
    m = re.search(r'precedent=\$\(ls.*?^fi', texte, re.S | re.M)
    assert m, "backup_db.sh ne compare pas avec la sauvegarde précédente"

    garde = '''
verdict() {
  taille=$1; taille_prec=$2
  if [ "$taille_prec" -gt 51200 ] && [ "$taille" -lt $(( taille_prec / 2 )) ]
  then echo REFUSE; else echo ACCEPTE; fi
}
verdict "$1" "$2"'''
    assert _bash(garde, "79000", "293000") == "REFUSE"     # le cas réel
    assert _bash(garde, "293000", "291000") == "ACCEPTE"   # stable
    assert _bash(garde, "340000", "293000") == "ACCEPTE"   # croissance
    assert _bash(garde, "176000", "293000") == "ACCEPTE"   # purge RGPD légitime
