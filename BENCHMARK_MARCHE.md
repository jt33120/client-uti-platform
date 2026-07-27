# Veille marché & recommandations produit

> Scan concurrentiel du secteur, standards de fait, angles morts réglementaires
> et recommandations priorisées pour la plateforme Groupement-IT.
>
> **Date du scan :** 27 juillet 2026 · **Méthode :** BMAD *Analysis phase* (Deep Recon
> par packs typés) · 8 packs de recherche, 108 acteurs analysés, 269 sources,
> 55 affirmations soumises à vérification adverse en source primaire.

---

## 0. Ce qu'il faut retenir en cinq minutes

Trois affirmations que l'on tenait pour acquises ont été **réfutées** par la
vérification en source primaire. Elles changent la stratégie, donc elles ouvrent
ce document.

**1. Le scoring IA explicable n'est pas notre différenciateur.**
Agrega (`agrega.io`) vend déjà, dans un abonnement à **119 €/mois HT pour 5
utilisateurs** (avec un palier *Guest* gratuit), un assistant IA qui affiche « un
score de compatibilité pour chaque proposition de profil, sous forme de
pourcentage », décomposé sur **7 critères consultables** (compétences, tarif,
langues, expérience, disponibilité, formation, localisation) avec points forts et
points faibles. Même triangle métier que nous : ESN donneuse d'ordre → partenaires
→ profils. Le score décomposé est un **standard de fait**, pas une avance. La
question en soutenance ne sera pas « avez-vous un score » mais « sur quoi
repose-t-il, et comment le justifiez-vous auprès du consultant ».

**2. L'échéance AI Act d'août 2026 n'existe plus.**
Le règlement **(UE) 2026/1744** (« Digital Omnibus on AI », JO L du 24/07/2026,
en vigueur le 27/07/2026) reporte les **sections 1, 2 et 3 du chapitre III** au
**2 décembre 2027** pour les systèmes autonomes de l'annexe III. De nombreuses
sources en ligne affichent encore le 2 août 2026 : elles sont périmées. Nous avons
**16 mois de plus**, pas six jours. Mobiliser l'équipe maintenant sur un dossier
annexe IV complet serait une erreur d'allocation.

**3. Ce qui est réellement opposable aujourd'hui est ailleurs**, et n'est pas
couvert par le produit : l'obligation de vigilance URSSAF (sanction financière
immédiate), l'article 22 du RGPD, et les contrôles CNIL 2026 dont le recrutement
est une thématique prioritaire — avec « les cabinets de recrutement » nommément
cités parmi les cibles.

