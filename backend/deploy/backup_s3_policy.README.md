# `backup_s3_policy.json` — pourquoi ces droits, et pas d'autres

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

## La vérifier

Une politique qu'on n'a pas essayé de violer n'est qu'une intention. Le
contrôle §4 de `setup_backup_offsite.sh` dépose un objet puis **essaie de le
supprimer** avec la clé du VPS : tant qu'il n'a pas affiché
« suppression REFUSÉE », le hors-site n'est pas en place — il est seulement
ailleurs. `backend/scripts/post_bascule_check.sh` rejoue ce même essai à chaque
passage.
