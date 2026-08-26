"""
Garde-fous de la SAUVEGARDE DES FICHIERS — la contrepartie de la décision.

Les CV, les pièces jointes d'appel d'offres, les attestations de vigilance
URSSAF et les KBIS ont quitté Supabase Storage pour le disque du VPS. Tant
qu'ils étaient chez un hébergeur, quelqu'un d'autre les répliquait. À partir de
maintenant, personne — sauf `deploy/backup_db.sh`.

Sans ce dispositif, la décision n'est pas « simplifier le stockage », c'est
« supprimer la sauvegarde des fichiers ». Et cette perte-là a la propriété
d'être invisible : la base se sauvegarde, se restaure, se compte ; ses lignes
désignent des fichiers qui n'existent plus, et on ne l'apprend qu'en cliquant.

LECTURE PAR TEXTE, JAMAIS PAR EXÉCUTION — même raison que
tests/test_backup_kit.py:14-19 : exécuter ces scripts demanderait PostgreSQL,
une clé S3 et une clé age. Leur absence en CI mettrait le test en `skip`,
c'est-à-dire silencieux, précisément là où on voudrait qu'il parle.
"""
import ast
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1]
RACINE = BACKEND.parent
DEPLOY = BACKEND / "deploy"

BACKUP = DEPLOY / "backup_db.sh"
DRILL = DEPLOY / "restore_drill.sh"
UNITE_BACKUP = DEPLOY / "uti-backup.service"
CONFIG = BACKEND / "config.py"
ENV_EXEMPLE = BACKEND / ".env.example"
RUNBOOK = RACINE / "RUNBOOK.md"

#: Valeur unique du répertoire de stockage. Elle apparaît dans quatre fichiers ;
#: c'est le nombre d'endroits où elle peut diverger.
DEPOT = "/var/lib/uti/files"


def lire(chemin: pathlib.Path) -> str:
    return chemin.read_text(encoding="utf-8")


def code_seul(chemin: pathlib.Path) -> str:
    """Le fichier privé de ses lignes de commentaire — les commentaires de ces
    scripts EXPLIQUENT ce qu'ils empêchent, ils citent donc forcément les mots
    qu'on cherche à interdire."""
    return "\n".join(l for l in lire(chemin).splitlines() if not l.lstrip().startswith("#"))


# ── Les fichiers partent, chiffrés, hors du VPS ────────────────────────────

def test_la_sauvegarde_embarque_le_repertoire_des_fichiers():
    """Sauvegarder la base sans les fichiers, c'est sauvegarder les références
    et perdre ce qu'elles désignent."""
    src = code_seul(BACKUP)
    assert "FILES_DIR" in src, "backup_db.sh ne connaît plus le répertoire des fichiers."
    assert re.search(r'tar -cf "\$ARCHIVE_F', src), (
        "backup_db.sh n'archive plus le répertoire des fichiers : les CV, les "
        "pièces d'AO et les attestations URSSAF ne sont sauvegardés nulle part."
    )
    assert re.search(r'envoyer "\$ARCHIVE_F\.age"', src), (
        "L'archive des fichiers ne part plus hors-site : elle mourrait avec le "
        "disque qu'elle est censée protéger."
    )


def test_les_fichiers_partent_chiffres_comme_la_base():
    """Ce sont des CV nominatifs et des pièces d'identité d'entreprise déposées
    par des tiers. Les envoyer en clair chez un hébergeur serait pire que ne pas
    les envoyer, parce que ça se voit moins."""
    src = code_seul(BACKUP)
    assert re.search(r'age -r "\$AGE_RECIPIENT" -o "\$ARCHIVE_F\.age', src), (
        "L'archive des fichiers n'est plus chiffrée avec la clé publique age."
    )
    # Le bloc « fichiers » vient APRÈS la garde sur AGE_RECIPIENT : sans clé
    # publique, le script a déjà crié et s'est arrêté.
    assert src.index("AGE_RECIPIENT") < src.index("ARCHIVE_F"), (
        "Le bloc des fichiers est passé AVANT la garde sur AGE_RECIPIENT : une "
        "archive de CV pourrait partir sans chiffrement."
    )


