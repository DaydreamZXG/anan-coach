from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_AI_PROVIDER_BASE_URL = "https://gemini-web2api.ergouapi.co/v1"


@dataclass(frozen=True)
class Settings:
    app_name: str = "考公搭子"
    app_slug: str = "gongkao-dazi"
    environment: str = "local"
    api_base_url: str = "https://kg.xingguoxian.cn"
    tos_bucket: str = "gongkao-dazi-prod"
    tos_region: str = "cn-guangzhou"
    ai_provider_base_url: str = DEFAULT_AI_PROVIDER_BASE_URL
    ai_model: str = "gemini-3.5-flash"
    ai_api_key: str = ""
    ai_companion_provider_release_id: str = ""
    ai_companion_provider_https_host_allowlist: str = ""
    ai_companion_provider_dpa_confirmed: bool = False
    ai_companion_provider_idempotency_capability: str = ""
    ai_companion_provider_idempotency_key_header: str = ""
    ai_companion_provider_request_id_header: str = ""
    ai_companion_provider_replay_retention_seconds: int = 0
    ai_companion_max_input_bytes: int = 0
    ai_companion_max_output_bytes: int = 0
    ai_essay_review_model: str = "gemini-3.5-flash"
    ai_essay_rubric_generator_model: str = ""
    ai_essay_rubric_critic_model: str = ""
    ai_essay_rubric_provider_release_id: str = ""
    ai_essay_rubric_max_input_bytes: int = 0
    ai_essay_rubric_max_output_bytes: int = 0
    ai_essay_review_provider_release_id: str = ""
    ai_essay_review_provider_https_host_allowlist: str = ""
    ai_essay_review_provider_dpa_confirmed: bool = False
    ai_essay_review_provider_idempotency_capability: str = ""
    ai_essay_review_provider_idempotency_key_header: str = ""
    ai_essay_review_provider_request_id_header: str = ""
    ai_essay_review_max_input_bytes: int = 0
    ai_essay_review_max_output_bytes: int = 0
    ai_price_version: str = ""
    ai_input_cny_per_million_tokens: str = ""
    ai_cached_input_cny_per_million_tokens: str = ""
    ai_output_cny_per_million_tokens: str = ""
    ai_essay_checkpoint_active_key_id: str = ""
    ai_essay_checkpoint_keys_json: str = ""
    ai_essay_checkpoint_ttl_seconds: int = 0
    app_store_apple_id: str = "6786934387"
    app_store_root_certificate_paths: str = ""
    bundle_id: str = "com.xingguoxian.gongkaodazi"
    wechat_mini_app_id: str = "wxa02a883c1807927b"
    wechat_mini_app_secret: str = ""
    wechat_api_base_url: str = "https://api.weixin.qq.com"
    database_url: str = ""


