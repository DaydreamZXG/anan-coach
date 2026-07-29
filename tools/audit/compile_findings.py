#!/usr/bin/env python3
"""Compile the deduplicated global finding ledger and its Markdown rendering."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit"
DRAFT = AUDIT / "subagents/synthesis/draft-findings.jsonl"
OUTPUT_JSONL = AUDIT / "findings.jsonl"
OUTPUT_MD = AUDIT / "findings.md"

REQUIRED = {
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
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: {error}") from error
    return rows


def evidence(kind: str, path: str, claim: str) -> dict[str, str]:
    return {"type": kind, "path": path, "claim": claim}


def enrich_existing(findings: list[dict[str, Any]]) -> None:
    by_id = {finding["id"]: finding for finding in findings}

    authentication = by_id["ROOT-AUTH-001"]
    authentication["severity_review"] = (
        "最终裁定为 P2：退出、重新登录并重新进入任务仍是可用替代路径；"
        "旧请求的迟到 401 可以清除新 session，证明公共认证协议存在稳定性风险，"
        "但当前证据没有证明主要任务完全不可完成，因此不升为 P1。"
    )

    gates = by_id["ROOT-GATE-001"]
    gates["actual_behavior"] += (
        "；官方 verify_wechat/CI 只进入 npm run check，不执行 554 项 Vitest、9 项云函数测试、"
        "OpenAPI 新鲜度或四个未接线 validator；根 README 仍写 53 页/3 tabs，实际为 "
        "64 注册路由/4 tabs。"
    )
    gates["user_impact"] += (
        " 即使本地存在大量测试和场景，官方合并门禁也可能从未执行它们；文档口径进一步"
        "掩盖真实覆盖分母。"
    )
    gates["evidence"].extend(
        [
            evidence(
                "baseline-summary",
                "audit/baseline/summary.md",
                "CI/package/README wiring and exact pass/fail counts",
            ),
            evidence(
                "ci-workflow",
                ".github/workflows/ci.yml",
                "WeChat job calls verify_wechat only",
            ),
            evidence(
                "verification-entrypoint",
                "scripts/verify_wechat.sh",
                "entrypoint only runs npm run check",
            ),
            evidence(
                "documentation",
                "README.md",
                "stale 53-page/3-tab count",
            ),
        ]
    )
    gates["affected_pages_or_modules"].extend(
        [
            ".github/workflows/ci.yml",
            "scripts/verify_wechat.sh",
            "README.md",
            "全部 Vitest 与 cloud tests 的合并门禁",
        ]
    )
    gates["recommended_fix_direction"].append(
        "定义一个唯一 verify 入口，执行 type/lint、Vitest、cloud、route/structure/visual/interaction/asset 和 OpenAPI 检查"
    )
    gates["recommended_regression_tests"].append(
        "CI 日志必须出现 554 Vitest、9 cloud tests 及所有覆盖 validator 的明确数量"
    )

    contract = by_id["ROOT-CONTRACT-001"]
    contract["actual_behavior"] += (
        "；此外当前 OpenAPI 没有 components.securitySchemes，244 个 operation 中 0 个"
        "声明 security，但后端依赖清点确认 246 个路由中 225 个具有传递 bearer 依赖。"
    )
    contract["user_impact"] += (
        " 生成合同还不能表达哪些接口必须认证，API 清点若只信 OpenAPI 会把受保护接口"
        "误标为公开。"
    )
    contract["evidence"].extend(
        [
            evidence(
                "openapi",
                "audit/api-contract/openapi.json",
                "no securitySchemes and no operation security declarations",
            ),
            evidence(
                "route-auth-inventory",
                "audit/subagents/e/evidence/fastapi-route-auth-inventory.csv",
                "225 of 246 routes have transitive bearer dependencies in code",
            ),
        ]
    )
    contract["affected_pages_or_modules"].extend(
        [
            "FastAPI OpenAPI auth metadata",
            "audit/api-inventory.csv 的认证分类",
        ]
    )
    contract["recommended_regression_tests"].append(
        "OpenAPI 必须声明 bearer security scheme，代码受保护路由与 operation security 集合对账"
    )

    idempotency = by_id["ROOT-IDEMP-001"]
    idempotency["id"] = "ROOT-IDEMP-CLIENT-001"
    idempotency["severity"] = "P2"
    idempotency["affected_pages_or_modules"] = [
        value
        for value in idempotency["affected_pages_or_modules"]
        if "practice" not in value
    ]
    idempotency["deduplication"] = (
        "岸岸、今日容量、督学偏好三个页面合并为客户端 operation-attempt 根因；"
        "刷题内岸岸留在 hypotheses；后端 verdict/XP 另列 BACKEND-COMMAND-001。"
    )
    idempotency["evidence_boundary"] = (
        "前端请求行为已动态复现；后端 stable-key replay/CAS 由正式源码与既有测试证明，"
        "未执行 response-loss 跨服务 E2E，因此按 P2 收敛。"
    )

    request = by_id["ROOT-REQUEST-001"]
    request["user_impact"] = (
        "report-center 会在已经显示较新 projection 后退回旧模考状态、报告入口和下一步动作，"
        "用户可能基于过期信息继续操作；刷新或重进是替代路径。"
    )
    request["affected_pages_or_modules"] = [
        "动态确认：pages/report-center/index",
        "公共能力缺口：api/client.ts 不暴露 RequestTask/cancellation",
    ]
    request["deduplication"] = (
        "正式实际行为只覆盖 report-center；ability、me、membership、notification-settings、"
        "privacy、private-knowledge、profile/archive 仅保留为回归扫描候选，不计已确认影响页。"
    )
    request["evidence_boundary"] = (
        "只有 report-center 动态复现；其他同构页面属于静态回归范围/hypothesis。"
    )

    gates["evidence"].extend(
        [
            evidence(
                "source",
                "apps/wechat/scripts/validate-structure.mjs",
                "uses main-package app.pages semantics",
            ),
            evidence(
                "source",
                "apps/wechat/scripts/validate-visual-coverage.mjs",
                "uses main-package app.pages semantics",
            ),
            evidence(
                "source",
                "apps/wechat/scripts/validate-interaction-coverage.mjs",
                "uses main-package app.pages semantics",
            ),
        ]
    )


SUPPLEMENTAL_FINDINGS: list[dict[str, Any]] = [
    {
        "id": "BACKEND-COMMAND-001",
        "severity": "P1",
        "confidence": "high",
        "category": "idempotency / transaction-boundary / progression-consistency",
        "functional_domain": "legacy review session、practice answer、等级进度",
        "page_or_business_flow": "submit verdict → persist answer → award XP → rank event/promotion",
        "preconditions": ["已认证用户拥有可作答 review card，同一逻辑答案发生网络重试"],
        "reproduction_steps": [
            "对同一 /api/v1/review-sessions/{docket}/cards/{card}/verdict 连续 POST 相同 body",
            "比较两次响应的 verdictId",
            "查询 learner.verdicts、official_ranks 与 rank_events",
        ],
        "expected_behavior": (
            "同一 command 重试返回原结果且不重复副作用；答案和进度原子提交，或以同一 command key "
            "通过幂等 outbox 只发布一次。"
        ),
        "actual_behavior": (
            "两次响应返回相同 verdictId，数据库只有 1 条 verdict，却得到 XP=20 和 2 条 xp_gain；"
            "verdict、docket-card、active date、XP、rank event、promotion 由不同层分别 commit。"
        ),
        "user_impact": (
            "普通网络重试会产生错误等级结果并重复发放 XP；中途失败还可留下答案已保存但进度未写，"
            "或进度被重放而没有新的逻辑答案。"
        ),
        "evidence": [
            evidence(
                "audit-probe",
                "tools/audit/e/test_confirmed_backend_risks.py",
                "same verdict replay preserves one verdict but awards XP twice",
            ),
            evidence(
                "test-run",
                "audit/subagents/e/evidence/confirmed-risk-probes.log",
                "duplicate-XP probe passes on ephemeral PostgreSQL",
            ),
            evidence(
                "transaction-inventory",
                "audit/subagents/e/evidence/domain-files-with-explicit-commits.txt",
                "repository/service commit ownership inventory",
            ),
            evidence(
                "source",
                "backend/app/domains/review_session/service.py",
                "verdict and progression orchestration",
            ),
            evidence(
                "source",
                "backend/app/domains/review_session/repository.py",
                "verdict repository commit boundary",
            ),
        ],
        "inferred_root_cause": (
            "后端命令没有 immutable command identity；use-case transaction ownership 下沉到多个 "
            "repository/service commit，重放与部分失败无法共享一个原子边界。"
        ),
        "affected_pages_or_modules": [
            "动态确认：/api/v1/review-sessions/*/verdict",
            "learner.verdicts / official_ranks / rank_events",
            "同构待回归：/api/v1/practice/tasks/*/answers",
        ],
        "recommended_fix_direction": [
            "要求不可变 command key/record 并对 same-key/different-payload fail closed",
            "把 verdict 与 progression 收敛到单一 unit-of-work",
            "如需异步发布，以 answer command key 驱动幂等 outbox/rank consumer",
        ],
        "recommended_regression_tests": [
            "相同 command 重放后 verdict=1、xp_gain=1、XP 只增加一次",
            "same-key/different-payload conflict",
            "并发重复 submit",
            "在每个 durable step 后注入失败并验证答案/进度不变量",
        ],
        "status": "confirmed",
        "source_findings": ["E-004"],
        "deduplication": (
            "只把动态确认的 verdict/XP 作为实际行为；practice answer 是同构回归范围，"
            "不冒充已动态复现。"
        ),
        "evidence_boundary": "本地临时 PostgreSQL 动态确认；未访问生产。",
        "recommended_wave": "Wave 1",
        "ledger_state": "open",
    },
    {
        "id": "FLOW-MOCK-001",
        "severity": "P2",
        "confidence": "high",
        "category": "state-machine / conflict-resolution / server-terminal-state",
        "functional_domain": "模考",
        "page_or_business_flow": "mock-exam submit → 409 resolve → latest submitted/report",
        "preconditions": [
            "本地打开 assigned 模考并拥有编辑 lease",
            "另一设备或先前请求已经使服务端会话进入 submitted",
        ],
        "reproduction_steps": [
            "当前设备点击交卷并让 submit 返回 mock_submission_conflict 409",
            "完成 conflict/resolve，让 GET session 返回完整且 status=submitted 的最新会话",
            "检查页面 status、interactionLocked、timer、答题卡和报告入口",
        ],
        "expected_behavior": (
            "以服务端终态为准，停止计时、锁定作答、清除 pending，并进入已交卷或报告生成状态。"
        ),
        "actual_behavior": (
            "本地清 pending 后抛普通 Error；外层 catch 解除 interaction lock，status 保持 ready，"
            "WXML 继续开放答题卡、草稿、工具和 footer。"
        ),
        "user_impact": (
            "用户在当前页无法确认交卷成功或进入报告，并继续看到可编辑的错误状态；退出重进可恢复，"
            "因此有替代路径。"
        ),
        "evidence": [
            evidence(
                "audit-test",
                "tools/audit/b/mock-terminal-conflict.audit.test.ts",
                "server submitted while local page remains ready/unlocked",
            ),
            evidence(
                "test-run",
                "audit/subagents/b/evidence/test-runs.md",
                "mock terminal conflict reproduction passes",
            ),
            evidence(
                "source-index",
                "audit/subagents/b/evidence/source-evidence.md",
                "exact submit/conflict/catch branches",
            ),
        ],
        "inferred_root_cause": (
            "submitWithLease 已识别 latest session 终止，却把受控终态转换成普通异常；外层分支"
            "没有用 latest session 收敛 runtime。"
        ),
        "affected_pages_or_modules": [
            "pages/mock-exam/index 的交卷冲突分支",
            "mock runtime terminal-state projection",
        ],
        "recommended_fix_direction": [
            "把 latest.status != assigned 建模为成功的终态恢复结果",
            "统一停表、锁交互、更新 session、清 pending，并提供报告或报告生成中入口",
        ],
        "recommended_regression_tests": [
            "409 → latest submitted",
            "409 → latest reviewed",
            "终态后 timer 停止、作答入口禁用、pending 清理、报告路由可达",
        ],
        "status": "confirmed",
        "source_findings": ["B-CORE-002"],
        "deduplication": "不并入 idempotency；这里服务端终态已经读取成功，缺陷是本地状态收敛。",
        "evidence_boundary": "动态隔离测试确认；未连接真实后端。",
        "recommended_wave": "Wave 2",
        "ledger_state": "open",
    },
    {
        "id": "STATE-ANAN-001",
        "severity": "P2",
        "confidence": "high",
        "category": "loading-empty-error-success conflation",
        "functional_domain": "AI 岸岸",
        "page_or_business_flow": "岸岸首页聚合 learning profile + today plan",
        "preconditions": [
            "learning-brain profile 成功并返回有效版本/证据",
            "GET /api/v2/plans/today 返回 503",
        ],
        "reproduction_steps": [
            "向岸岸页面注入成功 profile 和失败 today 响应",
            "调用 loadContext",
            "检查 contextStatus、errorMessage、目标与任务文案",
        ],
        "expected_behavior": (
            "显示 profile 可用但今日计划未载入的 partial/error 状态；不得对目标或任务是否存在下结论。"
        ),
        "actual_behavior": (
            "contextStatus=ready、errorMessage 为空，并显示“主目标待确认”“今日 0 项任务”"
            "“还没有确认主目标岗位”；提问入口仍按完整 ready 工作。"
        ),
        "user_impact": (
            "已有目标与计划的用户会看到相反事实，并被引导重复确认岗位；AI 上下文可用性被错误表达。"
        ),
        "evidence": [
            evidence(
                "audit-test",
                "tools/audit/d/state-contract.audit.test.ts",
                "planning 503 is presented as no goal/zero tasks",
            ),
            evidence(
                "test-run",
                "audit/subagents/d/evidence/test-run.txt",
                "partial-context reproduction passes",
            ),
            evidence(
                "source",
                "apps/wechat/miniprogram/pages/anan/index.ts",
                "catch(() => null) erases today error before projection",
            ),
        ],
        "inferred_root_cause": (
            "聚合页面用 null 同时表达资源为空和请求失败，没有 absent/empty/unavailable/stale 的 tagged state。"
        ),
        "affected_pages_or_modules": ["pages/anan/index", "companionContextView"],
        "recommended_fix_direction": [
            "使用 allSettled/tagged result 保留每个来源状态",
            "依赖失败来源的 insight 不生成确定性结论",
            "partial 状态提供独立重试，不阻断仍安全的 profile 信息",
        ],
        "recommended_regression_tests": [
            "profile 成功/today 失败",
            "profile 失败/today 成功",
            "真实无目标与 today 失败的文案不得相同",
            "stale cache + refresh failure",
        ],
        "status": "confirmed",
        "source_findings": ["D-F004"],
        "deduplication": "当前只有岸岸动态确认，不上升为所有聚合页的重复问题。",
        "evidence_boundary": "动态隔离测试确认；无真实 API。",
        "recommended_wave": "Wave 2",
        "ledger_state": "open",
    },
    {
        "id": "ROOT-MIGRATION-001",
        "severity": "P0",
        "confidence": "high",
        "category": "migration / release-governance / startup-readiness",
        "functional_domain": "部署、FastAPI/worker 启动、PostgreSQL schema compatibility",
        "page_or_business_flow": "artifact activation → Alembic baseline-to-head → /ready → runtime cutover",
        "preconditions": [
            "使用当前 artifact 和仓库定义的 audited legacy baseline",
            "本地临时 PostgreSQL；不访问生产",
        ],
        "reproduction_steps": [
            "运行 cd backend && .venv/bin/alembic heads，确认单 head 20260728_70",
            "比较 Settings 默认 _68、ops/env.example _69、artifact contract _70",
            "运行 test_wechat_identity_migration_upgrades_from_audited_baseline",
            "观察升级在 revision 20260716_33 调用当前 MockExamRepository 时 TypeError",
        ],
        "expected_behavior": (
            "所有 artifact/runtime/ops/readiness 权威解析到同一 head，audited baseline 能执行每个迁移并启动当前应用。"
        ),
        "actual_behavior": (
            "revision 声明为 _68/_69/_70 三套；激活脚本会拒绝不等于 artifact head 的配置；"
            "真实历史回放在 revision 33 因历史 migration import 可变当前应用接口而停止。"
        ),
        "user_impact": (
            "使用仓库记录的 baseline 的环境无法升级到当前 head，release 可在 cutover 前被阻断，"
            "或 /ready 因陈旧 runtime revision 拒绝已正确迁移的数据库。属于可复现的启动/发布阻塞；"
            "本轮未声称生产当前已处于该状态。"
        ),
        "evidence": [
            evidence(
                "migration-report",
                "audit/subagents/e/migration-audit.md",
                "single-head topology and baseline-to-head execution failure",
            ),
            evidence(
                "targeted-test",
                "audit/subagents/e/evidence/failure-release-and-migration-targeted.log",
                "upgrade stops at revision 33 with mutable repository signature",
            ),
            evidence(
                "revision-inventory",
                "audit/subagents/e/evidence/revision-drift.txt",
                "tracked revision authorities disagree",
            ),
            evidence(
                "runtime-import-inventory",
                "audit/subagents/e/evidence/migrations-import-runtime-code.txt",
                "historical migrations import mutable app modules",
            ),
            evidence(
                "baseline",
                "audit/baseline/results.tsv",
                "heads/history pass while executable migration pytest fails",
            ),
        ],
        "inferred_root_cause": (
            "数据库兼容 revision 被手工复制到多处；历史迁移依赖可变 app.* 接口；普通 pytest 使用 shadow DDL "
            "而非真实 Alembic chain，拓扑与可执行性没有统一权威。"
        ),
        "affected_pages_or_modules": [
            "全部 API 与 worker artifact activation",
            "/ready",
            "backend/migrations/versions/20260716_33_stage6_mock_reporting.py",
            "backend/app/core/config.py",
            "ops/release-db-compatibility.env",
            "ops/scripts/activate_release.sh",
            "backend/tests database fixture",
        ],
        "recommended_fix_direction": [
            "由 artifact Alembic head 生成唯一兼容权威并供 settings/ops/readiness/tests 消费",
            "历史 migration 的 transform 固化在 migration 内或永久版本化兼容模块",
            "CI/release preflight 必须运行 audited-baseline → head PostgreSQL 回放与应用 smoke",
        ],
        "recommended_regression_tests": [
            "audited baseline → head",
            "settings/artifact/ops/readiness revision 完全相等",
            "head 数据库上的 API/worker startup smoke",
            "shadow fixture 与真实链的 schema/data fingerprint 对账",
        ],
        "status": "confirmed",
        "source_findings": ["E-001"],
        "deduplication": "把 revision 漂移、shadow schema 和 revision 33 失败合成一个迁移/发布权威根因。",
        "evidence_boundary": "本地 audited baseline 确认；未检查生产数据库当前 revision。",
        "recommended_wave": "Wave 0",
        "ledger_state": "open",
    },
    {
        "id": "BACKEND-REPLAY-001",
        "severity": "P2",
        "confidence": "high",
        "category": "content-release / immutable-data / exact-replay",
        "functional_domain": "方法卡、知识图谱发布、岸岸知识",
        "page_or_business_flow": "content import/republication → immutable release → retry",
        "preconditions": ["方法卡已经被 active 或 retired knowledge-map release 引用"],
        "reproduction_steps": [
            "插入方法卡并创建/激活引用它的 release",
            "用完全相同的逻辑内容再次调用 MethodCardRepository.upsert_cards",
            "运行 targeted immutability test 并观察 BEFORE INSERT trigger",
        ],
        "expected_behavior": (
            "精确重放成功且不修改 updated_at；相同 ID 的内容漂移才被拒绝。"
        ),
        "actual_behavior": (
            "BEFORE INSERT trigger 在 ON CONFLICT 的等值判断前抛出 immutable 错误；"
            "即使 ON CONFLICT DO NOTHING 也无法精确重放。"
        ),
        "user_impact": (
            "发布后的安全重试和重复导入失败，自动发布/恢复流程非幂等，内容 rollout 可被卡住。"
        ),
        "evidence": [
            evidence(
                "targeted-test",
                "audit/subagents/e/evidence/failure-release-and-migration-targeted.log",
                "exact replay is rejected",
            ),
            evidence(
                "baseline-cluster",
                "audit/subagents/e/evidence/pytest-failure-clusters.csv",
                "two tracked failures map to this root",
            ),
            evidence(
                "source",
                "backend/migrations/versions/20260727_66_knowledge_release_immutability.py",
                "BEFORE INSERT immutability trigger",
            ),
        ],
        "inferred_root_cause": (
            "读侧不可变约束放在表级 INSERT trigger，而写侧仍把 replay 表达为 INSERT ... ON CONFLICT。"
        ),
        "affected_pages_or_modules": [
            "MethodCardRepository",
            "scripts/import_method_cards.py",
            "knowledge-map publisher",
            "Companion shared-card seed/refresh",
        ],
        "recommended_fix_direction": [
            "定义版本化 command/replay 协议",
            "先解析现存 canonical fingerprint，或通过 stored procedure 对 exact replay 返回成功、对 drift 拒绝",
        ],
        "recommended_regression_tests": [
            "exact replay 保持 timestamps",
            "changed payload 失败",
            "并发 exact replay 收敛",
            "publisher 在上次 commit 后重试成功",
        ],
        "status": "confirmed",
        "source_findings": ["E-002"],
        "deduplication": (
            "两个 baseline 测试症状合并为发布后 exact-replay 根因；现有证据只到发布/恢复阻断，"
            "未证明当前核心用户任务完全不可用，因此总审计按 P2 收敛。"
        ),
        "evidence_boundary": "本地 PostgreSQL 动态确认。",
        "recommended_wave": "Wave 1",
        "ledger_state": "open",
    },
    {
        "id": "ROOT-TESTDATA-001",
        "severity": "P2",
        "confidence": "high",
        "category": "test-infrastructure / frozen-content-contract / regression-governance",
        "functional_domain": "计划、刷题、结果、错因、媒体、结算及行测验收",
        "page_or_business_flow": "shared test seed → active frozen release → Xingce plan/task → intended assertion",
        "preconditions": ["运行 Git-tracked backend suite 的标准临时 PostgreSQL fixture"],
        "reproduction_steps": [
            "运行 failure-shared-planning-release-gate.log 中三条定向测试",
            "观察 plan 200 但只包含 essay，公共 helper 查找 xingce 时 StopIteration",
            "检查 shared seed 只插 questions/options，而新选择器要求 active vocab/release/binding/snapshot",
            "对照 pytest-failure-clusters.csv 的 52 个 node id",
        ],
        "expected_behavior": (
            "公共 factory 构造满足当前 frozen-release authority 的数据，使每条测试到达自身业务断言。"
        ),
        "actual_behavior": (
            "factory 仍模拟旧 mutable question-table 合同；52 个正式测试在 seed、release activation、"
            "binding validation 或 task lookup 阶段退出，没有执行原本断言。"
        ),
        "user_impact": (
            "大量测试数存在但核心行测回归实际上没有执行，新缺陷只能在人工测试中继续暴露。"
            "这是一条测试基础设施问题，不代表 52 个线上 Bug。"
        ),
        "evidence": [
            evidence(
                "failure-map",
                "audit/subagents/e/evidence/pytest-failure-clusters.csv",
                "52 tracked failures map to one frozen-release fixture root",
            ),
            evidence(
                "targeted-tests",
                "audit/subagents/e/evidence/failure-shared-planning-release-gate.log",
                "representative plan omits Xingce before assertions",
            ),
            evidence(
                "targeted-tests",
                "audit/subagents/e/evidence/failure-media-and-timing-targeted.log",
                "media/timing tests fail at same shared helper",
            ),
            evidence(
                "baseline",
                "audit/baseline/backend-pytest.xml",
                "993 total; 69 failed including 52 tracked cases in this cluster",
            ),
        ],
        "inferred_root_cause": (
            "生产内容权威变为 active vocabulary + release-scoped binding + immutable snapshot，"
            "但复用 test factory/acceptance fixture 没有随合同原子升级或版本化。"
        ),
        "affected_pages_or_modules": [
            "52 个 node id，详见 failure cluster CSV",
            "backend/tests shared Xingce/planning seed builders",
            "计划、行测 attempt/result/cause/media 的回归门禁",
        ],
        "recommended_fix_direction": [
            "建立唯一 canonical frozen-release test factory",
            "旧 builder 统一委托它；stale/invalid release 使用显式 negative factory",
            "先恢复测试到达率，再判断是否仍有真实业务断言失败",
        ],
        "recommended_regression_tests": [
            "52 条测试均到达预期业务断言",
            "factory 自检 release fingerprint/binding/snapshot/delivery eligibility",
            "禁止静默 fallback 到 legacy product.questions",
        ],
        "status": "confirmed",
        "source_findings": ["E-003"],
        "deduplication": "52 个失败只建一条；严重度在总审计按“门禁失效、未证实运行时故障”收敛为 P2。",
        "evidence_boundary": "确认测试基础设施漂移；不声称对应 52 个生产功能均损坏。",
        "recommended_wave": "Wave 0",
        "ledger_state": "open",
    },
    {
        "id": "ROOT-JOBS-001",
        "severity": "P1",
        "confidence": "high",
        "category": "background-job / lease-recovery / deployment-topology",
        "functional_domain": "隐私导出/注销、iLink 媒体删除、行测错因分析",
        "page_or_business_flow": "enqueue → claim → durable/external work → complete/retry",
        "preconditions": [
            "worker 在 claim commit 后退出，或按仓库标准 release topology 部署",
        ],
        "reproduction_steps": [
            "在临时 PostgreSQL enqueue 并 claim data-rights job，commit running",
            "推进审计时间 7 天并再次 process_next，读取 job 状态",
            "对账 CauseAgent producer/entrypoint 与 ops/systemd、activate_release runtime_units",
            "检查 iLink deleting claim 的后续可领取状态集合",
        ],
        "expected_behavior": (
            "每个 produced job type 都有部署 consumer、lease 过期、轮询/启动自动恢复和 readiness/metrics owner。"
        ),
        "actual_behavior": (
            "7 天后 data-rights job 仍为 running 且带旧 claim token；iLink deleting 可在 crash 后不可见；"
            "cause-agent 会 enqueue 且有 Python entrypoint，但标准 systemd/activation 不启动它，"
            "entrypoint 也未调用已有 recover_stale_claims。"
        ),
        "user_impact": (
            "隐私导出/删除或对象清理可能永久卡住；行测错因分析/reflection 在标准部署中可一直 queued，"
            "核心学习链路无法完成。"
        ),
        "evidence": [
            evidence(
                "audit-probe",
                "tools/audit/e/test_confirmed_backend_risks.py",
                "seven-day stale data-rights claim remains running",
            ),
            evidence(
                "test-run",
                "audit/subagents/e/evidence/confirmed-risk-probes.log",
                "stale-claim probe passes",
            ),
            evidence(
                "backend-report",
                "audit/subagents/e/report.md",
                "producer/consumer/recovery topology inventory",
            ),
            evidence(
                "source",
                "ops/scripts/activate_release.sh",
                "standard runtime units omit cause-agent worker",
            ),
            evidence(
                "source",
                "backend/app/domains/ilink_channel/repository.py",
                "deletion claims move to deleting while future claims select pending/failed",
            ),
            evidence(
                "source",
                "backend/app/workers/ilink_channel.py",
                "iLink worker claim/delete sequence",
            ),
            evidence(
                "source",
                "backend/app/workers/xingce_cause_agent.py",
                "cause worker entrypoint exists but does not invoke stale recovery",
            ),
            evidence(
                "deployment-units",
                "ops/systemd",
                "repository standard units contain no cause-agent service",
            ),
        ],
        "inferred_root_cause": (
            "没有 build-time job registry 将 producer、consumer unit、lease duration、stale recovery、"
            "readiness 和告警绑定为一个可执行合同。"
        ),
        "affected_pages_or_modules": [
            "DataRightsWorker",
            "ILinkWorker/object deletion",
            "CauseAgentWorker",
            "privacy pages",
            "reflection/cause-analysis",
            "ops/systemd and activate_release",
        ],
        "recommended_fix_direction": [
            "建立 job-type registry 与部署断言",
            "使用 lease_expires_at 并在每次 claim/start 前恢复 stale claim",
            "把 cause-agent unit 纳入标准 topology 与 readiness/queue-age metrics",
        ],
        "recommended_regression_tests": [
            "claim 后、外部调用后、completion 前 kill 并重启，最终只收敛一次",
            "deployment manifest 对每个 producer 断言一个 active consumer",
            "stale deleting iLink object 可回收",
            "cause job 在精确 release topology 下到达终态",
        ],
        "status": "confirmed",
        "source_findings": ["E-005"],
        "deduplication": "三个 domain 症状合并为统一 job registry/lease/deployment 根因。",
        "evidence_boundary": "仓库 topology 和本地 claim 动态确认；未声称生产已有卡单。",
        "recommended_wave": "Wave 1",
        "ledger_state": "open",
    },
    {
        "id": "BACKEND-AUTHZ-001",
        "severity": "P1",
        "confidence": "high",
        "category": "authorization / content-integrity / pre-submit-disclosure",
        "functional_domain": "题库与练习公平性",
        "page_or_business_flow": "anonymous knowledge search/detail → answer/explanation versus authenticated attempt",
        "preconditions": ["无需账号或 bearer token；API 网络可达"],
        "reproduction_steps": [
            "无 Authorization 请求 /api/v1/knowledge/questions/search?domain=objective&limit=100",
            "观察 summary 包含 questionId、stem、answer",
            "无 Authorization 请求 /api/v1/knowledge/questions/{questionId}",
            "观察 answer、options、preferred analysis、tags/chunks/assets",
        ],
        "expected_behavior": (
            "learner delivery 应认证且有阶段；提交前 projection 不返回答案/解析；bulk corpus 读取使用独立权限。"
        ),
        "actual_behavior": (
            "search/detail 只有 DB/service dependency；search 可枚举 100 条并直接选择 answer，detail "
            "无认证/状态过滤并序列化完整答案与解析。既有无认证 contract test 明确断言该行为。"
        ),
        "user_impact": (
            "匿名调用者可枚举受治理题干、答案和解析，绕过练习答题时序，损害题库价值和测评完整性。"
        ),
        "evidence": [
            evidence(
                "route-auth-inventory",
                "audit/subagents/e/evidence/fastapi-route-auth-inventory.csv",
                "knowledge search/detail have no bearer dependency",
            ),
            evidence(
                "contract-tests",
                "audit/subagents/e/evidence/anonymous-content-contract-tests.log",
                "no-auth tests assert answer and explanation exposure",
            ),
            evidence(
                "source",
                "backend/app/domains/knowledge/router.py",
                "public route dependencies",
            ),
            evidence(
                "source",
                "backend/app/domains/knowledge/schemas.py",
                "answer/explanation response fields",
            ),
        ],
        "inferred_root_cause": (
            "内部 content-browser schema 被直接挂到 public API，没有 delivery-stage projection 或 learner/admin scope。"
        ),
        "affected_pages_or_modules": [
            "GET /api/v1/knowledge/questions/search",
            "GET /api/v1/knowledge/questions/{question_id}",
            "knowledge schemas/repository",
        ],
        "recommended_fix_direction": [
            "由产品/安全 owner 明确 route policy",
            "分离 safe pre-submit projection 与 privileged corpus detail",
            "按 learner/admin scope 认证，答案解析只在授权 attempt 的允许阶段返回",
        ],
        "recommended_regression_tests": [
            "anonymous 401/403 或 safe projection",
            "authenticated pre-submit 无 answer",
            "authorized post-submit 才有 answer",
            "bulk enumeration permission/rate tests",
            "draft/inactive ID 不可匿名读取",
        ],
        "status": "confirmed-exposure;policy-review-required",
        "source_findings": ["E-006"],
        "deduplication": "search 与 detail 两个端点合并为同一匿名内容 projection 根因。",
        "evidence_boundary": "技术暴露已确认；未验证生产 gateway 是否另行封锁，产品公开内容政策需确认。",
        "recommended_wave": "Wave 1",
        "ledger_state": "open",
    },
]


def normalize_c_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = AUDIT / "subagents/c/findings.jsonl"
    if not path.exists():
        return findings
    for item in load_jsonl(path):
        aliases = {
            "functionalDomain": "functional_domain",
            "pageOrFlow": "page_or_business_flow",
            "reproductionSteps": "reproduction_steps",
            "expectedBehavior": "expected_behavior",
            "actualBehavior": "actual_behavior",
            "userImpact": "user_impact",
            "inferredRootCause": "inferred_root_cause",
            "affectedPagesOrModules": "affected_pages_or_modules",
            "recommendedFixDirection": "recommended_fix_direction",
            "recommendedRegressionTest": "recommended_regression_tests",
        }
        for source, target in aliases.items():
            if target not in item and source in item:
                item[target] = item.pop(source)
        for field in (
            "preconditions",
            "reproduction_steps",
            "affected_pages_or_modules",
            "recommended_fix_direction",
            "recommended_regression_tests",
        ):
            if isinstance(item.get(field), str):
                item[field] = [item[field]]
        for entry in item.get("evidence", []):
            if "claim" not in entry and "detail" in entry:
                entry["claim"] = entry.pop("detail")
        item.setdefault("source_findings", [item.get("id", "C")])
        item.setdefault("deduplication", "C 已按公共视觉根因去重。")
        item.setdefault("evidence_boundary", "当前 commit 微信开发者工具真实渲染。")
        item.setdefault(
            "recommended_wave",
            "Wave 4" if item.get("id") == "UI-ESSAY-TITLE-001" else "Wave 3",
        )
        item.setdefault("ledger_state", "open")
        findings.append(item)
    return findings


def validate(findings: list[dict[str, Any]]) -> None:
    ids = [item.get("id") for item in findings]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate finding ids: {duplicates}")
    for item in findings:
        missing = sorted(REQUIRED - item.keys())
        if missing:
            raise ValueError(f"{item.get('id')}: missing fields {missing}")
        if item["severity"] not in {"P0", "P1", "P2", "P3"}:
            raise ValueError(f"{item['id']}: invalid severity {item['severity']}")
        if item["confidence"] not in {"high", "medium", "low"}:
            raise ValueError(f"{item['id']}: invalid confidence")
        for field in (
            "preconditions",
            "reproduction_steps",
            "affected_pages_or_modules",
            "recommended_fix_direction",
            "recommended_regression_tests",
            "source_findings",
        ):
            if not isinstance(item.get(field), list) or not item[field]:
                raise ValueError(f"{item['id']}: {field} must be a non-empty list")
        if not isinstance(item["evidence"], list) or not item["evidence"]:
            raise ValueError(f"{item['id']}: missing evidence")
        for entry in item["evidence"]:
            path = ROOT / entry["path"].split(":", 1)[0]
            if not path.exists():
                raise ValueError(f"{item['id']}: missing evidence path {entry['path']}")


def markdown(findings: list[dict[str, Any]]) -> str:
    counts = Counter(item["severity"] for item in findings)
    lines = [
        "# 全局审计问题台账",
        "",
        "审计 commit：`01752f1d7b8b606b6900160eebba0888d8def294`  ",
        "性质：只读；已按公共根因去重，hypotheses 与审计阻塞项不计入本表。",
        "",
        "## 数量",
        "",
        f"- P0：{counts['P0']}",
        f"- P1：{counts['P1']}",
        f"- P2：{counts['P2']}",
        f"- P3：{counts['P3']}",
        f"- 合计：{len(findings)}",
        "",
        "## 索引",
        "",
        "| ID | 严重度 | 置信度 | 类别 | 功能域 | 修复波次 |",
        "|---|---:|---:|---|---|---|",
    ]
    for item in findings:
        lines.append(
            f"| `{item['id']}` | {item['severity']} | {item['confidence']} | "
            f"{item['category']} | {item['functional_domain']} | "
            f"{item.get('recommended_wave', '')} |"
        )
    for item in findings:
        lines.extend(
            [
                "",
                f"## {item['id']} — {item['actual_behavior'].split('；', 1)[0]}",
                "",
                f"- severity：`{item['severity']}`",
                f"- confidence：`{item['confidence']}`",
                f"- category：{item['category']}",
                f"- 功能域：{item['functional_domain']}",
                f"- 页面或业务链路：{item['page_or_business_flow']}",
                f"- 状态：`{item['status']}`",
                f"- 推荐波次：{item.get('recommended_wave', '')}",
                "",
                "### 前置条件",
                "",
            ]
        )
        lines.extend(f"{index}. {value}" for index, value in enumerate(item["preconditions"], 1))
        lines.extend(["", "### 复现步骤", ""])
        lines.extend(
            f"{index}. {value}" for index, value in enumerate(item["reproduction_steps"], 1)
        )
        lines.extend(
            [
                "",
                "### 期望 / 实际 / 影响",
                "",
                f"- 期望行为：{item['expected_behavior']}",
                f"- 实际行为：{item['actual_behavior']}",
                f"- 用户影响：{item['user_impact']}",
                "",
                "### 证据",
                "",
            ]
        )
        for entry in item["evidence"]:
            lines.append(f"- `{entry['path']}` — {entry['claim']}")
        lines.extend(
            [
                "",
                "### 根因与范围",
                "",
                f"- 推定根因：{item['inferred_root_cause']}",
                "- 受影响页面或模块：",
                "",
            ]
        )
        lines.extend(f"  - {value}" for value in item["affected_pages_or_modules"])
        lines.extend(["", "### 推荐修复方向", ""])
        lines.extend(
            f"{index}. {value}"
            for index, value in enumerate(item["recommended_fix_direction"], 1)
        )
        lines.extend(["", "### 推荐回归测试", ""])
        lines.extend(
            f"{index}. {value}"
            for index, value in enumerate(item["recommended_regression_tests"], 1)
        )
        lines.extend(
            [
                "",
                f"- 去重说明：{item.get('deduplication', '')}",
                f"- 证据边界：{item.get('evidence_boundary', '')}",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    findings = load_jsonl(DRAFT)
    enrich_existing(findings)
    findings.extend(SUPPLEMENTAL_FINDINGS)
    # E-006 proves anonymous technical exposure, but existing contract tests accept it
    # and no product/security policy in scope establishes that the corpus must be private.
    # Keep it in hypotheses until that policy is confirmed; a confirmed authorization
    # issue would be P0 under the user's severity definition, not an arbitrary P1.
    findings = [item for item in findings if item["id"] != "BACKEND-AUTHZ-001"]
    findings = normalize_c_findings(findings)
    for item in findings:
        item["ledger_state"] = "open"
    severity_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    findings.sort(key=lambda item: (severity_order[item["severity"]], item["id"]))
    validate(findings)
    OUTPUT_JSONL.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in findings),
        encoding="utf-8",
    )
    OUTPUT_MD.write_text(markdown(findings), encoding="utf-8")
    counts = Counter(item["severity"] for item in findings)
    print(
        f"Compiled {len(findings)} findings: "
        + ", ".join(f"{key}={counts[key]}" for key in ("P0", "P1", "P2", "P3"))
    )


if __name__ == "__main__":
    main()
