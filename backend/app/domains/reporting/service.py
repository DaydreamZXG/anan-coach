from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.reporting.external_reports import _projection as external_projection
from app.domains.reporting.repository import ReportingRepository
from app.domains.reporting.schemas import (
    ChartSpec,
    MockPaperSummary,
    ReportCardSummary,
    ReportCenterProjection,
    ReportConclusion,
    ReportProjection,
)


ReportKind = Literal["single_set", "weekly", "mock", "position_gap", "global"]


class ReportNotFound(LookupError):
    pass


class ReportingService:
    def __init__(self, *, repository: ReportingRepository | None = None) -> None:
        self._repository = repository or ReportingRepository()

    def center(
        self, session: Session, *, user_id: str, now: datetime | None = None
    ) -> ReportCenterProjection:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        sessions = self._repository.mock_sessions(session, user_id=user_id)
        papers = [
            MockPaperSummary(
                paperVersionId=row["paper_version_id"],
                title=row["title"],
                durationMinutes=int(row["duration_minutes"]),
                questionCount=len(row["question_ids"]),
                scoringPolicyVersionId=row["scoring_policy_version_id"],
                metricName=row["metric_name"],
                metricUnit=row["metric_unit"],
                metricDefinition=row["definition"],
                status=("in_progress" if row["status"] == "assigned" else "submitted"),
                sessionId=row["session_id"],
            )
            for row in sessions
        ]
        latest_external = self._repository.latest_confirmed_external(session, user_id=user_id)
        ability = self._repository.ability_history(session, user_id=user_id)
        goal = self._repository.current_goal(session, user_id=user_id)
        sets = self._repository.recent_set_summaries(session, user_id=user_id, limit=1)
        weekly = self._repository.weekly_execution_summary(session, user_id=user_id)
        cards = [
            ReportCardSummary(
                reportKind="single_set",
                title="单次题组",
                subtitle="正确率、用时和复盘证据",
                route="/reports/report-overview/index?kind=single_set",
                state="ready" if sets else "empty",
            ),
            ReportCardSummary(
                reportKind="weekly",
                title="本周学习",
                subtitle="计划执行和双轨投入",
                route="/reports/report-overview/index?kind=weekly",
                state="ready" if weekly else "empty",
            ),
            ReportCardSummary(
                reportKind="mock",
                title="模考报告",
                subtitle="表现指数、模块和错因",
                route="/reports/mock-report/index",
                state="ready" if any(row["review_payload"] for row in sessions) else "empty",
            ),
            ReportCardSummary(
                reportKind="position_gap",
                title="岗位分差",
                subtitle="目标分依据与能力区间",
                route="/reports/report-overview/index?kind=position_gap",
                state="ready" if goal is not None and ability else "partial",
            ),
            ReportCardSummary(
                reportKind="global",
                title="全局学习报告",
                subtitle="趋势、模块、速度和下一步",
                route="/reports/global-report/index",
                state="ready" if ability or sessions or weekly else "empty",
            ),
        ]
        return ReportCenterProjection(
            generatedAt=timestamp,
            standardPapers=papers,
            latestExternalReport=(
                external_projection(latest_external) if latest_external is not None else None
            ),
            reportCards=cards,
        )

    def report(
        self,
        session: Session,
        *,
        user_id: str,
        report_kind: ReportKind,
        subject_ref: str = "current",
        now: datetime | None = None,
    ) -> ReportProjection:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        facts = self._facts(
            session,
            user_id=user_id,
            report_kind=report_kind,
            subject_ref=subject_ref,
        )
        input_fingerprint = _fingerprint(
            {"reportKind": report_kind, "subjectRef": subject_ref, "facts": facts}
        )
        existing = self._repository.projection_by_fingerprint(
            session,
            user_id=user_id,
            report_kind=report_kind,
            subject_ref=subject_ref,
            input_fingerprint=input_fingerprint,
        )
        if existing is not None:
            return _stored_projection(existing)
        projection_id = str(uuid4())
        title, charts, conclusion = _compose_report(
            report_kind=report_kind,
            subject_ref=subject_ref,
            facts=facts,
            generated_at=timestamp,
        )
        state = _report_state(charts)
        projection = ReportProjection(
            projectionVersionId=projection_id,
            inputFingerprint=input_fingerprint,
            reportKind=report_kind,
            subjectRef=subject_ref,
            title=title,
            generatedAt=timestamp,
            state=state,
            conclusion=conclusion,
            charts=charts,
        )
        evidence_refs = sorted(
            {ref for chart in charts for ref in chart.evidence_refs} | set(conclusion.evidence_refs)
        )
        row = self._repository.insert_projection(
            session,
            values={
                "projection_version_id": projection_id,
                "user_id": user_id,
                "report_kind": report_kind,
                "subject_ref": subject_ref,
                "input_fingerprint": input_fingerprint,
                "payload": projection.model_dump(by_alias=True, mode="json"),
                "evidence_refs": evidence_refs,
                "next_plan_evidence_refs": conclusion.next_plan_evidence_refs,
                "generated_at": timestamp,
            },
        )
        return _stored_projection(row)

    def _facts(
        self,
        session: Session,
        *,
        user_id: str,
        report_kind: ReportKind,
        subject_ref: str,
    ) -> dict[str, Any]:
        mock_rows = self._repository.mock_sessions(session, user_id=user_id)
        if report_kind == "mock":
            selected = next(
                (
                    row
                    for row in mock_rows
                    if subject_ref == "current" or str(row["session_id"]) == subject_ref
                ),
                None,
            )
            if selected is None:
                raise ReportNotFound(subject_ref)
            mock_rows = [selected]
        return {
            "ability": [
                _jsonable_row(row)
                for row in self._repository.ability_history(session, user_id=user_id)
            ],
            "external": [
                _jsonable_row(row)
                for row in self._repository.external_history(session, user_id=user_id)
            ],
            "mocks": [_jsonable_row(row) for row in mock_rows],
            "sets": [
                _jsonable_row(row)
                for row in self._repository.recent_set_summaries(session, user_id=user_id)
            ],
            "weekly": [
                _jsonable_row(row)
                for row in self._repository.weekly_execution_summary(session, user_id=user_id)
            ],
            "goal": (
                _jsonable_row(goal)
                if (goal := self._repository.current_goal(session, user_id=user_id)) is not None
                else None
            ),
        }