def test_le_depot_des_fichiers_nenvoie_que_ce_qui_a_change():
    """50 Mo toutes les heures dans un conteneur à verrou d'objet, où rien ne
    s'efface, ce sont 1,2 Go par jour de contenu identique. Mais un envoi
    quotidien donnerait aux fichiers un RPO de 24 h contre 1 h à la base : un CV
    déposé le matin et perdu le soir serait référencé par une ligne restaurée
    pointant sur un fichier absent."""
    src = code_seul(BACKUP)
    assert ".empreinte_fichiers" in src, (
        "La détection de changement a disparu : soit on redépose 50 Mo par heure, "
        "soit on passe en quotidien et le RPO des fichiers cesse d'être celui de "
        "la base."
    )
    assert src.index(".empreinte_fichiers") < src.rindex("printf '%s\\n' \"$EMPREINTE\""), (
        "L'empreinte est écrite avant le dépôt : un envoi en échec serait "
        "considéré comme fait, et jamais retenté."
    )


def test_un_repertoire_absent_ne_passe_pas_en_silence():
    """Le cas qui rend tout ce dispositif inutile : l'application écrit ses
    fichiers en local, et le répertoire que la sauvegarde vise n'existe pas
    (FILES_DIR ≠ LOCAL_STORAGE_DIR, montage oublié, chemin renommé). Chaque
    heure produirait alors une sauvegarde verte de la moitié des données."""
    src = code_seul(BACKUP)
    assert "STORAGE_BACKEND=local" in src, (
        "backup_db.sh ne vérifie plus si l'application est réellement en mode "
        "local : un répertoire absent passerait pour une situation normale."
    )
    bloc = src[src.index("STORAGE_BACKEND=local"):]
    assert "alerte" in bloc.split("else")[0], (
        "Le cas « mode local mais répertoire absent » ne crie plus."
    )


def test_la_sauvegarde_verifie_sa_coherence_avec_la_base():
    """Une archive de fichiers VIDE alors que la base référence 32 CV n'est pas
    une petite anomalie : c'est la sauvegarde qui ne sauvegarde rien. Aucun
    contrôle de taille ne le dit — l'archive vide est une archive valide."""
    src = code_seul(BACKUP)
    assert "FROM public.submissions WHERE cv_url IS NOT NULL" in src, (
        "backup_db.sh ne confronte plus l'archive des fichiers aux références "
        "de la base."
    )


# ── Et ils se restaurent — vérifié, pas supposé ────────────────────────────

def test_la_repetition_restaure_aussi_les_fichiers():
    """`restore_drill.sh` prouvait qu'une base est restaurable. Depuis que les
    fichiers vivent ici, une base restaurable dont les fichiers manquent est une
    base de liens morts — et la restauration « réussit »."""
    src = code_seul(DRILL)
    assert re.search(r'tar -xf "\$ARCHIVE_F"', src), (
        "restore_drill.sh n'extrait plus l'archive des fichiers : rien ne prouve "
        "qu'elle se restaure."
    )
    assert "uti-fichiers-" in src, (
        "La répétition ne sait plus trouver l'archive des fichiers."
    )


def test_la_repetition_confronte_les_references_de_la_base_restauree():
    """Compter les fichiers ne suffit pas : ce qui compte est que CHAQUE
    référence de la base restaurée désigne un fichier présent. Les CINQ
    familles doivent y être — en oublier une, c'est valider une restauration où
    les attestations URSSAF ont disparu."""
    src = code_seul(DRILL)
    for table, bucket in (
        ("submissions", "cvs"),
        ("profiles", "avatars"),
        ("partner_compliance_docs", "compliance"),
        ("appels_offres", "ao-sources"),
        ("email_templates", "email-assets"),
    ):
        assert table in src and bucket in src, (
            f"La répétition ne vérifie plus les fichiers de « {table} » "
            f"(bucket « {bucket} ») : ils pourraient manquer sans que rien ne le dise."
        )
    assert "ABSENT de l'archive" in lire(DRILL), (
        "L'écart « référence sans fichier » n'est plus signalé."
    )


def test_la_repetition_couvre_les_cinq_buckets_du_code():
    """La liste des familles vérifiées doit suivre celle des buckets qui existent.

    Ce test existe parce que la version précédente en couvrait QUATRE sur cinq :
    les images des modèles d'e-mail manquaient. Elles ne sont référencées par
    aucune colonne — elles vivent dans le HTML de `email_templates.body` — et
    c'est précisément ce qui les avait fait oublier. `email_templates` étant vide
    en production, le trou était sans effet visible : la meilleure façon de durer.

    On ancre donc la liste sur `migrate_storage_to_ovh.BUCKETS`, source unique.
    Ajouter un bucket là-bas sans l'ajouter ici fera échouer ce test.
    """
    migration = lire(BACKEND / "scripts" / "migrate_storage_to_ovh.py")
    m = re.search(r"^BUCKETS\s*=\s*\[(.*?)\]", migration, re.M | re.S)
    assert m, "BUCKETS introuvable dans migrate_storage_to_ovh.py"
    buckets = re.findall(r'"([^"]+)"', m.group(1))
    assert len(buckets) == 5, f"Le nombre de buckets a changé : {buckets}"

    src = code_seul(DRILL)
    oublies = [b for b in buckets if b not in src]
    assert not oublies, (
        f"Bucket(s) absent(s) de la répétition de restauration : {oublies}. "
        "Une restauration serait déclarée conforme alors que ces fichiers "
        "auraient disparu."
    )


