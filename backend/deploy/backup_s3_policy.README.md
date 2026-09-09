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

La clé qui vit dans `/etc/uti-backup.env` doit porter **exactement** :

    listBuckets,listFiles,readFiles,writeFiles

et **rien d'autre**. En particulier pas `deleteFiles`, qui est la capacité qui
correspond à `s3:DeleteObject` ci-dessus, ni les capacités d'écriture de
rétention (`writeBucketRetentions`, `writeFileRetentions`) ni `bypassGovernance`,
qui sont les contournements décrits plus haut.

`deleteFiles` n'est nécessaire à rien : `deploy/s3_backup.py` n'appelle que
`put_object`, `head_object`, `get_object` et `list_objects_v2` — jamais
`delete_object`. Le retrait de cette capacité ne casse donc aucune sauvegarde,
et aucune rotation : la rotation est **locale**, et l'historique hors-site est
justement ce qu'on ne veut pas voir disparaître.

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

### Le verrou d'objet

B2 propose aussi un verrou d'objet. **Non vérifié sur le conteneur en place**,
et à ne pas supposer : le conteneur `uti-sauvegardes-1.0` a été créé le
9 septembre depuis la console web sans que ce point soit tranché. Vérifier son
état dans la documentation B2 avant d'écrire ici qu'il protège quelque chose —
la première couche (une clé qui ne sait pas supprimer) est indépendante de
celle-là et se pose tout de suite.

## La vérifier

Une politique qu'on n'a pas essayé de violer n'est qu'une intention. Le
contrôle §4 de `setup_backup_offsite.sh` dépose un objet puis **essaie de le
supprimer** avec la clé du VPS : tant qu'il n'a pas affiché
« suppression REFUSÉE », le hors-site n'est pas en place — il est seulement
ailleurs. `backend/scripts/post_bascule_check.sh` rejoue ce même essai à chaque
passage.
