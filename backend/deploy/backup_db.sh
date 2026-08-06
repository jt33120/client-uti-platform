#!/usr/bin/env bash
# =============================================================================
#  Sauvegarde de la base « uti » du VPS — locale, chiffrée, déposée hors-site.
#
#  POURQUOI CE FICHIER EXISTE
#  Tant que la base vivait chez Supabase, quelqu'un d'autre la sauvegardait.
#  À partir de la bascule, personne ne le fait. Une base de production sans
#  sauvegarde éprouvée n'autorise pas la suppression du filet de sécurité qu'est
#  encore le projet Supabase : c'est le premier des critères de suppression.
#
#  ET DEPUIS QUE LES FICHIERS SONT SUR CE DISQUE
#  Les CV, les pièces jointes d'appel d'offres, les attestations de vigilance
#  URSSAF et les KBIS ne sont plus chez un hébergeur qui les réplique : ils sont
#  sur CE disque, à côté de la base. Ce script les embarque donc dans la même
#  chaîne — même chiffrement, même destination, même alerte. Sans cela, la
#  décision de les rapatrier serait une perte de données déguisée en
#  simplification : un `rm -rf` emporterait la base ET les fichiers, et seule la
#  base reviendrait.
#
#  CE QUE ÇA FAIT
#   0. sauvegarde du RÉPERTOIRE DES FICHIERS quand son contenu a changé
#      (voir « pourquoi pas à chaque heure » plus bas) ;
#   1. pg_dump au format custom (rejouable table par table) ;
#   2. CHIFFREMENT age avec une clé PUBLIQUE : cette machine peut écrire une
#      sauvegarde, elle ne peut PAS la relire (voir « chiffrement » plus bas) ;
#   3. DÉPÔT HORS-SITE sur un conteneur OVH distinct, avec une clé S3 distincte
#      de celle de l'application, puis vérification que ce qui est arrivé
#      là-bas est bien ce qui est parti d'ici ;
#   4. conservation locale glissante : 72 horaires (3 j) + 14 quotidiennes +
#      8 hebdomadaires. La rétention HORS-SITE, elle, n'est PAS gérée ici : elle
#      est posée côté serveur (cycle de vie + verrou d'objet), précisément pour
#      que ce script ne PUISSE PAS effacer l'historique distant ;
#   5. en cas d'ÉCHEC, un e-mail part immédiatement via le SMTP du backend —
#      pas via la file d'e-mails, qui vit dans la base qu'on sauvegarde ;
#   6. un signal de vie au chien de garde externe : si le VPS ENTIER meurt,
#      c'est l'absence de ce signal qui déclenche l'alerte, puisque plus rien
#      ici ne peut la produire.
#
#  ── POURQUOI pg_dump ET PAS pgBackRest / barman ──────────────────────────
#  16 Mo, 22 tables, 1396 lignes dans la plus grosse. Un dump complet prend
#  deux secondes. À ce volume, la FRÉQUENCE remplace le PITR : une sauvegarde
#  par heure ramène la perte maximale à une heure, pour un coût d'exploitation
#  nul et zéro nouveau mode de panne.
#  pgBackRest apporterait la restauration à la seconde près — et, avec elle,
#  `archive_command`. Or si l'archivage des WAL échoue (dépôt injoignable, clé
#  expirée), PostgreSQL ARRÊTE de recycler pg_wal : le disque se remplit et la
#  PRODUCTION S'ARRÊTE. On échangerait « perdre au pire une heure » contre
#  « la base tombe parce que la sauvegarde a un problème ». À 16 Mo, ce n'est
#  pas un bon échange.
#  SEUIL DE BASCULE, écrit ici pour qu'il ne se décide pas à l'instinct : passer
#  à pgBackRest le jour où l'une de ces trois lignes est franchie —
#    * la base dépasse ~2 Go (le dump horaire ne tient plus dans la fenêtre) ;
#    * une heure de saisie perdue devient inacceptable pour le métier ;
#    * il y a plus d'un serveur à sauvegarder.
#
#  ── POURQUOI age, ET PAS gpg / openssl enc / le chiffrement serveur S3 ───
#  Les archives contiennent des CV (nom, téléphone, parcours), des adresses
#  e-mail, des empreintes argon2id et les secrets TOTP EN CLAIR de
#  profiles.mfa_secret. Elles partent chez un tiers.
#    * `openssl enc` est symétrique : la phrase de passe devrait vivre sur le
#      VPS, donc le VPS saurait déchiffrer, donc une compromission du VPS lit
#      TOUT l'historique. Éliminé.
#    * le chiffrement côté serveur S3 protège d'un disque volé chez OVH, pas
#      d'un compte OVH compromis : c'est OVH qui détient la clé. Utile en
#      complément, insuffisant seul. Éliminé comme mécanisme principal.
#    * gpg fait le travail, mais son trousseau, son agent et son pinentry sont
#      trois choses à comprendre à 3 h du matin, six mois plus tard.
#    * age chiffre vers une clé PUBLIQUE. AGE_RECIPIENT n'est pas un secret et
#      peut rester en clair dans l'unité systemd. La clé privée n'est PAS sur
#      cette machine : le VPS est mathématiquement incapable de relire ce qu'il
#      vient d'écrire. C'est exactement la propriété qu'on veut d'un rançongiciel
#      qui prendrait ce serveur.
#  OÙ VIT LA CLÉ PRIVÉE : voir RUNBOOK.md §9.3. Jamais ici. Jamais dans .env.
#  Jamais dans le conteneur qui contient les archives qu'elle ouvre.
#
#  Installation : voir uti-backup.service / uti-backup.timer.
#      sudo install -m 750 -o julian.talou -g julian.talou \
#           ~/app/backend/deploy/backup_db.sh /usr/local/bin/uti-backup
#      sudo install -m 640 -o julian.talou -g julian.talou \
#           ~/app/backend/deploy/lib_alerte.sh /usr/local/lib/uti-lib_alerte.sh
#      sudo install -d -m 750 -o julian.talou -g julian.talou /var/backups/uti
# =============================================================================
set -uo pipefail

