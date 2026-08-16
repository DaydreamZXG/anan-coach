from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.teaching_packages.publication.models import CandidateProjection


_ASSET_ID = re.compile(r"^da_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ACCEPTED_REVIEW_STATES = frozenset({"candidate_auto", "verified_sample"})
_SAFE_STRUCTURED_VISUAL_OMISSIONS = frozenset({"evidence", "complexity"})


class CandidateProjectionError(ValueError):
    """A candidate cannot produce a complete answer-free projection."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _literal(value: Any) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        raise CandidateProjectionError("non-finite numeric literal")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    rendered = str(value).strip()
    if not rendered:
        raise CandidateProjectionError("empty literal")
    return rendered


def _text(value: Any) -> str:
    return str(value or "").strip()


class _FactBuilder:
    def __init__(self, asset_id: str) -> None:
        self._asset_id = asset_id
        self._facts: list[dict[str, Any]] = []

    def add(
        self,
        kind: str,
        dimensions: Sequence[Any],
        value: Any,
        unit: Any = "",
    ) -> None:
        rendered_dimensions = tuple(_text(item) for item in dimensions)
        if any(not item for item in rendered_dimensions) or len(rendered_dimensions) > 20:
            raise CandidateProjectionError("invalid fact dimensions")
        rendered_value = _literal(value)
        rendered_unit = _text(unit)
        if len(rendered_value) > 2_000 or len(rendered_unit) > 120:
            raise CandidateProjectionError("fact literal exceeds contract limits")
        self._facts.append(
            {
                "factRef": f"visual:{self._asset_id}:fact:{len(self._facts) + 1:04d}",
                "kind": kind,
                "dimensions": list(rendered_dimensions),
                "value": rendered_value,
                "unit": rendered_unit,
            }
        )

    @property
    def facts(self) -> list[dict[str, Any]]:
        if not self._facts:
            raise CandidateProjectionError("candidate produced no facts")
        if len(self._facts) > 5_000:
            raise CandidateProjectionError("candidate produced too many facts")
        return self._facts


def _validate_candidate(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    if candidate.get("schema_version") != "data-analysis-chart-candidate-v1":
        raise CandidateProjectionError("unsupported candidate schema")
    asset_id = _text(candidate.get("asset_id"))
    if _ASSET_ID.fullmatch(asset_id) is None:
        raise CandidateProjectionError("invalid asset identity")
    source = candidate.get("source")
    if not isinstance(source, Mapping):
        raise CandidateProjectionError("source identity is missing")
    source_sha = _text(source.get("sha256"))
    if _SHA256.fullmatch(source_sha) is None:
        raise CandidateProjectionError("invalid source SHA-256")
    occurrences = source.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        raise CandidateProjectionError("source occurrences are missing")
    if candidate.get("review_status") not in _ACCEPTED_REVIEW_STATES:
        raise CandidateProjectionError("candidate review state is ineligible")
    verification = candidate.get("verification")
    if not isinstance(verification, Mapping) or verification.get("schema_valid") is not True:
        raise CandidateProjectionError("candidate verification is incomplete")
    if verification.get("missing_values_preserved") is not True:
        raise CandidateProjectionError("missing values were not preserved")
    chart_type = _text(candidate.get("chart_type"))
    if not chart_type or chart_type == "other_unsupported":
        raise CandidateProjectionError("unsupported chart type")
    return asset_id, source_sha, chart_type


def _add_header_facts(
    facts: _FactBuilder, *, title: str, unit: str
) -> None:
    if title:
        facts.add("title", (), title)
    if unit:
        facts.add("unit", (), unit)


def _project_table(
    facts: _FactBuilder,
    table: Mapping[str, Any],
    *,
    global_unit: str,
) -> None:
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not columns or not isinstance(rows, list) or not rows:
        raise CandidateProjectionError("table is incomplete")
    empty_indexes = tuple(index for index, column in enumerate(columns) if not _text(column))
    rendered_columns = tuple(
        _text(column) or f"column-{index + 1}"
        for index, column in enumerate(columns)
    )
    for column in rendered_columns:
        facts.add("legend", (column,), column)
    for row in rows:
        if not isinstance(row, Mapping):
            raise CandidateProjectionError("table row is not an object")
        label = _text(row.get("label"))
        values = row.get("values")
        if not label or not isinstance(values, list):
            raise CandidateProjectionError("table row width does not match columns")
        row_columns = rendered_columns
        if len(values) != len(row_columns) and len(values) == len(row_columns) - len(empty_indexes):
            # A blank leading corner header is sometimes serialized as a column
            # and sometimes only as the visual row-header cell. Both are valid;
            # align by the explicit empty positions instead of inventing values.
            row_columns = tuple(
                column for index, column in enumerate(row_columns) if index not in empty_indexes
            )
        if len(values) != len(row_columns):
            raise CandidateProjectionError("table row width does not match columns")
        row_unit = _text(row.get("unit")) or global_unit
        for column, value in zip(row_columns, values, strict=True):
            facts.add("table_cell", (label, column), value, row_unit)


def _project_pie(
    facts: _FactBuilder,
    pie_geometry: Mapping[str, Any],
    *,
    global_unit: str,
) -> None:
    slices = pie_geometry.get("slices")
    if not isinstance(slices, list) or not slices:
        raise CandidateProjectionError("pie geometry has no slices")
    for index, item in enumerate(slices):
        if not isinstance(item, Mapping):
            raise CandidateProjectionError("pie slice is not an object")
        label = _text(item.get("label")) or f"slice-{index + 1}"
        share = item.get("share")
        if not isinstance(share, (int, float)) or isinstance(share, bool):
            raise CandidateProjectionError("pie share is not numeric")
        facts.add("series_value", (label,), share * 100, global_unit or "%")
        geometry = {
            "startAngle": item.get("start_angle"),
            "endAngle": item.get("end_angle"),
            "sweepAngle": item.get("sweep_angle"),
        }
        facts.add("relationship", (label, "geometry"), geometry, "degrees")


def _project_series(
    facts: _FactBuilder,
    candidate: Mapping[str, Any],
    *,
    global_unit: str,
) -> None:
    categories_raw = candidate.get("categories")
    series_raw = candidate.get("series")
    if not isinstance(categories_raw, list) or not isinstance(series_raw, list) or not series_raw:
        raise CandidateProjectionError("chart series are incomplete")
    categories = tuple(_text(item) for item in categories_raw)
    if any(not item for item in categories):
        raise CandidateProjectionError("chart contains an empty category")
    for category in categories:
        facts.add("category", (category,), category)
    for index, series in enumerate(series_raw):
        if not isinstance(series, Mapping):
            raise CandidateProjectionError("series is not an object")
        label = _text(series.get("label")) or _text(series.get("id")) or f"series-{index + 1}"
        values = series.get("values")
        if not isinstance(values, list):
            raise CandidateProjectionError("series values are missing")
        if categories and len(values) != len(categories):
            raise CandidateProjectionError("series width does not match categories")
        dimensions = categories or tuple(str(item + 1) for item in range(len(values)))
        unit = _text(series.get("unit")) or global_unit
        facts.add("legend", (label,), label)
        for category, value in zip(dimensions, values, strict=True):
            facts.add("series_value", (label, category), value, unit)


def _project_axes(facts: _FactBuilder, axes: Any) -> None:
    if not isinstance(axes, Mapping):
        return
    x_axis = axes.get("x")
    if isinstance(x_axis, Mapping):
        facts.add("axis", ("x",), dict(x_axis), _text(x_axis.get("unit")))
    y_axes = axes.get("y")
    if isinstance(y_axes, list):
        for index, axis in enumerate(y_axes):
            if isinstance(axis, Mapping):
                facts.add(
                    "axis",
                    ("y", str(index + 1)),
                    dict(axis),
                    _text(axis.get("unit")),
                )


def _flatten_structured_visual(
    facts: _FactBuilder,
    value: Any,
    *,
    path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value):
            if key in _SAFE_STRUCTURED_VISUAL_OMISSIONS:
                continue
            _flatten_structured_visual(facts, value[key], path=(*path, str(key)))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _flatten_structured_visual(facts, item, path=(*path, str(index)))
        return
    if not path:
        raise CandidateProjectionError("structured visual contains no semantic path")
    facts.add("relationship", path, value)


def project_candidate(candidate: Mapping[str, Any]) -> CandidateProjection:
    """Project one schema-validated candidate into immutable answer-free facts.

    A present table is the declared authority for numeric literals. This is
    intentional: the batch contains candidates whose OCR series conflicts with
    their later crop-repaired table. Publishing both would create two truths.
    """

    asset_id, source_sha, chart_type = _validate_candidate(candidate)
    facts = _FactBuilder(asset_id)
    title = _text(candidate.get("title"))
    unit = _text(candidate.get("unit"))
    _add_header_facts(facts, title=title, unit=unit)

    table = candidate.get("table")
    pie_geometry = candidate.get("pie_geometry")
    structured_visual = candidate.get("structured_visual")
    if isinstance(table, Mapping):
        authority = "table"
        _project_table(facts, table, global_unit=unit)
    elif isinstance(pie_geometry, Mapping):
        authority = "pie_geometry"
        _project_pie(facts, pie_geometry, global_unit=unit)
    elif isinstance(structured_visual, Mapping):
        authority = "structured_visual"
        _flatten_structured_visual(facts, structured_visual)
    else:
        authority = "series"
        _project_axes(facts, candidate.get("axes"))
        _project_series(facts, candidate, global_unit=unit)

    payload = {"chartType": chart_type, "facts": facts.facts}
    return CandidateProjection(
        evidence_ref=f"structured-visual:{asset_id}",
        source_asset_sha256=source_sha,
        source_payload_sha256=canonical_sha256(candidate),
        chart_type=chart_type,
        payload=payload,
        authority=authority,
    )
