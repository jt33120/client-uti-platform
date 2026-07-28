# Points à faire valider par le conseil juridique

> **Objet :** questions soulevées par la veille marché de juillet 2026
> (`BENCHMARK_MARCHE.md`) et par la relecture du dossier `compliance/ai-act/`.
>
> **Destinataire :** conseil juridique UTI Group.
> **Rédigé par :** équipe produit · **Date :** 28 juillet 2026.
>
> Ce document n'est pas un avis juridique. Il liste des positions que l'équipe
> produit a prises ou envisage de prendre, avec la source primaire sur laquelle
> elle s'est appuyée, et demande confirmation ou correction avant d'engager des
> développements.
>
> **Rappel du contexte technique.** UTI exploite une plateforme B2B où des ESN
> partenaires soumettent des CV de consultants externes à des appels d'offres
> clients. Un moteur d'IA score et classe ces consultants (score /100, décomposé
> par critère pondéré). L'équipe commerce d'UTI décide ensuite qui présenter au
> client final. Le système est classé haut risque (Annexe III, point 4) par
> décision UTI Group de juin 2026.

---

## A. Deux corrections déjà appliquées au dossier — à confirmer

**A1. Numéro du règlement.** Le dossier citait le règlement **(UE) 2024/1688**.
L'AI Act est le règlement **(UE) 2024/1689** du 13 juin 2024 (JO L, 2024/1689 du
12.7.2024). La correction a été appliquée à `compliance/ai-act/README.md`.
→ *Confirmer qu'aucune autre pièce du dossier ne porte le mauvais numéro.*

**A2. Statut du report « Omnibus ».** Le dossier indiquait un report « provisoire
jusqu'à publication au JO ». Le règlement **(UE) 2026/1744** a été publié au JO L
du **24 juillet 2026** et est entré en vigueur le **27 juillet 2026**. Il reporte
les **sections 1, 2 et 3 du chapitre III** au **2 décembre 2027** pour les
systèmes autonomes de l'Annexe III. Le statut a été passé à « définitif ».
→ *Confirmer la lecture, et notamment le point B1 ci-dessous.*

---

## B. AI Act — questions ouvertes

**B1. Les sections 4 et 5 du chapitre III sont-elles réellement hors du champ du
report ?** La lecture de l'article 113 tel que modifié conduit à penser que le
report vise les sections 1 à 3 uniquement, et que les articles 40 à 49 (normes
harmonisées, évaluation de conformité, **enregistrement en base de données UE**)
ne sont pas décalés. C'est contre-intuitif : cela reviendrait à devoir
s'enregistrer avant que les obligations de fond ne s'appliquent.
→ *Question la plus structurante du dossier : elle conditionne le calendrier de
tout le chantier haut risque. Merci de trancher.*

**B2. Article 86 (droit à explication).** L'article 86 ne figure pas dans la liste
des articles modifiés par le règlement 2026/1744. Son articulation avec le report
n'est donc pas établie : le droit devient-il effectif à la date initiale ou suit-il
le 2 décembre 2027 ?
→ *Impact produit direct : nous prévoyons un PDF d'explication opposable
(recommandation R8). Faut-il le livrer maintenant ou en 2027 ?*

**B3. Périmètre de l'article 50, applicable au 2 août 2026.** Nous avons appliqué
la lecture suivante, à confirmer :
- **Assistant conversationnel** → art. 50(1). Divulgation obligatoire, sans
  exception. *Fait :* le widget est renommé « Assistant IA ».
- **Résumé d'AO et synthèse de vivier** → textes intégralement produits par le
  modèle et affichés à des utilisateurs. *Fait :* mention « généré par IA ».
- **Motif de refus transmis au partenaire** → **non marqué**, au motif que
  l'opérateur le choisit dans une liste ou l'édite, puis le valide explicitement
  avant envoi : il y a contrôle éditorial humain et UTI en assume la
  responsabilité. *Confirmer que ce raisonnement tient.*
- **Score affiché à un commercial interne** → nous considérons qu'un score
  numérique destiné à un utilisateur professionnel interne n'est pas un « contenu
  de synthèse » au sens de l'art. 50. *Confirmer.*
