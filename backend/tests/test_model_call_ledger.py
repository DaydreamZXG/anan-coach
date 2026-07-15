from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelPriceCard,
    ModelTokenUsage,
)


def test_model_call_ledger_allows_one_terminal_transition_then_is_immutable(
    db_session: Session,
) -> None:
    repository = ModelCallLedgerRepository()
    record = repository.begin(
        db_session,
        call_key="ledger-integrity-success",
        user_id=None,
        job_id=None,
        feature="essay_review",
        operation="grounded_review_initial",
        provider="provider@release",
        model="model-release",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        price_card=_price_card(),
        input_fingerprint="a" * 64,
    )
    repository.succeed(
        db_session,
        call_id=record.call_id,
        usage=ModelTokenUsage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
        ),
        output_fingerprint="b" * 64,
        latency_ms=10,
    )
    db_session.commit()

    with pytest.raises(DBAPIError, match="terminal model call ledger rows are immutable"):
        db_session.execute(
            text(
                "update ai_audit.model_call_ledger set error_code = 'tampered' "
                "where call_id = cast(:call_id as uuid)"
            ),
            {"call_id": str(record.call_id)},
        )
    db_session.rollback()

    with pytest.raises(DBAPIError, match="model call ledger history cannot be deleted"):
        db_session.execute(
            text(
                "delete from ai_audit.model_call_ledger "
                "where call_id = cast(:call_id as uuid)"
            ),
            {"call_id": str(record.call_id)},
        )
    db_session.rollback()


def test_model_call_ledger_rejects_impossible_usage_and_failed_shapes(
    db_session: Session,
) -> None:
    common = {
        "feature": "essay_review",
        "operation": "grounded_review_initial",
        "provider": "provider@release",
        "model": "model-release",
        "prompt_version": "prompt-v1",
        "schema_version": "schema-v1",
        "price_version": "price-v1",
        "input_rate": Decimal("2"),
        "cached_input_rate": Decimal("0.5"),
        "output_rate": Decimal("4"),
        "input_fingerprint": "c" * 64,
    }
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                insert into ai_audit.model_call_ledger (
                  call_id, call_key, feature, operation, provider, model,
                  prompt_version, schema_version, price_version,
                  input_rate_cny_per_million,
                  cached_input_rate_cny_per_million,
                  output_rate_cny_per_million,
                  input_fingerprint, output_fingerprint, status,
                  input_tokens, cached_input_tokens, output_tokens,
                  input_cost_cny, cached_input_cost_cny, output_cost_cny,
                  total_cost_cny, latency_ms, completed_at
                ) values (
                  gen_random_uuid(), 'ledger-impossible-cache', :feature,
                  :operation, :provider, :model, :prompt_version,
                  :schema_version, :price_version, :input_rate,
                  :cached_input_rate, :output_rate, :input_fingerprint,
                  repeat('d', 64), 'succeeded', 10, 11, 1,
                  0, 0, 0, 0, 1, now()
                )
                """
            ),
            common,
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                insert into ai_audit.model_call_ledger (
                  call_id, call_key, feature, operation, provider, model,
                  prompt_version, schema_version, price_version,
                  input_rate_cny_per_million,
                  cached_input_rate_cny_per_million,
                  output_rate_cny_per_million,
                  input_fingerprint, status, latency_ms, completed_at
                ) values (
                  gen_random_uuid(), 'ledger-failed-without-code', :feature,
                  :operation, :provider, :model, :prompt_version,
                  :schema_version, :price_version, :input_rate,
                  :cached_input_rate, :output_rate, :input_fingerprint,
                  'failed', 1, now()
                )
                """
            ),
            common,
        )
    db_session.rollback()


