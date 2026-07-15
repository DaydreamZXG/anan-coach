# AI 上岸顾问改造小本本

日期：2026-07-09  
状态：执行中  
目标：把当前“官场生存刷题游戏 + AI 辅助训练”收敛为“AI 上岸决策系统 + AI 私教训练闭环”。  

## Goal

把项目的产品中枢从“好玩地刷题”改成“AI 帮用户提高上岸概率”：先给用户可解释的岗位决策，再围绕目标岗位、目标分差、能力画像、错因和模考结果生成训练计划。

## Architecture

保留现有资产层和执行链：

- `product.questions`、`product.knowledge_map_nodes`、`product.method_cards`
- `product.positions`、`product.position_entry_score_*`
- `learner.profiles`、`learner.practice_answers`、`learner.mock_exam_sessions`、`learner.essay_attempts`
- iOS 的 onboarding、Docket、ReviewCards、Workbench、Essay、MockExam、Companion

新增或强化四个产品中枢 owner：

1. `position_decision`：AI 选岗决策 owner，输出冲/稳/保岗位组合和风险解释。
2. `learning_profile` / 强化 `calibration`：个人能力画像 owner，沉淀能力、错因、分差。
3. `planner` / 强化 `taskstream`：计划生成 owner，按岗位目标和错因生成今日/本周计划。
4. `commerce`：订阅、权益、AI 配额 owner，后置但不能继续停留在 bootstrap 文案。

## Tech Stack

- Backend：FastAPI / SQLAlchemy / PostgreSQL / pytest
- iOS：SwiftUI / Swift Concurrency / StoreKit 2 后续接入
- AI：OpenAI-compatible Gemini reverse proxy，必须结构化 JSON 合同、审计、配额

## Baseline / Authority Refs

- 用户产品构想：`/Users/zxg/Desktop/纯AI驱动考公APP产品方案.docx`
- 当前交接：`docs/HANDOVER.md`
- 当前定位偏差与纠偏：`docs/aegis/plans/2026-07-07-learning-first-correction.md`
- 当前游戏化架构：`docs/aegis/specs/2026-07-07-bureaucratic-survival-architecture-plan.md`
- 方法卡和错因闭环规格：`docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md`
- 国考目标分与岗位分数现有实现：`backend/app/domains/positions/*`

## Compatibility Boundary

- 不删除真实业务数据。
- 不做生产数据库破坏性迁移。
- 现有 `/api/v1/positions/*`、`/api/v1/taskstream/*`、`/api/v1/dockets/open-court` 保持兼容。
- 新能力先走新增 endpoint 和新增 domain，再逐步让 iOS onboarding / 首页消费。
- 游戏化术语可保留，但不得成为学习目标的主 owner。

## Verification

每个切片至少满足：

- 后端契约测试：`backend/.venv/bin/python -m pytest backend/tests/<target>.py -q`
- 全量后端回归：`backend/.venv/bin/python -m pytest backend/tests -q`
- iOS 改动后 simulator build：XcodeBuildMCP `build_sim` with `CODE_SIGNING_ALLOWED=NO`
- 生产不部署，除非用户单独要求。

## Requirement Ready Check

- Requirement source refs：用户要求“做好计划，记在小本本上，然后开始按照小本本开始做”；产品方案 docx。
- Goals and scope refs：AI 选岗、AI 规划、AI 教学错因、智能刷题、申论、模考、数据壁垒、商业化。
- User / scenario refs：考公用户先选岗，再围绕分差训练，最终报名决策。
- Acceptance / verification criteria refs：新增功能必须有后端契约测试；不可伪装 AI；数据依据必须可解释。
- Decision：ready。

## Architecture Integrity Lens

- Invariant：真实学习效果优先，游戏化只做反馈层。
- Canonical owner：岗位决策归 `position_decision`，不塞进 `positions` 检索服务；训练计划归 planner/taskstream，不塞进 iOS。
- Responsibility overlap：当前 `positions` 只负责职位与分数数据，不能继续承担“决策建议”。
- Retirement / falsifier：若未来 iOS 仍只展示岗位列表而不展示岗位组合报告，本计划未达成。
- Verdict：先新增 owner，保持旧检索 API 不动。

