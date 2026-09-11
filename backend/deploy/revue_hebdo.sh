#!/usr/bin/env bash
# =============================================================================
#  La revue du dimanche — ce qui se dégrade trop lentement pour être vu.
#
#  POURQUOI UN TROISIÈME CONTRÔLE
#  Il en existe déjà deux, et ils ne se remplacent pas :
#    • supervision.sh tourne toutes les 15 minutes. Il répond à « est-ce que ça
#      marche MAINTENANT ». Ses sondes doivent donc être rapides et gratuites :
#      pas question d'y compter des fichiers ou d'y interroger un tiers.
#    • post_bascule_check.sh est un contrôle qu'on LANCE, à la main, pour prouver
#      un état à un instant choisi. Personne ne le lance le dimanche.
#  Entre les deux, il reste une classe entière de pannes : celles qui ne cassent
#  rien aujourd'hui et rendent la plateforme inutilisable dans trois mois. Un
#  certificat qui n'est plus renouvelé, une file d'e-mails qui ne se vide plus,
#  un verrou d'immuabilité retiré d'un clic, des CV référencés en base dont le
#  fichier a disparu. Aucune de ces pannes ne déclenche quoi que ce soit : elles
#  se voient le jour où on en a besoin, c'est-à-dire trop tard.
#
#  POURQUOI LE DIMANCHE, ET PAS TOUTES LES HEURES
#  Ces contrôles coûtent : ils lisent tout le disque, interrogent Backblaze,
#  rejouent la suite de tests. À cadence rapide ils useraient la machine pour
#  observer des grandeurs qui bougent en semaines. Et surtout : une alerte qui
#  arrive 96 fois par jour n'est plus lue. Une fois par semaine, un dimanche
#  matin, la revue a le temps d'être lue vraiment.
#
#  CE SCRIPT ENVOIE UN E-MAIL À CHAQUE EXÉCUTION, MÊME TOUT VERT.
#  C'est délibéré, et c'est le seul endroit du dispositif où on le fait. Les
#  alertes ne partent que sur anomalie : leur silence est donc ambigu — « rien à
#  signaler » et « le dispositif est mort » se ressemblent. Le rapport hebdo
#  lève cette ambiguïté : s'il n'arrive pas dimanche, c'est LUI qui est cassé.
#  Un accusé de bonne santé qu'on reçoit est une preuve ; un silence n'en est
#  jamais une.
#
#  TROIS ÉTATS, PAS DEUX
#      ok  (vert)  le contrôle a tourné et il est satisfait
#      ko  (rouge) le contrôle a tourné et il a trouvé quelque chose
#      nv  (gris)  le contrôle n'a PAS PU tourner — ce n'est pas un échec, et
#                  surtout pas un succès. Un contrôle muet qui compte comme vert
#                  est la panne la plus coûteuse du dispositif : il a déjà
#                  annoncé « aucun réglage manquant » depuis une base injoignable
#                  (cf. tests/test_controles_qui_mentent.py).
#  Le gris ne fait pas échouer la revue, SAUF là où le silence masquerait un
#  danger : ces cas-là sont marqués « ko » et le commentaire dit pourquoi.
#
#  USAGE
#      bash ~/app/backend/deploy/revue_hebdo.sh      # ou uti-revue-hebdo.timer
#      echo $?     # 0 = rien à signaler, 1 = au moins un rouge
#
#      REVUE_TESTS=0  bash revue_hebdo.sh    # sauter la suite de tests (longue)
#      REVUE_EMAIL=0  bash revue_hebdo.sh    # n'envoyer aucun rapport
# =============================================================================
set -uo pipefail

BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
DEST="${BACKUP_DIR:-/var/backups/uti}"
FICHIERS="${FILES_DIR:-/var/lib/uti/files}"
BASE="${PGDATABASE:-uti}"
API="${API_URL:-http://127.0.0.1:8000}"
DOMAINE="${DOMAINE_PUBLIC:-}"     # vide = on le déduit de PUBLIC_BASE_URL

# Même raison que dans supervision.sh et backup_db.sh : l'authentification est
# « peer » avec correspondance (install_db.sh:263-268). Sans PGUSER, libpq
# demande le rôle « julian.talou », qui n'existe pas.
export PGUSER="${PGUSER:-uti_admin}"

# ── Seuils ──────────────────────────────────────────────────────────────────
# En clair et en haut : ce sont les seuls chiffres qu'on aura envie de changer,
# et les enfouir dans le corps du script revient à les figer.
TLS_JOURS_MIN="${TLS_JOURS_MIN:-21}"        # certbot renouvelle à 30 j : 21 = il a déjà raté 3 fois
OUTBOX_BLOQUEE_H="${OUTBOX_BLOQUEE_H:-6}"   # un e-mail en file depuis 6 h ne partira plus tout seul
VACUUM_JOURS_MAX="${VACUUM_JOURS_MAX:-14}"  # table jamais analysée depuis 2 semaines = plans faux
ERREURS_SEMAINE_MAX="${ERREURS_SEMAINE_MAX:-100}"  # au-delà, ce n'est plus du bruit
VERROU_JOURS_MIN="${VERROU_JOURS_MIN:-3}"   # marge avant expiration du verrou d'immuabilité
ORPHELINS_MAX="${ORPHELINS_MAX:-20}"        # fichiers sans référence tolérés (purge RGPD en retard)
REVUE_TESTS="${REVUE_TESTS:-1}"
REVUE_EMAIL="${REVUE_EMAIL:-1}"

# shellcheck source=./lib_alerte.sh disable=SC1091
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib_alerte.sh" 2>/dev/null \
  || . /usr/local/lib/uti-lib_alerte.sh

VERT=0; ROUGE=0; GRIS=0
RAPPORT=""

# Chaque ligne part deux fois : à l'écran (avec des couleurs, pour la lecture
# manuelle) et dans $RAPPORT (sans, pour l'e-mail). Les écrire deux fois à la
# main, c'est la garantie qu'un jour l'une des deux dira autre chose que l'autre.
_ajout() { RAPPORT+="$1"$'\n'; }

