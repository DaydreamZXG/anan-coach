#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:?release id is required}"
root="${STAGING_RELEASE_ROOT:-/srv/gongkao-dazi/releases}"
current_link="${GONGKAO_CURRENT_LINK:-/srv/gongkao-dazi/current}"
controller_root="${GONGKAO_CONTROLLER_ROOT:-/srv/gongkao-dazi/ops/scripts}"
systemd_root="${GONGKAO_SYSTEMD_ROOT:-/etc/systemd/system}"
config_root="${GONGKAO_CONFIG_ROOT:-/etc/gongkao-dazi}"
incoming="${root}/incoming/anan-${release_id}.tar.gz"
target="${root}/${release_id}"
previous_target="$(readlink -f "${current_link}" 2>/dev/null || true)"
switched=false
previous_compatible_with_candidate=false
previous_guard_capable=false
release_retention_count="${RELEASE_RETENTION_COUNT:-3}"
control_plane_backup=""
control_plane_changed=false
control_plane_restore_failed=false
control_plane_destinations=()
activation_committed=false
activation_marker_created=false
eligibility_marker_created=false
success_activation_marker=""
success_eligibility_marker=""
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
if [[ ! "${release_retention_count}" =~ ^[0-9]+$ ]] \
  || (( release_retention_count < 2 )); then
  printf '%s\n' "RELEASE_RETENTION_COUNT must be an integer of at least 2" >&2
  exit 2
fi
: "${EXPECTED_DEPLOYMENT_ENVIRONMENT:?EXPECTED_DEPLOYMENT_ENVIRONMENT is required}"
: "${EXPECTED_ROLLBACK_RELEASE_ID:?EXPECTED_ROLLBACK_RELEASE_ID is required}"
: "${EXPECTED_GIT_COMMIT:?EXPECTED_GIT_COMMIT is required}"
: "${EXPECTED_ROLLBACK_GIT_COMMIT:?EXPECTED_ROLLBACK_GIT_COMMIT is required}"
: "${EXPECTED_VERIFIER_SHA256:?EXPECTED_VERIFIER_SHA256 is required}"
: "${RELEASE_TRUST_PUBLIC_KEY:?RELEASE_TRUST_PUBLIC_KEY is required}"
: "${DATA_RELEASE_TRUST_PUBLIC_KEY:?DATA_RELEASE_TRUST_PUBLIC_KEY is required}"
: "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY:?WECHAT_UPLOADER_TRUST_PUBLIC_KEY is required}"
case "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "expected deployment environment is invalid" >&2
    exit 2
    ;;
esac
case "${EXPECTED_ROLLBACK_RELEASE_ID}" in
  *[!A-Za-z0-9._-]*|'')
    printf '%s\n' "expected rollback release id is invalid" >&2
    exit 2
    ;;
esac
for expected_commit in \
  "${EXPECTED_GIT_COMMIT}" \
  "${EXPECTED_ROLLBACK_GIT_COMMIT}"; do
  if [[ ! "${expected_commit}" =~ ^[0-9a-f]{40,64}$ ]]; then
    printf '%s\n' "expected application commit is invalid" >&2
    exit 2
  fi
