#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
target="${root}/${release_id}"
current_link="${GONGKAO_CURRENT_LINK:-/srv/gongkao-dazi/current}"
systemd_root="${GONGKAO_SYSTEMD_ROOT:-/etc/systemd/system}"
config_root="${GONGKAO_CONFIG_ROOT:-/etc/gongkao-dazi}"
previous_target="$(readlink -f "${current_link}" 2>/dev/null || true)"
switched=false
current_guard_capable=false
control_plane_backup=""
control_plane_changed=false
control_plane_destinations=()
runtime_identity_changed=false
runtime_config_root=""
essay_review_worker_enabled=false
coach_notifications_enabled=false
ilink_channel_enabled=false

case "${release_id}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "release id contains unsupported characters" >&2
    exit 2
    ;;
esac
: "${EXPECTED_DEPLOYMENT_ENVIRONMENT:?EXPECTED_DEPLOYMENT_ENVIRONMENT is required}"
: "${RELEASE_TRUST_PUBLIC_KEY:?RELEASE_TRUST_PUBLIC_KEY is required}"
: "${DATA_RELEASE_TRUST_PUBLIC_KEY:?DATA_RELEASE_TRUST_PUBLIC_KEY is required}"
case "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "expected deployment environment is invalid" >&2
    exit 2
    ;;
esac
assert_root_owned_file() {
  local path="$1"
  test -f "${path}"
  test ! -L "${path}"
  test "$(stat -c '%u' "${path}")" = 0
  test -z "$(find "${path}" -perm /022 -print -quit)"
}

assert_guard_capable_release() {
  local manifest="$1"
  local release_root="$2"
  local marker="${release_root}/ops/commercial-data-publication-guard.json"
  assert_root_owned_file "${marker}"
  python3 - "${manifest}" "${marker}" <<'PY'
from hashlib import sha256
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(sys.argv[2], "rb") as handle:
    encoded_marker = handle.read()
marker = json.loads(encoded_marker)
marker_digest = sha256(encoded_marker).hexdigest()
expected_marker = {
    "schemaVersion": "anan-release-capability/v1",
    "capability": "commercial-data-publication-guard",
    "contractVersion": "commercial-data-publication-guard@v1",
}
expected_authority = {
    "commercialDataPublication": {
        "capability": "commercial-data-publication-guard",
        "contractVersion": "commercial-data-publication-guard@v1",
        "markerPath": "ops/commercial-data-publication-guard.json",
        "markerSha256": marker_digest,
    }
}
if (
    marker != expected_marker
    or manifest.get("schemaVersion") != 4
    or manifest.get("guardCapabilities") != expected_authority
):
    raise SystemExit(
        "release lacks the signed commercial data publication guard capability"
    )
PY
}

load_snapshot_environment() {
  local path="$1"
  local assignments
  assert_root_owned_file "${path}"
  if ! assignments="$(python3 - "${path}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
key = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if not raw or raw.startswith("#"):
        continue
    if "=" not in raw:
        raise SystemExit(f"{path}: invalid snapshot environment line {number}")
    name, value = raw.split("=", 1)
    if key.fullmatch(name) is None or any(character.isspace() for character in value):
        raise SystemExit(f"{path}: invalid snapshot environment assignment {number}")
    print(f"{name}={value}")
PY
  )"; then
    return 1
  fi
  while IFS= read -r assignment; do
    [[ -n "${assignment}" ]] || continue
    export "${assignment}"
  done <<<"${assignments}"
}

create_release_config_snapshot() {
  local name
  local temporary
  local -a names=(
    api.env
    db.env
    worker-data-rights.env
    worker-cause-agent.env
    worker-external-learning.env
    worker-essay-review.env
    worker-coach-notification.env
    ilink-channel.env
    ilink-worker.env
    worker-heartbeat-data-rights.env
    worker-heartbeat-cause-agent.env
    worker-heartbeat-external-learning.env
    worker-heartbeat-essay-review.env
    worker-heartbeat-coach-notification.env
    worker-heartbeat-ilink.env
  )
  if [[ -f "${config_root}/backup.env" ]]; then
    names+=(backup.env)
  fi
  runtime_config_root="${root}/runtime-config/${release_id}"
  test -d "${root}"
  test ! -L "${root}"
  install -d -m 0700 "${root}/runtime-config"
  test ! -L "${root}/runtime-config"
  test ! -e "${runtime_config_root}"
  temporary="$(mktemp -d "${root}/runtime-config/.${release_id}.XXXXXX")"
  chmod 0700 "${temporary}"
  for name in "${names[@]}"; do
    assert_root_owned_file "${config_root}/${name}"
  done
  python3 - "${temporary}" "${config_root}" "${names[@]}" <<'PY'
from pathlib import Path
import re
import sys

destination = Path(sys.argv[1])
source_root = Path(sys.argv[2])
key = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
for name in sys.argv[3:]:
    source = source_root / name
    output: list[str] = []
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            raise SystemExit(f"{source}: invalid environment line {number}")
        variable, value = raw.split("=", 1)
        if key.fullmatch(variable) is None:
            raise SystemExit(f"{source}: invalid environment variable {number}")
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        if any(character.isspace() for character in value) or any(
            character in value for character in "\\\\\"'"
        ):
            raise SystemExit(f"{source}: unsupported environment value {number}")
        output.append(f"{variable}={value}")
    target = destination / name
    target.write_text("\n".join(output) + "\n", encoding="utf-8")
    target.chmod(0o400)
PY
  mv -f "${temporary}" "${runtime_config_root}"
}

seal_release_config_snapshot() {
  local identity_file="${runtime_config_root}/release-runtime.env"
  local manifest="${runtime_config_root}/config.snapshot.sha256"
  test -d "${runtime_config_root}"
  test ! -L "${runtime_config_root}"
  test ! -e "${manifest}"
  assert_root_owned_file "${identity_file}"
  python3 - "${runtime_config_root}" "${manifest}" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = Path(sys.argv[2])
entries = []
for path in sorted(root.glob("*.env")):
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"invalid snapshot entry: {path}")
    entries.append(f"{sha256(path.read_bytes()).hexdigest()}  {path.name}")
