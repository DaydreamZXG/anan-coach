#!/usr/bin/env python3
"""Publish all non-graphic question media into the current evidence identity chain."""

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
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication import (
    PublicationBinding,
    PublicationPlan,
    PublicationPlanItem,
    PublicationReasonCode,
    RuntimeAsset,
    RuntimeQuestion,
    StructuredVisualPublicationRepository,
    build_media_publication_plan,
    project_formula_candidate,
    project_non_graphic_candidate,
)
from app.domains.teaching_packages.publication.repository import publication_plan_sha256


DEFAULT_BATCH_ID = "non-graphic-question-media-20260813-v1"
DEFAULT_NON_GRAPHIC_DIR = Path("reports/question-image-audit/non-graphic-structured")
DEFAULT_FORMULA_DIR = Path("reports/question-formula-batch/results")
DEFAULT_ASSETS = Path("reports/question-image-audit/assets.jsonl")
DEFAULT_PLAN = Path("reports/non-graphic-question-media-publication-plan.json")
DEFAULT_REPORT = Path("reports/2026-08-13-non-graphic-media-evidence-publication.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "apply", "verify", "rollback"), default="dry-run")
    parser.add_argument("--database-url", default=os.getenv("QBANK_IMPORT_DATABASE_URL"))
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    parser.add_argument("--include", choices=("non-graphic", "formula", "all"), default="all")
    parser.add_argument(
        "--formula-priority",
        choices=("question_critical", "explanation", "all"),
        default="question_critical",
    )
    parser.add_argument("--non-graphic-dir", type=Path, default=DEFAULT_NON_GRAPHIC_DIR)
    parser.add_argument("--formula-dir", type=Path, default=DEFAULT_FORMULA_DIR)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--tos-hash-cache", type=Path)
    parser.add_argument("--tos-bucket", default=os.getenv("TOS_BUCKET", "gongkao-dazi-prod"))
    parser.add_argument("--tos-region", default=os.getenv("TOS_REGION", "cn-guangzhou"))
    parser.add_argument("--tos-endpoint", default=os.getenv("TOS_ENDPOINT"))
    parser.add_argument("--tos-access-key", default=os.getenv("TOS_ACCESS_KEY"))
    parser.add_argument("--tos-secret-key", default=os.getenv("TOS_SECRET_KEY"))
    parser.add_argument("--tos-workers", type=int, default=8)
    return parser


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _asset_inventory(path: Path) -> dict[str, dict[str, Any]]:
    return {str(item["asset_id"]): item for item in _read_jsonl(path)}


def _non_graphic_scope_occurrences(
    occurrences: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """Keep formula occurrences in this batch out of graphic reasoning."""

    return tuple(
        item
        for item in occurrences
        if str(item.get("track") or "") != "graphic_reasoning"
    )


def _load_candidates(args: argparse.Namespace) -> list[tuple[str, dict[str, Any], tuple[dict[str, Any], ...]]]:
    result: list[tuple[str, dict[str, Any], tuple[dict[str, Any], ...]]] = []
    inventory = _asset_inventory(args.assets)
    if args.include in {"non-graphic", "all"}:
        for path in sorted(args.non_graphic_dir.glob("qi_*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            occurrences = tuple(
                inventory.get(str(candidate.get("assetId")), {}).get("occurrences") or ()
            )
            result.append(("non-graphic", candidate, occurrences))
    if args.include in {"formula", "all"}:
        for path in sorted(args.formula_dir.glob("qi_*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if (
                args.formula_priority != "all"
                and candidate.get("priority") != args.formula_priority
            ):
                continue
            occurrences = _non_graphic_scope_occurrences(
                inventory.get(str(candidate.get("asset_id")), {}).get("occurrences")
                or ()
            )
            if not occurrences:
                continue
            result.append(("formula", candidate, occurrences))
    return result


def _source_keys(kind: str, candidate: dict[str, Any], occurrences: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    if kind == "non-graphic":
        return tuple(sorted(set(candidate["source"]["questionIds"])))
    return tuple(sorted({str(item.get("question_id")) for item in occurrences if item.get("question_id")}))


def _source_roles(kind: str, candidate: dict[str, Any], occurrences: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    if kind == "non-graphic":
        return tuple(candidate["source"].get("roles") or ())
    return tuple(sorted({str(item.get("role")) for item in occurrences if item.get("role")}))


def _fetch_runtime(
    session: Session,
    source_keys: set[str],
    image_refs_by_source: dict[str, set[str]],
):
    occurrence_map = {
        str(row["source_key"]): str(row["question_id"])
        for row in session.execute(
            text("""
                select source_occurrence_question_key as source_key, question_id
                from product.question_occurrences
                where source_occurrence_question_key = any(cast(:keys as text[]))
                order by source_occurrence_question_key, canonical_occurrence_rank nulls last, occurrence_id
            """), {"keys": sorted(source_keys)}
        ).mappings()
    }
    question_ids = sorted(set(occurrence_map.values()))
    questions = {
        str(row["question_id"]): RuntimeQuestion(
            question_id=str(row["question_id"]), status=str(row["status"]),
            content_version_id=str(row["content_version_id"]) if row["content_version_id"] else None,
            placeholder=bool(row["placeholder"]),
        )
        for row in session.execute(text("""
            select q.question_id, q.status, h.content_version_id,
              (q.answer = 'A' and (q.stem ilike '%default A%' or q.stem ilike '%默认A%' or q.stem ilike '%正在全力以赴征集%')) as placeholder
            from product.questions q left join product.question_content_heads h on h.question_id=q.question_id
            where q.question_id = any(cast(:ids as text[]))
        """), {"ids": question_ids}).mappings()
    }
    runtime_refs_by_question: dict[str, set[str]] = defaultdict(set)
    for source_key, question_id in occurrence_map.items():
        runtime_refs_by_question[question_id].update(
            image_refs_by_source.get(source_key, ())
        )
    raw: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    if question_ids:
        for row in session.execute(text("""
            select question_id, asset_id, object_key, part_kind, image_src
            from product.question_assets
            where question_id = any(cast(:ids as text[]))
              and nullif(btrim(object_key),'') is not null
            order by question_id, asset_id
        """), {"ids": question_ids}).mappings():
            question_id = str(row["question_id"])
            image_src = str(row["image_src"] or "")
            expected_refs = runtime_refs_by_question.get(question_id, set())
            if not any(
                image_src == expected or image_src.endswith(f"/{expected}")
                for expected in expected_refs
            ):
                continue
            raw[question_id].append((str(row["asset_id"]), str(row["object_key"]), str(row["part_kind"] or "")))
    return occurrence_map, questions, raw


def _load_hash_cache(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    result: dict[str, str] = {}
    for item in _read_jsonl(path):
        if isinstance(item, list):
            result[str(item[0])] = str(item[1])
        else:
            result[str(item["objectKey"])] = str(item["sha256"])
    return result


def _hash_objects(args: argparse.Namespace, keys: set[str]) -> dict[str, str]:
    hashes = _load_hash_cache(args.tos_hash_cache)
    missing = sorted(keys - hashes.keys())
    if not missing:
        return hashes
    required = (args.tos_endpoint, args.tos_access_key, args.tos_secret_key)
    if not all(required):
        raise SystemExit("TOS credentials are required when the hash cache is incomplete")
    import tos
    def digest(key: str) -> tuple[str, str]:
        client = tos.TosClientV2(args.tos_access_key, args.tos_secret_key, args.tos_endpoint, args.tos_region)
        response = client.get_object(args.tos_bucket, key)
        try:
            chunks: list[bytes] = []
            while chunk := response.read(1024 * 1024):
                chunks.append(chunk)
            return key, hashlib.sha256(b"".join(chunks)).hexdigest()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    with ThreadPoolExecutor(max_workers=args.tos_workers) as executor:
        futures = {executor.submit(digest, key): key for key in missing}
        for future in as_completed(futures):
            key = futures[future]
            try:
                _, hashes[key] = future.result()
            except Exception:
                hashes[key] = ""
    return hashes


def _combine(plans: list[PublicationPlan], batch_id: str) -> PublicationPlan:
    return PublicationPlan(batch_id=batch_id, policy_version="question-media-evidence-publication@v2", items=tuple(item for plan in plans for item in plan.items))


def _build_plan(args: argparse.Namespace, session: Session) -> PublicationPlan:
    entries = _load_candidates(args)
    keys = {key for kind, candidate, occurrences in entries for key in _source_keys(kind, candidate, occurrences)}
    image_refs_by_source: dict[str, set[str]] = defaultdict(set)
    for _, _, occurrences in entries:
        for item in occurrences:
            if item.get("question_id") and item.get("image_ref"):
                image_refs_by_source[str(item["question_id"])].add(
                    str(item["image_ref"])
                )
    occurrence_map, questions, raw_assets = _fetch_runtime(
        session, keys, image_refs_by_source
    )
    hashes = _hash_objects(args, {key for assets in raw_assets.values() for _, key, _ in assets})
    runtime_assets = {
        question_id: tuple(RuntimeAsset(asset_id=asset_id, question_id=question_id, object_key=object_key, content_sha256=hashes.get(object_key), part_kind=part_kind) for asset_id, object_key, part_kind in assets)
        for question_id, assets in raw_assets.items()
    }
    plans: list[PublicationPlan] = []
    for kind in ("non-graphic", "formula"):
        selected = [(candidate, occurrences) for entry_kind, candidate, occurrences in entries if entry_kind == kind]
        if not selected:
            continue
        occurrence_by_identity = {id(candidate): occurrences for candidate, occurrences in selected}
        if kind == "non-graphic":
            projector: Callable[[dict[str, Any]], Any] = project_non_graphic_candidate
        else:
            def projector(candidate: dict[str, Any]) -> Any:
                return project_formula_candidate(
                    candidate,
                    source_occurrences=occurrence_by_identity[id(candidate)],
                )
        plans.append(build_media_publication_plan(
            batch_id=args.batch_id,
            candidates=[candidate for candidate, _ in selected], projector=projector,
            source_keys_for_candidate=lambda candidate, k=kind: _source_keys(k, candidate, occurrence_by_identity[id(candidate)]),
            source_roles_for_candidate=lambda candidate, k=kind: _source_roles(k, candidate, occurrence_by_identity[id(candidate)]),
            occurrence_question_ids=occurrence_map, runtime_questions=questions, runtime_assets=runtime_assets,
        ))
    return _combine(plans, args.batch_id)


def _item(payload: dict[str, Any]) -> PublicationPlanItem:
    diagnostics = payload.get("diagnostics") or {}
    return PublicationPlanItem(
        batch_id=str(payload["batchId"]), evidence_ref=str(payload["evidenceRef"]),
        source_asset_sha256=str(payload["sourceAssetSha256"]), source_payload_sha256=str(payload["sourcePayloadSha256"]),
        verification_policy_version=str(payload["verificationPolicyVersion"]), payload=dict(payload["payload"]),
        bindings=tuple(PublicationBinding(question_id=str(item["questionId"]), content_version_id=str(item["contentVersionId"]), runtime_asset_id=str(item["runtimeAssetId"])) for item in payload["bindings"]),
        quarantine_reasons=tuple(PublicationReasonCode(item) for item in payload["quarantineReasons"]),
        unresolved_occurrence_keys=tuple(diagnostics.get("unresolvedOccurrenceKeys") or ()),
        rejected_question_ids=tuple(diagnostics.get("rejectedQuestionIds") or ()), authority=str(diagnostics.get("authority") or ""),
    )


def _payload(plan: PublicationPlan) -> dict[str, Any]:
    payload = plan.as_contract()
    for output, item in zip(payload["items"], plan.items, strict=True):
        output["diagnostics"] = {"authority": item.authority, "unresolvedOccurrenceKeys": list(item.unresolved_occurrence_keys), "rejectedQuestionIds": list(item.rejected_question_ids)}
    payload["planSha256"] = publication_plan_sha256(plan)
    return payload


def _load_plan(path: Path) -> PublicationPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PublicationPlan(str(payload["batchId"]), str(payload["verificationPolicyVersion"]), tuple(_item(item) for item in payload["items"]))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_report(path: Path, plan: PublicationPlan, mode: str) -> None:
    kinds: dict[str, int] = defaultdict(int)
    states: dict[str, int] = defaultdict(int)
    for item in plan.publishable_items:
        kinds[str(item.payload.get("evidenceKind"))] += 1
        states[str(item.payload.get("reviewState"))] += 1
    reasons: dict[str, int] = defaultdict(int)
    for item in plan.quarantined_items:
        for reason in item.quarantine_reasons:
            reasons[reason.value] += 1
    lines = ["# 非图推题目媒体证据接入报告", "", f"- 模式：`{mode}`", f"- 批次：`{plan.batch_id}`", f"- 计划 SHA-256：`{publication_plan_sha256(plan)}`", f"- 总候选：{len(plan.items):,}", f"- 可发布：{len(plan.publishable_items):,}", f"- 当前绑定：{plan.binding_count:,}", f"- 隔离：{len(plan.quarantined_items):,}", "", "## 发布类型", ""]
    lines += [f"- `{key}`：{value:,}" for key, value in sorted(kinds.items())] or ["- 无"]
    lines += ["", "## 审核来源", ""] + ([f"- `{key}`：{value:,}" for key, value in sorted(states.items())] or ["- 无"])
    lines += ["", "## 隔离原因", ""] + ([f"- `{key}`：{value:,}" for key, value in sorted(reasons.items())] or ["- 无"])
    lines += ["", "## 边界", "", "图形推理不在本批。题干/材料/选项证据可进入学生岸岸，解析/解法证据只进入后台核验；批次原文件、题文、答案、图片、卷属和用户数据均未修改。", ""]
    _write(path, "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("--database-url or QBANK_IMPORT_DATABASE_URL is required")
    if args.mode in {"apply", "verify"} and not args.expected_plan_sha256:
        raise SystemExit("--expected-plan-sha256 is required")
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
                plan = _build_plan(args, session)
            _write(args.plan, json.dumps(_payload(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            _write_report(args.report, plan, args.mode)
            print(json.dumps({"planSha256": publication_plan_sha256(plan), "items": len(plan.items), "publishable": len(plan.publishable_items), "bindings": plan.binding_count, "quarantined": len(plan.quarantined_items)}, ensure_ascii=False, sort_keys=True))
            return 0
        saved = _load_plan(args.plan)
        if publication_plan_sha256(saved) != args.expected_plan_sha256:
            raise SystemExit("saved plan SHA-256 mismatch")
        if args.tos_hash_cache is not None:
            raise SystemExit("apply/verify require live TOS hashing")
        with Session(engine) as session, session.begin():
            current = _build_plan(args, session)
            if publication_plan_sha256(current) != args.expected_plan_sha256:
                raise SystemExit("runtime reconciliation changed after dry-run")
            result = repository.apply(session, plan=current, expected_plan_sha256=args.expected_plan_sha256) if args.mode == "apply" else repository.verify(session, plan=current)
        _write_report(args.report, current, args.mode)
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
