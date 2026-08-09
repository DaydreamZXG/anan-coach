# 国考申论 AI rubric 生成、编译与发布

这条生产链路为申论计划与评阅生成可追溯的评分规程。它只处理当前已发布的
国考题目版本和该题最新的完整材料包，自动发现尚无“当前、已发布、
精确绑定该材料版本” rubric 的题目，无需人工 manifest。

发布结果始终是 `system_compiled + unverified`。AI 无权生成 `official`、
无权标记 `verified`，也无权绕过确定性编译门禁。

## 固定两次模型调用

1. generator 读取题干和完整材料，返回严格 JSON Schema 约束的完整候选
   rubric。
2. 独立 critic/reviser 同时读取完整原文和 generator 候选，返回一份
   完整终稿及明确的 `approved`。它不返回补丁。

单个题目的业务语义固定为 generator 和 critic 各一次，不做隐式
repair 或降级。网络中断、429/5xx 及无法确认计费的响应保持为可重放
的 `running`；操作员重跑时使用同一 provider idempotency key 查证原调用，
不创建第三次业务调用。critic 拒绝、
结构化响应无效、完整输入超过已配置的 provider 容量，都会使整个题目
硬失败且不发布。输入绝不裁剪，也不用摘要代替原材料。

generator/critic 各自有稳定的 prompt version、schema version、模型名和
provider release。任意一项变更都会产生新的输入指纹和调用键，不会篡改旧审计。
恢复独立 critic 的链路使用 pipeline v3。generator prompt v12 的生产 dry-run
已证明双阶段输出和确定性编译可通过，但供应商没有兑现同键回放，因此正式发布
使用 prompt v13 的新键并在同一次运行内完成生成、复核、编译和发布；它不得复用
历史单阶段或 v12 dry-run 占用过的 generator 幂等键。供应商回放能力必须另行
整改，不能通过删除账本或放松输出指纹校验来掩盖。
发现器会继续接受当前官方 rubric；旧的 `system_compiled` rubric 如果没有
pipeline v3 的 generator/critic 双调用审计，则视为待升级并只追加发布 successor，
不修改或删除旧版本。

## provider 数据边界与安全门禁

rubric provider DTO 只包含产品内容：题目版本、题干、分值、完整材料段落及
内容指纹。不包含用户 ID、考生答案、学习记录、错因、时间戳或任何用户侧
数据。输出使用 `extra=forbid` 的严格结构化契约。

生产环境复用 grounded essay review 的外部 provider 门禁：

- HTTPS 且精确 hostname allowlist；
- 已确认的数据处理协议；
- 已验证的 `replayable_queryable` 同键回放能力；
- 显式的 idempotency-key 和 response-request-ID header；
- 独立版本化的 rubric provider release。

provider 的原始 request ID 不入库。只有其 SHA-256 可写入 rubric 的
`compilation_audit.pipelineAudit`；`model_call_ledger` 也不保存原始 ID 或内容。

## 确定性编译门禁

critic 只有在 `approved=true` 时才能进入编译器。编译器会独立检查：

- task version、material bundle version、prompt SHA-256 和 material SHA-256 精确绑定；
- input fingerprint 覆盖完整有序 segment 指纹和所有版本标识；
- 每个材料得分点的 bundle/material/segment、Unicode 半开字符偏移、逐字原文
  和原文 SHA-256；
- 引用必须能在数据库完整 segment 中逐字定位；
- criteria 总分、题目总分和材料型 points 分值完全守恒；
- criterion code、point ID、归一化 label 唯一，同一原文切片不得重复计分。

任一检查失败都不会写入 rubric。已存在当前、已发布且精确绑定
该材料版本的 official rubric 时拒绝生成 system rubric。历史迁移留下的
未绑定 rubric 作为不可变审计证据保留，但被隔离在新的材料版本谱系之外：
它不得被猜测绑定，也不得阻塞当前材料的自动编译。

