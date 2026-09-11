"""
La revue hebdomadaire doit dire vrai — y compris quand elle ne peut pas savoir.

POURQUOI CE FICHIER EXISTE

Un contrôle qui tourne sans personne devant est une promesse : « si quelque
chose dérive, tu le sauras ». La seule façon de tenir cette promesse est de
vérifier que le contrôle PARLE dans les cas où il devrait parler. Ce dépôt a
déjà payé deux fois le défaut inverse — un `psql` muet compté comme « aucun
réglage manquant », un listage plat qui ne pouvait trouver aucun CV et
annonçait « aucune fuite ». Dans les deux cas, le contrôle était vert parce
qu'il n'avait rien mesuré.

Ces tests n'INSPECTENT donc pas le texte du script : ils en EXTRAIENT les blocs
réels et les EXÉCUTENT contre des bouchons. Un contrôle qu'on relit peut
mentir ; un contrôle qu'on exécute avec une base injoignable, un verrou retiré
ou un fichier manquant ne le peut pas.

LA RÈGLE QUE CHAQUE TEST DÉFEND
    Aucun chemin d'exécution ne produit « ok » sans avoir mesuré quelque chose.
    Ce qui n'a pas pu être mesuré rend « nv » (gris) — ou « ko » quand le
    silence lui-même masquerait un danger.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DEPLOY = BACKEND / "deploy"
REVUE = DEPLOY / "revue_hebdo.sh"
UNITE = DEPLOY / "uti-revue-hebdo.service"
MINUTEUR = DEPLOY / "uti-revue-hebdo.timer"

SOURCE = REVUE.read_text()


# ═══════════════════════════════════════════════════════════════════════════
#  Outillage : extraire un bloc réel, l'exécuter avec des bouchons
# ═══════════════════════════════════════════════════════════════════════════
# On découpe sur les séparateurs « ═══ » du script, qui bornent chaque section.
# Extraire par numéro plutôt que recopier le code est ce qui fait que ces tests
# suivent le script : si quelqu'un réécrit la section 2, c'est SA version qui
# est éprouvée, pas une copie devenue fausse.
def section(numero: int) -> str:
    depart = SOURCE.index(f'titre "{numero}.')
    suite = SOURCE.find("\n# ═══", depart)
    return SOURCE[depart: suite if suite != -1 else len(SOURCE)]


# Les bouchons impriment un préfixe stable au lieu de compter : on peut ainsi
# affirmer « cette exécution n'a produit AUCUN vert », ce qu'un total ne dit pas.
PREAMBULE = """
set -uo pipefail
VERT=0; ROUGE=0; GRIS=0; RAPPORT=""
_ajout()  { RAPPORT+="$1"$'\\n'; }
titre()   { :; }
ok()      { VERT=$((VERT+1));   echo "OK $1"; _ajout "OK $1"; }
ko()      { ROUGE=$((ROUGE+1)); echo "ROUGE $1"; _ajout "ROUGE $1"; }
nv()      { GRIS=$((GRIS+1));   echo "NV $1"; _ajout "NV $1"; }
detail()  { echo "   $1"; _ajout "   $1"; }
bloc()    { local l; while IFS= read -r l; do [ -n "$l" ] || continue; echo "   $l"; _ajout "   $l"; done <<< "${1:-}"; }
"""


def joue(corps: str, env: dict | None = None) -> str:
    """Exécute un bloc bash avec les bouchons, rend sa sortie."""
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}
    e.update(env or {})
    res = subprocess.run(["bash", "-c", PREAMBULE + corps],
                         capture_output=True, text=True, env=e)
    return res.stdout


def compte(sortie: str, prefixe: str) -> int:
    """Nombre de lignes de verdict — et pas `sortie.count(prefixe)`.

    Le piège a déjà été posé : les DONNÉES d'un contrôle peuvent contenir le mot
    « OK », et un test qui compte des occurrences dans tout le texte se met
    alors à mesurer le contenu au lieu du verdict.
    """
    return sum(1 for l in sortie.splitlines() if l.startswith(prefixe + " "))


def faux_python(racine: Path, sortie: str, code: int = 0) -> None:
    """Un faux `venv/bin/python` qui rend ce qu'on veut, sans lire son entrée.

    Le bloc lui envoie son programme par un heredoc ; l'ignorer est exactement
    ce qu'il faut, puisqu'on éprouve ici le bash qui INTERPRÈTE la réponse.
    """
    binaire = racine / "venv" / "bin"
    binaire.mkdir(parents=True, exist_ok=True)
    faux = binaire / "python"
    faux.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s' \"$STUB_SORTIE\"\n"
                    f"exit {code}\n")
    faux.chmod(faux.stat().st_mode | stat.S_IEXEC)


# ═══════════════════════════════════════════════════════════════════════════
#  §1 — Cohérence entre la base et le disque
# ═══════════════════════════════════════════════════════════════════════════
def joue_coherence(sortie: str, code: int = 0, tmp: Path | None = None) -> str:
    assert tmp is not None
    faux_python(tmp, sortie, code)
    return joue(section(1), {
        "BACKEND": str(tmp), "FICHIERS": str(tmp / "files"),
        "ORPHELINS_MAX": "20", "STUB_SORTIE": sortie,
    })


def test_un_inventaire_complet_et_sain_est_vert(tmp_path):
    s = joue_coherence("ATTENDUS=42\nPRESENTS=42\nMANQUANTS=0\nORPHELINS=0\n", tmp=tmp_path)
    assert compte(s, "ROUGE") == 0, s
    assert compte(s, "OK") == 2, s


def test_une_reference_sans_fichier_est_rouge_et_nommee(tmp_path):
    """Le cas qui coûte le plus cher : la base affiche un CV que personne ne
    peut ouvrir. Le rapport doit dire LESQUELS — un compte sans les chemins
    oblige à refaire l'enquête à la main."""
    s = joue_coherence(
        "ATTENDUS=42\nPRESENTS=41\nMANQUANTS=1\nORPHELINS=0\n"
        "DETAIL=submissions/abc → cvs/ao-1/xyz.pdf\n", tmp=tmp_path)
    assert compte(s, "ROUGE") == 1, s
    assert "cvs/ao-1/xyz.pdf" in s, "le chemin manquant n'apparaît pas dans le rapport"