titre()  { printf '\n\033[1m── %s\033[0m\n' "$1"; _ajout ""; _ajout "── $1"; }
ok()     { VERT=$((VERT+1));   printf '  \033[32m✓\033[0m %s\n' "$1"; _ajout "  OK   $1"; }
ko()     { ROUGE=$((ROUGE+1)); printf '  \033[31m✗\033[0m %s\n' "$1"; _ajout "  ROUGE $1"; }
nv()     { GRIS=$((GRIS+1));   printf '  \033[33m?\033[0m %s\n' "$1"; _ajout "  ?    $1"; }
detail() { printf '      %s\n' "$1"; _ajout "       $1"; }

# Un bloc de plusieurs lignes (sortie d'erreur, classement, extrait de journal).
# Il prend son texte en ARGUMENT et le lit par « <<< », jamais par un tube :
# `cmd | while read` exécute la boucle dans un SOUS-SHELL, où les ajouts à
# $RAPPORT meurent avec lui — l'e-mail arriverait amputé de tous les détails
# affichés à l'écran. C'est le piège qui avait déjà vidé un compteur de rouges
# dans post_bascule_check.sh (PR #219) ; on ne le repose pas ici.
bloc() {
  local ligne
  while IFS= read -r ligne; do
    [ -n "$ligne" ] || continue
    printf '       %s\n' "$ligne"
    _ajout "       $ligne"
  done <<< "${1:-}"
}

printf '\033[1mRevue hebdomadaire UTI — %s\033[0m\n' "$(date -Is)"
_ajout "Revue hebdomadaire UTI — $(date -Is)"
_ajout "Machine : $(hostname)"

# ═══════════════════════════════════════════════════════════════════════════
# 1. COHÉRENCE ENTRE LA BASE ET LE DISQUE
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : depuis la bascule, un CV est une LIGNE en base (submissions.cv_url)
# ET un FICHIER sur le disque (/var/lib/uti/files/cvs/<ao>/<uuid>). Rien ne tient
# ces deux moitiés ensemble : pas de contrainte, pas de transaction commune. Une
# restauration partielle, une purge RGPD interrompue, un `rm` malheureux, et la
# base continue d'afficher un CV que plus personne ne peut ouvrir. La plateforme
# a alors l'air parfaitement saine — jusqu'à ce qu'un client clique.
#
# Deux défauts symétriques, et ils ne pèsent pas pareil :
#   • référence SANS fichier  → panne visible par l'utilisateur = ROUGE ;
#   • fichier SANS référence  → donnée personnelle conservée sans raison, donc
#     un sujet RGPD (art. 5.1.e) plus qu'une panne = rouge seulement au-delà
#     d'un seuil, parce qu'un dépôt en cours de traitement en produit
#     légitimement quelques-uns.
#
# On passe par services/storage et services/postgrest_client — le code de
# l'application elle-même — et non par un `find` : c'est _object_path() qui sait
# défaire les URL publiques héritées de Supabase encore stockées dans certaines
# lignes. Un `find` les compterait toutes comme manquantes.
titre "1. Cohérence entre la base et le disque"
coherence=$(BACKEND_DIR="$BACKEND" FICHIERS_DIR="$FICHIERS" \
            "$BACKEND/venv/bin/python" - 2>&1 <<'PY'
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
os.chdir(os.environ["BACKEND_DIR"])          # config.py lit ./.env
from pathlib import Path
from services import storage
from services.postgrest_client import db

RACINE = Path(os.environ["FICHIERS_DIR"])
# (table, colonne, panier). clients.logo_url n'y est pas : aucun point de dépôt
# ne l'alimente aujourd'hui (seul routers/auth.py:1173 écrit dans « avatars »),
# donc l'y ajouter produirait un inventaire faux dans un sens comme dans l'autre.
SOURCES = [("submissions", "cv_url", "cvs"),
           ("consultants", "cv_url", "cvs"),
           ("profiles", "avatar_url", "avatars")]

attendus, manquants, refusees, absentes = set(), [], [], []
for table, colonne, panier in SOURCES:
    # Une table par une, chacune dans son propre essai : si « consultants »
    # n'est pas exposée par PostgREST, on doit encore pouvoir juger les deux
    # autres. Sans ça, un défaut de configuration d'UNE table ferait dire
    # « cohérence non vérifiée » alors que 90 % de l'inventaire était lisible.
    try:
        lignes = (db.table(table).select(f"id,{colonne}")
                    .not_.is_(colonne, "null").limit(10000).execute()).data or []
    except Exception as e:                                 # noqa: BLE001
        # « La colonne n'existe pas » n'est PAS « je n'ai pas pu lire ».
        # migrations/0016_schema_drift.sql documente que le schéma du dépôt et
        # la base de production ont divergé dans les deux sens : consultants.
        # cv_url figure dans schema.sql et n'a jamais existé en production.
        # Une colonne absente ne référence AUCUN fichier : l'inventaire reste
        # donc complet, et le compte des orphelins reste valable. La confondre
        # avec une lecture impossible suspendait ce compte pour toujours — un
        # contrôle éteint en silence par une divergence de schéma bénigne.
        texte = f"{getattr(e, 'code', '') or ''} {e}"
        if "42703" in texte or "42P01" in texte:      # undefined_column / undefined_table
            absentes.append(f"{table}.{colonne}")
            continue
        refusees.append(f"{table}.{colonne}: {type(e).__name__}: {str(e)[:120]}")
        continue
    for ligne in lignes:
        chemin = storage._object_path(panier, ligne[colonne])
        if not chemin:
            continue
        attendus.add(f"{panier}/{chemin}")
        if not (RACINE / panier / chemin).is_file():
            manquants.append(f"{table}/{ligne['id']} → {panier}/{chemin}")

# Les fichiers réellement présents, tous sous-répertoires confondus. storage.list
# ne peut pas servir ici : en mode local il filtre `if e.is_file()` sur UN niveau
# et ne voit donc jamais cvs/<ao>/<uuid> (le défaut corrigé en PR #220).
presents = set()
for panier in ("cvs", "avatars"):
    base = RACINE / panier
    if base.is_dir():
        presents |= {f"{panier}/{p.relative_to(base)}"
                     for p in base.rglob("*") if p.is_file()}

orphelins = sorted(presents - attendus)
print(f"ATTENDUS={len(attendus)}")
print(f"PRESENTS={len(presents)}")
print(f"MANQUANTS={len(manquants)}")
# Un fichier n'est « orphelin » que par rapport à un inventaire COMPLET. Si une
# table n'a pas pu être lue, ses fichiers apparaîtraient orphelins à tort — et
# la revue conseillerait de supprimer des CV parfaitement référencés. On refuse
# donc de compter plutôt que de compter faux.
print(f"ORPHELINS={-1 if refusees else len(orphelins)}")
for m in manquants[:10]:
    print(f"DETAIL={m}")
for o in orphelins[:10] if not refusees else []:
    print(f"ORPHELIN={o}")
for r in refusees:
    print(f"REFUSEE={r}")
for a in absentes:
    print(f"ABSENTE={a}")
PY
)
if [ $? -eq 0 ] && printf '%s' "$coherence" | grep -q '^ATTENDUS='; then
  eval "$(printf '%s' "$coherence" | grep -E '^(ATTENDUS|PRESENTS|MANQUANTS|ORPHELINS)=')"
  refusees=$(printf '%s' "$coherence" | sed -n 's/^REFUSEE=//p')
  absentes=$(printf '%s' "$coherence" | sed -n 's/^ABSENTE=//p')
  if [ "$MANQUANTS" -eq 0 ]; then
    ok "les $ATTENDUS fichiers référencés en base sont tous sur le disque"
  else
    ko "$MANQUANTS référence(s) sur $ATTENDUS pointent vers un fichier ABSENT — ces CV sont inouvrables"
    bloc "$(printf '%s' "$coherence" | sed -n 's/^DETAIL=//p')"
  fi
  if [ -n "$refusees" ]; then
    nv "inventaire incomplet : une table n'a pas répondu, les orphelins ne sont pas comptés"
    bloc "$refusees"
  elif [ "$ORPHELINS" -le "$ORPHELINS_MAX" ]; then
    ok "$ORPHELINS fichier(s) sans référence sur $PRESENTS (seuil : $ORPHELINS_MAX)"
  else
    ko "$ORPHELINS fichiers sur le disque ne sont référencés par AUCUNE ligne : données personnelles
