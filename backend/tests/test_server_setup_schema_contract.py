from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_server_setup_script_keeps_commerce_state_constraints() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = [
        "commerce_iap_products_tier_check",
        "check (tier in ('free', 'pro'))",
        "commerce_iap_products_billing_period_check",
        "check (billing_period in ('monthly', 'yearly'))",
        "commerce_user_entitlements_tier_check",
        "commerce_app_store_transactions_verification_status_check",
        "'verified_active'",
        "'verified_expired'",
        "'verified_revoked'",
    ]

    for fragment in required_fragments:
        assert fragment in script


def test_server_setup_script_keeps_commerce_audit_indexes() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = [
        "commerce_app_store_transactions_user_idx",
        "commerce_app_store_transactions_original_idx",
        "commerce_app_store_notification_events_created_idx",
        "commerce_entitlement_audit_logs_user_idx",
    ]

    for fragment in required_fragments:
        assert fragment in script


def test_server_setup_script_keeps_wechat_identity_security_contract() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = [
        "learner.wechat_identities",
        "unique (app_id, open_id)",
        "check (length(session_key_hash) = 64)",
        "learner_wechat_identities_user_idx",
        "learner_wechat_identities_union_idx",
    ]

    for fragment in required_fragments:
        assert fragment in script