**Et un défaut interne trouvé en cours de route, plus urgent que tout le reste :**
`consent_at` — la preuve horodatée du consentement RGPD d'un consultant — est
traitée comme une *colonne optionnelle* (`routers/consultants.py:52`). Si elle
n'est pas migrée, l'insert est **silencieusement rejoué sans elle** et le
consultant est créé sans trace de consentement, sans qu'aucune erreur ne remonte.
Or **aucune migration du dépôt ne crée cette colonne**. Même situation pour les
tables `pacs` / `pac_clients`, utilisées par `routers/pacs.py` sans aucun DDL
versionné (`backend/migrations/` s'arrête à `0009_profile_fields.sql`).

---

## 1. Positionnement réel

**Ce que la plateforme est.** Ni un ATS (pas de recrutement salarié pour un
employeur unique), ni un outil de réponse aux AO (elle ne rédige pas de mémoire
technique). C'est un **VMS léger opéré en position de *master vendor*** : UTI
publie un besoin client, des ESN partenaires répondent avec des CV, UTI arbitre et
présente sous sa marque. Cette position impose un niveau de transparence du
scoring vis-à-vis des partenaires supérieur à celui d'un *neutral vendor* — c'est
une contrainte, et c'est le socle du différenciateur.

**Les trois vrais concurrents fonctionnels** (pas les leaders de catégorie) :

| Acteur | Ce qu'il est pour nous |
|---|---|
| **Agrega** (`agrega.io`) | Le concurrent frontal. Même triangle métier, même anonymisation, score IA sur 7 critères à 119 €/mois HT. |
| **Opteamis** | L'étalon de volume auquel un prospect nous comparera (400 AO publiés/mois, 750 clients revendiqués). Pas de scoring décomposé publié. |
| **Hitechpros / Turnover-IT** | La baseline que les partenaires connaissent déjà : intercontrats, AO, alertes push, messagerie, baromètre depuis 2000. Tout ce qui est en dessous sera perçu comme une régression. |

À surveiller : **Eleven VMS** (LittleBig/Mantu) — son « AI Score » note des
*réponses fournisseurs à des AO*, pas des CV : le périmètre est plus étroit que ce
que le nom suggère. Et **Whoz**, dont le score XPS évalue la séniorité
**par compétence** (clients Atos, Orange, Capgemini).

**Ce que nous faisons déjà mieux — honnêtement, et c'est réel.**

- **Le score hybride à dégradation contrôlée.** `H = a·(w·L+(1−w)·D) + (1−a)·D`
  avec `a = max(A_FLOOR, 1−|D−L|/w)` : plus l'avis du LLM diverge de la grille
  déterministe, plus le système retombe sur la grille. Grille versionnée
  (`GRID_VERSION = "2.2.0"`). **Aucun acteur des 8 packs ne documente un
  mécanisme d'arbitrage entre score déterministe et avis modèle.** Textkernel
  refuse carrément de confier le matching au LLM ; nous avons construit le
  compromis.
- **Le surlignage géométrique du PDF source relié aux citations de l'IA**
  (`lib/pdfHighlight.js` + `pdfjs-dist`). Skima AI exporte un `SKIMA_Evidence.pdf`,
  TenderCrunch source ses passages — **personne ne relie la citation au pixel du CV
  d'origine dans l'interface**. C'est notre meilleur argument de démo, devant le
  score.
- **La synthèse de vivier** (resserrement, différenciateurs, angles morts, combien
  shortlister). Aucun équivalent identifié : tous les concurrents s'arrêtent au
  comparatif ligne à ligne.
- **La traçabilité de la décision humaine** : `audit_log` append-only avec
  `run_id`/`input_hash` sans PII, `human_decision` avec justification obligatoire
  sur override. Aucun éditeur français (Flatchr, Taleez, Beetween) ne publie
  l'équivalent.
- **La diffusion étagée `list_1`/`list_2`** avec planificateur et relances : c'est
  exactement le *tiered distribution* que SAP Fieldglass vend comme mécanisme phare.
- **Le portail partenaire avec suivi de soumission et motif de refus communiqué**,
  qui répond frontalement à l'irritant n°1 du corpus utilisateurs (« there is no way
  of knowing if my candidates are even viewed to be shortlisted », à propos de
  Fieldglass).

**L'angle réellement défendable n'est donc pas le score, c'est le faisceau :**
grille déterministe versionnée + repli quand le LLM diverge + surlignage du PDF
source + synthèse de vivier + traçabilité de la décision humaine + conformité
partenaire chaînée à l'acte commercial.

---

## 2. Analyse d'écart