conservées sans finalité (RGPD art. 5.1.e). Vérifier services/data_retention.py."
    bloc "$(printf '%s' "$coherence" | sed -n 's/^ORPHELIN=//p')"
  fi
  # Divergence de schéma : informatif, ni vert ni rouge. Ça ne dit rien de la
  # santé de la plateforme — mais ça dit que le schéma du dépôt et la base
  # réelle ne décrivent pas la même chose, et c'est le genre de chose qu'on
  # veut savoir AVANT de reconstruire la base ailleurs.
  if [ -n "$absentes" ]; then
    detail "colonnes du schéma absentes de cette base (cf. migrations/0016_schema_drift.sql) :"
    bloc "$absentes"
  fi
else
  # Muet = non vérifié. Écrire « aucune incohérence » ici serait exactement le
  # vert-tiré-d'un-silence que ce dépôt a déjà payé deux fois.
  ko "la cohérence base↔disque n'a PAS PU être vérifiée"
  bloc "$(printf '%s\n' "$coherence" | head -5)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 2. LA DERNIÈRE ARCHIVE HORS-SITE EST-ELLE ENCORE IMMUABLE
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI CE CONTRÔLE EXISTE, ET POURQUOI ICI
# La clé S3 posée sur le VPS doit pouvoir DÉPOSER : c'est sa raison d'être. Or
# chez Backblaze, `writeFiles` suffit à supprimer par nom — aucune liste de
# capacités ne peut donc rendre une clé de sauvegarde incapable d'effacer (voir
# backup_s3_policy.README.md). La seule protection réelle est le verrou
# d'objet en mode « compliance » : posé, il tient contre tout le monde, y
# compris contre le support de Backblaze.
#
# Mais ce verrou vient d'un RÉGLAGE DU CONTENEUR (« default retention period »),
# pas du code. Le retirer prend un clic, n'efface rien, ne casse rien et ne
# produit aucune alerte : les sauvegardes suivantes se déposent normalement,
# simplement plus protégées. On découvrirait la chose le jour du sinistre.
# C'est exactement la panne silencieuse que cette revue existe pour attraper :
# on relit le verrou SUR UN OBJET RÉEL, celui déposé en dernier, avec la clé du
# VPS elle-même. Un réglage qu'on lit dans une interface est une intention ; un
# verrou qu'on relit sur l'objet est un fait.
titre "2. Immuabilité de la dernière archive hors-site"
if [ -z "${BACKUP_S3_BUCKET:-}" ]; then
  # ROUGE et non gris : sans ces variables le contrôle ne prouve rien, et ce
  # qu'il ne prouve pas est justement la survie des sauvegardes. L'unité
  # systemd les fournit par EnvironmentFile ; leur absence signifie donc qu'on
  # a lancé la revue à la main, ou que l'unité a été modifiée.
  ko "BACKUP_S3_* absent de l'environnement : l'immuabilité n'est PAS vérifiée"
  detail "systemd les fournit via EnvironmentFile=/etc/uti-backup.env."
  detail "À la main :  sudo bash -c 'set -a; . /etc/uti-backup.env; set +a; bash $0'"
elif [ ! -f "$DEST/.dernier_succes" ]; then
  ko "aucune sauvegarde réussie n'est enregistrée : rien à vérifier hors-site"
else
  cle_objet=$(tail -1 "$DEST/.dernier_succes" | awk '{print $2}')
  taille_locale=$(tail -1 "$DEST/.dernier_succes" | awk '{print $3}')
  verrou=$(BACKEND_DIR="$BACKEND" CLE_OBJET="$cle_objet" TAILLE_ATTENDUE="${taille_locale:-0}" \
           VERROU_JOURS_MIN="$VERROU_JOURS_MIN" \
           "$BACKEND/venv/bin/python" - 2>&1 <<'PY'
import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.environ["BACKEND_DIR"], "deploy"))
import s3_backup

cle = os.environ["CLE_OBJET"]
try:
    tete = s3_backup._client().head_object(Bucket=s3_backup.BUCKET, Key=cle)