manifest.write_text("\n".join(entries) + "\n", encoding="utf-8")
manifest.chmod(0o400)
PY
  chmod 0500 "${runtime_config_root}"
}

assert_sealed_release_config_snapshot() {
  local snapshot_root="$1"
  local manifest="${snapshot_root}/config.snapshot.sha256"
  test -d "${snapshot_root}"
  test ! -L "${snapshot_root}"
  test -z "$(find "${snapshot_root}" -mindepth 1 -type l -print -quit)"
  test -z "$(find "${snapshot_root}" -mindepth 1 -type f -links +1 -print -quit)"
  assert_root_owned_file "${manifest}"
  (cd "${snapshot_root}" && shasum -a 256 -c "${manifest}") >/dev/null
}

use_release_config_snapshot() {
  runtime_config_root="${root}/runtime-config/${release_id}"
  assert_sealed_release_config_snapshot "${runtime_config_root}"
}

normalize_explicit_boolean() {
  local name="$1"
  local normalized="$2"
  normalized="${normalized#"${normalized%%[![:space:]]*}"}"
  normalized="${normalized%"${normalized##*[![:space:]]}"}"
  normalized="$(printf '%s' "${normalized}" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    1|true|yes|on)
      printf '%s\n' true
      ;;
    0|false|no|off)
      printf '%s\n' false
      ;;
    *)
      printf '%s\n' "${name} must be an explicit boolean" >&2
      return 2
      ;;
  esac
}

validate_worker_heartbeat_environments() {
  local name
  local path
  for name in \
    data-rights \
    cause-agent \
    external-learning \
    essay-review \
    coach-notification \
    ilink; do
    path="${config_root}/worker-heartbeat-${name}.env"
    assert_root_owned_file "${path}"
    if [[ "$(stat -c '%a' "${path}")" != 600 ]]; then
      printf '%s\n' \
        "worker heartbeat environment must have exact mode 0600: ${path}" \
        >&2
      return 1
    fi
  done
  python3 - "${config_root}" <<'PY'
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

expected = {
    "data-rights": "gongkao_worker_heartbeat_data_rights_login",
    "cause-agent": "gongkao_worker_heartbeat_cause_agent_login",
    "external-learning": "gongkao_worker_heartbeat_external_learning_login",
    "essay-review": "gongkao_worker_heartbeat_essay_review_login",
    "coach-notification": "gongkao_worker_heartbeat_coach_notification_login",
    "ilink": "gongkao_worker_heartbeat_ilink_login",
}
config_root = Path(sys.argv[1])
passwords: set[str] = set()
urls: set[str] = set()
for name, expected_username in expected.items():
    path = config_root / f"worker-heartbeat-{name}.env"
    assignments: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path.name} contains an invalid environment line")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in assignments:
            raise SystemExit(f"{path.name} contains a duplicate {key}")
        assignments[key] = value
    if set(assignments) != {"WORKER_HEARTBEAT_DATABASE_URL"}:
        raise SystemExit(
            f"{path.name} must contain only WORKER_HEARTBEAT_DATABASE_URL"
        )
    url = assignments["WORKER_HEARTBEAT_DATABASE_URL"]
    parsed = urlsplit(url)
    password = unquote(parsed.password or "")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.username != expected_username
        or not password
        or "replace_with" in password.casefold()
        or not parsed.hostname
        or not parsed.path.lstrip("/")
        or parsed.fragment
        or query.get("sslmode") != ["verify-full"]
        or len(query.get("sslrootcert", [])) != 1
        or not query["sslrootcert"][0].startswith("/")
    ):
        raise SystemExit(f"{path.name} has an invalid heartbeat database identity")
    if password in passwords or url in urls:
        raise SystemExit("worker heartbeat credentials must be distinct")
    passwords.add(password)
    urls.add(url)
PY
}

