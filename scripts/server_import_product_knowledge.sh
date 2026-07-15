#!/usr/bin/env bash
set -euo pipefail

HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"
RUN_KEY="${GONGKAO_PRODUCT_IMPORT_RUN_KEY:-product_knowledge_v1_20260703}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "DB_NAME='$DB_NAME' RUN_KEY='$RUN_KEY' bash -s" <<'REMOTE'
set -euo pipefail

echo "== verify canonical source =="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQL'
do $$
declare
  canonical_count bigint;
begin
  select count(*) into canonical_count from canonical_qbank.canonical_question;
  if canonical_count <> 55214 then
    raise exception 'canonical_qbank.canonical_question count mismatch: %', canonical_count;
  end if;
end $$;
SQL

echo "== rebuild product knowledge tables =="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQL'
drop table if exists product.question_assets cascade;
drop table if exists product.knowledge_chunks cascade;
drop table if exists product.question_tags cascade;
drop table if exists product.question_occurrences cascade;
drop table if exists product.question_explanations cascade;
drop table if exists product.question_options cascade;
drop table if exists product.questions cascade;

create table product.questions as
select
  cq.canonical_question_id as question_id,
  cq.question_domain as domain,
  cq.canonical_status as status,
  cq.merge_policy,
  cq.merge_evidence_source,
  cq.representative_source_question_id,
  cq.module_name,
  cq.question_type,
  cq.display_level1,
  cq.display_level2,
  cq.display_level3,
  cq.knowledge_family,
  cq.teaching_point,
  cq.micro_skill,
  cq.stem,
  cq.stem_html,
  coalesce(cq.option_texts, array[]::text[]) as option_texts,
  coalesce(cq.option_htmls, '[]'::jsonb) as option_htmls,
  cq.answer,
  coalesce(cq.shared_context_required, false) as shared_context_required,
  coalesce(cq.visual_required, false) as visual_required,
  coalesce(cq.needs_multimodal, false) as needs_multimodal,
  cq.material_id,
  cq.material_html,
  cq.material_text,
  cq.difficulty,
  cq.rate,
  cq.representative_correct_ratio,
  cq.representative_total_count,
  cq.representative_easy_wrong,
  cq.representative_most_wrong_answer,
  cq.source_member_count,
  cq.merged_source_member_count,
  cq.occurrence_count,
  cq.occurrence_province_count,
  cq.occurrence_paper_count,
  cq.first_year,
  cq.latest_year,
  cq.occurrence_provinces,
  cq.source_tag_link_count,
  cq.distinct_tag_count,
  cq.knowledge_chunk_count,
  cq.source_question_ids,
  cq.source_question_keys,
  cq.representative_classification,
  cq.representative_asset_refs,
  now() as imported_at
from canonical_qbank.canonical_question cq;

alter table product.questions
  add primary key (question_id);

create index product_questions_domain_idx
  on product.questions(domain);
create index product_questions_module_type_idx
  on product.questions(module_name, question_type);
create index product_questions_display_idx
  on product.questions(display_level1, display_level2, display_level3);
create index product_questions_status_idx
  on product.questions(status);
create index product_questions_year_idx
  on product.questions(latest_year desc nulls last, first_year);

create table product.question_options as
select
  q.question_id,
  opt.option_index,
  chr((64 + opt.option_index)::int) as label,
  opt.option_text,
  nullif(q.option_htmls->>((opt.option_index - 1)::int), '') as option_html
from product.questions q
cross join lateral unnest(q.option_texts) with ordinality as opt(option_text, option_index);

alter table product.question_options
  add primary key (question_id, option_index);
alter table product.question_options
  add constraint product_question_options_question_fk
  foreign key (question_id) references product.questions(question_id);

create table product.question_explanations as
select
  ce.canonical_question_id as question_id,
  ce.preferred_analysis,
  ce.original_analysis,
  ce.stored_analysis,
  ce.analysis_source,
  ce.analysis_status,
  coalesce(ce.analysis_needs_review, false) as analysis_needs_review,
  coalesce(ce.prefer_ai_analysis, false) as prefer_ai_analysis,
  ce.preferred_analysis_length,
  ce.final_analysis_asset_refs,
  now() as imported_at
from canonical_qbank.canonical_explanation ce;

alter table product.question_explanations
  add primary key (question_id);
alter table product.question_explanations
  add constraint product_question_explanations_question_fk
  foreign key (question_id) references product.questions(question_id);
