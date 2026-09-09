# Les droits de la clé de sauvegarde — pourquoi ceux-là, et pas d'autres

> **Le hors-site tourne chez Backblaze B2**, pas chez OVH : aller
> directement à la section « Backblaze B2 » plus bas. Les sections OVH
> restent pour le raisonnement, qui n'a pas changé, et pour le jour où le
> conteneur OVH redeviendrait la piste.

Ce fichier accompagne `backup_s3_policy.json`. **Il est séparé parce que JSON
n'a pas de commentaires** : glisser une clé `"_lisez_moi"` dans la politique
risquait de la faire rejeter par l'importateur d'OVH, et une politique rejetée
le jour de l'installation devient une politique qu'on n'installe pas.

## À qui l'attacher

Espace client OVH → *Public Cloud* → *Object Storage* → onglet **Utilisateurs** →
sélectionner `uti-backup-writer` → **Importer une politique JSON**.
Remplacer `NOM_DU_CONTENEUR` par le nom réel (ex. `uti-sauvegardes`) **avant**
l'import.

> ⚠️ **Cette politique ne protège de rien si elle est attachée au propriétaire
> du conteneur.** OVH documente que *« implicit deny is not supported by
> OVHcloud Object Storage if the user is the bucket owner »* : le propriétaire
> conserve l'ACL `FULL_CONTROL`, et un droit non accordé lui reste malgré tout
> ouvert. L'utilisateur qui reçoit cette politique doit donc être un **second**
> utilisateur, distinct de celui qui a créé le conteneur — et c'est la clé de ce
> second utilisateur, seulement, qui a le droit de vivre dans
> `/etc/uti-backup.env` sur le VPS.
> Source : <https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-identity-and-access-management/>

## Ce qui est autorisé

| Action | Pourquoi |
|---|---|
| `s3:PutObject` | Déposer une nouvelle archive — la seule chose que la sauvegarde ait à faire. |
| `s3:GetObject` | Relire ce qu'on vient de déposer. **Ce n'est pas une fuite** : les objets sont chiffrés vers une clé publique `age` dont la clé privée n'est pas sur le VPS. Pouvoir lire un fichier qu'on ne peut pas déchiffrer n'apprend rien. En échange, cela permet la vérification aller-retour de `backup_db.sh`, qui transforme « déposé » en « relisible » — la seule propriété qui compte. |
| `s3:ListBucket`, `s3:GetBucketLocation` | Retrouver la dernière archive pour la répétition hors-site (`restore_drill.sh --hors-site`). |

## Ce qui n'est PAS accordé — et c'est tout le sujet

| Action refusée | Ce qu'elle permettrait |
|---|---|
| `s3:DeleteObject`, `s3:DeleteObjectVersion` | Effacer l'historique depuis le VPS. C'est exactement le geste d'un rançongiciel, et c'est la différence entre « les sauvegardes sont ailleurs » et « les sauvegardes sont protégées ». |
| `s3:PutBucketVersioning`, `s3:PutLifecycleConfiguration`, `s3:PutObjectLockConfiguration` | Désarmer les protections **avant** d'effacer — le contournement évident d'un simple refus de suppression. |
| `s3:BypassGovernanceRetention` | Passer outre le verrou d'objet. (Sans effet ici puisque le verrou est en mode `COMPLIANCE`, que personne ne contourne — mais un mode `GOVERNANCE` posé un jour par erreur redeviendrait dangereux, et ce droit non accordé le rattrape.) |

## Deuxième ligne, indépendante

Le **verrou d'objet en mode `COMPLIANCE`** posé à la création du conteneur
(`setup_backup_offsite.sh`, étape 2) tient même si cette politique tombe — y
compris face à quelqu'un qui obtiendrait la clé du **propriétaire**. OVH :
*« objects cannot be modified or deleted by any user, including administrators,
during the entire retention period »*.
Source : <https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-managing-object-lock>

Les deux couches sont indépendantes, ce qui est le seul intérêt d'en avoir deux.

## Backblaze B2 — le fournisseur réellement utilisé

Le hors-site a été monté chez **Backblaze B2** le 9 septembre 2026, pas chez
OVH : la piste OVH a été abandonnée faute d'accès au compte. Tout ce qui
précède reste vrai *dans l'intention* — mêmes droits accordés, mêmes droits
refusés — mais B2 ne s'administre pas par politique JSON. Cette section-là est
celle qui s'applique aujourd'hui.

### Ce qui change : des capacités, pas une politique

B2 n'attache pas de document JSON à un utilisateur. Chaque **clé
d'application** porte sa propre liste de capacités, figée à sa création : ce
qui n'y figure pas est refusé, sans document à importer.

La clé qui vit dans `/etc/uti-backup.env` doit porter :

    listBuckets,listFiles,readFiles,writeFiles

et **rien d'autre** — en particulier pas `deleteFiles`, ni les capacités
d'écriture de rétention (`writeBucketRetentions`, `writeFileRetentions`), ni
`bypassGovernance`, qui sont les contournements décrits plus haut.

