create extension if not exists pgcrypto;

create schema product;
create schema learner;
create schema ai_audit;
create schema commerce;
create schema platform;
create schema learning;

create table platform.outbox_events (
  event_id uuid primary key,
  deduplication_key text not null unique,
  aggregate_type text not null,
  aggregate_id text not null,
  event_type text not null,
  payload jsonb not null,
  status text not null default 'pending' check (status in ('pending', 'publishing', 'published', 'failed')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  available_at timestamptz not null default now(),
  claimed_at timestamptz,
  claim_token uuid,
  published_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);

create index platform_outbox_dispatch_idx
  on platform.outbox_events(status, available_at, created_at);

create table platform.background_jobs (
  job_id uuid primary key,
  job_key text not null unique,
  job_type text not null,
  input_version text not null,
  payload jsonb not null,
  result jsonb,
  status text not null default 'queued' check (status in ('queued', 'running', 'waiting_confirmation', 'succeeded', 'failed', 'cancelled')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  max_attempts integer not null default 3 check (max_attempts > 0),
  run_after timestamptz not null default now(),
  claimed_at timestamptz,
  claim_token uuid,
  last_error_code text,
  last_error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  check (attempt_count <= max_attempts)
);

create index platform_background_jobs_claim_idx
  on platform.background_jobs(status, run_after, created_at);

create index platform_essay_review_job_review_version_idx
  on platform.background_jobs ((payload->>'reviewVersionId'), created_at)
  where job_type = 'essay_grounded_review_v2';

create index platform_essay_review_job_user_answer_idx
  on platform.background_jobs (
    (payload->>'userId'),
    ((payload->'request')->>'answerVersionId'),
    created_at
  )
  where job_type = 'essay_grounded_review_v2';

create table product.questions (
  question_id text primary key,
  domain text not null,
  status text not null,
  module_name text,
  question_type text,
  display_level1 text,
  display_level2 text,
  display_level3 text,
  knowledge_family text,
  teaching_point text,
  micro_skill text,
  stem text not null default '',
  stem_html text,
  answer text,
  material_text text,
  material_html text,
  difficulty text,
  shared_context_required boolean not null default false,
  visual_required boolean not null default false,
  needs_multimodal boolean not null default false,
  occurrence_count integer,
  occurrence_province_count integer,
  first_year integer,
  latest_year integer,
  source_member_count integer
);

create table product.question_explanations (
  question_id text primary key,
  preferred_analysis text,
  original_analysis text,
  stored_analysis text,
  analysis_source text,
  analysis_status text,
  prefer_ai_analysis boolean not null default false,
  analysis_needs_review boolean not null default false
);

create table product.knowledge_chunks (
  knowledge_chunk_id text primary key,
  question_id text,
  source_question_id text,
  source_dataset_id text,
  source_paper_row_id text,
  source_question_key text,
  source_paper_key text,
  chunk_kind text,
  chunk_index integer,
  material_order integer,
  title text,
  content text not null default '',
  content_sha256 text,
  display_path text,
  token_estimate integer,
  question_type text,
  province text,
  year integer,
  tags jsonb,
  metadata jsonb
);

create table product.question_options (
  question_id text not null,
  option_index integer not null,
  label text not null,
  option_text text not null,
  option_html text,
  primary key (question_id, option_index)
);

create table product.question_occurrences (
  occurrence_id text primary key,
  question_id text not null,
  source_question_id text,
  source_dataset_id text,
  province text,
  year integer,
  number integer,
  section text,
  paper_title text,
  occurrence_paper_title text,
  source_path text,
  canonical_occurrence_rank integer
);

create table product.question_tags (
  question_tag_id text primary key,
  question_id text not null,
  tag_type text not null,
  tag_value text not null,
  sort_order integer,
  evidence_link_count integer
);

create table product.question_assets (
  asset_id text primary key,
  question_id text not null,
  part_kind text,
  part_index integer,
  image_ordinal integer,
  image_src text,
  object_key text,
  is_representative_asset boolean not null default false
);

create table product.position_systems (
  system_code text primary key,
  name_zh text not null,
  paper_type text not null,
  is_big_six boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table product.positions (
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
  has_extra_exam boolean not null default false,
  remark text,
  raw_json jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (year, position_code, department_full)
);

create table product.position_requirements (
  requirement_id uuid primary key,
  position_id uuid not null references product.positions(position_id),
  rule_set_version text not null,
  position_data_version text not null,
  fact_key text not null check (fact_key in ('education', 'degree', 'major', 'fresh_graduate', 'political_status', 'grassroots_years', 'service_project', 'hukou', 'age', 'certifications', 'other_conditions')),
  operator text not null check (operator in ('equals', 'one_of', 'at_least', 'contains_any', 'manual_review')),
  expected jsonb,
  hard_condition boolean not null default true,
  source_text text not null,
  source_reference text not null,
  created_at timestamptz not null default now(),
  check ((operator = 'manual_review' and expected is null) or (operator <> 'manual_review' and expected is not null)),
  unique (position_id, rule_set_version, position_data_version, fact_key)
);

create index product_position_requirements_lookup_idx
  on product.position_requirements(position_id, rule_set_version, position_data_version);

create table product.position_rule_releases (
  release_id uuid primary key,
  exam_code text not null,
  exam_year integer not null check (exam_year between 2000 and 2100),
  rule_set_version text not null,
  position_data_version text not null,
  status text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  activated_at timestamptz,
  created_at timestamptz not null default now(),
  check ((status = 'active' and activated_at is not null) or status <> 'active'),
  unique (exam_code, exam_year, rule_set_version, position_data_version)
);

create unique index product_position_rule_releases_one_active_idx
  on product.position_rule_releases(exam_code, exam_year)
  where status = 'active';

create table product.position_rule_compilations (
  compilation_id uuid primary key,
  position_id uuid not null references product.positions(position_id),
  rule_set_version text not null,
  position_data_version text not null,
  compiler_version text not null,
  source_fingerprint text not null check (char_length(source_fingerprint) = 64),
  status text not null check (status in ('deterministic', 'needs_confirmation', 'no_conditions')),
  requirements_count integer not null,
  manual_review_count integer not null,
  warnings jsonb not null,
  compiled_at timestamptz not null,
  check (requirements_count >= 0 and manual_review_count >= 0 and manual_review_count <= requirements_count),
  unique (position_id, rule_set_version, position_data_version)
);

create index product_position_rule_compilations_release_idx
  on product.position_rule_compilations(rule_set_version, position_data_version, status);

create index product_positions_year_region_idx
  on product.positions(year, region, system_code);

create table product.position_score_histories (
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
  updated_at timestamptz not null default now(),
  unique (position_code, year)
);

create table product.position_score_links (
  position_id uuid not null references product.positions(position_id),
  score_history_id uuid not null references product.position_score_histories(score_history_id),
  match_method text not null,
  confidence text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_id, score_history_id)
);

create table product.position_entry_score_evidence (
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
  updated_at timestamptz not null default now(),
  unique (year, department_code, position_code)
);

create index product_position_entry_score_evidence_code_idx
  on product.position_entry_score_evidence(year, position_code, department_code);

create table product.position_entry_score_links (
  position_id uuid not null references product.positions(position_id),
  evidence_id uuid not null references product.position_entry_score_evidence(evidence_id),
  match_method text not null,
  confidence text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_id, evidence_id)
);

create index product_position_entry_score_links_evidence_idx
  on product.position_entry_score_links(evidence_id);

create table product.system_score_histories (
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
  updated_at timestamptz not null default now(),
  unique (system_code, region, year)
);

create table product.position_tags (
  position_source_id text not null,
  tag text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (position_source_id, tag)
);

create table product.method_cards (
  id text primary key,
  exam_scope text not null,
  track text not null check (track in ('xingce', 'shenlun')),
  module_name text not null,
  tag_path text not null,
  title text not null,
  recognition text not null,
  applicability jsonb not null,
  method_steps jsonb not null,
  error_signatures jsonb not null,
  fix_action text not null,
  time_budget_sec integer not null check (time_budget_sec > 0),
  accuracy_rule text not null,
  pitfall text,
  author_note text not null,
  validation jsonb not null,
  priority integer not null check (priority between 1 and 5),
  status text not null check (status in ('draft', 'published')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index product_method_cards_status_scope_idx
  on product.method_cards(status, exam_scope, track, module_name, tag_path);

create table product.exam_tag_weights (
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

create index product_exam_tag_weights_scope_rank_idx
  on product.exam_tag_weights(
    exam_scope,
    track,
    tag_type,
    weight desc,
    occurrence_count desc
  );

create table product.knowledge_map_nodes (
  node_id text primary key,
  exam_scope text not null,
  track text not null check (track in ('xingce', 'shenlun')),
  module_name text not null,
  node_type text not null check (
    node_type in (
      'module',
      'question_type',
      'knowledge_family',
      'knowledge_point',
      'micro_skill'
    )
  ),
  tag_type text,
  tag_value text not null,
  display_path text not null,
  parent_node_id text,
  aliases jsonb not null default '[]'::jsonb,
  support_status text not null check (
    support_status in ('text_supported', 'image_unsupported', 'draft', 'retired')
  ),
  near5_window_start_year integer,
  near5_window_end_year integer,
  near5_weight numeric(12,8),
  near5_occurrence_count integer,
  stable_for_old_questions boolean not null default false,
  method_card_ids jsonb not null default '[]'::jsonb,
  evidence jsonb not null default '{}'::jsonb,
  status text not null check (status in ('draft', 'published')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (exam_scope, track, module_name, node_type, tag_type, tag_value)
);

create index product_knowledge_map_nodes_rank_idx
  on product.knowledge_map_nodes(
    exam_scope,
    track,
    status,
    support_status,
    near5_weight desc nulls last,
    near5_occurrence_count desc nulls last
  );

create index product_knowledge_map_nodes_tag_idx
  on product.knowledge_map_nodes(exam_scope, track, module_name, tag_type, tag_value);

create table product.official_rank_config (
  rank_code text primary key,
  rank_level integer not null,
  rank_name text not null,
  xp_threshold integer not null,
  ui_theme text not null,
  ai_privilege text,
  unlock_features text[] not null default '{}'
);

insert into product.official_rank_config (
  rank_code,
  rank_level,
  rank_name,
  xp_threshold,
  ui_theme,
  ai_privilege,
  unlock_features
) values
  ('nine_pin_xianling', 9, '九品县令', 0, 'bamboo', '基础做题', '{}'),
  ('eight_pin_xiancheng', 8, '八品县丞', 500, 'bamboo', '错题本', '{"wrong_question_book"}'),
  ('seven_pin_zhixian', 7, '七品知县', 1500, 'rough_paper', '每日政务卡片', '{"government_cards"}'),
  ('six_pin_zhizhou', 6, '六品知州', 3000, 'rough_paper', 'AI幕僚(基础)', '{"ai_counselor_basic"}'),
  ('five_pin_zhifu', 5, '五品知府', 6000, 'ink', '雷达图+AI总结', '{"radar_chart","ai_summary"}'),
  ('four_pin_daoyuan', 4, '四品道员', 10000, 'ink', '申论Boss战', '{"essay_boss"}'),
  ('three_pin_buzhengshi', 3, '三品布政使', 16000, 'ink', '上帝视角(部分)', '{"god_view_partial"}'),
  ('two_pin_xunfu', 2, '二品巡抚', 25000, 'gold', 'AI无限解析', '{"ai_unlimited"}'),
  ('one_pin_shoufu', 1, '一品首辅', 40000, 'gold', '全部解锁', '{"all_unlocked"}');

create table learner.users (
  user_id uuid primary key,
  display_name text,
  email text,
  is_active boolean not null default true,
  account_status text not null default 'active' check (account_status in ('active', 'recovery_required', 'deletion_pending', 'disabled')),
  account_status_changed_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table learning.score_evidence (
  evidence_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  exam_code text not null,
  exam_year integer not null check (exam_year between 2000 and 2100),
  source_type text not null check (source_type in ('self_report', 'placement_test', 'controlled_mock', 'external_mock', 'official_exam')),
  reported_context text check (reported_context is null or reported_context in ('official_exam', 'full_mock', 'historical_estimate')),
  xingce_score numeric(5,2) check (xingce_score is null or xingce_score between 0 and 100),
  shenlun_score numeric(5,2) check (shenlun_score is null or shenlun_score between 0 and 100),
  total_score numeric(6,2),
  timed_status text not null check (timed_status in ('timed', 'untimed', 'unknown')),
  verification_status text not null check (verification_status in ('user_confirmed', 'system_verified')),
  occurred_at timestamptz not null,
  supersedes_evidence_id uuid,
  command_payload jsonb not null,
  created_at timestamptz not null default now(),
  check (xingce_score is not null or shenlun_score is not null),
  check (
    (source_type = 'self_report' and reported_context is not null)
    or (source_type <> 'self_report' and reported_context is null)
  ),
  check (
    (num_nonnulls(xingce_score, shenlun_score) = 1 and total_score is null)
    or (num_nonnulls(xingce_score, shenlun_score) = 2 and total_score is not null and total_score = xingce_score + shenlun_score)
  ),
  unique (evidence_id, user_id, exam_code, exam_year),
  foreign key (supersedes_evidence_id, user_id, exam_code, exam_year)
    references learning.score_evidence(evidence_id, user_id, exam_code, exam_year)
);

create index learning_score_evidence_user_exam_idx
  on learning.score_evidence(user_id, exam_code, exam_year, occurred_at);

create unique index learning_score_evidence_one_successor_idx
  on learning.score_evidence(supersedes_evidence_id)
  where supersedes_evidence_id is not null;

create table learning.ability_estimate_versions (
  estimate_version_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  exam_code text not null,
  exam_year integer not null check (exam_year between 2000 and 2100),
  status text not null check (status in ('partial', 'complete')),
  xingce_lower numeric(5,2),
  xingce_recommended numeric(5,2),
  xingce_upper numeric(5,2),
  shenlun_lower numeric(5,2),
  shenlun_recommended numeric(5,2),
  shenlun_upper numeric(5,2),
  total_lower numeric(6,2),
  total_recommended numeric(6,2),
  total_upper numeric(6,2),
  confidence text not null check (confidence in ('low', 'medium', 'high')),
  source_evidence_ids jsonb not null,
  evidence_summary jsonb not null,
  algorithm_version text not null,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_estimate_version_id uuid,
  generated_at timestamptz not null,
  valid_until timestamptz not null check (valid_until > generated_at),
  unique (user_id, exam_code, exam_year, input_fingerprint),
  unique (estimate_version_id, user_id, exam_code, exam_year),
  foreign key (supersedes_estimate_version_id, user_id, exam_code, exam_year)
    references learning.ability_estimate_versions(estimate_version_id, user_id, exam_code, exam_year),
  check (
    num_nonnulls(xingce_lower, xingce_recommended, xingce_upper) = 0
    or (num_nonnulls(xingce_lower, xingce_recommended, xingce_upper) = 3 and xingce_lower between 0 and 100 and xingce_lower <= xingce_recommended and xingce_recommended <= xingce_upper and xingce_upper <= 100)
  ),
  check (
    num_nonnulls(shenlun_lower, shenlun_recommended, shenlun_upper) = 0
    or (num_nonnulls(shenlun_lower, shenlun_recommended, shenlun_upper) = 3 and shenlun_lower between 0 and 100 and shenlun_lower <= shenlun_recommended and shenlun_recommended <= shenlun_upper and shenlun_upper <= 100)
  ),
  check (
    num_nonnulls(total_lower, total_recommended, total_upper) = 0
    or (num_nonnulls(total_lower, total_recommended, total_upper) = 3 and total_lower between 0 and 200 and total_lower <= total_recommended and total_recommended <= total_upper and total_upper <= 200)
  ),
  check (
    (status = 'complete' and xingce_recommended is not null and shenlun_recommended is not null and total_recommended is not null)
    or (status = 'partial' and total_recommended is null and ((xingce_recommended is null) <> (shenlun_recommended is null)))
  )
);

create index learning_ability_estimates_latest_idx
  on learning.ability_estimate_versions(user_id, exam_code, exam_year, generated_at);

create unique index learning_ability_estimates_one_successor_idx
  on learning.ability_estimate_versions(supersedes_estimate_version_id)
  where supersedes_estimate_version_id is not null;

create table product.essay_task_versions (
  task_version_id uuid primary key,
  task_key text not null check (char_length(task_key) > 0),
  version_no integer not null check (version_no >= 1),
  question_id text references product.questions(question_id),
  question_type text not null check (question_type in ('summary', 'comprehensive_analysis', 'countermeasure', 'implementation', 'composition')),
  prompt text not null check (char_length(prompt) > 0),
  prompt_sha256 text not null check (length(prompt_sha256) = 64),
  word_limit integer not null check (word_limit between 1 and 5000),
  maximum_score numeric(6,2) not null check (maximum_score > 0 and maximum_score <= 100),
  state text not null check (state in ('draft', 'published')),
  supersedes_task_version_id uuid,
  created_at timestamptz not null default now(),
  unique (task_key, version_no),
  unique (task_version_id, task_key),
  check (supersedes_task_version_id is null or supersedes_task_version_id <> task_version_id),
  foreign key (supersedes_task_version_id, task_key)
    references product.essay_task_versions(task_version_id, task_key)
);

create unique index product_essay_task_one_successor_idx
  on product.essay_task_versions(supersedes_task_version_id)
  where supersedes_task_version_id is not null;

create table product.essay_material_bundle_versions (
  material_bundle_version_id uuid primary key,
  task_version_id uuid not null,
  task_key text not null,
  version_no integer not null check (version_no >= 1),
  completeness text not null check (completeness in ('complete', 'incomplete')),
  source_kind text not null check (source_kind in ('own_bank', 'official', 'external_confirmed')),
  content_sha256 text not null check (length(content_sha256) = 64),
  supersedes_material_bundle_version_id uuid,
  created_at timestamptz not null default now(),
  unique (task_version_id, version_no),
  unique (material_bundle_version_id, task_version_id, task_key),
  unique (material_bundle_version_id, task_version_id),
  check (supersedes_material_bundle_version_id is null or supersedes_material_bundle_version_id <> material_bundle_version_id),
  foreign key (task_version_id, task_key)
    references product.essay_task_versions(task_version_id, task_key),
  foreign key (supersedes_material_bundle_version_id, task_version_id, task_key)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id, task_key)
);

create unique index product_essay_material_one_successor_idx
  on product.essay_material_bundle_versions(supersedes_material_bundle_version_id)
  where supersedes_material_bundle_version_id is not null;

create table product.essay_material_segments (
  material_segment_id uuid primary key,
  material_bundle_version_id uuid not null references product.essay_material_bundle_versions(material_bundle_version_id),
  material_id text not null check (char_length(material_id) > 0),
  segment_id text not null check (char_length(segment_id) > 0),
  material_order integer not null check (material_order >= 1),
  segment_order integer not null check (segment_order >= 1),
  text text not null check (char_length(text) > 0),
  text_sha256 text not null check (length(text_sha256) = 64),
  source_metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(source_metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (material_bundle_version_id, segment_id),
  unique (material_bundle_version_id, material_id, material_order, segment_order)
);

create table product.essay_rubric_versions (
  rubric_version_id uuid primary key,
  task_version_id uuid not null,
  task_key text not null,
  material_bundle_version_id uuid,
  compilation_input_fingerprint text,
  version_no integer not null check (version_no >= 1),
  source_kind text not null check (source_kind in ('official', 'system_compiled')),
  verification_status text not null check (verification_status in ('verified', 'unverified')),
  state text not null check (state in ('draft', 'published')),
  total_maximum numeric(6,2) not null check (total_maximum > 0 and total_maximum <= 100),
  criteria jsonb not null check (jsonb_typeof(criteria) = 'array' and jsonb_array_length(criteria) > 0),
  points jsonb not null check (jsonb_typeof(points) = 'array' and jsonb_array_length(points) > 0),
  content_sha256 text not null check (length(content_sha256) = 64),
  compilation_audit jsonb,
  supersedes_rubric_version_id uuid,
  created_at timestamptz not null default now(),
  unique (task_version_id, version_no),
  unique (rubric_version_id, task_version_id, task_key),
  unique (rubric_version_id, task_version_id),
  unique (rubric_version_id, task_version_id, material_bundle_version_id),
  check (source_kind <> 'system_compiled' or verification_status = 'unverified'),
  check (source_kind <> 'system_compiled' or (material_bundle_version_id is not null and compilation_input_fingerprint is not null and length(compilation_input_fingerprint) = 64)),
  check (source_kind <> 'system_compiled' or (compilation_audit is not null and jsonb_typeof(compilation_audit) = 'object' and compilation_audit ?& array['contractVersion','compilerVersion','promptVersion','provider','model','inputFingerprint','promptSha256','materialContentSha256'])),
  check (supersedes_rubric_version_id is null or supersedes_rubric_version_id <> rubric_version_id),
  foreign key (task_version_id, task_key)
    references product.essay_task_versions(task_version_id, task_key),
  foreign key (material_bundle_version_id, task_version_id)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id),
  foreign key (supersedes_rubric_version_id, task_version_id, task_key)
    references product.essay_rubric_versions(rubric_version_id, task_version_id, task_key)
);

create unique index product_essay_rubric_one_successor_idx
  on product.essay_rubric_versions(supersedes_rubric_version_id)
  where supersedes_rubric_version_id is not null;

create index product_essay_rubric_compilation_input_idx
  on product.essay_rubric_versions(task_version_id, compilation_input_fingerprint)
  where source_kind = 'system_compiled';

create table ai_audit.model_call_ledger (
  call_id uuid primary key,
  call_key text not null unique,
  user_id uuid references learner.users(user_id),
  job_id uuid references platform.background_jobs(job_id),
  feature text not null,
  operation text not null,
  provider text not null,
  model text not null,
  prompt_version text not null,
  schema_version text not null,
  price_version text not null check (char_length(price_version) between 1 and 120),
  input_rate_cny_per_million numeric(30,12) not null,
  cached_input_rate_cny_per_million numeric(30,12) not null,
  output_rate_cny_per_million numeric(30,12) not null,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  output_fingerprint text check (output_fingerprint is null or length(output_fingerprint) = 64),
  status text not null check (status in ('running', 'succeeded', 'failed')),
  input_tokens integer check (input_tokens is null or input_tokens >= 0),
  cached_input_tokens integer check (cached_input_tokens is null or cached_input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  input_cost_cny numeric(14,8),
  cached_input_cost_cny numeric(14,8),
  output_cost_cny numeric(14,8),
  total_cost_cny numeric(14,8),
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  error_code text,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  check ((status = 'running' and completed_at is null) or (status <> 'running' and completed_at is not null)),
  check (input_cost_cny is null or input_cost_cny >= 0),
  check (cached_input_cost_cny is null or cached_input_cost_cny >= 0),
  check (output_cost_cny is null or output_cost_cny >= 0),
  check (total_cost_cny is null or total_cost_cny >= 0),
  check (total_cost_cny is null or abs(total_cost_cny - coalesce(input_cost_cny, 0) - coalesce(cached_input_cost_cny, 0) - coalesce(output_cost_cny, 0)) < 0.00000001),
  check (status <> 'succeeded' or num_nonnulls(input_tokens, cached_input_tokens, output_tokens, input_cost_cny, cached_input_cost_cny, output_cost_cny, total_cost_cny, latency_ms, output_fingerprint) = 9),
  check (cached_input_tokens is null or input_tokens is null or cached_input_tokens <= input_tokens),
  check (input_rate_cny_per_million > 0 and cached_input_rate_cny_per_million >= 0 and output_rate_cny_per_million > 0),
  check (
    (status = 'running' and num_nonnulls(output_fingerprint, input_tokens, cached_input_tokens, output_tokens, input_cost_cny, cached_input_cost_cny, output_cost_cny, total_cost_cny, latency_ms, error_code, completed_at) = 0)
    or (status = 'succeeded' and error_code is null)
    or (status = 'failed' and coalesce(char_length(error_code), 0) > 0 and latency_ms is not null and output_fingerprint is null and num_nonnulls(input_tokens, cached_input_tokens, output_tokens, input_cost_cny, cached_input_cost_cny, output_cost_cny, total_cost_cny) = 0)
  )
);

create index ai_model_call_ledger_user_feature_idx
  on ai_audit.model_call_ledger(user_id, feature, started_at);

create index ai_model_call_ledger_job_idx
  on ai_audit.model_call_ledger(job_id, started_at);

create function ai_audit.reject_append_only_mutation()
returns trigger language plpgsql as $$
begin
  raise exception using
    errcode = '23514',
    message = tg_table_schema || '.' || tg_table_name || ' is append-only';
end;
$$;

create function ai_audit.enforce_model_call_ledger_transition()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'model call ledger history cannot be deleted';
  end if;
  if old.status <> 'running' then
    raise exception using
      errcode = '23514',
      message = 'terminal model call ledger rows are immutable';
  end if;
  if new.status not in ('succeeded', 'failed') then
    raise exception using
      errcode = '23514',
      message = 'model call ledger permits one terminal transition';
  end if;
  if row(
    new.call_id, new.call_key, new.user_id, new.job_id, new.feature,
    new.operation, new.provider, new.model, new.prompt_version,
    new.schema_version, new.price_version,
    new.input_rate_cny_per_million, new.cached_input_rate_cny_per_million,
    new.output_rate_cny_per_million, new.input_fingerprint,
    new.started_at
  ) is distinct from row(
    old.call_id, old.call_key, old.user_id, old.job_id, old.feature,
    old.operation, old.provider, old.model, old.prompt_version,
    old.schema_version, old.price_version,
    old.input_rate_cny_per_million, old.cached_input_rate_cny_per_million,
    old.output_rate_cny_per_million, old.input_fingerprint,
    old.started_at
  ) then
    raise exception using
      errcode = '23514',
      message = 'model call ledger identity is immutable';
  end if;
  return new;
end;
$$;

create trigger model_call_ledger_enforce_transition
before update or delete on ai_audit.model_call_ledger
for each row execute function ai_audit.enforce_model_call_ledger_transition();

create table ai_audit.essay_review_checkpoints (
  checkpoint_id uuid primary key,
  job_id uuid not null references platform.background_jobs(job_id) on delete cascade,
  phase text not null check (phase in ('initial', 'repair')),
  call_id uuid not null references ai_audit.model_call_ledger(call_id),
  contract_version text not null check (contract_version = 'essay-review-checkpoint-v1'),
  key_id text not null check (char_length(key_id) > 0),
  nonce bytea not null check (octet_length(nonce) = 12),
  ciphertext bytea not null check (octet_length(ciphertext) > 16),
  payload_sha256 text not null check (length(payload_sha256) = 64),
  created_at timestamptz not null,
  expires_at timestamptz not null check (expires_at > created_at),
  unique (job_id, phase)
);

create index ai_essay_review_checkpoint_expiry_idx
  on ai_audit.essay_review_checkpoints(expires_at);

create table learning.essay_review_execution_commands (
  command_id text primary key,
  user_id uuid not null references learner.users(user_id),
  command_kind text not null check (command_kind in ('initial', 'regenerate')),
  request_fingerprint text not null check (length(request_fingerprint) = 64),
  job_id uuid not null references platform.background_jobs(job_id),
  created_at timestamptz not null default now()
);

create index learning_essay_review_execution_command_user_idx
  on learning.essay_review_execution_commands(user_id, created_at);

create table learning.essay_attempts (
  attempt_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  task_version_id uuid not null,
  task_key text not null,
  material_bundle_version_id uuid not null,
  rubric_version_id uuid not null,
  source_context text not null check (source_context in ('in_program', 'paper', 'external')),
  timed_status text not null check (timed_status in ('timed', 'untimed', 'unknown')),
  elapsed_seconds integer check (elapsed_seconds is null or elapsed_seconds >= 1),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  submitted_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (user_id, input_fingerprint),
  unique (attempt_id, user_id, task_version_id),
  constraint essay_attempt_content_identity_key unique (
    attempt_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  ),
  check (timed_status <> 'timed' or elapsed_seconds is not null),
  foreign key (task_version_id, task_key)
    references product.essay_task_versions(task_version_id, task_key),
  foreign key (material_bundle_version_id, task_version_id, task_key)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id, task_key),
  foreign key (rubric_version_id, task_version_id, task_key)
    references product.essay_rubric_versions(rubric_version_id, task_version_id, task_key),
  foreign key (rubric_version_id, task_version_id, material_bundle_version_id)
    references product.essay_rubric_versions(rubric_version_id, task_version_id, material_bundle_version_id)
);

create index learning_essay_attempt_user_task_idx
  on learning.essay_attempts(user_id, task_version_id, submitted_at);

create table learning.essay_answer_versions (
  answer_version_id uuid primary key,
  command_id text not null unique,
  attempt_id uuid not null,
  user_id uuid not null,
  task_version_id uuid not null,
  version_no integer not null check (version_no >= 1),
  source_type text not null check (source_type in ('typed', 'ocr_confirmed')),
  answer_text text not null check (char_length(answer_text) > 0),
  answer_sha256 text not null check (length(answer_sha256) = 64),
  thinking_payload jsonb,
  thinking_sha256 text,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_answer_version_id uuid,
  created_at timestamptz not null default now(),
  unique (attempt_id, version_no),
  unique (attempt_id, input_fingerprint),
  unique (answer_version_id, attempt_id, user_id, task_version_id),
  check ((thinking_payload is null and thinking_sha256 is null) or (thinking_payload is not null and thinking_sha256 is not null and length(thinking_sha256) = 64)),
  check (thinking_payload is null or jsonb_typeof(thinking_payload) = 'object'),
  check (supersedes_answer_version_id is null or supersedes_answer_version_id <> answer_version_id),
  foreign key (attempt_id, user_id, task_version_id)
    references learning.essay_attempts(attempt_id, user_id, task_version_id),
  foreign key (supersedes_answer_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id)
);

create unique index learning_essay_answer_one_successor_idx
  on learning.essay_answer_versions(supersedes_answer_version_id)
  where supersedes_answer_version_id is not null;

create function learning.reject_essay_intelligence_mutation()
returns trigger language plpgsql as $$
begin
  raise exception using
    errcode = '23514',
    message = tg_table_schema || '.' || tg_table_name || ' is append-only';
end;
$$;

create table learning.essay_review_intent_versions (
  intent_version_id uuid primary key,
  intent_id uuid not null,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  attempt_id uuid not null,
  answer_version_id uuid not null,
  task_version_id uuid not null,
  material_bundle_version_id uuid not null,
  rubric_version_id uuid not null,
  review_scope text not null check (review_scope = 'grounded_full'),
  version_no integer not null check (version_no >= 1),
  status text not null check (status in ('pending', 'waiting_quota', 'queued')),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  intent_fingerprint text not null check (length(intent_fingerprint) = 64),
  state_fingerprint text not null check (length(state_fingerprint) = 64),
  job_id uuid references platform.background_jobs(job_id),
  quota_feature text,
  quota_usage_date date,
  quota_used integer,
  quota_limit integer,
  retry_after timestamptz,
  supersedes_intent_version_id uuid references learning.essay_review_intent_versions(intent_version_id),
  created_at timestamptz not null default now(),
  unique (intent_id, version_no),
  unique (user_id, answer_version_id, review_scope, version_no),
  check (
    (status = 'pending' and num_nonnulls(job_id, quota_feature, quota_usage_date, quota_used, quota_limit, retry_after) = 0)
    or (status = 'waiting_quota' and job_id is null and quota_feature = 'essay_review' and num_nonnulls(quota_usage_date, quota_used, quota_limit, retry_after) = 4 and quota_limit >= 0 and quota_used >= quota_limit)
    or (status = 'queued' and job_id is not null and quota_feature = 'essay_review' and retry_after is null and num_nonnulls(quota_usage_date, quota_used, quota_limit) = 3 and quota_limit >= 1 and quota_used between 1 and quota_limit)
  ),
  check (retry_after is null or retry_after > created_at),
  check (supersedes_intent_version_id is null or supersedes_intent_version_id <> intent_version_id),
  constraint essay_review_intent_attempt_content_fk foreign key (
    attempt_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  ) references learning.essay_attempts(
    attempt_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  ),
  foreign key (answer_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id),
  foreign key (material_bundle_version_id, task_version_id)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id),
  foreign key (rubric_version_id, task_version_id, material_bundle_version_id)
    references product.essay_rubric_versions(rubric_version_id, task_version_id, material_bundle_version_id)
);

create unique index learning_essay_review_intent_one_successor_idx
  on learning.essay_review_intent_versions(supersedes_intent_version_id)
  where supersedes_intent_version_id is not null;

create index learning_essay_review_intent_current_answer_idx
  on learning.essay_review_intent_versions(user_id, answer_version_id, review_scope, version_no);

create index learning_essay_review_intent_job_idx
  on learning.essay_review_intent_versions(job_id)
  where job_id is not null;

create function learning.enforce_essay_review_intent_insert()
returns trigger language plpgsql as $$
declare
  previous learning.essay_review_intent_versions%rowtype;
begin
  if new.version_no = 1 then
    if new.supersedes_intent_version_id is not null or new.status <> 'pending' then
      raise exception using errcode = '23514', message = 'initial review intent version must be pending';
    end if;
    return new;
  end if;
  if new.supersedes_intent_version_id is null then
    raise exception using errcode = '23514', message = 'review intent successor must reference its prior version';
  end if;
  select * into previous
    from learning.essay_review_intent_versions
   where intent_version_id = new.supersedes_intent_version_id;
  if not found then
    raise exception using errcode = '23503', message = 'review intent prior version does not exist';
  end if;
  if row(
    new.intent_id, new.user_id, new.attempt_id, new.answer_version_id,
    new.task_version_id, new.material_bundle_version_id, new.rubric_version_id,
    new.review_scope, new.input_fingerprint, new.intent_fingerprint
  ) is distinct from row(
    previous.intent_id, previous.user_id, previous.attempt_id,
    previous.answer_version_id, previous.task_version_id,
    previous.material_bundle_version_id, previous.rubric_version_id,
    previous.review_scope, previous.input_fingerprint,
    previous.intent_fingerprint
  ) then
    raise exception using errcode = '23514', message = 'review intent identity is immutable across versions';
  end if;
  if new.version_no <> previous.version_no + 1 then
    raise exception using errcode = '23514', message = 'review intent version must increase by one';
  end if;
  if new.status not in ('waiting_quota', 'queued') then
    raise exception using errcode = '23514', message = 'review intent successor has an invalid state transition';
  end if;
  return new;
end;
$$;

create trigger essay_review_intent_validate_insert
before insert on learning.essay_review_intent_versions
for each row execute function learning.enforce_essay_review_intent_insert();

create trigger essay_review_intent_versions_append_only
before update or delete on learning.essay_review_intent_versions
for each row execute function learning.reject_essay_intelligence_mutation();

create table learning.essay_review_versions (
  review_version_id uuid primary key,
  command_id text not null unique,
  attempt_id uuid not null,
  user_id uuid not null,
  task_version_id uuid not null,
  answer_version_id uuid not null,
  material_bundle_version_id uuid not null,
  rubric_version_id uuid not null,
  status text not null check (status in ('completed', 'blocked', 'failed')),
  review_scope text not null check (review_scope = 'grounded_full'),
  thinking_evidence_available boolean not null,
  readiness jsonb not null check (jsonb_typeof(readiness) = 'object'),
  score_lower numeric(6,2),
  score_recommended numeric(6,2),
  score_upper numeric(6,2),
  score_maximum numeric(6,2),
  score_confidence text check (score_confidence is null or score_confidence in ('low', 'medium')),
  score_confidence_reasons jsonb,
  grounding_contract_version text,
  grounding_report jsonb,
  dimensions jsonb not null,
  point_coverage jsonb not null,
  claim_evidence jsonb not null,
  findings jsonb not null,
  rewrite_plan jsonb,
  contract_version text not null,
  provider_schema_version text not null,
  rubric_contract_version text not null,
  prompt_version text not null,
  provider text,
  model text,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  execution_fingerprint text not null check (length(execution_fingerprint) = 64),
  execution_attempt_no integer not null check (execution_attempt_no >= 1),
  output_fingerprint text check (output_fingerprint is null or length(output_fingerprint) = 64),
  supersedes_review_version_id uuid,
  generated_at timestamptz not null,
  valid_until timestamptz not null check (valid_until > generated_at),
  unique (attempt_id, execution_fingerprint),
  unique (attempt_id, input_fingerprint, execution_attempt_no),
  unique (review_version_id, attempt_id, user_id, task_version_id),
  check ((status = 'completed' and num_nonnulls(score_lower, score_recommended, score_upper, score_maximum, score_confidence, provider, model, output_fingerprint, rewrite_plan) = 9) or (status <> 'completed' and num_nonnulls(score_lower, score_recommended, score_upper, score_maximum, score_confidence, output_fingerprint, rewrite_plan) = 0)),
  check ((status = 'completed' and score_confidence_reasons is not null and jsonb_typeof(score_confidence_reasons) = 'array' and jsonb_array_length(score_confidence_reasons) > 0) or (status <> 'completed' and score_confidence_reasons is null)),
  check ((status = 'completed' and grounding_contract_version is not null and grounding_contract_version = 'essay-grounding-v2' and grounding_report is not null and jsonb_typeof(grounding_report) = 'object' and grounding_report @> '{"verified": true, "eligibleForLearning": true}'::jsonb) or (status <> 'completed' and grounding_contract_version is null and grounding_report is null)),
  check (score_lower is null or (score_lower >= 0 and score_lower <= score_recommended and score_recommended <= score_upper and score_upper <= score_maximum and score_maximum <= 100)),
  check (jsonb_typeof(dimensions) = 'array' and jsonb_typeof(point_coverage) = 'array' and jsonb_typeof(claim_evidence) = 'array' and jsonb_typeof(findings) = 'array'),
  check (rewrite_plan is null or jsonb_typeof(rewrite_plan) = 'object'),
  check ((status = 'completed' and jsonb_array_length(dimensions) > 0 and jsonb_array_length(point_coverage) > 0 and jsonb_array_length(findings) > 0) or (status <> 'completed' and jsonb_array_length(dimensions) = 0 and jsonb_array_length(point_coverage) = 0 and jsonb_array_length(claim_evidence) = 0 and jsonb_array_length(findings) = 0)),
  check (supersedes_review_version_id is null or supersedes_review_version_id <> review_version_id),
  foreign key (attempt_id, user_id, task_version_id)
    references learning.essay_attempts(attempt_id, user_id, task_version_id),
  foreign key (answer_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id),
  foreign key (supersedes_review_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_review_versions(review_version_id, attempt_id, user_id, task_version_id),
  foreign key (material_bundle_version_id, task_version_id)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id),
  foreign key (rubric_version_id, task_version_id)
    references product.essay_rubric_versions(rubric_version_id, task_version_id),
  foreign key (rubric_version_id, task_version_id, material_bundle_version_id)
    references product.essay_rubric_versions(rubric_version_id, task_version_id, material_bundle_version_id)
);

create unique index learning_essay_review_one_successor_idx
  on learning.essay_review_versions(supersedes_review_version_id)
  where supersedes_review_version_id is not null;

create index learning_essay_review_user_task_idx
  on learning.essay_review_versions(user_id, task_version_id, generated_at);

create table ai_audit.essay_review_model_calls (
  review_version_id uuid not null references learning.essay_review_versions(review_version_id),
  call_id uuid not null references ai_audit.model_call_ledger(call_id),
  phase text not null check (phase in ('initial', 'repair')),
  ordinal smallint not null check (ordinal between 1 and 2),
  created_at timestamptz not null default now(),
  primary key (review_version_id, call_id),
  unique (review_version_id, ordinal)
);

create trigger essay_review_model_calls_append_only
before update or delete on ai_audit.essay_review_model_calls
for each row execute function ai_audit.reject_append_only_mutation();

create table learning.essay_rewrite_versions (
  rewrite_version_id uuid primary key,
  command_id text not null unique,
  attempt_id uuid not null,
  user_id uuid not null,
  task_version_id uuid not null,
  base_review_version_id uuid not null,
  base_answer_version_id uuid not null,
  rewritten_answer_version_id uuid not null,
  analysis_payload jsonb not null check (jsonb_typeof(analysis_payload) = 'object'),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  output_fingerprint text not null check (length(output_fingerprint) = 64),
  supersedes_rewrite_version_id uuid,
  created_at timestamptz not null default now(),
  unique (attempt_id, input_fingerprint),
  unique (rewrite_version_id, attempt_id, user_id, task_version_id),
  check (base_answer_version_id <> rewritten_answer_version_id),
  check (supersedes_rewrite_version_id is null or supersedes_rewrite_version_id <> rewrite_version_id),
  foreign key (attempt_id, user_id, task_version_id)
    references learning.essay_attempts(attempt_id, user_id, task_version_id),
  foreign key (base_review_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_review_versions(review_version_id, attempt_id, user_id, task_version_id),
  foreign key (base_answer_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id),
  foreign key (rewritten_answer_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id),
  foreign key (supersedes_rewrite_version_id, attempt_id, user_id, task_version_id)
    references learning.essay_rewrite_versions(rewrite_version_id, attempt_id, user_id, task_version_id)
);

create unique index learning_essay_rewrite_one_successor_idx
  on learning.essay_rewrite_versions(supersedes_rewrite_version_id)
  where supersedes_rewrite_version_id is not null;

create table learning.essay_transfer_verification_versions (
  verification_version_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  source_review_version_id uuid not null,
  source_attempt_id uuid not null,
  source_task_version_id uuid not null,
  transfer_attempt_id uuid not null,
  transfer_task_version_id uuid not null,
  transfer_answer_version_id uuid not null,
  fresh_material_bundle_version_id uuid not null,
  status text not null check (status in ('verified', 'not_verified', 'inconclusive')),
  tested_criterion_codes jsonb not null check (jsonb_typeof(tested_criterion_codes) = 'array' and jsonb_array_length(tested_criterion_codes) > 0),
  evidence_payload jsonb not null check (jsonb_typeof(evidence_payload) = 'object'),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  output_fingerprint text not null check (length(output_fingerprint) = 64),
  supersedes_verification_version_id uuid,
  generated_at timestamptz not null,
  valid_until timestamptz not null check (valid_until > generated_at),
  unique (user_id, input_fingerprint),
  unique (verification_version_id, user_id, source_review_version_id, transfer_attempt_id),
  check (source_attempt_id <> transfer_attempt_id and source_task_version_id <> transfer_task_version_id),
  check (supersedes_verification_version_id is null or supersedes_verification_version_id <> verification_version_id),
  foreign key (source_review_version_id, source_attempt_id, user_id, source_task_version_id)
    references learning.essay_review_versions(review_version_id, attempt_id, user_id, task_version_id),
  foreign key (transfer_attempt_id, user_id, transfer_task_version_id)
    references learning.essay_attempts(attempt_id, user_id, task_version_id),
  foreign key (transfer_answer_version_id, transfer_attempt_id, user_id, transfer_task_version_id)
    references learning.essay_answer_versions(answer_version_id, attempt_id, user_id, task_version_id),
  foreign key (fresh_material_bundle_version_id, transfer_task_version_id)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id),
  foreign key (supersedes_verification_version_id, user_id, source_review_version_id, transfer_attempt_id)
    references learning.essay_transfer_verification_versions(verification_version_id, user_id, source_review_version_id, transfer_attempt_id)
);

create unique index learning_essay_transfer_one_successor_idx
  on learning.essay_transfer_verification_versions(supersedes_verification_version_id)
  where supersedes_verification_version_id is not null;

create table learning.weekly_study_preference_versions (
  preference_version_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  version_no integer not null check (version_no >= 1),
  timezone text not null check (char_length(timezone) between 1 and 80),
  weekday_minutes integer not null check (weekday_minutes between 0 and 1440),
  weekend_minutes integer not null check (weekend_minutes between 0 and 1440),
  long_block_minutes integer not null check (long_block_minutes between 0 and 1440),
  fragment_minutes integer not null check (fragment_minutes between 0 and 1440),
  regular_rest_weekday smallint not null check (regular_rest_weekday between 1 and 7),
  regular_mock_weekday smallint not null check (regular_mock_weekday between 1 and 7),
  effective_from date not null,
  source_type text not null check (source_type in ('system_default', 'user_confirmed', 'user_revised')),
  confirmed boolean not null,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_preference_version_id uuid,
  created_at timestamptz not null default now(),
  unique (user_id, version_no),
  unique (preference_version_id, user_id),
  check (regular_rest_weekday <> regular_mock_weekday),
  check (
    (source_type = 'system_default' and confirmed = false)
    or (source_type in ('user_confirmed', 'user_revised') and confirmed = true)
  ),
  check (
    supersedes_preference_version_id is null
    or supersedes_preference_version_id <> preference_version_id
  ),
  foreign key (supersedes_preference_version_id, user_id)
    references learning.weekly_study_preference_versions(preference_version_id, user_id)
);

create index learning_weekly_preference_current_idx
  on learning.weekly_study_preference_versions(user_id, version_no);

create index learning_weekly_preference_input_idx
  on learning.weekly_study_preference_versions(user_id, input_fingerprint, version_no);

create unique index learning_weekly_preference_one_successor_idx
  on learning.weekly_study_preference_versions(supersedes_preference_version_id)
  where supersedes_preference_version_id is not null;

create table learning.daily_capacity_versions (
  capacity_version_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  local_date date not null,
  version_no integer not null check (version_no >= 1),
  available_minutes integer not null check (available_minutes between 0 and 1440),
  energy_level smallint not null check (energy_level between 1 and 5),
  stress_level smallint not null check (stress_level between 1 and 5),
  confidence_level smallint not null check (confidence_level between 1 and 5),
  confirmed boolean not null,
  source_type text not null check (source_type in ('weekly_preference', 'user_override')),
  weekly_preference_version_id uuid,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_capacity_version_id uuid,
  recorded_at timestamptz not null,
  unique (user_id, local_date, version_no),
  unique (capacity_version_id, user_id, local_date),
  check (
    (source_type = 'weekly_preference' and weekly_preference_version_id is not null and confirmed = false)
    or (source_type = 'user_override' and weekly_preference_version_id is not null and confirmed = true)
  ),
  check (
    supersedes_capacity_version_id is null
    or supersedes_capacity_version_id <> capacity_version_id
  ),
  foreign key (weekly_preference_version_id, user_id)
    references learning.weekly_study_preference_versions(preference_version_id, user_id),
  foreign key (supersedes_capacity_version_id, user_id, local_date)
    references learning.daily_capacity_versions(capacity_version_id, user_id, local_date)
);

create index learning_daily_capacity_current_idx
  on learning.daily_capacity_versions(user_id, local_date, version_no);

create index learning_daily_capacity_input_idx
  on learning.daily_capacity_versions(user_id, local_date, input_fingerprint, version_no);

create unique index learning_daily_capacity_one_successor_idx
  on learning.daily_capacity_versions(supersedes_capacity_version_id)
  where supersedes_capacity_version_id is not null;

create table learning.daily_plan_versions (
  plan_version_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  local_date date not null,
  version_no integer not null check (version_no >= 1),
  capacity_version_id uuid not null,
  weekly_preference_version_id uuid not null,
  day_mode text not null check (day_mode in ('normal', 'mock', 'recovery', 'unavailable')),
  headline text not null check (char_length(headline) > 0),
  rationale text not null check (char_length(rationale) > 0),
  goal_gap jsonb not null check (jsonb_typeof(goal_gap) = 'object'),
  scheduled_minutes integer not null check (scheduled_minutes between 0 and 1440),
  due_reflection_count integer check (due_reflection_count >= 0),
  due_verification_count integer check (due_verification_count >= 0),
  track_explanation jsonb not null check (jsonb_typeof(track_explanation) = 'object'),
  input_versions jsonb not null check (jsonb_typeof(input_versions) = 'object'),
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0),
  invalidation_conditions jsonb not null check (jsonb_typeof(invalidation_conditions) = 'array' and jsonb_array_length(invalidation_conditions) > 0),
  replan_reason_codes jsonb not null check (jsonb_typeof(replan_reason_codes) = 'array'),
  plan_basis text not null check (plan_basis = 'evidence_priority_v2'),
  contract_version text not null check (char_length(contract_version) > 0),
  algorithm_version text not null check (char_length(algorithm_version) > 0),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_plan_version_id uuid,
  generated_at timestamptz not null,
  valid_until timestamptz not null check (valid_until > generated_at),
  unique (user_id, local_date, version_no),
  unique (plan_version_id, user_id, local_date),
  unique (plan_version_id, user_id, local_date, version_no),
  check (
    supersedes_plan_version_id is null
    or supersedes_plan_version_id <> plan_version_id
  ),
  foreign key (capacity_version_id, user_id, local_date)
    references learning.daily_capacity_versions(capacity_version_id, user_id, local_date),
  foreign key (weekly_preference_version_id, user_id)
    references learning.weekly_study_preference_versions(preference_version_id, user_id),
  foreign key (supersedes_plan_version_id, user_id, local_date)
    references learning.daily_plan_versions(plan_version_id, user_id, local_date)
);

create index learning_daily_plan_current_idx
  on learning.daily_plan_versions(user_id, local_date, version_no);

create index learning_daily_plan_input_idx
  on learning.daily_plan_versions(user_id, local_date, input_fingerprint, version_no);

create unique index learning_daily_plan_one_successor_idx
  on learning.daily_plan_versions(supersedes_plan_version_id)
  where supersedes_plan_version_id is not null;

create table learning.daily_plan_tasks (
  task_id uuid primary key,
  plan_version_id uuid not null,
  user_id uuid not null,
  local_date date not null,
  plan_version_no integer not null check (plan_version_no >= 1),
  candidate_key text not null check (char_length(candidate_key) between 1 and 240),
  work_item_key text not null check (length(work_item_key) = 64),
  task_order integer not null check (task_order >= 1),
  track text not null check (track in ('xingce', 'essay', 'recovery', 'mock')),
  stage text not null check (
    stage in (
      'objective_baseline', 'objective_practice', 'objective_reflection',
      'objective_verification', 'essay_material_reading',
      'essay_point_extraction', 'essay_outlining', 'essay_drafting',
      'essay_review', 'essay_rewrite', 'essay_transfer', 'recovery_review',
      'weekly_mock'
    )
  ),
  kind text not null check (kind in ('practice', 'reflection', 'verification', 'mock_exam', 'essay')),
  title text not null check (char_length(title) > 0),
  reason text not null check (char_length(reason) > 0),
  estimated_minutes integer not null check (estimated_minutes between 1 and 1440),
  mandatory boolean not null,
  start_url text not null check (left(start_url, 1) = '/'),
  priority_score numeric(6,5) not null,
  goal_gain numeric(6,5) not null,
  ability_gain numeric(6,5) not null,
  verification_urgency numeric(6,5) not null,
  time_fit numeric(6,5) not null,
  load_penalty numeric(6,5) not null,
  track_balance_bonus numeric(6,5) not null,
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0),
  completion_contract jsonb not null check (jsonb_typeof(completion_contract) = 'object'),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  created_at timestamptz not null default now(),
  unique (plan_version_id, task_order),
  unique (plan_version_id, input_fingerprint),
  unique (plan_version_id, candidate_key),
  unique (plan_version_id, work_item_key),
  unique (task_id, user_id, local_date, plan_version_no),
  unique (
    task_id, plan_version_id, user_id, local_date, plan_version_no,
    candidate_key, work_item_key
  ),
  check (
    (track = 'xingce' and stage in ('objective_baseline', 'objective_practice', 'objective_reflection', 'objective_verification'))
    or (track = 'essay' and stage in ('essay_material_reading', 'essay_point_extraction', 'essay_outlining', 'essay_drafting', 'essay_review', 'essay_rewrite', 'essay_transfer'))
    or (track = 'recovery' and stage = 'recovery_review')
    or (track = 'mock' and stage = 'weekly_mock')
  ),
  check (
    (track = 'xingce' and kind in ('practice', 'reflection', 'verification'))
    or (track = 'essay' and kind = 'essay')
    or (track = 'recovery' and kind in ('reflection', 'verification'))
    or (track = 'mock' and kind = 'mock_exam')
  ),
  check (
    priority_score between 0 and 1 and goal_gain between 0 and 1
    and ability_gain between 0 and 1 and verification_urgency between 0 and 1
    and time_fit between 0 and 1 and load_penalty between 0 and 1
    and track_balance_bonus between 0 and 1
  ),
  foreign key (plan_version_id, user_id, local_date, plan_version_no)
    references learning.daily_plan_versions(plan_version_id, user_id, local_date, version_no)
);

create index learning_daily_plan_task_track_idx
  on learning.daily_plan_tasks(user_id, local_date, track, stage);

create table learning.task_execution_versions (
  execution_version_id uuid primary key,
  command_id text not null check (char_length(command_id) between 1 and 240),
  user_id uuid not null,
  local_date date not null,
  task_id uuid not null,
  plan_version_id uuid not null,
  plan_version_no integer not null check (plan_version_no >= 1),
  candidate_key text not null check (char_length(candidate_key) between 1 and 240),
  work_item_key text not null check (length(work_item_key) = 64),
  version_no integer not null check (version_no >= 1),
  status text not null check (status in ('in_progress', 'complete', 'abandoned')),
  elapsed_minutes integer not null check (elapsed_minutes between 0 and 1440),
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
  abandonment_reason text,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_execution_version_id uuid,
  supersedes_execution_version_no integer,
  recorded_at timestamptz not null,
  unique (user_id, command_id),
  unique (user_id, local_date, work_item_key, version_no),
  unique (execution_version_id, user_id, work_item_key, local_date, version_no),
  check (supersedes_execution_version_id is null or supersedes_execution_version_id <> execution_version_id),
  check (
    (version_no = 1 and supersedes_execution_version_id is null and supersedes_execution_version_no is null)
    or (version_no > 1 and supersedes_execution_version_id is not null and supersedes_execution_version_no = version_no - 1)
  ),
  check (
    (status = 'complete' and jsonb_array_length(evidence_refs) > 0 and abandonment_reason is null)
    or (status = 'abandoned' and char_length(trim(abandonment_reason)) > 0)
    or (status = 'in_progress' and abandonment_reason is null)
  ),
  foreign key (
    task_id, plan_version_id, user_id, local_date, plan_version_no,
    candidate_key, work_item_key
  ) references learning.daily_plan_tasks(
    task_id, plan_version_id, user_id, local_date, plan_version_no,
    candidate_key, work_item_key
  ),
  foreign key (
    supersedes_execution_version_id, user_id, work_item_key, local_date,
    supersedes_execution_version_no
  ) references learning.task_execution_versions(
    execution_version_id, user_id, work_item_key, local_date, version_no
  )
);

create index learning_task_execution_current_idx
  on learning.task_execution_versions(user_id, local_date, work_item_key, version_no);

create index learning_task_execution_terminal_candidate_idx
  on learning.task_execution_versions(user_id, local_date, work_item_key, status);

create unique index learning_task_execution_one_successor_idx
  on learning.task_execution_versions(supersedes_execution_version_id)
  where supersedes_execution_version_id is not null;

alter table learning.essay_answer_versions
  add constraint essay_answer_artifact_owner_task_key
  unique (answer_version_id, user_id, task_version_id);

alter table learning.essay_review_versions
  add constraint essay_review_artifact_binding_key
  unique (
    review_version_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  );

create table learning.essay_learning_artifact_versions (
  artifact_version_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  artifact_type text not null check (
    artifact_type in (
      'material_annotation', 'point_extraction', 'outline',
      'review_acknowledgement', 'reflection'
    )
  ),
  scope_key text not null check (length(scope_key) = 64),
  task_version_id uuid not null references product.essay_task_versions(task_version_id),
  material_bundle_version_id uuid not null,
  rubric_version_id uuid,
  review_version_id uuid,
  answer_version_id uuid,
  version_no integer not null check (version_no >= 1),
  payload jsonb not null check (
    jsonb_typeof(payload) = 'object' and payload->>'schemaType' = artifact_type
  ),
  payload_sha256 text not null check (length(payload_sha256) = 64),
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  supersedes_artifact_version_id uuid,
  created_at timestamptz not null default now(),
  unique (user_id, scope_key, version_no),
  unique (artifact_version_id, user_id, scope_key),
  check (
    (artifact_type = 'material_annotation' and rubric_version_id is null
      and review_version_id is null and answer_version_id is null)
    or (artifact_type in ('point_extraction', 'outline')
      and rubric_version_id is not null and review_version_id is null
      and answer_version_id is null)
    or (artifact_type = 'review_acknowledgement'
      and rubric_version_id is not null and review_version_id is not null
      and answer_version_id is null)
    or (artifact_type = 'reflection' and rubric_version_id is not null
      and answer_version_id is not null)
  ),
  check (
    supersedes_artifact_version_id is null
    or supersedes_artifact_version_id <> artifact_version_id
  ),
  foreign key (material_bundle_version_id, task_version_id)
    references product.essay_material_bundle_versions(material_bundle_version_id, task_version_id),
  foreign key (rubric_version_id, task_version_id)
    references product.essay_rubric_versions(rubric_version_id, task_version_id),
  foreign key (
    review_version_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  ) references learning.essay_review_versions(
    review_version_id, user_id, task_version_id,
    material_bundle_version_id, rubric_version_id
  ),
  foreign key (answer_version_id, user_id, task_version_id)
    references learning.essay_answer_versions(answer_version_id, user_id, task_version_id)
);

create unique index learning_essay_artifact_one_successor_idx
  on learning.essay_learning_artifact_versions(supersedes_artifact_version_id)
  where supersedes_artifact_version_id is not null;

create index learning_essay_artifact_current_task_idx
  on learning.essay_learning_artifact_versions(
    user_id, task_version_id, artifact_type, created_at
  );

create table commerce.iap_products (
  product_row_id uuid primary key default gen_random_uuid(),
  platform text not null default 'ios',
  product_id text not null unique,
  tier text not null check (tier in ('free', 'pro')),
  billing_period text not null check (billing_period in ('monthly', 'yearly')),
  title text not null,
  subtitle text not null,
  price_band text not null,
  sort_order integer not null,
  feature_highlights jsonb not null default '[]'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table commerce.user_entitlements (
  entitlement_id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references learner.users(user_id),
  tier text not null default 'free' check (tier in ('free', 'pro')),
  source text not null default 'free',
  subscription_start timestamptz,
  subscription_end timestamptz,
  product_ids jsonb not null default '[]'::jsonb,
  features jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table commerce.app_store_transactions (
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
  verification_status text not null check (
    verification_status in ('verified_active', 'verified_expired', 'verified_revoked')
  ),
  created_at timestamptz not null default now()
);

create table commerce.app_store_notification_events (
  event_id uuid primary key default gen_random_uuid(),
  notification_uuid text not null unique,
  notification_type text not null,
  subtype text,
  signed_payload text not null,
  processed_at timestamptz,
  created_at timestamptz not null default now()
);

create table commerce.entitlement_usage_daily (
  user_id uuid not null references learner.users(user_id),
  feature text not null,
  usage_date date not null,
  used_count integer not null default 0,
  limit_count integer not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, feature, usage_date)
);

create table commerce.feature_usage_reservations (
  reservation_id uuid primary key,
  reservation_key text not null unique
    check (char_length(reservation_key) between 1 and 300),
  user_id uuid not null references learner.users(user_id),
  feature text not null check (char_length(feature) between 1 and 120),
  usage_date date not null,
  limit_count integer not null check (limit_count > 0),
  usage_count_at_reservation integer not null
    check (usage_count_at_reservation between 1 and limit_count),
  status text not null check (status in ('reserved', 'consumed', 'refunded')),
  business_key text not null check (char_length(business_key) between 1 and 300),
  job_id uuid unique references platform.background_jobs(job_id),
  reserved_at timestamptz not null default now(),
  consumed_at timestamptz,
  refunded_at timestamptz,
  updated_at timestamptz not null default now(),
  unique (user_id, feature, business_key),
  check (
    (status = 'reserved' and consumed_at is null and refunded_at is null)
    or (status = 'consumed' and consumed_at is not null and refunded_at is null)
    or (status = 'refunded' and consumed_at is null and refunded_at is not null)
  )
);

create index commerce_feature_usage_reservations_usage_idx
  on commerce.feature_usage_reservations(user_id, feature, usage_date, status);

create function commerce.enforce_feature_usage_reservation_update()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation history cannot be deleted';
  end if;
  if old.status <> 'reserved' then
    raise exception using
      errcode = '23514',
      message = 'terminal feature usage reservation is immutable';
  end if;
  if row(
    new.reservation_id, new.reservation_key, new.user_id, new.feature,
    new.usage_date, new.limit_count, new.usage_count_at_reservation,
    new.business_key, new.reserved_at
  ) is distinct from row(
    old.reservation_id, old.reservation_key, old.user_id, old.feature,
    old.usage_date, old.limit_count, old.usage_count_at_reservation,
    old.business_key, old.reserved_at
  ) then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation identity is immutable';
  end if;
  if old.job_id is not null and new.job_id is distinct from old.job_id then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation job binding is immutable';
  end if;
  return new;
end;
$$;

create trigger feature_usage_reservation_enforce_update
before update or delete on commerce.feature_usage_reservations
for each row execute function commerce.enforce_feature_usage_reservation_update();

create table platform.companion_paid_commands (
  command_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  feature text not null
    check (feature in ('companion_ask', 'external_wrong_intake')),
  idempotency_key text not null
    check (char_length(idempotency_key) between 8 and 160),
  request_fingerprint text not null
    check (request_fingerprint ~ '^[0-9a-f]{64}$'),
  request_contract_version text not null,
  execution_payload jsonb not null
    check (jsonb_typeof(execution_payload) = 'object'),
  status text not null
    check (status in ('processing', 'provider_uncertain', 'succeeded', 'failed')),
  response_status_code integer,
  response_payload jsonb,
  response_fingerprint text,
  response_contract_version text not null,
  reservation_key text not null unique
    check (char_length(reservation_key) between 1 and 300),
  provider_idempotency_key text,
  delivery_count integer not null check (delivery_count >= 1),
  provider_attempt_count integer not null check (provider_attempt_count >= 0),
  provider_request_id_header text,
  provider_request_id_fingerprint text,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (user_id, feature, idempotency_key),
  check (
    char_length(request_contract_version) between 1 and 120
    and char_length(response_contract_version) between 1 and 120
  ),
  check (
    (feature = 'companion_ask' and provider_idempotency_key is not null
      and char_length(provider_idempotency_key) between 8 and 300)
    or (feature = 'external_wrong_intake' and provider_idempotency_key is null)
  ),
  check (feature <> 'external_wrong_intake' or provider_attempt_count = 0),
  check (
    status <> 'provider_uncertain'
    or (feature = 'companion_ask' and last_error_code is not null
      and char_length(last_error_code) between 1 and 160)
  ),
  check (
    (provider_request_id_header is null
      and provider_request_id_fingerprint is null)
    or (provider_request_id_header is not null
      and provider_request_id_fingerprint is not null
      and char_length(provider_request_id_header) between 1 and 120
      and provider_request_id_fingerprint ~ '^[0-9a-f]{64}$')
  ),
  check (
    (status in ('processing', 'provider_uncertain')
      and response_status_code is null
      and response_payload is null
      and response_fingerprint is null
      and completed_at is null)
    or (status = 'succeeded'
      and response_status_code is not null
      and response_status_code = 200
      and response_payload is not null
      and jsonb_typeof(response_payload) = 'object'
      and response_fingerprint is not null
      and response_fingerprint ~ '^[0-9a-f]{64}$'
      and last_error_code is null
      and completed_at is not null)
    or (status = 'failed'
      and response_status_code is not null
      and response_status_code between 400 and 599
      and response_payload is not null
      and jsonb_typeof(response_payload) = 'object'
      and response_fingerprint is not null
      and response_fingerprint ~ '^[0-9a-f]{64}$'
      and last_error_code is not null
      and char_length(last_error_code) between 1 and 160
      and completed_at is not null)
  )
);

create index platform_companion_paid_commands_user_created_idx
  on platform.companion_paid_commands(user_id, feature, created_at);

create index platform_companion_paid_commands_open_idx
  on platform.companion_paid_commands(status, updated_at)
  where status in ('processing', 'provider_uncertain');

create unique index platform_companion_paid_commands_provider_key_key
  on platform.companion_paid_commands(provider_idempotency_key)
  where provider_idempotency_key is not null;

create function platform.enforce_companion_paid_command_transition()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'companion paid command history cannot be deleted';
  end if;
  if old.status in ('succeeded', 'failed') then
    raise exception using
      errcode = '23514',
      message = 'terminal companion paid command is immutable';
  end if;
  if row(
    new.command_id, new.user_id, new.feature, new.idempotency_key,
    new.request_fingerprint, new.request_contract_version,
    new.execution_payload, new.response_contract_version,
    new.reservation_key, new.provider_idempotency_key, new.created_at
  ) is distinct from row(
    old.command_id, old.user_id, old.feature, old.idempotency_key,
    old.request_fingerprint, old.request_contract_version,
    old.execution_payload, old.response_contract_version,
    old.reservation_key, old.provider_idempotency_key, old.created_at
  ) then
    raise exception using
      errcode = '23514',
      message = 'companion paid command input is immutable';
  end if;
  if new.delivery_count < old.delivery_count
     or new.provider_attempt_count < old.provider_attempt_count then
    raise exception using
      errcode = '23514',
      message = 'companion paid command counters cannot decrease';
  end if;
  if not (
    (old.status = 'processing'
      and new.status in ('processing', 'provider_uncertain', 'succeeded', 'failed'))
    or (old.status = 'provider_uncertain'
      and new.status in ('provider_uncertain', 'processing'))
  ) then
    raise exception using
      errcode = '23514',
      message = 'invalid companion paid command state transition';
  end if;
  return new;
end;
$$;

create trigger companion_paid_command_enforce_transition
before update or delete on platform.companion_paid_commands
for each row execute function platform.enforce_companion_paid_command_transition();

create table commerce.entitlement_audit_logs (
  audit_log_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  action text not null,
  feature text,
  allowed boolean,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table learning.xingce_commands (
      user_id uuid not null references learner.users(user_id),
      command_id text not null,
      command_type text not null check (command_type in (
        'session_start', 'checkpoint_save', 'attempt_submit',
        'reflection_submit', 'cause_decision'
      )),
      target_id uuid,
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null,
      primary key (user_id, command_id),
      check (char_length(command_id) between 8 and 260)
    );

create index learning_xingce_commands_user_created_idx
      on learning.xingce_commands(user_id, created_at);

create table learning.xingce_task_sessions (
      session_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      source_plan_task_id uuid not null references learning.daily_plan_tasks(task_id),
      source_plan_version_id uuid not null references learning.daily_plan_versions(plan_version_id),
      local_date date not null,
      work_item_key text not null check (length(work_item_key) = 64),
      mode text not null check (mode in ('baseline', 'targeted')),
      state text not null check (
        state in ('active', 'submitted', 'settled')
      ),
      attempt_group_id uuid unique,
      revision bigint not null check (revision >= 1),
      bundle_version_id text not null check (length(bundle_version_id) = 64),
      safe_bundle jsonb not null check (
        jsonb_typeof(safe_bundle) = 'object'
        and not (safe_bundle::text ~* 'correct(answer|option)|explanation')
      ),
      scoring_bundle jsonb not null check (jsonb_typeof(scoring_bundle) = 'object'),
      current_question_id text not null,
      checkpoint jsonb not null check (
        jsonb_typeof(checkpoint) = 'object'
        and jsonb_typeof(checkpoint->'answers') = 'array'
      ),
      total_elapsed_seconds integer not null check (total_elapsed_seconds between 0 and 86400),
      created_at timestamptz not null,
      updated_at timestamptz not null,
      submitted_at timestamptz,
      completed_at timestamptz,
      unique (session_id, user_id),
      unique (user_id, work_item_key),
      check (submitted_at is null or state <> 'active'),
      check ((state = 'active') = (attempt_group_id is null)),
      check ((state = 'settled') = (completed_at is not null))
    );

create index learning_xingce_sessions_owner_state_idx
      on learning.xingce_task_sessions(user_id, state, updated_at);

create table learning.xingce_answer_versions (
      answer_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      version_no bigint not null check (version_no >= 1),
      payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and payload->>'questionId' = question_id
      ),
      payload_sha256 text not null check (length(payload_sha256) = 64),
      supersedes_answer_version_id uuid,
      created_at timestamptz not null,
      unique (answer_version_id, user_id, session_id, question_id),
      unique (session_id, question_id, version_no),
      check (
        (version_no = 1 and supersedes_answer_version_id is null)
        or (version_no > 1 and supersedes_answer_version_id is not null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_answer_version_id, user_id, session_id, question_id
      ) references learning.xingce_answer_versions(
        answer_version_id, user_id, session_id, question_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create unique index learning_xingce_answer_one_successor_idx
      on learning.xingce_answer_versions(supersedes_answer_version_id)
      where supersedes_answer_version_id is not null;

create table learning.xingce_attempt_records (
      attempt_record_id uuid primary key,
      attempt_group_id uuid not null,
      item_attempt_id text not null check (length(item_attempt_id) = 64),
      submit_command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      session_revision bigint not null check (session_revision >= 2),
      answer_version_id uuid not null,
      content_version_id text not null check (length(content_version_id) = 64),
      scoring_policy_version text not null,
      timing_evidence_ref text not null check (length(timing_evidence_ref) = 64),
      timing_evidence jsonb not null check (jsonb_typeof(timing_evidence) = 'object'),
      submission_hash text not null check (length(submission_hash) = 64),
      source_lineage_refs jsonb not null check (
        jsonb_typeof(source_lineage_refs) = 'array'
        and jsonb_array_length(source_lineage_refs) >= 3
      ),
      created_at timestamptz not null,
      unique (user_id, item_attempt_id),
      unique (session_id, question_id),
      unique (attempt_record_id, user_id, session_id, question_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (answer_version_id, user_id, session_id, question_id)
        references learning.xingce_answer_versions(
          answer_version_id, user_id, session_id, question_id
        ),
      foreign key (user_id, submit_command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create index learning_xingce_attempt_records_owner_created_idx
      on learning.xingce_attempt_records(user_id, created_at);

create table learning.xingce_assessments (
      assessment_id uuid primary key,
      attempt_record_id uuid not null,
      submit_command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      answer_version_id uuid not null,
      content_version_id text not null check (length(content_version_id) = 64),
      scoring_policy_version text not null,
      assessment_key_hash text not null unique check (length(assessment_key_hash) = 64),
      result_code text not null check (
        result_code in ('correct', 'incorrect', 'unanswered', 'timeout', 'guessed_correct')
      ),
      correct boolean not null,
      result_snapshot jsonb not null check (jsonb_typeof(result_snapshot) = 'object'),
      created_at timestamptz not null,
      unique (session_id, question_id),
      check (correct = (result_code in ('correct', 'guessed_correct'))),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (answer_version_id, user_id, session_id, question_id)
        references learning.xingce_answer_versions(
          answer_version_id, user_id, session_id, question_id
        ),
      foreign key (attempt_record_id, user_id, session_id, question_id)
        references learning.xingce_attempt_records(
          attempt_record_id, user_id, session_id, question_id
        ),
      foreign key (user_id, submit_command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create table learning.xingce_attempt_review_versions (
      review_version_id uuid primary key,
      command_id text not null,
      review_id uuid not null,
      user_id uuid not null,
      session_id uuid not null,
      revision bigint not null check (revision >= 1),
      state text not null check (
        state in (
          'reflection_due', 'cause_candidate_pending',
          'cause_decision_due', 'immediate_verification_due'
        )
      ),
      requires_cause_decision boolean not null,
      reflection_version_id uuid,
      cause_candidate_version_id uuid,
      cause_decision_version_id uuid,
      supersedes_review_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, revision),
      unique (review_version_id, review_id, user_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_review_version_id, review_id, user_id, session_id
      ) references learning.xingce_attempt_review_versions(
        review_version_id, review_id, user_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred,
      check (
        (revision = 1 and supersedes_review_version_id is null)
        or (revision > 1 and supersedes_review_version_id is not null)
      ),
      check (
        (state = 'reflection_due'
          and reflection_version_id is null
          and cause_candidate_version_id is null
          and cause_decision_version_id is null)
        or (state = 'cause_candidate_pending'
          and reflection_version_id is not null
          and cause_candidate_version_id is null
          and cause_decision_version_id is null)
        or (state = 'cause_decision_due'
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is null)
        or (state = 'immediate_verification_due'
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is not null)
      )
    );

create unique index learning_xingce_review_one_successor_idx
      on learning.xingce_attempt_review_versions(supersedes_review_version_id)
      where supersedes_review_version_id is not null;

create table learning.xingce_reflection_versions (
      reflection_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and length(trim(payload->>'rawText')) > 0
        and jsonb_typeof(payload->'tags') = 'array'
        and jsonb_array_length(payload->'tags') > 0
      ),
      payload_sha256 text not null check (length(payload_sha256) = 64),
      supersedes_reflection_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (user_id, command_id),
      unique (reflection_version_id, user_id, review_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_reflection_version_id, user_id, review_id, session_id
      ) references learning.xingce_reflection_versions(
        reflection_version_id, user_id, review_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create table learning.xingce_cause_decision_versions (
      decision_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      decision text not null check (decision in ('replace', 'skip')),
      replacement_cause text,
      supersedes_decision_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (user_id, command_id),
      unique (decision_version_id, user_id, review_id, session_id),
      check (
        (decision = 'replace' and length(trim(replacement_cause)) > 0)
        or (decision = 'skip' and replacement_cause is null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_decision_version_id, user_id, review_id, session_id
      ) references learning.xingce_cause_decision_versions(
        decision_version_id, user_id, review_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create table learning.xingce_cause_candidate_versions (
      candidate_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      state text not null check (state = 'unavailable'),
      reason_code text not null check (reason_code = 'scenario_not_activated'),
      charged boolean not null check (charged = false),
      input_version_refs jsonb not null check (
        jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) > 0
      ),
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (candidate_version_id, user_id, review_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_reflection_fk
      foreign key (reflection_version_id, user_id, review_id, session_id)
      references learning.xingce_reflection_versions(
        reflection_version_id, user_id, review_id, session_id
      );

alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_decision_fk
      foreign key (cause_decision_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_decision_versions(
        decision_version_id, user_id, review_id, session_id
      );

alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_candidate_fk
      foreign key (cause_candidate_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_candidate_versions(
        candidate_version_id, user_id, review_id, session_id
      );

create table learning.xingce_plan_evidence_versions (
      evidence_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      work_item_key text not null check (length(work_item_key) = 64),
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in ('pending_reflection', 'settled')),
      assessment_ids jsonb not null check (
        jsonb_typeof(assessment_ids) = 'array' and jsonb_array_length(assessment_ids) > 0
      ),
      execution_version_id uuid references learning.task_execution_versions(execution_version_id),
      supersedes_evidence_version_id uuid,
      created_at timestamptz not null,
      unique (session_id, version_no),
      unique (user_id, command_id),
      unique (evidence_version_id, user_id, session_id, work_item_key),
      check (
        (state = 'pending_reflection' and execution_version_id is null)
        or (state = 'settled' and execution_version_id is not null)
      ),
      check (
        (version_no = 1 and supersedes_evidence_version_id is null)
        or (version_no > 1 and supersedes_evidence_version_id is not null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_evidence_version_id, user_id, session_id, work_item_key
      ) references learning.xingce_plan_evidence_versions(
        evidence_version_id, user_id, session_id, work_item_key
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    );

create unique index learning_xingce_evidence_one_successor_idx
      on learning.xingce_plan_evidence_versions(supersedes_evidence_version_id)
      where supersedes_evidence_version_id is not null;

create table learning.plan_task_late_evidence_versions (
      late_evidence_id uuid primary key,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      session_id uuid not null,
      work_item_key text not null check (length(work_item_key) = 64),
      source_execution_version_id uuid not null
        references learning.task_execution_versions(execution_version_id),
      reason_code text not null,
      input_version_refs jsonb not null check (
        jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) > 0
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      created_at timestamptz not null,
      unique (user_id, command_id),
      unique (session_id, source_execution_version_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id)
    );

create function learning.reject_xingce_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'xingce learning facts are append-only';
    end;
    $$;

create trigger xingce_commands_append_only before update or delete on learning.xingce_commands for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_answer_versions_append_only before update or delete on learning.xingce_answer_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_attempt_records_append_only before update or delete on learning.xingce_attempt_records for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_assessments_append_only before update or delete on learning.xingce_assessments for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_attempt_review_versions_append_only before update or delete on learning.xingce_attempt_review_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_reflection_versions_append_only before update or delete on learning.xingce_reflection_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_cause_candidate_versions_append_only before update or delete on learning.xingce_cause_candidate_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_cause_decision_versions_append_only before update or delete on learning.xingce_cause_decision_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger xingce_plan_evidence_versions_append_only before update or delete on learning.xingce_plan_evidence_versions for each row execute function learning.reject_xingce_fact_mutation();

create trigger plan_task_late_evidence_versions_append_only before update or delete on learning.plan_task_late_evidence_versions for each row execute function learning.reject_xingce_fact_mutation();

create table learner.apple_identities (
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

create table learner.wechat_identities (
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

create index learner_wechat_identities_user_idx
  on learner.wechat_identities(user_id);

create index learner_wechat_identities_union_idx
  on learner.wechat_identities(union_id)
  where union_id is not null;

create table learner.app_sessions (
  session_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  session_token_hash text not null unique,
  device_id text not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  revoked_reason text check (revoked_reason is null or revoked_reason in ('user_logout', 'user_logout_all', 'user_revoked_device', 'account_recovery', 'security', 'expired_cleanup')),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create table learner.account_events (
  event_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  event_type text not null check (event_type in ('account_recovered', 'session_revoked', 'all_sessions_revoked', 'account_recovery_required', 'account_deletion_requested', 'account_disabled')),
  metadata jsonb not null,
  created_at timestamptz not null default now()
);

create index learner_account_events_user_idx
  on learner.account_events(user_id, created_at);

create table learner.official_ranks (
  user_id uuid primary key references learner.users(user_id),
  rank_code text not null default 'nine_pin_xianling',
  rank_level integer not null default 9,
  xp integer not null default 0,
  fame integer not null default 50,
  stamina integer not null default 100,
  stamina_updated_at timestamptz not null default now(),
  consecutive_days integer not null default 0,
  last_active_date date,
  demerits integer not null default 0,
  promoted_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table learner.rank_events (
  event_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  event_type text not null,
  old_rank text,
  new_rank text,
  xp_delta integer,
  fame_delta integer,
  reason text,
  created_at timestamptz not null default now()
);

create table learner.profiles (
  user_id uuid primary key references learner.users(user_id),
  target_exam text,
  target_region text,
  position_group text,
  daily_minutes integer check (daily_minutes is null or daily_minutes between 30 and 720),
  education text,
  major text,
  fresh_graduate boolean,
  political_status text,
  grassroots_years integer,
  study_mode text check (study_mode in ('fulltime', 'parttime')),
  target_position_id uuid,
  target_score numeric(6,2),
  baseline_source text check (baseline_source in ('self_report', 'placement_test', 'none')),
  baseline_xingce_score numeric(5,2),
  baseline_shenlun_score numeric(5,2),
  baseline_weak_modules jsonb,
  baseline_module_snapshot jsonb,
  onboarding_complete boolean not null default false,
  profile_version bigint not null default 1 check (profile_version >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table learner.onboarding_progress (
  user_id uuid primary key references learner.users(user_id),
  progress_version bigint not null check (progress_version >= 1),
  qualification_ready boolean not null,
  qualification_profile_version bigint,
  study_preference_ready boolean not null,
  study_preference_version bigint,
  result_ready boolean not null,
  result_acknowledged boolean not null,
  completed boolean not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  check (
    qualification_ready = (qualification_profile_version is not null)
    and (qualification_profile_version is null or qualification_profile_version >= 1)
  ),
  check (
    study_preference_ready = (study_preference_version is not null)
    and (study_preference_version is null or study_preference_version >= 1)
  ),
  check (result_ready = (qualification_ready and study_preference_ready)),
  check (result_acknowledged = completed),
  check (not completed or (result_ready and completed_at is not null)),
  check (completed or completed_at is null),
  foreign key (user_id, study_preference_version)
    references learning.weekly_study_preference_versions(user_id, version_no)
);

create table learner.onboarding_commands (
  command_id text primary key check (char_length(command_id) between 8 and 260),
  user_id uuid not null references learner.users(user_id),
  command_type text not null check (
    command_type in (
      'qualification_completed', 'qualification_invalidated',
      'study_preference_recorded', 'result_acknowledged'
    )
  ),
  request_payload jsonb not null check (jsonb_typeof(request_payload) = 'object'),
  response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
  created_at timestamptz not null default now()
);

create index learner_onboarding_commands_user_created_idx
  on learner.onboarding_commands(user_id, created_at);

create table learner.onboarding_progress_events (
  event_id uuid primary key,
  command_id text not null unique references learner.onboarding_commands(command_id),
  user_id uuid not null references learner.users(user_id),
  event_type text not null check (
    event_type in (
      'qualification_completed', 'qualification_invalidated',
      'study_preference_recorded', 'result_acknowledged'
    )
  ),
  from_progress_version bigint not null,
  to_progress_version bigint not null,
  state_snapshot jsonb not null check (jsonb_typeof(state_snapshot) = 'object'),
  created_at timestamptz not null default now(),
  unique (user_id, to_progress_version),
  check (
    from_progress_version >= 1
    and to_progress_version = from_progress_version + 1
  )
);

create function learner.reject_onboarding_history_mutation()
returns trigger language plpgsql as $$
begin
  raise exception using
    errcode = '23514',
    message = 'onboarding history is append-only';
end;
$$;

create trigger onboarding_commands_append_only
before update or delete on learner.onboarding_commands
for each row execute function learner.reject_onboarding_history_mutation();

create trigger onboarding_progress_events_append_only
before update or delete on learner.onboarding_progress_events
for each row execute function learner.reject_onboarding_history_mutation();

create function learner.enforce_onboarding_progress_transition()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'onboarding progress cannot be deleted';
  end if;
  if row(new.user_id, new.created_at) is distinct from
     row(old.user_id, old.created_at) then
    raise exception using
      errcode = '23514',
      message = 'onboarding progress identity is immutable';
  end if;
  if old.completed and row(
       new.progress_version, new.qualification_ready,
       new.qualification_profile_version, new.study_preference_ready,
       new.study_preference_version, new.result_ready,
       new.result_acknowledged, new.completed, new.completed_at
     ) is distinct from row(
       old.progress_version, old.qualification_ready,
       old.qualification_profile_version, old.study_preference_ready,
       old.study_preference_version, old.result_ready,
       old.result_acknowledged, old.completed, old.completed_at
     ) then
    raise exception using
      errcode = '23514',
      message = 'completed onboarding progress is immutable';
  end if;
  if new.progress_version <> old.progress_version + 1 then
    raise exception using
      errcode = '23514',
      message = 'onboarding progress version must advance exactly once';
  end if;
  if old.study_preference_ready and not new.study_preference_ready then
    raise exception using
      errcode = '23514',
      message = 'study preference readiness cannot be revoked';
  end if;
  return new;
end;
$$;

create trigger onboarding_progress_enforce_transition
before update or delete on learner.onboarding_progress
for each row execute function learner.enforce_onboarding_progress_transition();

create function learner.enforce_onboarding_profile_projection()
returns trigger language plpgsql as $$
declare
  owner_completed boolean;
begin
  select progress.completed
    into owner_completed
    from learner.onboarding_progress progress
   where progress.user_id = new.user_id;
  if new.onboarding_complete is distinct from coalesce(owner_completed, false) then
    raise exception using
      errcode = '23514',
      message = 'profiles.onboarding_complete is an onboarding progress projection';
  end if;
  return new;
end;
$$;

create trigger onboarding_profile_enforce_projection
before update of onboarding_complete on learner.profiles
for each row execute function learner.enforce_onboarding_profile_projection();

create table learner.qualification_profile_changes (
  change_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  from_profile_version bigint not null,
  to_profile_version bigint not null,
  command_payload jsonb not null,
  created_at timestamptz not null default now(),
  check (from_profile_version >= 1 and to_profile_version = from_profile_version + 1)
);

create table learner.qualification_facts (
  fact_id uuid primary key,
  change_id uuid not null references learner.qualification_profile_changes(change_id),
  user_id uuid not null references learner.users(user_id),
  profile_version bigint not null check (profile_version >= 1),
  fact_key text not null check (fact_key in ('education', 'degree', 'major', 'fresh_graduate', 'political_status', 'grassroots_years', 'service_project', 'hukou', 'age', 'certifications', 'other_conditions')),
  value jsonb not null,
  verification_status text not null check (verification_status in ('reported', 'confirmed', 'conflicting')),
  effective_at timestamptz not null,
  supersedes_fact_id uuid references learner.qualification_facts(fact_id),
  recorded_at timestamptz not null default now(),
  unique (user_id, fact_key, profile_version)
);

create index learner_qualification_facts_current_idx
  on learner.qualification_facts(user_id, fact_key, profile_version);

create table learner.qualification_evidence (
  evidence_id uuid primary key,
  fact_id uuid not null references learner.qualification_facts(fact_id) on delete cascade,
  source_type text not null check (source_type in ('learner_report', 'document', 'official_import', 'operator')),
  source_reference text,
  recorded_at timestamptz not null
);

create index learner_qualification_evidence_fact_idx
  on learner.qualification_evidence(fact_id);

create table learner.qualification_requirement_confirmations (
  confirmation_id uuid primary key,
  change_id uuid not null references learner.qualification_profile_changes(change_id),
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  position_id uuid not null references product.positions(position_id),
  requirement_id uuid not null references product.position_requirements(requirement_id),
  profile_version bigint not null check (profile_version >= 1),
  response text not null check (response in ('satisfied', 'not_satisfied', 'uncertain')),
  statement text check (statement is null or char_length(statement) <= 500),
  supersedes_confirmation_id uuid references learner.qualification_requirement_confirmations(confirmation_id),
  confirmed_at timestamptz not null,
  unique (user_id, requirement_id, profile_version)
);

create index learner_requirement_confirmations_current_idx
  on learner.qualification_requirement_confirmations(user_id, position_id, requirement_id, profile_version);

create table learner.eligibility_snapshots (
  snapshot_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  position_id uuid not null references product.positions(position_id),
  learner_profile_version bigint not null check (learner_profile_version >= 1),
  rule_set_version text not null,
  position_data_version text not null,
  status text not null check (status in ('eligible', 'needs_confirmation', 'ineligible')),
  checks jsonb not null,
  missing_fact_keys jsonb not null,
  evaluated_at timestamptz not null,
  unique (user_id, position_id, learner_profile_version, rule_set_version, position_data_version)
);

create index learner_eligibility_snapshots_history_idx
  on learner.eligibility_snapshots(user_id, position_id, evaluated_at);

create table learner.position_goal_versions (
  goal_version_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  position_id uuid not null references product.positions(position_id),
  role text not null check (role in ('primary', 'balanced', 'backup')),
  status text not null check (status in ('candidate', 'confirmed', 'retired')),
  target_score_lower numeric(6,2) not null,
  target_score_recommended numeric(6,2) not null,
  target_score_upper numeric(6,2) not null,
  target_score_evidence_level text not null check (target_score_evidence_level in ('direct', 'strong', 'limited', 'unavailable')),
  target_score_sample_count integer not null,
  target_score_valid_until timestamptz,
  learner_profile_version bigint not null check (learner_profile_version >= 1),
  eligibility_snapshot_id uuid not null references learner.eligibility_snapshots(snapshot_id),
  position_data_version text not null,
  supersedes_goal_version_id uuid references learner.position_goal_versions(goal_version_id),
  created_at timestamptz not null default now(),
  check (target_score_lower between 0 and 300 and target_score_lower <= target_score_recommended and target_score_recommended <= target_score_upper and target_score_upper <= 300),
  check ((target_score_evidence_level = 'unavailable' and target_score_sample_count = 0) or (target_score_evidence_level <> 'unavailable' and target_score_sample_count > 0))
);

create index learner_position_goal_versions_history_idx
  on learner.position_goal_versions(user_id, created_at);

create unique index learner_position_goal_versions_one_primary_idx
  on learner.position_goal_versions(user_id)
  where role = 'primary' and status = 'confirmed';

create table learner.position_goal_events (
  event_id uuid primary key,
  command_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  event_type text not null check (event_type in ('goal_selected', 'goal_changed', 'goal_retired')),
  from_goal_version_id uuid references learner.position_goal_versions(goal_version_id),
  to_goal_version_id uuid not null references learner.position_goal_versions(goal_version_id),
  change_reason text not null,
  command_payload jsonb not null,
  plan_impact jsonb not null,
  created_at timestamptz not null default now()
);

create index learner_position_goal_events_user_idx
  on learner.position_goal_events(user_id, created_at);

create table learner.position_goal_previews (
  preview_id uuid primary key,
  request_id text not null unique,
  user_id uuid not null references learner.users(user_id),
  position_id uuid not null references product.positions(position_id),
  eligibility_snapshot_id uuid not null references learner.eligibility_snapshots(snapshot_id),
  from_goal_version_id uuid references learner.position_goal_versions(goal_version_id),
  learner_profile_version bigint not null check (learner_profile_version >= 1),
  position_data_version text not null,
  target_score jsonb not null,
  plan_impact jsonb not null,
  request_payload jsonb not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  used_by_command_id text unique,
  created_at timestamptz not null default now(),
  check (expires_at > created_at),
  check (
    (used_at is null and used_by_command_id is null)
    or (used_at is not null and used_by_command_id is not null)
  )
);

create index learner_position_goal_previews_active_idx
  on learner.position_goal_previews(user_id, expires_at)
  where used_at is null;

create table learner.placement_attempts (
  attempt_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  task_id text not null,
  question_ids jsonb not null,
  answers jsonb not null,
  module_snapshot jsonb not null,
  estimated_xingce_score numeric(5,2) not null,
  created_at timestamptz not null default now()
);

create table learner.practice_answers (
  answer_id uuid primary key,
  user_id uuid not null references learner.users(user_id),
  task_id text not null,
  question_id text not null,
  selected_answer text not null,
  correct_answer text,
  correct boolean not null,
  answered_at timestamptz not null default now()
);

create table learner.external_question_intakes (
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

create table learner.external_wrong_questions (
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

create table learner.mock_exam_sessions (
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

create table learner.dockets (
  docket_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references learner.users(user_id),
  docket_date date not null,
  status text not null check (status in ('active', 'completed', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, docket_date, status)
);

create table learner.docket_cards (
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

create table learner.verdicts (
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

create table learner.essay_attempts (
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

create table ai_audit.usage_daily (
  learner_key text not null,
  feature text not null,
  usage_date date not null,
  used_count integer not null default 0,
  limit_count integer not null,
  updated_at timestamptz not null default now(),
  primary key (learner_key, feature, usage_date)
);

create table ai_audit.interaction_logs (
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

-- Stage 3.5 community method-card and reward tables are intentionally applied
-- by migration 20260716_28 in the test bootstrap. Keeping them migration-owned
-- exercises the exact additive DDL and append-only triggers used in production.