create index product_question_explanations_source_idx
  on product.question_explanations(analysis_source, analysis_status);

create table product.question_occurrences as
select
  qo.source_occurrence_id as occurrence_id,
  qo.canonical_question_id as question_id,
  qo.source_question_id,
  qo.source_dataset_id,
  qo.source_paper_row_id,
  qo.source_paper_key,
  qo.source_occurrence_question_key,
  qo.source_occurrence_paper_key,
  qo.source_path,
  qo.cls,
  qo.province,
  qo.occurrence_paper_title,
  qo.paper_title,
  qo.year,
  qo.number,
  qo.section,
  qo.occurrence_answer,
  qo.occurrence_correct_ratio,
  qo.occurrence_total_count,
  qo.occurrence_easy_wrong,
  qo.occurrence_most_wrong_answer,
  qo.canonical_occurrence_rank
from canonical_qbank.question_occurrence qo;

alter table product.question_occurrences
  add primary key (occurrence_id);
alter table product.question_occurrences
  add constraint product_question_occurrences_question_fk
  foreign key (question_id) references product.questions(question_id);
create index product_question_occurrences_question_idx
  on product.question_occurrences(question_id, canonical_occurrence_rank);
create index product_question_occurrences_year_province_idx
  on product.question_occurrences(year desc nulls last, province);

create table product.question_tags as
select
  cqt.canonical_question_tag_id as question_tag_id,
  cqt.canonical_question_id as question_id,
  cqt.source_tag_id,
  cqt.tag_type,
  cqt.tag_value,
  cqt.sort_order,
  cqt.evidence_link_count,
  cqt.source_question_count,
  cqt.evidence_sources
from canonical_qbank.canonical_question_tag cqt;

alter table product.question_tags
  add primary key (question_tag_id);
alter table product.question_tags
  add constraint product_question_tags_question_fk
  foreign key (question_id) references product.questions(question_id);
create index product_question_tags_question_idx
  on product.question_tags(question_id, sort_order);
create index product_question_tags_type_value_idx
  on product.question_tags(tag_type, tag_value);

create table product.knowledge_chunks as
select
  kcl.source_knowledge_chunk_id as knowledge_chunk_id,
  kcl.canonical_question_id as question_id,
  kcl.source_question_id,
  kcl.source_dataset_id,
  kcl.source_paper_row_id,
  kcl.source_question_key,
  kcl.source_paper_key,
  kcl.chunk_kind,
  kcl.chunk_index,
  kcl.material_order,
  kcl.title,
  kcl.content,
  kcl.content_sha256,
  kcl.token_estimate,
  kcl.display_path,
  kcl.question_type,
  kcl.province,
  kcl.year,
  kcl.tags,
  kcl.metadata,
  kcl.embedding_provider,
  kcl.embedding_model,
  kcl.embedding_dimension,
  kcl.embedded_at
from canonical_qbank.knowledge_chunk_link kcl;

alter table product.knowledge_chunks
  add primary key (knowledge_chunk_id);
alter table product.knowledge_chunks
  add constraint product_knowledge_chunks_question_fk
  foreign key (question_id) references product.questions(question_id);
create index product_knowledge_chunks_question_idx
  on product.knowledge_chunks(question_id, chunk_kind, chunk_index);
create index product_knowledge_chunks_sha_idx
  on product.knowledge_chunks(source_dataset_id, content_sha256);

create table product.question_assets as
select
  qa.question_asset_id as asset_id,
  qa.canonical_question_id as question_id,
  qa.source_question_id,
  qa.is_representative_asset,
  qa.part_kind,
  qa.part_index,
  qa.image_ordinal,
  qa.remote_asset_root,
  qa.image_src,
  qa.local_or_remote_path as object_key,
  qa.source_html
from canonical_qbank.question_asset qa;

alter table product.question_assets
  add primary key (asset_id);
alter table product.question_assets
  add constraint product_question_assets_question_fk
  foreign key (question_id) references product.questions(question_id);
create index product_question_assets_question_idx
  on product.question_assets(question_id);
create index product_question_assets_object_key_idx
  on product.question_assets(object_key);

alter table product.questions owner to gongkao_owner;
alter table product.question_options owner to gongkao_owner;
alter table product.question_explanations owner to gongkao_owner;
alter table product.question_occurrences owner to gongkao_owner;
alter table product.question_tags owner to gongkao_owner;
alter table product.knowledge_chunks owner to gongkao_owner;
alter table product.question_assets owner to gongkao_owner;

