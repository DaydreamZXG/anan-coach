#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
REMOTE_ROOT="${GONGKAO_REMOTE_ROOT:-/srv/gongkao-dazi}"
REMOTE_QBANK="$REMOTE_ROOT/import-staging/qbank"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"
RUN_KEY="${GONGKAO_IMPORT_RUN_KEY:-canonical_qbank_v1_20260703}"

ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=20 \
  "root@$HOST" \
  "REMOTE_QBANK='$REMOTE_QBANK' DB_NAME='$DB_NAME' RUN_KEY='$RUN_KEY' bash -s" <<'REMOTE'
set -euo pipefail

cd "$REMOTE_QBANK"

DUMP="data/question-bank-export/db/question-bank-subset-20260702.dump"
CANONICAL_SQL="tools/question-bank/canonical-qbank-v1.sql"

if [[ ! -f "$DUMP" ]]; then
  echo "missing dump: $REMOTE_QBANK/$DUMP" >&2
  exit 1
fi

if [[ ! -f "$CANONICAL_SQL" ]]; then
  echo "missing canonical sql: $REMOTE_QBANK/$CANONICAL_SQL" >&2
  exit 1
fi

echo "== input checksum =="
sha256sum "$DUMP"

echo "== reset source staging tables =="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQL'
drop schema if exists canonical_qbank cascade;
drop schema if exists compaction cascade;
drop schema if exists source_qbank cascade;
create schema source_qbank authorization gongkao_owner;

drop table if exists public."QuestionBankDataset" cascade;
drop table if exists public."QuestionBankKnowledgeChunk" cascade;
drop table if exists public."QuestionBankKnowledgeNodeRead" cascade;
drop table if exists public."QuestionBankPaper" cascade;
drop table if exists public."QuestionBankQuestion" cascade;
drop table if exists public."QuestionBankQuestionList" cascade;
drop table if exists public."QuestionBankQuestionListGroup" cascade;
drop table if exists public."QuestionBankQuestionListItem" cascade;
drop table if exists public."QuestionBankQuestionOccurrence" cascade;
drop table if exists public."QuestionBankQuestionTag" cascade;
drop table if exists public."QuestionBankTag" cascade;
drop table if exists public."QuestionBankTypeGroupRead" cascade;
SQL

echo "== restore source dump into public staging =="
sudo -u postgres pg_restore \
  --dbname "$DB_NAME" \
  --no-owner \
  --no-acl \
  --exit-on-error \
  "$DUMP"

echo "== move restored source tables into source_qbank =="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQL'
alter table public."QuestionBankDataset" set schema source_qbank;
alter table public."QuestionBankKnowledgeChunk" set schema source_qbank;
alter table public."QuestionBankKnowledgeNodeRead" set schema source_qbank;
alter table public."QuestionBankPaper" set schema source_qbank;
alter table public."QuestionBankQuestion" set schema source_qbank;
alter table public."QuestionBankQuestionList" set schema source_qbank;
alter table public."QuestionBankQuestionListGroup" set schema source_qbank;
alter table public."QuestionBankQuestionListItem" set schema source_qbank;
alter table public."QuestionBankQuestionOccurrence" set schema source_qbank;
alter table public."QuestionBankQuestionTag" set schema source_qbank;
alter table public."QuestionBankTag" set schema source_qbank;
alter table public."QuestionBankTypeGroupRead" set schema source_qbank;
SQL

echo "== build canonical_qbank v1 =="
cat > /tmp/gongkao_run_canonical_qbank_v1.sql <<'SQL'
\set ON_ERROR_STOP on
set search_path to source_qbank, public;
\i tools/question-bank/canonical-qbank-v1.sql
SQL
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f /tmp/gongkao_run_canonical_qbank_v1.sql
rm -f /tmp/gongkao_run_canonical_qbank_v1.sql

echo "== record ops import summary =="
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
  'canonical_qbank_v1',
  'data/question-bank-export/db/question-bank-subset-20260702.dump',
  '7d07c3296f047ffa038d56dcefc406fbc6ad78ac3a9bc57144e4c24ef9e572f5',
  'completed',
  'Restored source dump into source_qbank and built canonical_qbank v1 on server.'
) on conflict (run_key) do update set
  status = excluded.status,
  completed_at = now(),
  note = excluded.note;

with expected(metric, expected_value) as (
  values
    ('canonical_questions_total', 55214::bigint),
    ('canonical_questions_objective', 53011::bigint),
    ('canonical_questions_essay', 2203::bigint),
    ('canonical_explanations', 53011::bigint),
    ('knowledge_chunk_links', 23103::bigint),
    ('source_questions_total', 55767::bigint),
    ('source_questions_mapped', 55461::bigint),
    ('source_questions_quarantined', 306::bigint),
    ('merge_review_queue_groups', 82::bigint),
    ('accepted_merge_groups', 243::bigint),
    ('accepted_merge_removed_duplicates', 247::bigint),
    ('accepted_merge_source_members', 490::bigint)
), actual as (
  select metric, value::bigint as actual_value, note
  from canonical_qbank.import_summary
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
  case
    when expected.expected_value is null then 'recorded'
    when expected.expected_value = actual.actual_value then 'matched'
    else 'mismatch'
  end,
  actual.note
from actual
left join expected using (metric)
on conflict (run_key, metric) do update set
  actual_value = excluded.actual_value,
  expected_value = excluded.expected_value,
  status = excluded.status,
  note = excluded.note,
  checked_at = now();
SQL

echo "== canonical import summary =="
sudo -u postgres psql -d "$DB_NAME" -c "select metric, value, note from canonical_qbank.import_summary order by metric;"

echo "== count checks =="
sudo -u postgres psql -d "$DB_NAME" -c "select metric, actual_value, expected_value, status from ops.import_count_checks where run_key = '$RUN_KEY' and expected_value is not null order by metric;"

echo "== disk =="
df -h /
REMOTE
