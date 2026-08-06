"""
Garde-fous du dispositif de sauvegarde : hors-site, chiffré, éprouvé.

Trois conditions ont été posées pour supprimer le projet Supabase :
  1. les sauvegardes vivent HORS du VPS ;
  2. le cron CRIE quand il échoue ;
  3. une restauration a été faite POUR DE VRAI.
Aucune des trois ne se voit dans un fichier pris isolément, et chacune se dégrade
en silence : une ligne retirée de `backup_db.sh` et les archives repartent en
clair ; un mot changé dans `backup_s3_policy.json` et la clé du VPS peut effacer
l'historique. Rien ne casse — jusqu'au jour où tout est déjà perdu. Ces
vérifications coûtent une seconde de CI.

LECTURE PAR TEXTE ET PAR `ast`, JAMAIS PAR EXÉCUTION.
Même raison que tests/test_storage_acl.py:22 : exécuter ces scripts demanderait
un PostgreSQL, une clé S3 OVH et une clé `age`. Leur absence en CI mettrait le
test en `skip` — c'est-à-dire silencieux — précisément là où on voudrait qu'il
parle. Un garde-fou qui se tait quand l'environnement est incomplet ne garde
rien.
"""
import ast
import json
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1]
RACINE = BACKEND.parent
DEPLOY = BACKEND / "deploy"

BACKUP = DEPLOY / "backup_db.sh"
DRILL = DEPLOY / "restore_drill.sh"
SUPERVISION = DEPLOY / "supervision.sh"
LIB = DEPLOY / "lib_alerte.sh"
S3PY = DEPLOY / "s3_backup.py"
POLICY = DEPLOY / "backup_s3_policy.json"
SETUP = DEPLOY / "setup_backup_offsite.sh"
UNITE_BACKUP = DEPLOY / "uti-backup.service"
TIMER_BACKUP = DEPLOY / "uti-backup.timer"
UNITE_DRILL = DEPLOY / "uti-restore-drill.service"
UNITE_SUPERVISION = DEPLOY / "uti-supervision.service"
RUNBOOK = RACINE / "RUNBOOK.md"

TOUS_LES_SCRIPTS = (BACKUP, DRILL, SUPERVISION, LIB, SETUP, S3PY)
TOUTES_LES_UNITES = (UNITE_BACKUP, TIMER_BACKUP, UNITE_DRILL, UNITE_SUPERVISION)


def lire(chemin: pathlib.Path) -> str:
    return chemin.read_text(encoding="utf-8")


def code_seul(chemin: pathlib.Path) -> str:
    """Le fichier privé de ses lignes de commentaire.

    Les commentaires de ces scripts EXPLIQUENT les défauts qu'ils corrigent :
    ils citent donc forcément « public-read », « DeleteObject » ou la base
    vivante. Un test qui échouerait sur sa propre documentation apprend surtout
    à ne plus rien documenter.
    """
    return "\n".join(
        ligne for ligne in lire(chemin).splitlines() if not ligne.lstrip().startswith("#")
    )


# ── Condition 1 : hors du VPS, et hors d'atteinte du VPS ───────────────────

def test_la_sauvegarde_part_bien_hors_site():
    """Sans dépôt distant, un `rm -rf` ou un rançongiciel emporte la production
    ET ses sauvegardes du même geste : elles sont sur le même disque."""
    src = code_seul(BACKUP)
    assert "s3_backup.py" in src and "envoyer" in src, (
        "backup_db.sh n'appelle plus deploy/s3_backup.py envoyer : les archives "
        "ne quittent plus la machine qu'elles sont censées protéger."
    )


