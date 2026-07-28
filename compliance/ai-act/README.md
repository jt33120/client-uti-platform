# Conformité AI Act — Système de matching de CV UTI Group

> ⚠️ **Avertissement** : ce dossier est un cadre de travail technico-réglementaire
> rédigé par l'équipe produit. Il **ne constitue pas un avis juridique**. La
> classification « haut risque » et la version finale de chaque document doivent
> être **validées par un conseil juridique** avant signature de la Déclaration UE
> de conformité.

## 1. De quoi parle-t-on ?

UTI Group exploite une plateforme B2B où des **partenaires** soumettent des CV de
**consultants / freelances externes** à des **appels d'offres (AO)** clients. Un
moteur d'IA **score et classe** ces consultants par rapport à chaque AO
(score /100, breakdown, recommandation, rang).

Cette finalité — **évaluer et filtrer des candidats / accès au travail
indépendant** — relève de l'**Annexe III, point 4** du règlement (UE) 2024/1689
(« AI Act ») : **système d'IA à haut risque**. Cette classification est **actée**
(décision UTI Group, juin 2026).

## 2. Nos casquettes réglementaires

| Rôle | UTI Group ? | Obligations principales |
|------|-------------|--------------------------|
| **Provider** (fournisseur) | ✅ Oui — UTI construit et met en service le système | Art. 9–15, 17, 18-19, 43 + Annexe VI, 47-49, 72-73 |
| **Deployer** (déployeur) | ✅ Oui — l'équipe commerce UTI l'utilise | Art. 26 |
| **Distributeur** | ✅ Oui — mis à disposition de partenaires/clients | Diffusion ⇒ enregistrement base UE (Art. 49) bien réel |

Détail : voir [`phase-0-gouvernance/01-roles-et-responsabilites.md`](phase-0-gouvernance/01-roles-et-responsabilites.md).

## 3. Échéances

- **Mise en conformité haut risque visée : 2 décembre 2027** — report **définitif**
  depuis la publication du règlement (UE) 2026/1744 (« Digital Omnibus on AI »),
  JO L du 24/07/2026, en vigueur le 27/07/2026. Il décale les **sections 1, 2 et 3
  du chapitre III** pour les systèmes autonomes de l'Annexe III.
- **Déjà exigible** : Art. 4 (littératie IA, depuis fév. 2025) et obligations de
  documentation/transparence.
- **NON reporté, à ne pas confondre** : Art. 50 (transparence) au **2 août 2026**
  (marquage lisible par machine des contenus de synthèse au 2 décembre 2026 pour les
  systèmes déjà sur le marché) · Art. 5 (nouvelles interdictions) au 2 décembre 2026 ·
  Art. 86 (droit à explication), **non modifié** par le règlement 2026/1744 ·
  **sections 4 et 5 du chapitre III** (art. 40-49 : normes, évaluation de conformité,
  enregistrement en base UE), hors du champ du report — point contre-intuitif à faire
  trancher par le conseil juridique.

## 4. Périmètre & paramètres figés

| Élément | Valeur |
|--------|--------|
| Entité responsable | **UTI Group** |
| Marché visé | **France** |
| DPO | **Sullyvan BIJON** (interne) |
| Effectif | < 11 salariés (pas de CSE obligatoire) ; **personnes concernées = freelances externes** |
| Modèle IA actuel | Anthropic Claude 3.5 Haiku via OpenRouter (`anthropic/claude-3.5-haiku`) |
| Évaluation de conformité | **Contrôle interne — Annexe VI** (pas d'organisme notifié, pas de certification tierce payante) |

## 5. Organisation du dossier

| Phase | Dossier | Contenu |
|-------|---------|---------|
| **Suivi** | [`REGISTRE-CONFORMITE.md`](REGISTRE-CONFORMITE.md) | Les 29 actions, statut, responsables |
| **0** | [`phase-0-gouvernance/`](phase-0-gouvernance/) | Rôles, fiche système, littératie IA |
| **1** | [`phase-1-social-contractuel/`](phase-1-social-contractuel/) | Information/consentement des consultants, révision humaine, flow-down partenaires, usage interne |
| **2** | [`phase-2-risques-donnees/`](phase-2-risques-donnees/) | Gestion des risques (Art. 9), gouvernance des données (Art. 10), test de biais |
| **3** | [`phase-3-technique/`](phase-3-technique/) | Notice (Art. 13), spec architecture hybride, journalisation (Art. 12), supervision (Art. 14), robustesse (Art. 15) |
| **4** | [`phase-4-documentation-qms/`](phase-4-documentation-qms/) | Dossier technique Annexe IV, QMS (Art. 17), conservation (Art. 18-19) |
| **RGPD** | [`rgpd/`](rgpd/) | DPIA |

## 6. Comment lire le statut

Chaque document porte un en-tête :

```
Statut : 🟥 À FAIRE | 🟧 EN COURS | 🟩 VALIDÉ
Responsable : [nom]
Dernière mise à jour : AAAA-MM-JJ
Articles AI Act : ...
```

Les zones à compléter par UTI sont balisées `[À COMPLÉTER : ...]`.
