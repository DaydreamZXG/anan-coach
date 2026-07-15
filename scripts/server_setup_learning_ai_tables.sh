#!/usr/bin/env bash
set -euo pipefail

HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"

ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  "root@$HOST" \
  "DB_NAME='$DB_NAME' bash -s" <<'REMOTE'
set -euo pipefail

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQL'
create schema if not exists learner;
create schema if not exists ai_audit;
create schema if not exists product;
create schema if not exists commerce;
create extension if not exists pgcrypto;
create extension if not exists pg_trgm;

create table if not exists learner.users (
  user_id uuid primary key,
  display_name text,
  email text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists learner.apple_identities (
  identity_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  apple_sub text not null unique,
  email text,
  email_verified boolean not null default false,
  is_private_email boolean not null default false,
  last_identity_token_hash text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists learner_apple_identities_user_idx
  on learner.apple_identities(user_id);

create table if not exists learner.wechat_identities (
  identity_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  app_id text not null,
  open_id text not null,
  union_id text,
  session_key_hash text not null check (length(session_key_hash) = 64),
  last_login_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (app_id, open_id)
);

create index if not exists learner_wechat_identities_user_idx
  on learner.wechat_identities(user_id);

create index if not exists learner_wechat_identities_union_idx
  on learner.wechat_identities(union_id)
  where union_id is not null;

create table if not exists learner.app_sessions (
  session_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  session_token_hash text not null unique,
  device_id text not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create index if not exists learner_app_sessions_user_idx
  on learner.app_sessions(user_id, expires_at desc);

create table if not exists learner.profiles (
  user_id uuid primary key references learner.users(user_id),
  target_exam text,
  target_region text,
  position_group text,
  daily_minutes integer,
  education text,
  major text,
  fresh_graduate boolean,
  political_status text,
  grassroots_years integer,
  study_mode text,
  target_position_id uuid,
  target_score numeric(6,2),
  baseline_source text,
  baseline_xingce_score numeric(5,2),
  baseline_shenlun_score numeric(5,2),
  baseline_weak_modules jsonb,
  baseline_module_snapshot jsonb,
  onboarding_complete boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table learner.profiles
  add column if not exists education text,
  add column if not exists major text,
  add column if not exists fresh_graduate boolean,
  add column if not exists political_status text,
  add column if not exists grassroots_years integer,
  add column if not exists study_mode text,
  add column if not exists target_position_id uuid,
  add column if not exists target_score numeric(6,2),
  add column if not exists baseline_source text,
  add column if not exists baseline_xingce_score numeric(5,2),
  add column if not exists baseline_shenlun_score numeric(5,2),
  add column if not exists baseline_weak_modules jsonb,
  add column if not exists baseline_module_snapshot jsonb;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'learner_profiles_study_mode_check'
  ) then
    alter table learner.profiles
      add constraint learner_profiles_study_mode_check
      check (study_mode is null or study_mode in ('fulltime', 'parttime'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'learner_profiles_baseline_source_check'
  ) then
    alter table learner.profiles
      add constraint learner_profiles_baseline_source_check
      check (baseline_source is null or baseline_source in ('self_report', 'placement_test', 'none'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'learner_profiles_daily_minutes_check'
  ) then
    alter table learner.profiles
      add constraint learner_profiles_daily_minutes_check
      check (daily_minutes is null or daily_minutes between 30 and 720);
  end if;
end $$;

create table if not exists learner.placement_attempts (
  attempt_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  task_id text not null,
  question_ids jsonb not null,
  answers jsonb not null,
  module_snapshot jsonb not null,
  estimated_xingce_score numeric(5,2) not null,
  created_at timestamptz not null default now()
);

create table if not exists learner.practice_answers (
  answer_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  task_id text not null,
  question_id text not null,
  selected_answer text not null,
  correct_answer text,
  correct boolean not null,
  answered_at timestamptz not null default now()
);

create index if not exists learner_practice_answers_user_idx
  on learner.practice_answers(user_id, answered_at desc);

create table if not exists learner.external_question_intakes (
  intake_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  source text not null check (source = 'external'),
  stem text not null,
  options jsonb not null default '[]'::jsonb,
  selected_answer text,
  correct_answer text,
  extracted_tags jsonb not null default '[]'::jsonb,
  candidate_nodes jsonb not null default '[]'::jsonb,
  selected_node_id text,
  match_confidence numeric(5,4),
  status text not null check (status in ('pending_confirmation', 'rejected', 'confirmed')),
  rejection_reason text,
  answer_preview text,
  created_at timestamptz not null default now(),
  confirmed_at timestamptz
);

create index if not exists learner_external_question_intakes_user_idx
  on learner.external_question_intakes(user_id, created_at desc);

create table if not exists learner.external_wrong_questions (
  external_question_id uuid primary key default gen_random_uuid(),
  intake_id uuid not null unique references learner.external_question_intakes(intake_id),
  user_id uuid not null references learner.users(user_id),
  node_id text not null,
  source text not null check (source = 'external'),
  stem text not null,
  options jsonb not null default '[]'::jsonb,
  selected_answer text,
  correct_answer text,
  error_summary text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists learner_external_wrong_questions_user_idx
  on learner.external_wrong_questions(user_id, created_at desc);

create index if not exists learner_external_wrong_questions_node_idx
  on learner.external_wrong_questions(node_id, created_at desc);

create table if not exists learner.mock_exam_sessions (
  session_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  exam_scope text not null,
  track text not null check (track = 'xingce'),
  status text not null check (status in ('assigned', 'submitted', 'reviewed')),
  question_ids jsonb not null,
  blueprint jsonb not null,
  answers jsonb,
  review_payload jsonb,
  review_viewed_at timestamptz,
  submitted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists learner_mock_exam_sessions_user_created_idx
  on learner.mock_exam_sessions(user_id, created_at desc);

create index if not exists learner_mock_exam_sessions_unviewed_idx
  on learner.mock_exam_sessions(user_id, submitted_at desc)
  where review_payload is not null and review_viewed_at is null;

create table if not exists learner.dockets (
  docket_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  docket_date date not null,
  status text not null check (status in ('active', 'completed', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, docket_date, status)
);

create index if not exists learner_dockets_user_date_idx
  on learner.dockets(user_id, docket_date desc);

create table if not exists learner.docket_cards (
  card_id uuid primary key default gen_random_uuid(),
  docket_id uuid not null references learner.dockets(docket_id),
  question_id text references product.questions(question_id),
  type text not null check (type in ('daily_affair')),
  track text not null check (track in ('xingce', 'shenlun')),
  module_name text,
  world_label text,
  title text not null,
  narrative text not null,
  interaction text not null check (interaction in ('swipe_verdict')),
  source_grounding text not null,
  status text not null check (status in ('pending', 'completed', 'skipped')),
  sort_order integer not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (docket_id, sort_order)
);

create index if not exists learner_docket_cards_docket_idx
  on learner.docket_cards(docket_id, sort_order);

create table if not exists learner.verdicts (
  verdict_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  docket_id uuid not null references learner.dockets(docket_id),
  card_id uuid not null references learner.docket_cards(card_id),
  question_id text not null references product.questions(question_id),
  selected_answer text not null,
  correct boolean not null,
  elapsed_seconds integer,
  created_at timestamptz not null default now(),
  unique (user_id, card_id)
);

create index if not exists learner_verdicts_user_created_idx
  on learner.verdicts(user_id, created_at desc);

create table if not exists ai_audit.usage_daily (
  learner_key text not null,
  feature text not null,
  usage_date date not null,
  used_count integer not null default 0,
  limit_count integer not null,
  updated_at timestamptz not null default now(),
  primary key (learner_key, feature, usage_date)
);

create table if not exists ai_audit.interaction_logs (
  log_id text primary key,
  learner_key text not null,
  feature text not null,
  provider text not null,
  model text not null,
  status text not null,
  request_summary text not null,
  response_summary text not null,
  latency_ms integer not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists ai_interaction_logs_learner_feature_idx
  on ai_audit.interaction_logs(learner_key, feature, created_at desc);

create table if not exists product.method_cards (
  id text primary key,
  exam_scope text not null,
  track text not null,
  module_name text not null,
  tag_path text not null,
  title text not null,
  recognition text not null,
  applicability jsonb not null,
  method_steps jsonb not null,
  error_signatures jsonb not null,
  fix_action text not null,
  time_budget_sec integer not null,
  accuracy_rule text not null,
  pitfall text,
  author_note text not null,
  validation jsonb not null,
  priority integer not null,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_method_cards_track_check'
  ) then
    alter table product.method_cards
      add constraint product_method_cards_track_check
      check (track in ('xingce', 'shenlun'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_method_cards_status_check'
  ) then
    alter table product.method_cards
      add constraint product_method_cards_status_check
      check (status in ('draft', 'published'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_method_cards_time_budget_check'
  ) then
    alter table product.method_cards
      add constraint product_method_cards_time_budget_check
      check (time_budget_sec > 0);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_method_cards_priority_check'
  ) then
    alter table product.method_cards
      add constraint product_method_cards_priority_check
      check (priority between 1 and 5);
  end if;
end $$;

create index if not exists product_method_cards_status_scope_idx
  on product.method_cards(status, exam_scope, track, module_name, tag_path);

create table if not exists product.exam_tag_weights (
  exam_scope text not null,
  track text not null,
  module_name text not null,
  tag_type text not null,
  tag_value text not null,
  window_start_year integer not null,
  window_end_year integer not null,
  question_count integer not null,
  occurrence_count integer not null,
  weight numeric(12,8) not null,
  source_query jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (
    exam_scope,
    track,
    module_name,
    tag_type,
    tag_value,
    window_start_year,
    window_end_year
  )
);

create index if not exists product_exam_tag_weights_scope_rank_idx
  on product.exam_tag_weights(
    exam_scope,
    track,
    tag_type,
    weight desc,
    occurrence_count desc
  );

create table if not exists product.knowledge_map_nodes (
  node_id text primary key,
  exam_scope text not null,
  track text not null,
  module_name text not null,
  node_type text not null,
  tag_type text,
  tag_value text not null,
  display_path text not null,
  parent_node_id text,
  aliases jsonb not null default '[]'::jsonb,
  support_status text not null,
  near5_weight numeric(12,8),
  near5_occurrence_count integer,
  stable_for_old_questions boolean not null default false,
  method_card_ids jsonb not null default '[]'::jsonb,
  evidence jsonb not null default '{}'::jsonb,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table product.knowledge_map_nodes
  add column if not exists near5_window_start_year integer,
  add column if not exists near5_window_end_year integer;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_knowledge_map_nodes_track_check'
  ) then
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_track_check
      check (track in ('xingce', 'shenlun'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_knowledge_map_nodes_type_check'
  ) then
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_type_check
      check (node_type in ('module', 'question_type', 'knowledge_family', 'knowledge_point', 'micro_skill'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_knowledge_map_nodes_support_check'
  ) then
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_support_check
      check (support_status in ('text_supported', 'image_unsupported', 'draft', 'retired'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_knowledge_map_nodes_status_check'
  ) then
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_status_check
      check (status in ('draft', 'published'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'product_knowledge_map_nodes_scope_track_module_type_tag_key'
  ) then
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_scope_track_module_type_tag_key
      unique (exam_scope, track, module_name, node_type, tag_type, tag_value);
  end if;
end $$;

create index if not exists product_knowledge_map_nodes_rank_idx
  on product.knowledge_map_nodes(
    exam_scope,
    track,
    status,
    support_status,
    near5_weight desc nulls last,
    near5_occurrence_count desc nulls last
  );

create index if not exists product_knowledge_map_nodes_tag_idx
  on product.knowledge_map_nodes(exam_scope, track, module_name, tag_type, tag_value);

create table if not exists product.position_systems (
  system_code text primary key,
  name_zh text not null,
  paper_type text not null,
  is_big_six boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists product.positions (
  position_id uuid primary key default gen_random_uuid(),
  source_id text not null unique,
  year integer not null,
  position_code text not null,
  name text not null,
  department_full text not null,
  system_code text not null,
  region text not null,
  city text,
  paper_type text not null,
  headcount integer not null default 1,
  education_min text,
  major_required text,
  political_req text,
  hukou_req text,
  service_min_yrs integer,
  remark text,
  raw_json jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_positions_year_code_department_key'
  ) then
    alter table product.positions
      add constraint product_positions_year_code_department_key
      unique (year, position_code, department_full);
  end if;
end $$;

create index if not exists product_positions_year_region_idx
  on product.positions(year, region, system_code);

create index if not exists product_positions_major_trgm_idx
  on product.positions using gin (major_required gin_trgm_ops);

create table if not exists product.position_score_histories (
  score_history_id uuid primary key default gen_random_uuid(),
  source_id text not null unique,
  position_code text not null,
  year integer not null,
  entry_score_min real,
  entry_score_max real,
  admission_min real,
  admission_max real,
  signup_count integer,
  ratio real,
  data_source text,
  raw_json jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_position_score_histories_code_year_key'
  ) then
    alter table product.position_score_histories
      add constraint product_position_score_histories_code_year_key
      unique (position_code, year);
  end if;
end $$;

create table if not exists product.position_score_links (
  position_id uuid not null references product.positions(position_id),
  score_history_id uuid not null references product.position_score_histories(score_history_id),
  match_method text not null,
  confidence text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_id, score_history_id)
);

create table if not exists product.position_entry_score_evidence (
  evidence_id uuid primary key default gen_random_uuid(),
  year integer not null check (year between 2000 and 2100),
  department_code text not null check (department_code <> ''),
  position_code text not null check (position_code <> ''),
  agency text not null check (agency <> ''),
  bureau text not null check (bureau <> ''),
  position_title text not null check (position_title <> ''),
  entry_score_min numeric(6,2) not null check (entry_score_min > 0),
  source_name text not null check (source_name <> ''),
  source_url text,
  source_file_sha256 text not null check (length(source_file_sha256) = 64),
  candidate_count integer not null check (candidate_count > 0),
  raw_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_position_entry_score_evidence_year_dept_position_key'
  ) then
    alter table product.position_entry_score_evidence
      add constraint product_position_entry_score_evidence_year_dept_position_key
      unique (year, department_code, position_code);
  end if;
end $$;

create index if not exists product_position_entry_score_evidence_code_idx
  on product.position_entry_score_evidence(year, position_code, department_code);

create table if not exists product.position_entry_score_links (
  position_id uuid not null references product.positions(position_id),
  evidence_id uuid not null references product.position_entry_score_evidence(evidence_id),
  match_method text not null,
  confidence text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_id, evidence_id)
);

create index if not exists product_position_entry_score_links_evidence_idx
  on product.position_entry_score_links(evidence_id);

create table if not exists product.system_score_histories (
  system_score_history_id uuid primary key default gen_random_uuid(),
  source_id text not null unique,
  system_code text not null,
  region text not null,
  year integer not null,
  entry_score_min real,
  entry_score_max real,
  admission_min real,
  admission_max real,
  confidence text not null default 'aggregated',
  data_source text,
  raw_json jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'product_system_score_histories_system_region_year_key'
  ) then
    alter table product.system_score_histories
      add constraint product_system_score_histories_system_region_year_key
      unique (system_code, region, year);
  end if;
end $$;

create table if not exists product.position_tags (
  position_source_id text not null,
  tag text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_source_id, tag)
);

create table if not exists learner.essay_attempts (
  attempt_id text primary key,
  learner_key text not null,
  task_id text not null,
  original_answer text not null,
  review_payload jsonb not null,
  rewritten_answer text,
  comparison_payload jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists learner_essay_attempts_learner_idx
  on learner.essay_attempts(learner_key, updated_at desc);

create table if not exists commerce.iap_products (
  product_row_id uuid primary key default gen_random_uuid(),
  platform text not null default 'ios',
  product_id text not null unique,
  tier text not null,
  billing_period text not null,
  title text not null,
  subtitle text not null,
  price_band text not null,
  sort_order integer not null,
  feature_highlights jsonb not null default '[]'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists commerce.user_entitlements (
  entitlement_id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references learner.users(user_id),
  tier text not null default 'free',
  source text not null default 'free',
  subscription_start timestamptz,
  subscription_end timestamptz,
  product_ids jsonb not null default '[]'::jsonb,
  features jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists commerce.app_store_transactions (
  transaction_row_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  product_id text not null references commerce.iap_products(product_id),
  original_transaction_id text not null,
  transaction_id text not null unique,
  web_order_line_item_id text,
  environment text not null,
  purchase_date timestamptz not null,
  expires_date timestamptz,
  revocation_date timestamptz,
  signed_transaction_info text not null,
  verification_status text not null,
  created_at timestamptz not null default now()
);

create table if not exists commerce.app_store_notification_events (
  event_id uuid primary key default gen_random_uuid(),
  notification_uuid text not null unique,
  notification_type text not null,
  subtype text,
  signed_payload text not null,
  processed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists commerce.entitlement_usage_daily (
  user_id uuid not null references learner.users(user_id),
  feature text not null,
  usage_date date not null,
  used_count integer not null default 0,
  limit_count integer not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, feature, usage_date)
);

create table if not exists commerce.entitlement_audit_logs (
  audit_log_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  action text not null,
  feature text,
  allowed boolean,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'commerce_iap_products_tier_check'
  ) then
    alter table commerce.iap_products
      add constraint commerce_iap_products_tier_check
      check (tier in ('free', 'pro'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'commerce_iap_products_billing_period_check'
  ) then
    alter table commerce.iap_products
      add constraint commerce_iap_products_billing_period_check
      check (billing_period in ('monthly', 'yearly'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'commerce_user_entitlements_tier_check'
  ) then
    alter table commerce.user_entitlements
      add constraint commerce_user_entitlements_tier_check
      check (tier in ('free', 'pro'));
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'commerce_app_store_transactions_verification_status_check'
  ) then
    alter table commerce.app_store_transactions
      add constraint commerce_app_store_transactions_verification_status_check
      check (verification_status in (
        'verified_active',
        'verified_expired',
        'verified_revoked'
      ));
  end if;
end $$;

create index if not exists commerce_app_store_transactions_user_idx
  on commerce.app_store_transactions(user_id, created_at desc);

create index if not exists commerce_app_store_transactions_original_idx
  on commerce.app_store_transactions(original_transaction_id, created_at desc);

create index if not exists commerce_app_store_notification_events_created_idx
  on commerce.app_store_notification_events(created_at desc);

create index if not exists commerce_entitlement_audit_logs_user_idx
  on commerce.entitlement_audit_logs(user_id, created_at desc);

alter table learner.users owner to gongkao_owner;
alter table learner.apple_identities owner to gongkao_owner;
alter table learner.wechat_identities owner to gongkao_owner;
alter table learner.app_sessions owner to gongkao_owner;
alter table learner.profiles owner to gongkao_owner;
alter table learner.placement_attempts owner to gongkao_owner;
alter table learner.practice_answers owner to gongkao_owner;
alter table learner.external_question_intakes owner to gongkao_owner;
alter table learner.external_wrong_questions owner to gongkao_owner;
alter table learner.mock_exam_sessions owner to gongkao_owner;
alter table learner.dockets owner to gongkao_owner;
alter table learner.docket_cards owner to gongkao_owner;
alter table learner.verdicts owner to gongkao_owner;
alter table ai_audit.usage_daily owner to gongkao_owner;
alter table ai_audit.interaction_logs owner to gongkao_owner;
alter table product.method_cards owner to gongkao_owner;
alter table product.exam_tag_weights owner to gongkao_owner;
alter table product.knowledge_map_nodes owner to gongkao_owner;
alter table product.position_systems owner to gongkao_owner;
alter table product.positions owner to gongkao_owner;
alter table product.position_score_histories owner to gongkao_owner;
alter table product.position_score_links owner to gongkao_owner;
alter table product.position_entry_score_evidence owner to gongkao_owner;
alter table product.position_entry_score_links owner to gongkao_owner;
alter table product.system_score_histories owner to gongkao_owner;
alter table product.position_tags owner to gongkao_owner;
alter table learner.essay_attempts owner to gongkao_owner;
alter table commerce.iap_products owner to gongkao_owner;
alter table commerce.user_entitlements owner to gongkao_owner;
alter table commerce.app_store_transactions owner to gongkao_owner;
alter table commerce.app_store_notification_events owner to gongkao_owner;
alter table commerce.entitlement_usage_daily owner to gongkao_owner;
alter table commerce.entitlement_audit_logs owner to gongkao_owner;

grant usage on schema learner, ai_audit, product, commerce to gongkao_api;
grant select, insert, update on
  learner.users,
  learner.apple_identities,
  learner.wechat_identities,
  learner.app_sessions,
  learner.profiles,
  learner.placement_attempts,
  learner.practice_answers,
  learner.external_question_intakes,
  learner.external_wrong_questions,
  learner.mock_exam_sessions,
  learner.dockets,
  learner.docket_cards,
  learner.verdicts,
  ai_audit.usage_daily,
  ai_audit.interaction_logs,
  learner.essay_attempts,
  commerce.iap_products,
  commerce.user_entitlements,
  commerce.app_store_transactions,
  commerce.app_store_notification_events,
  commerce.entitlement_usage_daily,
  commerce.entitlement_audit_logs
to gongkao_api;

grant select on product.method_cards to gongkao_api;
grant select on product.exam_tag_weights to gongkao_api;
grant select on product.knowledge_map_nodes to gongkao_api;
grant select on
  product.position_systems,
  product.positions,
  product.position_score_histories,
  product.position_score_links,
  product.position_entry_score_evidence,
  product.position_entry_score_links,
  product.system_score_histories,
  product.position_tags
to gongkao_api;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'gongkao_importer') then
    grant select, insert, update, delete on
      product.exam_tag_weights,
      product.knowledge_map_nodes
    to gongkao_importer;

    grant select, insert, update on
      product.position_systems,
      product.positions,
      product.position_score_histories,
      product.position_score_links,
      product.position_entry_score_evidence,
      product.position_entry_score_links,
      product.system_score_histories,
      product.position_tags
    to gongkao_importer;
  end if;
end $$;
SQL

sudo -u postgres psql -d "$DB_NAME" -c "
select schemaname, tablename
from pg_tables
where schemaname in ('ai_audit', 'learner', 'product', 'commerce')
  and tablename in (
    'users',
    'apple_identities',
    'wechat_identities',
    'app_sessions',
    'profiles',
    'placement_attempts',
    'practice_answers',
    'external_question_intakes',
    'external_wrong_questions',
    'mock_exam_sessions',
    'dockets',
    'docket_cards',
    'verdicts',
    'usage_daily',
    'interaction_logs',
    'essay_attempts',
    'method_cards',
    'exam_tag_weights',
    'knowledge_map_nodes',
    'position_systems',
    'positions',
    'position_score_histories',
    'position_score_links',
    'position_entry_score_evidence',
    'position_entry_score_links',
    'system_score_histories',
    'position_tags',
    'iap_products',
    'user_entitlements',
    'app_store_transactions',
    'app_store_notification_events',
    'entitlement_usage_daily',
    'entitlement_audit_logs'
  )
order by schemaname, tablename;"
REMOTE
