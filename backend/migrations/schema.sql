-- ════════════════════════════════════════════════════════════════════
--  UTI / Groupement-IT — schéma complet de la base applicative.
--
--  UN SEUL FICHIER, UN SEUL ORDRE, AUCUNE DÉPENDANCE À SUPABASE.
--
--  Il remplace, pour toute RECONSTRUCTION, les ~46 fichiers SQL éparpillés
--  entre la racine du dépôt (supabase_schema.sql, supabase_migration_*.sql) et
--  backend/migrations/. Ces fichiers restent en place pour l'historique, mais
--  ils ne sont plus la source de vérité : leur ordre de dépendance n'était écrit
--  nulle part, et l'ordre alphabétique — le réflexe de tout `for f in *.sql` —
--  perdait six colonnes de ao_consultant_state en silence.
--
--  D'OÙ IL VIENT
--
--  Il n'a pas été écrit à la main. Il a été EXTRAIT d'une base réelle, obtenue en
--  rejouant tous les fichiers versionnés puis vérifiée objet par objet contre la
--  production Supabase : colonnes et types, index comparés par DÉFINITION et non
--  par nom, clés étrangères, activation de la RLS. L'écart final est nul.
--
--  CE QUI A ÉTÉ RETIRÉ DE SUPABASE, ET POURQUOI
--
--  * La clé étrangère profiles.id → auth.users(id). Le schéma `auth` appartenait
--    à GoTrue ; il n'existe plus.
--
--    Ce fichier ne crée AUCUNE table d'identifiants : il reproduit les 22
--    tables de la production, et le stockage des mots de passe n'en faisait pas
--    partie — il vivait dans auth.users. La table qui les portera est le
--    livrable du chantier « remplacement de GoTrue ». Tant qu'elle n'est pas
--    là, une base reconstruite ici est complète mais ne sait authentifier
--    personne. C'est un manque VISIBLE, pas un oubli.
--
--  * En conséquence, profiles.id reçoit un DEFAULT gen_random_uuid(). Il n'en
--    avait aucun : l'identifiant venait de la réponse de GoTrue à la création du
--    compte, et était passé explicitement à l'INSERT. Sans ce défaut, toute
--    création de profil échouerait sur une contrainte NOT NULL.
--
--  * Les policies RLS qui appelaient auth.uid() et public.current_app_role().
--    Elles n'existaient de toute façon PAS en production : `pg_policies` y est
--    vide. Ce qui existe, et qui est conservé ci-dessous, c'est l'activation de
--    la RLS SANS aucune policy — c'est-à-dire un refus total. Le backend passe
--    outre avec le rôle service_role. C'est une défense en profondeur : si
--    PostgREST se retrouvait un jour exposé avec une clé anon, il ne verrait
--    rien. Le coût est nul, on la garde.
--
--  * Les buckets `storage.*`. Les fichiers vivent sur OVH Object Storage
--    (services/storage.py, STORAGE_BACKEND=s3).
--
--  EXTENSIONS REQUISES : pgcrypto (gen_random_uuid) et pg_trgm (recherche
--  approximative sur les noms de clients). Toutes deux dans postgresql-contrib.
--
--  USAGE
--      sudo -u postgres createdb uti
--      cd backend/migrations
--      sudo -u postgres psql -d uti -v ON_ERROR_STOP=1 < schema.sql
--      sudo -u postgres psql -d uti -v ON_ERROR_STOP=1 < seed.sql
--
--  ⚠️  L'utilisateur `postgres` ne peut pas lire un fichier sous /home : les
--  répertoires personnels sont en 750, il ne peut même pas les traverser.
--  `sudo -u postgres psql -f ~/app/...` échoue donc sur « Permission denied ».
--  On REDIRIGE depuis son propre shell, qui a le droit de lire le fichier et
--  passe le descripteur au processus sudo. Résultat identique à -f, sans avoir
--  à copier les fichiers ni à ouvrir les droits du répertoire personnel.
--
--  Les données de référence ne sont PAS ici : voir seed.sql. Un schéma qui crée
--  des lignes métier n'est pas un schéma — c'est ce qui faisait apparaître deux
--  clients inventés dans toute base « vierge » reconstruite depuis le dépôt.
-- ════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Rôles PostgREST ─────────────────────────────────────────────────
-- Trois rôles, et UN SEUL contourne la RLS.
--
-- PostgREST se connecte avec `authenticator`, puis exécute SET ROLE vers le rôle
-- porté par la revendication `role` du JWT. C'est donc CE rôle-là, et non
-- l'authentificateur, qui décide de ce qui est visible.
--
-- POURQUOI service_role DOIT PORTER BYPASSRLS
--
-- Les 22 tables ont la RLS activée et zéro policy — un refus total. Un rôle qui
-- ne contourne pas la RLS y lit donc ZÉRO LIGNE, SANS LA MOINDRE ERREUR.
-- L'application ne tomberait pas : elle servirait des listes vides et un
-- PGRST116 « 0 rows » sur chaque `.single()`. C'est le pire mode de panne qui
-- soit, parce qu'il ressemble à une base vide plutôt qu'à une erreur — on
-- chercherait le problème du côté des données, pas des droits.
--
-- Ces rôles manquaient à la première version de ce fichier. Le test
-- d'acceptation passait quand même, parce que le BYPASSRLS avait été posé à la
-- main dans le bac à sable : le fonctionnement était vérifié, le
-- provisionnement ne l'était pas.
--
-- Aucun mot de passe ici : ce fichier est versionné. Celui d'`authenticator`
-- se pose au déploiement.
DO $$
BEGIN
  -- NOLOGIN : on n'ouvre jamais de connexion EN TANT QUE anon ou service_role ;
  -- on n'y accède que par SET ROLE depuis authenticator.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') THEN
    -- NOINHERIT : authenticator ne doit détenir AUCUN droit par héritage, sans
    -- quoi une requête sans jeton s'exécuterait avec les droits cumulés de ses
    -- rôles membres — donc avec ceux de service_role.
    CREATE ROLE authenticator LOGIN NOINHERIT;
  END IF;