def _compose_report(
    *,
    report_kind: ReportKind,
    subject_ref: str,
    facts: dict[str, Any],
    generated_at: datetime,
) -> tuple[str, list[ChartSpec], ReportConclusion]:
    title_map = {
        "single_set": "单次题组报告",
        "weekly": "本周学习报告",
        "mock": "整卷模考报告",
        "position_gap": "岗位分差报告",
        "global": "全局学习报告",
    }
    all_charts = {
        "trend": _confidence_trend(facts),
        "bullet": _module_bullet(facts),
        "quadrant": _speed_accuracy(facts),
        "heatmap": _error_heatmap(facts),
        "gap": _score_gap(facts),
        "weekly": _weekly_completion(facts),
    }
    selected = {
        "single_set": ["quadrant", "heatmap"],
        "weekly": ["weekly", "trend"],
        "mock": ["bullet", "quadrant", "heatmap"],
        "position_gap": ["gap", "trend"],
        "global": ["trend", "bullet", "quadrant", "heatmap", "gap", "weekly"],
    }[report_kind]
    charts = [all_charts[key] for key in selected]
    evidence_refs = sorted({ref for chart in charts for ref in chart.evidence_refs})
    next_refs = _next_plan_refs(facts, report_kind=report_kind)
    ready_charts = [chart for chart in charts if chart.data]
    if ready_charts:
        headline = ready_charts[0].summary
        detail = "结论仅基于下方可追溯证据；缺失的轨道保持为部分状态。"
    else:
        headline = "暂无足够的可追溯学习证据。"
        detail = "完成题组、整卷模考或确认外部成绩后再生成结论。"
    conclusion = ReportConclusion(
        headline=headline,
        detail=detail,
        evidenceRefs=evidence_refs,
        nextAction=(
            "已将当前正式证据引用绑定到下一次计划输入。"
            if next_refs
            else "先完成一条可确认证据，报告不会凭空改计划。"
        ),
        nextPlanEvidenceRefs=next_refs,
    )
    return title_map[report_kind], charts, conclusion