| Standard du secteur | Preuve (acteurs) | État | Impact si absent |
|---|---|---|---|
| Conformité fournisseur : collecte, **vérification d'authenticité** et alerte d'expiration | Provigis, Aprovall, Once For All, PIXID ; art. L.8222-1 c. trav. | **Absent** | **Critique** — solidarité financière + annulation des exonérations de cotisations sur nos propres salariés (L.133-4-5 CSS) |
| Cycle besoin → contrat → CRA → facture sans ressaisie | Fieldglass, PIXID, Opase, Piter, Nétive, LittleBig | **Absent** au-delà de l'étape 6 | Élevé en évaluation grand compte (l'acheteur note la couverture bout en bout) |
| Contractualisation dématérialisée signée électroniquement | Youtrust (QTSP eIDAS), LittleBig, Pixid, Piter | **Absent** | Élevé — sans acte signé daté, la relation n'est pas opposable en cas de litige |
| Résolution d'identité candidat inter-fournisseurs, **bloquante à la saisie** | VectorVMS, Conexis (« single, authoritative record »), Bullhorn | **Partiel** — unicité `(ao_id, consultant_id)` + drapeau de conflit à l'affichage, mais deux fiches distinctes ne sont pas bloquées | Élevé — litige d'antériorité, double mise en relation |
| Scorecard fournisseur chiffrée, **restituée au fournisseur** | Beeline (`submit-to-hire ratio`, `average response times`), Peopulse, Piter, Magnit Gateway | **Partiel** — `/admin/kpis` côté staff uniquement | Élevé — principal levier d'adhésion des ESN |
| Grille tarifaire opposable, contrôlée à la soumission | Fieldglass (*maverick spend*), Beeline Rate Visualization, PIXID | **Absent** — `tjm_achat`/`tjm_vente` en indicateur de marge seulement | Élevé — dérive des TJM, aucune maîtrise du coût |
| Information préalable de la personne évaluée + « pourquoi ai-je été écarté » | RGPD art. 13/14/15(1)(h) ; CNIL contrôles 2026 | **Partiel** — consentement horodaté, motif de refus au partenaire, DPIA. Pas de notice consultant. | **Critique en 2026** — axe explicite des contrôles CNIL |
| **Preuve mesurée** de l'intervention humaine effective | Doctrine CNIL ; AI Act art. 14(4)(d) | **Partiel** — `human_decision`, `/admin/decision-insights`. Pas de mesure du temps passé ni du taux de repêchage. | Élevé — c'est exactement ce qu'un contrôleur cherchera |
| Critères pondérés **publiés aux fournisseurs** à la diffusion | Piter (« concurrence équitable sur critères objectifs pré-établis »), Fieldglass, Mindquest | **Partiel** — pondération en étoiles côté staff, non diffusée | Moyen-Élevé — un classement sur critères non publiés n'est pas défendable |
| Dossier de compétences reformaté | Turnover-IT, BoondManager, Whoz, DC Creator | **Présent** (`cv_harmonizer`, format GRP-IT, FR/EN) — non contextualisé à l'AO | Faible — c'est un point fort |
| Recherche sémantique / langage naturel sur le vivier | hireEZ, Textkernel (29 langues), Daxtra | **Absent** — pas d'embeddings, recherche = filtre textuel | Moyen — table stake perçu en démo, mais le scoring par AO couvre le cas d'usage réel |
| Ancrage sur un référentiel public (ESCO / ROME 4.0) | ESCO v1.2.1, ROME 4.0 (API francetravail.io) | **Absent** — taxonomie propriétaire `_SKILL_ALIASES` | Faible-Moyen — argument de souveraineté, mais **aucun acheteur cité ne l'a demandé** |
| Mesure d'équité du scoring | Eightfold/BABL AI, SmartRecruiters/ConductorAI | **Partiel** — tests par permutation en CI | Moyen — voir §4 pour la reformulation, la voie « impact ratio par groupe » est **illégale en France** |
| Position publique écrite sur l'IA | Beamery (PDF public), Teamtailor, Eightfold (ISO/IEC 42001) | **Absent en externe** (le dossier `compliance/ai-act/` est interne) | Moyen-Élevé — demandé au stade de la short-list, coût de production faible |
| Intégration ATS/ERP partenaire | Turnover-IT (40+ ATS), BoondManager, Magnit API Toolkit | **Absent** — 0 webhook, 0 API publique, OpenAPI désactivé en prod | Moyen-Élevé — premier frein documenté à l'adoption partenaire |
| SLA de réponse par étape + alerte sur candidature qui refroidit | Beeline, Piter ; corpus praticiens | **Partiel** — file « à traiter », urgences deadline | Moyen |
| **Migrations versionnées complètes** | Pratique d'audit standard | **Défaut** — voir §0 | Élevé — bloquant pour tout audit, et perte silencieuse d'une preuve RGPD |
| Application mobile native | Bullhorn Mobile, myPixid | **Absent** (responsive) | **Non établi** — aucun verbatim demandeur trouvé, mais l'absence de preuve n'est pas une preuve d'absence. Ne pas prioriser sans entretiens. |

---

## 3. Angles morts réglementaires

### 3.1 Opposable maintenant

**Obligation de vigilance — absente du produit, sanction immédiate.**
Seuil : **5 000 € HT par opération** (art. R.8222-1 c. trav. — *par contrat*, et non
par cumul annuel avec un même prestataire, contrairement à ce qu'écrivent la plupart
des éditeurs de conformité). Périodicité : à la conclusion, puis **tous les six mois**
jusqu'à la fin d'exécution (art. D.8222-5). **Deux pièces, et non trois** :

1. l'attestation de vigilance URSSAF (art. L.243-15 CSS), **de moins de six mois**,
   dont l'authenticité doit être vérifiée auprès de l'URSSAF via le code de sécurité.
   **Un PDF téléversé sans vérification ne purge pas l'obligation** ;
2. un justificatif d'immatriculation (extrait K/Kbis, RNE, ou équivalent).

La **liste nominative des salariés étrangers** relève d'un régime **distinct**
(art. L.8254-1 et D.8254-2), exigible **à la conclusion seulement**, et uniquement
pour les salariés soumis à autorisation de travail. Deux workflows séparés, deux
périodicités, deux bases légales.

