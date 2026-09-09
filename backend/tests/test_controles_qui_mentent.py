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


# ── Le vert tiré d'un silence ──────────────────────────────────────────────
# Le 9 septembre, la section 3 appelait psql DEUX fois : une fois derrière un
# pipe (sous-shell, où les ROUGE de ko() étaient perdus), puis une seconde fois
# pour recompter avec `grep -cE 'MANQUANT|INERTE' || true`. Or psql qui ne peut
# pas se CONNECTER sort non nul en n'écrivant rien : grep comptait 0, `|| true`
# avalait le code, et le script concluait « aucun réglage manquant ». La panne
# la plus grave que ce bloc puisse rencontrer était la seule qu'il taisait.
def _bloc_seed() -> str:
    texte = CONTROLE.read_text()
    m = re.search(r'^if seed_sortie=.*?^fi$', texte, re.S | re.M)
    assert m, "le bloc de vérification du seed n'a plus la forme attendue"
    return (
        'ROUGE=0\n'
        'ok() { echo "OK $1"; }\n'
        'ko() { echo "KO $1"; ROUGE=$((ROUGE+1)); }\n'
        'nv() { echo "NV $1"; }\n'
        'BACKEND=/inexistant\n'
        # psql de substitution : sortie et code de retour pilotés par le test.
        'psql() { [ -n "$STUB_SORTIE" ] && printf %s\\\\n "$STUB_SORTIE"; return "$STUB_CODE"; }\n'
        + m.group(0)
        + '\necho "ROUGE=$ROUGE"\n'
    )


def _joue_seed(sortie: str, code: int) -> tuple[str, int]:
    out = subprocess.run(
        ["bash", "-c", _bloc_seed()],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "STUB_SORTIE": sortie, "STUB_CODE": str(code)},
    )
    texte = out.stdout
    m = re.search(r'ROUGE=(\d+)', texte)
    assert m, f"le bloc n'a pas terminé : {out.stdout!r} {out.stderr!r}"
    return texte, int(m.group(1))


def _lignes(texte: str, prefixe: str) -> int:
    return sum(1 for l in texte.splitlines() if l.startswith(prefixe + " "))


SEED_VERT = (
    "app_settings/notifications      OK\n"
    "app_settings/data_retention     OK (purge désactivée — décision assumée)\n"
    "app_settings/ai_budget          OK (20.0 $/sem, 60.0 $/mois)\n"
    "scoring_config                  OK (1 ligne)"
)


def test_seed_complet_ne_produit_aucun_rouge():
    texte, rouge = _joue_seed(SEED_VERT, 0)
    assert rouge == 0, texte
    # Les lignes ÉMISES par le stub, pas les « OK » du contenu : la sortie de
    # verify_seed.sql porte elle-même « OK (purge désactivée…) ».
    assert _lignes(texte, "OK") == 4, texte


def test_seed_incomplet_compte_chaque_manquant():
    """Le comptage doit survivre à la disparition du sous-shell.

    L'ancien montage lisait les lignes derrière un pipe : `ko` s'exécutait dans
    un sous-shell et son incrément de ROUGE mourait avec lui. D'où le second
    appel à psql, qui portait le vrai défaut.
    """
    sortie = SEED_VERT.replace("app_settings/ai_budget          OK (20.0 $/sem, 60.0 $/mois)",
                               "app_settings/ai_budget          MANQUANT")
    sortie = sortie.replace("app_settings/notifications      OK",
                            "app_settings/notifications      INERTE (plafonds à 0)")
    texte, rouge = _joue_seed(sortie, 0)
    assert rouge == 2, texte


@pytest.mark.parametrize("sortie, code", [
    ("", 2),                                        # connexion refusée
    ("psql: error: Peer authentication failed", 2),  # le cas root, précisément
    ("", 0),                                        # muet mais « réussi »
])
def test_une_base_qui_ne_repond_pas_est_un_rouge_pas_un_vert(sortie, code):
    """C'EST LE TEST QUI COMPTE. ROUGE=0 vaut « Supabase peut être supprimé »."""
    texte, rouge = _joue_seed(sortie, code)
    assert rouge >= 1, (
        f"psql sortie={sortie!r} code={code} n'a produit aucun rouge : le script "
        f"conclurait « réglages vérifiés » sans avoir lu la base.\n{texte}"
    )
    assert _lignes(texte, "OK") == 0, f"des réglages sont déclarés conformes : {texte!r}"


def test_le_recomptage_par_grep_a_disparu():
    texte = CONTROLE.read_text()
    assert "grep -cE 'MANQUANT|INERTE'" not in texte, (
        "le recomptage est de retour : `|| true` avale le code de psql et un "
        "échec de connexion se compte 0"
    )
    assert texte.count('-f "$BACKEND/migrations/verify_seed.sql"') == 1, (
        "verify_seed.sql est de nouveau interrogé deux fois ; les deux appels "
        "peuvent diverger et le second masque les pannes de connexion"
    )