def test_le_detail_des_manquants_survit_jusqua_le_mail(tmp_path):
    """`cmd | while read` exécute la boucle dans un sous-shell : les ajouts au
    rapport y meurent. L'écran montrerait les chemins, l'e-mail arriverait
    amputé — et c'est l'e-mail qu'on lit le dimanche."""
    s = joue_coherence(
        "ATTENDUS=9\nPRESENTS=8\nMANQUANTS=1\nORPHELINS=0\n"
        "DETAIL=submissions/abc → cvs/ao-1/xyz.pdf\n",
        tmp=tmp_path) + joue(  # on relit $RAPPORT après coup
        section(1) + '\nprintf "RAPPORT_FINAL:%s" "$RAPPORT"\n',
        {"BACKEND": str(tmp_path), "FICHIERS": str(tmp_path / "files"),
         "ORPHELINS_MAX": "20",
         "STUB_SORTIE": "ATTENDUS=9\nPRESENTS=8\nMANQUANTS=1\nORPHELINS=0\n"
                        "DETAIL=submissions/abc → cvs/ao-1/xyz.pdf\n"})
    apres = s.split("RAPPORT_FINAL:", 1)[1]
    assert "cvs/ao-1/xyz.pdf" in apres, (
        "le détail n'est pas dans $RAPPORT : il a été produit dans un sous-shell"
    )


def test_une_base_muette_nest_pas_une_base_coherente(tmp_path):
    """Le défaut fondateur de ce fichier : ne RIEN pouvoir lire ne prouve pas
    que tout va bien."""
    s = joue_coherence("", code=1, tmp=tmp_path)
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") == 1, s
    assert "PAS PU" in s


def test_une_table_refusee_suspend_le_compte_des_orphelins(tmp_path):
    """Un inventaire partiel ferait passer des CV parfaitement référencés pour
    des fichiers à supprimer. On préfère ne pas compter que compter faux."""
    s = joue_coherence(
        "ATTENDUS=10\nPRESENTS=99\nMANQUANTS=0\nORPHELINS=-1\n"
        "REFUSEE=consultants.cv_url: APIError: relation absente\n", tmp=tmp_path)
    assert compte(s, "NV") == 1, s
    assert "consultants.cv_url" in s
    assert "ROUGE 89" not in s, "des orphelins ont été comptés sur un inventaire partiel"