except Exception as e:                                    # noqa: BLE001
    sys.exit(f"ABSENTE: {type(e).__name__}: {str(e)[:160]}")

taille = tete.get("ContentLength", 0)
attendue = int(os.environ.get("TAILLE_ATTENDUE") or 0)
mode = tete.get("ObjectLockMode") or ""
jusqua = tete.get("ObjectLockRetainUntilDate")

print(f"TAILLE={taille}")
print(f"TAILLE_ATTENDUE={attendue}")
print(f"MODE={mode}")
if jusqua:
    reste = (jusqua - datetime.now(timezone.utc)) / timedelta(days=1)
    print(f"JUSQUA={jusqua.isoformat()}")
    print(f"RESTE_JOURS={reste:.1f}")
PY
  )
  if printf '%s' "$verrou" | grep -q '^ABSENTE:'; then
    ko "la dernière archive n'est pas lisible hors-site — ${verrou#ABSENTE: }"
    detail "Le dépôt a peut-être échoué APRÈS avoir écrit le marqueur local."
  elif printf '%s' "$verrou" | grep -q '^TAILLE='; then
    eval "$(printf '%s' "$verrou" | grep -E '^(TAILLE|TAILLE_ATTENDUE|MODE|RESTE_JOURS|JUSQUA)=' | sed 's/=\(.*\)/="\1"/')"
    if [ "${TAILLE_ATTENDUE:-0}" -gt 0 ] && [ "$TAILLE" = "$TAILLE_ATTENDUE" ]; then
      ok "l'archive déposée fait bien $TAILLE octets hors-site (identique au marqueur local)"
    elif [ "${TAILLE_ATTENDUE:-0}" -gt 0 ]; then
      ko "l'archive hors-site fait $TAILLE octets, le marqueur local en annonce $TAILLE_ATTENDUE : dépôt tronqué"
    else
      nv "taille locale inconnue (marqueur ancien) : seule la présence est vérifiée"
    fi
    case "$MODE" in
      COMPLIANCE)
        # Le seul mode qui protège ici : la clé du VPS porte bypassGovernance,
        # ce qui rend le mode « governance » purement décoratif pour elle.
        ok "verrou d'objet COMPLIANCE actif jusqu'au ${JUSQUA:-?} (${RESTE_JOURS:-?} j)" ;;
      GOVERNANCE)
        ko "verrou en mode GOVERNANCE : la clé du VPS porte bypassGovernance et peut donc
le contourner. Seul COMPLIANCE protège contre une compromission du VPS." ;;
      "")
        ko "AUCUN verrou d'objet sur la dernière archive : la rétention par défaut du
conteneur a été retirée ou n'a jamais pris. Les sauvegardes redeviennent effaçables
par la clé du VPS. Backblaze → Bucket Settings → Object Lock → Default Retention." ;;
      *)
        nv "mode de verrou inattendu « $MODE » — à regarder à la main" ;;
    esac
    # Un verrou qui expire dans deux jours n'est plus une protection : il faut
    # que la rétention couvre au moins l'intervalle entre deux revues.
    if [ -n "${RESTE_JOURS:-}" ]; then
      if awk -v r="$RESTE_JOURS" -v m="$VERROU_JOURS_MIN" 'BEGIN{exit !(r < m)}'; then
        ko "le verrou expire dans $RESTE_JOURS jours (seuil $VERROU_JOURS_MIN) : la rétention par défaut
du conteneur a été raccourcie."
      fi
    fi
  else
    ko "le contrôle d'immuabilité n'a PAS PU tourner"
    bloc "$(printf '%s\n' "$verrou" | head -5)"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# 3. CERTIFICAT TLS
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : certbot renouvelle tout seul à 30 jours de l'échéance. Quand son
# minuteur meurt — ou que le renouvellement échoue parce que le port 80 a été
# fermé entre-temps — il ne se passe RIEN pendant un mois, puis la plateforme
# devient inaccessible d'un coup, pour tout le monde, avec un avertissement de
# sécurité effrayant. 21 jours restants signifie que trois tentatives de
# renouvellement ont déjà échoué : c'est le bon moment pour l'apprendre.
titre "3. Certificat TLS"
if [ -z "$DOMAINE" ] && [ -f "$BACKEND/.env" ]; then
  DOMAINE=$(grep -m1 '^PUBLIC_BASE_URL=' "$BACKEND/.env" 2>/dev/null \
            | sed 's|^PUBLIC_BASE_URL=||; s|^https\?://||; s|/.*$||' | tr -d "\"' ")
fi
if [ -z "$DOMAINE" ]; then
  nv "aucun domaine public connu (ni DOMAINE_PUBLIC, ni PUBLIC_BASE_URL dans .env)"
elif ! command -v openssl >/dev/null 2>&1; then
  nv "openssl absent : l'échéance du certificat n'est pas vérifiée"