END
$$;

-- Réaffirmé HORS du bloc de création : les rôles sont globaux au CLUSTER, pas à
-- la base. Une base reconstruite sur un cluster qui possède déjà un
-- `service_role` hériterait de ses attributs, BYPASSRLS compris ou non — et
-- l'écart resterait invisible jusqu'à la première lecture vide.
ALTER ROLE service_role BYPASSRLS;

GRANT anon, service_role TO authenticator;

CREATE TABLE public.ai_usage (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    provider text,
    model text,
    operation text,
    generation_id text,
    user_id uuid,
    user_email text,
    entity_type text,
    entity_id text,
    input_tokens integer,
    output_tokens integer,
    cached_tokens integer,
    cost_usd numeric(12,6),
    cost_source text,
    latency_ms integer
);

--
--

CREATE TABLE public.ao_consultant_state (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ao_id uuid NOT NULL,
    consultant_id uuid NOT NULL,
    human_rank integer,
    contact_status text DEFAULT 'none'::text NOT NULL,
    contacted_at timestamp with time zone,
    decided_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    refusal_reason text,
    client_decision text,
    client_decision_at timestamp with time zone,
    client_decision_note text,
    tjm_achat integer,
    tjm_vente integer,
    eval_points_forts text,
    eval_differenciants text,
    validation text,
    sent_to_client_at timestamp with time zone,
    commercial_exchange boolean DEFAULT false NOT NULL,
    deal_status text
);

--
--

CREATE TABLE public.app_settings (
    key text NOT NULL,
    value jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE public.appels_offres (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid,
    title text NOT NULL,
    description text NOT NULL,
    skills_required text NOT NULL,
    budget_max integer,
    location text,
    duration text,
    context text,
    ao_type text,
    deadline date,
    status text DEFAULT 'open'::text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    ai_summary text,
    reference text,
    source_files jsonb,
    work_mode text,
    latitude double precision,
    longitude double precision,
    langue_requise text,
    notified_at timestamp with time zone,
    list2_scheduled_at timestamp with time zone,
    list2_notified_at timestamp with time zone,
    last_relance_at timestamp with time zone,
    relance_count integer DEFAULT 0 NOT NULL,
    scoring_overrides jsonb,
    archived boolean DEFAULT false NOT NULL,
    archived_at timestamp with time zone,
    ao_outcome text,
    winning_partner_id uuid,
    outcome_note text,
    outcome_at timestamp with time zone,
    outcome_by uuid,
    is_draft boolean DEFAULT false NOT NULL,
    CONSTRAINT appels_offres_ao_outcome_chk CHECK (((ao_outcome IS NULL) OR (ao_outcome = ANY (ARRAY['pourvu'::text, 'non_pourvu'::text, 'sans_suite'::text])))),
    CONSTRAINT appels_offres_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text])))
);