def test_model_call_ledger_replay_uses_the_original_price_snapshot(
    db_session: Session,
) -> None:
    repository = ModelCallLedgerRepository()
    original_price = _price_card()
    replacement_price = ModelPriceCard(
        version="price-v2",
        input_cny_per_million=Decimal("20"),
        cached_input_cny_per_million=Decimal("5"),
        output_cny_per_million=Decimal("40"),
    )
    usage = ModelTokenUsage(
        input_tokens=100,
        cached_input_tokens=40,
        output_tokens=20,
    )
    original = repository.begin(
        db_session,
        call_key="ledger-price-snapshot-replay",
        user_id=None,
        job_id=None,
        feature="essay_review",
        operation="grounded_review_initial",
        provider="provider@release",
        model="model-release",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        price_card=original_price,
        input_fingerprint="e" * 64,
    )
    db_session.commit()

    replay = repository.begin(
        db_session,
        call_key="ledger-price-snapshot-replay",
        user_id=None,
        job_id=None,
        feature="essay_review",
        operation="grounded_review_initial",
        provider="provider@release",
        model="model-release",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        price_card=replacement_price,
        input_fingerprint="e" * 64,
    )
    assert replay.call_id == original.call_id
    assert replay.price_card == original_price

    repository.succeed(
        db_session,
        call_id=replay.call_id,
        usage=usage,
        output_fingerprint="f" * 64,
        latency_ms=10,
    )
    db_session.commit()

    stored = db_session.execute(
        text(
            "select price_version, input_rate_cny_per_million, "
            "cached_input_rate_cny_per_million, output_rate_cny_per_million, "
            "input_cost_cny, cached_input_cost_cny, output_cost_cny, "
            "total_cost_cny from ai_audit.model_call_ledger "
            "where call_id = cast(:call_id as uuid)"
        ),
        {"call_id": str(original.call_id)},
    ).mappings().one()
    expected = original_price.cost(usage)
    assert stored["price_version"] == original_price.version
    assert stored["input_rate_cny_per_million"] == Decimal("2")
    assert stored["cached_input_rate_cny_per_million"] == Decimal("0.5")
    assert stored["output_rate_cny_per_million"] == Decimal("4")
    assert stored["input_cost_cny"] == expected.input_cost_cny
    assert stored["cached_input_cost_cny"] == expected.cached_input_cost_cny
    assert stored["output_cost_cny"] == expected.output_cost_cny
    assert stored["total_cost_cny"] == expected.total_cost_cny


def test_model_call_ledger_rounds_components_before_conserving_total(
    db_session: Session,
) -> None:
    repository = ModelCallLedgerRepository()
    price_card = ModelPriceCard(
        version="price-low-rate-v1",
        input_cny_per_million=Decimal("0.006"),
        cached_input_cny_per_million=Decimal("0"),
        output_cny_per_million=Decimal("0.006"),
    )
    usage = ModelTokenUsage(
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=1,
    )
    record = repository.begin(
        db_session,
        call_key="ledger-component-rounding",
        user_id=None,
        job_id=None,
        feature="essay_review",
        operation="grounded_review_initial",
        provider="provider@release",
        model="model-release",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        price_card=price_card,
        input_fingerprint="1" * 64,
    )
    repository.succeed(
        db_session,
        call_id=record.call_id,
        usage=usage,
        output_fingerprint="2" * 64,
        latency_ms=1,
    )
    db_session.commit()

    costs = db_session.execute(
        text(
            "select input_cost_cny, cached_input_cost_cny, output_cost_cny, "
            "total_cost_cny from ai_audit.model_call_ledger "
            "where call_id = cast(:call_id as uuid)"
        ),
        {"call_id": str(record.call_id)},
    ).mappings().one()
    assert costs == {
        "input_cost_cny": Decimal("0.00000001"),
        "cached_input_cost_cny": Decimal("0E-8"),
        "output_cost_cny": Decimal("0.00000001"),
        "total_cost_cny": Decimal("0.00000002"),
    }


def _price_card() -> ModelPriceCard:
    return ModelPriceCard(
        version="price-v1",
        input_cny_per_million=Decimal("2"),
        cached_input_cny_per_million=Decimal("0.5"),
        output_cny_per_million=Decimal("4"),
    )
