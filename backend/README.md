# 岸岸 Backend

FastAPI 后端为微信小程序提供身份、建档、岗位、计划、学习、内容、AI、商业和数据权利等
领域能力。产品范围与系统边界见
[正式产品与系统说明](../docs/product/anan-product-system-spec.md)。

## 本地开发

推荐使用锁定依赖：

```bash
uv sync --locked --extra dev
uv run pytest
uv run uvicorn app.main:app --reload
```

无法安装 `uv` 时，可以显式使用兼容虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
PYTHON="$PWD/.venv/bin/python" ../scripts/verify_backend.sh
uvicorn app.main:app --reload
```

`scripts/verify_backend.sh` 默认使用隔离的 `uv run --locked --extra dev` 环境，不会自动选择
`backend/.venv`。CI 应使用锁定依赖路径。

## 权威入口

- 应用工厂：`app/main.py`
- 路由注册：`app/api/router.py`
- 生成接口契约：运行应用后的 `/openapi.json` 或 `app.openapi()`
- 数据库迁移：`migrations/versions/`
- 环境变量样例：`ops/env.example`
- 后端验证：`../scripts/verify_backend.sh`
- 发布与恢复：`../ops/RUNBOOK.md`

不要在 README 中维护容易失真的手写全接口清单。查询当前接口和迁移 head：

```bash
uv run python - <<'PY'
from app.main import app
print(f"OpenAPI paths: {len(app.openapi()['paths'])}")
PY

uv run alembic heads
```

2026-08-12 审计时生成契约包含 246 个路径，迁移目录包含 107 个文件，最新迁移文件为
`20260810_106_companion_user_memory.py`。这些是审计快照，不替代上述实时命令。

## 不可破坏的工程边界

- 客户端只提交后端会话 token，不提交可信的 learner ID；微信 secret 只存在服务端；
- 规范事实由对应领域拥有，跨域写入通过所有者命令、版本和证据引用完成；
- 命令写路径必须处理幂等重放、输入指纹、乐观并发和失败恢复；
- AI 输出默认是候选，不能直接改写资格、岗位、能力、错因、掌握、计划或权益事实；
- 内容、题目、rubric 和 provider 都必须绑定版本、考试范围、授权和发布状态；
- 外部 provider 的不确定结果、重复收费风险和 PII 出口按 fail-closed 处理；
- 迁移由 Alembic 单头链拥有，不从 README 中复制固定 head 做发布判断；
- 数据导出、删除和账户状态只能通过数据权利与身份领域处理。

## 当前产品范围冲突

仓库中仍有国考、行测和申论专用代码，尤其是岗位数据、模考、救援训练和申论 rubric 发布。
当前目标产品转向事业编 A 类职测与综应后，必须先建立版本化 `exam_scope` 与内容发布契约；
禁止只改 UI 文案或静默回退国考内容后宣称支持事业编。

## 发布声明

本地测试通过不等于生产可用。部署前仍需验证迁移、备份与恢复、签名制品、provider 发布、
内容数据、数据权利、容量、告警和目标环境冒烟。历史 provider 与运维约束保存在
`docs/engineering/_archive/backend-readme-2026-08-12.md`，使用前必须与当前代码和运行手册
交叉核对。