--
--

CREATE TABLE public.audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    ao_id uuid,
    event_type text NOT NULL,
    actor_id uuid,
    model_version text,
    grid_version text,
    input_hash text,
    payload jsonb,
    severity text DEFAULT 'info'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE public.client_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token text NOT NULL,
    ao_id uuid NOT NULL,
    client_id uuid,
    created_by uuid,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE public.clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    sector text,
    logo_url text,
    contact_name text,
    contact_email text,
    parent_client_id uuid,
    perimetre text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    city text,
    latitude double precision,
    longitude double precision,
    tier text
);

--
--

CREATE TABLE public.consultants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    tjm integer,
    skills text NOT NULL,
    experience_years integer,
    availability text,
    employment_type text,
    email text,
    phone text,
    cv_url text,
    cv_text text,
    cv_filename text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    city text,
    latitude double precision,
    longitude double precision,
    availability_status text,
    available_from date,
    consent_at timestamp with time zone,
    purged_at timestamp with time zone,
    CONSTRAINT consultants_employment_type_check CHECK ((employment_type = ANY (ARRAY['independant'::text, 'salarie'::text])))
);

--
--

CREATE TABLE public.email_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    to_email text NOT NULL,
    to_name text,
    reply_to text,
    subject text NOT NULL,
    html text NOT NULL,
    text text,
    category text NOT NULL,
    template_key text,
    context jsonb,
    ao_id uuid,
    recipient_id uuid,
    created_by uuid,
    idempotency_key text,
    status text DEFAULT 'queued'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT email_outbox_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'sending'::text, 'sent'::text, 'dead'::text])))
);

--
--

CREATE TABLE public.email_templates (
    key text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    format text DEFAULT 'html'::text NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE public.human_decision (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ao_id uuid NOT NULL,
    submission_id uuid,
    consultant_id uuid,
    ai_rank integer,
    ai_score integer,
    decision text NOT NULL,
    justification text,
    decided_by uuid NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT human_decision_decision_check CHECK ((decision = ANY (ARRAY['retained'::text, 'rejected'::text, 'overridden'::text])))
);

--
--

CREATE TABLE public.invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token text NOT NULL,
    email text NOT NULL,
    name text,
    role text DEFAULT 'ao'::text NOT NULL,
    invited_by uuid,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    used_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    org text,
    CONSTRAINT invitations_org_check CHECK (((org IS NULL) OR (org = ANY (ARRAY['uti'::text, 'groupement-it'::text])))),
    CONSTRAINT invitations_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'commerce'::text, 'ao'::text])))
);

--
--

CREATE TABLE public.matchings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ao_id uuid,
    submission_id uuid,
    consultant_id uuid NOT NULL,
    score_total integer NOT NULL,
    breakdown jsonb,
    points_forts jsonb,
    points_faibles jsonb,
    resume_matching text,
    recommandation text,
    rank integer,
    cost_usd numeric(10,4) DEFAULT 0,
    ran_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    score_llm integer,
    score_hybride integer,
    agreement integer,
    llm_breakdown jsonb,
    llm_global text,
    hybrid_breakdown jsonb,
    weights jsonb,
    langues jsonb
);

--
--

CREATE TABLE public.pac_clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pac_id uuid NOT NULL,
    client_id uuid NOT NULL,
    tier text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT pac_clients_tier_check CHECK ((tier = ANY (ARRAY['list_1'::text, 'list_2'::text, 'suspended'::text])))
);

--
--

CREATE TABLE public.pacs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now()
);

--
--

CREATE TABLE public.partner_clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    partner_id uuid NOT NULL,
    client_id uuid NOT NULL,
    tier text NOT NULL,
    assigned_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    assigned_at timestamp with time zone DEFAULT now(),
    CONSTRAINT partner_clients_tier_check CHECK ((tier = ANY (ARRAY['list_1'::text, 'list_2'::text, 'suspended'::text])))
);

--
--