`deleteFiles` n'est nécessaire à rien ici : `deploy/s3_backup.py` n'appelle que
`put_object`, `head_object`, `get_object` et `list_objects_v2` — jamais
`delete_object`. Le retrait de cette capacité ne casse donc aucune sauvegarde,
ni aucune rotation : la rotation est **locale**, et l'historique hors-site est
justement ce qu'on ne veut pas voir disparaître.

> ### ⚠️ Mais cette liste NE SUFFIT PAS, et c'est le point à ne pas manquer
>
> Une première version de cette page affirmait qu'une clé sans `deleteFiles`
> ne peut pas effacer. **C'est faux chez B2**, et la documentation le dit :
> *« `writeFiles` is necessary when you delete a file by name, and
> `deleteFiles` is required when you delete a specific version »*
> (<https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys>).
>
> Or `writeFiles` est **obligatoire pour déposer**. Il n'existe donc
> **aucune liste de capacités** qui laisse la clé écrire sans la laisser
> supprimer par nom. Retirer `deleteFiles` reste utile — cela bloque la
> suppression DÉFINITIVE d'une version — mais présenter cette couche comme
> la protection serait exactement l'erreur que ce fichier dénonce ailleurs :
> une garantie affirmée, jamais éprouvée.
>
> Ce qui protège réellement est le **verrou d'objet**, ci-dessous.

    b2 key create --bucket <conteneur> uti-backup-writer \
      listBuckets,listFiles,readFiles,writeFiles

(sur les versions plus anciennes du client : `b2 create-key --bucket …`. La
console web n'expose qu'un choix grossier « Read and Write », qui **inclut la
suppression** : c'est par là qu'une clé trop puissante arrive sur le VPS sans
qu'on l'ait décidé.)

### La même erreur qu'OVH, sous un autre nom

L'avertissement du haut — *ne jamais poser sur le VPS la clé du propriétaire* —
vaut ici mot pour mot, avec un autre vocabulaire. Chez B2 le piège n'est pas
l'ACL du propriétaire mais la **clé d'application principale** (*master
application key*), celle du compte : elle ignore toute restriction, par
construction. Elle ne doit jamais quitter le gestionnaire de mots de passe.
Seule une clé **restreinte à un conteneur**, avec la liste ci-dessus, a le
droit de vivre dans `/etc/uti-backup.env`.

### Le verrou d'objet — la couche qui porte réellement

Chez OVH, le verrou se pose **à la création du conteneur, et jamais après**.
Cette règle-là a été transposée à B2 par erreur, ce qui faisait conclure que le
conteneur `uti-sauvegardes-1.0`, créé sans verrou, était définitivement perdu
pour cette protection. **B2 ne fonctionne pas comme ça** :

> *You may enable Object Lock on a bucket when creating a new bucket **or on an
> existing bucket**.*
> <https://www.backblaze.com/docs/cloud-storage-enable-object-lock-or-a-legal-hold-on-an-existing-bucket>

Activer le verrou ne suffit pas : il faut ensuite poser une **période de
rétention par défaut**, sans quoi rien n'est immuable. Et cette protection
**n'est pas rétroactive** — les objets déposés AVANT la pose de la rétention
restent supprimables. Ce n'est pas un obstacle ici : une sauvegarde tombe toutes
les heures, donc l'historique protégé se reconstitue en quelques jours.

Choisir la durée de rétention est une décision, pas un réglage : c'est le temps
pendant lequel PERSONNE ne peut effacer — le propriétaire du compte non plus —
et donc aussi le temps que le stockage est facturé. À 300 Ko par archive, le
coût n'est pas le critère ; le critère est le délai au bout duquel on découvre
un sinistre.

### Ce que le contrôle mesure, et ce qu'il devrait mesurer

`post_bascule_check.sh` appelle `delete_object(Bucket, Key)` **sans numéro de
version** : une suppression PAR NOM. Deux conséquences, toutes deux à corriger
avant de croire ce contrôle :

  * retirer `deleteFiles` de la clé ne le fera **pas** passer au vert, puisque
    `writeFiles` suffit à cette forme-là ;
  * sur un conteneur versionné — et activer le verrou active le versionnage —
    une suppression par nom **réussit** en posant un marqueur, tandis que la
    version protégée reste dessous, intacte et récupérable.

Le contrôle rendrait donc « SUPPRESSION ACCEPTÉE » sur une configuration
correctement protégée. La question à poser n'est pas « l'appel échoue-t-il ? »
mais **« la version est-elle encore là après ? »**.

## La vérifier

Une politique qu'on n'a pas essayé de violer n'est qu'une intention. Le
contrôle §4 de `setup_backup_offsite.sh` dépose un objet puis **essaie de le
supprimer** avec la clé du VPS : tant qu'il n'a pas affiché
« suppression REFUSÉE », le hors-site n'est pas en place — il est seulement
ailleurs. `backend/scripts/post_bascule_check.sh` rejoue ce même essai à chaque
passage.
