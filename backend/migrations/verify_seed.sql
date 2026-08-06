-- ════════════════════════════════════════════════════════════════════
--  Contrôle de la configuration présente en base.
--
--      psql -d uti -f backend/migrations/verify_seed.sql
--
--  POURQUOI TROIS ÉTATS ET NON DEUX
--
--  « Présent / absent » ne suffit pas. L'incident d'origine ne venait pas d'une
--  valeur fausse mais d'une ligne ABSENTE : la purge RGPD retombait sur son
--  défaut `enabled: false` et ne s'exécutait jamais, sans erreur ni trace.
--  Il existe une seconde forme du même piège, plus sournoise : une ligne
--  PRÉSENTE, d'apparence configurée, dont la valeur désarme la fonction. C'est
--  le cas de `ai_budget` avec des plafonds à 0 — services/ai_budget.py:144 sort
--  immédiatement et aucune alerte ne partira jamais.
--
--  D'où l'état INERTE, traité comme un rouge par scripts/post_bascule_check.sh.
-- ════════════════════════════════════════════════════════════════════

\pset tuples_only on
\pset format unaligned

-- ── app_settings ────────────────────────────────────────────────────
SELECT 'app_settings/notifications      ' ||
       CASE WHEN EXISTS (SELECT 1 FROM public.app_settings WHERE key = 'notifications')
            THEN 'OK' ELSE 'MANQUANT' END;

-- La purge est en opt-in strict : `enabled: false` est une décision légitime,
-- pas un défaut. Ce qui doit être signalé, c'est l'ABSENCE de la ligne.
SELECT 'app_settings/data_retention     ' ||
       CASE WHEN NOT EXISTS (SELECT 1 FROM public.app_settings WHERE key = 'data_retention')
              THEN 'MANQUANT'
            WHEN (SELECT (value->>'enabled')::boolean FROM public.app_settings WHERE key = 'data_retention')
              THEN 'OK (purge ACTIVE, ' ||
                   (SELECT value->>'months' FROM public.app_settings WHERE key = 'data_retention') || ' mois)'
            ELSE 'OK (purge désactivée — décision assumée)' END;

SELECT 'app_settings/ai_budget          ' ||
       CASE WHEN NOT EXISTS (SELECT 1 FROM public.app_settings WHERE key = 'ai_budget')
              THEN 'MANQUANT'
            WHEN NOT (SELECT (value->>'enabled')::boolean FROM public.app_settings WHERE key = 'ai_budget')
              THEN 'INERTE (enabled=false : aucune alerte ne partira)'
            WHEN (SELECT COALESCE((value->>'weekly_usd')::numeric, 0)
                       + COALESCE((value->>'monthly_usd')::numeric, 0)
                  FROM public.app_settings WHERE key = 'ai_budget') = 0
              THEN 'INERTE (plafonds à 0 — ai_budget.py:144 sort immédiatement)'
            ELSE 'OK (' ||
                 (SELECT value->>'weekly_usd' FROM public.app_settings WHERE key = 'ai_budget') ||
                 ' $/sem, ' ||
                 (SELECT value->>'monthly_usd' FROM public.app_settings WHERE key = 'ai_budget') ||
                 ' $/mois)' END;

-- ── Grille de scoring ───────────────────────────────────────────────
-- Table vide → services/scoring_settings.py:34 renvoie {} et le moteur applique
-- SES defaults, qui ne sont pas la grille calibrée. Aucune erreur nulle part :
-- seuls les scores changent.
SELECT 'scoring_config                  ' ||
       CASE WHEN NOT EXISTS (SELECT 1 FROM public.scoring_config)
              THEN 'MANQUANT (le moteur utilisera ses propres defaults)'
            ELSE 'OK (' || (SELECT count(*)::text FROM public.scoring_config) || ' ligne)' END;

-- ── Comptes ─────────────────────────────────────────────────────────
-- Zéro admin = personne ne peut administrer la plateforme, y compris pour
-- réparer. Sur une base neuve c'est le premier mur qu'on rencontre.
SELECT 'profiles (admins)               ' ||
       CASE WHEN (SELECT count(*) FROM public.profiles WHERE role = 'admin') = 0
              THEN 'MANQUANT (aucun admin — lancer scripts/bootstrap_admin.py)'
            ELSE 'OK (' || (SELECT count(*)::text FROM public.profiles WHERE role = 'admin') || ' admin)' END;

-- ── Référentiel de ciblage ──────────────────────────────────────────
-- services/notifications.py sélectionne les destinataires par `tier`. Table
-- vide, l'envoi d'un AO RÉUSSIT avec zéro destinataire : ni erreur, ni
-- avertissement, ni e-mail. Même famille de silence que data_retention.
SELECT 'partner_clients (ciblage AO)    ' ||
       CASE WHEN (SELECT count(*) FROM public.partner_clients) = 0
              THEN 'INERTE (aucune association partenaire/client : toute campagne partira à 0 destinataire)'
            ELSE 'OK (' || (SELECT count(*)::text FROM public.partner_clients) || ' association(s))' END;

-- ── Modèles d'e-mails ───────────────────────────────────────────────
-- Table vide = comportement NORMAL : services/email_templates.py rend les
-- modèles du dépôt. Affiché pour qu'un zéro ne se lise pas comme une perte.
SELECT 'email_templates                 OK (' ||
       (SELECT count(*)::text FROM public.email_templates) ||
       ' personnalisation(s) — 0 est normal, les modèles du dépôt s''appliquent)';

\pset tuples_only off