CREATE TABLE public.partner_compliance_docs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    partner_id uuid NOT NULL,
    doc_type text NOT NULL,
    file_url text,
    filename text,
    issued_at date,
    authenticity_checked_at timestamp with time zone,
    authenticity_ref text,
    checked_by uuid,
    uploaded_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT partner_compliance_docs_doc_type_check CHECK ((doc_type = ANY (ARRAY['vigilance'::text, 'immatriculation'::text, 'salaries_etrangers'::text])))
);

--
--

CREATE TABLE public.partner_email_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ao_id uuid,
    recipient_id uuid,
    recipient_email text,
    kind text,
    status text,
    error text,
    sent_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
--

CREATE TABLE public.profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    avatar_url text,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    org text,
    status text DEFAULT 'active'::text NOT NULL,
    last_login_ip text,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_secret text,
    mfa_required boolean DEFAULT true NOT NULL,
    title text,
    phone text,
    preferred_language text DEFAULT 'fr'::text NOT NULL,
    notif_deadline_alerts boolean DEFAULT true NOT NULL,
    notif_missing_info boolean DEFAULT true NOT NULL,
    ai_literacy_ack_at timestamp with time zone,
    ai_literacy_version text,
    CONSTRAINT profiles_org_check CHECK (((org IS NULL) OR (org = ANY (ARRAY['uti'::text, 'groupement-it'::text])))),
    CONSTRAINT profiles_preferred_language_check CHECK ((preferred_language = ANY (ARRAY['fr'::text, 'en'::text]))),
    CONSTRAINT profiles_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'commerce'::text, 'ao'::text]))),
    CONSTRAINT profiles_status_check CHECK ((status = ANY (ARRAY['active'::text, 'suspended'::text, 'disabled'::text])))
);

--
--

CREATE TABLE public.scoring_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    w_competences integer DEFAULT 40 NOT NULL,
    w_seniorite integer DEFAULT 20 NOT NULL,
    w_contexte integer DEFAULT 20 NOT NULL,
    w_tjm integer DEFAULT 20 NOT NULL,
    seniority_full_years integer DEFAULT 8 NOT NULL,
    reco_fort_min integer DEFAULT 75 NOT NULL,
    reco_moyen_min integer DEFAULT 50 NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    s_competences smallint,
    s_seniorite smallint,
    s_contexte smallint,
    s_tjm smallint,
    s_points_forts_cv smallint,
    s_elements_differenciants smallint,
    w_points_forts_cv integer DEFAULT 0 NOT NULL,
    w_elements_differenciants integer DEFAULT 0 NOT NULL,
    CONSTRAINT scoring_reco_order CHECK ((reco_fort_min > reco_moyen_min)),
    CONSTRAINT scoring_weights_sum CHECK (((((((w_competences + w_seniorite) + w_contexte) + w_points_forts_cv) + w_elements_differenciants) + w_tjm) = 100))
);

--
--

CREATE TABLE public.submissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ao_id uuid NOT NULL,
    consultant_id uuid NOT NULL,
    submitted_by uuid,
    cv_url text,
    cv_text text,
    cv_filename text,
    submitted_at timestamp with time zone DEFAULT now(),
    worked_at_client boolean,
    worked_at_client_exit_date date,
    points_forts text,
    elements_differenciants text,
    cv_structured jsonb,
    purged_at timestamp with time zone
);

--
--

CREATE TABLE public.support_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    from_name text NOT NULL,
    from_email text NOT NULL,
    type text DEFAULT 'question'::text NOT NULL,
    subject text NOT NULL,
    message text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT support_messages_status_check CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text]))),
    CONSTRAINT support_messages_type_check CHECK ((type = ANY (ARRAY['bug'::text, 'question'::text, 'suggestion'::text, 'other'::text])))
);

--
--

ALTER TABLE ONLY public.ai_usage
    ADD CONSTRAINT ai_usage_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.ao_consultant_state
    ADD CONSTRAINT ao_consultant_state_ao_id_consultant_id_key UNIQUE (ao_id, consultant_id);

--
--

ALTER TABLE ONLY public.ao_consultant_state
    ADD CONSTRAINT ao_consultant_state_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);

--
--

ALTER TABLE ONLY public.appels_offres
    ADD CONSTRAINT appels_offres_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.client_reviews
    ADD CONSTRAINT client_reviews_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.client_reviews
    ADD CONSTRAINT client_reviews_token_key UNIQUE (token);

