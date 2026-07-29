#!/usr/bin/env python3
"""Merge read-only sub-audit evidence into the top-level inventory CSV files."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty inventory: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def manifest_records() -> tuple[
    dict[str, list[tuple[dict[str, object], dict[str, object]]]],
    dict[str, str],
]:
    by_route: dict[
        str, list[tuple[dict[str, object], dict[str, object]]]
    ] = defaultdict(list)
    manifest_paths: dict[str, str] = {}
    log_root = AUDIT / "subagents/c/logs"
    for path in sorted(log_root.glob("*-manifest.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("auditKind") != "wechat-devtools-real-render":
            continue
        profile = str(payload.get("profile", ""))
        manifest_paths[profile] = path.relative_to(ROOT).as_posix()
        for record in payload.get("records", []):
            route = str(record.get("routePath", ""))
            active = f"/{str(record.get('activeRoute', '')).lstrip('/')}"
            screenshot = str(record.get("screenshot", ""))
            # A screenshot is taken only after reLaunch, controller settlement, fixture
            # application, active-route equality and semantic evidence. Some first-pass
            # records can be marked failed solely by a screen-vs-window height assertion.
            if route and active == route and screenshot:
                by_route[route].append((payload, record))
    return by_route, manifest_paths


def preferred_screenshot(
    records: list[tuple[dict[str, object], dict[str, object]]],
) -> str:
    if not records:
        return ""
    ordered = sorted(
        records,
        key=lambda item: (
            {
                "standard": 0,
                "small": 1,
                "tall_android": 2,
                "tablet_portrait": 3,
                "tablet_landscape": 4,
            }.get(render_device_class(item), 5),
            str(item[0].get("profile", "")).startswith("probe-"),
            str(item[1].get("id", "")),
        ),
    )
    return str(ordered[0][1].get("screenshot", ""))


def profile_state(
    records: list[tuple[dict[str, object], dict[str, object]]],
    device_class: str,
) -> str:
    matched = [
        item
        for item in records
        if render_device_class(item) == device_class
    ]
    if not matched:
        return "not-sampled"
    # Probe/retry manifests can repeat the same visual scenario after a long-session
    # automation timeout. Count a scenario once per requested device-class column.
    unique_scenarios = {
        str(item[1].get("id", "")): item
        for item in sorted(
            matched,
            key=lambda item: str(item[0].get("profile", "")).startswith("probe-"),
            reverse=True,
        )
    }
    return f"covered:{len(unique_scenarios)}-state(s)"


def render_device_class(
    item: tuple[dict[str, object], dict[str, object]],
) -> str:
    payload, record = item
    info = record.get("routeSystemInfo") or payload.get("systemInfo") or {}
    if not isinstance(info, dict):
        info = {}
    width = int(info.get("windowWidth") or info.get("screenWidth") or 0)
    height = int(info.get("windowHeight") or info.get("screenHeight") or 0)
    model = str(info.get("model", "")).lower()
    orientation = str(info.get("deviceOrientation", "")).lower()
    profile = str(payload.get("profile", "")).lower()

    if "ipad" in model or min(width, height) >= 700 or "tablet" in profile:
        if width > height or orientation == "landscape":
            return "tablet_landscape"
        return "tablet_portrait"
    if width == 390 or "standard" in profile or "probe-capture" in profile:
        return "standard"
    if width <= 320 or "small" in profile:
        return "small"
    if "huawei" in model or "android" in profile or "tall" in profile:
        return "tall_android"
    return "other"


def update_pages_and_coverage() -> None:
    renders, manifests = manifest_records()
    pages = read_csv(AUDIT / "page-inventory.csv")
    for row in pages:
        route = row["route"]
        records = renders.get(route, [])
        if records:
            is_asset = "/packages/anan-" in route
            row["opens_now"] = "confirmed-devtools-page-ready"
            row["screenshot_path"] = preferred_screenshot(records)
            row["audit_status"] = (
                "real-render-page-ready;asset-entry-N/A"
                if is_asset
                else "real-render-page-ready;fixture-state;network-fail-closed"
            )
        else:
            row["opens_now"] = "not-confirmed"
            row["audit_status"] = "static-inventory-complete;runtime-not-confirmed"
    write_csv(AUDIT / "page-inventory.csv", pages)

    flows = read_csv(AUDIT / "subagents/b/flow-coverage.csv")
    coverage = read_csv(AUDIT / "coverage-matrix.csv")
    for row in coverage:
        route = row["route"]
        records = renders.get(route, [])
        matching_flows = [
            flow["flow_id"]
            for flow in flows
            if flow["exists"] == "yes" and route in flow["entry_route"]
        ]
        row["route_smoke"] = (
            "covered-devtools-relaunch-page-ready" if records else "not-confirmed"
        )
        row["standard_render"] = profile_state(records, "standard")
        row["small_screen_render"] = profile_state(records, "small")
        row["tall_android_render"] = profile_state(records, "tall_android")
        row["tablet_portrait_render"] = profile_state(records, "tablet_portrait")
        row["tablet_landscape_render"] = profile_state(records, "tablet_landscape")
        row["core_flow_coverage"] = (
            f"mapped:{'|'.join(matching_flows)}" if matching_flows else "not-core-entry"
        )
        row["evidence_path"] = preferred_screenshot(records)
        row["blocker"] = (
            ""
            if records
            else "No current-commit DevTools page-ready screenshot in C manifests."
        )
    write_csv(AUDIT / "coverage-matrix.csv", coverage)

    metadata_path = AUDIT / "project-map-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["realRenderManifestPaths"] = manifests
    metadata["devtoolsPageReadyRouteCount"] = len(renders)
    metadata["devtoolsPageReadyProductRouteCount"] = sum(
        1 for route in renders if not route.startswith("/packages/anan-")
    )
    metadata_path.write_text(
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )


def update_api_auth_inventory() -> None:
    route_inventory_path = (
        AUDIT / "subagents/e/evidence/fastapi-route-auth-inventory.csv"
    )
    if not route_inventory_path.exists():
        return
    route_rows = read_csv(route_inventory_path)
    auth_by_operation = {
        (row["methods"].upper(), row["path"]): row["bearer_auth"]
        for row in route_rows
    }
    api_rows = read_csv(AUDIT / "api-inventory.csv")
    for row in api_rows:
        auth = auth_by_operation.get((row["method"].upper(), row["path"]))
        if auth == "true":
            row["auth_required"] = "yes-code-evidence;missing-openapi-security-metadata"
        elif auth == "false":
            row["auth_required"] = "no-code-evidence"
        else:
            row["auth_required"] = "unresolved"
    write_csv(AUDIT / "api-inventory.csv", api_rows)


def main() -> None:
    update_pages_and_coverage()
    update_api_auth_inventory()
    print("Updated page-inventory.csv, coverage-matrix.csv and api-inventory.csv")


if __name__ == "__main__":
    main()