DEST="${BACKUP_DIR:-/var/backups/uti}"
BASE="${PGDATABASE:-uti}"
BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
# Doit correspondre à LOCAL_STORAGE_DIR de backend/.env (config.py). Deux valeurs
# qui divergent produiraient une sauvegarde parfaitement verte d'un répertoire
# vide — la panne la plus discrète de tout ce dispositif.
FICHIERS="${FILES_DIR:-/var/lib/uti/files}"
JOUR=$(date -u +%F)
# Horodatage UTC dans le nom : l'ordre alphabétique des objets S3 devient
# l'ordre chronologique, ce sur quoi s'appuie « la dernière sauvegarde ».
# En heure locale, le passage à l'heure d'hiver produirait deux fichiers 02h30.
HORODATE=$(date -u +%Y%m%dT%H%M%SZ)
FICHIER="$DEST/uti-$HORODATE.pgcustom"

# shellcheck source=./lib_alerte.sh disable=SC1091
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib_alerte.sh" 2>/dev/null \
  || . /usr/local/lib/uti-lib_alerte.sh

alerte() { crie "sauvegarde" "$1"; }

# Signale au chien de garde que la tâche COMMENCE : il mesure ainsi la durée et
# repère une exécution qui part mais ne revient jamais (blocage), cas qu'un
# simple ping final ne distingue pas d'une exécution qui n'a pas eu lieu.
ping_garde "/start"

mkdir -p "$DEST" || alerte "répertoire $DEST inaccessible"

