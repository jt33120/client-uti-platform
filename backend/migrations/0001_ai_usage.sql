-- Registre d'usage IA — source de vérité du coût des appels LLM (services/ai_ledger.py).
-- À exécuter dans Supabase → SQL Editor. Idempotent (IF NOT EXISTS).
create table if not exists public.ai_usage (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  provider      text,               -- openrouter | mistral | provider
  model         text,
  operation     text,               -- extraction | scoring | draft | summary | assistant | harmonize
  generation_id text,               -- id OpenRouter (audit → GET /api/v1/generation?id=…)
  user_id       uuid,               -- compte à l'origine de l'appel (null si tâche système)
  user_email    text,
  entity_type   text,               -- ao | consultant | matching | assistant
  entity_id     text,
  input_tokens  integer,
  output_tokens integer,
  cached_tokens integer,
  cost_usd      numeric(12,6),      -- coût réel OpenRouter si dispo, sinon estimation
  cost_source   text,               -- openrouter (facturé) | provider | estimate | none
  latency_ms    integer
);

create index if not exists ai_usage_created_idx   on public.ai_usage (created_at desc);
create index if not exists ai_usage_operation_idx on public.ai_usage (operation);
create index if not exists ai_usage_entity_idx    on public.ai_usage (entity_type, entity_id);
create index if not exists ai_usage_user_idx       on public.ai_usage (user_id);

-- Recharge le cache de schéma PostgREST (sinon PGRST204 sur les nouvelles colonnes).
notify pgrst, 'reload schema';
