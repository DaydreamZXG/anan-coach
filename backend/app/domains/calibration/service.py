from __future__ import annotations

from app.domains.calibration.repository import CalibrationRawRecord, CalibrationRepository
from app.domains.calibration.schemas import (
    AbilityEstimate,
    CalibrationSources,
    ModuleAbilityEstimate,
)

PRACTICE_MEDIUM_CONFIDENCE_MIN_ANSWERS = 30


class CalibrationService:
    def __init__(self, repository: CalibrationRepository) -> None:
        self._repository = repository

    def get_ability_estimate(self, *, user_id: str) -> AbilityEstimate:
        raw = self._repository.get_raw(user_id=user_id)
        score, confidence = _score_and_confidence(raw)
        module_estimates = _module_estimates(raw)
        sources = CalibrationSources(
            baseline=raw.baseline_count,
            practice_answers=raw.practice_answers,
            mock_exams=raw.mock_exams,
        )
        return AbilityEstimate(
            xingce_score=score,
            confidence=confidence,
            status_label=_status_label(confidence),
            data_line=_data_line(sources),
            sources=sources,
            module_estimates=module_estimates,
            latest_mock_reason=raw.latest_mock_reason,
        )


def _score_and_confidence(raw: CalibrationRawRecord) -> tuple[float | None, str]:
    if raw.latest_mock_score is not None:
        return round(raw.latest_mock_score, 1), "high"
    if raw.practice_answers >= PRACTICE_MEDIUM_CONFIDENCE_MIN_ANSWERS:
        accuracy = raw.practice_correct / raw.practice_answers
        return round(accuracy * 100, 1), "medium"
    if raw.baseline_xingce_score is not None:
        return round(raw.baseline_xingce_score, 1), "low"
    return None, "unavailable"


def _status_label(confidence: str) -> str:
    return {
        "high": "高置信校准中",
        "medium": "中置信校准中",
        "low": "低置信校准中",
        "unavailable": "等待校准",
    }[confidence]


def _data_line(sources: CalibrationSources) -> str:
    return (
        f"基于 {sources.baseline} 次摸底/自报 + "
        f"{sources.practice_answers} 题练习 + {sources.mock_exams} 次模考"
    )


def _module_estimates(raw: CalibrationRawRecord) -> list[ModuleAbilityEstimate]:
    if raw.mock_modules:
        return [
            ModuleAbilityEstimate(
                module_name=str(item["moduleName"]),
                accuracy=float(item["accuracy"]),
                sample_size=int(item["total"]),
                source="mock_exam",
            )
            for item in raw.mock_modules
            if "moduleName" in item and "accuracy" in item and "total" in item
        ]
    return [
        ModuleAbilityEstimate(
            module_name=str(item["module_name"]),
            accuracy=(
                round(int(item["correct_count"]) / int(item["sample_size"]), 4)
                if int(item["sample_size"])
                else 0.0
            ),
            sample_size=int(item["sample_size"]),
            source="practice",
        )
        for item in raw.practice_modules
    ]