## 幂等、成本与只追加发布

每个阶段都使用由完整输入、prompt/schema/provider/model 版本派生的稳定
provider idempotency key。每次调用都在 `ai_audit.model_call_ledger` 记录 token、
价格版本、首次调用的三项费率快照和人民币成本。跨部署重放也必须
使用账本中的原费率结算，不得被新环境变量改写。同键回放会向 provider
查证同一结果，但不重复计费；
provider 若对同一键返回不同输出指纹，链路立即硬失败。
已返回合法 usage 的 HTTP 响应即使严格结构校验不通过，也必须按真实 token
记录已计费的模型调用；该题仍然失败且不发布 rubric。

rubric 发布是只追加的。同一编译内容重放返回 `unchanged`；合法的新内容
追加 successor，不修改旧版本。

## 生产配置

除 grounded review 已有的 provider 安全变量外，必须显式配置：

```dotenv
AI_ESSAY_RUBRIC_GENERATOR_MODEL=provider-generator-model
AI_ESSAY_RUBRIC_CRITIC_MODEL=provider-critic-model
AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID=rubric-contract-2026-07-13
AI_ESSAY_RUBRIC_MAX_INPUT_BYTES=0
AI_ESSAY_RUBRIC_MAX_OUTPUT_BYTES=0
AI_PRICE_VERSION=provider-price-2026-07
AI_INPUT_CNY_PER_MILLION_TOKENS=0
AI_CACHED_INPUT_CNY_PER_MILLION_TOKENS=0
AI_OUTPUT_CNY_PER_MILLION_TOKENS=0
```

容量和价格示例中的 `0` 都是 fail-closed 占位符；生产启动前必须换成
已验证的 generator/critic 完整上下文字节容量和已审批的实际价格。
rubric 容量与 review 容量独立，不能互相借用。input/output 价格必须大于 0，
cached input 价格可为 0。

## 运行命令

先运行 dry-run。它会真实执行两次可审计模型调用和确定性编译，但不写入
rubric。模型调用成本仍会入账。

```bash
backend/.venv/bin/python scripts/auto_publish_guokao_essay_rubrics.py \
  --database-url "$DATABASE_URL" \
  --dry-run
```

检查 JSON 报告后发布全部待处理题目：

```bash
backend/.venv/bin/python scripts/auto_publish_guokao_essay_rubrics.py \
  --database-url "$DATABASE_URL"
```

可以用可重复的 `--task-version-id` 限定题目：

```bash
backend/.venv/bin/python scripts/auto_publish_guokao_essay_rubrics.py \
  --database-url "$DATABASE_URL" \
  --task-version-id 00000000-0000-0000-0000-000000000000 \
  --dry-run
```

命令标准输出始终是机器可读 JSON，包含 pipeline version、发现/成功/失败数、
每个题目的 generator/critic call ID、回放状态、发布动作和结构化失败原因。
任一题目失败时进程退出码为 `5`。正常无待处理题目时返回成功的空报告。

## 事故处置

- 不要直接修改失败 ledger 或旧 rubric。
- 网络、429/5xx 或 usage envelope 缺失等不确定结果保持 `running`；修正
  provider 可用性后使用原 release 和原调用键重放查证。
- 只有已证实的不可重试失败才记为 `failed`；修正合同、prompt、schema、
  model 或 provider release 后必须生成新的可审计调用键。
- 如果 provider 同键响应发生变化，停止批次并向 provider 核对幂等记录，不得
  人工跳过。
- 通过 `model_call_ledger.call_id` 和 rubric `compilation_audit.pipelineAudit` 交叉
  追溯；provider 事故排查时，将 provider 侧原始 request ID 在本地做
  SHA-256 后与审计指纹比对，不将原始 ID 写入本系统。

`scripts/publish_guokao_essay_rubrics.py` 保留给经审批的历史数据导入和事故处理；
常规生产链路使用上述自动命令，不依赖人工 rubric manifest。