- **CV harmonisé imprimé et envoyé au client final** → marqué dans l'application
  (pour l'opérateur) mais **non marqué sur le document sorti**. Le document est
  relu par l'opérateur et diffusé sous la marque Groupement-IT.
  *Question : un document commercial reformaté par IA, diffusé à un tiers, doit-il
  porter une mention ? Et qu'implique l'échéance du 2 décembre 2026 sur le
  marquage lisible par machine pour ce cas précis ?*

**B4. Qualification des rôles.** Le dossier retient fournisseur + déployeur +
distributeur. Point à sécuriser : le modèle est appelé via **OpenRouter**, qui
route vers des fournisseurs tiers. Nous retenons que l'article 25(1) ne transfère
pas la qualité de fournisseur à l'éditeur du modèle dès lors qu'UTI met le système
en service sous son propre nom.
→ *Confirmer, et préciser la qualification d'OpenRouter dans la chaîne.*

---

## C. RGPD

**C1. Article 22 — base de licéité et réalité de l'intervention humaine.**
Nous retenons que l'art. 22 n'est pas une interdiction absolue (exceptions de
l'art. 22(2)) mais que la CNIL considère qu'une décision devient « exclusivement
automatisée » **par construction** lorsque les candidatures reléguées ne sont pas
réellement examinées. Notre architecture (l'IA classe, le commerce décide) est
conforme dans son principe.
→ *Quelle base d'exception retenir ? Et quel niveau de preuve de l'intervention
humaine attendre ? Nous instrumentons la mesure (temps de décision, profils
réellement ouverts, taux de repêchage hors top N) — est-ce le bon indicateur ?*

**C2. Qui doit informer le consultant ?** Le consultant est le plus souvent salarié
ou sous-traitant d'un partenaire et n'a **aucune relation directe** avec UTI. Nous
avons écarté le fondement de l'article **L.1221-8 du code du travail** : il vise le
« candidat à un emploi », dans le chapitre « Formation du contrat de travail », or
le consultant est positionné sur une **mission**, pas sur un emploi.
Nous retenons RGPD art. 13/14, 15(1)(h), 22 et AI Act art. 26(7).
→ *Confirmer l'écartement de L.1221-8. Et trancher la qualification avec les
partenaires : responsables conjoints (art. 26) ou responsables distincts ? La
réponse détermine la clause à insérer au contrat-cadre partenaire.*

**C3. Chaîne de sous-traitance du LLM.** OpenRouter route vers des fournisseurs
tiers potentiellement hors UE. Questions : DPA à obtenir et auprès de qui,
localisation effective du traitement, durée de rétention côté fournisseur,
garanties de transfert (chapitre V), inscription au registre des traitements.
→ *Faut-il restreindre le routage à des fournisseurs UE ? C'est faisable
techniquement, avec un coût sur le choix des modèles.*

**C4. Durées de conservation.** Troisième axe annoncé des contrôles CNIL 2026,
aujourd'hui non implémenté. Nous prévoyons une purge automatique.
→ *Quelles durées retenir par catégorie : CV déposés, consultants n'ayant jamais
été soumis, résultats de matching, journaux d'audit ? Le journal d'audit AI Act
(art. 12) et la minimisation RGPD ont des exigences opposées : arbitrage à rendre.*

---

## D. Droit social — sous-traitance de prestation intellectuelle

**D1. Obligation de vigilance — périmètre exact.** Nous retenons :
- seuil de **5 000 € HT par opération** (art. R.8222-1), et **non** par cumul annuel
  avec un même prestataire — contrairement à ce qu'affirment plusieurs éditeurs de
  solutions de conformité ;
- vérification **à la conclusion puis tous les six mois** jusqu'à la fin
  d'exécution (art. D.8222-5) ;
- **deux pièces** : attestation de vigilance URSSAF (art. L.243-15 CSS) de moins de
  six mois **dont l'authenticité doit être vérifiée auprès de l'URSSAF**, et un
  justificatif d'immatriculation ;
- la **liste nominative des salariés étrangers** relève d'un régime **distinct**
  (art. L.8254-1 et D.8254-2), exigible **à la conclusion seulement**.
→ *Confirmer les quatre points. Notre modèle de données en dépend directement
(deux workflows séparés, deux périodicités).*

**D2. À quel acte rattacher le contrôle ?** Nous avions envisagé de bloquer l'envoi
d'un CV au client final tant que l'attestation du partenaire n'est pas valide. Nous
y avons renoncé : l'obligation se rattache au **contrat de prestation**, pas à la
présentation d'une candidature.
→ *Confirmer. Et préciser l'acte déclencheur exact dans notre cas : contrat-cadre,
bon de commande, ou premier jour d'exécution ?*

**D3. Prêt de main-d'œuvre illicite et marchandage.** Nous avons **écarté** l'idée
d'une règle automatique du type « facturation au temps passé au-delà du coût
salarial chargé = opération lucrative ». Motif : ce critère ne figure pas à
l'article L.8241-1, qui définit le but non lucratif *a contrario* (ne refacturer
que salaires, charges et frais professionnels). Une telle règle produirait des faux
positifs sur toute prestation d'ESN légale, y compris celles d'UTI.
Nous prévoyons à la place des **champs de preuve** dans le bon de commande
(livrables formulés en résultats, qualification moyens/résultat, responsable
hiérarchique côté partenaire) et une alerte de dérive de mission.
→ *Confirmer l'analyse, et valider la liste des champs de preuve avant
développement. C'est le point où une erreur produit coûterait le plus cher.*

**D4. Signature électronique.** Pour le contrat-cadre partenaire, nous envisageons
une signature **qualifiée** (seule à emporter la présomption de fiabilité de
l'art. 1367 du code civil, décret 2017-1416) et une signature **avancée** pour les
bons de commande.
→ *Ce niveau est-il nécessaire pour le contrat-cadre, ou l'avancée suffit-elle
compte tenu de nos volumes et de notre exposition au risque ?*

---

## E. Facturation électronique

Obligation de **réception** par plateforme agréée au **1er septembre 2026** pour
toute entreprise assujettie à la TVA ; obligation d'**émission** à la même date
pour les grandes entreprises et ETI.
→ *Il s'agit d'abord d'une obligation d'entreprise pour UTI, pas d'un chantier
produit. Confirmer le régime applicable à UTI (taille), et l'échéance d'émission
qui nous concerne. La plateforme n'a pas vocation à devenir une plateforme
agréée — elle s'y raccordera le moment venu.*

---

## Synthèse des décisions bloquées sur cet avis

| # | Décision produit en attente | Bloque |
|---|---|---|
| B1 | Calendrier du chantier haut risque | Planification 2026-2027 |
| B2 | Livrer le PDF d'explication maintenant ou en 2027 | Recommandation R8 |
| B3 | Marquage des documents sortants | Fin du chantier art. 50 |
| C2 | Responsables conjoints ou distincts | Clause du contrat-cadre partenaire |
| C3 | Restreindre le routage LLM à l'UE | Architecture d'appel du modèle |
| C4 | Durées de conservation par catégorie | Développement de la purge |
| D1 | Modèle de données de la conformité partenaire | Recommandation R5 |
| D3 | Champs de preuve anti-marchandage | Recommandation R17 |