def test_des_orphelins_au_dela_du_seuil_sont_rouges(tmp_path):
    s = joue_coherence("ATTENDUS=10\nPRESENTS=99\nMANQUANTS=0\nORPHELINS=89\n", tmp=tmp_path)
    assert compte(s, "ROUGE") == 1, s
    assert "RGPD" in s, "le motif du rouge (conservation sans finalité) a disparu"


# ═══════════════════════════════════════════════════════════════════════════
#  §2 — Immuabilité de la dernière archive hors-site
# ═══════════════════════════════════════════════════════════════════════════
#  C'est le contrôle qui porte la décision de septembre 2026 : garder la clé
#  Backblaze actuelle. Cette clé porte `bypassGovernance` et `deleteFiles` ; ce
#  qui protège les sauvegardes n'est donc pas la clé, c'est le verrou d'objet
#  en mode COMPLIANCE. Or ce verrou tient à un réglage de conteneur qu'un clic
#  retire, sans rien casser et sans rien annoncer. Ces tests-là sont la
#  contrepartie de la décision.
def joue_verrou(sortie: str, code: int = 0, *, tmp: Path, marqueur: str | None =
                "2026-09-09T13:08:56+00:00 uti/2026/09/uti-x.pgcustom.age 303478") -> str:
    faux_python(tmp, sortie, code)
    dest = tmp / "backups"
    dest.mkdir(exist_ok=True)
    if marqueur is not None:
        (dest / ".dernier_succes").write_text(marqueur + "\n")
    return joue(section(2), {
        "BACKEND": str(tmp), "DEST": str(dest), "VERROU_JOURS_MIN": "3",
        "BACKUP_S3_BUCKET": "uti-sauvegardes", "STUB_SORTIE": sortie,
    })


VERROU_SAIN = ("TAILLE=303478\nTAILLE_ATTENDUE=303478\nMODE=COMPLIANCE\n"
               "JUSQUA=2026-09-23T13:08:55+00:00\nRESTE_JOURS=12.0\n")


def test_un_verrou_compliance_valide_est_vert(tmp_path):
    s = joue_verrou(VERROU_SAIN, tmp=tmp_path)
    assert compte(s, "ROUGE") == 0, s
    assert compte(s, "OK") == 2, s      # la taille, puis le verrou


def test_un_verrou_retire_est_rouge(tmp_path):
    """Le scénario exact qu'on surveille : la rétention par défaut du conteneur
    a été retirée. Rien ne casse, les dépôts continuent, et les archives
    redeviennent effaçables par la clé du VPS."""
    s = joue_verrou(VERROU_SAIN.replace("MODE=COMPLIANCE", "MODE="), tmp=tmp_path)
    assert compte(s, "ROUGE") >= 1, s
    assert "AUCUN verrou" in s


def test_le_mode_governance_ne_passe_pas_pour_une_protection(tmp_path):
    """Piège réel : l'interface Backblaze propose « governance » par défaut, et
    la clé du VPS porte `bypassGovernance`. Un verrou que la clé surveillée peut
    lever elle-même est un verrou décoratif."""
    s = joue_verrou(VERROU_SAIN.replace("COMPLIANCE", "GOVERNANCE"), tmp=tmp_path)
    assert compte(s, "ROUGE") >= 1, s
    assert "bypassGovernance" in s


def test_un_verrou_qui_expire_bientot_est_rouge(tmp_path):
    """Une rétention raccourcie de 14 jours à 1 jour laisse le mode COMPLIANCE
    affiché et ne protège plus rien d'une semaine sur l'autre."""
    s = joue_verrou(VERROU_SAIN.replace("RESTE_JOURS=12.0", "RESTE_JOURS=0.8"), tmp=tmp_path)
    assert compte(s, "ROUGE") >= 1, s
    assert "expire dans" in s


def test_une_archive_tronquee_est_rouge(tmp_path):
    s = joue_verrou(VERROU_SAIN.replace("TAILLE=303478", "TAILLE=1024"), tmp=tmp_path)
    assert compte(s, "ROUGE") >= 1, s
    assert "tronqué" in s