validate_worker_workload_environments() {
  local name
  local path
  for name in \
    worker-data-rights.env \
    worker-cause-agent.env \
    worker-external-learning.env \
    worker-essay-review.env \
    worker-coach-notification.env \
    ilink-channel.env \
    ilink-worker.env; do
    path="${config_root}/${name}"
    assert_root_owned_file "${path}"
    if [[ "$(stat -c '%a' "${path}")" != 600 ]]; then
      printf '%s\n' \
        "worker workload environment must have exact mode 0600: ${path}" \
        >&2
      return 1
    fi
  done
  assert_root_owned_file "${config_root}/db.env"
  if [[ "$(stat -c '%a' "${config_root}/db.env")" != 600 ]]; then
    printf '%s\n' "database environment must have exact mode 0600" >&2
    return 1
  fi
  python3 - "${config_root}" "${target}/ops" <<'PY'
from pathlib import Path
import re
import sys

config_root = Path(sys.argv[1])
contract_root = Path(sys.argv[2])
database_contract_version = "gongkao-database-environment/v1"
names = (
    "worker-data-rights.env",
    "worker-cause-agent.env",
    "worker-external-learning.env",
    "worker-essay-review.env",
    "worker-coach-notification.env",
    "ilink-channel.env",
    "ilink-worker.env",
)
key_pattern = re.compile(r"^[A-Z][A-Z0-9_]*$")
database_required_keys = {
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "MIGRATION_PGPASSFILE",
    "REQUIRED_DATABASE_REVISION",
}
database_allowed_keys = database_required_keys | {
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT_SECONDS",
    "DATABASE_STATEMENT_TIMEOUT_MS",
    "DATABASE_LOCK_TIMEOUT_MS",
    "DATABASE_IDLE_TRANSACTION_TIMEOUT_MS",
    "MIGRATION_DATABASE_OS_USER",
}
worker_environment_files = {
    "gongkao-dazi-data-rights-worker.service": (
        "/etc/gongkao-dazi/worker-data-rights.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-data-rights.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
    "gongkao-dazi-cause-agent-worker.service": (
        "/etc/gongkao-dazi/worker-cause-agent.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-cause-agent.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
    "gongkao-dazi-external-learning-worker.service": (
        "/etc/gongkao-dazi/worker-external-learning.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-external-learning.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
    "gongkao-dazi-essay-review-worker.service": (
        "/etc/gongkao-dazi/worker-essay-review.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-essay-review.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
    "gongkao-dazi-coach-notification-worker.service": (
        "/etc/gongkao-dazi/worker-coach-notification.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-coach-notification.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
    "gongkao-dazi-ilink-worker.service": (
        "/etc/gongkao-dazi/ilink-channel.env",
        "/etc/gongkao-dazi/ilink-worker.env",
        "/etc/gongkao-dazi/db.env",
        "/etc/gongkao-dazi/worker-heartbeat-ilink.env",
        "/etc/gongkao-dazi/release-runtime.env",
    ),
}

def keys(path: Path) -> set[str]:
    result: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path.name} contains an invalid environment line")
        key = line.split("=", 1)[0].strip()
        if key in result or key_pattern.fullmatch(key) is None:
            raise SystemExit(f"{path.name} contains an invalid or duplicate key")
        result.add(key)
    return result

database_keys = keys(config_root / "db.env")
unexpected_database_keys = database_keys - database_allowed_keys
missing_database_keys = database_required_keys - database_keys
if unexpected_database_keys or missing_database_keys:
    details: list[str] = []
    if unexpected_database_keys:
        details.append(
            "forbidden keys " + ", ".join(sorted(unexpected_database_keys))
        )
    if missing_database_keys:
        details.append(
            "missing required keys " + ", ".join(sorted(missing_database_keys))
        )
    raise SystemExit(
        f"db.env violates {database_contract_version}: {'; '.join(details)}"
    )

workload_keys: set[str] = set()
for name in names:
    actual = keys(config_root / name)
    expected = keys(contract_root / f"{name}.example")
    if actual != expected:
        raise SystemExit(f"{name} does not match its release-scoped key contract")
    if {
        "DATABASE_URL",
        "WORKER_HEARTBEAT_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "MIGRATION_PGPASSFILE",
    } & actual:
        raise SystemExit(f"{name} contains a forbidden database authority")
    workload_keys.update(actual)

database_workload_overlap = database_keys & workload_keys
if database_workload_overlap:
    raise SystemExit(
        f"db.env violates {database_contract_version}: shared database keys "
        "would override worker workload keys "
        + ", ".join(sorted(database_workload_overlap))
    )

for unit_name, expected_files in worker_environment_files.items():
    unit_path = contract_root / "systemd" / unit_name
    actual_files: list[str] = []
    section = ""
    for raw_line in unit_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section == "[Service]" and line.startswith("EnvironmentFile="):
            actual_files.append(line.split("=", 1)[1].strip())
    if tuple(actual_files) != expected_files:
        raise SystemExit(
            f"{unit_name} EnvironmentFile load sequence violates "
            f"{database_contract_version}"
        )
PY
}

validate_worker_enablement_alignment() {
  python3 - \
    "${config_root}/worker-essay-review.env" \
    "${config_root}/worker-coach-notification.env" \
    "${essay_review_worker_enabled}" \
    "${coach_notifications_enabled}" <<'PY'
from pathlib import Path
import sys

def explicit_boolean(path: Path, key: str) -> bool:
    matches: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            normalized = value.strip().strip("\"'").strip().casefold()
            matches.append(normalized)
    if len(matches) != 1:
        raise SystemExit(f"{path.name} must define {key} exactly once")
    if matches[0] in {"1", "true", "yes", "on"}:
        return True
    if matches[0] in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{path.name} has an invalid {key}")

expected = (
    (Path(sys.argv[1]), "ESSAY_REVIEW_WORKER_ENABLED", sys.argv[3] == "true"),
    (Path(sys.argv[2]), "COACH_NOTIFICATIONS_ENABLED", sys.argv[4] == "true"),
)
for path, key, enabled in expected:
    if explicit_boolean(path, key) is not enabled:
        raise SystemExit(f"{path.name} disagrees with the API {key} authority")
PY
}

ensure_worker_runtime_principals() {
  local release_root="$1"
  local sysusers_contract="${release_root}/ops/sysusers.d/gongkao-dazi-workers.conf"
  local api_uid
  local entry
  local gid
  local shell
  local uid
  local user
  local seen_uids=" "
  test -f "${sysusers_contract}"
  test ! -L "${sysusers_contract}"
  assert_root_owned_file "${config_root}/db-ca.pem"
  command -v systemd-sysusers >/dev/null
  systemd-sysusers "${sysusers_contract}"
  api_uid="$(id -u deploy)"
  for user in \
    gongkao-wkr-data \
    gongkao-wkr-cause \
    gongkao-wkr-external \
    gongkao-wkr-essay \
    gongkao-wkr-coach \
    gongkao-wkr-ilink; do
    entry="$(getent passwd "${user}")"
    IFS=: read -r _ _ uid gid _ _ shell <<<"${entry}"
    if [[ ! "${uid}" =~ ^[0-9]+$ \
      || ! "${gid}" =~ ^[0-9]+$ \
      || "${uid}" == "${api_uid}" \
      || "${seen_uids}" == *" ${uid} "* \
      || "${shell}" != */nologin \
      || "$(id -gn "${user}")" != "${user}" ]]; then
      printf '%s\n' "worker runtime principal is not isolated: ${user}" >&2
      return 1
    fi
    seen_uids+="${uid} "
    sudo -u "${user}" test -x "${release_root}/backend/.venv/bin/python"
    sudo -u "${user}" test -r "${config_root}/db-ca.pem"
  done
}

run_worker_configuration_preflight() {
  local code="$1"
  shift
  env -i \
    PATH="${PATH}" \
    HARNESS_REAL_PYTHON="${HARNESS_REAL_PYTHON:-}" \
    TARGET_ROOT="${target}" \
    CONFIG_ROOT="${runtime_config_root}" \
    RELEASE_APP_ENV="${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    RELEASE_APP_GIT_COMMIT="${target_commit}" \
    RELEASE_DATA_RECEIPT_SHA256="${current_data_receipt_sha256}" \
    /bin/bash -Eeuo pipefail -c '
      code="$1"
      shift
      load_environment() {
        local assignments
        assignments="$(python3 - "$1" <<PY
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
key = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if not raw or raw.startswith("#"):
        continue
    if "=" not in raw:
        raise SystemExit(f"{path}: invalid snapshot environment line {number}")
    name, value = raw.split("=", 1)
    if key.fullmatch(name) is None or any(character.isspace() for character in value):
        raise SystemExit(f"{path}: invalid snapshot environment assignment {number}")
    print(f"{name}={value}")
PY
        )"
        while IFS= read -r assignment; do
          [[ -n "${assignment}" ]] || continue
          export "${assignment}"
        done <<<"${assignments}"
      }
      load_environment "${CONFIG_ROOT}/db.env"
      for environment_name in "$@"; do
        load_environment "${CONFIG_ROOT}/${environment_name}"
      done
      export APP_ENV="${RELEASE_APP_ENV}"
      export APP_GIT_COMMIT="${RELEASE_APP_GIT_COMMIT}"
      export APP_DATA_RECEIPT_SHA256="${RELEASE_DATA_RECEIPT_SHA256}"
      cd "${TARGET_ROOT}/backend"
      exec "${TARGET_ROOT}/backend/.venv/bin/python" -c "${code}"
    ' worker-preflight "${code}" "$@"
}

preflight_worker_configurations() {
  run_worker_configuration_preflight \
    'from app.core.config import get_settings; from app.domains.learning_media.composition import build_learning_media_storage; build_learning_media_storage(get_settings())' \
    worker-data-rights.env \
    worker-heartbeat-data-rights.env
  run_worker_configuration_preflight \
    'from app.core.config import get_settings; from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker; CauseAgentWorker(settings=get_settings())' \
    worker-cause-agent.env \
    worker-heartbeat-cause-agent.env
  run_worker_configuration_preflight \
    'from app.core.config import get_settings; from app.workers.external_learning import _validate_worker_settings; settings = get_settings(); _validate_worker_settings(settings)' \
    worker-external-learning.env \
    worker-heartbeat-external-learning.env
  if [[ "${essay_review_worker_enabled}" == true ]]; then
    run_worker_configuration_preflight \
      'from app.core.config import get_settings; from app.domains.essay_intelligence.review_worker import GroundedEssayReviewWorker; from app.workers.essay_reviews import _validate_worker_settings; settings = get_settings(); _validate_worker_settings(settings); GroundedEssayReviewWorker(settings=settings)' \
      worker-essay-review.env \
      worker-heartbeat-essay-review.env
  fi
  if [[ "${coach_notifications_enabled}" == true ]]; then
    run_worker_configuration_preflight \
      'from sqlalchemy.orm import Session; from app.core.config import get_settings; from app.domains.coach_notifications.worker import CoachNotificationWorker; session = Session(); CoachNotificationWorker(session=session, settings=get_settings()); session.close()' \
      worker-coach-notification.env \
      worker-heartbeat-coach-notification.env
  fi
  if [[ "${ilink_channel_enabled}" == true ]]; then
    run_worker_configuration_preflight \
      'from app.workers.ilink_channel import ILinkWorker; ILinkWorker()' \
      ilink-channel.env \
      ilink-worker.env \
      worker-heartbeat-ilink.env
  fi
}

for trust_key in \
  "${RELEASE_TRUST_PUBLIC_KEY}" \
  "${DATA_RELEASE_TRUST_PUBLIC_KEY}"; do
  if [[ ! "${trust_key}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "rollback trust key must be a safe absolute path" >&2
    exit 2
  fi
  assert_root_owned_file "${trust_key}"
done
environment_file="${config_root}/release-environment"
assert_root_owned_file "${environment_file}"
actual_environment="$(tr -d '\r\n' < "${environment_file}")"
if [[ "${actual_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
  printf '%s\n' "rollback environment does not match the trusted target" >&2
  exit 1
fi
test -d "${target}"
test ! -L "${target}"
target_identity="$(readlink -f "${target}")"
case "${target_identity}" in
  "${root}/"*) ;;
  *)
    printf '%s\n' "rollback target escapes the trusted release root" >&2
    exit 1
    ;;
esac
test -n "${previous_target}"
test -d "${previous_target}"
case "${previous_target}" in
  "${root}/"*) ;;
  *)
    printf '%s\n' "current release escapes the trusted release root" >&2
    exit 1
    ;;
esac
current_release_id="$(basename "${previous_target}")"
activation_marker="${target}/release-authority/activation.json"
eligibility_marker="${target}/release-authority/rollback-eligible/${current_release_id}.json"
if [[ -e "${eligibility_marker}" || -L "${eligibility_marker}" ]]; then
  selected_rollback_marker="${eligibility_marker}"
  marker_kind="eligibility"
elif [[ -e "${activation_marker}" || -L "${activation_marker}" ]]; then
  selected_rollback_marker="${activation_marker}"
  marker_kind="activation"
else
  printf '%s\n' \
    "rollback target has no successful activation or rollback eligibility" >&2
  exit 1
fi
test ! -L "${selected_rollback_marker}"
test "$(stat -c '%u' "${selected_rollback_marker}")" = 0
test -z "$(find "${selected_rollback_marker}" -perm /022 -print -quit)"
target_manifest="${target}/release-authority/release.json"
target_manifest_signature="${target_manifest}.sig"
current_manifest="${previous_target}/release-authority/release.json"
current_manifest_signature="${current_manifest}.sig"
for authority_file in \
  "${target_manifest}" \
  "${target_manifest_signature}" \
  "${current_manifest}" \
  "${current_manifest_signature}"; do
  test -f "${authority_file}"
done
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${target_manifest_signature}" \
  "${target_manifest}"
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${current_manifest_signature}" \
  "${current_manifest}"
assert_guard_capable_release "${target_manifest}" "${target}"
assert_guard_capable_release "${current_manifest}" "${previous_target}"
current_guard_capable=true
python3 - \
  "${selected_rollback_marker}" \
  "${marker_kind}" \
  "${release_id}" \
  "${current_release_id}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    marker = json.load(handle)
if sys.argv[2] == "activation":
    valid = (
        marker.get("schemaVersion") == "anan-application-activation/v1"
        and marker.get("status") == "succeeded"
        and marker.get("releaseId") == sys.argv[3]
    )
else:
    valid = (
        marker.get("schemaVersion") == "anan-rollback-eligibility/v1"
        and marker.get("status") == "eligible"
        and marker.get("targetReleaseId") == sys.argv[3]
        and marker.get("authorizedByReleaseId") == sys.argv[4]
    )
if not valid or marker.get("environment") != sys.argv[5]:
    raise SystemExit("rollback marker authority is invalid")
PY
IFS=$'\t' read -r target_commit current_commit < <(
  python3 - \
    "${target_manifest}" \
    "${current_manifest}" \
    "${release_id}" \
    "${current_release_id}" \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    target = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    current = json.load(handle)
target_commit = target.get("source", {}).get("gitCommit")
current_commit = current.get("source", {}).get("gitCommit")
if (
    target.get("releaseId") != sys.argv[3]
    or current.get("releaseId") != sys.argv[4]
    or target.get("deployment", {}).get("environment") != sys.argv[5]
    or current.get("deployment", {}).get("environment") != sys.argv[5]
    or not isinstance(target_commit, str)
    or not isinstance(current_commit, str)
    or re.fullmatch(r"[0-9a-f]{40,64}", target_commit) is None
    or re.fullmatch(r"[0-9a-f]{40,64}", current_commit) is None
):
    raise SystemExit("rollback release manifest identity is invalid")
print(target_commit, current_commit, sep="\t")
PY
)
selected_rollback_marker_sha256="$(
  shasum -a 256 "${selected_rollback_marker}" | awk '{print $1}'
)"
target_manifest_sha256="$(
  shasum -a 256 "${target_manifest}" | awk '{print $1}'
)"
current_manifest_sha256="$(
  shasum -a 256 "${current_manifest}" | awk '{print $1}'
)"
export APP_ENV="${EXPECTED_DEPLOYMENT_ENVIRONMENT}"
export APP_GIT_COMMIT="${target_commit}"