def test_les_images_des_modeles_email_sont_extraites_du_html():
    """Les images d'e-mail ne sont dans aucune colonne : il faut les lire dans le HTML.

    Vérifier la seule présence de « email-assets » ne suffirait pas — encore
    faut-il que la requête aille les CHERCHER dans `body`. Et son expression
    rationnelle doit rester alignée sur celle du script de migration : une
    vérification plus large signalerait des fichiers que la migration ne
    réécrit jamais, une plus étroite en raterait.
    """
    src = code_seul(DRILL)
    assert "regexp_matches" in src and "email_templates" in src, (
        "Les images des modèles ne sont plus extraites du HTML de email_templates.body."
    )
    assert "/email-assets/" in src, "Le motif ne cible plus le bucket email-assets."

    migration = lire(BACKEND / "scripts" / "migrate_storage_to_ovh.py")
    assert "/email-assets/" in migration, (
        "Le script de migration ne réécrit plus ces images : les deux motifs "
        "doivent rester alignés."
    )


def test_la_repetition_ne_touche_jamais_au_depot_vivant():
    """Elle extrait dans un répertoire jetable. Écrire dans $FICHIERS
    remplacerait des CV de production par une version restaurée — un script de
    restauration qui se trompe de cible est pire que pas de sauvegarde."""
    src = code_seul(DRILL)
    for ligne in src.splitlines():
        if "tar -xf" in ligne:
            assert '-C "$EXTRAIT"' in ligne, (
                f"L'extraction ne vise plus le répertoire jetable : {ligne.strip()}"
            )
            assert "$FICHIERS" not in ligne, "L'extraction vise le dépôt VIVANT."


# ── Une seule valeur, quatre fichiers ──────────────────────────────────────

def test_tous_les_fichiers_designent_le_meme_depot():
    """FILES_DIR (sauvegarde + répétition), Environment= (unité systemd),
    LOCAL_STORAGE_DIR (config.py, .env.example). Cinq endroits, une valeur.

    Deux valeurs qui divergent ne CASSENT rien : la sauvegarde tourne, elle est
    verte, et elle archive un répertoire vide. C'est le mode de panne le plus
    silencieux de tout ce chantier, donc celui qu'un test doit tenir.
    """
    arbre = ast.parse(lire(CONFIG))
    defaut_config = None
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.AnnAssign) and isinstance(noeud.target, ast.Name)
                and noeud.target.id == "local_storage_dir"
                and isinstance(noeud.value, ast.Constant)):
            defaut_config = noeud.value.value
    assert defaut_config == DEPOT, (
        f"config.py:local_storage_dir vaut {defaut_config!r} et non {DEPOT!r} — "
        f"la sauvegarde viserait un autre répertoire que l'application."
    )
    for chemin in (BACKUP, DRILL):
        assert f'FILES_DIR:-{DEPOT}' in lire(chemin), (
            f"{chemin.name} ne vise plus {DEPOT}."
        )
    assert f"Environment=FILES_DIR={DEPOT}" in lire(UNITE_BACKUP), (
        "uti-backup.service ne transmet plus FILES_DIR : le script retomberait "
        "sur son défaut, qui peut avoir divergé."
    )
    assert f"LOCAL_STORAGE_DIR={DEPOT}" in lire(ENV_EXEMPLE), (
        ".env.example annonce un autre répertoire que celui qui est sauvegardé."
    )


def test_le_depot_ne_vit_ni_dans_larbre_git_ni_sous_home():
    """~/app est le dépôt git que deploy.sh remplace à chaque mise à jour : des
    CV de production y seraient effacés par un déploiement. /home est par
    ailleurs le répertoire dont RUNBOOK.md rappelle qu'il n'est pas lisible par
    tous les services. /var/lib est l'emplacement prévu pour l'état applicatif
    persistant, et c'est ce que la sauvegarde vise."""
    assert DEPOT.startswith("/var/lib/"), (
        "Le dépôt de fichiers a été déplacé hors de /var/lib."
    )
    assert "/app/" not in DEPOT and not DEPOT.startswith("/home/"), (
        "Le dépôt de fichiers vit dans l'arbre déployé : deploy.sh l'effacerait."
    )