done
if [[ ! "${EXPECTED_VERIFIER_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  printf '%s\n' "expected release verifier digest is invalid" >&2
  exit 2
fi
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
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
with open(sys.argv[2], "rb") as handle:
    encoded_marker = handle.read()
marker = json.loads(encoded_marker)
expected_marker = {
    "schemaVersion": "anan-release-capability/v1",
    "capability": "commercial-data-publication-guard",
    "contractVersion": "commercial-data-publication-guard@v1",
}
marker_digest = sha256(encoded_marker).hexdigest()
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
    or re.fullmatch(r"[0-9a-f]{64}", marker_digest) is None
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

validate_backup_environment_authority_paths() {
  local path="${config_root}/backup.env"
  [[ -e "${path}" ]] || return 0
  assert_root_owned_file "${path}"
  python3 - "${path}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
assignments: dict[str, str] = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit("backup.env contains an invalid assignment")
    key, value = line.split("=", 1)
    if key in assignments:
        raise SystemExit(f"backup.env duplicates {key}")
    assignments[key] = value.strip().strip("\"'")
if "BACKUP_TARGET_ID" in assignments:
    raise SystemExit("BACKUP_TARGET_ID is forbidden")
if "REQUIRED_DATABASE_REVISION" in assignments:
    raise SystemExit(
        "backup.env must not define REQUIRED_DATABASE_REVISION; "
        "backup derives it from the current release artifact"
    )
expected = {
    "BACKUP_TARGET_AUTHORITY_FILE": "/etc/gongkao-dazi/backup-target-authority.json",
    "PGPASSFILE": "/etc/gongkao-dazi/secrets/backup.pgpass",
    "BACKUP_SIGNING_PRIVATE_KEY_FILE": "/etc/gongkao-dazi/secrets/backup-signing.pem",
    "BACKUP_DIRECTORY": "/var/backups/gongkao-dazi/encrypted",
    "BACKUP_METRICS_FILE": "/var/lib/gongkao-backup/metrics/anan-backup.prom",
}
for key, value in expected.items():
    if assignments.get(key) != value:
        raise SystemExit(f"backup.env {key} violates the systemd isolation contract")
PY
}

ensure_isolated_runtime_principals() {
  local release_root="$1"
  local sysusers_contract="${release_root}/ops/sysusers.d/gongkao-dazi-workers.conf"
  local tmpfiles_contract="${release_root}/ops/tmpfiles.d/gongkao-dazi-backup.conf"
  local api_uid
  local entry
  local gid
  local shell
  local uid
  local user
  local seen_uids=" "
  test -f "${sysusers_contract}"
  test ! -L "${sysusers_contract}"
  assert_root_owned_file "${sysusers_contract}"
  test -f "${tmpfiles_contract}"
  test ! -L "${tmpfiles_contract}"
  assert_root_owned_file "${tmpfiles_contract}"
  assert_root_owned_file "${config_root}/db-ca.pem"
  command -v systemd-sysusers >/dev/null
  systemd-sysusers "${sysusers_contract}"
  command -v systemd-tmpfiles >/dev/null
  systemd-tmpfiles --create "${tmpfiles_contract}"
  api_uid="$(id -u deploy)"
  for user in \
    gongkao-wkr-data \
    gongkao-wkr-cause \
    gongkao-wkr-external \
    gongkao-wkr-essay \
    gongkao-wkr-coach \
    gongkao-wkr-ilink \
    gongkao-backup; do
    entry="$(getent passwd "${user}")"
    IFS=: read -r _ _ uid gid _ _ shell <<<"${entry}"
    if [[ ! "${uid}" =~ ^[0-9]+$ \
      || ! "${gid}" =~ ^[0-9]+$ \
      || "${uid}" == "${api_uid}" \
      || "${seen_uids}" == *" ${uid} "* \
      || "${shell}" != */nologin \
      || "$(id -gn "${user}")" != "${user}" ]]; then
      printf '%s\n' "runtime principal is not isolated: ${user}" >&2
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
    RELEASE_APP_GIT_COMMIT="${EXPECTED_GIT_COMMIT}" \
    RELEASE_DATA_RECEIPT_SHA256="${incoming_data_receipt_sha256}" \
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
  "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
  "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}"; do
  if [[ ! "${trust_key}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    printf '%s\n' "release trust key must be a safe absolute path" >&2
    exit 2
  fi
  assert_root_owned_file "${trust_key}"
done
actual_previous_id="$(basename "${previous_target}")"
if [[ -z "${previous_target}" \
  || ! -d "${previous_target}" \
  || "${actual_previous_id}" != "${EXPECTED_ROLLBACK_RELEASE_ID}" ]]; then
  printf '%s\n' "current release does not match the expected rollback identity" >&2
  exit 1
fi
case "${previous_target}" in
  "${root}/"*) ;;
  *)
    printf '%s\n' "current release escapes the trusted release root" >&2
    exit 1
    ;;
esac
previous_manifest="${previous_target}/release-authority/release.json"
previous_manifest_signature="${previous_manifest}.sig"
assert_root_owned_file "${previous_manifest}"
assert_root_owned_file "${previous_manifest_signature}"
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${previous_manifest_signature}" \
  "${previous_manifest}"
python3 - \
  "${previous_manifest}" \
  "${EXPECTED_ROLLBACK_RELEASE_ID}" \
  "${EXPECTED_ROLLBACK_GIT_COMMIT}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    authority = json.load(handle)
if (
    authority.get("releaseId") != sys.argv[2]
    or authority.get("source", {}).get("gitCommit") != sys.argv[3]
    or authority.get("deployment", {}).get("environment") != sys.argv[4]
):
    raise SystemExit("current application release authority is invalid")
PY
assert_guard_capable_release "${previous_manifest}" "${previous_target}"
previous_guard_capable=true
previous_manifest_sha256="$(
  shasum -a 256 "${previous_manifest}" | awk '{print $1}'
)"
environment_file="${config_root}/release-environment"
assert_root_owned_file "${environment_file}"
actual_environment="$(tr -d '\r\n' < "${environment_file}")"
if [[ "${actual_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
  printf '%s\n' "target environment changed after manifest verification" >&2
  exit 1
fi
export APP_ENV="${EXPECTED_DEPLOYMENT_ENVIRONMENT}"
export APP_GIT_COMMIT="${EXPECTED_GIT_COMMIT}"
release_manifest="${root}/incoming/anan-${release_id}.release.json"
release_manifest_signature="${release_manifest}.sig"
release_verifier="${root}/incoming/anan-${release_id}.verifier.py"
wechat_upload_receipt="${root}/incoming/anan-${release_id}.wechat-upload-receipt.json"
wechat_upload_signature="${wechat_upload_receipt}.sig"
wechat_upload_info="${root}/incoming/anan-${release_id}.wechat-upload-info.json"
wechat_upload_raw_log="${root}/incoming/anan-${release_id}.wechat-upload.log"
data_release_receipt="${root}/incoming/anan-${release_id}.data-release-receipt.json"
data_release_signature="${data_release_receipt}.sig"
for incoming_file in \
  "${incoming}" \
  "${incoming}.sha256" \
  "${release_manifest}" \
  "${release_manifest}.sha256" \
  "${release_manifest_signature}" \
  "${release_verifier}" \
  "${wechat_upload_receipt}" \
  "${wechat_upload_signature}" \
  "${wechat_upload_info}" \
  "${wechat_upload_raw_log}" \
  "${data_release_receipt}" \
  "${data_release_signature}"; do
  assert_root_owned_file "${incoming_file}"
done
incoming_data_receipt_sha256="$(
  shasum -a 256 "${data_release_receipt}" | awk '{print $1}'
)"
export APP_DATA_RECEIPT_SHA256="${incoming_data_receipt_sha256}"

verify_incoming_release_bundle() {
  printf '%s  %s\n' \
    "${EXPECTED_VERIFIER_SHA256}" \
    "${release_verifier}" | shasum -a 256 -c -
  openssl dgst -sha256 \
    -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${release_manifest_signature}" \
    "${release_manifest}"
  openssl dgst -sha256 \
    -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${data_release_signature}" \
    "${data_release_receipt}"
  openssl dgst -sha256 \
    -verify "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}" \
    -signature "${wechat_upload_signature}" \
    "${wechat_upload_receipt}"
  python3 "${release_verifier}" verify \
    --manifest "${release_manifest}" \
    --artifact "${incoming}" \
    --expected-commit "${EXPECTED_GIT_COMMIT}" \
    --expected-release-id "${release_id}" \
    --expected-environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-rollback-release-id "${EXPECTED_ROLLBACK_RELEASE_ID}" \
    --expected-compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --expected-compatible-commit "${EXPECTED_ROLLBACK_GIT_COMMIT}" \
    --wechat-upload-receipt "${wechat_upload_receipt}" \
    --wechat-upload-receipt-signature "${wechat_upload_signature}" \
    --wechat-upload-info-output "${wechat_upload_info}" \
    --wechat-upload-raw-log "${wechat_upload_raw_log}" \
    --wechat-uploader-trust-public-key \
      "${WECHAT_UPLOADER_TRUST_PUBLIC_KEY}" \
    --data-release-receipt "${data_release_receipt}" \
    --require-deployable
}

verify_incoming_release_bundle
python3 - \
  "${release_manifest}" \
  "${release_id}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
  "${EXPECTED_ROLLBACK_RELEASE_ID}" \
  "${EXPECTED_GIT_COMMIT}" \
  "${EXPECTED_ROLLBACK_GIT_COMMIT}" \
  "${data_release_receipt}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    authority = json.load(handle)
deployment = authority.get("deployment")
data_authority = authority.get("dataAuthorities", {}).get("receipt", {})
with open(sys.argv[7], encoding="utf-8") as handle:
    detached_data_authority = json.load(handle)
compatible_commits = detached_data_authority.get(
    "compatibleApplicationCommits",
)
if (
    authority.get("releaseId") != sys.argv[2]
    or not isinstance(deployment, dict)
    or deployment.get("environment") != sys.argv[3]
    or deployment.get("rollbackReleaseId") != sys.argv[4]
    or authority.get("source", {}).get("gitCommit") != sys.argv[5]
    or data_authority.get("authority") != detached_data_authority
    or not isinstance(compatible_commits, list)
    or sys.argv[5] not in compatible_commits
    or sys.argv[6] not in compatible_commits
):
    raise SystemExit("incoming release authority is invalid")
PY

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
  global_data_receipt="${resolved}/receipt.json"
  global_data_signature="${resolved}/receipt.json.sig"
  for authority_file in \
    "${global_data_receipt}" \
    "${global_data_signature}"; do
    test -f "${authority_file}"
    test ! -L "${authority_file}"
    test "$(stat -c '%u' "${authority_file}")" = 0
    test -z "$(find "${authority_file}" -perm /022 -print -quit)"
  done
  openssl dgst -sha256 \
    -verify "${DATA_RELEASE_TRUST_PUBLIC_KEY}" \
    -signature "${global_data_signature}" \
    "${global_data_receipt}"
}

validate_backup_unit_contract() {
  local service="$1"
  local timer="$2"
  local expected_entrypoint="$3"
  local expected_environment="$4"
  python3 - \
    "${service}" \
    "${timer}" \
    "${expected_entrypoint}" \
    "${expected_environment}" <<'PY'
from pathlib import Path
import sys

service_path = Path(sys.argv[1])
timer_path = Path(sys.argv[2])
expected_entrypoint = sys.argv[3]
expected_environment = sys.argv[4]


def load_unit(path: Path) -> dict[tuple[str, str], list[str]]:
    section: str | None = None
    properties: dict[tuple[str, str], list[str]] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if not section:
                raise SystemExit(f"{path}: empty section at line {number}")
            continue
        if section is None or "=" not in line:
            raise SystemExit(f"{path}: invalid unit line {number}")
        key, value = line.split("=", 1)
        if not key:
            raise SystemExit(f"{path}: empty property at line {number}")
        properties.setdefault((section, key), []).append(value)
    return properties


def require_one(
    path: Path,
    properties: dict[tuple[str, str], list[str]],
    section: str,
    key: str,
) -> str:
    values = properties.get((section, key), [])
    if len(values) != 1:
        raise SystemExit(f"{path}: expected exactly one {section}.{key}")
    return values[0]


service = load_unit(service_path)
service_expectations = {
    ("Unit", "After"): "postgresql.service network-online.target",
    ("Service", "Type"): "oneshot",
    ("Service", "User"): "gongkao-backup",
    ("Service", "Group"): "gongkao-backup",
    ("Service", "EnvironmentFile"): expected_environment,
    ("Service", "ExecStart"): expected_entrypoint,
    ("Service", "NoNewPrivileges"): "true",
    ("Service", "PrivateTmp"): "true",
    ("Service", "PrivateDevices"): "true",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "true",
    ("Service", "ProtectControlGroups"): "true",
    ("Service", "ProtectKernelModules"): "true",
    ("Service", "ProtectKernelTunables"): "true",
    ("Service", "ProtectKernelLogs"): "true",
    ("Service", "ProtectClock"): "true",
    ("Service", "ProtectHostname"): "true",
    ("Service", "ProtectProc"): "invisible",
    ("Service", "ProcSubset"): "pid",
    ("Service", "RestrictNamespaces"): "true",
    ("Service", "RestrictRealtime"): "true",
    ("Service", "RestrictSUIDSGID"): "true",
    ("Service", "LockPersonality"): "true",
    ("Service", "RemoveIPC"): "true",
    ("Service", "CapabilityBoundingSet"): "",
    ("Service", "AmbientCapabilities"): "",
    ("Service", "RestrictAddressFamilies"): "AF_UNIX AF_INET AF_INET6",
    ("Service", "SystemCallArchitectures"): "native",
    ("Service", "UMask"): "0077",
}
for (section, key), expected in service_expectations.items():
    actual = require_one(service_path, service, section, key)
    if actual != expected:
        raise SystemExit(
            f"{service_path}: invalid {section}.{key} backup contract"
        )
service_path_expectations = {
    ("Service", "ReadOnlyPaths"): [
        "/etc/gongkao-dazi/backup-target-authority.json",
        "/etc/gongkao-dazi/secrets/backup.pgpass",
        "/etc/gongkao-dazi/secrets/backup-signing.pem",
    ],
    ("Service", "ReadWritePaths"): [
        "/var/backups/gongkao-dazi/encrypted",
        "/var/lib/gongkao-backup/metrics",
    ],
    ("Service", "InaccessiblePaths"): [
        "-/etc/gongkao-dazi/api.env",
        "-/etc/gongkao-dazi/db.env",
    ],
}
for (section, key), expected in service_path_expectations.items():
    actual = service.get((section, key), [])
    if actual != expected:
        raise SystemExit(
            f"{service_path}: invalid {section}.{key} backup sandbox contract"
        )

timer = load_unit(timer_path)
timer_expectations = {
    ("Timer", "Unit"): "gongkao-dazi-backup.service",
    ("Timer", "OnCalendar"): "*-*-* 00/6:15:00",
    ("Timer", "Persistent"): "true",
    ("Timer", "RandomizedDelaySec"): "300",
    ("Install", "WantedBy"): "timers.target",
}
for (section, key), expected in timer_expectations.items():
    actual = require_one(timer_path, timer, section, key)
    if actual != expected:
        raise SystemExit(f"{timer_path}: invalid {section}.{key} backup contract")
PY
}

assert_no_packaged_backup_dropins() {
  local unit_root="$1"
  local unit
  for unit in gongkao-dazi-backup.service gongkao-dazi-backup.timer; do
    if [[ -e "${unit_root}/${unit}.d" \
      || -L "${unit_root}/${unit}.d" ]]; then
      printf '%s\n' "release backup units must not contain drop-ins" >&2
      return 1
    fi
  done
}

assert_no_effective_backup_dropins() {
  local unit
  local drop_ins
  for unit in gongkao-dazi-backup.service gongkao-dazi-backup.timer; do
    if [[ -e "${systemd_root}/${unit}.d" \
      || -L "${systemd_root}/${unit}.d" ]]; then
      printf '%s\n' "installed backup units must not contain drop-ins" >&2
      return 1
    fi
    drop_ins="$(
      systemctl show \
        --property=DropInPaths \
        --value \
        "${unit}"
    )"
    if [[ -n "${drop_ins}" ]]; then
      printf '%s\n' \
        "effective backup unit ${unit} contains forbidden drop-ins" \
        >&2
      return 1
    fi
  done
}

assert_release_backup_control_plane() {
  local release_root="$1"
  local service="${release_root}/ops/systemd/gongkao-dazi-backup.service"
  local timer="${release_root}/ops/systemd/gongkao-dazi-backup.timer"
  local entrypoint="${release_root}/ops/scripts/backup.sh"
  for path in "${service}" "${timer}" "${entrypoint}"; do
    assert_root_owned_file "${path}"
  done
  test -x "${entrypoint}"
  /bin/bash -n "${entrypoint}"
  assert_no_packaged_backup_dropins "${release_root}/ops/systemd"
  validate_backup_unit_contract \
    "${service}" \
    "${timer}" \
    "${current_link}/ops/scripts/backup.sh" \
    "/etc/gongkao-dazi/backup.env"
  assert_no_effective_backup_dropins
}

assert_installed_target_backup_control_plane() {
  local service="${systemd_root}/gongkao-dazi-backup.service"
  local timer="${systemd_root}/gongkao-dazi-backup.timer"
  local entrypoint="${target}/ops/scripts/backup.sh"
  local environment="${runtime_config_root}/backup.env"
  local resolved_current
  resolved_current="$(readlink -f "${current_link}")"
  if [[ "${resolved_current}" != "${target}" ]]; then
    printf '%s\n' \
      "installed backup controller is not bound to the activated release" \
      >&2
    return 1
  fi
  assert_sealed_release_config_snapshot "${runtime_config_root}"
  for path in "${service}" "${timer}" "${entrypoint}" "${environment}"; do
    assert_root_owned_file "${path}"
  done
  test -x "${entrypoint}"
  sudo -u gongkao-backup test -x "${entrypoint}"
  /bin/bash -n "${entrypoint}"
  validate_backup_unit_contract \
    "${service}" \
    "${timer}" \
    "${current_link}/ops/scripts/backup.sh" \
    "${environment}"
  assert_no_effective_backup_dropins
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
  for name in activate_release.sh rollback_release.sh; do
    destination="${controller_root}/${name}"
    control_plane_destinations+=("${destination}")
    if [[ -e "${destination}" || -L "${destination}" ]]; then
      cp -p -- "${destination}" "${control_plane_backup}/${name}"
    else
      : > "${control_plane_backup}/${name}.missing"
    fi
  done
}

restore_control_plane() {
  local destination
  local name
  local temporary
  local failed=false
  if [[ "${control_plane_changed}" != true || -z "${control_plane_backup}" ]]; then
    return
  fi
  for destination in "${control_plane_destinations[@]}"; do
    name="$(basename "${destination}")"
    if [[ -f "${control_plane_backup}/${name}.missing" ]]; then
      if ! rm -f -- "${destination}"; then
        failed=true
      fi
    else
      if ! temporary="$(mktemp "${destination}.restore.XXXXXX")"; then
        failed=true
        continue
      fi
      if ! cp -p -- "${control_plane_backup}/${name}" "${temporary}" \
        || ! mv -f -- "${temporary}" "${destination}"; then
        rm -f -- "${temporary}" || true
        failed=true
      fi
    fi
  done
  if ! systemctl daemon-reload; then
    failed=true
  fi
  if [[ "${failed}" == true ]]; then
    control_plane_restore_failed=true
    return 1
  fi
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

write_failed_activation_marker() {
  local marker_root="${target}/release-authority"
  local marker="${marker_root}/activation-failed.json"
  local temporary
  if [[ ! -d "${marker_root}" || -e "${marker_root}/activation.json" ]]; then
    return
  fi
  temporary="$(mktemp "${marker_root}/.activation-failed.XXXXXX")"
  python3 - \
    "${release_id}" \
    "${EXPECTED_GIT_COMMIT}" \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" > "${temporary}" <<'PY'
import datetime
import json
import sys

json.dump(
    {
        "schemaVersion": "anan-application-activation/v1",
        "status": "failed",
        "releaseId": sys.argv[1],
        "gitCommit": sys.argv[2],
        "environment": sys.argv[3],
        "recordedAt": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    },
    sys.stdout,
    sort_keys=True,
)
sys.stdout.write("\n")
PY
  chmod 0444 "${temporary}"
  if [[ ! -e "${marker}" ]]; then
    mv "${temporary}" "${marker}"
  else
    rm -f -- "${temporary}"
  fi
}

write_success_activation_markers() {
  local data_sha256
  local eligibility_root
  local eligibility
  local eligibility_temporary
  local activation
  local activation_temporary
  if [[ "${previous_guard_capable}" != true ]]; then
    printf '%s\n' \
      "refusing to grant rollback eligibility to a legacy release" >&2
    return 1
  fi
  data_sha256="$(
    shasum -a 256 "${global_data_receipt}" | awk '{print $1}'
  )"
  eligibility_root="${previous_target}/release-authority/rollback-eligible"
  install -d -m 0755 "${eligibility_root}"
  eligibility="${eligibility_root}/${release_id}.json"
  success_eligibility_marker="${eligibility}"
  test ! -e "${success_eligibility_marker}"
  eligibility_temporary="$(
    mktemp "${eligibility_root}/.${release_id}.XXXXXX"
  )"
  python3 - \
    "${actual_previous_id}" \
    "${release_id}" \
    "${EXPECTED_GIT_COMMIT}" \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    "${data_sha256}" > "${eligibility_temporary}" <<'PY'
import datetime
import json
import sys

json.dump(
    {
        "schemaVersion": "anan-rollback-eligibility/v1",
        "status": "eligible",
        "targetReleaseId": sys.argv[1],
        "authorizedByReleaseId": sys.argv[2],
        "authorizedByGitCommit": sys.argv[3],
        "environment": sys.argv[4],
        "dataReceiptSha256": sys.argv[5],
        "recordedAt": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    },
    sys.stdout,
    sort_keys=True,
)
sys.stdout.write("\n")
PY
  chmod 0444 "${eligibility_temporary}"
  mv "${eligibility_temporary}" "${success_eligibility_marker}"
  eligibility_marker_created=true

  activation="${authority_root}/activation.json"
  success_activation_marker="${activation}"
  test ! -e "${success_activation_marker}"
  activation_temporary="$(mktemp "${authority_root}/.activation.XXXXXX")"
  python3 - \
    "${release_id}" \
    "${EXPECTED_GIT_COMMIT}" \
    "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    "${actual_previous_id}" \
    "${data_sha256}" > "${activation_temporary}" <<'PY'
import datetime
import json
import sys

json.dump(
    {
        "schemaVersion": "anan-application-activation/v1",
        "status": "succeeded",
        "releaseId": sys.argv[1],
        "gitCommit": sys.argv[2],
        "environment": sys.argv[3],
        "previousReleaseId": sys.argv[4],
        "dataReceiptSha256": sys.argv[5],
        "activatedAt": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    },
    sys.stdout,
    sort_keys=True,
)
sys.stdout.write("\n")
PY
  chmod 0444 "${activation_temporary}"
  mv "${activation_temporary}" "${success_activation_marker}"
  activation_marker_created=true
}