def test_une_archive_absente_hors_site_est_rouge(tmp_path):
    """Le dépôt peut échouer APRÈS l'écriture du marqueur local : la sauvegarde
    se croit réussie, et il n'y a rien chez le tiers."""
    s = joue_verrou("ABSENTE: ClientError: 404 Not Found", code=1, tmp=tmp_path)
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") >= 1, s


def test_un_controle_dimmuabilite_muet_nest_jamais_vert(tmp_path):
    s = joue_verrou("", code=1, tmp=tmp_path)
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") >= 1, s


def test_sans_les_variables_s3_le_controle_se_declare_rouge(tmp_path):
    """Gris serait plus doux et plus faux : ce qu'on ne prouve pas ici, c'est la
    survie des sauvegardes."""
    faux_python(tmp_path, "")
    s = joue(section(2), {"BACKEND": str(tmp_path), "DEST": str(tmp_path),
                          "VERROU_JOURS_MIN": "3", "STUB_SORTIE": ""})
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") == 1, s


def test_sans_sauvegarde_reussie_il_ny_a_rien_a_verifier_et_cest_rouge(tmp_path):
    s = joue_verrou(VERROU_SAIN, tmp=tmp_path, marqueur=None)
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") == 1, s


# ═══════════════════════════════════════════════════════════════════════════
#  §5 — Minuteurs
# ═══════════════════════════════════════════════════════════════════════════
def joue_minuteurs(etats: dict[str, tuple[str, str]]) -> str:
    """`etats` : minuteur -> (is-enabled, is-active). Absent = inconnu."""
    cas = "\n".join(
        f'    {nom}) [ "$1" = "is-enabled" ] && echo "{e}"; [ "$1" = "is-active" ] && echo "{a}" ;;'
        for nom, (e, a) in etats.items())
    bouchon = f"""
systemctl() {{
  case "$1" in
    list-units) return 0 ;;
    show) echo "dim. 2026-09-13 07:30:00 UTC" ; return 0 ;;
  esac
  case "$2" in
{cas}
    *) return 1 ;;
  esac
}}
"""
    return joue(bouchon + section(5))


TOUS_ARMES = {n: ("enabled", "active") for n in (
    "uti-backup.timer", "uti-restore-drill.timer",
    "uti-supervision.timer", "uti-revue-hebdo.timer")}


def test_quatre_minuteurs_armes_font_quatre_verts():
    s = joue_minuteurs(TOUS_ARMES)
    assert compte(s, "ROUGE") == 0, s
    assert compte(s, "OK") == 5, s      # 4 minuteurs + « aucune unité en échec »


def test_un_minuteur_absent_est_rouge_et_pas_silencieux():
    """`systemctl list-timers` ne montre QUE les minuteurs actifs : un minuteur
    désinstallé y est invisible. C'est pour ça qu'on les nomme un par un."""
    etats = dict(TOUS_ARMES)
    del etats["uti-backup.timer"]
    s = joue_minuteurs(etats)
    assert compte(s, "ROUGE") == 1, s
    assert "uti-backup.timer" in s and "JAMAIS" in s


def test_un_minuteur_desactive_est_rouge():
    etats = dict(TOUS_ARMES)
    etats["uti-supervision.timer"] = ("disabled", "inactive")
    s = joue_minuteurs(etats)
    assert compte(s, "ROUGE") == 1, s
    assert "uti-supervision.timer" in s


def _joue_unites_en_echec(sortie_list_units: str) -> str:
    """Éprouve le seul contrôle des unités en échec, sans les minuteurs."""
    bouchon = ("systemctl() { [ \"$1\" = \"list-units\" ] && "
               "{ printf '%s' \"$STUB_ECHECS\"; return 0; }; return 1; }\n")
    return joue(bouchon + section(5), {"STUB_ECHECS": sortie_list_units})


def test_la_revue_ne_compte_pas_sa_propre_defaillance_de_la_semaine_passee():
    """Le défaut apparu à la première installation sur le VPS.

    Le service sort en 1 dès qu'un point est rouge — c'est ainsi qu'il apparaît
    dans `systemctl list-units --failed`. systemd le laisse donc en « failed »
    jusqu'à son exécution suivante : toute la semaine. Sans filtre, la revue du
    dimanche suivant compterait sa propre défaillance comme une anomalie — un
    rouge qui n'apprend rien, puisque ses motifs sont déjà détaillés section par
    section, et qui s'auto-entretient : un rouge en produit un autre, semaine
    après semaine, sans jamais pouvoir se refermer.
    """
    s = _joue_unites_en_echec(
        "uti-revue-hebdo.service loaded failed failed Revue hebdomadaire\n")
    assert "OK aucune unité en échec" in s, s


