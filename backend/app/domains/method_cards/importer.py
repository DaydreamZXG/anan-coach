from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import yaml

from app.domains.method_cards.repository import MethodCardRecord, MethodCardRepository


class MethodCardImportError(ValueError):
    pass


@dataclass(frozen=True)
class MethodCardImportResult:
    file_count: int
    card_count: int
    card_ids: list[str]


_CARD_ID_RE = re.compile(r"^mc_[a-z0-9_]+$")
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_YAML_SUFFIXES = {".yaml", ".yml"}
_IMMUTABLE_RELEASE_SQLSTATE = "23514"
_IMMUTABLE_RELEASE_MESSAGE = (
    "method cards referenced by published knowledge releases are immutable"
)


def import_method_cards(
    *,
    session: Session,
    paths: Iterable[Path],
) -> MethodCardImportResult:
    yaml_files = _collect_yaml_files(paths)
    cards: list[MethodCardRecord] = []
    for yaml_file in yaml_files:
        cards.extend(_load_cards_from_file(yaml_file))
    _reject_duplicate_ids(cards)

    try:
        MethodCardRepository(session).upsert_cards(cards)
    except IntegrityError as exc:
        if not _is_released_method_card_drift(exc):
            raise
        session.rollback()
        raise MethodCardImportError(
            "Method-card import contains same-ID content drift for a card "
            "referenced by an active or retired knowledge release"
        ) from exc
    return MethodCardImportResult(
        file_count=len(yaml_files),
        card_count=len(cards),
        card_ids=[card.id for card in cards],
    )


def _is_released_method_card_drift(error: IntegrityError) -> bool:
    return (
        getattr(error.orig, "sqlstate", None) == _IMMUTABLE_RELEASE_SQLSTATE
        and _IMMUTABLE_RELEASE_MESSAGE in str(error.orig)
    )


def _collect_yaml_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = raw_path.expanduser().resolve()
        if not path.exists():
            raise MethodCardImportError(f"Method-card path does not exist: {path}")
        if path.is_dir():
            files.extend(
                item
                for item in sorted(path.rglob("*"))
                if item.is_file()
                and item.suffix.lower() in _YAML_SUFFIXES
                and not item.name.endswith(".example.yaml")
                and not item.name.endswith(".example.yml")
            )
        elif path.suffix.lower() in _YAML_SUFFIXES:
            files.append(path)
        else:
            raise MethodCardImportError(f"Method-card path is not YAML: {path}")

    if not files:
        raise MethodCardImportError("No method-card YAML files found")
    return sorted(files)


def _load_cards_from_file(path: Path) -> list[MethodCardRecord]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MethodCardImportError(f"{path}: invalid YAML") from exc

    if isinstance(raw, dict):
        documents = [raw]
    elif isinstance(raw, list):
        if not raw:
            raise MethodCardImportError(f"{path}: expected at least one method card")
        documents = raw
    else:
        raise MethodCardImportError(f"{path}: expected a YAML object or list")

    cards: list[MethodCardRecord] = []
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            raise MethodCardImportError(f"{path} item {index}: expected an object")
        cards.append(_validate_card(item, source=f"{path} item {index}"))
    return cards