acquire_data_release_lock() {
  local lock_path="${DATA_RELEASE_COORDINATION_LOCK:-${config_root}/data-release.lock}"
  local lock_parent
  if [[ ! "${lock_path}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "data release coordination lock path is invalid" >&2
    return 1
  fi
  lock_parent="$(dirname "${lock_path}")"
  test -d "${lock_parent}"
  test "$(stat -c '%u' "${lock_parent}")" = 0
  test -z "$(find "${lock_parent}" -maxdepth 0 -perm /022 -print -quit)"
  if [[ ! -e "${lock_path}" ]]; then
    install -m 0600 /dev/null "${lock_path}"
  fi
  test -f "${lock_path}"
  test ! -L "${lock_path}"
  test "$(stat -c '%u' "${lock_path}")" = 0
  test "$(stat -c '%a' "${lock_path}")" = 600
  exec 9>>"${lock_path}"
  flock -x 9
}

write_runtime_identity() {
  local commit="$1"
  local receipt_sha256="$2"
  local identity_file="${runtime_config_root}/release-runtime.env"
  local temporary
  if [[ ! "${commit}" =~ ^[0-9a-f]{40,64}$ ]]; then
    printf '%s\n' "runtime application commit is invalid" >&2
    return 1
  fi
  if [[ ! "${receipt_sha256}" =~ ^[0-9a-f]{64}$ ]]; then
    printf '%s\n' "runtime data receipt digest is invalid" >&2
    return 1
  fi
  temporary="$(mktemp "${runtime_config_root}/.release-runtime.XXXXXX")"
  printf 'APP_ENV=%s\nAPP_GIT_COMMIT=%s\nAPP_DATA_RECEIPT_SHA256=%s\n' \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    "${commit}" \
    "${receipt_sha256}" >"${temporary}"
  chmod 0400 "${temporary}"
  mv -f "${temporary}" "${identity_file}"
  seal_release_config_snapshot
}

resolve_current_data_authority() {
  local resolved
  local authority_file
  data_authority_root="${DATA_RELEASE_AUTHORITY_ROOT:-${config_root}/data-releases}"
  if [[ ! "${data_authority_root}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "data release authority root is invalid" >&2
    return 1
  fi
  test -d "${data_authority_root}"
  test ! -L "${data_authority_root}"
  test "$(stat -c '%u' "${data_authority_root}")" = 0
  test -z "$(
    find "${data_authority_root}" -maxdepth 0 -perm /022 -print -quit
  )"
  resolved="$(readlink -f "${data_authority_root}/current")"
  case "${resolved}" in
    "${data_authority_root}/revisions/"*) ;;
    *)
      printf '%s\n' "current data authority escapes its trusted root" >&2
      return 1
      ;;
  esac
  test -d "${resolved}"
  current_data_receipt="${resolved}/receipt.json"
  current_data_signature="${resolved}/receipt.json.sig"
  for authority_file in \
    "${current_data_receipt}" \
    "${current_data_signature}"; do
    test -f "${authority_file}"
    test ! -L "${authority_file}"
    test "$(stat -c '%u' "${authority_file}")" = 0
    test -z "$(find "${authority_file}" -perm /022 -print -quit)"
  done
  openssl dgst -sha256 \
    -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${current_data_signature}" \
    "${current_data_receipt}"
  data_revision="$(
    python3 - \
      "${current_data_receipt}" \
      "${target_commit}" \
      "${current_commit}" \
      "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
compatible = data.get("compatibleApplicationCommits")
revision = data.get("database", {}).get("alembicRevision")
if (
    data.get("environment") != sys.argv[4]
    or not isinstance(compatible, list)
    or sys.argv[2] not in compatible
    or sys.argv[3] not in compatible
    or not isinstance(revision, str)
    or not revision
):
    raise SystemExit("global data release authority is invalid for rollback")
print(revision)
PY
  )"
}