## Plan Pressure Test

- Owner / contract / retirement：新增公共 API，需测试和 README 暴露。
- Architecture integrity：先 additive，后续再迁移 onboarding 消费。
- Verification scope：后端新接口 + 全量 pytest。
- Task executability：第一切片可在后端内完成。
- Pressure result：proceed。

## Complexity Budget

- Artifact class：public API + domain owner。
- Target files：`backend/app/domains/position_decision/*`、`backend/app/api/router.py`、`backend/tests/test_position_decision.py`、`backend/README.md`。
- Current pressure：`positions` 已有目标分与检索逻辑，适合作为依赖，不适合塞入更多决策逻辑。
- Projected post-change pressure：新增小 domain，低耦合。
- Budget result：within-budget。
- Planned governance：新增 owner 文件，旧 API 不删除。

## Roadmap

### Phase 1：AI 选岗决策 owner

目标：从“岗位列表”升级为“冲/稳/保岗位组合报告”。

任务：

1. 新增 `position_decision` domain。
2. 复用 `PositionService.search_positions` 和 `target_score_basis_for_position`。
3. 根据用户条件、当前分数、风险偏好生成推荐桶：
   - `safe`：当前分数接近或超过目标分、样本置信较高。
   - `balanced`：差距可训练弥补。
   - `stretch`：差距明显但可作为冲刺。
4. 输出推荐理由、风险标签、分差、数据依据、下一步动作。
5. 新增契约测试。

### Phase 2：错因与方法卡接主链

目标：做错题后直接回答“为什么错、怎么避免、练什么”。

任务：

1. `AnswerFeedback` 新增 `errorDiagnosis` 和可空 `methodCard`。
2. `learner.practice_answers` 写入 `error_code` / `method_card_id`。
3. 工作台按错因聚合复盘对象。
4. iOS `CoachFeedbackCard` 默认展示错因和同类识别，长解析折叠。

### Phase 3：Planner v2

目标：从 `rules_v1` 的轮换任务升级为岗位目标驱动计划。

任务：

1. 汇总目标岗位、目标分、当前估分、错因、模考、申论维度。
2. 输出今日任务、本周计划、明日预告、计划依据。
3. 每个任务必须有 `sourceGrounding` 和人能看懂的 reason。
4. Docket 训练入口消费 planner，而不是只取全局最高频知识点。

### Phase 4：申论和模考深度接入

目标：申论、模考不再是独立功能，而是计划生成输入。

任务：

1. 申论五维历史趋势进入能力画像。
2. 模考模块贡献、错题簇、趋势进入 planner。
3. 模考后自动生成下一周提分路径。

### Phase 5：商业化权益闭环

目标：让免费/会员/高级服务真实可售。

任务：

1. 新增 `commerce` domain。
2. iOS StoreKit 2 购买与恢复购买。
3. 服务端验证 App Store transaction。
4. AI 配额由 entitlement 决定，不再写死。
5. Paywall 只出现在高价值 AI 功能门口。

## Current Slice

Slice：Phase 1 / 后端 `position_decision`。

Files：

- Create `backend/app/domains/position_decision/__init__.py`
- Create `backend/app/domains/position_decision/schemas.py`
- Create `backend/app/domains/position_decision/service.py`
- Create `backend/app/domains/position_decision/router.py`
- Modify `backend/app/api/router.py`
- Modify `backend/README.md`
- Create `backend/tests/test_position_decision.py`

Boundary：

- 不改现有 positions API。
- 不改数据库 schema。
- 不接真实 AI provider；第一版是可解释 deterministic decision engine，后续可在同 owner 内增加 AI 叙事层。

Verification：

```bash
backend/.venv/bin/python -m pytest backend/tests/test_position_decision.py -q
backend/.venv/bin/python -m pytest backend/tests -q
```

## Execution Log

### 2026-07-09 / Slice 1：Backend position_decision

- 新增 `position_decision` domain 和 `GET /api/v1/position-decisions/recommendations`。
- 输出冲/稳/保岗位组合、分差、风险标签、推荐理由、下一步动作。
- 验证：`backend/.venv/bin/python -m pytest backend/tests -q` 通过。

### 2026-07-09 / Slice 2：iOS onboarding 接入

