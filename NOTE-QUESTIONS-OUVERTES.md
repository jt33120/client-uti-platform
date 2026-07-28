# Questions ouvertes — modèle, amorçage, client final, fiabilité

> Suite du §6 de `BENCHMARK_MARCHE.md`. Ces quatre questions débordent le
> périmètre technique et conditionnent la valeur de tout le reste du plan.
> Elles appellent une décision, pas du code.
>
> **Date :** 28 juillet 2026. Données de marché issues du scan du 27 juillet
> (8 packs, 108 acteurs, sources en annexe du benchmark).

---

## 1. La question préalable, qui commande les trois autres

Le §6 demandait « qui paie, combien ». En relisant le code, je crois que la
question est mal posée — et qu'il faut d'abord trancher celle-ci :

> **La plateforme est-elle l'outil interne qui rend UTI meilleure sur son propre
> métier de placement, ou un produit vendu à d'autres ESN ?**

Ce ne sont pas deux versions de la même chose. Ce sont deux entreprises.

**Ce que dit le code aujourd'hui.** Le modèle de données suit `tjm_achat`,
`tjm_vente`, la marge, `deal_status` gagnée/perdue, le time-to-fill, le taux de
pourvu. Ce sont les métriques d'un **intermédiaire qui prend une marge sur une
prestation**, pas celles d'un éditeur de logiciel — un éditeur suivrait des
abonnements actifs, du churn, de l'usage par siège. La plateforme est instrumentée
comme un outil interne.

**Ce que ça change, question par question :**

| | Outil interne (hypothèse A) | Produit vendu aux ESN (hypothèse B) |
|---|---|---|
| **Revenu** | La marge de placement, déjà existante. Pas de prix à fixer. | Un abonnement à créer, face à Agrega à 119 €/mois HT. |
| **Concurrent** | Opteamis, Hitechpros — sur le **flux d'AO**, pas sur le logiciel. | Agrega frontalement, sur le logiciel. |
| **Amorçage** | Pas de problème biface : UTI apporte elle-même les AO. Le côté rare est le partenaire avec de bons consultants. | Problème biface complet, et c'est là que meurent ces produits. |
| **Métrique de succès** | Taux de transformation d'UTI, délai de réponse, marge moyenne. | ARR, sièges actifs, churn. |
| **Conformité** | UTI est déployeur et fournisseur pour son usage. | UTI devient fournisseur d'un système haut risque **pour des tiers** — l'enregistrement en base UE et la notice deviennent des livrables commerciaux, pas des formalités. |

**Ma recommandation : assumer A, explicitement, et arrêter d'arbitrer comme si
c'était B.** Trois raisons.

1. **L'avantage concurrentiel d'UTI n'est pas le logiciel, c'est la vitesse de
   réponse à un AO.** Le faisceau technique (score hybride, surlignage du PDF,
   synthèse de vivier) vaut surtout parce qu'il permet à UTI de répondre mieux et
   plus vite que les ESN concurrentes sur le même AO. Vendu à ces mêmes
   concurrentes, il détruit cet avantage.
2. **En B, la compétition est déjà perdue d'avance sur le prix.** Agrega vend le
   score IA décomposé à 119 €/mois HT avec un palier gratuit. BoondManager, qui est
   l'ERP déjà installé chez beaucoup d'ESN, est à 59-99 € HT/manager/mois. Se
   placer là demande une équipe commerciale, un support, une roadmap dictée par des
   clients — pas 1 à 3 personnes.
3. **En A, la moitié des recommandations du benchmark deviennent facultatives.**
   La scorecard partenaire, l'API d'import, le baromètre TJM ne servent qu'à
   fidéliser une offre vendue. En interne, ils ne servent que s'ils améliorent le
   taux de transformation d'UTI — et c'est un test bien plus simple à passer.

**Ce que je ne peux pas savoir**, et qui pourrait renverser l'analyse : s'il existe
une intention commerciale explicite de vendre la plateforme, ou un engagement pris
auprès d'un tiers. `PROJECT_VISION.md` est un fichier **vide** dans le dépôt — la
vision n'est écrite nulle part. C'est le premier document à produire, avant toute
priorisation.

---

## 2. Amorçage biface

**Sous l'hypothèse A, le problème n'existe pas sous la forme décrite.** UTI produit
son propre flux d'AO ; elle n'a pas à attendre une masse critique de donneurs
d'ordre. Le benchmark citait Opteamis (400 AO/mois) comme étalon — c'est un étalon
pertinent pour une place de marché, pas pour un outil interne.

**Le côté réellement rare est le partenaire qui répond vite avec de bons profils.**
Et là, la donnée existe déjà dans la base sans être exploitée : délai entre
notification et première soumission, taux de CV retenus, taux de conflits de
présentation. C'est la recommandation R10 du benchmark — mais son intérêt change de
nature. En interne, elle ne sert pas à « fidéliser des clients » : elle sert à
**savoir à qui envoyer la liste 1**, ce qui est directement un levier de marge.