def test_le_depot_hors_site_nutilise_pas_la_cle_de_lapplication():
    """La clé S3 de `backend/.env` (config.py:28-29) sert les CV et les avatars.

    La réutiliser ici serait la faute qui annule le chantier entier : qui prend
    le VPS lit le .env, et avec cette clé efface la production ET l'historique
    des sauvegardes. Le module de dépôt ne doit donc connaître QUE ses propres
    variables BACKUP_S3_*.
    """
    src = lire(S3PY)
    arbre = ast.parse(src)
    importe = {
        n.module for n in ast.walk(arbre) if isinstance(n, ast.ImportFrom) and n.module
    } | {
        alias.name for n in ast.walk(arbre) if isinstance(n, ast.Import) for alias in n.names
    }
    assert "config" not in importe, (
        "deploy/s3_backup.py importe config : il verrait settings.s3_access_key, "
        "la clé de l'application. C'est exactement le mélange que ce fichier existe "
        "pour empêcher."
    )
    # Sur l'ARBRE, pas sur le texte : la docstring de ce module explique
    # justement pourquoi il n'utilise pas `s3_access_key`, et un test qui
    # échouerait sur cette explication apprendrait à ne plus l'écrire.
    lues: set[str] = set()
    for n in ast.walk(arbre):
        cible = None
        if isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ":
            cible = n.slice
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "get" and ast.unparse(n.func.value) == "os.environ"):
            cible = n.args[0] if n.args else None
        if isinstance(cible, ast.Constant) and isinstance(cible.value, str):
            lues.add(cible.value)
    assert lues, "deploy/s3_backup.py ne lit plus aucune variable d'environnement."
    for nom in lues:
        assert nom.startswith("BACKUP_S3_"), (
            f"deploy/s3_backup.py lit « {nom} » : seules les variables BACKUP_S3_* "
            f"lui sont permises. La clé applicative (S3_*) et la clé de sauvegarde "
            f"doivent rester deux clés différentes, sinon une compromission du VPS "
            f"emporte la production ET l'historique."
        )


def test_la_cle_du_vps_ne_peut_pas_supprimer():
    """La politique S3 attachée au déposant ne doit accorder aucun droit
    d'effacement, ni aucun droit de DÉSARMER les protections avant d'effacer.

    C'est ce qui distingue « les sauvegardes sont ailleurs » de « les
    sauvegardes sont protégées ».
    """
    politique = json.loads(lire(POLICY))
    actions = [
        a
        for st in politique["Statement"]
        for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
    ]
    assert "s3:PutObject" in actions, "Le déposant ne peut plus rien déposer."
    interdits = (
        "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucket",
        "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration",
        "s3:PutObjectLockConfiguration", "s3:BypassGovernanceRetention", "s3:*",
    )
    for action in actions:
        assert action not in interdits, (
            f"« {action} » est accordé à la clé qui vit sur le VPS. Une "
            f"compromission du VPS pourrait alors effacer l'historique des "
            f"sauvegardes — le scénario même contre lequel le hors-site existe."
        )
    for st in politique["Statement"]:
        assert st["Effect"] == "Allow", (
            "Un Deny explicite ici serait trompeur : OVH n'applique pas le refus "
            "implicite au PROPRIÉTAIRE du conteneur. La protection vient de "
            "l'utilisateur distinct + du verrou d'objet, pas d'un Deny."
        )


def test_aucun_script_du_kit_nappelle_delete_object():
    """Même sans le droit S3, un appel de suppression dans le code serait une
    invitation : il suffirait d'élargir la politique un jour « pour dépanner »
    pour que le geste destructeur soit déjà écrit et prêt à s'exécuter.

    La rotation hors-site est posée CÔTÉ SERVEUR (cycle de vie + verrou d'objet,
    setup_backup_offsite.sh) précisément pour qu'aucun script du VPS n'ait à
    savoir effacer.
    """
    for chemin in (S3PY, BACKUP, DRILL, SUPERVISION):
        code = code_seul(chemin)
        for interdit in ("delete_object", "delete_objects", "delete_bucket"):
            assert interdit not in code, (
                f"{chemin.name} appelle {interdit}() : un script capable "
                f"d'effacer l'historique est un script qu'un attaquant peut "
                f"détourner pour effacer l'historique."
            )


def test_le_conteneur_est_cree_avec_le_verrou_dobjet():
    """Object Lock ne peut PAS être ajouté après coup à un conteneur existant.

    C'est la seule décision de ce dispositif qui ne se rattrape pas : un
    conteneur créé sans le drapeau devra être remplacé. Le script doit donc le
    poser à la création, en mode COMPLIANCE — le seul que même un administrateur
    ne contourne pas.
    """
    src = code_seul(SETUP)
    assert "ObjectLockEnabledForBucket=True" in src, (
        "setup_backup_offsite.sh crée le conteneur sans verrou d'objet. "
        "Irréversible : il faudra en créer un autre."
    )
    assert '"COMPLIANCE"' in src, (
        "Le mode GOVERNANCE se contourne avec s3:BypassGovernanceRetention. "
        "Face à une clé volée, seul COMPLIANCE tient."
    )
    assert "NoncurrentVersionExpiration" in src, (
        "Le versionnage est imposé par le verrou : sans expiration des versions "
        "non courantes, rien n'est jamais supprimé et la facture monte sans fin."
    )


# ── Condition 2 : ça crie, y compris quand le VPS est mort ─────────────────