grant select on
  product.questions,
  product.question_options,
  product.question_explanations,
  product.question_occurrences,
  product.question_tags,
  product.knowledge_chunks,
  product.question_assets
to gongkao_api;

grant select, insert, update, delete on
  product.questions,
  product.question_options,
  product.question_explanations,
  product.question_occurrences,
  product.question_tags,
  product.knowledge_chunks,
  product.question_assets
to gongkao_importer;
SQL

echo "== record product import summary =="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -v run_key="$RUN_KEY" <<'SQL'
create table if not exists ops.import_runs (
  run_key text primary key,
  import_kind text not null,
  source_ref text not null,
  source_sha256 text not null,
  status text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz not null default now(),
  note text not null default ''
);

create table if not exists ops.import_count_checks (
  run_key text not null references ops.import_runs(run_key) on delete cascade,
  metric text not null,
  actual_value bigint not null,
  expected_value bigint,
  status text not null,
  note text not null default '',
  checked_at timestamptz not null default now(),
  primary key (run_key, metric)
);

insert into ops.import_runs (
  run_key,
  import_kind,
  source_ref,
  source_sha256,
  status,
  note
) values (
  :'run_key',
  'product_knowledge_v1',
  'canonical_qbank.import_summary canonical_qbank_v1_20260703',
  '7d07c3296f047ffa038d56dcefc406fbc6ad78ac3a9bc57144e4c24ef9e572f5',
  'completed',
  'Imported canonical_qbank v1 audit layer into product runtime knowledge tables.'
) on conflict (run_key) do update set
  status = excluded.status,
  completed_at = now(),
  note = excluded.note;

with expected(metric, expected_value) as (
  values
    ('product_questions', 55214::bigint),
    ('product_objective_questions', 53011::bigint),
    ('product_essay_questions', 2203::bigint),
    ('product_question_explanations', 53011::bigint),
    ('product_question_occurrences', 104453::bigint),
    ('product_question_tags', 557844::bigint),
    ('product_knowledge_chunks', 23103::bigint),
    ('product_question_assets', 52112::bigint)
), actual(metric, actual_value, note) as (
  values
    ('product_questions', (select count(*) from product.questions), 'runtime product question rows'),
    ('product_objective_questions', (select count(*) from product.questions where domain = 'objective'), 'runtime objective question rows'),
    ('product_essay_questions', (select count(*) from product.questions where domain = 'essay'), 'runtime essay question rows'),
    ('product_question_explanations', (select count(*) from product.question_explanations), 'runtime explanation rows'),
    ('product_question_occurrences', (select count(*) from product.question_occurrences), 'runtime source occurrence rows'),
    ('product_question_tags', (select count(*) from product.question_tags), 'runtime canonical tag rows'),
    ('product_knowledge_chunks', (select count(*) from product.knowledge_chunks), 'runtime knowledge chunks'),
    ('product_question_assets', (select count(*) from product.question_assets), 'runtime question asset refs')
)
insert into ops.import_count_checks (
  run_key,
  metric,
  actual_value,
  expected_value,
  status,
  note
)
select
  :'run_key',
  actual.metric,
  actual.actual_value,
  expected.expected_value,
  case when actual.actual_value = expected.expected_value then 'matched' else 'mismatch' end,
  actual.note
from actual
join expected using (metric)
on conflict (run_key, metric) do update set
  actual_value = excluded.actual_value,
  expected_value = excluded.expected_value,
  status = excluded.status,
  note = excluded.note,
  checked_at = now();
SQL

echo "== product count checks =="
sudo -u postgres psql -d "$DB_NAME" -c "select metric, actual_value, expected_value, status from ops.import_count_checks where run_key = '$RUN_KEY' order by metric;"

echo "== sample question id =="
sudo -u postgres psql -d "$DB_NAME" -Atc "select question_id from product.questions where domain = 'objective' and coalesce(array_length(option_texts, 1), 0) > 0 and answer is not null order by latest_year desc nulls last, occurrence_count desc, question_id limit 1;"

echo "== disk =="
df -h /
REMOTE

GONGKAO_SERVER_HOST="$HOST" \
GONGKAO_SSH_KEY="$SSH_KEY" \
GONGKAO_DB_NAME="$DB_NAME" \
"$SCRIPT_DIR/server_refresh_guokao_exam_tag_weights.sh"