def _confidence_trend(facts: dict[str, Any]) -> ChartSpec:
    rows = facts["ability"]
    data = [
        {
            "at": row["generated_at"],
            "lower": _num(
                row.get("total_lower") or row.get("xingce_lower") or row.get("shenlun_lower")
            ),
            "recommended": _num(
                row.get("total_recommended")
                or row.get("xingce_recommended")
                or row.get("shenlun_recommended")
            ),
            "upper": _num(
                row.get("total_upper") or row.get("xingce_upper") or row.get("shenlun_upper")
            ),
            "component": "total"
            if row.get("total_recommended") is not None
            else ("xingce" if row.get("xingce_recommended") is not None else "shenlun"),
            "confidence": row["confidence"],
            "estimateVersionId": str(row["estimate_version_id"]),
        }
        for row in rows
    ]
    refs = [f"ability-estimate:{row['estimate_version_id']}" for row in rows]
    return ChartSpec(
        chartId="ability-confidence-trend",
        chartType="confidence_trend",
        title="能力区间趋势",
        definition="仅使用未被取代的正式成绩证据所生成的版本化能力区间。",
        period=_period(data),
        unit="成绩分",
        confidence=(rows[-1]["confidence"] if rows else "low"),
        summary=(
            f"当前建议值 {data[-1]['recommended']:.1f}，区间 "
            f"{data[-1]['lower']:.1f}–{data[-1]['upper']:.1f}。"
            if data
            else "尚无可用的能力区间。"
        ),
        evidenceRefs=refs,
        data=data,
        partialReasons=[] if data else ["ability_estimate_unavailable"],
    )


def _module_bullet(facts: dict[str, Any]) -> ChartSpec:
    mock = next((item for item in facts["mocks"] if item.get("review_payload")), None)
    modules = (mock or {}).get("review_payload", {}).get("modules", [])
    data = []
    for item in modules:
        actual = float(item["accuracy"]) * 100
        change = item.get("changeFromPrevious")
        marker = actual - float(change) * 100 if change is not None else None
        data.append(
            {
                "module": item["moduleName"],
                "actual": round(actual, 1),
                "comparisonMarker": round(marker, 1) if marker is not None else None,
                "comparisonMeaning": "previous_mock",
            }
        )
    ref = f"mock-session:{mock['session_id']}" if mock else None
    weakest = min(data, key=lambda item: item["actual"]) if data else None
    return ChartSpec(
        chartId="mock-module-bullet",
        chartType="module_bullet",
        title="模块表现对比",
        definition="当次模块正确率；对比线仅表示本人上一场，不是人工设定目标。",
        period=_mock_period(mock),
        unit="%",
        confidence="not_applicable",
        summary=(
            f"本场最低模块是{weakest['module']} {weakest['actual']:.1f}%。"
            if weakest
            else "尚无已交卷的模块数据。"
        ),
        evidenceRefs=[ref] if ref else [],
        data=data,
        partialReasons=[] if data else ["submitted_mock_unavailable"],
    )


def _speed_accuracy(facts: dict[str, Any]) -> ChartSpec:
    data = []
    for row in facts["sets"]:
        total = int(row["total_count"])
        elapsed = float(row["elapsed_seconds"])
        data.append(
            {
                "sessionId": str(row["session_id"]),
                "at": row["occurred_at"],
                "accuracy": round(int(row["correct_count"]) / total * 100, 1) if total else 0,
                "secondsPerQuestion": round(elapsed / total, 1) if total else None,
            }
        )
    refs = [f"xingce-session:{row['session_id']}" for row in facts["sets"]]
    return ChartSpec(
        chartId="set-speed-accuracy",
        chartType="speed_accuracy_quadrant",
        title="速度 × 正确率",
        definition="每个点为一次已提交题组；速度来自服务端冻结的单题计时证据。",
        period=_period(data),
        unit="%，秒/题",
        confidence="not_applicable",
        summary=(
            f"最近题组 {data[0]['accuracy']:.1f}%，{data[0]['secondsPerQuestion']:.1f} 秒/题。"
            if data and data[0]["secondsPerQuestion"] is not None
            else "尚无完整的题组计时证据。"
        ),
        evidenceRefs=refs,
        data=data,
        partialReasons=[] if data else ["timed_set_unavailable"],
    )


def _error_heatmap(facts: dict[str, Any]) -> ChartSpec:
    mock = next((item for item in facts["mocks"] if item.get("review_payload")), None)
    clusters = (mock or {}).get("review_payload", {}).get("wrongClusters", [])
    data = [
        {
            "nodeId": item["nodeId"],
            "path": item["displayPath"],
            "wrongCount": int(item["wrongCount"]),
            "fixAction": item["fixAction"],
        }
        for item in clusters
    ]
    ref = f"mock-session:{mock['session_id']}" if mock else None
    return ChartSpec(
        chartId="wrong-cause-heatmap",
        chartType="error_heatmap",
        title="错因热力",
        definition="错题按已发布知识节点聚合；颜色之外同时保留错题数。",
        period=_mock_period(mock),
        unit="错题数",
        confidence="not_applicable",
        summary=(
            f"最密集错因是{data[0]['path']}，共 {data[0]['wrongCount']} 题。"
            if data
            else "尚无可归因的整卷错题。"
        ),
        evidenceRefs=[ref] if ref else [],
        data=data,
        partialReasons=[] if data else ["wrong_cluster_unavailable"],
    )