else
  fin=$(echo | timeout 15 openssl s_client -servername "$DOMAINE" -connect "$DOMAINE:443" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -z "$fin" ]; then
    # Pas « certificat expiré » : on n'a pas pu regarder. Mais pas vert non plus.
    ko "impossible de lire le certificat de $DOMAINE (hôte injoignable depuis le VPS ?)"
    detail "openssl s_client -connect $DOMAINE:443 </dev/null"
  else
    reste=$(( ( $(date -d "$fin" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))
    if [ "$reste" -ge "$TLS_JOURS_MIN" ]; then
      ok "certificat de $DOMAINE valide encore $reste jours (jusqu'au $fin)"
    else
      ko "certificat de $DOMAINE : plus que $reste jours. Le renouvellement automatique NE MARCHE PLUS.
  sudo certbot renew --dry-run ; systemctl list-timers 'certbot*'"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# 4. MISES À JOUR DE SÉCURITÉ EN ATTENTE
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : un VPS exposé qui n'a pas vu un correctif depuis six mois est le
# scénario d'intrusion le plus banal qui soit. On COMPTE, on ne met pas à jour :
# un `apt upgrade` non surveillé peut redémarrer PostgreSQL au milieu d'un dump.
# La revue signale, l'humain décide et regarde.
titre "4. Correctifs système en attente"
if ! command -v apt-get >/dev/null 2>&1; then
  nv "apt absent : distribution non-Debian, contrôle inapplicable"
else
  # Une seule simulation, et son code de sortie est lu. Deux appels séparés dont
  # on ne garde que la sortie donnaient « 0 correctif » — donc un vert — dès que
  # apt échouait (verrou pris par unattended-upgrades, listes non initialisées).
  # « Je n'ai pas pu compter » et « il n'y en a aucun » se ressemblent beaucoup
  # trop pour qu'on les confonde sur un VPS exposé.
  if simulation=$(apt-get -s -o Debug::NoLocking=true upgrade 2>&1); then
    secu=$(printf '%s' "$simulation" | grep -ci '^Inst.*security' || true)
    total=$(printf '%s' "$simulation" | grep -c '^Inst ' || true)
    if [ "${secu:-0}" -eq 0 ]; then
      ok "aucun correctif de sécurité en attente (${total:-0} mise(s) à jour ordinaire(s))"
    else
      ko "$secu correctif(s) de SÉCURITÉ en attente sur ${total:-0} mises à jour.
  sudo apt-get update && sudo apt-get upgrade    # à faire hors heures ouvrées"
    fi
  else
    ko "apt n'a pas pu simuler la mise à jour : les correctifs en attente ne sont PAS comptés"
    bloc "$(printf '%s\n' "$simulation" | head -3)"
  fi
  if [ -f /var/run/reboot-required ]; then
    ko "redémarrage requis : un correctif de noyau est installé mais pas actif"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# 5. UNITÉS ET MINUTEURS
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : un minuteur systemd désactivé ne dit rien. `systemctl list-timers`
# ne montre QUE les minuteurs actifs — un minuteur absent de la liste est donc
# invisible, et c'est le mode de panne le plus vicieux du dispositif : la
# sauvegarde ne tourne plus, et la supervision ne surveille que l'ÂGE de la
# dernière sauvegarde réussie, donc elle criera... mais seulement au bout de
# trois heures, et uniquement si elle-même tourne encore. On vérifie donc
# nommément que chaque minuteur attendu est armé.
titre "5. Unités systemd et minuteurs"
if ! command -v systemctl >/dev/null 2>&1; then
  nv "systemctl absent : hors d'un système systemd"
else
  # On lit le CODE DE SORTIE, pas seulement la sortie. `command -v systemctl` ne
  # prouve que la présence du binaire : dans un conteneur, ou avec un bus
  # inaccessible, il répond « Failed to connect to bus » SUR STDERR et rend 1.
  # La liste est alors vide — et une liste vide, prise pour argent comptant,
  # affirme « aucune unité en échec » précisément quand on ne sait rien. Ce
  # script a produit ce vert-là une fois, en essai, avant cette correction.
  if echecs=$(systemctl list-units --failed --no-legend --plain 2>&1); then
    # On s'exclut soi-même, et c'est indispensable. Ce service sort en 1 dès
    # qu'un point est rouge — c'est ainsi qu'il apparaît dans
    # `systemctl list-units --failed`. systemd le laisse donc en « failed »
    # jusqu'à son exécution suivante, c'est-à-dire PENDANT TOUTE LA SEMAINE.
    # Sans ce filtre, la revue du dimanche suivant compterait sa propre
    # défaillance de la semaine passée comme une anomalie : un rouge qui
    # n'apprend rien (les motifs sont déjà détaillés section par section) et qui
    # s'auto-entretient — un rouge en produit un autre, indéfiniment. Un rouge
    # qui ne peut plus se refermer est un rouge qu'on cesse de lire.
    #
    # uti-supervision.service n'est PAS exclu, lui, bien qu'il sorte aussi en 1
    # sur anomalie : il tourne toutes les 15 minutes, donc son état reflète sa
    # DERNIÈRE exécution. C'est une information d'aujourd'hui, pas l'écho d'une
    # semaine passée.
    liste=$(printf '%s' "$echecs" | awk 'NF && $1 != "uti-revue-hebdo.service" {print $1}')
    if [ -z "$liste" ]; then
      ok "aucune unité en échec"
    else
      ko "unité(s) en échec : $(echo "$liste" | tr '\n' ' ')"
      detail "systemctl status <unité> ; journalctl -u <unité> -n 50 --no-pager"
    fi
  else
    ko "systemd n'a pas répondu : les unités en échec ne sont PAS vérifiées"
    bloc "$(printf '%s\n' "$echecs" | head -3)"
  fi

  for minuteur in uti-backup.timer uti-restore-drill.timer uti-supervision.timer uti-revue-hebdo.timer; do
    etat=$(systemctl is-enabled "$minuteur" 2>/dev/null || true)
    actif=$(systemctl is-active "$minuteur" 2>/dev/null || true)
    if [ "$etat" = "enabled" ] && [ "$actif" = "active" ]; then
      prochain=$(systemctl show "$minuteur" -p NextElapseUSecRealtime --value 2>/dev/null)
      ok "$minuteur armé (prochain : ${prochain:-?})"
    elif [ -z "$etat" ]; then
      ko "$minuteur n'existe pas sur cette machine : le contrôle qu'il porte ne tourne JAMAIS"
    else
      ko "$minuteur est « $etat / $actif » : il ne se déclenchera pas"
      detail "sudo systemctl enable --now $minuteur"
    fi
  done
fi

# ═══════════════════════════════════════════════════════════════════════════
# 6. ERREURS APPLICATIVES DE LA SEMAINE
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : services/error_log.py garde les 200 dernières erreurs EN MÉMOIRE,
# et /admin/errors les affiche. Elles disparaissent à chaque redémarrage — donc
# à chaque déploiement. Personne n'ouvre cette page un dimanche. Depuis que
# record() émet aussi une ligne « UTI_EVT » dans journald, ces dégradations
# survivent et deviennent comptables sur sept jours : c'est la seule vue qui
# montre qu'un repli LLM silencieux tourne 400 fois par semaine.
titre "6. Erreurs applicatives des 7 derniers jours"
if ! command -v journalctl >/dev/null 2>&1; then
  nv "journalctl absent : le journal applicatif n'est pas lisible"
else
  jrn=$(journalctl -u uti-backend --since "-7 days" --no-pager -o cat 2>/dev/null)
  if [ -z "$jrn" ]; then
    # Un backend qui n'a RIEN journalisé en sept jours n'est pas un backend sain :
    # il est arrêté, ou son journal n'est pas persistant (/var/log/journal absent).
    ko "aucune ligne de journal pour uti-backend sur 7 jours : service arrêté, ou
journal volatil (créer /var/log/journal puis « sudo systemctl restart systemd-journald »)"
  else
    nb_evt=$(printf '%s' "$jrn" | grep -c 'UTI_EVT ' || true)
    nb_err=$(printf '%s' "$jrn" | grep 'UTI_EVT ' | grep -c '"level": *"error"' || true)
    nb_trace=$(printf '%s' "$jrn" | grep -c '^Traceback' || true)
    if [ "${nb_err:-0}" -le "$ERREURS_SEMAINE_MAX" ]; then
      ok "${nb_err:-0} erreur(s) applicative(s) sur 7 jours (seuil $ERREURS_SEMAINE_MAX), ${nb_evt:-0} événement(s) au total"
    else
      ko "${nb_err:-0} erreurs applicatives sur 7 jours : ce n'est plus du bruit de fond"
    fi
    [ "${nb_trace:-0}" -gt 0 ] && detail "$nb_trace trace(s) Python complète(s) — journalctl -u uti-backend --since -7d | grep -A15 Traceback"
    # Le classement par source dit OÙ ça se dégrade, ce qu'un total ne dit pas.
    top=$(printf '%s' "$jrn" | grep -o '"source": *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/' \
          | sort | uniq -c | sort -rn | head -5)
    if [ -n "$top" ]; then
      detail "par source :"
      bloc "$top"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# 7. SANTÉ DE POSTGRESQL
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI CES TROIS-LÀ, ET PAS VINGT MÉTRIQUES
#   • autovacuum : une table jamais nettoyée grossit et ses statistiques
#     vieillissent. Le planificateur choisit alors de mauvais plans, et la
#     plateforme « ralentit sans raison » — le diagnostic le plus difficile à
#     poser après coup, et le plus facile à voir venir ici.
#   • transaction ouverte depuis des heures : elle bloque le nettoyage de TOUTES
#     les tables et fait enfler la base sans aucune alerte.
#   • connexions : un pool qui fuit se voit ici des semaines avant de saturer.
titre "7. Santé de PostgreSQL"
sortie_pg=$(psql -d "$BASE" -v ON_ERROR_STOP=1 -tA -F'|' 2>&1 <<'SQL'
SELECT 'TAILLE', pg_size_pretty(pg_database_size(current_database()));
SELECT 'CONNEXIONS', count(*)::text || '/' || current_setting('max_connections')
  FROM pg_stat_activity;
SELECT 'TRANSACTION_LONGUE',
       coalesce(max(extract(epoch FROM (now() - xact_start)) / 60)::int, 0)::text
  FROM pg_stat_activity WHERE state <> 'idle' AND xact_start IS NOT NULL;
SELECT 'JAMAIS_NETTOYEE', relname
  FROM pg_stat_user_tables
 WHERE n_live_tup > 500
   AND greatest(coalesce(last_autoanalyze, 'epoch'), coalesce(last_analyze, 'epoch'))
       < now() - interval '14 days'
 ORDER BY n_live_tup DESC LIMIT 10;
SQL
)
if [ $? -eq 0 ] && printf '%s' "$sortie_pg" | grep -q '^TAILLE|'; then
  detail "base : $(printf '%s' "$sortie_pg" | sed -n 's/^TAILLE|//p')"
  detail "connexions : $(printf '%s' "$sortie_pg" | sed -n 's/^CONNEXIONS|//p')"
  minutes=$(printf '%s' "$sortie_pg" | sed -n 's/^TRANSACTION_LONGUE|//p')
  if [ "${minutes:-0}" -lt 60 ]; then
    ok "aucune transaction ouverte depuis plus d'une heure (max ${minutes:-0} min)"
  else
    ko "une transaction est ouverte depuis ${minutes} minutes : elle empêche le nettoyage
de TOUTES les tables. SELECT pid, query, xact_start FROM pg_stat_activity ORDER BY xact_start;"
  fi
  sales=$(printf '%s' "$sortie_pg" | grep -c '^JAMAIS_NETTOYEE|' || true)
  if [ "${sales:-0}" -eq 0 ]; then
    ok "toutes les tables ont été analysées dans les $VACUUM_JOURS_MAX derniers jours"
  else
    ko "$sales table(s) de plus de 500 lignes sans ANALYZE depuis $VACUUM_JOURS_MAX jours : $(
        printf '%s' "$sortie_pg" | sed -n 's/^JAMAIS_NETTOYEE|//p' | tr '\n' ' ')"
    detail "autovacuum est-il actif ?  psql -d $BASE -c 'SHOW autovacuum;'"
  fi
else
  ko "PostgreSQL n'a pas répondu : sa santé n'est PAS vérifiée"
  bloc "$(printf '%s\n' "$sortie_pg" | head -3)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 8. FILE D'ENVOI DES E-MAILS
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : depuis la migration 0015, plus rien n'est envoyé en direct — tout
# est DÉPOSÉ dans email_outbox et dépilé par services/scheduler.py. Cette file
# est le point de panne le plus silencieux de la plateforme : si le dépileur
# meurt, les appels HTTP continuent de réussir, l'interface affiche « invitation
# envoyée », et personne ne reçoit rien. Aucune erreur nulle part. On mesure
# donc l'ÂGE du plus vieux message encore en file — la seule grandeur qui
# distingue « ça vient d'être déposé » de « ça ne partira jamais ».
titre "8. File d'envoi des e-mails"
sortie_file=$(psql -d "$BASE" -v ON_ERROR_STOP=1 -tA -F'|' 2>&1 <<'SQL'
SELECT 'EN_FILE', count(*),
       coalesce(max(extract(epoch FROM (now() - created_at)) / 3600)::int, 0)
  FROM public.email_outbox WHERE status = 'queued';
SELECT 'MORTS', count(*) FROM public.email_outbox
 WHERE status = 'dead' AND created_at > now() - interval '7 days';
SELECT 'ENVOYES', count(*) FROM public.email_outbox
 WHERE status = 'sent' AND created_at > now() - interval '7 days';
SQL
)
if [ $? -eq 0 ] && printf '%s' "$sortie_file" | grep -q '^EN_FILE|'; then
  en_file=$(printf '%s' "$sortie_file" | sed -n 's/^EN_FILE|//p' | cut -d'|' -f1)
  age_h=$(printf '%s' "$sortie_file" | sed -n 's/^EN_FILE|//p' | cut -d'|' -f2)
  morts=$(printf '%s' "$sortie_file" | sed -n 's/^MORTS|//p')
  envoyes=$(printf '%s' "$sortie_file" | sed -n 's/^ENVOYES|//p')
  if [ "${age_h:-0}" -lt "$OUTBOX_BLOQUEE_H" ]; then
    ok "file d'e-mails saine : ${en_file:-0} en attente, le plus ancien depuis ${age_h:-0} h, ${envoyes:-0} partis cette semaine"
  else
    ko "${en_file} e-mail(s) en file, le plus ancien depuis ${age_h} h : le dépileur ne tourne plus.
L'interface dit « envoyé » et personne ne reçoit rien.  systemctl status uti-backend"
  fi
  if [ "${morts:-0}" -eq 0 ]; then
    ok "aucun e-mail abandonné cette semaine"
  else
    ko "${morts} e-mail(s) abandonné(s) après épuisement des tentatives cette semaine.
  psql -d $BASE -c \"SELECT to_email, subject, last_error FROM email_outbox WHERE status='dead' ORDER BY created_at DESC LIMIT 10;\""
  fi
else
  ko "la file d'e-mails n'a PAS PU être interrogée"
  bloc "$(printf '%s\n' "$sortie_file" | head -3)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 9. PERMISSIONS DES FICHIERS SENSIBLES
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : un `chmod` de trop pendant un dépannage ne casse rien, ne s'annonce
# pas, et ouvre les secrets de la plateforme à tout compte du VPS. C'est le
# genre de régression qui ne se voit que si on la cherche exprès — donc une
# fois par semaine.
titre "9. Permissions des fichiers sensibles"
verifier_droits() {
  local chemin="$1" acceptables="$2" conseil="$3"
  if [ ! -e "$chemin" ]; then
    # « Absent » et « je ne peux pas aller voir » ne sont pas la même chose, et
    # la différence est exactement ce que ce contrôle existe pour attraper.
    # `test -e` est FAUX dès que le répertoire parent n'est pas traversable par
    # l'utilisateur courant — /etc/postgrest est en 0750 root. Le contrôle
    # annonçait donc « /etc/postgrest/postgrest.conf absent » sur une machine
    # où ce fichier existe et sert PostgREST : un fichier de configuration
    # déclaré inexistant, donc jamais vérifié, et personne pour s'en étonner.
    local parent; parent=$(dirname "$chemin")
    if [ -d "$parent" ] && [ ! -x "$parent" ]; then
      nv "$chemin : $parent non traversable sous $(id -un) — présence NON vérifiée"
      detail "sudo stat -c '%a %U:%G' $chemin"
    else
      nv "$chemin absent (rien à vérifier)"
    fi
    return
  fi
  # stat sur un fichier 0600 root réussit même sous julian.talou : les
  # métadonnées d'un fichier sont lisibles dès qu'on peut traverser son
  # répertoire. On lit donc bien les droits réels, sans sudo et sans lire le
  # contenu.
  local droits; droits=$(stat -c '%a' "$chemin" 2>/dev/null)
  local proprio; proprio=$(stat -c '%U:%G' "$chemin" 2>/dev/null)
  # Liste explicite des modes acceptés, et surtout PAS une comparaison
  # numérique : un mode est de l'octal, pas un nombre. « 0060 » (groupe en
  # lecture-écriture, donc plus ouvert que 600) est numériquement INFÉRIEUR à
  # 600 et passerait pour plus restrictif. Une inégalité aurait donc validé des
  # droits plus larges que ceux qu'on exige.
  if [ -z "$droits" ]; then
    nv "$chemin : droits illisibles"
    return
  fi
  case " $acceptables " in
    *" $droits "*) ok "$chemin : $droits ($proprio)" ;;
    *)             ko "$chemin : $droits ($proprio) — attendu $acceptables.  sudo chmod $conseil $chemin" ;;
  esac
}
verifier_droits "$BACKEND/.env"                 "600 400" 600
verifier_droits /etc/uti-backup.env             "600 400" 600
verifier_droits /etc/uti-supervision.env        "600 400" 600
verifier_droits /etc/uti-restore-drill.env      "600 400" 600
verifier_droits /etc/postgrest/postgrest.conf   "600 640 440 400" 640
if [ -d "$FICHIERS" ]; then
  droits_fichiers=$(stat -c '%a' "$FICHIERS" 2>/dev/null)
  case "$droits_fichiers" in
    700|750) ok "$FICHIERS : $droits_fichiers (les CV ne sont pas lisibles par les autres comptes)" ;;
    "")      nv "$FICHIERS : droits illisibles" ;;
    *)       ko "$FICHIERS : $droits_fichiers — les CV déposés sont lisibles au-delà du service.  sudo chmod 700 $FICHIERS" ;;
  esac