remove_provisional_success_markers() {
  if [[ "${activation_marker_created}" == true \
    && "${success_activation_marker}" == "${target}/release-authority/activation.json" ]]; then
    rm -f -- "${success_activation_marker}"
    activation_marker_created=false
  fi
  if [[ "${eligibility_marker_created}" == true \
    && "${success_eligibility_marker}" \
       == "${previous_target}/release-authority/rollback-eligible/${release_id}.json" ]]; then
    rm -f -- "${success_eligibility_marker}"
    eligibility_marker_created=false
  fi
}

disable_unit_during_recovery() {
  local unit="$1"
  if ! systemctl disable --now "${unit}"; then
    printf '%s\n' "failed to disable ${unit} during activation recovery" >&2
  fi
}

restore_previous_release() {
  local exit_code=$?
  trap - ERR
  if [[ "${activation_committed}" == true ]]; then
    printf '%s\n' \
      "activation is committed; post-commit cleanup failed without rolling back the active release" \
      >&2
    exit "${exit_code}"
  fi
  if ! remove_provisional_success_markers; then
    printf '%s\n' "failed to remove provisional activation markers" >&2
  fi
  if ! restore_control_plane; then
    printf '%s\n' \
      "failed to restore the previous control plane; recovery material is retained at ${control_plane_backup}" \
      >&2
  fi
  if [[ "${switched}" == true && -n "${previous_target}" && -d "${previous_target}" ]]; then
    if [[ "${previous_compatible_with_candidate}" != true \
      || "${previous_guard_capable}" != true ]]; then
      printf '%s\n' \
        "refusing to restore an artifact without compatibility and guard preflight" \
        >&2
      if [[ "${control_plane_restore_failed}" != true ]] \
        && ! discard_control_plane_backup; then
        printf '%s\n' "failed to discard the control-plane recovery material" >&2
      fi
      exit "${exit_code}"
    fi
    if ! assert_sealed_release_config_snapshot \
      "${root}/runtime-config/$(basename "${previous_target}")"; then
      printf '%s\n' "previous release lacks a sealed runtime configuration snapshot" >&2
      if [[ "${control_plane_restore_failed}" != true ]] \
        && ! discard_control_plane_backup; then
        printf '%s\n' "failed to discard the control-plane recovery material" >&2
      fi
      exit "${exit_code}"
    fi
    ln -sfn "${previous_target}" "${current_link}"
    runtime_identity_changed=false
    restore_units=(
      gongkao-dazi-api \
      gongkao-dazi-external-learning-worker \
      gongkao-dazi-data-rights-worker \
      gongkao-dazi-cause-agent-worker
    )
    if [[ "${essay_review_worker_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/essay_reviews.py" ]]; then
      restore_units+=(gongkao-dazi-essay-review-worker)
    else
      disable_unit_during_recovery gongkao-dazi-essay-review-worker
    fi
    if [[ "${coach_notifications_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/coach_notifications.py" ]]; then
      restore_units+=(gongkao-dazi-coach-notification-worker)
    else
      disable_unit_during_recovery gongkao-dazi-coach-notification-worker
    fi
    if [[ "${COMMERCIAL_MODE:-free_cold_start}" == "paid" \
      && -f "${previous_target}/ops/systemd/gongkao-dazi-commerce-worker.service" \
      && -n "${WECHAT_VIRTUAL_PAY_OFFER_ID:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_APP_KEY:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN:-}" \
      && -n "${WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY:-}" ]]; then
      restore_units+=(gongkao-dazi-commerce-worker)
    else
      disable_unit_during_recovery gongkao-dazi-commerce-worker
    fi
    if [[ "${ilink_channel_enabled}" == "true" \
      && -f "${previous_target}/backend/app/workers/ilink_channel.py" ]]; then
      restore_units+=(gongkao-dazi-ilink-worker)
    else
      disable_unit_during_recovery gongkao-dazi-ilink-worker
    fi
    if ! systemctl enable "${restore_units[@]}"; then
      printf '%s\n' "failed to re-enable the previous runtime units" >&2
    fi
    if ! systemctl restart "${restore_units[@]}"; then
      printf '%s\n' "failed to restart the previous runtime units" >&2
    fi
  fi
  if ! write_failed_activation_marker; then
    printf '%s\n' "failed to write the activation failure marker" >&2
  fi
  if [[ "${control_plane_restore_failed}" == true ]]; then
    printf '%s\n' \
      "control-plane recovery remains incomplete; retained ${control_plane_backup}" \
      >&2
  elif ! discard_control_plane_backup; then
    printf '%s\n' "failed to discard the control-plane recovery material" >&2
  fi
  exit "${exit_code}"
}