install_release_units() {
  local release_root="$1"
  local unit
  local destination
  local temporary
  local found=false
  shopt -s nullglob
  for unit in "${release_root}/ops/systemd/"*.service "${release_root}/ops/systemd/"*.timer; do
    destination="${systemd_root}/$(basename "${unit}")"
    temporary="$(mktemp "${systemd_root}/.release-unit.XXXXXX")"
    if ! python3 - "${unit}" "${temporary}" "${runtime_config_root}" <<'PY'
from pathlib import Path
import sys

source, destination, snapshot = map(Path, sys.argv[1:])
allowed = {path.name for path in snapshot.glob("*.env")}
lines: list[str] = []
for raw in source.read_text(encoding="utf-8").splitlines():
    if not raw.startswith("EnvironmentFile="):
        lines.append(raw)
        continue
    prefix, path = raw.split("=", 1)
    optional = path.startswith("-")
    candidate = path[1:] if optional else path
    if not candidate.startswith("/etc/gongkao-dazi/"):
        lines.append(raw)
        continue
    name = Path(candidate).name
    if name not in allowed:
        if not optional:
            raise SystemExit(f"{source.name}: unsnapshotted EnvironmentFile {candidate}")
    lines.append(f"{prefix}={'-' if optional else ''}{snapshot / name}")
destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    then
      rm -f -- "${temporary}"
      return 1
    fi
    if ! install -m 0644 "${temporary}" "${destination}"; then
      rm -f -- "${temporary}"
      return 1
    fi
    rm -f -- "${temporary}"
    found=true
  done
  shopt -u nullglob
  if [[ "${found}" != true ]]; then
    printf '%s\n' "release contains no systemd unit definitions" >&2
    return 1
  fi
  systemctl daemon-reload
}

