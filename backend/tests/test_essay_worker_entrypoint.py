from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import Settings
from app.workers.essay_reviews import _validate_worker_settings


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_UNIT = REPO_ROOT / "ops" / "systemd" / "gongkao-dazi-essay-review-worker.service"


def test_essay_worker_requires_explicit_enablement() -> None:
    with pytest.raises(
        ValueError,
        match="ESSAY_REVIEW_WORKER_ENABLED must be true",
    ):
        _validate_worker_settings(Settings(essay_review_worker_enabled=False))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "essay_review_worker_poll_seconds",
            0.0,
            "ESSAY_REVIEW_WORKER_POLL_SECONDS",
        ),
        (
            "essay_review_worker_poll_seconds",
            31.0,
            "ESSAY_REVIEW_WORKER_POLL_SECONDS",
        ),
        (
            "essay_review_worker_stale_recovery_seconds",
            9.0,
            "ESSAY_REVIEW_WORKER_STALE_RECOVERY_SECONDS",
        ),
        (
            "essay_review_worker_stale_recovery_seconds",
            301.0,
            "ESSAY_REVIEW_WORKER_STALE_RECOVERY_SECONDS",
        ),
    ),
)
def test_essay_worker_rejects_unsafe_loop_settings(
    field: str,
    value: float,
    message: str,
) -> None:
    settings = replace(
        Settings(essay_review_worker_enabled=True),
        **{field: value},
    )

    with pytest.raises(ValueError, match=message):
        _validate_worker_settings(settings)


def test_essay_worker_accepts_bounded_loop_settings() -> None:
    _validate_worker_settings(
        Settings(
            essay_review_worker_enabled=True,
            essay_review_worker_poll_seconds=1.0,
            essay_review_worker_stale_recovery_seconds=60.0,
        )
    )


def test_essay_worker_systemd_unit_uses_formal_module_and_hardening() -> None:
    source = SERVICE_UNIT.read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/gongkao-dazi/worker-essay-review.env" in source
    assert "EnvironmentFile=/etc/gongkao-dazi/api.env" not in source
    assert "EnvironmentFile=/etc/gongkao-dazi/db.env" in source
    assert (
        "ExecStart=/srv/gongkao-dazi/current/backend/.venv/bin/python -m app.workers.essay_reviews"
    ) in source
    assert "NoNewPrivileges=true" in source
    assert "ProtectSystem=strict" in source
    assert "ProtectHome=true" in source
    assert "UMask=0077" in source