def _reject_duplicate_ids(cards: list[MethodCardRecord]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for card in cards:
        if card.id in seen:
            duplicates.add(card.id)
        seen.add(card.id)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise MethodCardImportError(f"Duplicate method-card id(s): {duplicate_list}")


def _validate_card(data: dict[str, Any], *, source: str) -> MethodCardRecord:
    card_id = _required_string(data, "id", source)
    if not _CARD_ID_RE.fullmatch(card_id):
        raise MethodCardImportError(f"{source}: id must match mc_[a-z0-9_]+")

    exam_scope = _required_string(data, "exam_scope", source)
    if exam_scope != "guokao":
        raise MethodCardImportError(f"{source}: exam_scope must be guokao")

    track = _required_string(data, "track", source)
    if track not in {"xingce", "shenlun"}:
        raise MethodCardImportError(f"{source}: track must be xingce or shenlun")

    status = _required_string(data, "status", source)
    if status not in {"draft", "published"}:
        raise MethodCardImportError(f"{source}: status must be draft or published")

    method_steps = _required_string_list(data, "method_steps", source)
    if len(method_steps) > 5:
        raise MethodCardImportError(f"{source}: method_steps must contain at most 5 items")

    error_signatures = _required_error_signatures(data, source)
    validation = _required_validation(data, source=source, status=status)

    return MethodCardRecord(
        id=card_id,
        exam_scope=exam_scope,
        track=track,
        module_name=_required_string(data, "module_name", source),
        tag_path=_required_string(data, "tag_path", source),
        title=_required_string(data, "title", source),
        recognition=_required_string(data, "recognition", source),
        applicability=_required_applicability(data, source),
        method_steps=method_steps,
        error_signatures=error_signatures,
        fix_action=_required_string(data, "fix_action", source),
        time_budget_sec=_positive_int(data, "time_budget_sec", source),
        accuracy_rule=_required_string(data, "accuracy_rule", source),
        pitfall=_optional_string(data, "pitfall", source),
        author_note=_required_string(data, "author_note", source),
        validation=validation,
        priority=_priority(data, source),
        status=status,
        knowledge_nodes=_knowledge_nodes(data, source),
    )


def _required_string(data: dict[str, Any], field: str, source: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MethodCardImportError(f"{source}: {field} must be a non-empty string")
    return value.strip()


def _optional_string(data: dict[str, Any], field: str, source: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MethodCardImportError(f"{source}: {field} must be null or a non-empty string")
    return value.strip()


def _knowledge_nodes(data: dict[str, Any], source: str) -> list[str]:
    value = data.get("knowledge_nodes")
    if value is None:
        return []
    if not isinstance(value, list):
        raise MethodCardImportError(f"{source}: knowledge_nodes must be a list or null")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise MethodCardImportError(
                f"{source}: knowledge_nodes[{index}] must be a non-empty string"
            )
        result.append(item.strip())
    return result


def _positive_int(data: dict[str, Any], field: str, source: str) -> int:
    value = data.get(field)
    if not isinstance(value, int) or value <= 0:
        raise MethodCardImportError(f"{source}: {field} must be a positive integer")
    return value


def _priority(data: dict[str, Any], source: str) -> int:
    value = _positive_int(data, "priority", source)
    if value > 5:
        raise MethodCardImportError(f"{source}: priority must be between 1 and 5")
    return value


def _required_string_list(
    data: dict[str, Any],
    field: str,
    source: str,
) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list) or not value:
        raise MethodCardImportError(f"{source}: {field} must be a non-empty list")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise MethodCardImportError(
                f"{source}: {field}[{index}] must be a non-empty string"
            )
        result.append(item.strip())
    return result


def _required_applicability(
    data: dict[str, Any],
    source: str,
) -> dict[str, list[str]]:
    value = data.get("applicability")
    if not isinstance(value, dict):
        raise MethodCardImportError(f"{source}: applicability must be an object")
    include = _string_list_value(value, "include", source)
    exclude = _string_list_value(value, "exclude", source)
    return {"include": include, "exclude": exclude}


def _string_list_value(
    data: dict[str, Any],
    field: str,
    source: str,
) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list):
        raise MethodCardImportError(f"{source}: applicability.{field} must be a list")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise MethodCardImportError(
                f"{source}: applicability.{field}[{index}] must be a non-empty string"
            )
        result.append(item.strip())
    return result


def _required_error_signatures(
    data: dict[str, Any],
    source: str,
) -> list[dict[str, str]]:
    value = data.get("error_signatures")
    if not isinstance(value, list) or not value:
        raise MethodCardImportError(f"{source}: error_signatures must be a non-empty list")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise MethodCardImportError(
                f"{source}: error_signatures[{index}] must be an object"
            )
        code = _required_string(item, "code", f"{source}: error_signatures[{index}]")
        if not _SNAKE_RE.fullmatch(code):
            raise MethodCardImportError(
                f"{source}: error_signatures[{index}].code must be snake_case"
            )
        description = _required_string(
            item,
            "description",
            f"{source}: error_signatures[{index}]",
        )
        result.append({"code": code, "description": description})
    return result


def _required_validation(
    data: dict[str, Any],
    *,
    source: str,
    status: str,
) -> dict[str, Any]:
    value = data.get("validation")
    if not isinstance(value, dict):
        raise MethodCardImportError(f"{source}: validation must be an object")

    tested_count = _non_negative_int(value, "tested_count", f"{source}: validation")
    passed_count = _non_negative_int(value, "passed_count", f"{source}: validation")
    if passed_count > tested_count:
        raise MethodCardImportError(
            f"{source}: validation.passed_count cannot exceed tested_count"
        )

    sample_question_ids = _list_value(value, "sample_question_ids", f"{source}: validation")
    reviewer = value.get("reviewer")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        raise MethodCardImportError(
            f"{source}: validation.reviewer must be null or a non-empty string"
        )
    reviewed_at = _reviewed_at_value(value.get("reviewed_at"))
    if value.get("reviewed_at") is not None and reviewed_at is None:
        raise MethodCardImportError(
            f"{source}: validation.reviewed_at must be null, a date, or a non-empty string"
        )

    if status == "published":
        if tested_count < 10 or passed_count < 8:
            raise MethodCardImportError(
                f"{source}: published cards require tested_count >= 10 and passed_count >= 8"
            )
        if not sample_question_ids:
            raise MethodCardImportError(
                f"{source}: published cards require sample_question_ids"
            )
        if not reviewer or not isinstance(reviewer, str) or not reviewer.strip():
            raise MethodCardImportError(f"{source}: published cards require reviewer")
        if not reviewed_at:
            raise MethodCardImportError(f"{source}: published cards require reviewed_at")

    return {
        "tested_count": tested_count,
        "passed_count": passed_count,
        "sample_question_ids": sample_question_ids,
        "reviewer": reviewer.strip() if isinstance(reviewer, str) else None,
        "reviewed_at": reviewed_at,
    }


def _reviewed_at_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _non_negative_int(data: dict[str, Any], field: str, source: str) -> int:
    value = data.get(field)
    if not isinstance(value, int) or value < 0:
        raise MethodCardImportError(f"{source}: {field} must be a non-negative integer")
    return value


def _list_value(data: dict[str, Any], field: str, source: str) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list):
        raise MethodCardImportError(f"{source}: {field} must be a list")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise MethodCardImportError(
                f"{source}: {field}[{index}] must be a non-empty string"
            )
        result.append(item.strip())
    return result
