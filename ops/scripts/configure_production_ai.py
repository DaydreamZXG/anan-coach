#!/usr/bin/env python3
"""Configure production text AI without persisting or printing the API secret."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import stat
from tempfile import NamedTemporaryFile


AI_SETTINGS = {
    "AI_PROVIDER_BASE_URL": "https://tokenrhythm.studio/v1",
    "AI_MODEL": "deepseek-v4-flash",
    "AI_SMALL_MODEL": "deepseek-v4-flash",
    "AI_HIGH_CAPABILITY_MODEL": "deepseek-v4-flash",
    "AI_COMPANION_TRANSPORT": "openai_compatible",
    "AI_COMPANION_PROVIDER_BASE_URL": "https://tokenrhythm.studio/v1",
    "AI_COMPANION_MODEL": "deepseek-v4-flash",
    "AI_COMPANION_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash",
    "AI_COMPANION_PROVIDER_HTTPS_HOST_ALLOWLIST": "tokenrhythm.studio",
    "AI_COMPANION_PROVIDER_DPA_CONFIRMED": "false",
    "AI_COMPANION_PROVIDER_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_COMPANION_PROVIDER_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_COMPANION_PROVIDER_REQUEST_ID_HEADER": "body:id",
    "AI_COMPANION_PROVIDER_REPLAY_RETENTION_SECONDS": "0",
    "AI_COMPANION_MAX_INPUT_BYTES": "1048576",
    "AI_COMPANION_MAX_OUTPUT_BYTES": "262144",
    "AI_XINGCE_CAUSE_MODEL": "deepseek-v4-flash",
    "AI_XINGCE_CAUSE_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash",
    "AI_XINGCE_CAUSE_PROVIDER_HTTPS_HOST_ALLOWLIST": "tokenrhythm.studio",
    "AI_XINGCE_CAUSE_PROVIDER_DPA_CONFIRMED": "false",
    "AI_XINGCE_CAUSE_PROVIDER_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_XINGCE_CAUSE_PROVIDER_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_XINGCE_CAUSE_PROVIDER_REQUEST_ID_HEADER": "body:id",
    "AI_XINGCE_CAUSE_PROVIDER_REPLAY_RETENTION_SECONDS": "0",
    "AI_XINGCE_CAUSE_MAX_INPUT_BYTES": "1048576",
    "AI_XINGCE_CAUSE_MAX_OUTPUT_BYTES": "262144",
    "AI_COMMUNITY_CANDIDATE_MODEL": "deepseek-v4-flash",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_HTTPS_HOST_ALLOWLIST": "tokenrhythm.studio",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_DPA_CONFIRMED": "false",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_REQUEST_ID_HEADER": "body:id",
    "AI_COMMUNITY_CANDIDATE_PROVIDER_REPLAY_RETENTION_SECONDS": "0",
    "AI_COMMUNITY_CANDIDATE_MAX_INPUT_BYTES": "1048576",
    "AI_COMMUNITY_CANDIDATE_MAX_OUTPUT_BYTES": "262144",
    "AI_ESSAY_PRIMARY_BASE_URL": "https://gemini-web2api.ergouapi.co/v1",
    "AI_ESSAY_REVIEW_MODEL": "gemini-3.5-flash",
    "AI_ESSAY_RUBRIC_GENERATOR_MODEL": "deepseek-v4-flash",
    "AI_ESSAY_RUBRIC_CRITIC_MODEL": "deepseek-v4-flash",
    "AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash",
    "AI_ESSAY_RUBRIC_MAX_INPUT_BYTES": "8388608",
    "AI_ESSAY_RUBRIC_MAX_OUTPUT_BYTES": "2097152",
    "AI_ESSAY_REVIEW_PROVIDER_RELEASE_ID": "gemini-web2api-review-2026-07-18.1",
    "AI_ESSAY_REVIEW_PROVIDER_HTTPS_HOST_ALLOWLIST": "gemini-web2api.ergouapi.co",
    "AI_ESSAY_REVIEW_PROVIDER_DPA_CONFIRMED": "false",
    "AI_ESSAY_REVIEW_PROVIDER_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_ESSAY_REVIEW_PROVIDER_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_ESSAY_REVIEW_PROVIDER_REQUEST_ID_HEADER": "body:id",
    "AI_ESSAY_REVIEW_PROVIDER_REPLAY_RETENTION_SECONDS": "0",
    "AI_ESSAY_REVIEW_MAX_INPUT_BYTES": "8388608",
    "AI_ESSAY_REVIEW_MAX_OUTPUT_BYTES": "2097152",
    "AI_ESSAY_GRADING_PLAN_RELEASE_ID": "essay-grading-3model-2026-08-08.1",
    "AI_ESSAY_TOKENRHYTHM_BASE_URL": "https://tokenrhythm.studio/v1",
    "AI_ESSAY_TOKENRHYTHM_MODEL": "deepseek-v4-flash",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_HTTPS_HOST_ALLOWLIST": "tokenrhythm.studio",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_DPA_CONFIRMED": "false",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_REQUEST_ID_HEADER": "body:id",
    "AI_ESSAY_TOKENRHYTHM_PROVIDER_REPLAY_RETENTION_SECONDS": "0",
    "AI_ESSAY_TOKENRHYTHM_MAX_INPUT_BYTES": "8388608",
    "AI_ESSAY_TOKENRHYTHM_MAX_OUTPUT_BYTES": "2097152",
    "AI_ESSAY_ADJUDICATOR_BASE_URL": "https://tokenrhythm.studio/v1",
    "AI_ESSAY_ADJUDICATOR_PROVIDER_RELEASE_ID": "tokenrhythm-deepseek-v4-flash-adjudicator",
    "AI_ESSAY_ADJUDICATOR_MODEL": "deepseek-v4-flash",
    "AI_ESSAY_ADJUDICATOR_IDEMPOTENCY_KEY_HEADER": "Idempotency-Key",
    "AI_ESSAY_ADJUDICATOR_REQUEST_ID_HEADER": "body:id",
    "AI_ESSAY_ADJUDICATOR_IDEMPOTENCY_CAPABILITY": "nonreplayable_audited",
    "AI_ESSAY_ADJUDICATOR_MAX_INPUT_BYTES": "8388608",
    "AI_ESSAY_ADJUDICATOR_MAX_OUTPUT_BYTES": "2097152",
    "CLOUDBASE_HUNYUAN_FUNCTION_NAME": "hunyuan-essay-grader",
    "CLOUDBASE_HUNYUAN_RELEASE_ID": "hunyuan-cloud-function-2026-07-18.1",
    "CLOUDBASE_HUNYUAN_MODEL": "hunyuan-2.0-instruct-20251111",
    "CLOUDBASE_HUNYUAN_MAX_INPUT_BYTES": "196608",
    "CLOUDBASE_HUNYUAN_MAX_OUTPUT_BYTES": "262144",
    "ESSAY_REVIEW_WORKER_ENABLED": "true",
    "AI_PRICE_VERSION": "tokenrhythm-deepseek-v4-flash-provisional",
    "AI_INPUT_CNY_PER_MILLION_TOKENS": "0.01",
    "AI_CACHED_INPUT_CNY_PER_MILLION_TOKENS": "0.01",
    "AI_OUTPUT_CNY_PER_MILLION_TOKENS": "0.01",
}

_ESSAY_PRICE_CARD = {
    "version": "operator-provisional-2026-08-08",
    "inputCnyPerMillion": "0.01",
    "cachedInputCnyPerMillion": "0.01",
    "outputCnyPerMillion": "0.01",
}
AI_SETTINGS["AI_ESSAY_GRADING_PRICE_CARDS_JSON"] = json.dumps(
    {
        key: _ESSAY_PRICE_CARD
        for key in ("gemini", "tokenrhythm", "hunyuan", "adjudicator")
    },
    separators=(",", ":"),
    sort_keys=True,
)

DEFERRED_PAYMENT_SECRETS = {
    "WECHAT_VIRTUAL_PAY_OFFER_ID",
    "WECHAT_VIRTUAL_PAY_APP_KEY",
    "WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN",
    "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY",
}

def _key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    candidate = stripped.split("=", 1)[0]
    return candidate if candidate.replace("_", "").isalnum() else None


def _existing_value(lines: list[str], name: str) -> str | None:
    for line in lines:
        if _key(line) != name:
            continue
        value = line.strip().split("=", 1)[1]
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            return None
        return parsed[0] if len(parsed) == 1 else None
    return None


def _render_assignment(name: str, value: str) -> str:
    return f"{name}={shlex.quote(value)}\n"


def _rewrite_env(
    env_file: Path,
    desired: dict[str, str],
    *,
    managed_comment: str,
    omitted_names: set[str] | None = None,
) -> None:
    original = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
    omitted_names = omitted_names or set()
    rendered: list[str] = []
    written: set[str] = set()
    for line in original:
        name = _key(line)
        if name in omitted_names:
            continue
        if name in desired:
            if name not in written:
                rendered.append(_render_assignment(name, desired[name]))
                written.add(name)
            continue
        rendered.append(line)
    if rendered and not rendered[-1].endswith("\n"):
        rendered[-1] += "\n"
    rendered.append(f"\n# {managed_comment}\n")
    for name, value in desired.items():
        if name not in written:
            rendered.append(_render_assignment(name, value))

    backup = env_file.with_suffix(env_file.suffix + ".previous")
    shutil.copy2(env_file, backup)
    os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=env_file.parent, delete=False
    ) as handle:
        handle.writelines(rendered)
        temporary = Path(handle.name)
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, env_file)


def configure(env_file: Path, api_key: str) -> None:
    if not api_key.strip():
        raise SystemExit("AI API key must not be empty")
    original = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
    required_existing = (
        "AI_ESSAY_PRIMARY_API_KEY",
        "CLOUDBASE_ENV_ID",
        "CLOUDBASE_API_KEY",
    )
    missing = [name for name in required_existing if not _existing_value(original, name)]
    if missing:
        raise SystemExit(
            "three-model essay secrets are incomplete: " + ", ".join(missing)
        )
    operations_token = _existing_value(original, "OPERATIONS_METRICS_TOKEN")
    desired = dict(AI_SETTINGS)
    desired["AI_API_KEY"] = api_key.strip()
    desired["AI_COMPANION_API_KEY"] = api_key.strip()
    desired["AI_ESSAY_TOKENRHYTHM_API_KEY"] = api_key.strip()
    desired["AI_ESSAY_ADJUDICATOR_API_KEY"] = api_key.strip()
    desired["OPERATIONS_METRICS_TOKEN"] = operations_token or secrets.token_urlsafe(48)
    _rewrite_env(
        env_file,
        desired,
        managed_comment="Production text AI contract (managed by configure_production_ai.py)",
        omitted_names=DEFERRED_PAYMENT_SECRETS,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file", type=Path, default=Path("/etc/gongkao-dazi/api.env")
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root so the production env remains root-only")
    api_key = getpass.getpass("TokenRhythm test API key: ")
    configure(args.env_file, api_key)
    print(
        f"configured={args.env_file} secret_output=false "
        "virtual_payment_secrets=absent"
    )


if __name__ == "__main__":
    main()