--
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.consultants
    ADD CONSTRAINT consultants_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.email_outbox
    ADD CONSTRAINT email_outbox_idempotency_key_key UNIQUE (idempotency_key);

--
--

ALTER TABLE ONLY public.email_outbox
    ADD CONSTRAINT email_outbox_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.email_templates
    ADD CONSTRAINT email_templates_pkey PRIMARY KEY (key);

--
--

ALTER TABLE ONLY public.human_decision
    ADD CONSTRAINT human_decision_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_token_key UNIQUE (token);

--
--

ALTER TABLE ONLY public.matchings
    ADD CONSTRAINT matchings_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pac_clients
    ADD CONSTRAINT pac_clients_pac_id_client_id_key UNIQUE (pac_id, client_id);

--
--

ALTER TABLE ONLY public.pac_clients
    ADD CONSTRAINT pac_clients_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.pacs
    ADD CONSTRAINT pacs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.partner_clients
    ADD CONSTRAINT partner_clients_partner_id_client_id_key UNIQUE (partner_id, client_id);

--
--

ALTER TABLE ONLY public.partner_clients
    ADD CONSTRAINT partner_clients_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.partner_compliance_docs
    ADD CONSTRAINT partner_compliance_docs_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.partner_email_log
    ADD CONSTRAINT partner_email_log_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_email_key UNIQUE (email);

--
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.scoring_config
    ADD CONSTRAINT scoring_config_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_ao_id_consultant_id_key UNIQUE (ao_id, consultant_id);

--
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);

--
--

ALTER TABLE ONLY public.support_messages
    ADD CONSTRAINT support_messages_pkey PRIMARY KEY (id);

--
--

CREATE INDEX ai_usage_created_idx ON public.ai_usage USING btree (created_at DESC);

--
--

CREATE INDEX ai_usage_entity_idx ON public.ai_usage USING btree (entity_type, entity_id);

--
--

CREATE INDEX ai_usage_operation_idx ON public.ai_usage USING btree (operation);

--
--

CREATE INDEX ai_usage_user_idx ON public.ai_usage USING btree (user_id);

--
--

CREATE INDEX idx_ao_consultant_state_ao ON public.ao_consultant_state USING btree (ao_id);

--
--

CREATE INDEX idx_aos_archived ON public.appels_offres USING btree (archived);

--
--

CREATE INDEX idx_aos_auto_archive ON public.appels_offres USING btree (deadline) WHERE ((archived = false) AND (archived_at IS NULL));

--
--

CREATE INDEX idx_aos_client_id ON public.appels_offres USING btree (client_id);

--
--

CREATE INDEX idx_aos_created_at ON public.appels_offres USING btree (created_at DESC);

--
--

CREATE INDEX idx_aos_is_draft ON public.appels_offres USING btree (is_draft) WHERE (is_draft = true);

--
--

CREATE INDEX idx_aos_status ON public.appels_offres USING btree (status);

--
--

CREATE INDEX idx_appels_offres_ao_outcome ON public.appels_offres USING btree (ao_outcome);

--
--

CREATE INDEX idx_audit_log_ao_id ON public.audit_log USING btree (ao_id);

--
--

CREATE INDEX idx_audit_log_created_at ON public.audit_log USING btree (created_at DESC);

--
--

CREATE INDEX idx_audit_log_run_id ON public.audit_log USING btree (run_id);

--
--

CREATE INDEX idx_client_reviews_ao ON public.client_reviews USING btree (ao_id);

--
--

CREATE INDEX idx_client_reviews_token ON public.client_reviews USING btree (token);

--
--

CREATE INDEX idx_clients_created_at ON public.clients USING btree (created_at DESC);

--
--

CREATE INDEX idx_clients_name ON public.clients USING btree (name);

--
--

CREATE INDEX idx_clients_name_trgm ON public.clients USING gin (name public.gin_trgm_ops);

--
--

CREATE UNIQUE INDEX idx_clients_name_unique_lower ON public.clients USING btree (lower(name));

--
--

CREATE INDEX idx_clients_parent ON public.clients USING btree (parent_client_id);

--
--

CREATE INDEX idx_consultants_availability ON public.consultants USING btree (availability_status) WHERE (availability_status IS NOT NULL);