- `OnboardingStore` 接入 position decision API。
- 目标确认页展示 AI 上岸顾问报告，支持从推荐结果中选择目标岗位。
- 只在用户提供真实当前总分时请求报告；缺少行测/申论分时要求补充，不编造估分。
- 验证：后端全量 pytest 通过；XcodeBuildMCP simulator build 通过。

### 2026-07-09 / Slice 3：国考地区专业分数预测

Goal：当目标岗位缺少同岗历年进面分时，`position_decision` 用现有国考数据按“上一年 + 地区 + 专业 + 学历/政治面貌/基层年限 + 系统/城市”寻找真实相似样本，预测决策目标分。

Files：

- Modify `backend/app/domains/positions/repository.py`
- Modify `backend/app/domains/positions/service.py`
- Create `backend/app/domains/position_decision/score_prediction.py`
- Modify `backend/app/domains/position_decision/schemas.py`
- Modify `backend/app/domains/position_decision/service.py`
- Modify `backend/tests/test_position_decision.py`
- Modify iOS onboarding models/view only if new response fields should be visible.

Boundary：

- 不改生产数据库 schema。
- 不改 `/api/v1/positions/search` 的语义；`positions` 只提供真实样本查询。
- 不把预测当真实录取概率；只输出可解释目标分和样本依据。
- 样本不足时保持 unknown，不编造分数。

Verification：

```bash
backend/.venv/bin/python -m pytest backend/tests/test_position_decision.py -q
backend/.venv/bin/python -m pytest backend/tests -q
```

如果 iOS 解码或展示新增字段：

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result：

- `positions` 新增真实分数样本读取方法，供决策 owner 使用。
- `position_decision.score_prediction` 按上一年、地区、专业、城市/系统/学历/政治/基层年限匹配样本。
- `PositionDecisionItem` 新增 `decisionTargetScore`、`decisionScoreBasis`。
- 同岗只有 1 条分数样本时视为决策证据不足；若地区专业样本足够，则使用区域专业预测，并排除目标岗位自身样本。
- iOS onboarding 报告行展示决策目标分。
- 验证：目标测试、后端全量、iOS simulator build 均通过。

### 2026-07-09 / Slice 4：鹿鸣上岸方案 UI

Goal：把 onboarding 目标页从“岗位推荐列表”升级为“AI 上岸方案”，以“公考搭子 / 上岸搭子”为产品名，动物陪伴角色命名为“鹿鸣”。专业报告是主结构，鹿鸣负责一句话判断、情绪承接和节奏提醒。

Spec：

- `docs/aegis/specs/2026-07-09-ai-shangan-mascot-ui-spec.md`

Decision：

- 不复制“上岸猫”方向，不做猫系或泛宠物陪伴。
- 不把鹿鸣做成一级聊天 tab。
- 保留 `position_decision` 作为岗位决策 owner。
- 旧“官场/幕僚/县令/惩罚”叙事从新主路径降级，后续逐步清理。

Planned first implementation：

- 新增可维护的 `LumingMascotView`。
- 重构 `PositionDecisionReport` 为 `AI 上岸方案`：鹿鸣主判断、当前分、决策目标分、分差、数据依据、推荐岗位组合、缺失条件、生成今日计划 CTA。
- 在 iOS 公开展示 `decisionScoreBasis`，让用户知道目标分来自同岗历史分还是地区专业预测。
- 不改后端岗位决策契约；若今日计划需要新输入，再补 planner/taskstream 契约。

Verification：

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

若修改 planner/taskstream：

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result：

- 新增 `LumingShanganPlanView.swift`，集中承载 `LumingMascotView`、AI 上岸方案头部、分数指标、分数依据、岗位组合、缺失条件和下一步动作。
- `OnboardingTargetStep` 首屏改为 `AI 上岸方案`，目标分确认下沉为“生成首日计划前确认”。
- 岗位推荐行展示 `decisionScoreBasis.evidenceSummary`、样本数、置信度、分差、目标分和推荐理由。
- 从鹿鸣方案选择岗位时，`OnboardingStore.selectedDecisionItem` 会保留对应 `decisionTargetScore`；保存 profile 时优先写入该决策目标分。
- 手动选择岗位会清空旧 decision item，避免把旧预测分带到新岗位。
- 顺手修复 `MockExamView` 的 Timer 主线程状态更新 warning。
- 验证：后端全量 pytest 通过；XcodeBuildMCP simulator build 通过且 0 warning / 0 error。

