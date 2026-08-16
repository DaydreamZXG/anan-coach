#!/usr/bin/env python3
"""Reconcile and publish structured data-analysis visual evidence.

``dry-run`` is the default and writes an exact content-addressed plan. ``apply``
requires that plan SHA, then writes all versions/bindings/receipt in one database
transaction. ``verify`` proves the active rows still match the current content
heads. ``rollback`` withdraws only an exact batch and retains immutable versions.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication import (
    PublicationPlan,
    RuntimeAsset,
    RuntimeQuestion,
    StructuredVisualPublicationRepository,
    build_publication_plan,
)
from app.domains.teaching_packages.publication.models import (
    PublicationBinding,
    PublicationPlanItem,
    PublicationReasonCode,
)
from app.domains.teaching_packages.publication.repository import publication_plan_sha256


DEFAULT_BATCH_ID = "data-analysis-structured-visual-20260813-v1"
DEFAULT_STRUCTURED_DIR = Path("reports/data-analysis-batch/structured")
DEFAULT_SCHEMA_PATH = Path("reports/data-analysis-batch/structured-schema.json")
DEFAULT_PLAN_PATH = Path("reports/data-analysis-structured-visual-publication-plan.json")
DEFAULT_REPORT_PATH = Path("reports/2026-08-13-data-analysis-evidence-publication.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("dry-run", "apply", "verify", "rollback"), default="dry-run"
    )
    parser.add_argument("--database-url", default=os.getenv("QBANK_IMPORT_DATABASE_URL"))
    parser.add_argument("--structured-dir", type=Path, default=DEFAULT_STRUCTURED_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--tos-hash-cache", type=Path)
    parser.add_argument("--tos-bucket", default=os.getenv("TOS_BUCKET", "gongkao-dazi-prod"))
    parser.add_argument("--tos-region", default=os.getenv("TOS_REGION", "cn-guangzhou"))
    parser.add_argument("--tos-endpoint", default=os.getenv("TOS_ENDPOINT"))
    parser.add_argument("--tos-access-key", default=os.getenv("TOS_ACCESS_KEY"))
    parser.add_argument("--tos-secret-key", default=os.getenv("TOS_SECRET_KEY"))
    parser.add_argument("--tos-workers", type=int, default=8)
    return parser


def _require(args: argparse.Namespace) -> None:
    if not args.database_url:
        raise SystemExit("--database-url or QBANK_IMPORT_DATABASE_URL is required")
    if args.mode in {"apply", "verify"} and not args.expected_plan_sha256:
        raise SystemExit("--expected-plan-sha256 from dry-run is required")
    if args.tos_workers <= 0:
        raise SystemExit("--tos-workers must be positive")


def _load_candidates(
    structured_dir: Path, schema_path: Path
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, ...]]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    candidates: list[dict[str, Any]] = []
    invalid: dict[str, tuple[str, ...]] = {}
    for path in sorted(structured_dir.glob("*.json")):
        if path.name.startswith("._"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = _candidate_schema_errors(payload, schema)
        if errors:
            invalid[str(payload.get("asset_id") or path.stem)] = errors
        candidates.append(payload)
    return candidates, invalid


def _candidate_schema_errors(
    payload: Any, schema: dict[str, Any]
) -> tuple[str, ...]:
    """Dependency-free closed-root validation for this frozen batch contract.

    Variant internals are subsequently validated by the deterministic projector;
    this check proves the candidate is the declared closed v1 envelope instead of
    trusting its self-reported ``verification.schema_valid`` flag alone.
    """

    if not isinstance(payload, dict):
        return ("root: expected object",)
    errors: list[str] = []
    required = set(schema.get("required") or ())
    properties = set((schema.get("properties") or {}).keys())
    missing = sorted(required - set(payload))
    extras = sorted(set(payload) - properties)
    errors.extend(f"root: missing {key}" for key in missing)
    errors.extend(f"root: unexpected {key}" for key in extras)
    if payload.get("schema_version") != "data-analysis-chart-candidate-v1":
        errors.append("schema_version: unsupported")
    if payload.get("chart_type") not in {
        "grouped_bar", "stacked_bar", "line", "combo", "pie_donut",
        "scatter", "table", "structured_visual",
    }:
        errors.append("chart_type: unsupported")
    confidence = payload.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        errors.append("confidence: expected number from 0 to 1")
    source = payload.get("source")
    if not isinstance(source, dict):
        errors.append("source: expected object")
    else:
        source_required = {"sha256", "local_path", "occurrences"}
        source_allowed = source_required
        errors.extend(f"source: missing {key}" for key in sorted(source_required - set(source)))
        errors.extend(f"source: unexpected {key}" for key in sorted(set(source) - source_allowed))
        if not isinstance(source.get("occurrences"), list) or not source.get("occurrences"):
            errors.append("source.occurrences: expected non-empty array")
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification: expected object")
    elif verification.get("schema_valid") is not True:
        errors.append("verification.schema_valid: expected true")
    return tuple(sorted(errors))


def _load_hash_cache(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, list) and len(item) == 2:
            result[str(item[0])] = str(item[1])
        elif isinstance(item, dict):
            result[str(item["objectKey"])] = str(item["sha256"])
    return result


def _read_tos_object(client: Any, bucket: str, object_key: str) -> bytes:
    response = client.get_object(bucket, object_key)
    try:
        chunks: list[bytes] = []
        while chunk := response.read(1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _hash_object_keys(args: argparse.Namespace, keys: set[str]) -> dict[str, str]:
    hashes = _load_hash_cache(args.tos_hash_cache)
    missing = sorted(key for key in keys if key not in hashes)
    if not missing:
        return hashes
    required = (
        args.tos_bucket,
        args.tos_region,
        args.tos_endpoint,
        args.tos_access_key,
        args.tos_secret_key,
    )
    if not all(required):
        raise SystemExit("TOS credentials are required when the hash cache is incomplete")
    import tos

    def digest(key: str) -> tuple[str, str]:
        client = tos.TosClientV2(
            args.tos_access_key,
            args.tos_secret_key,
            args.tos_endpoint,
            args.tos_region,
        )
        return key, hashlib.sha256(_read_tos_object(client, args.tos_bucket, key)).hexdigest()

    with ThreadPoolExecutor(max_workers=args.tos_workers) as executor:
        futures = {executor.submit(digest, key): key for key in missing}
        for future in as_completed(futures):
            key = futures[future]
            try:
                _, value = future.result()
            except Exception:
                # A missing/unreadable object quarantines only the affected
                # candidate through a content-hash mismatch. It must not hide
                # the eligibility of every other immutable asset.
                hashes[key] = ""
            else:
                hashes[key] = value
    return hashes


def _fetch_runtime(
    session: Session,
    *,
    source_keys: set[str],
) -> tuple[
    dict[str, str],
    dict[str, RuntimeQuestion],
    dict[str, list[tuple[str, str]]],
]:
    occurrence_map = {
        str(row["source_key"]): str(row["question_id"])
        for row in session.execute(
            text(
                """
                select occurrence.source_occurrence_question_key as source_key,
                       occurrence.question_id
                from product.question_occurrences occurrence
                where occurrence.source_occurrence_question_key = any(
                  cast(:source_keys as text[])
                )
                order by occurrence.source_occurrence_question_key,
                         occurrence.canonical_occurrence_rank nulls last,
                         occurrence.occurrence_id
                """
            ),
            {"source_keys": sorted(source_keys)},
        ).mappings()
        if row["source_key"] and row["question_id"]
    }
    question_ids = sorted(set(occurrence_map.values()))
    questions: dict[str, RuntimeQuestion] = {}
    if question_ids:
        for row in session.execute(
            text(
                """
                select question.question_id, question.status,
                       head.content_version_id,
                       (question.answer = 'A' and (
                         question.stem ilike '%default A%'
                         or question.stem ilike '%默认A%'
                         or question.stem ilike '%正在全力以赴征集%'
                       )) as placeholder
                from product.questions question
                left join product.question_content_heads head
                  on head.question_id = question.question_id
                where question.question_id = any(cast(:question_ids as text[]))
                """
            ),
            {"question_ids": question_ids},
        ).mappings():
            questions[str(row["question_id"])] = RuntimeQuestion(
                question_id=str(row["question_id"]),
                status=str(row["status"]),
                content_version_id=(
                    str(row["content_version_id"])
                    if row["content_version_id"] is not None
                    else None
                ),
                placeholder=bool(row["placeholder"]),
            )
    assets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    if question_ids:
        for row in session.execute(
            text(
                """
                select asset.question_id, asset.asset_id,
                       nullif(btrim(asset.object_key), '') as object_key
                from product.question_assets asset
                where asset.question_id = any(cast(:question_ids as text[]))
                  and nullif(btrim(asset.object_key), '') is not null
                order by asset.question_id, asset.asset_id
                """
            ),
            {"question_ids": question_ids},
        ).mappings():
            assets[str(row["question_id"])].append(
                (str(row["asset_id"]), str(row["object_key"]))
            )
    return occurrence_map, questions, assets


def _source_keys(candidates: list[dict[str, Any]]) -> set[str]:
    return {
        str(occurrence["question_id"])
        for candidate in candidates
        for occurrence in candidate["source"]["occurrences"]
        if occurrence.get("question_id")
    }


def _runtime_assets(
    raw_assets: dict[str, list[tuple[str, str]]], object_hashes: dict[str, str]
) -> dict[str, tuple[RuntimeAsset, ...]]:
    return {
        question_id: tuple(
            RuntimeAsset(
                asset_id=asset_id,
                question_id=question_id,
                object_key=object_key,
                content_sha256=object_hashes.get(object_key),
            )
            for asset_id, object_key in assets
        )
        for question_id, assets in raw_assets.items()
    }


def _item_from_json(item: dict[str, Any]) -> PublicationPlanItem:
    diagnostics = item.get("diagnostics") or {}
    return PublicationPlanItem(
        batch_id=str(item["batchId"]),
        evidence_ref=str(item["evidenceRef"]),
        source_asset_sha256=str(item["sourceAssetSha256"]),
        source_payload_sha256=str(item["sourcePayloadSha256"]),
        verification_policy_version=str(item["verificationPolicyVersion"]),
        payload=dict(item["payload"]),
        bindings=tuple(
            PublicationBinding(
                question_id=str(binding["questionId"]),
                content_version_id=str(binding["contentVersionId"]),
                runtime_asset_id=str(binding["runtimeAssetId"]),
            )
            for binding in item["bindings"]
        ),
        quarantine_reasons=tuple(
            PublicationReasonCode(reason) for reason in item["quarantineReasons"]
        ),
        unresolved_occurrence_keys=tuple(diagnostics.get("unresolvedOccurrenceKeys") or ()),
        rejected_question_ids=tuple(diagnostics.get("rejectedQuestionIds") or ()),
        authority=str(diagnostics.get("authority") or ""),
    )


def _load_plan(path: Path) -> PublicationPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PublicationPlan(
        batch_id=str(payload["batchId"]),
        policy_version=str(payload["verificationPolicyVersion"]),
        items=tuple(_item_from_json(item) for item in payload["items"]),
    )


def _plan_payload(plan: PublicationPlan) -> dict[str, Any]:
    payload = plan.as_contract()
    for output, item in zip(payload["items"], plan.items, strict=True):
        output["diagnostics"] = {
            "authority": item.authority,
            "unresolvedOccurrenceKeys": list(item.unresolved_occurrence_keys),
            "rejectedQuestionIds": list(item.rejected_question_ids),
        }
    payload["planSha256"] = publication_plan_sha256(plan)
    return payload


def _write_plan(path: Path, plan: PublicationPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_plan_payload(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_report(
    path: Path,
    *,
    plan: PublicationPlan,
    schema_invalid: dict[str, tuple[str, ...]],
    mode: str,
) -> None:
    partial = tuple(
        item
        for item in plan.publishable_items
        if item.unresolved_occurrence_keys or item.rejected_question_ids
    )
    authority_counts: dict[str, int] = defaultdict(int)
    for item in plan.publishable_items:
        authority_counts[item.authority] += 1
    lines = [
        "# 资料分析结构化证据全量接入报告",
        "",
        f"- 模式：`{mode}`",
        f"- 批次：`{plan.batch_id}`",
        f"- 发布策略：`{plan.policy_version}`",
        f"- 计划 SHA-256：`{publication_plan_sha256(plan)}`",
        f"- 结构化资产：{len(plan.items):,}",
        f"- 可发布资产：{len(plan.publishable_items):,}",
        f"- 当前题目绑定：{plan.binding_count:,}",
        f"- 隔离资产：{len(plan.quarantined_items):,}",
        f"- 有效绑定但含旧来源残留：{len(partial):,}",
        f"- Schema 无效：{len(schema_invalid):,}",
        "",
        "## 事实主权",
        "",
    ]
    for authority, count in sorted(authority_counts.items()):
        lines.append(f"- `{authority}`：{count:,}")
    lines.extend(["", "## 隔离清单", ""])
    if not plan.quarantined_items:
        lines.append("- 无")
    else:
        for item in plan.quarantined_items:
            reasons = ", ".join(reason.value for reason in item.quarantine_reasons)
            lines.append(f"- `{item.evidence_ref}`：`{reasons}`")
    lines.extend(["", "## 保留绑定的旧来源残留", ""])
    if not partial:
        lines.append("- 无")
    else:
        for item in partial:
            lines.append(
                f"- `{item.evidence_ref}`：有效绑定 {len(item.bindings)}；"
                f"未解析旧来源 {len(item.unresolved_occurrence_keys)}；"
                f"拒绝题目 {len(item.rejected_question_ids)}"
            )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "批次原文件未修改。发布层只保存答案无关事实；题文、答案、图片、卷属和用户数据均未改写。表格存在时以表格为事实主权，避免将已知的 series/table 冲突同时发布。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_plan(args: argparse.Namespace, session: Session) -> tuple[PublicationPlan, dict[str, tuple[str, ...]]]:
    candidates, invalid = _load_candidates(args.structured_dir, args.schema)
    occurrence_map, questions, raw_assets = _fetch_runtime(
        session, source_keys=_source_keys(candidates)
    )
    object_keys = {
        object_key for assets in raw_assets.values() for _, object_key in assets
    }
    hashes = _hash_object_keys(args, object_keys)
    plan = build_publication_plan(
        batch_id=args.batch_id,
        candidates=candidates,
        occurrence_question_ids=occurrence_map,
        runtime_questions=questions,
        runtime_assets=_runtime_assets(raw_assets, hashes),
    )
    invalid_ids = set(invalid)
    if invalid_ids:
        replaced: list[PublicationPlanItem] = []
        for item in plan.items:
            asset_id = item.evidence_ref.removeprefix("structured-visual:")
            if asset_id not in invalid_ids:
                replaced.append(item)
                continue
            replaced.append(
                PublicationPlanItem(
                    batch_id=item.batch_id,
                    evidence_ref=item.evidence_ref,
                    source_asset_sha256=item.source_asset_sha256,
                    source_payload_sha256=item.source_payload_sha256,
                    verification_policy_version=item.verification_policy_version,
                    payload=item.payload,
                    bindings=(),
                    quarantine_reasons=(PublicationReasonCode.SOURCE_SCHEMA_INVALID,),
                )
            )
        plan = PublicationPlan(plan.batch_id, plan.policy_version, tuple(replaced))
    return plan, invalid


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require(args)
    engine = create_engine(args.database_url, pool_pre_ping=True, future=True)
    repository = StructuredVisualPublicationRepository()
    try:
        if args.mode == "rollback":
            with Session(engine) as session, session.begin():
                result = repository.rollback(session, batch_id=args.batch_id)
            print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
            return 0

        if args.mode == "dry-run":
            with Session(engine) as session:
                plan, invalid = _build_plan(args, session)
            _write_plan(args.plan, plan)
            _write_report(args.report, plan=plan, schema_invalid=invalid, mode=args.mode)
            print(json.dumps(_plan_payload(plan) | {
                "items": len(plan.items),
                "publishable": len(plan.publishable_items),
                "bindings": plan.binding_count,
                "quarantined": len(plan.quarantined_items),
            }, ensure_ascii=False, sort_keys=True))
            return 0

        saved_plan = _load_plan(args.plan)
        if saved_plan.batch_id != args.batch_id:
            raise SystemExit("plan batchId does not match --batch-id")
        saved_sha = publication_plan_sha256(saved_plan)
        if saved_sha != args.expected_plan_sha256:
            raise SystemExit("plan SHA-256 does not match --expected-plan-sha256")
        if args.tos_hash_cache is not None:
            raise SystemExit("apply/verify require live TOS hashing; omit --tos-hash-cache")
        with Session(engine) as session, session.begin():
            current_plan, invalid = _build_plan(args, session)
            current_sha = publication_plan_sha256(current_plan)
            if current_sha != args.expected_plan_sha256:
                raise SystemExit(
                    "current question/content/TOS reconciliation changed after dry-run; "
                    "run a new dry-run"
                )
            if args.mode == "apply":
                result = repository.apply(
                    session,
                    plan=current_plan,
                    expected_plan_sha256=args.expected_plan_sha256,
                )
            else:
                result = repository.verify(session, plan=current_plan)
        _write_report(
            args.report, plan=current_plan, schema_invalid=invalid, mode=args.mode
        )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