# ── La consigne de secours ne doit pas casser ce qu'elle répare ────────────
def test_on_ne_conseille_plus_de_relancer_le_controle_en_root():
    """`sudo -E $0` rendait trois contrôles au hors-site et en cassait sept.

    pg_hba est en « peer map=uti » : c'est le compte UNIX appelant qui choisit
    le rôle PostgreSQL, et pg_ident.conf ne mappe que julian.talou et postgrest.
    Sous root, les deux psql de la section 3 échouent.
    """
    # Hors commentaires : la ligne qui met en garde CONTRE `sudo -E $0` le cite,
    # et une recherche sur le fichier entier se piégerait sur son propre avis.
    code = "\n".join(l for l in CONTROLE.read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "sudo -E $0" not in code, (
        "le script conseille de nouveau de se relancer en root"
    )
    texte = CONTROLE.read_text()
    assert "sudo -v" in texte, "la manœuvre de remplacement a disparu"

    ident = (RACINE / "deploy" / "install_db.sh").read_text()
    m = re.search(r'cat > "\$\{PG_CONF_DIR\}/pg_ident\.conf".*?\nEOF', ident, re.S)
    assert m, "pg_ident.conf n'est plus écrit par install_db.sh"
    assert not re.search(r'^uti\s+"?root"?', m.group(0), re.M), (
        "root est désormais mappé dans pg_ident.conf : la consigne « relancer "
        "en root » redeviendrait valide, revoir ce test et le message du script"
    )


# ── Deux contrôles de fuite qui n'ont jamais tourné ────────────────────────
# La section 4 tirait la clé d'un CV de `storage.list("cvs", "")`. En mode
# local, ce listage ne rend que les fichiers posés DIRECTEMENT dans cvs/, or un
# CV s'écrit en cvs/<ao_id>/<uuid>.pdf : il ne voyait que des répertoires. Le
# script annonçait donc « aucun CV en stockage » — en vert — sur une plateforme
# qui en servait 31, et les deux contrôles de fuite étaient sautés.
def test_les_cv_vivent_dans_des_sous_repertoires():
    """Les deux moitiés de la panne, chacune vérifiable dans le dépôt."""
    routeur = (RACINE / "routers" / "submissions.py").read_text()
    assert 'storage_path = f"{ao_id}/{submission_uuid}' in routeur, (
        "les CV ne sont plus rangés par appel d'offres — si le stockage est "
        "redevenu plat, relire pourquoi ce contrôle interrogeait la base"
    )
    storage_py = (RACINE / "services" / "storage.py").read_text()
    m = re.search(r'^def list\(bucket.*?(?=^def |\Z)', storage_py, re.S | re.M)
    assert m and "e.is_file()" in m.group(0), (
        "storage.list ne filtre plus sur e.is_file() : vérifier s'il descend "
        "désormais dans les sous-répertoires"
    )


def test_la_cle_du_cv_ne_vient_plus_dun_listage_plat():
    texte = CONTROLE.read_text()
    assert 'storage.list("cvs"' not in texte, (
        "la clé du CV repasse par un listage qui ne peut structurellement rien "
        "trouver ; les contrôles de fuite redeviendraient décoratifs"
    )
    assert 'db.table("submissions").select("cv_url")' in texte, (
        "la clé du CV ne vient plus de la base"
    )
    assert "ok \"aucun CV en stockage" not in texte, (
        "l'absence de CV est de nouveau annoncée en VERT alors qu'elle veut "
        "dire « rien n'a été éprouvé »"
    )


def _bloc_fuite_locale() -> str:
    texte = CONTROLE.read_text()
    m = re.search(r'^    if \[ -z "\$BASE" \]; then\n.*?^    fi$', texte, re.S | re.M)
    assert m, "le bloc de contrôle des fuites locales n'a plus la forme attendue"
    return (
        'ok() { echo "OK $1"; }\n'
        'ko() { echo "KO $1"; }\n'
        'nv() { echo "NV $1"; }\n'
        'BASE="https://exemple.test"\n'
        'CLE_CV="$CLE_STUB"\n'
        # curl de substitution : un code par route, pilotés séparément.
        'curl() { case "$*" in\n'
        '  */files/public/*) echo "$CODE_PUBLIC" ;;\n'
        '  */files/d/*)      echo "$CODE_JETON" ;;\n'
        'esac; }\n'
        + m.group(0)
    )


def _joue_fuite(cle: str, code_public: str, code_jeton: str) -> str:
    out = subprocess.run(
        ["bash", "-c", _bloc_fuite_locale()],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "CLE_STUB": cle,
             "CODE_PUBLIC": code_public, "CODE_JETON": code_jeton},
    )
    assert out.returncode == 0, f"{out.stdout!r} {out.stderr!r}"
    return out.stdout