### 2026-07-09 / Slice 5：鹿鸣今日上岸路线首页

Goal：把鹿鸣上岸方案从 onboarding 结果页延伸到首页，形成“问诊 -> 上岸方案 -> 今日路线 -> 开始训练”的完整主路径。

Files：

- Create `apps/ios/Gongkao/Gongkao/LumingTodayRouteView.swift`
- Modify `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppModels.swift`
- Modify `apps/ios/Gongkao/Gongkao/StudyDeskView.swift`
- Modify `apps/ios/Gongkao/Gongkao/CoachComponents.swift`
- Modify `apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj`

Boundary：

- 不新增 planner/taskstream 后端契约。
- 不伪造首页分差或任务；首页只消费 `GET /api/v1/taskstream/today` 的真实 `target`、`trackMix`、`tasks`、`rationale`。
- 不删除旧 Docket domain；只是让 iPhone 今日主入口回到 AI 上岸路线。

Result：

- 新增 `LumingTodayRouteView`，展示鹿鸣今日判断、目标岗位/考试、分差、今日任务数、总分钟、下一项任务、任务理由、来源依据和任务路径。
- `TodayView` loaded 状态先展示鹿鸣今日上岸路线；feed 卡片降为“补充提醒”，且不重复展示 goal/coach/task 主卡。
- iPhone `AppSection.today` 从旧 `DocketView` 切到 `TodayView`。
- iPad `StudyDeskView` 中栏改用同一套 `LumingTodayRouteView`，避免 iPhone/iPad 主路径分裂。
- 顶部 tab、iPad 侧栏、通知文案、工作台标题、共享任务路径组件中的主路径措辞改为“今日路线 / 工作台 / 我的 / 鹿鸣判断”。
- 验证：后端全量 pytest 通过；XcodeBuildMCP simulator build 通过且 0 warning / 0 error。

### 2026-07-09 / Slice 6：第一波商业化闭环

Goal：让 App 具备第一波商业化能力：服务端拥有商品、交易、权益、额度 owner；iOS Profile 提供真实 StoreKit 购买/恢复入口；AI 高价值功能由服务端权益门槛控制。

Files：

