from __future__ import annotations

from pathlib import Path


def test_primary_execution_paths_use_progression_owner() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "domains"
    checked = [
        root / "practice" / "router.py",
        root / "mock_exam" / "service.py",
        root / "review_session" / "service.py",
        root / "docket" / "service.py",
    ]

    for path in checked:
        source = path.read_text()
        assert "ProgressionService" in source
        assert "RankService(" not in source
        assert "get_rank_service" not in source


def test_retired_essay_writer_cannot_bypass_v2_completion_event() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "domains"
    legacy_router = (root / "essay" / "router.py").read_text()
    review_worker = (
        root / "essay_intelligence" / "review_worker.py"
    ).read_text()

    assert "essay_v1_write_retired" in legacy_router
    assert "ProgressionService" not in legacy_router
    assert "RankService(" not in legacy_router
    assert 'event_type=f"essay_review.{response.status}"' in review_worker
    assert 'event_type="essay_review.failed"' in review_worker