else
  ko "$FICHIERS n'existe pas : aucun fichier n'est servi depuis le disque"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 10. ACTIVITÉ DE LA SEMAINE
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI : c'est le seul contrôle qui ne cherche pas une panne mais une
# ABSENCE. Toutes les sondes techniques peuvent être vertes pendant qu'une
# régression du front empêche tout dépôt de CV : la base répond, le disque est
# sain, les e-mails partent — et il ne se passe plus rien. Un chiffre à zéro
# une semaine où on attendait de l'activité est une question, pas une alerte :
# ce bloc informe, il ne compte donc ni vert ni rouge.
titre "10. Activité de la semaine"
# UNE REQUÊTE PAR MESURE, et non quatre dans le même appel. Avec
# ON_ERROR_STOP=1, la première requête en erreur emporte les trois suivantes :
# à la première exécution réelle, « aos » (la table s'appelle appels_offres) et
# « submissions.created_at » (la colonne s'appelle submitted_at) ont ainsi réduit
# les quatre mesures à une seule ligne d'erreur. Isolées, une mesure fausse ne
# coûte qu'elle-même — et la ligne dit laquelle, plutôt que de tout taire.
mesures=(
  "candidatures déposées|SELECT count(*) FROM public.submissions WHERE submitted_at > now() - interval '7 days';"
  "appels d'offres créés|SELECT count(*) FROM public.appels_offres WHERE created_at > now() - interval '7 days';"
  "consultants ajoutés|SELECT count(*) FROM public.consultants WHERE created_at > now() - interval '7 days';"
  "comptes connectés|SELECT count(*) FROM public.profiles WHERE last_login_at > now() - interval '7 days';"
)
muettes=0
for mesure in "${mesures[@]}"; do
  libelle="${mesure%%|*}"
  requete="${mesure#*|}"
  if valeur=$(psql -d "$BASE" -v ON_ERROR_STOP=1 -tA -c "$requete" 2>&1); then
    detail "$libelle cette semaine : $valeur"
  else
    muettes=$((muettes+1))
    detail "$libelle : NON MESURÉ — $(printf '%s' "$valeur" | head -1)"
  fi