def test_lalerte_ne_passe_pas_par_la_file_demails():
    """`services/email_outbox` stocke les messages EN BASE.

    Or ce qu'on annonce ici, c'est justement une panne de la base ou de sa
    sauvegarde. Un canal de secours qui dépend de ce qu'il surveille ne
    prévient jamais. L'envoi doit être direct (services/email.py:send_email).
    """
    src = lire(LIB)
    assert "email_outbox" not in code_seul(LIB), (
        "lib_alerte.sh est repassé par la file d'e-mails, qui vit dans la base "
        "dont il annonce la panne."
    )
    assert "from services.email import send_email" in src, (
        "lib_alerte.sh n'utilise plus services/email.py : une seconde "
        "implémentation d'envoi finira par diverger de la première."
    )


def test_le_chien_de_garde_externe_est_branche():
    """Si le VPS entier est mort, plus rien ici ne peut émettre d'alerte : c'est
    une impossibilité de construction, pas un oubli. Seul un service tiers qui
    ATTEND un signal régulier détecte ce silence-là."""
    assert "ping_garde" in lire(LIB) and "HEALTHCHECK_URL" in lire(LIB), (
        "lib_alerte.sh n'a plus de fonction de ping : une panne totale du VPS "
        "ne préviendrait plus personne."
    )
    for chemin in (BACKUP, DRILL, SUPERVISION):
        assert "ping_garde" in code_seul(chemin), (
            f"{chemin.name} ne signale plus sa vie au chien de garde externe."
        )


def test_le_ping_de_supervision_nest_envoye_que_si_tout_est_vert():
    """Un ping envoyé quoi qu'il arrive transformerait le chien de garde en
    simple détecteur de machine allumée. Envoyé seulement sur succès, son
    silence couvre d'un coup trois pannes : VPS mort, supervision morte,
    anomalie qui dure."""
    src = code_seul(SUPERVISION)
    bloc = src.split('if [ "$ROUGE" -eq 0 ]')
    assert len(bloc) == 2, "Le test final sur $ROUGE a disparu de supervision.sh."
    assert 'ping_garde ""' in bloc[1], (
        "Le ping de succès n'est plus dans la branche « tout est vert » : le "
        "chien de garde se tairait alors même qu'une anomalie dure."
    )


def test_la_sauvegarde_refuse_de_partir_en_clair():
    """Fail-closed voulu : les archives contiennent des CV, des adresses e-mail
    et les secrets TOTP en clair de profiles.mfa_secret. Déposer cela chez un
    tiers sans chiffrement serait pire que ne rien déposer, parce que ça se voit
    moins. Sans AGE_RECIPIENT, le script doit ÉCHOUER, pas continuer."""
    src = code_seul(BACKUP)
    assert "AGE_RECIPIENT" in src, "backup_db.sh ne chiffre plus les archives."
    # La branche « pas de clé publique » doit mener à alerte/crie, pas à un envoi.
    branche = re.search(r"if \[ -n \"\$\{AGE_RECIPIENT:-\}\" \];.*?\nfi\n", src, re.S)
    assert branche, "La garde sur AGE_RECIPIENT a disparu de backup_db.sh."
    assert "alerte" in branche.group(0).split("else")[-1], (
        "Sans AGE_RECIPIENT, backup_db.sh ne crie plus : il enverrait des données "
        "personnelles non chiffrées chez un tiers, ou déposerait en silence."
    )


# ── Condition 3 : une restauration éprouvée, jamais sur la base vivante ────

def test_la_repetition_ne_peut_pas_viser_la_base_vivante():
    """Un script de restauration qui se trompe de cible est la seule chose au
    monde qui soit pire que pas de sauvegarde du tout : il DÉTRUIT la production
    en croyant la protéger. Trois garde-fous indépendants, parce qu'un seul
    finit toujours par être contourné par une « petite modification »."""
    src = code_seul(DRILL)
    assert 'CIBLE="uti_drill_' in src, (
        "Le nom de la base jetable ne porte plus le marqueur _drill_."
    )
    assert "*_drill_*)" in src, (
        "Le refus explicite sur un nom de cible sans _drill_ a disparu."
    )
    assert '[ "$CIBLE" != "$BASE" ]' in src, (
        "restore_drill.sh ne compare plus la cible à la base VIVANTE."
    )
    assert "trap nettoyer EXIT" in src, (
        "Sans trap, chaque échec laisse une base orpheline : au bout de quelques "
        "mois le disque se remplit, et un disque plein corrompt le VPS entier."
    )
    # pg_restore ne doit jamais viser $BASE.
    for ligne in src.splitlines():
        if "pg_restore" in ligne and "-d" in ligne:
            assert '-d "$BASE"' not in ligne, f"pg_restore vise la base vivante : {ligne.strip()}"