# ── Contrôle de CONFIGURATION, avant toute chose ────────────────────────────
# Si l'application écrit ses fichiers en local et que le répertoire à
# sauvegarder n'existe pas, ce script produirait chaque heure une sauvegarde
# verte de la MOITIÉ des données. Il doit donc refuser de commencer.
#
# Placé ICI et pas au moment de traiter les fichiers : plus loin, le dump de la
# base est déjà fait et déposé, et un exit à ce stade sauterait la rotation
# locale et le marqueur de succès — la supervision annoncerait alors que la
# sauvegarde de la BASE ne tourne plus, ce qui est faux et envoie chercher au
# mauvais endroit. Un défaut de configuration se refuse avant de travailler,
# pas au milieu.
if grep -qE '^[[:space:]]*STORAGE_BACKEND=local' "$BACKEND/.env" 2>/dev/null \
   && [ ! -d "$FICHIERS" ]; then
  alerte "STORAGE_BACKEND=local mais $FICHIERS est absent : les CV, pièces d'AO et attestations URSSAF ne sont sauvegardés NULLE PART"
fi

# Refuser de sauvegarder s'il ne reste presque rien : un dump tronqué par un
# disque plein est pire qu'une absence de dump, parce qu'il rassure.
libre_ko=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
[ "$libre_ko" -gt 1048576 ] || alerte "moins de 1 Go libre sur $DEST ($((libre_ko/1024)) Mo)"

pg_dump -d "$BASE" --no-owner --no-privileges -Fc -f "$FICHIER.partiel" \
  || alerte "pg_dump a échoué"

# Renommage atomique : un fichier au nom définitif est un fichier complet.
mv "$FICHIER.partiel" "$FICHIER"
chmod 600 "$FICHIER"

taille=$(stat -c%s "$FICHIER")
# La base fait 16 Mo de données ; un dump compressé sous 50 Ko signifie
# « schéma sans données », donc une catastrophe silencieuse.
[ "$taille" -gt 51200 ] || alerte "dump anormalement petit ($taille octets)"

# Contrôle de lisibilité : pg_restore --list échoue sur une archive corrompue.
# Sans ce contrôle, on découvrirait le problème le jour de la restauration.
pg_restore --list "$FICHIER" > /dev/null || alerte "l'archive produite est illisible"

# ── Chiffrement au repos ────────────────────────────────────────────────────
# Le fichier CLAIR reste en local (mode 600, disque du VPS) : c'est lui que
# restore_drill.sh rejoue chaque semaine, sans avoir besoin de la clé privée.
# Le fichier CHIFFRÉ est le seul à quitter la machine.
CHIFFRE="$FICHIER.age"
if [ -n "${AGE_RECIPIENT:-}" ]; then
  command -v age >/dev/null || alerte "AGE_RECIPIENT est défini mais 'age' n'est pas installé (apt install age)"
  # -a serait de l'armure ASCII : inutile ici et +33 % de volume.
  age -r "$AGE_RECIPIENT" -o "$CHIFFRE.partiel" "$FICHIER" \
    || alerte "le chiffrement age a échoué"
  mv "$CHIFFRE.partiel" "$CHIFFRE"
  chmod 600 "$CHIFFRE"
  # Un fichier age valide commence par « age-encryption.org/v1 ». Le vérifier
  # coûte une lecture de 21 octets et détecte le cas où age aurait produit un
  # fichier vide sans code d'erreur.
  head -c 21 "$CHIFFRE" | grep -q '^age-encryption.org/v1' \
    || alerte "le fichier chiffré n'a pas l'en-tête age attendu"
else
  # Fail-closed volontaire : sans clé publique, rien ne part hors-site. Déposer
  # EN CLAIR des CV et des secrets TOTP chez un tiers serait pire que ne rien
  # déposer, parce que ça se voit moins.
  alerte "AGE_RECIPIENT non défini : refus d'envoyer des données personnelles non chiffrées hors-site"
fi