- Create `backend/app/domains/commerce/*`
- Modify `backend/app/api/router.py`
- Modify `backend/app/api/routes.py`
- Modify `backend/app/core/config.py`
- Modify `backend/app/domains/essay/*`
- Modify `backend/app/domains/companion/*`
- Modify `backend/tests/sql/minimal_schema.sql`
- Modify `scripts/server_setup_learning_ai_tables.sh`
- Create `backend/tests/test_commerce.py`
- Modify `backend/tests/test_ai_audit_repository.py`
- Modify `backend/tests/test_companion_external.py`
- Modify `backend/tests/test_essay_routes.py`
- Create `apps/ios/Gongkao/Gongkao/CommerceModels.swift`
- Create `apps/ios/Gongkao/Gongkao/CommerceStore.swift`
- Create `apps/ios/Gongkao/Gongkao/CommerceViews.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppModels.swift`
- Modify `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
- Modify `apps/ios/Gongkao/Gongkao/CompanionAskView.swift`
- Modify `apps/ios/Gongkao/Gongkao/CaptureQuestionView.swift`
- Modify `apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj`
- Create `docs/product/iap-products.md`
- Modify `docs/ops/server-inventory.md`

Boundary：

- 不接受本地会员开关。
- 不接受未校验 App Store 签名交易的 Pro 授权。
- iOS 不决定产品 ID、价格带、权益或额度。
- `commerce.entitlement_usage_daily` 是权益额度 owner；`ai_audit` 只保留 AI 交互日志和旧的底层审计能力。
- `APP_STORE_ROOT_CERTIFICATE_PATHS` 未配置时，交易接口必须拒绝授权，不能假成功。

Result：

- 新增 `commerce` domain：商品目录、当前权益、App Store 交易上报、Pro 授权、权益审计和 feature quota reservation/refund。
- 服务端商品 ID 固定为 `gongkao_ai_pro_monthly`、`gongkao_ai_pro_yearly`，额度合同记录在 `docs/product/iap-products.md`。
- App Store signed transaction 校验覆盖 JWS header/cert chain、Apple root anchor、bundleId、productId、transactionId、originalTransactionId、过期和撤销状态。
- 申论 AI 批改、鹿鸣 AI 问答、外部错题解析改为从 `commerce` 获取 free/pro 限额；模型失败时退还权益用量。
- 用户当天用完免费额度后升级 Pro，下一次 reservation 使用新 Pro 限额，不被旧 free `limit_count` 卡住。
- iOS `CommerceStore` 使用 StoreKit 2 加载商品、购买、恢复购买，并把 `VerificationResult<Transaction>.jwsRepresentation` 上报服务端。
- Profile 静态 AI Pro 文案升级为真实 paywall：展示当前权益、今日剩余额度、StoreKit 实际价格、购买/恢复入口和服务端校验结果。
- iOS 高价值入口的 quota 错误文案改为可理解的 Pro 权益提示。
- 商业化审计后补强：购买时使用 `Product.PurchaseOption.appAccountToken(userId)`，服务端要求 signed transaction 中的 `appAccountToken` 等于当前登录用户，防止交易跨账号复用。
- 商业化审计后补强：`Transaction.updates` 会在有登录态时自动提交服务端，避免 pending/延迟交易完成后权益不刷新。
- 商业化审计后补强：新增 App Store Server Notifications V2 入口，验证 `signedPayload`、记录 `notificationUUID`，并按嵌套交易续期/撤销权益。
- 商业化审计后补强：App Store Server Notifications 重放处理改为先登记 `notificationUUID`，重复通知不再验证或应用嵌套交易。
- 外部验收补强：新增 `docs/ops/iap-sandbox-verification.md`，覆盖 App Store Connect 商品、证书配置、购买、恢复、续期通知、重复通知、退款撤销和证据归档。
- 生产部署补强：`scripts/server_setup_learning_ai_tables.sh` 现在补齐 commerce tier/billing/status check constraints，并增加交易、通知、审计查询索引；`backend/tests/test_server_setup_schema_contract.py` 固定生产脚本契约。
- 额度周期补强：feature usage reservation 和 entitlement display 现在按 `FEATURE_QUOTAS.period` 取周期桶，月额度不会按日刷新，日额度仍按日刷新。
- 权益重算补强：`commerce.app_store_transactions` 作为 App Store 交易事实表，`commerce.user_entitlements` 改为从 active/non-revoked transactions reconcile 出来的快照，避免旧月订阅退款误伤当前年订阅。
- `originalTransactionId` 语义补强：撤销匹配不再把同一 `originalTransactionId` 下所有 active transaction 一刀切失效，而是限定到同一 transaction 或同一 original/product/expires 服务期。
- 验证：commerce 目标测试 `12 passed, 1 warning`；commerce + server setup 目标测试 `14 passed, 1 warning`；后端全量回归 `91 passed, 1 warning`；iOS simulator build 通过且 0 warning / 0 error；商业化静态审计未发现旧硬编码额度、假成功或本地会员入口。

Audit conclusion：

- 这次审计发现并修复了 4 个第一波商业化不能放过的问题：StoreKit 交易未绑定后端用户、pending/延迟交易完成后未主动上报服务端、App Store Server Notifications V2 没有对外入口、重复通知重放会进入嵌套交易处理。
- 当前代码层已经具备第一波收费闭环的骨架：服务端商品/权益/额度 owner、iOS StoreKit 购买恢复、App Store signed transaction 校验、用户绑定、通知续期/撤销处理、从交易事实重算的权益快照、AI 高价值功能额度门控、按周期扣减的额度系统、生产建表脚本约束和审计索引。
- 尚不能宣称生产收费闭环完全完成：必须按 `docs/ops/iap-sandbox-verification.md` 完成 App Store Connect 商品配置、服务器 `APP_STORE_ROOT_CERTIFICATE_PATHS`、通知 URL 配置，并通过真实 sandbox 购买/恢复/退款/过期/重放流程。