def test_sans_cv_le_controle_du_jeton_tourne_quand_meme():
    """LE TEST QUI COMPTE. Le refus d'un jeton invalide ne dépend d'aucun CV,
    et il était pourtant enfermé dans la branche « on a trouvé un CV »."""
    sortie = _joue_fuite("", "", "403")
    assert _lignes(sortie, "NV") == 1, sortie
    assert "jeton invalide est refusé" in sortie, (
        f"le contrôle du jeton est encore sauté faute de CV :\n{sortie}"
    )
    assert _lignes(sortie, "KO") == 0, sortie


def test_sans_cv_un_jeton_accepte_reste_rouge():
    sortie = _joue_fuite("", "", "200")
    assert _lignes(sortie, "KO") == 1, sortie
    assert "la signature des URLs ne protège rien" in sortie, sortie


def test_avec_un_cv_les_deux_controles_tournent():
    sortie = _joue_fuite("42/abc.pdf", "404", "403")
    assert _lignes(sortie, "OK") == 2, sortie
    assert _lignes(sortie, "NV") == 0, sortie
    assert _lignes(sortie, "KO") == 0, sortie


def test_un_cv_servi_publiquement_est_rouge():
    sortie = _joue_fuite("42/abc.pdf", "200", "403")
    assert _lignes(sortie, "KO") == 1, sortie
    assert "est traité comme public" in sortie, sortie


# ── Une promesse écrite dans la documentation, tenue par le code ───────────
def test_le_depot_hors_site_ne_supprime_jamais():
    """`backup_s3_policy.README.md` affirme que retirer la capacité
    `deleteFiles` de la clé Backblaze ne casse aucune sauvegarde. Cette
    affirmation n'est vraie que tant que s3_backup.py ne supprime rien — le
    jour où elle cesserait de l'être, les dépôts échoueraient en production
    et la documentation expliquerait pourquoi c'est impossible.
    """
    code = (RACINE / "deploy" / "s3_backup.py").read_text()
    for appel in ("delete_object", "delete_objects", "delete_bucket"):
        assert appel not in code, (
            f"s3_backup.py appelle {appel} : la clé B2 aurait besoin de "
            f"`deleteFiles`, ce que backup_s3_policy.README.md interdit. "
            f"Trancher, et mettre les deux d'accord."
        )

    readme = (RACINE / "deploy" / "backup_s3_policy.README.md").read_text()
    assert "listBuckets,listFiles,readFiles,writeFiles" in readme, (
        "la liste des capacités B2 a bougé sans que ce test le sache"
    )
    assert "deleteFiles" in readme, "le README ne nomme plus la capacité refusée"


def test_le_readme_ne_promet_pas_quune_liste_de_capacites_protege():
    """La nuance qu'une relecture « simplificatrice » ferait sauter en premier.

    B2 : « writeFiles is necessary when you delete a file by name, deleteFiles
    is required when you delete a specific version. » Comme writeFiles est
    indispensable pour DÉPOSER, aucune liste de capacités ne laisse la clé
    écrire sans la laisser supprimer par nom. Une première version de ce
    fichier affirmait le contraire ; le jour où quelqu'un raccourcira ce
    paragraphe, la fausse garantie reviendra.
    """
    readme = (RACINE / "deploy" / "backup_s3_policy.README.md").read_text()
    assert "NE SUFFIT PAS" in readme, (
        "le README ne dit plus que la liste de capacités est insuffisante"
    )
    assert "delete a file by name" in readme, (
        "la citation Backblaze qui fonde cette limite a disparu : sans elle, "
        "le paragraphe redevient une opinion qu'on peut supprimer"
    )
    assert "existing bucket" in readme, (
        "le README ne dit plus que le verrou d'objet s'active sur un conteneur "
        "EXISTANT chez B2 — c'est la règle OVH qui avait été transposée à tort"
    )


def test_le_controle_de_suppression_supprime_encore_par_nom():
    """Tant que c'est vrai, le README doit continuer à prévenir que ce contrôle
    ne mesure pas ce qu'il prétend. Le jour où il testera la survie de la
    version, ce test tombera — et c'est le signal pour retirer l'avertissement.
    """
    texte = CONTROLE.read_text()
    if "delete_object(Bucket=b, Key=k)" in texte and "VersionId" not in texte:
        readme = (RACINE / "deploy" / "backup_s3_policy.README.md").read_text()
        assert "sans numéro de\nversion" in readme or "sans numéro de version" in readme, (
            "le contrôle supprime toujours par nom, mais le README ne prévient "
            "plus que son verdict est trompeur sur un conteneur verrouillé"
        )