# ── Dépôt hors-site ─────────────────────────────────────────────────────────
# Sans cette étape, un rançongiciel — ou un `rm -rf` malheureux — emporte la
# production ET ses sauvegardes du même geste : elles sont sur le même disque.
# La clé utilisée ici (BACKUP_S3_*) n'est PAS celle de l'application (S3_* dans
# backend/.env) et n'a pas le droit de SUPPRIMER. Voir setup_backup_offsite.sh.
CLE_S3="uti/$(date -u +%Y/%m)/uti-$HORODATE.pgcustom.age"
# Les BACKUP_S3_* arrivent par EnvironmentFile=/etc/uti-backup.env (unité systemd)
# et sont lues par s3_backup.py dans son environnement — jamais passées en
# argument, où `ps` les exposerait à tout utilisateur du VPS.
taille_distante=$("$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" envoyer "$CHIFFRE" "$CLE_S3") \
  || alerte "dépôt hors-site impossible ($CLE_S3) — la sauvegarde locale existe mais ne survivrait pas au VPS"

taille_chiffree=$(stat -c%s "$CHIFFRE")
[ "$taille_distante" = "$taille_chiffree" ] \
  || alerte "l'objet déposé fait $taille_distante octets, le fichier local $taille_chiffree"

# Vérification octet par octet une fois par jour seulement (créneau de 3 h) :
# re-télécharger à chaque heure coûterait 24 fois le volume pour une garantie
# qu'un contrôle quotidien apporte déjà. Le contrôle de taille, lui, tourne à
# chaque exécution parce qu'il est gratuit.
if [ "$(date -u +%H)" = "03" ]; then
  "$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" verifier "$CHIFFRE" "$CLE_S3" >/dev/null \
    || alerte "l'objet hors-site $CLE_S3 diffère du fichier local — dépôt corrompu"
fi

# Le fichier chiffré local n'a plus d'utilité une fois déposé : le garder
# doublerait l'occupation disque sans rien apporter (le clair sert aux
# répétitions, le chiffré est hors-site).
rm -f "$CHIFFRE"

# ── Les FICHIERS (CV, pièces jointes d'AO, URSSAF, KBIS) ────────────────────
# POURQUOI PAS À CHAQUE HEURE, ET POURQUOI PAS UNE FOIS PAR JOUR NON PLUS
# 38 objets, ~50 Mo. Envoyer 50 Mo toutes les heures, c'est 1,2 Go par jour dans
# un conteneur À VERROU D'OBJET où rien ne s'efface : la facture monterait sans
# fin pour un contenu identique 23 heures sur 24. Une fois par jour, à l'inverse,
# donnerait aux fichiers un RPO de 24 h contre 1 h à la base — et un CV déposé à
# 10 h, perdu à 23 h, serait référencé par une ligne restaurée pointant sur un
# fichier absent. C'est la panne la plus pénible à diagnostiquer : la
# restauration « réussit ».
# D'où : contrôle horaire, envoi UNIQUEMENT si le contenu a changé. Les fichiers
# sont immuables (on dépose et on supprime, on ne modifie pas), donc la plupart
# des heures ne coûtent qu'un `find`.
# ── Le leg FICHIERS ne doit pas faire passer la BASE pour morte ────────────
# `crie` termine le script (exit 1). Utilisé ici, un échec sur les fichiers
# sautait la rotation locale ET l'écriture de .dernier_succes — donc la
# supervision aurait annoncé « la sauvegarde de la BASE ne tourne plus », ce qui
# est faux et envoie chercher au mauvais endroit. Pire : la rotation étant
# sautée, ~100 Mo d'archives horaires s'accumuleraient chaque jour jusqu'à
# remplir le disque, et un disque plein corrompt le VPS entier.
#
# La base est intégralement sauvegardée et déposée AVANT ce bloc. Un échec ici
# est donc grave mais PARTIEL : il se signale, il laisse une trace que
# supervision.sh relève, et il n'annule pas ce qui a réussi.
echec_fichiers() {
  printf '%s %s\n' "$(date -uIs)" "$1" > "$DEST/.echec_fichiers"
  previens "sauvegarde" "leg FICHIERS en échec : $1
La sauvegarde de la BASE, elle, a réussi et a été déposée hors-site.
Les fichiers du disque ne sont donc PAS protégés depuis $(cat "$DEST/.dernier_succes_fichiers" 2>/dev/null | cut -d" " -f1 || echo 'jamais')."
}