--
--

CREATE INDEX idx_consultants_created_at ON public.consultants USING btree (created_at DESC);

--
--

CREATE INDEX idx_consultants_created_by ON public.consultants USING btree (created_by);

--
--

CREATE INDEX idx_consultants_purge ON public.consultants USING btree (created_at) WHERE (purged_at IS NULL);

--
--

CREATE INDEX idx_email_outbox_ao ON public.email_outbox USING btree (ao_id);

--
--

CREATE INDEX idx_email_outbox_claimed ON public.email_outbox USING btree (claimed_at) WHERE (status = 'sending'::text);

--
--

CREATE INDEX idx_email_outbox_created ON public.email_outbox USING btree (created_at DESC);

--
--

CREATE INDEX idx_email_outbox_ready ON public.email_outbox USING btree (next_attempt_at) WHERE (status = 'queued'::text);

--
--

CREATE INDEX idx_email_outbox_recipient ON public.email_outbox USING btree (recipient_id);

--
--

CREATE INDEX idx_human_decision_ao_id ON public.human_decision USING btree (ao_id);

--
--

CREATE INDEX idx_human_decision_decided_by ON public.human_decision USING btree (decided_by);

--
--

CREATE INDEX idx_matchings_ao_id ON public.matchings USING btree (ao_id);

--
--

CREATE INDEX idx_matchings_rank ON public.matchings USING btree (ao_id, rank);

--
--

CREATE INDEX idx_matchings_submission ON public.matchings USING btree (submission_id);

--
--

CREATE INDEX idx_pac_clients_client_id ON public.pac_clients USING btree (client_id);

--
--

CREATE INDEX idx_pac_clients_pac_id ON public.pac_clients USING btree (pac_id);

--
--

CREATE INDEX idx_pacs_created_at ON public.pacs USING btree (created_at DESC);

--
--

CREATE INDEX idx_partner_clients_client ON public.partner_clients USING btree (client_id);

--
--

CREATE INDEX idx_partner_clients_partner ON public.partner_clients USING btree (partner_id);

--
--

CREATE INDEX idx_partner_clients_tier ON public.partner_clients USING btree (tier);

--
--

CREATE INDEX idx_partner_compliance_lookup ON public.partner_compliance_docs USING btree (partner_id, doc_type, issued_at DESC);

--
--

CREATE INDEX idx_partner_email_log_ao ON public.partner_email_log USING btree (ao_id);

--
--

CREATE INDEX idx_partner_email_log_created ON public.partner_email_log USING btree (created_at DESC);

--
--

CREATE INDEX idx_submissions_ao_id ON public.submissions USING btree (ao_id);

--
--

CREATE INDEX idx_submissions_consultant ON public.submissions USING btree (consultant_id);

--
--

CREATE INDEX idx_submissions_purge ON public.submissions USING btree (submitted_at) WHERE (purged_at IS NULL);

--
--

CREATE INDEX idx_submissions_submitted_by ON public.submissions USING btree (submitted_by);

--
--