def _score_gap(facts: dict[str, Any]) -> ChartSpec:
    goal = facts["goal"]
    ability = facts["ability"][-1] if facts["ability"] else None
    data = []
    refs = []
    if goal and ability and ability.get("total_recommended") is not None:
        current = float(ability["total_recommended"])
        target = float(goal["target_score_recommended"])
        data = [
            {"step": "current_ability", "value": current},
            {"step": "gap", "value": round(target - current, 2)},
            {"step": "position_target", "value": target},
        ]
        refs = [
            f"ability-estimate:{ability['estimate_version_id']}",
            f"position-goal:{goal['goal_version_id']}",
        ]
    return ChartSpec(
        chartId="position-score-gap",
        chartType="score_gap_waterfall",
        title="岗位分差拆解",
        definition="岗位目标分建议值减去当前双科能力建议值；两端均保留原区间证据。",
        period="当前有效版本",
        unit="分",
        confidence=(ability["confidence"] if ability else "low"),
        summary=(
            f"当前建议值距岗位目标建议值还有 {data[1]['value']:.1f} 分。"
            if data
            else "需要有效岗位目标和双科能力区间才能计算。"
        ),
        evidenceRefs=refs,
        data=data,
        partialReasons=[] if data else ["goal_or_complete_ability_unavailable"],
    )


def _weekly_completion(facts: dict[str, Any]) -> ChartSpec:
    data = [
        {
            "track": row["track"],
            "kind": row["kind"],
            "planned": int(row["planned_count"]),
            "completed": int(row["completed_count"]),
            "minutes": int(row["completed_minutes"]),
        }
        for row in facts["weekly"]
    ]
    planned = sum(item["planned"] for item in data)
    completed = sum(item["completed"] for item in data)
    return ChartSpec(
        chartId="weekly-plan-completion",
        chartType="weekly_completion",
        title="近 7 天计划执行",
        definition="只统计当前任务执行版本；已取代和放弃版本不计入完成。",
        period="近 7 个自然日",
        unit="任务数，分钟",
        confidence="not_applicable",
        summary=(
            f"近 7 天完成 {completed}/{planned} 项正式计划任务。"
            if planned
            else "近 7 天尚无正式计划任务。"
        ),
        evidenceRefs=["planning:task-execution-current"] if data else [],
        data=data,
        partialReasons=[] if data else ["weekly_execution_unavailable"],
    )


def _next_plan_refs(facts: dict[str, Any], *, report_kind: ReportKind) -> list[str]:
    refs: list[str] = []
    if facts["ability"]:
        refs.append(f"ability-estimate:{facts['ability'][-1]['estimate_version_id']}")
    if facts["mocks"] and facts["mocks"][0].get("review_payload"):
        refs.append(f"mock-session:{facts['mocks'][0]['session_id']}")
    if report_kind in {"single_set", "global"} and facts["sets"]:
        refs.append(f"xingce-session:{facts['sets'][0]['session_id']}")
    return refs


def _report_state(charts: list[ChartSpec]) -> str:
    ready = sum(bool(chart.data) for chart in charts)
    if ready == len(charts):
        return "ready"
    if ready:
        return "partial"
    return "empty"


def _stored_projection(row: RowMapping) -> ReportProjection:
    payload = dict(row["payload"])
    payload["inputFingerprint"] = str(row["input_fingerprint"])
    return ReportProjection.model_validate(payload)


def _jsonable_row(row: RowMapping) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _num(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _period(data: list[dict[str, Any]]) -> str:
    if not data:
        return "暂无数据"
    start = str(data[0].get("at", ""))[:10]
    end = str(data[-1].get("at", ""))[:10]
    return start if start == end else f"{start} 至 {end}"


def _mock_period(mock: dict[str, Any] | None) -> str:
    if mock is None:
        return "暂无数据"
    return str(mock.get("submitted_at") or mock.get("started_at") or "")[:10]
