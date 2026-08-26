# Plan de test QA — Plateforme G-IT

Plan de test de bout en bout de la plateforme. À utiliser **à chaque release**.

- **Manuellement** : suis les étapes, remplis la colonne *Statut* (✅ / ❌).
- **Avec un agent computer-use (Cowork)** : colle la section [« Mission »](#mission-à-donner-à-lagent) ci-dessous + le plan, fournis les accès, laisse-le naviguer et remplir les statuts.

> ⚠️ Ces tests s'exécutent en **production** : ils créent de vraies données et envoient de **vrais emails**. Préfixe toute donnée créée par `TEST-` pour pouvoir nettoyer. Le matching IA **consomme du crédit OpenRouter**.

---

## Pré-requis

| Élément | Valeur |
|---|---|
| URL plateforme | https://plateforme.groupement-it.com |
| Compte **admin** | `__________` / `__________` |
| Email de réception (invitations/support) | `__________` |
| Un fichier **CV PDF** valide (< 10 Mo) | `cv_test.pdf` |
| Un fichier **non-PDF** (ex. image) | `image.png` |

---

## Mission (à donner à l'agent)

> Teste de bout en bout la plateforme à l'URL fournie. Pour **chaque ligne** des tableaux ci-dessous, exécute l'étape et renseigne la colonne **Statut** (✅ si le *Résultat attendu* est obtenu, ❌ sinon) + une note/capture en cas d'échec. Utilise le préfixe `TEST-` pour toute donnée créée. **Ne valide rien d'irréversible sans me prévenir.** À la fin, renvoie un **compte-rendu** : nb de ✅/❌, et la liste des bugs trouvés.

---

## 1. Authentification & profil

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 1.1 | Se connecter en admin | Arrivée sur le tableau de bord | | |
| 1.2 | Profil → modifier le nom | Nom mis à jour, persiste après refresh | | |
| 1.3 | Profil → uploader un avatar (image) | Avatar visible | | |
| 1.4 | Se déconnecter puis reconnecter | Login OK | | |
| 1.5 | « Mot de passe oublié » avec l'email admin | Un email de réinitialisation arrive | | |

## 2. Admin — données de base

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 2.1 | Créer un client `TEST-Client-Acme` | Client créé et listé | | |
| 2.2 | Créer un AO `TEST-AO-DevPython` (titre, description, compétences) rattaché au client | AO créé | | |
| 2.3 | Ouvrir la liste des appels d'offres | L'AO `TEST-AO-DevPython` apparaît | | |
| 2.4 | Ouvrir le vivier de consultants | La liste se charge sans erreur | | |

## 3. Invitation partenaire (flux email)

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 3.1 | Partenaires / Accès partenaires → inviter avec l'email de réception | Confirmation d'envoi | | |
| 3.2 | Vérifier la boîte mail | Email d'invitation **reçu** (expéditeur Groupement-IT). Vérifier aussi les indésirables : un message accepté par le relais peut ne jamais arriver | | |
| 3.3 | Ouvrir le lien → créer le compte `TEST-Partenaire` | Compte créé, connexion OK | | |
| 3.4 | (Admin) Donner au partenaire un accès `list_1` ou `list_2` au client `TEST-Client-Acme` | Accès enregistré | | |
| 3.5 | Réutiliser le **même lien** d'invitation une 2e fois | Lien refusé (usage unique / expiré) | | |

## 4. Partenaire — soumission de consultant

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 4.1 | Se connecter en `TEST-Partenaire` | Accès à l'espace partenaire | | |
| 4.2 | Ajouter un consultant au vivier `TEST-Consultant-Jean` (nom, compétences, TJM) | Consultant créé | | |
| 4.3 | Soumettre ce consultant à l'AO `TEST-AO-DevPython` + upload d'un CV PDF | Soumission enregistrée, CV accessible | | |
| 4.4 | Vérifier l'affichage de la soumission | Visible côté partenaire | | |

## 5. Cas limites (uploads & doublons)

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 5.1 | Uploader un fichier **non-PDF** | Refusé (message « PDF uniquement ») | | |
| 5.2 | Uploader un PDF **> 10 Mo** | Refusé (message « trop volumineux ») | | |
| 5.3 | Re-soumettre le **même consultant au même AO** | Refusé (« déjà soumis ») | | |

## 6. Matching IA

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 6.1 | (Admin) Sur `TEST-AO-DevPython`, lancer le scoring IA | Traitement (10-30 s) sans erreur | | |
| 6.2 | Consulter les résultats | Scores / classement affichés pour les consultants soumis | | |

## 7. Cloisonnement des accès (sécurité)

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 7.1 | En `TEST-Partenaire`, tenter de voir un client/AO **sans accès** | Non visible / accès refusé | | |
| 7.2 | Section **PACs** | Se charge correctement | | |

## 8. Support / contact

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 8.1 | Envoyer un message via Contact/Support | Confirmation d'envoi | | |
| 8.2 | Vérifier la boîte admin (`plateforme@groupement-it.com`) | Email reçu, « répondre à » = adresse de l'expéditeur | | |

## 9. Assistant IA

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 9.1 | Ouvrir l'assistant, demander « emmène-moi créer un appel d'offres » | Il route vers la bonne page (IA OpenRouter active, pas le fallback) | | |
| 9.2 | Demander une action hors périmètre | Réponse cohérente / refus poli | | |

## 10. Nettoyage (optionnel)

| # | Étape | Résultat attendu | Statut | Notes |
|---|---|---|---|---|
| 10.1 | Supprimer une soumission `TEST-` | Suppression OK (fichier retiré du stockage) | | |
| 10.2 | Supprimer consultant / AO / client / partenaire `TEST-` | Données retirées | | |

---

## Compte-rendu

- ✅ : ___ / ❌ : ___
- Bugs trouvés :
  1. …
- Environnement testé : prod (`plateforme.groupement-it.com`) · date : ____
