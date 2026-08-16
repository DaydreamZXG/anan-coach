#!/usr/bin/env python3
"""Publish current original-analysis images as verifier-only teaching evidence."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication import (
    AnalysisMediaPublicationRepository,
    AnalysisPublicationBinding,
    AnalysisPublicationPlan,
    AnalysisPublicationPlanItem,
    AnalysisPublicationReasonCode,
    AnalysisRuntimeQuestion,
    build_analysis_publication_plan,
    project_formula_candidate,
    project_analysis_source_image,
    project_non_graphic_candidate,
)
from app.domains.teaching_packages.publication.repository import (
    publication_plan_sha256,
)


DEFAULT_BATCH_ID = "analysis-media-verifier-evidence-20260813-v1"
DEFAULT_NON_GRAPHIC_DIR = Path("reports/question-image-audit/non-graphic-structured")
DEFAULT_FORMULA_DIR = Path("reports/question-formula-batch/results")
DEFAULT_ASSETS = Path("reports/question-image-audit/assets.jsonl")
DEFAULT_PLAN = Path("reports/analysis-media-verifier-publication-plan.json")
DEFAULT_REPORT = Path(
    "reports/2026-08-13-analysis-media-verifier-evidence-publication.md"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("dry-run", "apply", "verify", "rollback"),
        default="dry-run",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("QBANK_IMPORT_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument(
        "--include", choices=("non-graphic", "formula", "all"), default="all"
    )
    parser.add_argument("--non-graphic-dir", type=Path, default=DEFAULT_NON_GRAPHIC_DIR)
    parser.add_argument("--formula-dir", type=Path, default=DEFAULT_FORMULA_DIR)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--expected-plan-sha256")
    return parser


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def _analysis_inventory(
    path: Path,
) -> dict[str, tuple[dict[str, Any], tuple[dict[str, Any], ...]]]:
    result: dict[
        str, tuple[dict[str, Any], tuple[dict[str, Any], ...]]
    ] = {}
    for item in _read_jsonl(path):
        occurrences = tuple(
            occurrence
            for occurrence in item.get("occurrences") or ()
            if str(occurrence.get("role") or "").casefold()
            in {"analysis", "solution", "explanation", "answer"}
            and str(occurrence.get("track") or "") != "graphic_reasoning"
        )
        if occurrences:
            result[str(item["asset_id"])] = (item, occurrences)
    return result


def _load_entries(args: argparse.Namespace):
    inventory = _analysis_inventory(args.assets)
    entries: list[
        tuple[dict[str, Any], tuple[dict[str, Any], ...], Callable[..., Any]]
    ] = []
    covered_real_images: set[str] = set()
    if args.include in {"non-graphic", "all"}:
        for path in sorted(args.non_graphic_dir.glob("qi_*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            inventory_entry = inventory.get(str(candidate.get("assetId")))
            occurrences = inventory_entry[1] if inventory_entry else ()
            if occurrences:
                covered_real_images.add(str(candidate.get("assetId")))
                entries.append(
                    (candidate, occurrences, project_non_graphic_candidate)
                )
        for asset_id, (inventory_row, occurrences) in sorted(inventory.items()):
            if asset_id in covered_real_images:
                continue
            if inventory_row.get("kind") != "image":
                continue
            entries.append(
                (inventory_row, occurrences, project_analysis_source_image)
            )
    if args.include in {"formula", "all"}:
        for path in sorted(args.formula_dir.glob("qi_*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if candidate.get("priority") != "explanation":
                continue
            inventory_entry = inventory.get(str(candidate.get("asset_id")))
            occurrences = inventory_entry[1] if inventory_entry else ()
            if not occurrences:
                continue
            entries.append(
                (
                    candidate,
                    occurrences,
                    lambda value, source_occurrences=occurrences: (
                        project_formula_candidate(
                            value, source_occurrences=source_occurrences
                        )
                    ),
                )
            )
    return entries


def _fetch_runtime(
    session: Session,
    source_keys: set[str],
) -> tuple[dict[str, str], dict[str, AnalysisRuntimeQuestion]]:
    occurrence_map = {
        str(row["source_key"]): str(row["question_id"])
        for row in session.execute(
            text(
                """
                select distinct on (source_occurrence_question_key)
                       source_occurrence_question_key as source_key, question_id
                from product.question_occurrences
                where source_occurrence_question_key = any(cast(:keys as text[]))
                order by source_occurrence_question_key,
                         canonical_occurrence_rank nulls last, occurrence_id
                """
            ),
            {"keys": sorted(source_keys)},
        ).mappings()
    }
    question_ids = sorted(set(occurrence_map.values()))
    questions: dict[str, AnalysisRuntimeQuestion] = {}
    if not question_ids:
        return occurrence_map, questions
    for row in session.execute(
        text(
            """
            select question.question_id, question.status, head.content_version_id,
                   version.snapshot_payload ->> 'analysis_source' as analysis_source,
                   explanation.final_analysis_asset_refs,
                   (
                     question.answer = 'A'
                     and (
                       question.stem ilike '%default A%'
                       or question.stem ilike '%默认A%'
                       or question.stem ilike '%正在全力以赴征集%'
                     )
                   ) as placeholder
            from product.questions question
            left join product.question_content_heads head
              on head.question_id = question.question_id
            left join product.question_content_versions version
              on version.question_id = head.question_id
             and version.content_version_id = head.content_version_id
            left join product.question_explanations explanation
              on explanation.question_id = question.question_id
            where question.question_id = any(cast(:ids as text[]))
            """
        ),
        {"ids": question_ids},
    ).mappings():
        questions[str(row["question_id"])] = AnalysisRuntimeQuestion(
            question_id=str(row["question_id"]),
            status=str(row["status"]),
            content_version_id=(
                str(row["content_version_id"])
                if row["content_version_id"]
                else None
            ),
            analysis_source=str(row["analysis_source"] or ""),
            placeholder=bool(row["placeholder"]),
            final_analysis_asset_refs=(
                dict(row["final_analysis_asset_refs"])
                if isinstance(row["final_analysis_asset_refs"], dict)
                else None
            ),
        )
    return occurrence_map, questions


def _build_plan(args: argparse.Namespace, session: Session) -> AnalysisPublicationPlan:
    entries = _load_entries(args)
    source_keys = {
        str(occurrence.get("question_id"))
        for _, occurrences, _ in entries
        for occurrence in occurrences
        if occurrence.get("question_id")
    }
    occurrence_map, questions = _fetch_runtime(session, source_keys)
    return build_analysis_publication_plan(
        batch_id=args.batch_id,
        entries=entries,
        occurrence_question_ids=occurrence_map,
        runtime_questions=questions,
    )


def _item(payload: dict[str, Any]) -> AnalysisPublicationPlanItem:
    diagnostics = payload.get("diagnostics") or {}
    return AnalysisPublicationPlanItem(
        batch_id=str(payload["batchId"]),
        evidence_ref=str(payload["evidenceRef"]),
        source_asset_sha256=str(payload["sourceAssetSha256"]),
        source_payload_sha256=str(payload["sourcePayloadSha256"]),
        verification_policy_version=str(payload["verificationPolicyVersion"]),
        payload=dict(payload["payload"]),
        analysis_bindings=tuple(
            AnalysisPublicationBinding(
                question_id=str(item["questionId"]),
                content_version_id=str(item["contentVersionId"]),
                analysis_source=str(item["analysisSource"]),
                source_occurrence_question_key=str(
                    item["sourceOccurrenceQuestionKey"]
                ),
                source_image_ref=str(item["sourceImageRef"]),
                runtime_object_key=str(item.get("runtimeObjectKey") or ""),
            )
            for item in payload["analysisBindings"]
        ),
        quarantine_reasons=tuple(
            AnalysisPublicationReasonCode(item)
            for item in payload["quarantineReasons"]
        ),
        unresolved_occurrence_keys=tuple(
            diagnostics.get("unresolvedOccurrenceKeys") or ()
        ),
        rejected_question_ids=tuple(
            diagnostics.get("rejectedQuestionIds") or ()
        ),
        authority=str(diagnostics.get("authority") or ""),
    )


def _payload(plan: AnalysisPublicationPlan) -> dict[str, Any]:
    payload = plan.as_contract()
    for output, item in zip(payload["items"], plan.items, strict=True):
        output["diagnostics"] = {
            "authority": item.authority,
            "unresolvedOccurrenceKeys": list(item.unresolved_occurrence_keys),
            "rejectedQuestionIds": list(item.rejected_question_ids),
        }
    payload["planSha256"] = publication_plan_sha256(plan)
    return payload


def _load_plan(path: Path) -> AnalysisPublicationPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return AnalysisPublicationPlan(
        batch_id=str(payload["batchId"]),
        policy_version=str(payload["verificationPolicyVersion"]),
        items=tuple(_item(item) for item in payload["items"]),
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_report(path: Path, plan: AnalysisPublicationPlan, mode: str) -> None:
    kinds: dict[str, int] = defaultdict(int)
    states: dict[str, int] = defaultdict(int)
    reasons: dict[str, int] = defaultdict(int)
    object_bound = 0
    source_bound = 0
    for item in plan.publishable_items:
        kinds[str(item.payload.get("evidenceKind"))] += 1
        states[str(item.payload.get("reviewState"))] += 1
        for binding in item.analysis_bindings:
            if binding.runtime_object_key:
                object_bound += 1
            else:
                source_bound += 1
    for item in plan.quarantined_items:
        for reason in item.quarantine_reasons:
            reasons[reason.value] += 1
    lines = [
        "# 解析媒体验证者证据接入报告",
        "",
        f"- 模式：`{mode}`",
        f"- 批次：`{plan.batch_id}`",
        f"- 计划 SHA-256：`{publication_plan_sha256(plan)}`",
        f"- 总候选：{len(plan.items):,}",
        f"- 可发布：{len(plan.publishable_items):,}",
        f"- 当前解析绑定：{plan.binding_count:,}",
        f"- 已发布对象绑定：{object_bound:,}",
        f"- 源解析图片绑定：{source_bound:,}",
        f"- 隔离：{len(plan.quarantined_items):,}",
        "",
        "## 发布类型",
        "",
    ]
    lines += [f"- `{key}`：{value:,}" for key, value in sorted(kinds.items())] or ["- 无"]
    lines += ["", "## 审核来源", ""]
    lines += [f"- `{key}`：{value:,}" for key, value in sorted(states.items())] or ["- 无"]
    lines += ["", "## 隔离原因", ""]
    lines += [f"- `{key}`：{value:,}" for key, value in sorted(reasons.items())] or ["- 无"]
    lines += [
        "",
        "## 边界",
        "",
        "本批只绑定当前 `original_analysis` 的解析/解法媒体，固定为 verifier 受众。学生岸岸不可见；题文、答案、解析正文、原始图片、卷属、用户数据和服务发布头均不修改。",
        "",
    ]
    _write(path, "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required")
    if args.mode in {"apply", "verify"} and not args.expected_plan_sha256:
        raise SystemExit("--expected-plan-sha256 is required")
    database_url = args.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg://", 1
    )
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    repository = AnalysisMediaPublicationRepository()
    try:
        if args.mode == "rollback":
            with Session(engine) as session, session.begin():
                result = repository.rollback(session, batch_id=args.batch_id)
            print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
            return 0
        if args.mode == "dry-run":
            with Session(engine) as session:
                plan = _build_plan(args, session)
            _write(
                args.plan,
                json.dumps(_payload(plan), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            _write_report(args.report, plan, args.mode)
            print(
                json.dumps(
                    {
                        "planSha256": publication_plan_sha256(plan),
                        "items": len(plan.items),
                        "publishable": len(plan.publishable_items),
                        "bindings": plan.binding_count,
                        "quarantined": len(plan.quarantined_items),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        saved = _load_plan(args.plan)
        if publication_plan_sha256(saved) != args.expected_plan_sha256:
            raise SystemExit("saved plan SHA-256 mismatch")
        with Session(engine) as session, session.begin():
            current = _build_plan(args, session)
            if publication_plan_sha256(current) != args.expected_plan_sha256:
                raise SystemExit("runtime reconciliation changed after dry-run")
            result = (
                repository.apply(
                    session,
                    plan=current,
                    expected_plan_sha256=args.expected_plan_sha256,
                )
                if args.mode == "apply"
                else repository.verify(session, plan=current)
            )
        _write_report(args.report, current, args.mode)
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