def test_la_repetition_echoue_sur_la_moindre_erreur():
    """Sans --exit-on-error, pg_restore signale les erreurs et rend 0.

    On validerait alors des restaurations partielles pendant des mois, et la
    répétition hebdomadaire dirait « tout va bien » sur une base amputée.
    """
    assert "--exit-on-error" in code_seul(DRILL), (
        "pg_restore sans --exit-on-error rend 0 même en cas d'erreur : la "
        "répétition validerait des restaurations partielles."
    )


def test_la_repetition_compare_toutes_les_tables_pas_un_echantillon():
    """Compter quelques tables choisies à la main laisse passer la perte d'une
    table dont on n'aurait pas pensé à parler — et c'est toujours celle-là."""
    src = code_seul(DRILL)
    assert "query_to_xml" in src and "pg_class" in src, (
        "restore_drill.sh ne compte plus TOUTES les tables : la comparaison "
        "porterait sur un échantillon choisi à l'avance."
    )
    assert "comm -23" in src, (
        "La détection des tables ABSENTES de la restauration a disparu. Une "
        "table perdue passerait pour une table à zéro ligne."
    )


def test_la_repetition_verifie_le_contenu_pas_seulement_le_volume():
    """Des comptes justes sur des colonnes vides passeraient tous les
    comptages. On vérifie donc que ce qui permet de SE CONNECTER a survécu :
    sans cela, la base restaurée est complète et personne ne peut y entrer."""
    src = code_seul(DRILL)
    assert "user_credentials" in src and "argon2id" in src, (
        "restore_drill.sh ne vérifie plus les empreintes argon2id "
        "(migrations/0019_auth_maison.sql:66) : une restauration où plus personne "
        "ne peut se connecter serait déclarée conforme."
    )
    assert "contype='f'" in src, (
        "Le contrôle des clés étrangères a disparu : une FK non recréée laisse "
        "entrer des lignes orphelines, et la divergence n'apparaît qu'en prod."
    )


def test_la_cle_privee_nest_jamais_sur_le_vps_automatiquement():
    """Le mode --hors-site exige la clé PRIVÉE age. L'automatiser reviendrait à
    poser cette clé sur le VPS — ce qui annulerait la seule propriété qui rend
    les archives sûres chez un tiers : que cette machine ne sache pas les
    relire. La répétition hors-site reste donc manuelle et trimestrielle."""
    unite = lire(UNITE_DRILL)
    assert "--hors-site" not in re.sub(r"^#.*$", "", unite, flags=re.M), (
        "uti-restore-drill.service lance le mode --hors-site : la clé PRIVÉE "
        "devrait vivre sur le VPS, et une compromission lirait tout l'historique."
    )
    assert "AGE_IDENTITY" not in re.sub(r"^#.*$", "", unite, flags=re.M), (
        "AGE_IDENTITY (clé privée) est référencée dans une unité systemd."
    )


# ── Rien en dur, nulle part ────────────────────────────────────────────────

def test_aucun_secret_en_dur_dans_le_kit():
    """Une clé privée age, un secret S3 ou un UUID de sonde committé, c'est un
    secret publié : le dépôt est public (main.py:170 le rappelle pour le SHA)."""
    motifs = {
        "clé privée age": r"AGE-SECRET-KEY-1[A-Z0-9]{10}",
        "clé publique age réelle": r"age1[a-z0-9]{50,}",
        "UUID de sonde healthchecks": r"hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}",
        "secret S3": r"(ACCESS|SECRET)_KEY\s*=\s*[A-Za-z0-9/+]{16,}",
    }
    for chemin in TOUS_LES_SCRIPTS + TOUTES_LES_UNITES + (POLICY,):
        contenu = lire(chemin)
        for nom, motif in motifs.items():
            trouve = re.search(motif, contenu)
            assert not trouve, f"{chemin.name} contient un {nom} en dur : {trouve.group(0)[:24]}…"


def test_les_secrets_arrivent_par_environmentfile():
    """`systemctl cat` est lisible par tous les utilisateurs du VPS ; un
    EnvironmentFile en 600 ne l'est pas. Mettre BACKUP_S3_SECRET_KEY dans
    l'unité elle-même publierait la clé à tout compte local."""
    for unite in (UNITE_BACKUP, UNITE_DRILL, UNITE_SUPERVISION):
        src = lire(unite)
        assert "EnvironmentFile=" in src, f"{unite.name} n'utilise plus d'EnvironmentFile."
        for ligne in src.splitlines():
            if ligne.startswith("Environment=") and "SECRET" in ligne.upper():
                raise AssertionError(
                    f"{unite.name} porte un secret en clair dans l'unité : {ligne}"
                )