def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("APP_ENV", "local"),
        api_base_url=os.getenv("API_BASE_URL", "https://kg.xingguoxian.cn"),
        tos_bucket=os.getenv("TOS_BUCKET", "gongkao-dazi-prod"),
        tos_region=os.getenv("TOS_REGION", "cn-guangzhou"),
        ai_provider_base_url=os.getenv(
            "AI_PROVIDER_BASE_URL", DEFAULT_AI_PROVIDER_BASE_URL
        ),
        ai_model=os.getenv("AI_MODEL", "gemini-3.5-flash"),
        ai_api_key=os.getenv("AI_API_KEY", ""),
        ai_companion_provider_release_id=os.getenv(
            "AI_COMPANION_PROVIDER_RELEASE_ID",
            "",
        ),
        ai_companion_provider_https_host_allowlist=os.getenv(
            "AI_COMPANION_PROVIDER_HTTPS_HOST_ALLOWLIST",
            "",
        ),
        ai_companion_provider_dpa_confirmed=_environment_bool(
            "AI_COMPANION_PROVIDER_DPA_CONFIRMED",
            default=False,
        ),
        ai_companion_provider_idempotency_capability=os.getenv(
            "AI_COMPANION_PROVIDER_IDEMPOTENCY_CAPABILITY",
            "",
        ),
        ai_companion_provider_idempotency_key_header=os.getenv(
            "AI_COMPANION_PROVIDER_IDEMPOTENCY_KEY_HEADER",
            "",
        ),
        ai_companion_provider_request_id_header=os.getenv(
            "AI_COMPANION_PROVIDER_REQUEST_ID_HEADER",
            "",
        ),
        ai_companion_provider_replay_retention_seconds=int(
            os.getenv("AI_COMPANION_PROVIDER_REPLAY_RETENTION_SECONDS", "0")
        ),
        ai_companion_max_input_bytes=int(
            os.getenv("AI_COMPANION_MAX_INPUT_BYTES", "0")
        ),
        ai_companion_max_output_bytes=int(
            os.getenv("AI_COMPANION_MAX_OUTPUT_BYTES", "0")
        ),
        ai_essay_review_model=os.getenv(
            "AI_ESSAY_REVIEW_MODEL",
            os.getenv("AI_MODEL", "gemini-3.5-flash"),
        ),
        ai_essay_rubric_generator_model=os.getenv(
            "AI_ESSAY_RUBRIC_GENERATOR_MODEL",
            "",
        ),
        ai_essay_rubric_critic_model=os.getenv(
            "AI_ESSAY_RUBRIC_CRITIC_MODEL",
            "",
        ),
        ai_essay_rubric_provider_release_id=os.getenv(
            "AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID",
            "",
        ),
        ai_essay_rubric_max_input_bytes=int(
            os.getenv("AI_ESSAY_RUBRIC_MAX_INPUT_BYTES", "0")
        ),
        ai_essay_rubric_max_output_bytes=int(
            os.getenv("AI_ESSAY_RUBRIC_MAX_OUTPUT_BYTES", "0")
        ),
        ai_essay_review_provider_release_id=os.getenv(
            "AI_ESSAY_REVIEW_PROVIDER_RELEASE_ID",
            "",
        ),
        ai_essay_review_provider_https_host_allowlist=os.getenv(
            "AI_ESSAY_REVIEW_PROVIDER_HTTPS_HOST_ALLOWLIST",
            "",
        ),
        ai_essay_review_provider_dpa_confirmed=_environment_bool(
            "AI_ESSAY_REVIEW_PROVIDER_DPA_CONFIRMED",
            default=False,
        ),
        ai_essay_review_provider_idempotency_capability=os.getenv(
            "AI_ESSAY_REVIEW_PROVIDER_IDEMPOTENCY_CAPABILITY",
            "",
        ),
        ai_essay_review_provider_idempotency_key_header=os.getenv(
            "AI_ESSAY_REVIEW_PROVIDER_IDEMPOTENCY_KEY_HEADER",
            "",
        ),
        ai_essay_review_provider_request_id_header=os.getenv(
            "AI_ESSAY_REVIEW_PROVIDER_REQUEST_ID_HEADER",
            "",
        ),
        ai_essay_review_max_input_bytes=int(
            os.getenv("AI_ESSAY_REVIEW_MAX_INPUT_BYTES", "0")
        ),
        ai_essay_review_max_output_bytes=int(
            os.getenv("AI_ESSAY_REVIEW_MAX_OUTPUT_BYTES", "0")
        ),
        ai_price_version=os.getenv("AI_PRICE_VERSION", ""),
        ai_input_cny_per_million_tokens=os.getenv(
            "AI_INPUT_CNY_PER_MILLION_TOKENS",
            "",
        ),
        ai_cached_input_cny_per_million_tokens=os.getenv(
            "AI_CACHED_INPUT_CNY_PER_MILLION_TOKENS",
            "",
        ),
        ai_output_cny_per_million_tokens=os.getenv(
            "AI_OUTPUT_CNY_PER_MILLION_TOKENS",
            "",
        ),
        ai_essay_checkpoint_active_key_id=os.getenv(
            "AI_ESSAY_CHECKPOINT_ACTIVE_KEY_ID",
            "",
        ),
        ai_essay_checkpoint_keys_json=os.getenv(
            "AI_ESSAY_CHECKPOINT_KEYS_JSON",
            "",
        ),
        ai_essay_checkpoint_ttl_seconds=int(
            os.getenv("AI_ESSAY_CHECKPOINT_TTL_SECONDS", "0")
        ),
        app_store_root_certificate_paths=os.getenv(
            "APP_STORE_ROOT_CERTIFICATE_PATHS",
            "",
        ),
        wechat_mini_app_id=os.getenv(
            "WECHAT_MINI_APP_ID",
            "wxa02a883c1807927b",
        ),
        wechat_mini_app_secret=os.getenv("WECHAT_MINI_APP_SECRET", ""),
        wechat_api_base_url=os.getenv(
            "WECHAT_API_BASE_URL",
            "https://api.weixin.qq.com",
        ),
        database_url=os.getenv("DATABASE_URL", ""),
    )


def _environment_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be an explicit boolean")
