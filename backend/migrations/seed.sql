-- ════════════════════════════════════════════════════════════════════
--  UTI / Groupement-IT — données indispensables au fonctionnement.
--
--  À jouer APRÈS schema.sql, sur une base neuve :
--      cd backend/migrations && sudo -u postgres psql -d uti -v ON_ERROR_STOP=1 < seed.sql
--
--  ⚠️  L'utilisateur `postgres` ne peut pas lire un fichier sous /home : les
--  répertoires personnels sont en 750, il ne peut même pas les traverser.
--  `sudo -u postgres psql -f ~/app/...` échoue donc sur « Permission denied ».
--  On REDIRIGE depuis son propre shell, qui a le droit de lire le fichier et
--  passe le descripteur au processus sudo. Résultat identique à -f, sans avoir
--  à copier les fichiers ni à ouvrir les droits du répertoire personnel.
--
--  CE QUI EST ICI, ET CE QUI N'Y EST PAS
--
--  Ici : uniquement de la CONFIGURATION — des réglages qu'un humain a calibrés
--  et qui n'ont pas d'équivalent dans le code. Pas une seule ligne de donnée
--  métier : ni client, ni consultant, ni appel d'offres. C'est précisément ce
--  mélange qui faisait apparaître deux clients inventés (« AGIRC ARRCO »,
--  « Groupe France Télévisions ») dans toute base reconstruite depuis le dépôt,
--  parce que supabase_migration_client_hierarchy.sql mêlait DDL et INSERT.
--
--  POURQUOI DES LIGNES QUI NE FONT QUE RÉPÉTER LES DÉFAUTS DU CODE
--
--  Parce que l'ABSENCE d'une ligne et sa PRÉSENCE avec la valeur par défaut ne
--  se comportent pas pareil dans cette application, et que c'est déjà arrivé :
--  aucune ligne `data_retention` n'existait en production, la purge RGPD
--  retombait donc sur `enabled: false` et ne s'exécutait jamais — sans erreur,
--  sans trace, sans que l'écran d'administration ne montre autre chose qu'un
--  réglage d'apparence normale.
--
--  Une ligne explicite rend l'état VISIBLE : l'admin voit « purge désactivée »
--  comme une décision, pas comme un vide. C'est la même leçon que les zéro
--  étoile du scoring — un critère à 0 doit dire qu'il est à 0, pas se taire.
--
--  IDEMPOTENT. Rejouable sans écraser ce qu'un administrateur aurait modifié
--  depuis : chaque insertion est conditionnée à l'absence de la ligne.
-- ════════════════════════════════════════════════════════════════════

-- ── Réglages applicatifs ────────────────────────────────────────────
-- Clés lues par services/app_settings.py. Les valeurs reproduisent la
-- configuration de production au 6 août 2026.

-- Notifications partenaires et relances. `relance_auto_enabled` est à false :
-- les relances sont déclenchées à la main par le commercial.
INSERT INTO public.app_settings (key, value) VALUES (
  'notifications',
  '{"enabled": true, "list2_delay_days": 2, "relance_auto_enabled": false, "relance_interval_days": 7, "relance_max": 2}'::jsonb
) ON CONFLICT (key) DO NOTHING;

-- Purge RGPD. Volontairement DÉSACTIVÉE : l'activation est une décision qui
-- appartient au DPO, pas au déploiement — une purge est irréversible. Le
-- plancher de 6 mois est imposé par le code (_coerce_retention), 24 mois est le
-- défaut retenu dans compliance/ai-act/rgpd/POLITIQUE-CONSERVATION.md.
-- La ligne existe pour que « désactivée » soit un choix affiché, pas un oubli.
INSERT INTO public.app_settings (key, value) VALUES (
  'data_retention',
  '{"enabled": false, "months": 24}'::jsonb
) ON CONFLICT (key) DO NOTHING;

-- Budgets IA — ARMÉS, et c'est tout l'intérêt.
--
-- Une ligne à 0/0 reproduisait l'incident `data_retention` sous une autre forme :
-- services/ai_budget.py sort immédiatement quand les deux plafonds sont nuls
-- (« aucune limite active »). La ligne existe, l'écran d'administration
-- l'affiche, et aucune alerte ne part jamais. « Présent mais inerte » se
-- diagnostique encore plus mal qu'« absent », parce que l'écran a l'air normal.
--
-- Les valeurs ci-dessous ne sont pas une estimation de budget : ce sont des
-- plafonds d'ALARME. La dépense observée en production est de 3,39 $ sur un mois
-- pour 426 appels ; 60 $/mois est donc environ dix-huit fois le trafic réel. Ce
-- n'est pas une contrainte, c'est le seuil au-delà duquel quelque chose ne
-- tourne manifestement plus rond — une boucle d'appels, un traitement en
-- rafale. Une alerte coûte un e-mail ; une boucle non surveillée peut courir un
-- mois. À resserrer une fois le trafic réel connu (Supervision → Usage IA).
INSERT INTO public.app_settings (key, value) VALUES (
  'ai_budget',
  '{"enabled": true, "weekly_usd": 20.0, "monthly_usd": 60.0}'::jsonb
) ON CONFLICT (key) DO NOTHING;

-- ── Grille de scoring ───────────────────────────────────────────────
-- Calibration réelle de la production, à conserver : elle a été réglée à la
-- main et n'a pas d'équivalent dans le code (services/scoring_settings.py
-- renvoie {} si la table est vide, et le moteur retombe alors sur ses propres
-- DEFAULTS, qui ne sont PAS ceux-ci).
--
-- Noter `s_tjm = 0` : le critère TJM est délibérément neutralisé — il reste
-- présent et réactivable d'un clic, mais ne pèse pas dans la note. C'est un
-- arbitrage produit, pas un oubli ; l'interface l'affiche comme « EXCLU ».
--
-- Les colonnes `w_*` sont conservées pour compatibilité, mais le moteur
-- n'utilise plus que les étoiles `s_*`, normalisées à somme 100.
INSERT INTO public.scoring_config (
  w_competences, w_seniorite, w_contexte, w_tjm,
  w_points_forts_cv, w_elements_differenciants,
  s_competences, s_seniorite, s_contexte, s_tjm,
  s_points_forts_cv, s_elements_differenciants,
  seniority_full_years, reco_fort_min, reco_moyen_min
)
SELECT 30, 29, 29, 0,
       6, 6,
       5, 5, 5, 0,
       1, 1,
       8, 75, 50
WHERE NOT EXISTS (SELECT 1 FROM public.scoring_config);

-- ── Modèles d'e-mails ───────────────────────────────────────────────
-- Rien à insérer : la table est vide en production et services/email_templates.py
-- rend les modèles par défaut codés dans le dépôt. Une ligne n'apparaît que
-- lorsqu'un administrateur personnalise un modèle depuis l'écran dédié.
-- Mentionné explicitement pour qu'une table vide ne passe pas pour un oubli.

-- ── Premier administrateur ──────────────────────────────────────────
-- Volontairement absent d'ici : un compte se crée avec un mot de passe, et un
-- mot de passe n'a pas sa place dans un fichier versionné. Utiliser
-- backend/scripts/bootstrap_admin.py, qui le demande de façon interactive.
