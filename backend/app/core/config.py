from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
from typing import Literal, cast


DEFAULT_AI_PROVIDER_BASE_URL = "https://ai-provider.invalid/v1"

CommercialMode = Literal["free_cold_start", "paid"]
UsageAccessMode = Literal["unlimited", "entitlement_quota"]


@dataclass(frozen=True)
class Settings:
    app_name: str = "考公搭子"
    app_slug: str = "gongkao-dazi"
    environment: str = "local"
    commercial_mode: CommercialMode = "free_cold_start"
    usage_access_mode: UsageAccessMode = "unlimited"
    api_base_url: str = "https://kg.xingguoxian.cn"
    tos_bucket: str = "gongkao-dazi-prod"
    tos_region: str = "cn-guangzhou"
    tos_endpoint: str = ""
    tos_access_key: str = ""
    tos_secret_key: str = ""
    tos_quarantine_prefix: str = "private/quarantine/external-learning"
    upload_quota_policy_version: str = ""
    upload_maximum_pending_bytes_per_user: int = 0
    upload_maximum_stored_bytes_per_user: int = 0
    upload_scanner_base_url: str = ""
    upload_scanner_api_key: str = ""
    upload_scanner_release_id: str = ""
    upload_scanner_https_host_allowlist: str = ""
    upload_scanner_route: str = ""
    upload_scanner_policy_version: str = ""
    upload_scanner_dpa_version: str = ""
    upload_scanner_mainland_only: bool = False
    upload_scanner_maximum_definition_age: timedelta = timedelta(0)
    recognition_processor_endpoint: str = ""
    recognition_processor_token: str = ""
    recognition_processor_release_id: str = ""
    recognition_processor_adapter: str = "private_contract"
    recognition_processor_model: str = ""
    recognition_processor_actual_model: str = ""
    recognition_processor_max_input_bytes: int = 0
    recognition_processor_max_output_bytes: int = 0
    recognition_processor_max_pages: int = 0
    recognition_processor_https_host_allowlist: str = ""
    recognition_processor_approvals_json: str = "[]"
    recognition_default_import_route: str = ""
    recognition_default_answer_route: str = ""
    recognition_default_essay_handwriting_route: str = ""
    recognition_default_external_score_report_route: str = ""
    source_artifact_access_ttl_seconds: int = 300
    matching_policy_version: str = "external-hybrid-match-v1"
    matching_similar_threshold: float = 0.86
    matching_ambiguity_margin: float = 0.03
    matching_text_weight: float = 0.75
    matching_visual_weight: float = 0.25
    matching_essay_min_prompt_chars: int = 30
    matching_essay_require_material: bool = True
    matching_maximum_candidates: int = 8
    matching_sql_prefilter_threshold: float = 0.20
    matching_sql_prefetch_factor: int = 5
    matching_sql_maximum_prefetch: int = 100
    packet_renderer_base_url: str = ""
    packet_renderer_api_key: str = ""
    packet_renderer_release_id: str = ""
    packet_renderer_https_host_allowlist: str = ""
    packet_renderer_dpa_confirmed: bool = False
    packet_renderer_idempotency_header: str = ""
    packet_renderer_request_id_header: str = ""
    packet_access_ttl_seconds: int = 300
    external_learning_worker_poll_seconds: float = 1.0
    external_learning_maintenance_interval_seconds: int = 30
    external_learning_upload_recovery_age_seconds: int = 60
    external_learning_stale_claim_seconds: int = 900
    external_learning_maintenance_batch_size: int = 100
    ai_provider_base_url: str = DEFAULT_AI_PROVIDER_BASE_URL
    ai_model: str = "gemini-3.5-flash"
    ai_small_model: str = ""
    ai_high_capability_model: str = ""
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
    ai_xingce_cause_model: str = "gemini-3.5-flash"
    ai_xingce_cause_provider_release_id: str = ""
    ai_xingce_cause_provider_https_host_allowlist: str = ""
    ai_xingce_cause_provider_dpa_confirmed: bool = False
    ai_xingce_cause_provider_idempotency_capability: str = ""
    ai_xingce_cause_provider_idempotency_key_header: str = ""
    ai_xingce_cause_provider_request_id_header: str = ""
    ai_xingce_cause_provider_replay_retention_seconds: int = 0
    ai_xingce_cause_max_input_bytes: int = 0
    ai_xingce_cause_max_output_bytes: int = 0
    ai_community_candidate_model: str = "gemini-3.5-flash"
    ai_community_candidate_provider_release_id: str = ""
    ai_community_candidate_provider_https_host_allowlist: str = ""
    ai_community_candidate_provider_dpa_confirmed: bool = False
    ai_community_candidate_provider_idempotency_capability: str = ""
    ai_community_candidate_provider_idempotency_key_header: str = ""
    ai_community_candidate_provider_request_id_header: str = ""
    ai_community_candidate_provider_replay_retention_seconds: int = 0
    ai_community_candidate_max_input_bytes: int = 0
    ai_community_candidate_max_output_bytes: int = 0
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
    ai_essay_review_provider_replay_retention_seconds: int = 0
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
    wechat_mini_app_id: str = "wx42824c7f7bfbf4b2"
    wechat_mini_app_secret: str = ""
    wechat_api_base_url: str = "https://api.weixin.qq.com"
    wechat_pay_mch_id: str = ""
    wechat_pay_merchant_serial: str = ""
    wechat_pay_merchant_private_key_path: str = ""
    wechat_pay_api_v3_key: str = ""
    wechat_pay_platform_certificates_json: str = ""
    wechat_pay_notify_url: str = ""
    wechat_pay_refund_notify_url: str = ""
    wechat_pay_order_expiry_seconds: int = 1800
    wechat_virtual_pay_offer_id: str = ""
    wechat_virtual_pay_app_key: str = ""
    wechat_virtual_pay_env: int = 0
    wechat_virtual_pay_message_token: str = ""
    wechat_virtual_pay_message_encoding_aes_key: str = ""
    privacy_export_expiry_seconds: int = 86400
    operations_metrics_token: str = ""
    required_database_revision: str = "20260719_41"
    support_jwt_jwks_url: str = ""
    support_jwt_issuer: str = ""
    support_jwt_audience: str = ""
    database_url: str = ""