def test_une_autre_unite_en_echec_reste_rouge():
    """Le filtre ne doit porter que sur soi : c'est une exception nommée, pas
    un assouplissement du contrôle."""
    s = _joue_unites_en_echec(
        "uti-revue-hebdo.service loaded failed failed Revue hebdomadaire\n"
        "postgresql.service loaded failed failed PostgreSQL\n")
    assert compte(s, "ROUGE") >= 1, s
    verdict = next(l for l in s.splitlines() if l.startswith("ROUGE unité"))
    assert "postgresql.service" in verdict, verdict
    assert "uti-revue-hebdo.service" not in verdict, verdict


def test_la_supervision_en_echec_reste_rouge():
    """Elle, on la garde : elle tourne toutes les 15 minutes, donc son état
    reflète sa dernière exécution — une information d'aujourd'hui, pas l'écho
    d'une semaine passée."""
    s = _joue_unites_en_echec(
        "uti-supervision.service loaded failed failed Supervision\n")
    assert compte(s, "ROUGE") >= 1, s
    assert "uti-supervision.service" in s


def test_un_systemd_injoignable_nest_pas_une_machine_sans_panne():
    """Le défaut que ce script a RÉELLEMENT produit, en essai, avant correction.

    `command -v systemctl` ne prouve que la présence du binaire. Dans un
    conteneur, ou avec un bus inaccessible, il écrit « Failed to connect to
    bus » sur stderr et rend 1 : la liste des unités en échec revient vide, et
    le contrôle annonçait « ✓ aucune unité en échec » — un vert tiré d'un
    silence, exactement la faute que tout ce dépôt traque."""
    bouchon = ('systemctl() { echo "Failed to connect to bus" >&2; return 1; }\n')
    s = joue(bouchon + section(5))
    assert "OK aucune unité en échec" not in s, s
    assert compte(s, "ROUGE") >= 1, s


def test_un_apt_en_panne_ne_compte_pas_zero_correctif():
    """Même faute, même remède : un `apt-get` qui échoue (verrou pris, listes
    non initialisées) produisait « 0 correctif de sécurité en attente » sur une
    machine dont on ne savait rien."""
    bouchon = ('apt-get() { echo "E: Could not get lock" >&2; return 100; }\n')
    s = joue(bouchon + section(4))
    assert "OK aucun correctif" not in s, s
    assert compte(s, "ROUGE") >= 1, s


def test_un_apt_qui_repond_compte_vraiment():
    bouchon = ("apt-get() { printf '%s\\n' "
               "'Inst libc6 [2.36] (2.37 Debian-Security:12/stable [amd64])' "
               "'Inst curl [7.88] (7.89 Debian:12/stable [amd64])'; }\n")
    s = joue(bouchon + section(4))
    assert compte(s, "ROUGE") == 1, s
    assert "1 correctif" in s, s


