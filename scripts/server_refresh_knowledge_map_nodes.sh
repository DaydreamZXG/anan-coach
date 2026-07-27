#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
execution_mode="remote"
if [[ "${1:-}" == "--local" ]]; then
  execution_mode="local"
  shift
fi
if (( $# > 0 )); then
  printf '%s\n' "usage: $0 [--local]" >&2
  exit 2
fi

HOST="${GONGKAO_SERVER_HOST:-101.96.198.253}"
SSH_KEY="${GONGKAO_SSH_KEY:-$HOME/.ssh/gongkao_dazi_ed25519}"
DB_NAME="${GONGKAO_DB_NAME:-gongkao_dazi}"
REFRESH_SQL="${GONGKAO_KNOWLEDGE_MAP_SQL:-$SCRIPT_DIR/sql/refresh_knowledge_map_nodes.sql}"
: "${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION:?GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION:?GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION is required}"
: "${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL:?GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL is required}"

if [[ ! -f "$REFRESH_SQL" ]]; then
  echo "Missing refresh SQL: $REFRESH_SQL" >&2
  exit 1
fi

printf -v release_version_arg '%q' "$GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION"
printf -v source_data_version_arg '%q' "$GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION"
printf -v fresh_until_arg '%q' "$GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL"

run_psql() {
  if [[ "${execution_mode}" == "local" ]]; then
    : "${GONGKAO_KNOWLEDGE_MAP_DATABASE_URL:?GONGKAO_KNOWLEDGE_MAP_DATABASE_URL is required for --local}"
    : "${GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER:?GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER is required for --local}"
    sudo -u "${GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER}" \
      psql \
      -v ON_ERROR_STOP=1 \
      --set=release_version="${GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION}" \
      --set=source_data_version="${GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION}" \
      --set=fresh_until="${GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL}" \
      --dbname="${GONGKAO_KNOWLEDGE_MAP_DATABASE_URL}"
    return
  fi

  ssh -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=20 \
    "root@$HOST" \
    "sudo -u postgres psql -v ON_ERROR_STOP=1 \
      --set=release_version=$release_version_arg \
      --set=source_data_version=$source_data_version_arg \
      --set=fresh_until=$fresh_until_arg \
      -d '$DB_NAME'"
}

echo "== publish knowledge map release =="
{
  cat <<'PSQL_PARAMETER_PREAMBLE'
select set_config(
  'app.knowledge_map_release_version',
  :'release_version',
  false
);
select set_config(
  'app.knowledge_map_source_data_version',
  :'source_data_version',
  false
);
select set_config(
  'app.knowledge_map_fresh_until',
  :'fresh_until',
  false
);
PSQL_PARAMETER_PREAMBLE
  cat "$REFRESH_SQL"
  cat <<'PSQL_POSTCOMMIT_VALIDATION'
\echo == verify committed knowledge map release ==
do $postcommit$
declare
  committed_release product.knowledge_map_releases%rowtype;
  actual_node_count integer;
  actual_binding_count integer;
  actual_binding_fingerprint text;
begin
  select release.*
  into strict committed_release
  from product.knowledge_map_releases release
  where release.release_version =
        current_setting('app.knowledge_map_release_version')
    and release.exam_scope = 'guokao'
    and release.track = 'xingce'
    and release.source_data_version =
        current_setting('app.knowledge_map_source_data_version')
    and release.fresh_until =
        current_setting('app.knowledge_map_fresh_until')::timestamptz
    and release.status = 'active';

  select count(*)::integer
  into actual_node_count
  from product.knowledge_map_nodes node
  where node.release_id = committed_release.release_id;

  select manifest.binding_count, manifest.binding_fingerprint
  into actual_binding_count, actual_binding_fingerprint
  from product.knowledge_map_question_binding_manifest(
    committed_release.release_id
  ) manifest;

  if committed_release.release_version like 'knowledge-map@legacy-pre-%'
     or committed_release.source_data_version like 'legacy:%'
     or committed_release.vocab_version like 'legacy:%'
     or committed_release.generator_version like 'legacy:%'
     or committed_release.fresh_until <= clock_timestamp()
     or committed_release.node_count <= 0
     or actual_node_count <> committed_release.node_count
     or committed_release.question_binding_count <= 0
     or actual_binding_count <> committed_release.question_binding_count
     or actual_binding_fingerprint is distinct from
        committed_release.question_binding_fingerprint
     or (
       select count(*)
     from product.knowledge_map_releases release
       where release.exam_scope = 'guokao'
         and release.track = 'xingce'
         and release.status = 'active'
     ) <> 1
     or (
       select count(*)
       from product.vocab_module_alias_map canonical
       where canonical.version = committed_release.vocab_version
         and canonical.action = 'canonical'
     ) <> 6
     or (
       select count(distinct canonical.canonical_code)
       from product.vocab_module_alias_map canonical
       where canonical.version = committed_release.vocab_version
         and canonical.action = 'canonical'
     ) <> 6
     or (
       select count(*)
       from product.vocab_module_alias_map canonical
       where canonical.version = committed_release.vocab_version
         and canonical.action = 'canonical'
         and (canonical.canonical_code, canonical.raw_module_name) in (
           values
             ('political_theory', '政治理论'),
             ('general_knowledge', '常识判断'),
             ('verbal_comprehension', '言语理解与表达'),
             ('quantitative_relations', '数量关系'),
             ('judgment_reasoning', '判断推理'),
             ('data_analysis', '资料分析')
         )
     ) <> 6
     or (
       select count(distinct canonical.canonical_code)
       from product.vocab_module_alias_map canonical
       where canonical.version = committed_release.vocab_version
         and canonical.action = 'canonical'
         and exists (
           select 1
           from product.knowledge_map_nodes node
           join product.knowledge_map_question_bindings binding
             on binding.release_id = node.release_id
            and binding.node_id = node.node_id
            and binding.module_code = canonical.canonical_code
            and binding.canonical_module = canonical.raw_module_name
            and binding.delivery_eligible
           where node.release_id = committed_release.release_id
             and node.module_name = canonical.raw_module_name
             and node.status = 'published'
             and node.support_status = 'text_supported'
             and node.node_type in ('micro_skill', 'knowledge_point')
             and node.tag_type is not null
             and coalesce(node.near5_occurrence_count, 0) > 0
         )
     ) <> 6 then
    raise exception using
      errcode = '23514',
      message = 'committed knowledge-map release failed postcondition validation';
  end if;
end
$postcommit$;

\echo == knowledge map sample ==
select
  release.release_version,
  node.module_name,
  node.node_type,
  node.tag_value,
  node.support_status,
  node.near5_occurrence_count,
  node.near5_weight
from product.knowledge_map_releases release
join product.knowledge_map_nodes node
  on node.release_id = release.release_id
where release.release_version =
      current_setting('app.knowledge_map_release_version')
  and release.exam_scope = 'guokao'
  and release.track = 'xingce'
  and release.status = 'active'
  and release.fresh_until > now()
  and node.status = 'published'
order by
  node.near5_weight desc nulls last,
  node.near5_occurrence_count desc nulls last,
  node.module_name,
  node.tag_value
limit 20;
PSQL_POSTCOMMIT_VALIDATION
} | run_psql
