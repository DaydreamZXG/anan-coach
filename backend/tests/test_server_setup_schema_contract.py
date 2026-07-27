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


def test_server_setup_script_respects_versioned_knowledge_map_ownership() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    release_guard = (
        "and to_regclass('product.knowledge_map_releases') is null then"
    )
    legacy_business_key = (
        "add constraint "
        "product_knowledge_map_nodes_scope_track_module_type_tag_key"
    )
    assert release_guard in script
    assert script.index(release_guard) < script.index(legacy_business_key)
    assert (
        "alter table product.knowledge_map_releases owner to gongkao_owner"
        in script
    )
    assert "grant select on product.knowledge_map_releases to gongkao_api" in script


def test_server_setup_script_freezes_external_question_release_lineage() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = (
        "add column if not exists knowledge_release_id uuid",
        "with fixed_legacy_binding as",
        "'knowledge-map-legacy|' || node.exam_scope || '|' || node.track",
        "'knowledge-map@legacy-pre-20260727-65:'",
        "and intake.created_at <= release.retired_at",
        "external-question legacy knowledge binding is missing or ambiguous",
        "learner_external_intake_release_status_check",
        "learner_external_intake_release_node_fkey",
        "learner_external_intake_binding_key",
        "learner_external_wrong_intake_binding_fkey",
        "learner_external_wrong_release_node_fkey",
        "external_question_intake_binding_immutable",
        "external_wrong_question_binding_immutable",
        "learner_external_intake_release_node_idx",
        "learner_external_wrong_release_node_idx",
        "alter column knowledge_release_id set not null",
    )
    for fragment in required_fragments:
        assert fragment in script

    assert "with unambiguous_binding as" not in script
    assert "having count(distinct node.release_id) = 1" not in script
    backfill = script.index("with fixed_legacy_binding as")
    ambiguity_guard = script.index(
        "external-question legacy knowledge binding is missing or ambiguous"
    )
    binding_constraint = script.index("learner_external_intake_release_node_fkey")
    immutable_trigger = script.index("external_question_intake_binding_immutable")
    assert backfill < ambiguity_guard < binding_constraint < immutable_trigger


def test_server_setup_script_owns_external_lineage_guards() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = (
        "alter function learner.guard_external_intake_knowledge_binding()",
        "alter function learner.guard_external_wrong_knowledge_binding()",
        "revoke all on function learner.guard_external_intake_knowledge_binding()",
        "revoke all on function learner.guard_external_wrong_knowledge_binding()",
        "grant execute on function learner.guard_external_intake_knowledge_binding()",
        "grant execute on function learner.guard_external_wrong_knowledge_binding()",
    )
    for fragment in required_fragments:
        assert fragment in script


def test_bootstrap_and_alembic_share_external_lineage_contract() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()
    migration = PROJECT_ROOT.joinpath(
        "backend/migrations/versions/"
        "20260727_67_external_question_knowledge_lineage.py"
    ).read_text()

    shared_contract = (
        "with fixed_legacy_binding as",
        "'knowledge-map@legacy-pre-20260727-65:'",
        "and intake.created_at <= release.retired_at",
        "learner_external_intake_release_status_check",
        "learner_external_intake_release_node_fkey",
        "learner_external_intake_binding_key",
        "learner_external_wrong_intake_binding_fkey",
        "learner_external_wrong_release_node_fkey",
        "external_question_intake_binding_immutable",
        "external_wrong_question_binding_immutable",
        "learner_external_intake_release_node_idx",
        "learner_external_wrong_release_node_idx",
    )
    for fragment in shared_contract:
        assert fragment in script
        assert fragment in migration


def test_server_setup_governs_question_binding_runtime_permissions() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    required_fragments = (
        "alter table product.knowledge_map_question_bindings owner to gongkao_owner",
        "product.knowledge_map_question_binding_row_fingerprint(",
        "product.knowledge_map_question_binding_authority_is_valid(",
        "product.knowledge_map_question_binding_manifest(uuid)",
        "product.guard_knowledge_map_question_binding_mutation()",
        "product.guard_knowledge_map_release_lifecycle()",
        "grant select on product.knowledge_map_question_bindings to gongkao_api",
        "grant select on product.question_content_versions to gongkao_api",
        "grant select on product.vocab_module_alias_map to gongkao_api",
        "grant select, insert\n        on product.knowledge_map_question_bindings\n"
        "        to gongkao_importer",
        "grant select on product.question_content_versions to gongkao_importer",
    )
    for fragment in required_fragments:
        assert fragment in script

    api_grant = script.index(
        "grant execute on function\n"
        "      product.knowledge_map_question_binding_manifest(uuid)\n"
        "      to gongkao_api"
    )
    importer_manifest_grant = script.index(
        "grant execute on function\n"
        "        product.knowledge_map_question_binding_manifest(uuid)\n"
        "        to gongkao_importer"
    )
    importer_fingerprint_grant = script.index(
        "grant execute on function\n"
        "        product.knowledge_map_question_binding_row_fingerprint("
    )
    assert api_grant < importer_manifest_grant
    assert importer_fingerprint_grant < importer_manifest_grant
    assert (
        "grant select, insert, update\n"
        "        on product.knowledge_map_question_bindings"
        not in script
    )


def test_server_setup_grants_api_exact_module_vocabulary_read_access() -> None:
    script = PROJECT_ROOT.joinpath("scripts/server_setup_learning_ai_tables.sh").read_text()

    assert (
        "alter table product.vocab_module_alias_map owner to gongkao_owner"
        in script
    )
    assert "grant select on product.vocab_module_alias_map to gongkao_api" in script
    assert "grant select on product.vocab_facet_rules to gongkao_api" not in script