prepare_control_plane_backup() {
  local source
  local destination
  local name
  local found=false
  control_plane_backup="$(mktemp -d /tmp/anan-control-plane.XXXXXX)"
  shopt -s nullglob
  for source in "${target}/ops/systemd/"*.service "${target}/ops/systemd/"*.timer; do
    name="$(basename "${source}")"
    destination="${systemd_root}/${name}"
    control_plane_destinations+=("${destination}")
    if [[ -e "${destination}" || -L "${destination}" ]]; then
      cp -p -- "${destination}" "${control_plane_backup}/${name}"
    else
      : > "${control_plane_backup}/${name}.missing"
    fi
    found=true
  done
  shopt -u nullglob
  if [[ "${found}" != true ]]; then
    printf '%s\n' "release contains no systemd unit definitions" >&2
    return 1
  fi
}

restore_control_plane() {
  local destination
  local name
  if [[ "${control_plane_changed}" != true || -z "${control_plane_backup}" ]]; then
    return
  fi
  for destination in "${control_plane_destinations[@]}"; do
    name="$(basename "${destination}")"
    if [[ -f "${control_plane_backup}/${name}.missing" ]]; then
      rm -f -- "${destination}"
    else
      cp -p -- "${control_plane_backup}/${name}" "${destination}"
    fi
  done
  systemctl daemon-reload
  control_plane_changed=false
}

discard_control_plane_backup() {
  if [[ -z "${control_plane_backup}" ]]; then
    return
  fi
  case "${control_plane_backup}" in
    /tmp/anan-control-plane.*)
      rm -rf -- "${control_plane_backup}"
      ;;
    *)
      printf '%s\n' \
        "refusing unsafe control-plane backup cleanup ${control_plane_backup}" \
        >&2
      return 1
      ;;
  esac
  control_plane_backup=""
}

restore_original_pointer() {
  local exit_code=$?
  trap - ERR
  restore_control_plane || true
  if [[ "${switched}" == true \
    && "${current_guard_capable}" == true \
    && -n "${previous_target}" \
    && -d "${previous_target}" ]]; then
    if ! assert_sealed_release_config_snapshot \
      "${root}/runtime-config/$(basename "${previous_target}")"; then
      printf '%s\n' "current release lacks a sealed runtime configuration snapshot" >&2
      discard_control_plane_backup || true
      exit "${exit_code}"
    fi
    ln -sfn "${previous_target}" "${current_link}"
    runtime_identity_changed=false
    restore_units=(
      gongkao-dazi-api
      gongkao-dazi-external-learning-worker \
      gongkao-dazi-data-rights-worker
      gongkao-dazi-cause-agent-worker
    )
    if [[ "${essay_review_worker_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/essay_reviews.py" ]]; then
      restore_units+=(gongkao-dazi-essay-review-worker)
    else
      systemctl disable --now gongkao-dazi-essay-review-worker 2>/dev/null || true
    fi
    if [[ "${coach_notifications_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/coach_notifications.py" ]]; then
      restore_units+=(gongkao-dazi-coach-notification-worker)
    else
      systemctl disable --now \
        gongkao-dazi-coach-notification-worker 2>/dev/null || true
    fi
    if [[ "${COMMERCIAL_MODE:-free_cold_start}" == "paid" \
      && -f "${previous_target}/ops/systemd/gongkao-dazi-commerce-worker.service" \
      && -n "${WECHAT_VIRTUAL_PAY_OFFER_ID:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_APP_KEY:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY:-}" ]]; then
      restore_units+=(gongkao-dazi-commerce-worker)
    else
      systemctl disable --now gongkao-dazi-commerce-worker 2>/dev/null || true
    fi
    if [[ "${ilink_channel_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/ilink_channel.py" ]]; then
      restore_units+=(gongkao-dazi-ilink-worker)
    else
      systemctl disable --now gongkao-dazi-ilink-worker 2>/dev/null || true
    fi
    systemctl enable "${restore_units[@]}" || true
    systemctl restart "${restore_units[@]}" || true
  fi
  discard_control_plane_backup || true
  exit "${exit_code}"
}

trap restore_original_pointer ERR

ensure_worker_runtime_principals "${target}"
validate_worker_workload_environments
use_release_config_snapshot
load_snapshot_environment "${runtime_config_root}/api.env"
load_snapshot_environment "${runtime_config_root}/db.env"
load_snapshot_environment "${runtime_config_root}/ilink-channel.env"
load_snapshot_environment "${runtime_config_root}/ilink-worker.env"
essay_review_worker_enabled="$(
  normalize_explicit_boolean \
    ESSAY_REVIEW_WORKER_ENABLED \
    "${ESSAY_REVIEW_WORKER_ENABLED:-false}"
)"
coach_notifications_enabled="$(
  normalize_explicit_boolean \
    COACH_NOTIFICATIONS_ENABLED \
    "${COACH_NOTIFICATIONS_ENABLED:-false}"
)"
ilink_channel_enabled="$(
  normalize_explicit_boolean \
    ILINK_CHANNEL_ENABLED \
    "${ILINK_CHANNEL_ENABLED:-false}"
)"
validate_worker_enablement_alignment
validate_worker_heartbeat_environments
# The two revalidated manifests, not a stale generic env file, select the
# application identity used by rollback probes.
export APP_ENV="${EXPECTED_DEPLOYMENT_ENVIRONMENT}"
export APP_GIT_COMMIT="${target_commit}"