done
# Le bloc informe et ne juge pas — sauf s'il n'a rien pu mesurer du tout : une
# mesure qui échoue en silence ferait passer une base inaccessible pour une
# semaine sans activité.
if [ "$muettes" -eq "${#mesures[@]}" ]; then
  ko "aucune mesure d'activité n'a abouti : la base ne répond pas, ou son schéma
n'est pas celui qu'attend ce contrôle"
elif [ "$muettes" -gt 0 ]; then
  nv "$muettes mesure(s) d'activité sur ${#mesures[@]} n'ont pas pu être faites"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 11. LA SUITE DE TESTS DU CODE DÉPLOYÉ
# ═══════════════════════════════════════════════════════════════════════════
# POURQUOI REJOUER LES TESTS EN PRODUCTION, PUISQUE LA CI LES A DÉJÀ JOUÉS
# La CI prouve que le code du DÉPÔT passe ses tests, avec les dépendances du
# fichier requirements.txt, sur une machine neuve. Elle ne prouve rien sur la
# machine qui sert réellement : le venv du VPS dérive (une dépendance installée
# à la main un soir de panne), le .env diffère, la version de Python n'est pas
# la même. Rejouer la suite ICI répond à une autre question — « le code qui
# tourne chez le client passe-t-il encore ses propres tests, dans son propre
# environnement ». C'est le contrôle qui attrape les dérives de venv, et elles
# ne se voient nulle part ailleurs.
titre "11. Suite de tests du code déployé"
if [ "$REVUE_TESTS" != "1" ]; then
  nv "suite de tests sautée (REVUE_TESTS=0)"