ALTER TABLE ONLY public.ao_consultant_state
    ADD CONSTRAINT ao_consultant_state_ao_id_fkey FOREIGN KEY (ao_id) REFERENCES public.appels_offres(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.ao_consultant_state
    ADD CONSTRAINT ao_consultant_state_consultant_id_fkey FOREIGN KEY (consultant_id) REFERENCES public.consultants(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.appels_offres
    ADD CONSTRAINT appels_offres_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.appels_offres
    ADD CONSTRAINT appels_offres_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.client_reviews
    ADD CONSTRAINT client_reviews_ao_id_fkey FOREIGN KEY (ao_id) REFERENCES public.appels_offres(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.client_reviews
    ADD CONSTRAINT client_reviews_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_parent_client_id_fkey FOREIGN KEY (parent_client_id) REFERENCES public.clients(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.consultants
    ADD CONSTRAINT consultants_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.email_outbox
    ADD CONSTRAINT email_outbox_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.human_decision
    ADD CONSTRAINT human_decision_ao_id_fkey FOREIGN KEY (ao_id) REFERENCES public.appels_offres(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.human_decision
    ADD CONSTRAINT human_decision_consultant_id_fkey FOREIGN KEY (consultant_id) REFERENCES public.consultants(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.human_decision
    ADD CONSTRAINT human_decision_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.profiles(id) ON DELETE RESTRICT;
-- RESTRICT, et non SET NULL : `decided_by` est NOT NULL, donc un SET NULL ne
-- « détache » pas la trace — il fait ÉCHOUER la suppression du compte sur une
-- violation de NOT NULL. La base refusait déjà de supprimer, mais pour la
-- mauvaise raison et avec un message qui ne désigne pas la règle. RESTRICT,
-- lui, l'énonce : une trace de décision humaine (AI Act art. 14) ne se détruit
-- pas avec le compte de son auteur.
--
-- Le droit à l'effacement (RGPD art. 17) reste servi, mais en ANONYMISANT la
-- ligne profiles au lieu de la supprimer — exactement ce que
-- services/data_retention.py fait déjà des consultants, et pour le motif qu'il
-- énonce lui-même. Le raisonnement vaut plus fortement encore ici : un
-- consultant est le SUJET de la décision, l'opérateur en est le RESPONSABLE.

--
--

ALTER TABLE ONLY public.human_decision
    ADD CONSTRAINT human_decision_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES public.submissions(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_invited_by_fkey FOREIGN KEY (invited_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT invitations_used_by_fkey FOREIGN KEY (used_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.matchings
    ADD CONSTRAINT matchings_ao_id_fkey FOREIGN KEY (ao_id) REFERENCES public.appels_offres(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.matchings
    ADD CONSTRAINT matchings_consultant_id_fkey FOREIGN KEY (consultant_id) REFERENCES public.consultants(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.matchings
    ADD CONSTRAINT matchings_ran_by_fkey FOREIGN KEY (ran_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.matchings
    ADD CONSTRAINT matchings_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES public.submissions(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.pac_clients
    ADD CONSTRAINT pac_clients_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.pac_clients
    ADD CONSTRAINT pac_clients_pac_id_fkey FOREIGN KEY (pac_id) REFERENCES public.pacs(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.pacs
    ADD CONSTRAINT pacs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.partner_clients
    ADD CONSTRAINT partner_clients_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.partner_clients
    ADD CONSTRAINT partner_clients_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.partner_clients
    ADD CONSTRAINT partner_clients_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.partner_compliance_docs
    ADD CONSTRAINT partner_compliance_docs_checked_by_fkey FOREIGN KEY (checked_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.partner_compliance_docs
    ADD CONSTRAINT partner_compliance_docs_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.partner_compliance_docs
    ADD CONSTRAINT partner_compliance_docs_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.scoring_config
    ADD CONSTRAINT scoring_config_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_ao_id_fkey FOREIGN KEY (ao_id) REFERENCES public.appels_offres(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_consultant_id_fkey FOREIGN KEY (consultant_id) REFERENCES public.consultants(id) ON DELETE CASCADE;

--
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_submitted_by_fkey FOREIGN KEY (submitted_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE ONLY public.support_messages
    ADD CONSTRAINT support_messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE SET NULL;

--
--

ALTER TABLE public.ai_usage ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.ao_consultant_state ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.app_settings ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.appels_offres ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.client_reviews ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.consultants ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.email_outbox ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.email_templates ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.human_decision ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.matchings ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.pac_clients ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.pacs ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.partner_clients ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.partner_compliance_docs ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.partner_email_log ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.scoring_config ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;

--
--

ALTER TABLE public.support_messages ENABLE ROW LEVEL SECURITY;

--
--

-- ── Droits ──────────────────────────────────────────────────────────
-- `anon` ne reçoit RIEN — pas même USAGE sur le schéma. Une requête portant un
-- jeton `anon` se heurte donc à « permission denied », bruyamment, AVANT
-- d'atteindre la RLS. Deux verrous indépendants plutôt qu'un : le retrait
-- accidentel de l'un laisse l'autre debout.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;

-- Les tables créées PLUS TARD (migrations 0019 et suivantes) doivent être
-- accessibles au backend sans qu'on pense à repasser un GRANT. Sans cette
-- ligne, une table neuve renverrait « permission denied » en production le jour
-- de son déploiement, et seulement là.
--
-- Ne vaut que pour les objets créés par le rôle qui exécute ce fichier : jouer
-- schema.sql ET les migrations suivantes avec le MÊME compte propriétaire.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_role;
