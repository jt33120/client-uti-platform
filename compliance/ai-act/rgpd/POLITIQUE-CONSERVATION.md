# Politique de conservation des données

> **Statut :** 🟧 EN COURS — les durées ci-dessous sont une proposition de
> l'équipe produit, à arbitrer avec le DPO et le conseil juridique
> (cf. `compliance/QUESTIONS-CONSEIL-JURIDIQUE.md`, point C4).
> **Responsable :** Sullyvan BIJON (DPO) · **Dernière mise à jour :** 28 juillet 2026
> **Base légale :** RGPD art. 5-1-e (limitation de la conservation) · AI Act art. 12 (journalisation)

Les **durées de conservation** sont le troisième axe annoncé des contrôles
prioritaires de la CNIL pour 2026 en matière de recrutement, aux côtés des
systèmes de décision automatisée et de l'information des candidats.

---

## 1. Le conflit à arbitrer, énoncé d'abord

Deux exigences tirent en sens opposé et il faut le dire avant de fixer des durées :

- la **minimisation RGPD** pousse à effacer tôt ;
- la **journalisation de l'AI Act (art. 12)** impose de conserver les traces
  permettant de reconstituer le fonctionnement du système, et l'art. 18 impose au
  fournisseur de conserver la documentation.

La ligne retenue ici : **effacer tôt les données identifiantes, conserver
longtemps les traces non identifiantes.** Le journal d'audit ne contient ni nom,
ni email, ni texte de CV — il porte des identifiants techniques, un `input_hash`
et des scores. Il peut donc être conservé sans contradiction avec la minimisation.

---

## 2. Durées par catégorie

| Catégorie | Contenu | Durée proposée | Point de départ | Action à l'échéance |
|---|---|---|---|---|
| **CV et texte extrait** (`submissions.cv_url`, `cv_text`, `cv_structured`) | Donnée la plus sensible du système | **24 mois** | Date de soumission | Suppression du fichier stocké + effacement des textes. La ligne est conservée, vidée. |
| **Retour client en texte libre** (`ao_consultant_state.client_decision_note`) | Peut contenir une appréciation nominative | **24 mois** | Même échéance que le CV associé | Effacement du texte. `client_decision` (énuméré, non identifiant) et sa date sont conservés. |
| **Fiche consultant** (`consultants` : nom, email, téléphone, ville, géoloc) | Identité du candidat | **24 mois** | Dernière activité = max(création de la fiche, dernière soumission) | Anonymisation. TJM, compétences, années d'expérience et type d'emploi sont conservés — non identifiants et porteurs de la valeur analytique. |
| **Résultats de matching** (`matchings`) | Scores, décomposition, avis | **Conservé** tant que l'AO existe | — | Aucune. Les scores ne sont pas identifiants une fois la fiche consultant anonymisée. |
| **Journal d'audit** (`audit_log`) | `run_id`, `input_hash`, version de grille — **sans PII** | **Proposition : 5 ans** | Date de l'exécution | À arbitrer. Sert la reconstitution exigée par l'art. 12. |
| **Décisions humaines** (`human_decision`) | Qui a décidé, quand, avec quelle justification | **Proposition : 5 ans** | Date de la décision | À arbitrer. Preuve de la supervision humaine (art. 14). |
| **Usage IA** (`ai_usage`) | Coûts, latences, opérations | **13 mois** | Date de l'appel | Purge. Donnée d'exploitation, non identifiante. |
| **Journal d'envois** (`partner_email_log`, `email_log`) | Destinataires, horodatages | **13 mois** | Date d'envoi | Purge. |
| **Comptes utilisateurs** (`profiles`) | Personnel UTI et partenaires | Durée de la relation + **12 mois** | Fin de la relation | Suppression ou anonymisation. |
| **IP de connexion** (`profiles.last_login_ip`) | Sécurité | **12 mois** | Dernière connexion | Effacement. |

**Pourquoi 24 mois pour les CV.** C'est la durée usuelle retenue en matière de
recrutement et elle correspond au cycle de renouvellement des missions dans le
conseil : en deçà, on efface des profils encore pertinents commercialement ; au-delà,
on conserve des CV périmés sans usage réel. Le réglage est paramétrable en
administration, avec un **plancher de 6 mois** pour éviter une purge trop agressive
par erreur de saisie.

---

## 3. État d'implémentation

| Mécanisme | État |
|---|---|
| Anonymisation des CV hors délai | ✅ Implémentée (`services/data_retention.py`), exécutée à chaque tick du planificateur |
| Effacement du retour client en texte libre | ✅ Implémentée, même échéance |
| Anonymisation des fiches consultants inactives | ✅ Implémentée (migration `0013`) |
| Visibilité du réglage en administration | ✅ Le nombre d'enregistrements dépassant le délai est affiché, **que la purge soit active ou non** |
| Purge des journaux (`ai_usage`, `email_log`, IP) | ❌ Non implémentée — durées à arbitrer d'abord |
| Suppression des comptes inactifs | ❌ Non implémentée |

**Point d'attention.** La purge est en **opt-in strict** : elle ne s'exécute que si
un administrateur l'a activée. Au 28 juillet 2026 elle était **désactivée en
production**, et rien ne le signalait — le réglage par défaut la rendait
silencieusement inerte. L'écran d'administration affiche désormais explicitement
l'état et le volume concerné, précisément pour que cette inaction ne puisse plus
passer inaperçue.

L'activation reste une **décision du DPO**, pas un défaut technique : purger est
irréversible.

---

## 4. Droits des personnes

Indépendamment des durées ci-dessus :

- **Effacement (art. 17)** — `DELETE /users/{user_id}/gdpr` supprime le compte et
  les fichiers CV associés dans le stockage ;
- **Accès et portabilité (art. 15 et 20)** — export des données d'un consultant
  disponible depuis sa fiche ;
- **Consentement** — horodaté à la création de la fiche consultant
  (`consultants.consent_at`).