def test_le_runbook_dit_quoi_faire_des_fichiers_le_jour_du_sinistre():
    """C'est le document qu'on ouvrira en panique. S'il ne dit pas comment
    remettre les fichiers en place et avec quels droits, la base reviendra seule
    et l'application affichera des liens morts."""
    src = lire(RUNBOOK)
    for attendu in (DEPOT, "uti-fichiers-", "chown"):
        assert attendu in src, (
            f"La procédure de reprise ne mentionne plus « {attendu} » : les "
            f"fichiers ne reviendraient pas, ou reviendraient avec les mauvais droits."
        )


# ── Ajoutés après la revue adversariale du chantier ─────────────────────────

def test_les_fichiers_sont_redeposes_meme_sans_changement():
    """Sans redépôt périodique, la garantie s'annule toute seule.

    En régime de croisière — 11 utilisateurs, des fichiers qui ne bougent plus —
    l'empreinte du répertoire ne change jamais, donc aucun redépôt. Or le
    conteneur hors-site a un verrou d'objet de 30 jours et une politique de
    cycle de vie : passé ce délai, la dernière archive de fichiers EXPIRE. Les
    CV n'existeraient alors plus qu'en un exemplaire, sur le VPS — exactement ce
    que la décision de les y poser avait promis d'éviter.

    Et rien ne l'aurait signalé : la sauvegarde serait restée verte tout du long.
    """
    src = lire(BACKUP)
    assert "REDEPOT_MAX_J" in src, (
        "Le redépôt périodique a disparu de backup_db.sh. L'archive des fichiers "
        "expirera hors-site sans jamais être renouvelée."
    )
    assert re.search(r'redepot_du.*=.*1', src), "La variable de décision du redépôt a disparu."
    assert re.search(r'\|\|\s*\[\s*"\$redepot_du"\s*=\s*"1"\s*\]', src), (
        "Le test d'empreinte n'est plus complété par le test d'âge : le redépôt "
        "ne se déclenchera plus que sur changement de contenu."
    )


def test_un_echec_sur_les_fichiers_ne_tue_pas_la_sauvegarde_de_la_base():
    """`crie` termine le script — il ne doit pas être utilisé dans ce bloc.

    La base est intégralement sauvegardée et déposée AVANT le leg fichiers. Un
    `exit 1` à ce stade sautait la rotation locale ET l'écriture de
    .dernier_succes : la supervision aurait annoncé « la sauvegarde de la BASE
    ne tourne plus », ce qui est faux, et la rotation sautée aurait accumulé
    ~100 Mo d'archives horaires par jour jusqu'à remplir le disque.
    """
    src = lire(BACKUP)
    assert "echec_fichiers()" in src, (
        "La fonction non fatale echec_fichiers a disparu : le leg fichiers "
        "utilise de nouveau une alerte qui termine le script."
    )
    debut = src.index("FICHIERS_ENVOYES=0")
    fin = src.index("# ── Rotation LOCALE")
    bloc = src[debut:fin]
    assert "alerte " not in bloc, (
        "Un `alerte` (donc un exit 1) est revenu dans le bloc fichiers : un "
        "échec partiel y ferait de nouveau passer la sauvegarde de la base pour "
        "morte."
    )


def test_la_repetition_choisit_larchive_par_son_suffixe():
    """`lister "uti/" | tail -1` ne ramène plus le dump de base.

    Le conteneur contient trois familles d'objets, et le tri porte sur la clé
    entière : « uti/fichiers/… » et « uti/conf/… » trient APRÈS « uti/2026/… ».
    Un tail -1 nu rendait donc l'archive des FICHIERS à pg_restore.
    """
    src = lire(DRILL)
    assert re.search(r"lister \"uti/\"[^|]*\|\s*grep '\\\.pgcustom\\\.age\\?\$'", src) \
        or "grep '\\.pgcustom\\.age$'" in src, (
        "La sélection du dump de base ne filtre plus sur le suffixe .pgcustom.age."
    )
    assert "uti-fichiers-.*\\.tar\\.age$" in src, (
        "La sélection de l'archive de fichiers ne filtre plus sur son suffixe."
    )