def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("APP_ENV", "local"),
        commercial_mode=_commercial_mode(),
        usage_access_mode=_usage_access_mode(),
        api_base_url=os.getenv("API_BASE_URL", "https://kg.xingguoxian.cn"),
        tos_bucket=os.getenv("TOS_BUCKET", "gongkao-dazi-prod"),
        tos_region=os.getenv("TOS_REGION", "cn-guangzhou"),
        tos_endpoint=os.getenv("TOS_ENDPOINT", ""),
        tos_access_key=os.getenv("TOS_ACCESS_KEY", ""),
        tos_secret_key=os.getenv("TOS_SECRET_KEY", ""),
        tos_quarantine_prefix=os.getenv(
            "TOS_QUARANTINE_PREFIX", "private/quarantine/external-learning"
        ),
        upload_quota_policy_version=os.getenv("UPLOAD_QUOTA_POLICY_VERSION", ""),
        upload_maximum_pending_bytes_per_user=int(
            os.getenv("UPLOAD_MAXIMUM_PENDING_BYTES_PER_USER", "0")
        ),
        upload_maximum_stored_bytes_per_user=int(
            os.getenv("UPLOAD_MAXIMUM_STORED_BYTES_PER_USER", "0")
        ),
        upload_scanner_base_url=os.getenv("UPLOAD_SCANNER_BASE_URL", ""),
        upload_scanner_api_key=os.getenv("UPLOAD_SCANNER_API_KEY", ""),
        upload_scanner_release_id=os.getenv("UPLOAD_SCANNER_RELEASE_ID", ""),
        upload_scanner_https_host_allowlist=os.getenv(
            "UPLOAD_SCANNER_HTTPS_HOST_ALLOWLIST", ""
        ),
        upload_scanner_route=os.getenv("UPLOAD_SCANNER_ROUTE", ""),
        upload_scanner_policy_version=os.getenv("UPLOAD_SCANNER_POLICY_VERSION", ""),
        upload_scanner_dpa_version=os.getenv("UPLOAD_SCANNER_DPA_VERSION", ""),
        upload_scanner_mainland_only=_environment_bool(
            "UPLOAD_SCANNER_MAINLAND_ONLY", default=False
        ),
        upload_scanner_maximum_definition_age=timedelta(
            seconds=int(os.getenv("UPLOAD_SCANNER_MAXIMUM_DEFINITION_AGE_SECONDS", "0"))
        ),
        recognition_processor_endpoint=os.getenv("RECOGNITION_PROCESSOR_ENDPOINT", ""),
        recognition_processor_token=os.getenv("RECOGNITION_PROCESSOR_TOKEN", ""),
        recognition_processor_release_id=os.getenv(
            "RECOGNITION_PROCESSOR_RELEASE_ID", ""
        ),
        recognition_processor_adapter=os.getenv(
            "RECOGNITION_PROCESSOR_ADAPTER", "private_contract"
        ),
        recognition_processor_model=os.getenv("RECOGNITION_PROCESSOR_MODEL", ""),
        recognition_processor_actual_model=os.getenv(
            "RECOGNITION_PROCESSOR_ACTUAL_MODEL", ""
        ),
        recognition_processor_max_input_bytes=int(
            os.getenv("RECOGNITION_PROCESSOR_MAX_INPUT_BYTES", "0")
        ),
        recognition_processor_max_output_bytes=int(
            os.getenv("RECOGNITION_PROCESSOR_MAX_OUTPUT_BYTES", "0")
        ),
        recognition_processor_max_pages=int(
            os.getenv("RECOGNITION_PROCESSOR_MAX_PAGES", "0")
        ),
        recognition_processor_https_host_allowlist=os.getenv(
            "RECOGNITION_PROCESSOR_HTTPS_HOST_ALLOWLIST", ""
        ),
        recognition_processor_approvals_json=os.getenv(
            "RECOGNITION_PROCESSOR_APPROVALS_JSON", "[]"
        ),
        recognition_default_import_route=os.getenv(
            "RECOGNITION_DEFAULT_IMPORT_ROUTE", ""
        ),
        recognition_default_answer_route=os.getenv(
            "RECOGNITION_DEFAULT_ANSWER_ROUTE", ""
        ),
        recognition_default_essay_handwriting_route=os.getenv(
            "RECOGNITION_DEFAULT_ESSAY_HANDWRITING_ROUTE", ""
        ),
        recognition_default_external_score_report_route=os.getenv(
            "RECOGNITION_DEFAULT_EXTERNAL_SCORE_REPORT_ROUTE", ""
        ),
        source_artifact_access_ttl_seconds=int(
            os.getenv("SOURCE_ARTIFACT_ACCESS_TTL_SECONDS", "300")
        ),
        matching_policy_version=os.getenv(
            "MATCHING_POLICY_VERSION", "external-hybrid-match-v1"
        ),
        matching_similar_threshold=float(
            os.getenv("MATCHING_SIMILAR_THRESHOLD", "0.86")
        ),
        matching_ambiguity_margin=float(os.getenv("MATCHING_AMBIGUITY_MARGIN", "0.03")),
        matching_text_weight=float(os.getenv("MATCHING_TEXT_WEIGHT", "0.75")),
        matching_visual_weight=float(os.getenv("MATCHING_VISUAL_WEIGHT", "0.25")),
        matching_essay_min_prompt_chars=int(
            os.getenv("MATCHING_ESSAY_MIN_PROMPT_CHARS", "30")
        ),
        matching_essay_require_material=_environment_bool(
            "MATCHING_ESSAY_REQUIRE_MATERIAL", default=True
        ),
        matching_maximum_candidates=int(os.getenv("MATCHING_MAXIMUM_CANDIDATES", "8")),
        matching_sql_prefilter_threshold=float(
            os.getenv("MATCHING_SQL_PREFILTER_THRESHOLD", "0.20")
        ),
        matching_sql_prefetch_factor=int(
            os.getenv("MATCHING_SQL_PREFETCH_FACTOR", "5")
        ),
        matching_sql_maximum_prefetch=int(
            os.getenv("MATCHING_SQL_MAXIMUM_PREFETCH", "100")
        ),
        packet_renderer_base_url=os.getenv("PACKET_RENDERER_BASE_URL", ""),
        packet_renderer_api_key=os.getenv("PACKET_RENDERER_API_KEY", ""),
        packet_renderer_release_id=os.getenv("PACKET_RENDERER_RELEASE_ID", ""),
        packet_renderer_https_host_allowlist=os.getenv(
            "PACKET_RENDERER_HTTPS_HOST_ALLOWLIST", ""
        ),
        packet_renderer_dpa_confirmed=_environment_bool(
            "PACKET_RENDERER_DPA_CONFIRMED", default=False
        ),
        packet_renderer_idempotency_header=os.getenv(
            "PACKET_RENDERER_IDEMPOTENCY_HEADER", ""
        ),
        packet_renderer_request_id_header=os.getenv(
            "PACKET_RENDERER_REQUEST_ID_HEADER", ""
        ),
        packet_access_ttl_seconds=int(os.getenv("PACKET_ACCESS_TTL_SECONDS", "300")),
        external_learning_worker_poll_seconds=float(
            os.getenv("EXTERNAL_LEARNING_WORKER_POLL_SECONDS", "1")
        ),
        external_learning_maintenance_interval_seconds=int(
            os.getenv("EXTERNAL_LEARNING_MAINTENANCE_INTERVAL_SECONDS", "30")
        ),
        external_learning_upload_recovery_age_seconds=int(
            os.getenv("EXTERNAL_LEARNING_UPLOAD_RECOVERY_AGE_SECONDS", "60")
        ),
        external_learning_stale_claim_seconds=int(
            os.getenv("EXTERNAL_LEARNING_STALE_CLAIM_SECONDS", "900")
        ),
        external_learning_maintenance_batch_size=int(
            os.getenv("EXTERNAL_LEARNING_MAINTENANCE_BATCH_SIZE", "100")
        ),
        ai_provider_base_url=os.getenv(
            "AI_PROVIDER_BASE_URL", DEFAULT_AI_PROVIDER_BASE_URL
        ),
        ai_model=os.getenv("AI_MODEL", "gemini-3.5-flash"),
        ai_small_model=os.getenv("AI_SMALL_MODEL", ""),
        ai_high_capability_model=os.getenv("AI_HIGH_CAPABILITY_MODEL", ""),
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
        ai_xingce_cause_model=os.getenv(
            "AI_XINGCE_CAUSE_MODEL",
            os.getenv("AI_MODEL", "gemini-3.5-flash"),
        ),
        ai_xingce_cause_provider_release_id=os.getenv(
            "AI_XINGCE_CAUSE_PROVIDER_RELEASE_ID",
            "",
        ),
        ai_xingce_cause_provider_https_host_allowlist=os.getenv(
            "AI_XINGCE_CAUSE_PROVIDER_HTTPS_HOST_ALLOWLIST",
            "",
        ),
        ai_xingce_cause_provider_dpa_confirmed=_environment_bool(
            "AI_XINGCE_CAUSE_PROVIDER_DPA_CONFIRMED",
            default=False,
        ),
        ai_xingce_cause_provider_idempotency_capability=os.getenv(
            "AI_XINGCE_CAUSE_PROVIDER_IDEMPOTENCY_CAPABILITY",
            "",
        ),
        ai_xingce_cause_provider_idempotency_key_header=os.getenv(
            "AI_XINGCE_CAUSE_PROVIDER_IDEMPOTENCY_KEY_HEADER",
            "",
        ),
        ai_xingce_cause_provider_request_id_header=os.getenv(
            "AI_XINGCE_CAUSE_PROVIDER_REQUEST_ID_HEADER",
            "",
        ),
        ai_xingce_cause_provider_replay_retention_seconds=int(
            os.getenv("AI_XINGCE_CAUSE_PROVIDER_REPLAY_RETENTION_SECONDS", "0")
        ),
        ai_xingce_cause_max_input_bytes=int(
            os.getenv("AI_XINGCE_CAUSE_MAX_INPUT_BYTES", "0")
        ),
        ai_xingce_cause_max_output_bytes=int(
            os.getenv("AI_XINGCE_CAUSE_MAX_OUTPUT_BYTES", "0")
        ),
        ai_community_candidate_model=os.getenv(
            "AI_COMMUNITY_CANDIDATE_MODEL",
            os.getenv("AI_MODEL", "gemini-3.5-flash"),
        ),
        ai_community_candidate_provider_release_id=os.getenv(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_RELEASE_ID",
            "",
        ),
        ai_community_candidate_provider_https_host_allowlist=os.getenv(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_HTTPS_HOST_ALLOWLIST",
            "",
        ),
        ai_community_candidate_provider_dpa_confirmed=_environment_bool(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_DPA_CONFIRMED",
            default=False,
        ),
        ai_community_candidate_provider_idempotency_capability=os.getenv(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_IDEMPOTENCY_CAPABILITY",
            "",
        ),
        ai_community_candidate_provider_idempotency_key_header=os.getenv(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_IDEMPOTENCY_KEY_HEADER",
            "",
        ),
        ai_community_candidate_provider_request_id_header=os.getenv(
            "AI_COMMUNITY_CANDIDATE_PROVIDER_REQUEST_ID_HEADER",
            "",
        ),
        ai_community_candidate_provider_replay_retention_seconds=int(
            os.getenv(
                "AI_COMMUNITY_CANDIDATE_PROVIDER_REPLAY_RETENTION_SECONDS",
                "0",
            )
        ),
        ai_community_candidate_max_input_bytes=int(
            os.getenv("AI_COMMUNITY_CANDIDATE_MAX_INPUT_BYTES", "0")
        ),
        ai_community_candidate_max_output_bytes=int(
            os.getenv("AI_COMMUNITY_CANDIDATE_MAX_OUTPUT_BYTES", "0")
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
        ai_essay_review_provider_replay_retention_seconds=int(
            os.getenv(
                "AI_ESSAY_REVIEW_PROVIDER_REPLAY_RETENTION_SECONDS",
                "0",
            )
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
            "wx42824c7f7bfbf4b2",
        ),
        wechat_mini_app_secret=os.getenv("WECHAT_MINI_APP_SECRET", ""),
        wechat_api_base_url=os.getenv(
            "WECHAT_API_BASE_URL",
            "https://api.weixin.qq.com",
        ),
        wechat_pay_mch_id=os.getenv("WECHAT_PAY_MCH_ID", ""),
        wechat_pay_merchant_serial=os.getenv("WECHAT_PAY_MERCHANT_SERIAL", ""),
        wechat_pay_merchant_private_key_path=os.getenv(
            "WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH", ""
        ),
        wechat_pay_api_v3_key=os.getenv("WECHAT_PAY_API_V3_KEY", ""),
        wechat_pay_platform_certificates_json=os.getenv(
            "WECHAT_PAY_PLATFORM_CERTIFICATES_JSON", ""
        ),
        wechat_pay_notify_url=os.getenv("WECHAT_PAY_NOTIFY_URL", ""),
        wechat_pay_refund_notify_url=os.getenv("WECHAT_PAY_REFUND_NOTIFY_URL", ""),
        wechat_pay_order_expiry_seconds=int(
            os.getenv("WECHAT_PAY_ORDER_EXPIRY_SECONDS", "1800")
        ),
        wechat_virtual_pay_offer_id=os.getenv("WECHAT_VIRTUAL_PAY_OFFER_ID", ""),
        wechat_virtual_pay_app_key=os.getenv("WECHAT_VIRTUAL_PAY_APP_KEY", ""),
        wechat_virtual_pay_env=int(os.getenv("WECHAT_VIRTUAL_PAY_ENV", "0")),
        wechat_virtual_pay_message_token=os.getenv(
            "WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN", ""
        ),
        wechat_virtual_pay_message_encoding_aes_key=os.getenv(
            "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY", ""
        ),
        privacy_export_expiry_seconds=int(
            os.getenv("PRIVACY_EXPORT_EXPIRY_SECONDS", "86400")
        ),
        operations_metrics_token=os.getenv("OPERATIONS_METRICS_TOKEN", ""),
        required_database_revision=os.getenv(
            "REQUIRED_DATABASE_REVISION", "20260719_41"
        ),
        support_jwt_jwks_url=os.getenv("SUPPORT_JWT_JWKS_URL", ""),
        support_jwt_issuer=os.getenv("SUPPORT_JWT_ISSUER", ""),
        support_jwt_audience=os.getenv("SUPPORT_JWT_AUDIENCE", ""),
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


def _commercial_mode() -> CommercialMode:
    value = os.getenv("COMMERCIAL_MODE", "free_cold_start").strip().lower()
    if value not in {"free_cold_start", "paid"}:
        raise ValueError("COMMERCIAL_MODE must be either 'free_cold_start' or 'paid'")
    return cast(CommercialMode, value)


def _usage_access_mode() -> UsageAccessMode:
    value = os.getenv("USAGE_ACCESS_MODE", "unlimited").strip().lower()
    if value not in {"unlimited", "entitlement_quota"}:
        raise ValueError(
            "USAGE_ACCESS_MODE must be either 'unlimited' or 'entitlement_quota'"
        )
    return cast(UsageAccessMode, value)
