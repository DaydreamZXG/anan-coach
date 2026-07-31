"""回归：真实后端冒烟中疑似"前后端 API 合同缺口"的 5 个端点。

冒烟报告（audit/HANDOFF-20260801-v0.md §三 A 类）把以下 5 个请求记为
"前端调用了后端不存在的端点（404）"：

1. GET  /api/v2/mock-exams/weekly
2. GET  /api/v2/onboarding/ability-baseline
3. GET  /api/v2/onboarding/position-preference
4. GET  /api/v2/onboarding/study-preference
5. GET  /api/v2/reporting/reports/mock

核实结论（以代码为准）：
- 上述 5 个端点全部已注册、且方法/路径与前端调用一一对应，并非"缺失端点"。
- 冒烟看到的 404 是业务级 404（数据缺失）：
  * onboarding 三个 GET 在用户未建档时按约定返回 `position_preference_missing` /
    `ability_baseline_missing` / `study_preference_missing`（见
    app/domains/onboarding/router.py::_onboarding_error_response），
    前端 controller 均显式消费这些 code 作为"空档页"正常降级。
  * reporting `/reports/{report_kind}` 在用户没有对应报告主体时抛
    ReportNotFound → HTTP 404（业务级），mock-report 页面对无模考数据的
    账号显示错误态属预期降级。
- 前端周模考实际调用的是 POST /api/v1/mock-exams/weekly（非 GET /api/v2），
  该路径在 apps/wechat/miniprogram/api/mock-exam.ts:191 与后端完全一致。

本文件把这条"前端调用路径 → 后端 OpenAPI 已注册"的合同固化为回归测试，
防止将来有人按冒烟报告的 A 类清单误删/误改端点。测试只读 OpenAPI 与业务
404 语义，不触碰其它模块。
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.main import create_app
from tests.test_ability_estimation import _client, _seed_user_and_release

# (method, path) —— 全部来自 apps/wechat/miniprogram/api/*.ts 的实际调用点。
FRONTEND_CONTRACT_ENDPOINTS: list[tuple[str, str]] = [
    # apps/wechat/miniprogram/api/mock-exam.ts:191 createOrRestoreWeekly()
    ("post", "/api/v1/mock-exams/weekly"),
    # apps/wechat/miniprogram/api/onboarding.ts
    ("get", "/api/v2/onboarding/ability-baseline"),
    ("put", "/api/v2/onboarding/ability-baseline"),
    ("get", "/api/v2/onboarding/position-preference"),
    ("put", "/api/v2/onboarding/position-preference"),
    ("get", "/api/v2/onboarding/study-preference"),
    ("put", "/api/v2/onboarding/study-preference"),
    # apps/wechat/miniprogram/api/reporting.ts:34 getReport(kind="mock")
    # 前端调用字面 /api/v2/reporting/reports/mock，后端以模板路径
    # /api/v2/reporting/reports/{report_kind} 注册；kind 枚举见
    # test_reports_report_kind_accepts_the_frontend_report_kinds。
    ("get", "/api/v2/reporting/reports/{report_kind}"),
]


def test_frontend_called_endpoints_are_registered_in_openapi() -> None:
    """冒烟报告的 5 个"404 缺口"端点必须全部出现在后端 OpenAPI 路径表中。"""
    app = create_app()
    schema = app.openapi()
    paths = schema.get("paths", {})

    for method, path in FRONTEND_CONTRACT_ENDPOINTS:
        path_item = paths.get(path)
        assert path_item is not None, f"missing OpenAPI path {method.upper()} {path}"
        assert (
            method in path_item
        ), f"missing method {method.upper()} for {path} (registered: {sorted(path_item)})"


def test_reports_report_kind_accepts_the_frontend_report_kinds() -> None:
    """GET /api/v2/reporting/reports/{report_kind} 必须覆盖前端 ReportKind 全集
    （apps/wechat/miniprogram/api/reporting.ts 的 "single_set" | "weekly" |
    "mock" | "position_gap" | "global"）。"""
    app = create_app()
    schema = app.openapi()
    operation = schema["paths"]["/api/v2/reporting/reports/{report_kind}"]["get"]
    kind_param = next(
        param
        for param in operation["parameters"]
        if param.get("name") == "report_kind" and param.get("in") == "path"
    )
    enum_values = kind_param["schema"]["enum"]
    assert {"single_set", "weekly", "mock", "position_gap", "global"} <= set(enum_values)


def test_reports_mock_without_mock_session_is_business_404_not_missing_route(
    db_session_factory: sessionmaker[Session],
) -> None:
    """reports/mock 对无模考数据的账号返回 404（业务级"报告主体不存在"），
    路由本身存在。这复现冒烟账号看到 404 的真实原因。"""
    _seed_user_and_release(db_session_factory, token="route-contract-mock-404-token")
    client = _client(db_session_factory)
    auth = {"Authorization": "Bearer route-contract-mock-404-token"}
    response = client.get("/api/v2/reporting/reports/mock", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