Sanction : solidarité financière (L.8222-2/3) **et** annulation des
réductions/exonérations de cotisations dont UTI bénéficie **au titre de ses propres
salariés** (L.133-4-5 CSS) — cette dernière doublement conditionnée (manquement
d'UTI *et* travail dissimulé avéré chez le cocontractant sur la même période).

**RGPD article 22 — applicable aujourd'hui, sans lien avec le calendrier AI Act.**
Nuance : l'art. 22 n'est **pas** une interdiction absolue ; l'art. 22(2) prévoit trois
exceptions (nécessité contractuelle, autorisation par le droit, consentement
explicite), assorties des garanties de l'art. 22(3). Le vrai point de bascule est
ailleurs : la CNIL considère qu'un outil de classement produit une décision « fondée
exclusivement sur un traitement automatisé » **par construction** lorsque les
candidatures reléguées ne sont pas réellement examinées, faute de temps. **Un bouton
« valider » sur une liste préclassée n'est pas une intervention humaine.** Notre
architecture (l'IA classe, le commerce décide) est conforme dans son principe ; il
faut la rendre **démontrable dans les logs**.

**Contrôles CNIL 2026.** Le recrutement figure parmi les thématiques prioritaires,
avec trois axes cités : systèmes de décision automatisée, **information des
candidats**, **durées de conservation**. Cibles annoncées mot pour mot : « les grandes
entreprises **et les cabinets de recrutement**, compte tenu de la multiplicité des
candidatures qu'ils reçoivent et des sélections qu'ils opèrent ». La CNIL précise que
ces contrôles « préfigureront l'exercice de ses futures attributions en tant
qu'autorité de surveillance de marché dans le champ travail au titre du règlement IA ».

**AI Act — ce qui n'est PAS reporté.**
- **art. 4** (maîtrise de l'IA) : depuis le 2 février 2025, assoupli en obligation de
  moyens depuis le 27/07/2026. Vise déjà UTI comme fournisseur *et* déployeur.
- **art. 50** (transparence) : **2 août 2026**. Marquage *machine-readable* des
  contenus de synthèse : 2 décembre 2026 pour les systèmes déjà sur le marché.
- **art. 5** : nouvelles interdictions au 2 décembre 2026.
- **art. 86** (droit à explication) : **non modifié** par le règlement 2026/1744 —
  son articulation avec le report est une question ouverte, à faire trancher par un
  juriste.
- Les **sections 4 et 5 du chapitre III** (normes, évaluation de conformité,
  enregistrement en base UE, art. 40-49) ne sont **pas** visées par le report. Point
  contre-intuitif, à confirmer avant toute décision d'architecture.

**Facturation électronique — 1er septembre 2026.** Toute entreprise assujettie à la
TVA doit pouvoir **recevoir** ses factures via une plateforme agréée ; les GE et ETI
doivent aussi **émettre**. C'est d'abord une obligation d'entreprise pour UTI, pas un
chantier produit — mais elle conditionne la façon dont la plateforme rattachera un
jour une facture partenaire à une mission.

### 3.2 Bonnes pratiques non datées mais commercialement décisives

- **Qualification fournisseur/déployeur écrite.** Appeler Claude via OpenRouter **ne
  transfère pas** la responsabilité à Anthropic (art. 25(1)) : UTI met le système en
  service sous son nom, elle en est fournisseur. Teamtailor est le seul acteur des
  packs à documenter publiquement cette répartition — c'est copiable en une page.
- **Chaîne de sous-traitance du LLM.** OpenRouter route vers des fournisseurs tiers
  potentiellement hors UE. Article 28 et chapitre V du RGPD : DPA, localisation,
  durée de rétention côté fournisseur, garanties de transfert. **Plus vite contrôlé
  et plus opposable qu'un référentiel de compétences.**
- **Exclusion documentée des proxies discriminants.** Le risque propre à un scoring
  par LLM sur CV brut est que le modèle mobilise ces signaux **implicitement**. La
  restriction doit passer par un **masquage du texte transmis**, pas par une consigne
  dans le prompt. `pseudonymize.strip_pii` couvre nom/email/téléphone/URLs ; il ne
  couvre ni la ville, ni les dates de diplôme, ni l'établissement, ni la nationalité.
- **Résilience à l'injection de prompt.** Un CV PDF est un fichier fourni par un tiers
  non maîtrisé : du texte masqué peut viser à gonfler la note. L'art. 15 exige
  explicitement une résilience aux tentatives de manipulation.
- **Base légale de l'information du consultant.** Fonder l'obligation sur l'art.
  **L.1221-8** du code du travail est douteux : ce texte vise le « candidat à un
  emploi », or le consultant est positionné sur une **mission**. Les fondements
  solides sont RGPD art. 13/14, 15(1)(h), 22 et AI Act art. 26(7).

---

## 4. Recommandations priorisées

> La vague « Maintenant » est volontairement courte. Une première version de ce
> plan comptait 10 chantiers dont 5 de taille M sur 3 mois : deux à trois fois la
> capacité réelle d'une équipe de 1 à 3 personnes qui maintient déjà la production.
> Ce qui suit est ce qui tient réellement.

### Vague « Maintenant » (0-3 mois) — tout est de taille S, sauf R5

**R0. Rattraper les migrations manquantes** · S · **Impact fort**
Le défaut décrit au §0. Trois migrations : `pacs`/`pac_clients` (DDL absent alors
que `routers/pacs.py` les utilise), les colonnes `consultants.consent_at`,
`availability_status`, `available_from`, `city`, `latitude`, `longitude`. Puis
**retirer `consent_at` de `_OPTIONAL_COLS`** : une preuve RGPD ne doit jamais être
silencieusement abandonnée par un `except`. Ajouter un contrôle de cohérence de
schéma en CI. *C'est la seule recommandation qui corrige un défaut actif en
production.*

**R1. Mesure de la supervision humaine effective** · S/M · **Impact fort**
Étendre `/admin/decision-insights` : temps écoulé entre affichage du classement et
décision, **nombre de profils réellement ouverts** rapporté à la taille du vivier,
**taux de repêchage hors top N**, taux d'override et distribution des motifs.
Nouvel onglet dans `SupervisionPage`.
*Pourquoi en premier :* c'est précisément ce qu'un contrôleur cherchera à établir,
et cela doit exister **avant** d'émettre le moindre document d'explication opposable
(R6) — sinon on produit soi-même la preuve à charge.
*Preuve :* doctrine CNIL (« écartées ou reléguées en second rang sans contrôle
humain, faute de temps ») ; AI Act art. 14(4)(d).

**R2. Publier les critères pondérés dans l'AO diffusé** · S · **Impact fort**
Rendre les étoiles (`scoring_config.s_*` fusionnées avec `scoring_overrides`)
visibles dans la vue AO côté partenaire et dans l'email de notification. Aucun
nouveau modèle de données.
*Preuve :* Piter, Mindquest. Un classement sur critères non publiés n'est
défendable ni juridiquement, ni commercialement.

**R3. Durées de conservation et purge effective** · S · **Impact fort**
Troisième axe des contrôles CNIL 2026, aujourd'hui non couvert. Politique écrite par
catégorie (CV, consultants sans soumission, matchings, logs), job de purge dans
`services/scheduler.py`, écran d'état dans `AdminPage`. Le schéma mentionne déjà
l'intention (« les consultants inactifs PEUVENT être purgés ») sans l'implémenter.

**R4. Conformité art. 50 — marquage des contenus générés** · S · **Impact moyen**
Mention « généré par IA, vérifié par [nom] le [date] » sur `ai_summary`, la synthèse
de vivier, le CV harmonisé, le motif de refus ; métadonnée dans les PDF/DOCX générés ;
bandeau sur `AssistantWidget`. Échéance 2 août 2026 pour l'interaction, 2 décembre
2026 pour le marquage lisible par machine.
*À calibrer :* l'art. 50 vise l'interaction utilisateur et les contenus diffusés ;
un score affiché à un commercial interne n'est pas clairement dans le champ. À faire,
sans en faire un couperet.

**R5. Coffre-fort de conformité partenaire — en mode alerte** · M · **Impact fort**
Table `partner_compliance_docs` (`partner_id`, `doc_type` ∈ {vigilance,
immatriculation, salariés_étrangers}, fichier en bucket privé, `issued_at`,
`expires_at`, `authenticity_checked_at`, `authenticity_code`, `checked_by`) +
historique. Endpoints `POST/GET /partners/{id}/compliance` et
`.../compliance/{doc}/verify`. Relance J-30 via le planificateur existant. Onglet
« Conformité » dans `PartnerDetailPage`.
**Alerte, pas blocage.** L'obligation se rattache au **contrat ≥ 5 000 € HT**, pas à
l'envoi d'un CV : bloquer `send-cv-to-client` serait juridiquement inutile et
commercialement suicidaire sur une plateforme sans volume. Le blocage viendra avec le
bon de commande (R10).
*Preuve :* Provigis, Once For All, PIXID. **Aucun acteur des packs ne relie la
conformité à l'acte commercial — c'est un différenciateur, pas un rattrapage.**

**R6. Chaîne de sous-traitance LLM** · S · **Impact moyen-fort**
Cartographier ce qu'OpenRouter route et où, obtenir le DPA, documenter la rétention
et les garanties de transfert (chapitre V), inscrire le résultat au registre des
traitements. Décider si un routage restreint aux fournisseurs UE est nécessaire.

**R7. Registre de formation *AI literacy* (art. 4)** · S · **Impact moyen**
Seule obligation AI Act déjà exigible et non couverte. Module court (lecture du score,
limites, biais d'automatisation), attestation par utilisateur dans `profiles`, rappel
annuel. Réutiliser `email_templates` et le planificateur.

### Vague « Ensuite » (3-9 mois)

**R8. Notice consultant + explication exportable** · S/M · Fort
Page publique `/information-ia` (rôle de l'IA, critères, pondérations, limites, voie
de contestation, contact DPO) et `GET /matching/{ao}/{consultant}/explanation.pdf`
généré depuis `matchings` (`hybrid_breakdown`, `weights`) et `human_decision` :
score par critère, poids appliqués, décision humaine finale et son auteur, horodatage.
Clause partenaire imposant l'information en amont du consultant.
*Note juridique :* il n'existe **pas**, en droit français ni dans le RGPD, de « droit
de savoir pourquoi un CV a été rejeté ». Le fondement est l'art. 15(1)(h) (logique
sous-jacente) et l'art. 22(3). Ne pas sur-promettre dans la notice.

**R9. Résolution d'identité inter-partenaires** · M · Fort
Colonne `identity_hash` sur `consultants` (email, téléphone et nom normalisés puis
hachés séparément). `POST /submissions/check-identity` appelé **avant** création,
retournant les fiches candidates avec date de première présentation et partenaire
d'origine ; écran imposant un choix explicite (rattacher / poursuivre avec motif).
Trace dans `audit_log`. **Ne jamais demander de NIR ni d'adresse personnelle.**

**R10. Scorecard partenaire, restituée au partenaire** · M · Fort
Vue calculée sur `submissions` + `ao_consultant_state` + `partner_email_log` :
*submit-to-hire ratio*, délai médian notification → première soumission, position TJM
vs médiane de l'AO, taux de CV retenus, taux de conflits. `GET /partners/{id}/scorecard`
(staff) et `GET /me/scorecard` (partenaire).
*Preuve :* Beeline nomme les trois premières métriques telles quelles ; Magnit Gateway
revendique +17 % de *win rate* pour les fournisseurs qui voient leur position.

**R11. Grille tarifaire par client et profil** · M · Moyen-fort
Table `rate_cards` (client, famille de profil, séniorité, TJM min/max, validité),
contrôle **non bloquant** à la soumission avec dérogation tracée. Affichage du
positionnement du TJM proposé vs médiane historique — la donnée existe déjà dans
`ao_consultant_state.tjm_achat`.

**R12. Masquage étendu des proxies discriminants** · M · Fort
Étendre `pseudonymize.strip_pii` : ville et code postal, dates de diplôme (dérivation
de l'âge), établissement, nationalité, patronyme. Réidentification uniquement après
décision humaine. À vendre sous le nom « scoring aveugle ».

**R13. Équité du scoring — *sans* données de groupe protégé** · M · Fort
⚠️ **Reformulation importante.** La voie standard du marché (audit type NYC LL144 :
*scoring rate* et *impact ratio* par genre et origine) suppose une collecte de données
sensibles **que le droit français interdit largement** (art. 9 RGPD, pas de
statistiques ethno-raciales). La recommander telle quelle mènerait à un chantier soit
vide, soit illicite.
Ce qui est **légal et réalisable** : généraliser les tests **contrefactuels par
permutation** déjà présents en CI (même CV, attribut varié → le score doit être
stable), y ajouter l'**ablation de proxies** (le score bouge-t-il quand on retire la
ville, l'établissement, les dates ?), et surveiller la **dérive après chaque changement
de prompt ou de modèle** — les versions sont déjà dans `audit_log`. Onglet « Équité »
dans `SupervisionPage`. C'est publiable, et **aucun éditeur français ne publie
l'équivalent**.

**R14. Déclaration publique d'explicabilité IA** · S · Fort
5 à 10 pages versionnées, exportables depuis `compliance/ai-act/` : rôle de l'IA,
critères et pondérations, formule hybride et sa logique de repli, données utilisées et
non utilisées, limites connues, garde-fous, voie de contestation. Publié sur le site,
joignable en réponse à AO.
*Preuve :* Beamery (*AI Explainability Statement*, 21/09/2024), Teamtailor, Eightfold.

**R15. SLA de réponse + date de shortlist annoncée + alerte de refroidissement** · S/M · Moyen-fort
Champ `shortlist_at` sur `appels_offres`, annoncé dans l'email de diffusion. Règle
planificateur : soumission sans changement de statut depuis N jours → tâche dans la
file « à traiter ». Rapport de SLA par étape et par partenaire dans `/admin/kpis`.

**R16. Import du dossier de compétences partenaire** · M/L · Fort
`POST /submissions/import` acceptant un dossier exporté (DOCX/PDF/JSON), mappé vers
`cv_structured` via la chaîne de parsing existante. API légère + clé par partenaire.
**Ne pas viser la synchronisation bidirectionnelle en v1.**
*Preuve :* premier frein documenté à l'adoption partenaire.

### Vague « Plus tard / à surveiller »

| Reco | Effort | Note |
|---|---|---|
| **R17.** Contractualisation (contrat-cadre + BDC signés, QES eIDAS pour le cadre) | L | Le blocage de conformité se branche ici, pas sur l'envoi de CV. Prévoir un avocat. |
| **R18.** Recherche sémantique (pgvector) | L | Table stake *perçu* en démo ; le scoring par AO couvre le cas d'usage réel. Déclencher au-delà de quelques milliers de consultants. |
| **R19.** Détection « compétence revendiquée vs étayée » | M | Pertinent quand les CV viennent de partenaires ayant intérêt à survendre. |
| **R20.** Séniorité par compétence + ancrage ESCO/ROME | L | **Déprioriser** : aucun acheteur cité ne l'a demandé, et aucun crosswalk officiel n'existe. |
| **R21.** Baromètre TJM propriétaire | M | **Attendre le volume.** Hitechpros tient 26 ans de séries ; un baromètre sur quelques dizaines d'AO détruirait la crédibilité qu'il prétend créer. |
| **R22.** Serveur MCP en lecture seule | S/M | Preuve mince (deux ATS US, dont un en bêta), zéro demande dans le corpus. Curiosité, pas priorité. |
| **R23.** Raccordement à une plateforme agréée (factures) | M | **Se raccorder, jamais devenir PDP.** |
| **R24.** Connecteur VMS client final | L | Quand un grand compte imposera son VMS. La voie du connecteur tiers (Bullhorn VMS Sync, 100+ portails) est plus réaliste que le développement en propre. |

---

## 5. Ce qu'il ne faut PAS faire

**1. Ne pas construire la chaîne CRA → facturation pour « couvrir le cycle complet ».**
Trois raisons cumulatives. La taille : plusieurs mois-homme et un engagement de
maintenance permanent. L'architecture : le planificateur est un `asyncio`
**in-process mono-worker** et le rate-limit vit en mémoire (perdu au redémarrage,
commentaire « pour ce POC » assumé dans le code) — la facturation exige une fiabilité
transactionnelle que cette architecture n'offre pas. La réglementation : au
1er septembre 2026, toute facture B2B doit transiter par une **plateforme agréée**
immatriculée. Construire une facturation non raccordée serait à jeter.

**2. Ne pas coder de règle bloquante « TJM > coût salarial chargé = prêt de
main-d'œuvre illicite ».**
L'erreur la plus séduisante et la plus fausse. Le critère « facturé au temps passé »
**n'existe pas** dans l'art. L.8241-1, qui définit le but non lucratif *a contrario* :
est non lucrative l'opération où le prêteur ne refacture **que** les salaires, charges
et frais professionnels. Le test porte sur le **dépassement du coût réel refacturé**,
pas sur l'unité de facturation. Une règle automatique produirait des faux positifs sur
**toute prestation d'ESN légale, y compris les nôtres**. À construire à la place : les
**champs de preuve** dans le bon de commande (livrables formulés en résultats et non en
« mise à disposition d'un profil », qualification moyens/résultat, responsable
hiérarchique côté partenaire) et une alerte de **dérive de mission** (même consultant,
même client, au-delà d'un seuil de durée). Fabriquer la preuve documentaire au fil de
l'eau, ne pas bloquer sur un ratio. À valider par un avocat en droit social.

**3. Ne pas lancer le dossier haut risque complet pour 2026.**
16 mois de plus, pas six jours (§0). Mobiliser 3 personnes sur un dossier annexe IV,
une déclaration UE de conformité et un QMS art. 17 maintenant reviendrait à
déprioriser ce qui est **réellement opposable** : vigilance URSSAF, RGPD art. 22,
contrôles CNIL 2026, art. 50 et art. 4. **Ordre de bataille : R0 à R7 d'abord, le
dossier annexe IV en 2027**, en capitalisant sur `compliance/ai-act/` et la DPIA déjà
rédigées.

**4. Ne pas faire du « scoring explicable » l'argument de vente unique.**
Voir §0 : Agrega le vend déjà à 119 €/mois. Le score est un table stake. Vendre le
faisceau, pas la note sur 100.

**5. Ne pas construire un ATS pour les partenaires ni exiger qu'ils changent d'outil.**
Ils ont déjà leurs consultants dans BoondManager, Turnover-IT ou leur CRM. Le corpus
d'avis est sans ambiguïté sur ce qui se passe quand une plateforme impose sa saisie —
et un partenaire ESN ne migrera pas son ATS pour UTI. Assumer la position
d'intermédiaire : **place de marché AO ↔ partenaires**, avec import de fichier et API
légère (R16), pas un système de référence concurrent. Corollaire tarifaire : **ne
jamais facturer le partenaire qui dépose un CV**.

**Bonus — pas d'entretien vidéo IA avec analyse faciale ou émotionnelle.** HireVue a
retiré l'analyse faciale en janvier 2021 après la plainte EPIC ; la reconnaissance des
émotions au travail relève des pratiques **interdites** de l'art. 5, non reportées.
Si un pré-entretien IA est un jour envisagé : voix et texte oui, inférence émotionnelle
ou faciale jamais.

---

## 6. Questions ouvertes — hors périmètre technique, mais déterminantes

Ce scan porte sur le produit. Quatre questions le débordent et conditionnent la valeur
de tout ce qui précède. Elles appellent une décision, pas du code.

1. **Le modèle économique.** Qui paie, combien, et comment se positionner face aux
   119 €/mois HT d'Agrega ? Prioriser 24 chantiers sans connaître l'unité de valeur
   facturée est le défaut de fond de tout plan produit à ce stade.
2. **L'amorçage biface.** Une place de marché AO ↔ ESN sans liquidité ne vaut rien
   (Opteamis : 400 AO/mois). Aucune recommandation technique ne traite l'acquisition
   de partenaires ni le volume d'AO. **Le risque de mort du produit est là, pas dans
   l'URSSAF.**
3. **Le client final.** Il paie la mission et n'apparaît dans aucune recommandation
   au-delà du lien de retour existant : pas de portail, pas de partage de shortlist,
   pas de retour structuré exploitable.
4. **La fiabilité d'exécution.** Le planificateur mono-worker et le rate-limit en
   mémoire sont invoqués au §5.1 pour *interdire* la facturation, mais R3, R5, R7 et
   R15 reposent dessus. Si ces chantiers avancent, cette dette devient bloquante.

---

## 7. Limites de méthode

À dire avant qu'on nous le reproche :

- **G2, Capterra et Gartner bloquent l'accès automatisé** (HTTP 403). Les notes et
  volumes d'avis cités par des blogs d'éditeurs n'ont pu être confirmés qu'en partie,
  via Software Advice. Les statistiques de sentiment par thème (« 80 % des avis
  négatifs sur la performance ») **ne sont publiées par personne** : elles ont été
  retirées de ce document.
- **Le corpus de voix utilisateurs est Reddit et des forums** : non probabiliste, non
  auditable, non versionné. Il sert à **repérer des irritants**, jamais à mesurer leur
  fréquence. Les conclusions du type « le mobile n'est pas réclamé » sont des
  **non-observations**, pas des constats — d'où le « non établi » au §2.
- **Les chiffres d'acteurs sont autodéclarés** (PIXID, Hitechpros, Opteamis) et
  plusieurs se sont révélés déformés en cascade (« 222 000 entreprises clientes » est
  en réalité « 222 000 *sites* clients finaux »). Ne pas les réutiliser en soutenance
  sans revenir à la page source.
- **Les points réglementaires ont été vérifiés en source primaire** (Legifrance,
  EUR-Lex, CNIL, impots.gouv.fr) et pas seulement en source secondaire — c'est ce qui a
  permis de détecter que le calendrier AI Act circulant en ligne est périmé. Ce
  document **ne constitue toutefois pas un avis juridique** : les points 3.1 et 5.2
  doivent être validés par un conseil.