def test_la_cle_publique_age_est_bien_un_gabarit():
    """Elle DOIT rester un marqueur à remplacer : une vraie clé publique
    committée ferait chiffrer les sauvegardes de production vers une clé dont
    personne ne détient plus la privée."""
    src = lire(UNITE_BACKUP)
    ligne = next(l for l in src.splitlines() if l.startswith("Environment=AGE_RECIPIENT="))
    assert "REMPLACER" in ligne.upper(), (
        "uti-backup.service contient une clé publique age concrète. Si sa clé "
        "privée n'est pas celle de Julian, les sauvegardes sont illisibles pour lui."
    )


# ── Cohérence entre ce qui tourne et ce qui est promis ─────────────────────

def test_la_sauvegarde_est_horaire_comme_le_runbook_lannonce():
    """Le RUNBOOK annonce un RPO ≤ 1 h 05. Cette promesse ne tient que si le
    timer est horaire : repasser en quotidien multiplierait la perte maximale
    par 24 sans que le document le dise, et c'est le document qu'on lira le jour
    du sinistre."""
    timer = code_seul(TIMER_BACKUP)
    assert re.search(r"^OnCalendar=hourly\s*$", timer, re.M), (
        "uti-backup.timer n'est plus horaire : le RPO d'une heure annoncé au "
        "RUNBOOK §10.1 devient faux."
    )
    assert re.search(r"RPO.*(1\s*h|une heure)", lire(RUNBOOK), re.I), (
        "Le RUNBOOK n'annonce plus de RPO chiffré."
    )


def test_la_supervision_lit_le_dernier_succes_et_pas_la_date_dun_fichier():
    """La date du dernier .pgcustom ne prouve rien : une exécution interrompue
    en laisse un tout frais, et la supervision dirait « tout va bien » sur une
    sauvegarde qui n'a jamais fini."""
    assert ".dernier_succes" in code_seul(SUPERVISION), (
        "supervision.sh ne lit plus le marqueur de dernier SUCCÈS."
    )
    assert ".dernier_succes" in code_seul(BACKUP), (
        "backup_db.sh n'écrit plus son marqueur de succès : la supervision "
        "surveillerait un fichier qui n'existe pas."
    )


def test_la_supervision_attend_401_de_postgrest_pas_200():
    """install_db.sh ne définit pas db-anon-role (test_deploy_db_kit.py:53) :
    401 sans jeton est le comportement CORRECT. Un 200 signifierait que la base
    est lisible sans authentification — pire qu'une panne, et invisible pour
    une sonde qui se contenterait de « le service répond »."""
    src = code_seul(SUPERVISION)
    assert "401)" in src and "200)" in src, (
        "supervision.sh ne distingue plus 401 (attendu) de 200 (base ouverte "
        "sans authentification)."
    )


def test_la_supervision_ninonde_pas_la_boite_mail():
    """Toutes les 15 min, une anomalie qui dure produirait 96 e-mails par jour.
    Au bout de deux jours ils sont filtrés en « Autres », et l'alerte suivante —
    la vraie — ne sera pas lue. Une alerte qu'on n'ouvre plus est pire qu'une
    absence d'alerte, parce qu'on croit être couvert."""
    src = code_seul(SUPERVISION)
    assert "RAPPEL_MIN" in src, "L'anti-répétition a disparu de supervision.sh."
    assert "resoudre" in src, (
        "Le retour à la normale n'est plus annoncé : on ne saurait jamais si le "
        "silence veut dire « réparé » ou « la supervision est morte aussi »."
    )


def test_le_runbook_contient_une_procedure_de_reprise_chiffree():
    """C'est le document qu'on ouvrira en panique, sur un téléphone, avec un VPS
    qui n'existe plus. S'il n'a pas de RTO chiffré et d'ordre des étapes, il ne
    sert à rien au moment où il devrait servir."""
    src = lire(RUNBOOK)
    assert "Reprise après sinistre" in src, "La section de reprise a disparu du RUNBOOK."
    assert re.search(r"\bRTO\b", src) and re.search(r"\bRPO\b", src), (
        "Le RUNBOOK n'annonce plus de RTO/RPO chiffrés."
    )
    for attendu in ("age -d -i", "roles_postgrest.sql", "post_bascule_check.sh"):
        assert attendu in src, (
            f"La procédure de reprise ne mentionne plus « {attendu} ». Les rôles "
            f"sont des objets de CLUSTER, absents du dump : les oublier laisse "
            f"une base restaurée que PostgREST ne peut pas lire."
        )
