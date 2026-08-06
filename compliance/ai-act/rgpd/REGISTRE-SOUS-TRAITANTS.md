# Registre des sous-traitants — traitement « Présélection de consultants »

> **Base légale :** RGPD art. 28 (sous-traitance) et art. 30-2 (registre du
> sous-traitant / de la chaîne). AI Act art. 12 pour la conservation des
> journaux hébergés.
>
> **Statut :** 🟧 à faire viser par le DPO (Sullyvan BIJON) — voir §4.
>
> **Dernière révision :** août 2026, à l'occasion du changement d'hébergeur de
> la base de données.

---

## 1. Pourquoi ce document existe

Jusqu'en août 2026, la base de données et le stockage des fichiers étaient
confiés à **Supabase, Inc.** Aucun document de conformité ne le mentionnait :
la seule trace était une ligne d'architecture technique
([dossier annexe IV](../phase-4-documentation-qms/01-dossier-technique-annexe-IV.md)).

Or Supabase hébergeait :

- les **CV** des consultants et leur texte extrait — la donnée la plus sensible
  du système ;
- les **comptes utilisateurs** et leurs empreintes de mot de passe ;
- le **journal d'audit** exigé par l'AI Act art. 12.

C'était donc un sous-traitant au sens de l'art. 28, avec transfert de données
personnelles. Le retrait de ce sous-traitant est un **changement de la chaîne de
sous-traitance** : il se documente, et il se documente **avant**.

---

## 2. Chaîne de sous-traitance — avant / après

| Rôle | Avant août 2026 | À partir du 17 août 2026 | Localisation |
|---|---|---|---|
| Hébergement du **frontend** | Vercel Inc. | Vercel Inc. *(inchangé)* | USA |
| Hébergement de l'**API** | OVH SAS | OVH SAS *(inchangé)* | Roubaix, France |
| Hébergement de la **base de données** | **Supabase, Inc.** (infra AWS `eu-west-1`, Irlande) | **OVH SAS** — PostgreSQL auto-hébergé sur le VPS, aucun tiers | Roubaix, France |
| **Authentification** (mots de passe, sessions) | **Supabase, Inc.** (GoTrue) | **UTI Group** — code interne, aucun tiers | Roubaix, France |
| **Stockage des fichiers** (CV, pièces jointes, attestations) | **Supabase Storage** | **OVH Object Storage** (région GRA) | Gravelines, France |
| **Inférence IA** | OpenRouter → Anthropic | OpenRouter → Anthropic *(inchangé)* | Hors UE |
| **Envoi d'e-mails** | Infomaniak | Infomaniak *(inchangé)* | Suisse |

**Effet net :** trois sous-traitants disparaissent de la chaîne (base,
authentification, stockage, tous portés par Supabase) et aucun ne s'ajoute — OVH
y figurait déjà pour l'API. Le nombre d'entités touchant des données
personnelles **diminue**, et les données de base et de fichiers **restent
intégralement en France**.

C'est un changement **favorable** aux personnes concernées, ce qui ne dispense
ni de le documenter, ni de le faire viser.

---

## 3. Détail par sous-traitant restant

| Sous-traitant | Traitement confié | Données | Localisation | Encadrement art. 28 |
|---|---|---|---|---|
| **OVH SAS** — 2 rue Kellermann, 59100 Roubaix, France | Hébergement de l'API, de la base de données et des fichiers | Toutes catégories : identité, coordonnées, CV, scores, journaux | France (Roubaix, Gravelines) | Conditions particulières OVH + DPA. **À joindre au dossier** |
| **Vercel Inc.** — 340 S Lemon Ave #4133, Walnut, CA 91789, USA | Hébergement des fichiers statiques du frontend | Aucune donnée personnelle stockée ; adresses IP dans les journaux d'accès | USA | DPA Vercel + clauses contractuelles types. **À vérifier** |
| **OpenRouter, Inc.** → **Anthropic PBC** | Extraction et analyse des CV par LLM | Contenu de CV, pseudonymisé (`services/pseudonymize.py`) ; pages du CV **en image** si `VISION_ENABLED=true` | Hors UE | 🟥 DPA à conclure — risque R6 du [registre des risques](../phase-2-risques-donnees/01-systeme-gestion-risques.md) |
| **Infomaniak Network SA** — Genève, Suisse | Acheminement des e-mails transactionnels | Adresse e-mail, nom, contenu des notifications | Suisse (décision d'adéquation) | DPA Infomaniak. **À joindre au dossier** |

---

## 4. Actions ouvertes

- [ ] Faire **viser ce registre par le DPO** avant la suppression du projet
      Supabase (critère n° 11 de `BASCULE.md`).
- [ ] Vérifier si l'**information des personnes concernées**
      ([notice](../../phase-1-social-contractuel/01-information-personnes-concernees.md))
      mentionne un hébergeur nommément. Si oui, la corriger : le changement doit
      être porté à leur connaissance.
- [ ] Mettre à jour les **mentions légales** (`frontend/src/pages/LegalPages.jsx`).
- [ ] Réviser la **DPIA** — une DPIA se revoit à chaque changement significatif
      de sous-traitant ([DPIA §7](DPIA.md)).
- [ ] Joindre les **DPA** d'OVH, Vercel et Infomaniak au dossier.
- [ ] Conclure le **DPA OpenRouter/Anthropic** (action antérieure, toujours ouverte).
- [ ] Archiver le `MANIFEST.txt` de l'archive Supabase comme **preuve de
      restitution et d'effacement** au titre de l'art. 28-3-g : le sous-traitant
      sortant doit restituer ou effacer les données. La suppression du projet
      Supabase est l'effacement ; l'archive hors ligne en est la restitution.