elif [ ! -x "$BACKEND/venv/bin/pytest" ]; then
  # Gris et non rouge : ne pas installer pytest en production est un choix
  # défendable. Mais on dit comment l'activer, sinon ce contrôle restera gris
  # pour toujours et vaudra son absence.
  nv "pytest absent du venv de production : la suite n'est pas rejouée"
  detail "$BACKEND/venv/bin/pip install pytest pytest-asyncio"
else
  # --timeout n'existe pas sans pytest-timeout : on borne avec timeout(1), qui
  # est toujours là. Une suite qui pend vaut une suite qui échoue.
  sortie_tests=$(cd "$BACKEND" && timeout 900 ./venv/bin/pytest -q 2>&1 | tail -20)
  code_tests=$?
  resume=$(printf '%s' "$sortie_tests" | tail -1)
  if [ "$code_tests" -eq 0 ]; then
    ok "la suite passe sur le code déployé — $resume"
  elif [ "$code_tests" -eq 124 ]; then
    ko "la suite de tests ne s'est pas terminée en 15 minutes (bloquée)"
  else
    ko "la suite de tests ÉCHOUE sur la machine de production — $resume"
    detail "cd $BACKEND && ./venv/bin/pytest -q     # le détail complet"
    bloc "$(printf '%s\n' "$sortie_tests" | tail -8)"
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
#  Verdict
# ═══════════════════════════════════════════════════════════════════════════
resume="$VERT vert(s), $ROUGE rouge(s), $GRIS non vérifié(s)"
_ajout ""
_ajout "───────────────────────────────────────────"
_ajout "Verdict : $resume"

if [ "$ROUGE" -eq 0 ]; then
  sujet="✅ UTI — revue hebdomadaire : rien à signaler"
  printf '\n\033[32m✅ %s — %s\033[0m\n' "$(date -Is)" "$resume"
else
  sujet="🚨 UTI — revue hebdomadaire : $ROUGE point(s) à regarder"
  printf '\n\033[31m❌ %s — %s\033[0m\n' "$(date -Is)" "$resume"
fi

# L'e-mail part dans les deux cas — c'est le sens de ce script (cf. en-tête).
# `courriel` est best-effort et n'échoue jamais bruyamment : un SMTP en panne ne
# doit pas transformer une revue verte en revue rouge.
if [ "$REVUE_EMAIL" = "1" ]; then
  courriel "$sujet" "$RAPPORT"
fi

# Le ping ne part QUE si tout est vert : le chien de garde externe couvre alors
# d'un seul signal « le VPS est mort », « la revue est morte » et « la revue a
# trouvé quelque chose ». Même raisonnement que supervision.sh.
#
# Écrit en if/else et non en « test && a || b ». Dans cette dernière forme, si
# `a` rend un code non nul, `b` s'exécute AUSSI : on enverrait le signal d'échec
# juste après le signal de succès. Aujourd'hui ping_garde() rend toujours 0
# (lib_alerte.sh termine par « || true »), donc le raccourci serait correct —
# mais il ne l'est que par la grâce d'un détail d'implémentation d'un AUTRE
# fichier, que personne ne pensera à relire en le modifiant. Le if/else ne
# dépend de rien.
if [ "$ROUGE" -eq 0 ]; then
  ping_garde ""
  exit 0
fi
ping_garde "/fail"
exit 1