def test_la_revue_surveille_son_propre_minuteur():
    """Un contrôle qui ne se surveille pas lui-même se tait le jour où il meurt."""
    assert "uti-revue-hebdo.timer" in section(5), (
        "la revue ne vérifie pas que SON minuteur est armé : le jour où il "
        "saute, plus rien ne le dit"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  §6 — Erreurs applicatives de la semaine
# ═══════════════════════════════════════════════════════════════════════════
def joue_journal(contenu: str) -> str:
    bouchon = "journalctl() { printf '%s' \"$STUB_JOURNAL\"; }\n"
    return joue(bouchon + section(6),
                {"ERREURS_SEMAINE_MAX": "100", "STUB_JOURNAL": contenu})


def test_un_journal_vide_est_rouge_pas_vert():
    """Zéro erreur en sept jours ne veut pas dire « rien ne casse » : ça veut
    dire que le service est arrêté, ou que journald est volatil. Compter ce
    silence comme une semaine sans incident est le mensonge le plus facile à
    écrire de tout ce script."""
    s = joue_journal("")
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") == 1, s
    assert "volatil" in s or "arrêté" in s


def test_un_journal_sain_compte_zero_erreur():
    s = joue_journal("INFO démarrage\nINFO requête servie\n")
    assert compte(s, "ROUGE") == 0, s
    assert compte(s, "OK") == 1, s


def test_les_erreurs_sont_comptees_et_classees_par_source():
    jrn = "\n".join(
        f'UTI_EVT {{"ts": "2026-09-1{i}", "level": "error", "source": "llm.extraction", "message": "x"}}'
        for i in range(5))
    s = joue_journal(jrn)
    assert compte(s, "ROUGE") == 0, s      # 5 < seuil
    assert "5 erreur" in s, s
    assert "llm.extraction" in s, "le classement par source ne remonte pas"


def test_au_dela_du_seuil_les_erreurs_deviennent_un_rouge():
    jrn = "\n".join(
        f'UTI_EVT {{"ts": "t{i}", "level": "error", "source": "smtp", "message": "x"}}'
        for i in range(150))
    s = joue_journal(jrn)
    assert compte(s, "ROUGE") == 1, s


def test_un_avertissement_nest_pas_une_erreur():
    """`level` distingue la panne du repli maîtrisé. Les confondre ferait crier
    la revue sur des fonctionnements nominaux, et on cesserait de la lire."""
    jrn = "\n".join(
        f'UTI_EVT {{"ts": "t{i}", "level": "warning", "source": "ai_budget", "message": "x"}}'
        for i in range(150))
    s = joue_journal(jrn)
    assert compte(s, "ROUGE") == 0, s
    assert "0 erreur" in s, s


# ═══════════════════════════════════════════════════════════════════════════
#  §9 — Permissions des fichiers sensibles
# ═══════════════════════════════════════════════════════════════════════════
def joue_droits(chemin: Path, acceptables: str = "600 400") -> str:
    m = re.search(r"^verifier_droits\(\) \{.*?^\}", section(9), re.S | re.M)
    assert m, "la fonction verifier_droits n'a plus la forme attendue"
    return joue(m.group(0) + f'\nverifier_droits "{chemin}" "{acceptables}" 600\n')


def test_un_secret_en_600_est_vert(tmp_path):
    f = tmp_path / "secret.env"; f.write_text("x"); f.chmod(0o600)
    assert compte(joue_droits(f), "OK") == 1


def test_un_secret_lisible_par_tous_est_rouge(tmp_path):
    f = tmp_path / "secret.env"; f.write_text("x"); f.chmod(0o644)
    s = joue_droits(f)
    assert compte(s, "ROUGE") == 1, s


def test_un_mode_plus_ouvert_mais_numeriquement_plus_petit_est_rouge(tmp_path):
    """Le piège que ce contrôle a failli poser : un mode est de l'OCTAL, pas un
    nombre. 060 (groupe en lecture-écriture, donc PLUS ouvert que 600) est
    numériquement INFÉRIEUR à 600 ; une comparaison « ≤ 600 » l'aurait donc
    déclaré « plus restrictif que demandé » et peint en vert un secret partagé
    avec tout un groupe."""
    f = tmp_path / "secret.env"; f.write_text("x"); f.chmod(0o060)
    s = joue_droits(f)
    assert compte(s, "OK") == 0, s
    assert compte(s, "ROUGE") == 1, s


def test_un_fichier_absent_est_gris_pas_rouge(tmp_path):
    """Tous les VPS n'ont pas tous les fichiers d'environnement. Crier sur une
    absence légitime, c'est fabriquer le bruit qui fera ignorer le vrai rouge."""
    s = joue_droits(tmp_path / "jamais-cree.env")
    assert compte(s, "NV") == 1, s
    assert compte(s, "ROUGE") == 0, s


# ═══════════════════════════════════════════════════════════════════════════
#  §11 — La suite de tests rejouée sur la machine de production
# ═══════════════════════════════════════════════════════════════════════════
def joue_tests(code: int | None, resume: str = "42 passed") -> tuple[str, Path]:
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    if code is not None:
        b = tmp / "venv" / "bin"; b.mkdir(parents=True)
        p = b / "pytest"
        p.write_text(f"#!/bin/sh\necho '{resume}'\nexit {code}\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return joue(section(11), {"BACKEND": str(tmp), "REVUE_TESTS": "1"}), tmp


def test_une_suite_qui_passe_sur_la_prod_est_verte():
    s, _ = joue_tests(0)
    assert compte(s, "OK") == 1, s


def test_une_suite_qui_echoue_sur_la_prod_est_rouge():
    """C'est le contrôle qui attrape la dérive de venv : la CI prouve que le
    code du dépôt passe ses tests sur une machine neuve, pas que la machine qui
    sert les clients passe les siens."""
    s, _ = joue_tests(1, resume="3 failed, 39 passed")
    assert compte(s, "ROUGE") == 1, s
    assert "3 failed" in s, "le résumé de pytest n'est pas remonté dans le rapport"


def test_une_suite_bloquee_est_rouge_et_dite_bloquee():
    s, _ = joue_tests(124)
    assert compte(s, "ROUGE") == 1, s
    assert "bloquée" in s


def test_sans_pytest_le_controle_est_gris_et_dit_comment_lactiver():
    """Gris, pas vert : ne pas rejouer les tests n'est pas les avoir réussis."""
    s, _ = joue_tests(None)
    assert compte(s, "OK") == 0, s
    assert compte(s, "NV") == 1, s
    assert "pip install pytest" in s


def test_le_saut_explicite_reste_gris():
    s = joue(section(11), {"BACKEND": "/inexistant", "REVUE_TESTS": "0"})
    assert compte(s, "NV") == 1, s
    assert compte(s, "OK") == 0, s


# ═══════════════════════════════════════════════════════════════════════════
#  Le rapport et le verdict
# ═══════════════════════════════════════════════════════════════════════════
def joue_verdict(rouge: int) -> str:
    m = re.search(r'^resume="\$VERT vert.*', SOURCE, re.S | re.M)
    assert m, "le bloc de verdict n'a plus la forme attendue"
    # `ping_garde` rend ici un code NON NUL — et c'est tout l'intérêt du
    # bouchon. Avec un bouchon qui réussit, la forme « test && a || b » se
    # comporte exactement comme un if/else et le test ne prouve rien : c'est ce
    # qu'il faisait avant, et il validait la version fautive.
    bouchons = ('courriel() { echo "COURRIEL_SUJET=$1"; echo "COURRIEL_CORPS=$2"; }\n'
                'ping_garde() { echo "PING=${1:-vert}"; return 1; }\n'
                f'VERT=3; GRIS=1; ROUGE={rouge}; REVUE_EMAIL=1\n'
                'RAPPORT="corps du rapport"\n')
    res = subprocess.run(["bash", "-c", "set -uo pipefail\n" + bouchons + m.group(0)],
                         capture_output=True, text=True,
                         env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    return res.stdout + f"\nCODE={res.returncode}"


def test_le_rapport_part_meme_quand_tout_est_vert():
    """La raison d'être de ce script. Une alerte ne part que sur anomalie : son
    silence est donc ambigu — « rien à signaler » et « le dispositif est mort »
    se ressemblent. Le rapport hebdomadaire lève l'ambiguïté, et il ne la lève
    que s'il part AUSSI les semaines où tout va bien."""
    s = joue_verdict(0)
    assert "COURRIEL_SUJET=" in s, "aucun rapport envoyé une semaine verte"
    assert "corps du rapport" in s
    assert "CODE=0" in s


def test_une_revue_rouge_sort_en_erreur_et_le_dit_dans_le_sujet():
    s = joue_verdict(2)
    assert "CODE=1" in s, "l'unité systemd n'apparaîtrait pas en échec"
    assert "2 point" in s, s


def test_le_chien_de_garde_nest_pinge_au_vert_quune_seule_fois():
    """« test && a || b » exécute AUSSI `b` si `a` rend un code non nul : on
    enverrait le signal d'échec juste après le signal de succès."""
    s = joue_verdict(0)
    assert s.count("PING=") == 1, f"deux pings envoyés pour une seule revue :\n{s}"
    assert "PING=/fail" not in s


# ═══════════════════════════════════════════════════════════════════════════
#  Les unités systemd
# ═══════════════════════════════════════════════════════════════════════════
def test_lunite_neutralise_la_sonde_de_la_sauvegarde():
    """LE piège de cette unité, et il est silencieux.

    La revue lit /etc/uti-backup.env pour y prendre les clés du conteneur
    hors-site. Ce fichier contient AUSSI HEALTHCHECK_URL — la sonde DE LA
    SAUVEGARDE. Sans neutralisation, une revue verte le dimanche pingerait la
    sonde de la sauvegarde, et le chien de garde externe croirait la sauvegarde
    vivante alors qu'elle serait morte depuis six jours. Le dispositif tout
    entier repose sur le fait que chaque sonde ne parle que d'elle-même.

    L'ordre est ce qui fait la correction : systemd applique les directives dans
    l'ordre du fichier."""
    texte = UNITE.read_text()
    i_backup = texte.index("EnvironmentFile=/etc/uti-backup.env")
    i_neutre = texte.index("Environment=HEALTHCHECK_URL=\n")
    i_propre = texte.index("EnvironmentFile=-/etc/uti-revue-hebdo.env")
    assert i_backup < i_neutre < i_propre, (
        "la neutralisation de HEALTHCHECK_URL n'est plus entre les deux fichiers "
        "d'environnement : la revue pingerait la sonde de la sauvegarde"
    )


def test_la_sonde_propre_est_facultative_mais_celle_de_la_sauvegarde_non():
    """Le « - » sur le fichier de la revue (facultatif) et son absence sur celui
    de la sauvegarde (obligatoire) : sans les clés S3, la revue ne peut pas
    vérifier l'immuabilité, et doit échouer au démarrage plutôt que tourner en
    annonçant un contrôle qu'elle n'a pas fait."""
    texte = UNITE.read_text()
    assert "EnvironmentFile=/etc/uti-backup.env" in texte
    assert "EnvironmentFile=-/etc/uti-revue-hebdo.env" in texte


def test_la_revue_ne_tourne_pas_en_root():
    texte = UNITE.read_text()
    assert "User=julian.talou" in texte, (
        "en root, l'authentification « peer » de PostgreSQL ne mappe aucun rôle "
        "(pg_ident.conf) et toutes les requêtes de la revue échoueraient"
    )
    assert "NoNewPrivileges=true" in texte
    assert "ProtectSystem=full" in texte


def test_le_minuteur_tombe_bien_le_week_end():
    texte = MINUTEUR.read_text()
    assert re.search(r"^OnCalendar=(Sun|Sat) ", texte, re.M), (
        "la revue ne se déclenche plus le week-end"
    )
    assert "Persistent=true" in texte, (
        "sans Persistent, un VPS éteint le dimanche fait sauter la revue de la "
        "semaine — or c'est après un incident qu'on veut vérifier ce qui a dérivé"
    )


def test_lunite_pointe_le_binaire_installe_et_pas_le_depot():
    """~/app est remplacé à chaque déploiement : y pointer ferait dépendre un
    contrôle de production de l'état d'un `git pull`."""
    texte = UNITE.read_text()
    assert "ExecStart=/usr/local/bin/uti-revue-hebdo" in texte
    assert "ExecStart=/home" not in texte


def test_le_script_tient_dans_le_delai_de_lunite():
    """TimeoutStartSec doit couvrir la borne que le script s'impose à lui-même
    sur la suite de tests (`timeout 900`), sinon systemd tue la revue avant
    qu'elle ait pu conclure — et l'incident serait imputé à la revue."""
    borne = int(re.search(r"timeout (\d+) ", SOURCE).group(1))
    limite = int(re.search(r"TimeoutStartSec=(\d+)", UNITE.read_text()).group(1))
    assert limite > borne, (
        f"TimeoutStartSec={limite} s ne laisse pas de marge sur les {borne} s "
        f"que le script accorde à pytest"
    )


def test_le_script_est_syntaxiquement_valide():
    """Une revue qui ne démarre pas ne crie pas : elle ne fait rien, et son
    silence ressemble à une semaine sans incident."""
    res = subprocess.run(["bash", "-n", str(REVUE)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("numero", range(1, 12))
def test_chaque_section_existe_et_rend_un_verdict(numero):
    """Garde-fou de structure : une section supprimée par accident ne laisserait
    aucune trace dans le rapport — il serait simplement plus court."""
    corps = section(numero)
    assert re.search(r"\b(ok|ko|nv|detail) ", corps), (
        f"la section {numero} ne produit aucune ligne de verdict"
    )