prune_old_releases() {
  local entry
  local reserved=1
  local extra_kept=0
  local extra_limit
  if [[ -n "${previous_target}" && "${previous_target}" != "${target}" ]]; then
    reserved=2
  fi
  extra_limit=$((release_retention_count - reserved))
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] || continue
    if [[ "${entry}" == "${target}" || "${entry}" == "${previous_target}" \
      || "${entry}" == "${root}/runtime-config" ]]; then
      continue
    fi
    if (( extra_kept < extra_limit )); then
      extra_kept=$((extra_kept + 1))
      continue
    fi
    case "${entry}" in
      "${root}"/*) rm -rf -- "${entry}" ;;
      *)
        printf '%s\n' "refusing unsafe release cleanup target ${entry}" >&2
        return 1
        ;;
    esac
  done < <(
    find "${root}" -mindepth 1 -maxdepth 1 -type d \
      ! -name incoming -print | sort -r
  )
  rm -f -- \
    "${incoming}" \
    "${incoming}.sha256" \
    "${root}/incoming/anan-${release_id}.release.json" \
    "${root}/incoming/anan-${release_id}.release.json.sha256" \
    "${root}/incoming/anan-${release_id}.release.json.sig" \
    "${root}/incoming/anan-${release_id}.verifier.py" \
    "${root}/incoming/anan-${release_id}.wechat-upload-receipt.json" \
    "${root}/incoming/anan-${release_id}.wechat-upload-receipt.json.sig" \
    "${root}/incoming/anan-${release_id}.wechat-upload-info.json" \
    "${root}/incoming/anan-${release_id}.wechat-upload.log" \
    "${root}/incoming/anan-${release_id}.data-release-receipt.json" \
    "${root}/incoming/anan-${release_id}.data-release-receipt.json.sig"
}

prune_orphaned_runtime_config_snapshots() {
  local snapshot
  local release_name
  local snapshot_store="${root}/runtime-config"
  test -d "${snapshot_store}"
  test ! -L "${snapshot_store}"
  while IFS= read -r snapshot; do
    [[ -n "${snapshot}" ]] || continue
    release_name="$(basename "${snapshot}")"
    case "${release_name}" in
      *[!A-Za-z0-9._-]*|'')
        printf '%s\n' "refusing unsafe runtime configuration snapshot ${snapshot}" >&2
        return 1
        ;;
    esac
    if [[ ! -d "${root}/${release_name}" || -L "${root}/${release_name}" ]]; then
      # Sealed snapshots are stored with a 0500 directory and 0400 files, so
      # restore owner write permission first; unprivileged removal fails
      # otherwise (for example on macOS test harnesses). BSD chmod does not
      # accept "--"; the snapshot path is absolute, so it cannot begin with
      # a dash.
      chmod -R u+w "${snapshot}"
      rm -rf -- "${snapshot}"
    fi
  done < <(find "${snapshot_store}" -mindepth 1 -maxdepth 1 -type d -print | sort)
}

trap restore_previous_release ERR

# The caller's verification and this point are separated by controller
# extraction and process startup. Re-verify the exact immutable bundle at the
# last possible point before extraction so an in-between mutation fails closed.
verify_incoming_release_bundle
cd "${root}/incoming"
shasum -a 256 -c "${incoming}.sha256"
test ! -e "${target}"
mkdir -p "${target}"
tar -xzf "${incoming}" -C "${target}"
assert_guard_capable_release "${release_manifest}" "${target}"
assert_release_backup_control_plane "${target}"
authority_root="${target}/release-authority"
mkdir -p "${authority_root}"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.release.json" \
  "${authority_root}/release.json"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.release.json.sig" \
  "${authority_root}/release.json.sig"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.wechat-upload-receipt.json" \
  "${authority_root}/wechat-upload-receipt.json"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.wechat-upload-receipt.json.sig" \
  "${authority_root}/wechat-upload-receipt.json.sig"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.wechat-upload-info.json" \
  "${authority_root}/wechat-upload-info.json"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.wechat-upload.log" \
  "${authority_root}/wechat-upload.log"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.data-release-receipt.json" \
  "${authority_root}/data-release-receipt.json"
install -m 0444 \
  "${root}/incoming/anan-${release_id}.data-release-receipt.json.sig" \
  "${authority_root}/data-release-receipt.json.sig"
cd "${target}/backend"
uv_binary="${GONGKAO_UV_BINARY:-uv}"
command -v "${uv_binary}" >/dev/null
UV_NO_PROGRESS=1 "${uv_binary}" sync \
  --frozen \
  --no-dev \
  --no-editable \
  --project "${target}/backend"
validate_backup_environment_authority_paths
ensure_isolated_runtime_principals "${target}"
validate_worker_workload_environments
create_release_config_snapshot
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
preflight_worker_configurations
# The signed release identity wins over any stale identity keys left in the
# general-purpose environment files.
export APP_ENV="${EXPECTED_DEPLOYMENT_ENVIRONMENT}"
export APP_GIT_COMMIT="${EXPECTED_GIT_COMMIT}"
export APP_DATA_RECEIPT_SHA256="${incoming_data_receipt_sha256}"
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
: "${MIGRATION_PGPASSFILE:?MIGRATION_PGPASSFILE is required}"
: "${REQUIRED_DATABASE_REVISION:?REQUIRED_DATABASE_REVISION is required}"
: "${WEKNORA_PRIVATE_KNOWLEDGE_MODE:?WEKNORA_PRIVATE_KNOWLEDGE_MODE must be explicit}"
: "${TOS_PRESIGN_ENDPOINT:?TOS_PRESIGN_ENDPOINT is required}"
migration_database_os_user="${MIGRATION_DATABASE_OS_USER:-postgres}"
test -f "${MIGRATION_PGPASSFILE}"
test ! -L "${MIGRATION_PGPASSFILE}"
test "$(stat -c '%a' "${MIGRATION_PGPASSFILE}")" = 600
test "$(stat -c '%u' "${MIGRATION_PGPASSFILE}")" = "$(
  id -u "${migration_database_os_user}"
)"
"${target}/backend/.venv/bin/python" \
  "${target}/ops/scripts/probe_tos_public_presign.py"
artifact_contract="${target}/ops/release-db-compatibility.env"
artifact_revision="$(
  "${target}/backend/.venv/bin/python" \
    -m app.core.database_compatibility head \
    --backend-root "${target}/backend"
)"
"${target}/backend/.venv/bin/python" \
  -m app.core.database_compatibility compatible-revisions \
  --backend-root "${target}/backend" \
  --contract "${artifact_contract}" >/dev/null
if [[ "${REQUIRED_DATABASE_REVISION}" != "${artifact_revision}" ]]; then
  printf '%s\n' \
    "REQUIRED_DATABASE_REVISION must match release artifact head ${artifact_revision}" \
    >&2
  exit 1
fi
if [[ -n "${previous_target}" && -d "${previous_target}" ]]; then
  previous_contract="${previous_target}/ops/release-db-compatibility.env"
  "${target}/backend/.venv/bin/python" \
    -m app.core.database_compatibility assert-revision \
    --backend-root "${previous_target}/backend" \
    --contract "${previous_contract}" \
    --revision "${artifact_revision}" >/dev/null
  previous_compatible_with_candidate=true
fi
case "${WEKNORA_PRIVATE_KNOWLEDGE_MODE}" in
  canonical|weknora) ;;
  *)
    printf '%s\n' "WEKNORA_PRIVATE_KNOWLEDGE_MODE must be canonical or weknora" >&2
    exit 1
    ;;
esac
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
locked_actual_previous_id="$(basename "${locked_previous_target}")"
if [[ "${locked_actual_previous_id}" != "${EXPECTED_ROLLBACK_RELEASE_ID}" ]]; then
  printf '%s\n' "locked current release identity is unexpected" >&2
  exit 1
fi
locked_previous_manifest="${locked_previous_target}/release-authority/release.json"
locked_previous_manifest_signature="${locked_previous_manifest}.sig"
assert_root_owned_file "${locked_previous_manifest}"
assert_root_owned_file "${locked_previous_manifest_signature}"
locked_previous_manifest_sha256="$(
  shasum -a 256 "${locked_previous_manifest}" | awk '{print $1}'
)"
if [[ "${locked_previous_manifest_sha256}" != "${previous_manifest_sha256}" ]]; then
  printf '%s\n' \
    "current release manifest changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
openssl dgst -sha256 \
  -verify "${RELEASE_TRUST_PUBLIC_KEY}" \
  -signature "${locked_previous_manifest_signature}" \
  "${locked_previous_manifest}"
python3 - \
  "${locked_previous_manifest}" \
  "${EXPECTED_ROLLBACK_RELEASE_ID}" \
  "${EXPECTED_ROLLBACK_GIT_COMMIT}" \
  "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    authority = json.load(handle)
if (
    authority.get("releaseId") != sys.argv[2]
    or authority.get("source", {}).get("gitCommit") != sys.argv[3]
    or authority.get("deployment", {}).get("environment") != sys.argv[4]
):
    raise SystemExit("locked current application release authority is invalid")
PY
assert_guard_capable_release \
  "${locked_previous_manifest}" \
  "${locked_previous_target}"
previous_guard_capable=true
assert_root_owned_file "${environment_file}"
locked_environment="$(tr -d '\r\n' < "${environment_file}")"
if [[ "${locked_environment}" != "${actual_environment}" \
  || "${locked_environment}" != "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" ]]; then
  printf '%s\n' \
    "target environment changed while waiting for the data release coordination lock" \
    >&2
  exit 1
fi
locked_previous_contract="${locked_previous_target}/ops/release-db-compatibility.env"
"${target}/backend/.venv/bin/python" \
  -m app.core.database_compatibility assert-revision \
  --backend-root "${locked_previous_target}/backend" \
  --contract "${locked_previous_contract}" \
  --revision "${artifact_revision}" >/dev/null
previous_compatible_with_candidate=true
resolve_current_data_authority
if ! cmp -s \
  "${authority_root}/data-release-receipt.json" \
  "${global_data_receipt}"; then
  printf '%s\n' \
    "candidate data receipt is not the current global data authority" >&2
  exit 1
fi
global_data_receipt_sha256="$(
  shasum -a 256 "${global_data_receipt}" | awk '{print $1}'
)"
if [[ "${global_data_receipt_sha256}" \
  != "${incoming_data_receipt_sha256}" ]]; then
  printf '%s\n' "global data receipt changed after incoming verification" >&2
  exit 1
fi
export APP_DATA_RECEIPT_SHA256="${global_data_receipt_sha256}"
current_revision="$(
  sudo -u "${migration_database_os_user}" \
    env \
      DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${target}/backend/.venv/bin/python" \
      -m app.core.database_compatibility assert-current \
      --backend-root "${target}/backend" \
      --contract "${artifact_contract}"
)"
if [[ "${current_revision}" != "${artifact_revision}" ]]; then
  printf '%s\n' "database revision does not match release requirement" >&2
  exit 1
fi
sudo -u "${migration_database_os_user}" \
  env -i \
    MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
    PGPASSFILE="${MIGRATION_PGPASSFILE}" \
  "${target}/backend/.venv/bin/python" \
    "${target}/ops/scripts/commercial_data_publication.py" \
    assert-pairing \
    --receipt-sha256 "${global_data_receipt_sha256}" \
    --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
    --expected-revision "${artifact_revision}" \
    --compatible-commit "${EXPECTED_GIT_COMMIT}" \
    --compatible-commit "${EXPECTED_ROLLBACK_GIT_COMMIT}" >/dev/null
data_release_verification="$(
  sudo -u "${migration_database_os_user}" \
    env \
      MIGRATION_DATABASE_URL="${MIGRATION_DATABASE_URL}" \
      PGPASSFILE="${MIGRATION_PGPASSFILE}" \
    "${target}/backend/.venv/bin/python" \
      "${target}/ops/scripts/data_release_authority.py" \
      --environment "${EXPECTED_DEPLOYMENT_ENVIRONMENT}" \
      --expected-revision "${artifact_revision}" \
      --compatible-commit "${EXPECTED_GIT_COMMIT}" \
      --compatible-commit "${EXPECTED_ROLLBACK_GIT_COMMIT}" \
      verify \
      --receipt "${global_data_receipt}"
)"
printf '%s\n' "${data_release_verification}" \
  > "${authority_root}/data-release-verification.json"
chmod 0444 "${authority_root}/data-release-verification.json"

runtime_units=(
  gongkao-dazi-api
  gongkao-dazi-external-learning-worker
  gongkao-dazi-data-rights-worker
  gongkao-dazi-cause-agent-worker
)
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
if [[ "${essay_review_worker_enabled}" == "true" ]]; then
  runtime_units+=(gongkao-dazi-essay-review-worker)
else
  disable_essay_review=true
fi

case "${ilink_channel_enabled}" in
  true)
    runtime_units+=(gongkao-dazi-ilink-worker)
    ;;
  false)
    disable_ilink=true
    ;;
esac

write_runtime_identity \
  "${EXPECTED_GIT_COMMIT}" \
  "${global_data_receipt_sha256}"
assert_sealed_release_config_snapshot "${runtime_config_root}"
prepare_control_plane_backup
control_plane_changed=true
install_release_units "${target}"
install -D -m 0750 \
  "${target}/ops/scripts/activate_release.sh" \
  "${controller_root}/activate_release.sh"
install -D -m 0750 \
  "${target}/ops/scripts/rollback_release.sh" \
  "${controller_root}/rollback_release.sh"
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
if [[ "$ready" != true ]]; then
  systemctl --no-pager --full status gongkao-dazi-api >&2 || true
  false
fi
for unit in "${runtime_units[@]}"; do
  systemctl is-active --quiet "${unit}"
done
assert_installed_target_backup_control_plane
write_success_activation_markers
discard_control_plane_backup
control_plane_changed=false
activation_committed=true
prune_old_releases
prune_orphaned_runtime_config_snapshots
trap - ERR
printf '%s\n' "activated release ${release_id} at database revision ${current_revision}"