**À faire, à faible coût :** mesurer avant de construire. Sur les AO déjà passés,
calculer le délai médian de première soumission par partenaire et le taux de
retenus. Si la dispersion entre partenaires est forte, la diffusion étagée
`list_1`/`list_2` est mal calibrée aujourd'hui, et la corriger vaut plus que
n'importe quelle fonctionnalité nouvelle. Si elle est faible, la scorecard ne
servira à rien et il faut la déprioriser. **Une requête SQL répond à cette
question ; ne pas développer avant de l'avoir posée.**

---

## 3. Le client final

C'est l'angle mort le plus coûteux du benchmark, et je le maintiens.

Aujourd'hui, le client final n'a qu'une surface : le lien tokenisé de retour
(`/client-review/:token`), qui lui permet de répondre *intéressé / refusé / à
revoir*. C'est déjà plus que ce que font la plupart des acteurs analysés — et c'est
sous-exploité.

**Ce que le client final juge, quand il compare UTI à une autre ESN sur le même AO,
ce n'est pas la qualité du CV : c'est la qualité de la sélection.** Or UTI possède
exactement ce qu'il faut pour la démontrer : le radar comparé du vivier, la synthèse
de resserrement, le surlignage des preuves dans le CV source. Rien de tout cela ne
lui est montré.

**Recommandation concrète, effort faible :** étendre la page de retour client en
**page de présentation de shortlist**. Pour les 2 à 4 profils envoyés : le radar
comparé, une phrase de justification par profil, et le CV harmonisé. Sans montrer
ni le score /100, ni les profils écartés, ni quoi que ce soit d'interne.

Deux bénéfices distincts : c'est un argument commercial différenciant à coût quasi
nul (tous les composants existent), et cela **matérialise la décision humaine**
— UTI présente une sélection argumentée, pas un classement machine. C'est
exactement la preuve attendue au titre de l'article 22 du RGPD.

**Réserve :** ne rien exposer qui révèle le vivier des partenaires ou les TJM
d'achat. Le périmètre du token doit rester strictement celui des profils envoyés.

---

## 4. Fiabilité d'exécution

C'est la seule des quatre questions qui soit purement technique, et elle porte une
contradiction que le benchmark n'avait pas tranchée.

**Le constat.** Le planificateur (`services/scheduler.py`) est une boucle `asyncio`
in-process sur un uvicorn **mono-worker**. Le rate-limit (`services/ratelimit.py`)
est une fenêtre glissante **en mémoire**, perdue à chaque redémarrage — le code
l'assume explicitement (« pour ce POC, l'in-memory suffit et reste honnête »).

**La contradiction.** Le benchmark invoque cette architecture pour *interdire* de
construire la facturation — à raison. Mais plusieurs recommandations de la vague
« Maintenant » reposent dessus : les relances de conformité partenaire (R5), la
purge des données (R3), les alertes de SLA (R15).

**Ce que ça implique réellement, sans dramatiser.** Le mono-worker n'est pas un
défaut en soi : c'est un choix cohérent à ce volume, et il garantit qu'aucune tâche
planifiée ne s'exécute en double. Les vraies limites sont :

1. **Aucune tâche ne survit à un redémarrage mal placé.** Si le service redémarre
   pendant un tick, l'itération est perdue — sans trace. Pour une relance
   commerciale, c'est acceptable. Pour une relance d'attestation URSSAF expirée,
   ça ne l'est pas.
2. **Impossible de passer à plusieurs workers** sans dédoublonner les envois.
3. **Le rate-limit se réinitialise à chaque déploiement**, ce qui rouvre
   brièvement les endpoints coûteux (appels LLM).

**Recommandation, par ordre de rapport valeur/effort :**

- **Rendre les tâches planifiées idempotentes et traçables** (effort S). Journaliser
  chaque exécution avec sa fenêtre temporelle, et faire dépendre l'action d'un état
  en base plutôt que du fait d'avoir tourné (« relance envoyée le X » plutôt que
  « on est mardi »). Cela seul supprime le problème 1 sans changer d'architecture,
  et c'est un prérequis à R5.
- **Ne pas migrer vers Redis ou un worker externe maintenant** (effort L, bénéfice
  nul au volume actuel). Le déclencheur, c'est le passage à plusieurs workers ou
  l'arrivée d'un engagement contractuel sur un délai — pas avant.
- **Le rate-limit peut rester en mémoire.** Le risque est borné et le coût d'un
  store partagé n'est pas justifié tant qu'il n'y a qu'un worker.

---

## Ce que je propose de faire dans l'ordre

1. **Écrire `PROJECT_VISION.md`** et y trancher A ou B. Tout le reste en dépend, et
   le fichier est vide aujourd'hui.
2. **Poser la requête SQL sur la dispersion des partenaires** (§2). Une réponse,
   deux heures, et elle valide ou invalide une recommandation de taille M.
3. **Étendre la page de retour client en présentation de shortlist** (§3). Meilleur
   rapport valeur/effort de tout le benchmark si l'hypothèse A est retenue.
4. **Rendre les tâches planifiées idempotentes** (§4), avant de brancher la relance
   de conformité partenaire dessus.