acquire_data_release_lock
locked_previous_target="$(readlink -f "${current_link}" 2>/dev/null || true)"
if [[ -z "${locked_previous_target}" \
  || ! -d "${locked_previous_target}" \
  || "${locked_previous_target}" != "${previous_target}" ]]; then
  printf '%s\n' \
    "current release changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
case "${locked_previous_target}" in
  "${root}/"*) ;;
  *)
    printf '%s\n' "locked current release escapes the trusted release root" >&2
    exit 1
    ;;
esac
locked_current_release_id="$(basename "${locked_previous_target}")"
if [[ "${locked_current_release_id}" != "${current_release_id}" ]]; then
  printf '%s\n' "locked current release identity is unexpected" >&2
  exit 1
fi
locked_target_identity="$(readlink -f "${target}" 2>/dev/null || true)"
if [[ -z "${locked_target_identity}" \
  || ! -d "${locked_target_identity}" \
  || -L "${target}" \
  || "${locked_target_identity}" != "${target_identity}" ]]; then
  printf '%s\n' \
    "rollback target changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
assert_root_owned_file "${environment_file}"
locked_environment="$(tr -d '\r\n' < "${environment_file}")"
if [[ "${locked_environment}" != "${actual_environment}" \
  || "${locked_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
  printf '%s\n' \
    "rollback environment changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
locked_activation_marker="${target}/release-authority/activation.json"
locked_eligibility_marker="${target}/release-authority/rollback-eligible/${locked_current_release_id}.json"
if [[ -e "${locked_eligibility_marker}" \
  || -L "${locked_eligibility_marker}" ]]; then
  locked_selected_rollback_marker="${locked_eligibility_marker}"
  locked_marker_kind="eligibility"
elif [[ -e "${locked_activation_marker}" \
  || -L "${locked_activation_marker}" ]]; then
  locked_selected_rollback_marker="${locked_activation_marker}"
  locked_marker_kind="activation"
else
  printf '%s\n' \
    "rollback target lost its activation or eligibility marker while waiting for the lock" \
    >&2
  exit 1
fi
if [[ "${locked_selected_rollback_marker}" != "${selected_rollback_marker}" \
  || "${locked_marker_kind}" != "${marker_kind}" ]]; then
  printf '%s\n' \
    "rollback marker selection changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
assert_root_owned_file "${locked_selected_rollback_marker}"
locked_marker_sha256="$(
  shasum -a 256 "${locked_selected_rollback_marker}" | awk '{print $1}'
)"
if [[ "${locked_marker_sha256}" != "${selected_rollback_marker_sha256}" ]]; then
  printf '%s\n' \
    "rollback marker changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
locked_target_manifest="${locked_target_identity}/release-authority/release.json"
locked_target_manifest_signature="${locked_target_manifest}.sig"
locked_current_manifest="${locked_previous_target}/release-authority/release.json"
locked_current_manifest_signature="${locked_current_manifest}.sig"
for locked_authority_file in \
  "${locked_target_manifest}" \
  "${locked_target_manifest_signature}" \
  "${locked_current_manifest}" \
  "${locked_current_manifest_signature}"; do
  assert_root_owned_file "${locked_authority_file}"
done
locked_target_manifest_sha256="$(
  shasum -a 256 "${locked_target_manifest}" | awk '{print $1}'
)"
locked_current_manifest_sha256="$(
  shasum -a 256 "${locked_current_manifest}" | awk '{print $1}'
)"
if [[ "${locked_target_manifest_sha256}" != "${target_manifest_sha256}" \
  || "${locked_current_manifest_sha256}" != "${current_manifest_sha256}" ]]; then
  printf '%s\n' \
    "release manifests changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${locked_target_manifest_signature}" \
  "${locked_target_manifest}"
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${locked_current_manifest_signature}" \
  "${locked_current_manifest}"
assert_guard_capable_release \
  "${locked_target_manifest}" \
  "${locked_target_identity}"
assert_guard_capable_release \
  "${locked_current_manifest}" \
  "${locked_previous_target}"
current_guard_capable=true
python3 - \
  "${locked_selected_rollback_marker}" \
  "${locked_marker_kind}" \
  "${release_id}" \
  "${locked_current_release_id}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    marker = json.load(handle)
if sys.argv[2] == "activation":
    valid = (
        marker.get("schemaVersion") == "anan-application-activation/v1"
        and marker.get("status") == "succeeded"
        and marker.get("releaseId") == sys.argv[3]
    )
else:
    valid = (
        marker.get("schemaVersion") == "anan-rollback-eligibility/v1"
        and marker.get("status") == "eligible"
        and marker.get("targetReleaseId") == sys.argv[3]
        and marker.get("authorizedByReleaseId") == sys.argv[4]
    )
if not valid or marker.get("environment") != sys.argv[5]:
    raise SystemExit("locked rollback marker authority is invalid")
PY
IFS=$'\t' read -r locked_target_commit locked_current_commit < <(
  python3 - \
    "${locked_target_manifest}" \
    "${locked_current_manifest}" \
    "${release_id}" \
    "${locked_current_release_id}" \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    target = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    current = json.load(handle)
target_commit = target.get("source", {}).get("gitCommit")
current_commit = current.get("source", {}).get("gitCommit")
if (
    target.get("releaseId") != sys.argv[3]
    or current.get("releaseId") != sys.argv[4]
    or target.get("deployment", {}).get("environment") != sys.argv[5]
    or current.get("deployment", {}).get("environment") != sys.argv[5]
    or not isinstance(target_commit, str)
    or not isinstance(current_commit, str)
    or re.fullmatch(r"[0-9a-f]{40,64}", target_commit) is None
    or re.fullmatch(r"[0-9a-f]{40,64}", current_commit) is None
):
    raise SystemExit("locked rollback release manifest identity is invalid")
print(target_commit, current_commit, sep="\t")
PY
)
if [[ "${locked_target_commit}" != "${target_commit}" \
  || "${locked_current_commit}" != "${current_commit}" ]]; then
  printf '%s\n' \
    "release commits changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