FICHIERS_ENVOYES=0
if [ -d "$FICHIERS" ]; then
  # Empreinte du contenu : chemin + taille + date de modification. PAS le
  # contenu lui-même — relire 50 Mo chaque heure pour découvrir que rien n'a
  # bougé coûterait plus que ce qu'on économise.
  EMPREINTE=$(find "$FICHIERS" -type f -printf '%P %s %T@\n' 2>/dev/null \
              | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
  MARQUEUR_F="$DEST/.empreinte_fichiers"

  # ── Redépôt PÉRIODIQUE, même sans changement ──────────────────────────────
  # Le seul test d'empreinte annulait silencieusement toute la garantie.
  #
  # En régime de croisière — 11 utilisateurs, des fichiers qui ne bougent plus —
  # l'empreinte ne change jamais. Aucun redépôt, donc. Or le conteneur hors-site
  # a un verrou d'objet de 30 jours et une politique de cycle de vie : passé ce
  # délai, la dernière archive de fichiers EXPIRE et disparaît. Les CV
  # n'existeraient alors plus qu'en un seul exemplaire, sur le VPS, exactement
  # ce que la décision « les fichiers vivent sur le disque » avait promis
  # d'éviter. Et rien ne l'aurait signalé : la sauvegarde serait restée verte.
  #
  # On redépose donc aussi quand le dernier succès date de plus de REDEPOT_MAX_J
  # jours — valeur très en deçà de l'expiration, pour qu'un incident de quelques
  # jours ne mange pas la marge. Coût : une archive de ~50 Mo par semaine.
  REDEPOT_MAX_J="${REDEPOT_MAX_J:-7}"
  SUCCES_F="$DEST/.dernier_succes_fichiers"
  redepot_du=0
  if [ ! -f "$SUCCES_F" ]; then
    redepot_du=1
  elif [ -n "$(find "$SUCCES_F" -mtime "+${REDEPOT_MAX_J}" 2>/dev/null)" ]; then
    redepot_du=1
  fi

  if [ "$EMPREINTE" != "$(cat "$MARQUEUR_F" 2>/dev/null)" ] || [ "$redepot_du" = "1" ]; then
    ARCHIVE_F="$DEST/uti-fichiers-$HORODATE.tar"
    # -C "$FICHIERS" . : l'archive contient « ./cvs/… », donc elle se restaure
    # dans n'importe quel répertoire. Une archive qui embarquerait le chemin
    # absolu ne se restaurerait qu'au même endroit — inutilisable pour une
    # répétition, qui extrait justement AILLEURS.
    tar -cf "$ARCHIVE_F.partiel" -C "$FICHIERS" . \
      || { echec_fichiers "tar a échoué sur $FICHIERS"; false; } || true
    mv "$ARCHIVE_F.partiel" "$ARCHIVE_F"
    chmod 600 "$ARCHIVE_F"

    nb_fichiers=$(tar -tf "$ARCHIVE_F" | grep -vc '/$' || true)
    # Contrôle de COHÉRENCE avec la base, pas seulement de volume : une archive
    # vide alors que la base référence des CV signifie que le répertoire a été
    # vidé ou que FILES_DIR ne pointe pas où le backend écrit. Rien ne casse
    # aujourd'hui — on ne s'en apercevrait qu'en restaurant.
    refs=$(psql -d "$BASE" -tAc \
      "SELECT count(*) FROM public.submissions WHERE cv_url IS NOT NULL;" 2>/dev/null \
      | tr -d '[:space:]')
    if [ "${refs:-0}" -gt 0 ] && [ "${nb_fichiers:-0}" -eq 0 ]; then
      previens "sauvegarde" "l'archive des fichiers est VIDE alors que la base référence $refs CV.
Vérifier que FILES_DIR ($FICHIERS) est bien LOCAL_STORAGE_DIR de backend/.env.
La base est sauvegardée ; les fichiers qu'elle référence ne le sont pas."
    fi

    age -r "$AGE_RECIPIENT" -o "$ARCHIVE_F.age.partiel" "$ARCHIVE_F" \
      || { echec_fichiers "le chiffrement age des fichiers a échoué"; false; } || true
    mv "$ARCHIVE_F.age.partiel" "$ARCHIVE_F.age"
    chmod 600 "$ARCHIVE_F.age"

    CLE_S3_F="uti/fichiers/$(date -u +%Y/%m)/uti-fichiers-$HORODATE.tar.age"
    taille_f_distante=$("$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" \
                        envoyer "$ARCHIVE_F.age" "$CLE_S3_F") \
      || { echec_fichiers "dépôt hors-site des FICHIERS impossible ($CLE_S3_F) — les CV ne survivraient pas au VPS"; false; } || true
    taille_f_chiffree=$(stat -c%s "$ARCHIVE_F.age")
    [ "$taille_f_distante" = "$taille_f_chiffree" ] \
      || echec_fichiers "l'archive de fichiers déposée fait $taille_f_distante octets, la locale $taille_f_chiffree"

    rm -f "$ARCHIVE_F.age"
    # L'empreinte n'est écrite qu'APRÈS un dépôt réussi : un échec laisse
    # l'ancienne valeur, donc la tentative recommence à l'heure suivante au lieu
    # d'être considérée comme faite.
    printf '%s\n' "$EMPREINTE" > "$MARQUEUR_F"
    printf '%s %s %s\n' "$(date -uIs)" "$CLE_S3_F" "$taille_f_chiffree" \
      > "$DEST/.dernier_succes_fichiers"
    # Le marqueur d'échec ne disparaît qu'à un succès COMPLET : tant qu'il est
    # là, supervision.sh le signale.
    rm -f "$DEST/.echec_fichiers"
    FICHIERS_ENVOYES=$nb_fichiers
    # L'archive CLAIRE reste en local : c'est elle que restore_drill.sh extrait
    # chaque semaine, sans avoir besoin de la clé privée. Rotation à 14 jours,
    # alignée sur les quotidiennes de la base.
    find "$DEST" -name 'uti-fichiers-*.tar' -mtime +14 -delete
  fi
elif grep -qE '^[[:space:]]*STORAGE_BACKEND=local' "$BACKEND/.env" 2>/dev/null; then
  # Ne devrait jamais arriver : le contrôle de configuration en tête de script
  # a déjà refusé de démarrer dans ce cas. Conservé comme filet, non fatal ici
  # pour la même raison que le reste du bloc.
  echec_fichiers "$FICHIERS a disparu en cours d'exécution"
else
  echo "[sauvegarde] $FICHIERS absent — attendu tant que STORAGE_BACKEND n'est pas « local »."
fi

# ── Configuration, une fois par jour ────────────────────────────────────────
# POURQUOI : restaurer la base ne remet pas la plateforme en service. Le .env
# porte une vingtaine de secrets (JWT_SECRET, SMTP_PASSWORD, clés LLM, clés S3)
# et /etc/postgrest/ porte le secret JWT que PostgREST doit partager avec le
# backend. Sans eux, un VPS neuf a une base parfaite et une application qui ne
# démarre pas — et c'est CETTE étape, pas la base, qui domine le temps de
# reprise. Les chiffrer vers la même clé publique est cohérent : le dump
# contient déjà les secrets TOTP en clair (profiles.mfa_secret), donc l'archive
# de base est DÉJÀ aussi sensible que le .env. Rien n'est aggravé, et le temps
# de reprise passe de « une demi-journée à retrouver vingt secrets » à
# « déchiffrer un fichier ».
if [ "$(date -u +%H)" = "03" ]; then
  CONF_TAR="$DEST/conf-$JOUR.tar"
  # --ignore-failed-read : /etc/postgrest peut ne pas encore exister sur une
  # machine en cours d'installation ; ce n'est pas une raison de perdre le .env.
  tar -cf "$CONF_TAR" --ignore-failed-read \
      -C / "etc/postgrest" \
      -C "$BACKEND" ".env" 2>/dev/null
  if [ -s "$CONF_TAR" ]; then
    # `previens` et non `crie` : une configuration non déposée allonge la
    # reprise, elle ne perd aucune donnée. Faire échouer toute la sauvegarde
    # pour ça reviendrait à perdre la base pour protéger le .env.
    if ! ( age -r "$AGE_RECIPIENT" -o "$CONF_TAR.age" "$CONF_TAR" &&
           "$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" \
             envoyer "$CONF_TAR.age" "uti/conf/conf-$JOUR.tar.age" >/dev/null ); then
      previens "sauvegarde" "la configuration (.env, /etc/postgrest) n'a pas pu être déposée hors-site.
La base, elle, est sauvegardée. Mais une reprise sur VPS neuf exigera de
reconstituer les secrets à la main — compter une demi-journée de plus."
    fi
  fi
  # Le .tar en clair contient TOUS les secrets de la plateforme : il ne survit
  # pas à cette ligne. shred plutôt que rm — un rm laisse les octets sur le
  # disque, et ce disque part un jour en recyclage chez OVH.
  shred -u "$CONF_TAR" "$CONF_TAR.age" 2>/dev/null || rm -f "$CONF_TAR" "$CONF_TAR.age"
fi

# ── Rotation LOCALE ─────────────────────────────────────────────────────────
# Rappel : elle ne concerne QUE /var/backups/uti. La rétention hors-site est
# posée côté serveur par une règle de cycle de vie et par le verrou d'objet
# (setup_backup_offsite.sh) — précisément pour que ce script, s'il était
# détourné, ne PUISSE PAS effacer l'historique distant.
# Une sauvegarde par heure sur 3 jours = 72 fichiers × ~2 Mo ≈ 150 Mo : de quoi
# revenir en arrière finement sur la fenêtre où l'on s'aperçoit d'une bêtise.
find "$DEST" -name 'uti-*.pgcustom' -mmin +4320 ! -name 'uti-quot-*' ! -name 'uti-hebdo-*' -delete
# Une quotidienne (créneau de 3 h) conservée 14 jours : couvre la période
# d'observation post-bascule sans garder 336 fichiers horaires.
if [ "$(date -u +%H)" = "03" ]; then
  cp -a "$FICHIER" "$DEST/uti-quot-$JOUR.pgcustom"
  find "$DEST" -name 'uti-quot-*.pgcustom' -mtime +14 -delete
  # Une hebdomadaire le dimanche, conservée 8 semaines : rattrape une corruption
  # découverte tardivement, que 14 jours ne couvriraient pas.
  if [ "$(date -u +%u)" = "7" ]; then
    cp -a "$FICHIER" "$DEST/uti-hebdo-$JOUR.pgcustom"
    find "$DEST" -name 'uti-hebdo-*.pgcustom' -mtime +56 -delete
  fi
fi

# Trace horodatée du dernier SUCCÈS. supervision.sh lit ce fichier plutôt que la
# date du dernier .pgcustom : un dump laissé par une exécution à moitié réussie
# porte une date récente et ferait croire que tout va bien.
printf '%s %s %s\n' "$(date -uIs)" "$CLE_S3" "$taille_chiffree" > "$DEST/.dernier_succes"

# Signal de vie : tant qu'il arrive, le chien de garde se tait. Il n'aboie que
# sur son ABSENCE — donc y compris quand le VPS ne peut plus rien émettre.
ping_garde ""

if [ "$FICHIERS_ENVOYES" -gt 0 ]; then
  suffixe_fichiers=" + $FICHIERS_ENVOYES fichier(s) déposé(s)"
else
  suffixe_fichiers=" (fichiers inchangés, rien à redéposer)"
fi
echo "[sauvegarde] OK $FICHIER ($((taille/1024)) Ko) → $CLE_S3 ($((taille_chiffree/1024)) Ko chiffrés)$suffixe_fichiers — $(find "$DEST" -name 'uti-*.pgcustom' | wc -l) archive(s) locale(s)"
