#!/usr/bin/env python3
"""Validate the read-only global-audit deliverables.

The validator never writes to the repository. Git changes outside ``audit/``
and ``tools/audit/`` are printed for attribution review, but never affect the
exit status.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CsvSpec:
    relative_path: str
    expected_rows: int
    required_columns: frozenset[str]
    unique_keys: tuple[tuple[str, ...], ...]


CSV_SPECS = (
    CsvSpec(
        relative_path="audit/page-inventory.csv",
        expected_rows=64,
        required_columns=frozenset(
            {
                "page_name",
                "route",
                "package",
                "package_root",
                "functional_domain",
                "template_type",
                "entry_points",
                "required_route_params",
                "shared_components",
                "api_dependencies",
                "login_required",
                "opens_now",
                "screenshot_path",
                "audit_status",
                "visual_scenario_count",
                "interaction_contract",
                "incoming_reference_files",
            }
        ),
        unique_keys=(("route",),),
    ),
    CsvSpec(
        relative_path="audit/component-inventory.csv",
        expected_rows=25,
        required_columns=frozenset(
            {
                "component_name",
                "path",
                "functional_domain",
                "globally_registered",
                "consumer_count",
                "consumers",
                "dependencies",
                "artifact_complete",
                "audit_status",
            }
        ),
        unique_keys=(("path",),),
    ),
    CsvSpec(
        relative_path="audit/api-inventory.csv",
        expected_rows=244,
        required_columns=frozenset(
            {
                "method",
                "path",
                "operation_id",
                "tags",
                "summary",
                "auth_required",
                "request_body",
                "success_codes",
                "error_codes",
                "deprecated",
                "frontend_literal_reference_count",
                "frontend_literal_reference_files",
                "audit_status",
            }
        ),
        unique_keys=(("method", "path"), ("operation_id",)),
    ),
    CsvSpec(
        relative_path="audit/coverage-matrix.csv",
        expected_rows=64,
        required_columns=frozenset(
            {
                "route",
                "functional_domain",
                "package",
                "static_inventory",
                "referenced_by_tests",
                "test_file_count",
                "test_files",
                "visual_scenario_count",
                "existing_visual_fixture",
                "interaction_contract",
                "route_smoke",
                "standard_render",
                "small_screen_render",
                "tall_android_render",
                "tablet_portrait_render",
                "tablet_landscape_render",
                "core_flow_coverage",
                "evidence_path",
                "blocker",
            }
        ),
        unique_keys=(("route",),),
    ),
)

FINDING_REQUIRED_FIELDS = (
    "id",
    "severity",
    "confidence",
    "category",
    "functional_domain",
    "page_or_business_flow",
    "preconditions",
    "reproduction_steps",
    "expected_behavior",
    "actual_behavior",
    "user_impact",
    "evidence",
    "inferred_root_cause",
    "affected_pages_or_modules",
    "recommended_fix_direction",
    "recommended_regression_tests",
    "status",
)

VALID_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
VALID_CONFIDENCES = frozenset({"high", "medium", "low"})
FINDING_LIST_FIELDS = (
    "preconditions",
    "reproduction_steps",
    "evidence",
    "affected_pages_or_modules",
    "recommended_fix_direction",
    "recommended_regression_tests",
    "source_findings",
)
UNRESOLVED_MARKER = re.compile(r"TODO|TBD|PLACEHOLDER", re.IGNORECASE)

C_EXPECTED_COUNTS = {
    "business_scenarios": 88,
    "business_routes": 58,
    "asset_entries": 6,
    "registered_routes": 64,
}

C_PROFILE_EXPECTATIONS = {
    "tablet portrait": {
        "windowWidth": 768,
        "windowHeight": 1024,
        "deviceOrientation": "portrait",
    },
    "tablet landscape": {
        "windowWidth": 1024,
        "windowHeight": 768,
        "deviceOrientation": "landscape",
    },
}

C_COUNT_ALIASES = {
    "business_scenarios": (
        frozenset(
            {
                "businessscenariocount",
                "businessscenarioscount",
                "visualbusinessscenariocount",
                "existingvisualscenariocount",
            }
        ),
        frozenset({"businessscenarios", "visualscenarios"}),
    ),
    "business_routes": (
        frozenset(
            {
                "businessroutecount",
                "businessroutescount",
                "productroutecount",
                "productroutescount",
            }
        ),
        frozenset({"businessroutes", "productroutes"}),
    ),
    "asset_entries": (
        frozenset(
            {
                "assetentrycount",
                "assetentriescount",
                "assetroutecount",
                "assetentryroutecount",
            }
        ),
        frozenset({"assetentries", "assetroutes", "assetentryroutes"}),
    ),
    "registered_routes": (
        frozenset(
            {
                "registeredroutecount",
                "registeredroutescount",
                "totalroutecount",
                "allroutecount",
            }
        ),
        frozenset({"registeredroutes", "allroutes"}),
    ),
}


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.passes: list[str] = []
        self.skips: list[str] = []
        self.infos: list[str] = []

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def passed(self, message: str) -> None:
        self.passes.append(message)

    def skip(self, message: str) -> None:
        self.skips.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)

    def emit(self) -> None:
        for message in self.passes:
            print(f"[PASS] {message}")
        for message in self.skips:
            print(f"[SKIP] {message}")
        for message in self.infos:
            print(f"[INFO] {message}")
        for message in self.errors:
            print(f"[FAIL] {message}", file=sys.stderr)
        print(
            "deliverable validation: "
            f"{len(self.passes)} passed, "
            f"{len(self.skips)} skipped, "
            f"{len(self.errors)} failed"
        )


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help=f"repository root (default: {default_root})",
    )
    parser.add_argument(
        "--c-manifest",
        type=Path,
        default=None,
        help=(
            "explicit aggregate C manifest; otherwise use AUDIT_C_MANIFEST or "
            "a direct *manifest*.json child of audit/subagents/c"
        ),
    )
    return parser.parse_args()


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence, set)):
        return bool(value)
    return True


def read_csv(
    root: Path,
    spec: CsvSpec,
    validation: Validation,
) -> list[dict[str, str]] | None:
    path = root / spec.relative_path
    if not path.is_file():
        validation.fail(f"{spec.relative_path}: file is missing")
        return None

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                validation.fail(f"{spec.relative_path}: CSV header is missing")
                return None
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                validation.fail(f"{spec.relative_path}: duplicate header names")
                return None
            missing_columns = sorted(
                spec.required_columns.difference(reader.fieldnames)
            )
            if missing_columns:
                validation.fail(
                    f"{spec.relative_path}: missing required columns "
                    f"{', '.join(missing_columns)}"
                )
            rows = [
                {key: (value or "").strip() for key, value in row.items()}
                for row in reader
                if any((value or "").strip() for value in row.values())
            ]
    except (OSError, csv.Error, UnicodeError) as error:
        validation.fail(f"{spec.relative_path}: cannot read CSV: {error}")
        return None

    if len(rows) != spec.expected_rows:
        validation.fail(
            f"{spec.relative_path}: expected {spec.expected_rows} data rows, "
            f"found {len(rows)}"
        )

    for key_columns in spec.unique_keys:
        seen: dict[tuple[str, ...], int] = {}
        for row_number, row in enumerate(rows, start=2):
            key = tuple(row.get(column, "").strip() for column in key_columns)
            label = "+".join(key_columns)
            if not all(key):
                validation.fail(
                    f"{spec.relative_path}:{row_number}: empty unique key {label}"
                )
                continue
            first_row = seen.get(key)
            if first_row is not None:
                validation.fail(
                    f"{spec.relative_path}:{row_number}: duplicate {label}={key!r}; "
                    f"first seen at row {first_row}"
                )
            else:
                seen[key] = row_number

    if (
        not any(error.startswith(f"{spec.relative_path}:") for error in validation.errors)
        and len(rows) == spec.expected_rows
    ):
        keys = ", ".join("+".join(key) for key in spec.unique_keys)
        validation.passed(
            f"{spec.relative_path}: {len(rows)} rows, required columns and "
            f"unique key(s) {keys}"
        )
    return rows


def validate_csvs(root: Path, validation: Validation) -> None:
    loaded: dict[str, list[dict[str, str]]] = {}
    for spec in CSV_SPECS:
        rows = read_csv(root, spec, validation)
        if rows is not None:
            loaded[spec.relative_path] = rows

    pages = loaded.get("audit/page-inventory.csv")
    coverage = loaded.get("audit/coverage-matrix.csv")
    if pages is None or coverage is None:
        return
    page_routes = {row["route"] for row in pages}
    coverage_routes = {row["route"] for row in coverage}
    if page_routes != coverage_routes:
        only_pages = sorted(page_routes - coverage_routes)
        only_coverage = sorted(coverage_routes - page_routes)
        validation.fail(
            "page-inventory/coverage-matrix route sets differ: "
            f"only page-inventory={only_pages}, only coverage-matrix={only_coverage}"
        )
    else:
        validation.passed(
            "page-inventory and coverage-matrix contain the same 64 routes"
        )

    for row_number, row in enumerate(coverage, start=2):
        try:
            expected_count = int(row["test_file_count"])
        except ValueError:
            validation.fail(
                f"audit/coverage-matrix.csv:{row_number}: test_file_count is not an integer"
            )
            continue
        actual_count = len(
            [path for path in row["test_files"].split("|") if path.strip()]
        )
        if expected_count != actual_count:
            validation.fail(
                f"audit/coverage-matrix.csv:{row_number}: test_file_count="
                f"{expected_count} but test_files contains {actual_count} paths"
            )

    required_param_rows = [
        row
        for row in pages
        if "!" in row.get("required_route_params", "")
    ]
    if len(required_param_rows) != 17:
        validation.fail(
            "audit/page-inventory.csv: expected 17 routes with required "
            f"parameters, found {len(required_param_rows)}"
        )
    else:
        validation.passed(
            "audit/page-inventory.csv: 17 routes have explicit required "
            "parameter contracts"
        )

    required_param_expectations = {
        "/pages/community-structure/index": "cardId!",
        "/pages/ability-module/index": "track!|moduleKey!",
        "/pages/mock-report/index": "sessionId?",
    }
    pages_by_route = {row["route"]: row for row in pages}
    for route, expected in required_param_expectations.items():
        actual = pages_by_route.get(route, {}).get("required_route_params")
        if actual != expected:
            validation.fail(
                f"audit/page-inventory.csv: {route} required_route_params "
                f"expected {expected!r}, found {actual!r}"
            )
    if all(
        pages_by_route.get(route, {}).get("required_route_params") == expected
        for route, expected in required_param_expectations.items()
    ):
        validation.passed(
            "audit/page-inventory.csv: community, ability-module and "
            "mock-report parameter contracts match audited runtime behavior"
        )

    render_expectations = {
        "standard_render": 88,
        "small_screen_render": 16,
        "tall_android_render": 16,
        "tablet_portrait_render": 88,
        "tablet_landscape_render": 88,
    }
    render_pattern = re.compile(r"^covered:(\d+)-state\(s\)$")
    business_rows = [
        row
        for row in coverage
        if int(row.get("visual_scenario_count") or "0") > 0
    ]
    for column, expected_total in render_expectations.items():
        actual_total = 0
        malformed: list[str] = []
        for row in business_rows:
            value = row.get(column, "")
            if value == "not-sampled":
                continue
            match = render_pattern.fullmatch(value)
            if not match:
                malformed.append(f"{row['route']}={value!r}")
                continue
            actual_total += int(match.group(1))
        if malformed:
            validation.fail(
                f"audit/coverage-matrix.csv: malformed {column} values: "
                + ", ".join(malformed)
            )
        elif actual_total != expected_total:
            validation.fail(
                f"audit/coverage-matrix.csv: {column} covers "
                f"{actual_total} business states, expected {expected_total}"
            )
        else:
            validation.passed(
                f"audit/coverage-matrix.csv: {column} covers "
                f"{actual_total} audited business states"
            )


def load_jsonl(path: Path, validation: Validation) -> list[dict[str, Any]]:
    if not path.is_file():
        validation.fail("audit/findings.jsonl: file is missing")
        return []
    findings: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        validation.fail(f"audit/findings.jsonl: cannot read: {error}")
        return []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            validation.fail(
                f"audit/findings.jsonl:{line_number}: invalid JSON: {error.msg}"
            )
            continue
        if not isinstance(value, dict):
            validation.fail(
                f"audit/findings.jsonl:{line_number}: finding must be an object"
            )
            continue
        value["_audit_line_number"] = line_number
        findings.append(value)
    if not findings:
        validation.fail("audit/findings.jsonl: no findings")
    return findings


def validate_findings(root: Path, validation: Validation) -> None:
    findings_path = root / "audit/findings.jsonl"
    findings = load_jsonl(findings_path, validation)
    seen_ids: dict[str, int] = {}

    for finding in findings:
        line_number = int(finding["_audit_line_number"])
        finding_id = str(finding.get("id", "")).strip()
        prefix = f"audit/findings.jsonl:{line_number}"
        missing = [
            field
            for field in FINDING_REQUIRED_FIELDS
            if field not in finding or not is_nonempty(finding[field])
        ]
        if missing:
            validation.fail(
                f"{prefix}: missing/empty required fields {', '.join(missing)}"
            )

        if finding_id:
            if finding_id in seen_ids:
                validation.fail(
                    f"{prefix}: duplicate id {finding_id!r}; first seen at line "
                    f"{seen_ids[finding_id]}"
                )
            else:
                seen_ids[finding_id] = line_number

        severity = finding.get("severity")
        if severity not in VALID_SEVERITIES:
            validation.fail(
                f"{prefix} ({finding_id or 'unknown id'}): invalid severity "
                f"{severity!r}"
            )
        confidence = finding.get("confidence")
        if confidence not in VALID_CONFIDENCES:
            validation.fail(
                f"{prefix} ({finding_id or 'unknown id'}): invalid confidence "
                f"{confidence!r}"
            )
        if finding.get("ledger_state") != "open":
            validation.fail(
                f"{prefix} ({finding_id or 'unknown id'}): ledger_state must be "
                f"'open', found {finding.get('ledger_state')!r}"
            )
        for field in FINDING_LIST_FIELDS:
            value = finding.get(field)
            if not isinstance(value, list) or not value:
                validation.fail(
                    f"{prefix} ({finding_id or 'unknown id'}): {field} must be "
                    "a non-empty list"
                )

        evidence = finding.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            validation.fail(
                f"{prefix} ({finding_id or 'unknown id'}): evidence must be a "
                "non-empty list"
            )
            continue
        for evidence_index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                validation.fail(
                    f"{prefix} ({finding_id or 'unknown id'}): evidence "
                    f"{evidence_index} must be an object"
                )
                continue
            evidence_path = item.get("path")
            if not isinstance(evidence_path, str) or not evidence_path.strip():
                validation.fail(
                    f"{prefix} ({finding_id or 'unknown id'}): evidence "
                    f"{evidence_index} has no path"
                )
                continue
            claim = item.get("claim")
            if not isinstance(claim, str) or not claim.strip():
                validation.fail(
                    f"{prefix} ({finding_id or 'unknown id'}): evidence "
                    f"{evidence_index} has no claim"
                )
            locator_match = re.match(r"^(.*?):\d+(?::\d+)?$", evidence_path)
            filesystem_path = (
                locator_match.group(1) if locator_match else evidence_path
            )
            resolved = Path(filesystem_path)
            if not resolved.is_absolute():
                resolved = root / resolved
            if not resolved.exists():
                validation.fail(
                    f"{prefix} ({finding_id or 'unknown id'}): evidence path "
                    f"does not exist: {evidence_path}"
                )

    finding_errors = [
        error
        for error in validation.errors
        if error.startswith("audit/findings.jsonl")
    ]
    if findings and not finding_errors:
        evidence_count = sum(len(finding["evidence"]) for finding in findings)
        validation.passed(
            f"audit/findings.jsonl: {len(findings)} unique open findings, "
            f"17 required fields and {evidence_count} existing evidence paths"
        )


def validate_reports(root: Path, validation: Validation) -> None:
    for relative_path in ("audit/summary.md", "audit/repair-plan.md"):
        path = root / relative_path
        if not path.is_file():
            validation.fail(f"{relative_path}: file is missing")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            validation.fail(f"{relative_path}: cannot read: {error}")
            continue
        if not content.strip():
            validation.fail(f"{relative_path}: file is empty")
            continue
        markers = sorted(
            {match.group(0).upper() for match in UNRESOLVED_MARKER.finditer(content)}
        )
        if markers:
            validation.fail(
                f"{relative_path}: unresolved marker(s): {', '.join(markers)}"
            )
            continue
        validation.passed(f"{relative_path}: exists, non-empty, no TODO/TBD/PLACEHOLDER")


def normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def value_as_count(value: Any, *, allow_sequence: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if allow_sequence and isinstance(value, list):
        return len(value)
    return None


def walk_json(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield normalized_key(str(key)), child
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def explicit_manifest_count(data: Any, metric: str) -> tuple[int | None, str | None]:
    scalar_aliases, sequence_aliases = C_COUNT_ALIASES[metric]
    scalar_values: list[int] = []
    sequence_values: list[int] = []
    fallback_values: list[int] = []
    for key, value in walk_json(data):
        if key in scalar_aliases:
            parsed = value_as_count(value, allow_sequence=False)
            if parsed is not None:
                scalar_values.append(parsed)
        if key in sequence_aliases:
            parsed = value_as_count(value, allow_sequence=True)
            if parsed is not None:
                sequence_values.append(parsed)
        if metric == "registered_routes" and key == "uniqueroutecount":
            parsed = value_as_count(value, allow_sequence=False)
            if parsed is not None:
                fallback_values.append(parsed)

    values = scalar_values or sequence_values or fallback_values
    if not values:
        return None, None
    unique_values = sorted(set(values))
    if len(unique_values) != 1:
        return None, f"conflicting {metric} counts: {unique_values}"
    return unique_values[0], None


def record_route(record: Mapping[str, Any]) -> str:
    for key in ("routePath", "path", "route"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            route = value.strip().split("?", 1)[0].split("#", 1)[0]
            return route if route.startswith("/") else f"/{route}"
    return ""


def is_asset_record(record: Mapping[str, Any]) -> bool:
    route = record_route(record)
    kind = str(
        record.get("kind")
        or record.get("routeKind")
        or record.get("entryType")
        or ""
    )
    return (
        "/asset-entry/" in route
        or route.endswith("/asset-entry/index")
        or normalized_key(kind) in {"assetentry", "assetroute"}
    )


def derive_counts_from_records(data: Any) -> dict[str, int] | None:
    records: list[Any] | None = None
    if isinstance(data, dict):
        for key in ("records", "scenarios"):
            value = data.get(key)
            if isinstance(value, list) and value:
                records = value
                break
    if not records or not all(isinstance(record, dict) for record in records):
        return None

    typed_records = [record for record in records if isinstance(record, dict)]
    business_records = [record for record in typed_records if not is_asset_record(record)]
    asset_records = [record for record in typed_records if is_asset_record(record)]

    def scenario_identity(record: Mapping[str, Any], index: int) -> str:
        for key in ("id", "scenarioId", "scenario_id"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"row:{index}"

    business_scenario_ids = {
        scenario_identity(record, index)
        for index, record in enumerate(business_records)
    }
    business_routes = {
        route for record in business_records if (route := record_route(record))
    }
    asset_routes = {
        route for record in asset_records if (route := record_route(record))
    }
    all_routes = business_routes | asset_routes
    return {
        "business_scenarios": len(business_scenario_ids),
        "business_routes": len(business_routes),
        "asset_entries": len(asset_routes),
        "registered_routes": len(all_routes),
    }


def resolve_c_manifests(
    root: Path,
    explicit_manifest: Path | None,
) -> list[Path]:
    if explicit_manifest is not None:
        path = explicit_manifest
        if not path.is_absolute():
            path = root / path
        return [path]

    environment_path = os.environ.get("AUDIT_C_MANIFEST", "").strip()
    if environment_path:
        path = Path(environment_path)
        if not path.is_absolute():
            path = root / path
        return [path]

    c_root = root / "audit/subagents/c"
    preferred = c_root / "manifest.json"
    if preferred.exists():
        return [preferred]
    if not c_root.is_dir():
        return []
    # Per-run manifests under c/logs are intentionally not aggregate delivery
    # manifests. A final C manifest must be a direct child or be selected with
    # --c-manifest / AUDIT_C_MANIFEST.
    return sorted(c_root.glob("*manifest*.json"))


def validate_c_manifest(
    root: Path,
    explicit_manifest: Path | None,
    validation: Validation,
) -> None:
    manifests = resolve_c_manifests(root, explicit_manifest)
    if not manifests:
        validation.skip(
            "aggregate C manifest not present; conditional 88/58/6/64 check skipped"
        )
        return

    for manifest in manifests:
        label = (
            manifest.relative_to(root).as_posix()
            if manifest.is_relative_to(root)
            else str(manifest)
        )
        if not manifest.is_file():
            validation.fail(f"{label}: C manifest is missing or not a file")
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            validation.fail(f"{label}: cannot read C manifest: {error}")
            continue
        if not isinstance(data, dict):
            validation.fail(f"{label}: C manifest root must be an object")
            continue

        derived = derive_counts_from_records(data)
        actual: dict[str, int] = {}
        manifest_failed = False
        for metric, expected in C_EXPECTED_COUNTS.items():
            explicit, conflict = explicit_manifest_count(data, metric)
            if conflict:
                validation.fail(f"{label}: {conflict}")
                manifest_failed = True
                continue
            derived_value = derived.get(metric) if derived is not None else None
            if (
                explicit is not None
                and derived_value is not None
                and explicit != derived_value
            ):
                validation.fail(
                    f"{label}: explicit {metric}={explicit} disagrees with "
                    f"records-derived {derived_value}"
                )
                manifest_failed = True
                continue
            value = explicit if explicit is not None else derived_value
            if value is None:
                validation.fail(
                    f"{label}: cannot determine {metric}; provide an explicit "
                    "count/array or complete records"
                )
                manifest_failed = True
                continue
            actual[metric] = value
            if value != expected:
                validation.fail(
                    f"{label}: expected {metric}={expected}, found {value}"
                )
                manifest_failed = True

        if not manifest_failed and actual == C_EXPECTED_COUNTS:
            validation.passed(
                f"{label}: C manifest counts are 88 business scenarios, "
                "58 business routes, 6 asset entries and 64 registered routes"
            )

        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            validation.fail(
                f"{label}: profiles must be an object containing tablet "
                "portrait and landscape results"
            )
            continue
        profile_values = [
            profile for profile in profiles.values() if isinstance(profile, dict)
        ]
        for profile_label, expected_profile in C_PROFILE_EXPECTATIONS.items():
            matching_profiles = []
            for profile in profile_values:
                system_info = profile.get("systemInfo")
                if not isinstance(system_info, dict):
                    continue
                if all(
                    system_info.get(key) == expected_value
                    for key, expected_value in expected_profile.items()
                ):
                    matching_profiles.append(profile)
            if len(matching_profiles) != 1:
                validation.fail(
                    f"{label}: expected exactly one {profile_label} profile "
                    f"with runtime {expected_profile}, found "
                    f"{len(matching_profiles)}"
                )
                continue
            profile = matching_profiles[0]
            expected_profile_counts = {
                "scenarioCount": 94,
                "businessScenarioCount": 88,
                "assetEntryCount": 6,
                "capturedCount": 94,
                "failedCount": 0,
                "businessRouteCount": 58,
                "registeredRouteCount": 64,
            }
            mismatches = {
                key: (profile.get(key), expected)
                for key, expected in expected_profile_counts.items()
                if profile.get(key) != expected
            }
            records = profile.get("records")
            if not isinstance(records, list) or len(records) != 94:
                mismatches["records"] = (
                    len(records) if isinstance(records, list) else None,
                    94,
                )
            if mismatches:
                validation.fail(
                    f"{label}: {profile_label} profile count mismatch: "
                    f"{mismatches}"
                )
                continue
            missing_screenshots = []
            for record in records:
                if not isinstance(record, dict):
                    missing_screenshots.append("<non-object record>")
                    continue
                screenshot = record.get("screenshot")
                if (
                    not isinstance(screenshot, str)
                    or not screenshot.strip()
                    or not (root / screenshot).is_file()
                ):
                    missing_screenshots.append(str(screenshot))
            if missing_screenshots:
                validation.fail(
                    f"{label}: {profile_label} has "
                    f"{len(missing_screenshots)} missing screenshot artifacts"
                )
                continue
            validation.passed(
                f"{label}: {profile_label} real-render profile is "
                "94/94 captured, 0 failed, with 88 business states, "
                "58 business routes and 6 asset entries"
            )


def report_external_git_status(root: Path, validation: Validation) -> None:
    command = [
        "git",
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude)audit/**",
        ":(exclude)tools/audit/**",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        validation.info(
            f"git status outside audit roots could not run ({error}); "
            "informational only"
        )
        return
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        validation.info(
            "git status outside audit roots failed "
            f"({detail}); informational only"
        )
        return
    status = completed.stdout.rstrip()
    if status:
        validation.info(
            "Git status outside audit/ and tools/audit/ is attribution-only "
            "and does not fail validation:\n"
            f"{status}"
        )
    else:
        validation.info("Git status outside audit/ and tools/audit/: clean")


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    validation = Validation()
    if not (root / ".git").exists():
        validation.fail(f"repository root does not contain .git: {root}")
        validation.emit()
        return 1

    validate_csvs(root, validation)
    validate_findings(root, validation)
    validate_reports(root, validation)
    validate_c_manifest(root, args.c_manifest, validation)
    report_external_git_status(root, validation)
    validation.emit()
    return 1 if validation.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