resolve_current_data_authority
current_data_receipt_sha256="$(
  shasum -a 256 "${current_data_receipt}" | awk '{print $1}'
)"
export APP_DATA_RECEIPT_SHA256="${current_data_receipt_sha256}"
python3 - \
  "${selected_rollback_marker}" \
  "${marker_kind}" \
  "${target_commit}" \
  "${current_commit}" \
  "${current_data_receipt_sha256}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    marker = json.load(handle)
if sys.argv[2] == "activation":
    authorized = marker.get("gitCommit") == sys.argv[3]
else:
    authorized = marker.get("authorizedByGitCommit") == sys.argv[4]
if not authorized or marker.get("dataReceiptSha256") != sys.argv[5]:
    raise SystemExit(
        "rollback marker is not authorized by the current global data receipt"
    )
PY
preflight_worker_configurations
test -d "${target}/backend"
test -x "${target}/backend/.venv/bin/uvicorn"
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${MIGRATION_PGPASSFILE:?MIGRATION_PGPASSFILE is required}"
migration_database_os_user="${MIGRATION_DATABASE_OS_USER:-postgres}"
test -f "${MIGRATION_PGPASSFILE}"
test ! -L "${MIGRATION_PGPASSFILE}"
test "$(stat -c '%a' "${MIGRATION_PGPASSFILE}")" = 600
test "$(stat -c '%u' "${MIGRATION_PGPASSFILE}")" = "$(
  id -u "${migration_database_os_user}"
)"
compatibility_python="${locked_previous_target}/backend/.venv/bin/python"
test -x "${compatibility_python}"
artifact_contract="${target}/ops/release-db-compatibility.env"
"${compatibility_python}" \
  -m app.core.database_compatibility compatible-revisions \
  --backend-root "${target}/backend" \
  --contract "${artifact_contract}" >/dev/null
current_revision="$(
  sudo -u "${migration_database_os_user}" \
    env \
      DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${compatibility_python}" \
      -m app.core.database_compatibility assert-current \
      --backend-root "${target}/backend" \
      --contract "${artifact_contract}"
)"
if [[ "${current_revision}" != "${data_revision}" ]]; then
  printf '%s\n' \
    "active data receipt revision does not match the live database" >&2
  exit 1
fi
sudo -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${compatibility_python}" \
    "${previous_target}/ops/scripts/commercial_data_publication.py" \
    assert-pairing \
    --receipt-sha256 "${current_data_receipt_sha256}" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${current_revision}" \
    --compatible-commit "${target_commit}" \
    --compatible-commit "${current_commit}" >/dev/null
data_release_verification="$(
  sudo -u "${migration_database_os_user}" \
    env \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${compatibility_python}" \
      "${previous_target}/ops/scripts/data_release_authority.py" \
      --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
      --expected-revision "${current_revision}" \
      --compatible-commit "${target_commit}" \
      --compatible-commit "${current_commit}" \
      verify \
      --receipt "${current_data_receipt}"
)"
test -n "${data_release_verification}"
runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-data-rights-worker
  gongkao-dazi-cause-agent-worker
)
case "${essay_review_worker_enabled}" in
  true)
    test -f "${target}/backend/app/workers/essay_reviews.py"
    runtime_units+=(gongkao-dazi-essay-review-worker)
    ;;
  false)
    disable_essay_review=true
    ;;
esac
if [[ "${coach_notifications_enabled}" == "true" \
  && -f "${target}/backend/app/workers/coach_notifications.py" ]]; then
  runtime_units+=(gongkao-dazi-coach-notification-worker)
else
  stop_coach_notifications=true
fi

if [[ "${COMMERCIAL_MODE:-free_cold_start}" == "paid" \
  && -n "${WECHAT_VIRTUAL_PAY_OFFER_ID:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_APP_KEY:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN:-}" \
  && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY:-}" ]]; then
  runtime_units+=(gongkao-dazi-commerce-worker)
else
  stop_commerce=true
fi

case "${ilink_channel_enabled}" in
  true)
    test -f "${target}/backend/app/workers/ilink_channel.py"
    runtime_units+=(gongkao-dazi-ilink-worker)
    ;;
  false)
    disable_ilink=true
    ;;
esac

assert_sealed_release_config_snapshot "${runtime_config_root}"
prepare_control_plane_backup
control_plane_changed=true
install_release_units "${target}"
ln -sfn "${target}" "${current_link}"
switched=true
runtime_identity_changed=true
systemctl enable "${runtime_units[@]}"
if [[ "${stop_coach_notifications:-false}" == true ]]; then
  systemctl disable --now \
    gongkao-dazi-coach-notification-worker 2>/dev/null || true
fi
if [[ "${stop_commerce:-false}" == true ]]; then
  systemctl disable --now gongkao-dazi-commerce-worker 2>/dev/null || true
fi
if [[ "${disable_essay_review:-false}" == true ]]; then
  systemctl disable --now gongkao-dazi-essay-review-worker 2>/dev/null || true
fi
if [[ "${disable_ilink:-false}" == true ]]; then
  systemctl disable --now gongkao-dazi-ilink-worker 2>/dev/null || true
fi
systemctl restart "${runtime_units[@]}"
ready=false
for _ in $(seq 1 30); do
  if curl \
    --disable \
    --fail \
    --silent \
    --show-error \
    --noproxy '*' \
    --connect-timeout 1 \
    --max-time 2 \
    --proto '=http' \
    http://127.0.0.1:8000/ready >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "${ready}" != true ]]; then
  systemctl --no-pager --full status gongkao-dazi-api >&2 || true
  false
fi
for unit in "${runtime_units[@]}"; do
  systemctl is-active --quiet "${unit}"
done
control_plane_changed=false
discard_control_plane_backup
trap - ERR
printf '%s\n' \
  "rolled back application release to ${release_id} at database revision ${current_revision}"
