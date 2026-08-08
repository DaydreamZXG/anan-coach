# 系统掌握地图(学习版)

> 用途:让产品负责人自己搞懂「每个功能怎么转的、改哪能生效、最容易踩什么坑」,不用通读 493 个后端文件 + 265 个前端文件。
> 生成:2026-08-04,由 10 个并行深读基于真实代码 trace 而成,每条功能点带 file:line 引用。
> 配套:[结构健康度评估报告 2026-08-04](2026-08-04-structure-health-assessment.md)

## 全景图:10 个产品簇

| 产品标签 | 主要域 | 一句话 | 健康度 |
|---|---|---|---|
| 启动 / 登录 / 建档 | auth、onboarding | 用户怎么进来、档案怎么建 | ⚠️ 认证层三套并存 |
| 今日 / 计划 / 调休 | planning、task_runtime | 每天排什么、怎么走完、怎么回写 | ⚠️ 完成状态机是 bug 重灾区 |
| 行测 | xingce_learning、mastery、retake、cause_agent | 刷题、交卷、复盘、掌握、复测 | ⚠️ 与 planning 边界纠缠 |
| 申论 | essay_intelligence、essay | 写作、批改、改写,全链路最重 AI 域 | ✅ 结构最健康 |
| 岸岸 AI | companion、ai/* | AI 督学对话,一入口三重防线 | ⚠️ 解耦到一半(死链路) |
| 岗位 / 选岗 | positions、qualification、position_decision | 岗位浏览、资格核验、目标分 | ✅ 边界清晰,decision 域边缘化 |
| 拍照搜题 / 导入 / 私人知识库 | external_ingestion、ilink_channel、private_knowledge | 外部材料进系统三条独立链路 | ✅ 三条链路独立 |
| 商业 / 会员 / 社区 | commerce、community_* | 支付、权益、方法卡、奖励 | ⚠️ 业务规则在 SQL 里 + 一张双写表 |
| 我的 / 账户 / 合规 | data_rights、learning_brain、memory、coach_notifications | 聚合、导出、删除、记忆、通知 | ⚠️ erase/export 是手工清单 |
| 运维 / 基础设施 | ops、scripts、.product-governance | 部署、门禁、治理、数据资产 | ✅ 门禁是安全网,但备份未启用 |

> 阅读提示:每簇 5-6 条功能点,每条 = 产品意图 + 调用链 + 关键文件(file:line) + 核心表 + 易错点。**真正要常驻脑内的文件不超过 20 个,其余可查。**

## 1. 启动 / 登录 / 建档

**本簇概览**:这是产品的"冷启动主干"：微信一键登录建立会话 -> 服务端按幂等命令推进七步建档 -> 建档完成后今日页才解锁。整体呈"后端单账号聚合(learner.users + learner.profiles 每登录即确保行存在) + 前端每页自建 ApiClient 裸拼 token"的形态，认证层未收敛成单一拦截器。跨功能警告：(1) 隐私同意表 privacy.consent_events 已建表但全仓无任何业务写入，登录页"我已阅读并同意"只是本地 checkbox，服务端没有同意记录；(2) 401 处理三套并存——统一 onUnauthorized 只清本地会话不跳转，真正跳登录分散在页面控制器/守卫里；(3) 建档 completed 之后多数步骤被服务端锁定(onboarding_already_completed)，只有学习时间偏好可事后回改；(4) token 为 30 天 opaque token(gkd_ 前缀)，库中只存 SHA256，前端不主动判过期，全靠 401 被动触发。

#### 1. 应用启动与路由装配

- **产品意图**:后端是一个 FastAPI 单体应用，create_app 工厂在模块加载时即执行(app = create_app())，一个 HTTP 中间件统一注入 request_id 并给 /health、/api/*、/internal-runtime/* 打 no-store 头。全部业务路由经 api/router.py 聚合约 40 个领域 router 再挂到 app(另单独挂 ilink_channel)，异常处理按类型统一映射到人话错误契约。前端入口是 pages/login/index(第一个页面即登录页)，app.json 声明 4 个自定义 Tab(今日/能力/岸岸/我的)、38 个主包页面、13 个分包(其中 6 个是纯资产的 anan 分包只有 entry 没有页面，7 个功能分包含 onboarding/positions 等)，共 60 个页面路由。app.ts onLaunch 做 401 复位、按微信 envVersion 切换环境(生产 https://kg.xingguoxian.cn)、字体加载与网络监听。
- **调用链**:backend/app/main.py:101 app=create_app() -> main.py:92 include_router(api/router.py) -> api/router.py:67-117 聚合 auth/onboarding 等 40+ 域 router -> /health (api/routes.py:30-38); 前端 app.json pages[0]=pages/login/index -> app.ts:50 onLaunch -> installUnauthorizedReset + configureForWechatEnvVersion(config/environment.ts:31) -> 各页按 routes.ts 注册路由跳转
- **关键文件**:
  - `backend/app/main.py:48-101`
  - `backend/app/api/router.py:67-117`
  - `backend/app/api/routes.py:30-38`
  - `backend/app/internal_services/router.py:13`
  - `apps/wechat/miniprogram/app.json`
  - `apps/wechat/miniprogram/app.ts:42-96`
  - `apps/wechat/miniprogram/config/environment.ts:17-48`
  - `apps/wechat/miniprogram/app/navigation/routes.ts:15-76`
- **易错点**:
  - 任务里说的"64 个路由"实际是 60 个页面路由(38 主包 + 22 分包页面)；6 个 anan 分包(packages/anan-*)是纯资产包，没有页面只有 package-entry.js
  - /docs 在生产环境被关闭(main.py:53)，排查接口要靠读源码而不是 Swagger
  - 分包页面(如 onboarding/positions)与主包页面混用 routes.ts 同一套注册表，新增页面必须同步 app.json + routes.ts 两处，漏一处跳转就失败
- **关联**: 路由注册与导航守卫、微信登录与会话

#### 2. 微信登录与会话

- **产品意图**:登录页用微信一键登录：先让用户勾选"已阅读并同意用户协议与隐私指引"(否则按钮禁用)，再 wx.login 拿临时 code，连同设备 ID 调 POST /api/v2/auth/wechat。后端拿 code 调微信 jscode2session 换 openid(有 unionid 会做账号归并)，首次登录建 learner.users + learner.wechat_identities + learner.profiles + learner.onboarding_progress 行，然后签发一个 30 天有效的 opaque token(gkd_ 前缀，库里只存 SHA256)写进 learner.app_sessions。响应里带 onboarding_progress，前端按 nextStep 决定跳建档还是今日。token 存在 wx storage(auth/session.ts，key=gongkao.auth.session.v1，带 generation 版本防并发覆盖)，冷启动时登录页 resume 会用旧 token 拉一次建档进度做"恢复登录"，401 或代码 authentication_required 才丢弃会话。登录页还支持 returnTo 回跳(auth-return.ts，存 7 天)让用户从任意受保护页面被踢回登录后能回到原处。
- **调用链**:pages/login/index.ts:60 login() 需 legalAccepted -> wx.login 拿 code (index.ts:27-39) -> WechatLoginController.run (features/identity/wechat-login-controller.ts:70) -> POST /api/v2/auth/wechat (index.ts:93-94) -> auth/router.py:107 login_with_wechat -> AuthService.login_with_wechat (auth/service.py:38) -> WechatIdentityExchange.exchange jscode2session (auth/wechat.py:62-126) -> AuthRepository.upsert_wechat_user (auth/repository.py:212) -> create_session (repository.py:362, learner.app_sessions) -> 响应含 onboarding_progress -> commitSession storeWechatSession (api/auth.ts:5) -> storeAuthSession (auth/session.ts:83) 写 storage -> navigate(progress.nextStep)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/login/index.ts:27-39,60-132`
  - `apps/wechat/miniprogram/features/identity/wechat-login-controller.ts:42-141`
  - `apps/wechat/miniprogram/auth/session.ts:35-141`
  - `apps/wechat/miniprogram/api/client.ts:100-153`
  - `apps/wechat/miniprogram/api/auth.ts:5-17`
  - `apps/wechat/miniprogram/app/navigation/auth-return.ts:32-183`
  - `backend/app/domains/auth/router.py:107-180`
  - `backend/app/domains/auth/service.py:38-44`
  - `backend/app/domains/auth/wechat.py:62-126`
  - `backend/app/domains/auth/repository.py:212-441`
- **核心表**: `learner.users`, `learner.wechat_identities`, `learner.apple_identities`, `learner.app_sessions`, `learner.profiles`, `learner.onboarding_progress`, `learner.account_events`
- **易错点**:
  - token 不落地只存 SHA256，出问题无法从库反查明文；前端也不判本地过期，全靠请求 401 被动发现
  - unionid 归并在 upsert_wechat_user 里做(按 last_login_at 排序取 owner)，改绑定/换号逻辑要非常小心不要归并错账户
  - session.ts 用 generation 字段区分新旧会话，clearAuthSessionIfCurrent 只清"发起失败请求的那个会话"；多设备同账号同时登录时不要随意全局清会话
- **关联**: 认证与 401 处理、新手建档 onboarding

#### 3. 新手建档 onboarding

- **产品意图**:新用户首次登录后按服务端 nextStep 串起七步建档：入口页(entry)选"我是新用户开始建档"或"我是老用户修改档案"；依次是备考经历(preparation-experience，同时可改昵称)、报考资格(qualification，即 profile 步，写学历/专业/应届等事实)、岗位偏好(position-preference，选地区/可调剂/层级)、学习时间偏好(study-preference，周内/周末分钟数)、能力起点(ability-baseline，可选自报行测申论分或"跳过，之后安排摸底")、结果页(result，展示岗位目标与分数差距，确认后完成)。每一步都是带 Idempotency-Key 的幂等命令(command_id=user_id:idempotency_key)，服务端用 expected*Version 乐观锁防并发冲突，命令回放写 learner.onboarding_commands、进度快照写 learner.onboarding_progress_events，并触发 outbox 事件。进度状态机在 learner.onboarding_progress：experience->qualification->study_preference->result->completed；岗位偏好与能力起点这两个步不推进 progress 版本。完成后今日页解锁；之后可通过档案页(profile/archive)回改各模块，但服务端锁定多数步骤——只有学习时间偏好真正允许建档完成后修改。
- **调用链**:pages/onboarding/entry/index.ts startProfile -> onboardingPreparationExperience -> put /api/v2/onboarding/preparation-experience (onboarding/router.py:212, 写 learner.preparation_experience_versions) -> pages/onboarding/index.ts 报考资格 submit -> PUT /api/v2/qualification/profile (QualificationApi) 写 learner.qualification_facts + qualification_profile_changes -> POST /api/v2/onboarding/qualification/complete (router.py:238) -> 岗位偏好 PUT /api/v2/onboarding/position-preference (router.py:289, learner.position_preference_versions + 更新 profiles.target_region) -> 学习时间 PUT /api/v2/onboarding/study-preference (router.py:416, learning.weekly_study_preference_versions + profiles.daily_minutes) -> 能力起点 PUT /api/v2/onboarding/ability-baseline (router.py:344, learner.ability_baseline_declaration_versions + 自报分写 learning.score_evidence) -> 结果页 GET /api/v2/onboarding/result + POST /api/v2/onboarding/complete (router.py:442 acknowledge -> advance_progress completed=true + profiles.onboarding_complete=true)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/onboarding/entry/index.ts`
  - `apps/wechat/miniprogram/pages/onboarding/preparation-experience/index.ts:192-226`
  - `apps/wechat/miniprogram/pages/onboarding/index.ts:293-481`
  - `apps/wechat/miniprogram/pages/onboarding/position-preference/index.ts:143-187`
  - `apps/wechat/miniprogram/pages/onboarding/study-preference/index.ts`
  - `apps/wechat/miniprogram/pages/onboarding/ability-baseline/index.ts:94-131`
  - `apps/wechat/miniprogram/pages/onboarding/result/index.ts`
  - `backend/app/domains/onboarding/router.py:169-465`
  - `backend/app/domains/onboarding/progress_service.py:54-882`
  - `backend/app/domains/onboarding/progress_repository.py:533-700`
  - `backend/app/domains/onboarding/baseline_service.py:58-170`
  - `backend/app/domains/onboarding/baseline_repository.py:81-155`
  - `apps/wechat/miniprogram/app/navigation/routes.ts:127-138`
- **核心表**: `learner.onboarding_progress`, `learner.preparation_experience_versions`, `learner.qualification_facts`, `learner.qualification_profile_changes`, `learner.position_preference_versions`, `learning.weekly_study_preference_versions`, `learner.ability_baseline_declaration_versions`, `learning.score_evidence`, `learning.ability_estimate_versions`, `learner.onboarding_commands`, `learner.onboarding_progress_events`, `learner.profiles`, `platform.outbox_events`
- **易错点**:
  - 服务端 nextStep 语义和页面不完全对应：qualification 完成后的 nextStep='study_preference' 实际先落到岗位偏好页，position-preference 不推进度、study-preference 才推；改流程时别按直觉改 nextStep
  - 建档 completed 后 preparation_experience / ability_baseline 都抛 onboarding_already_completed 拒绝修改(progress_service.py:109-113、baseline_service.py:105-109)，只有 study-preference 因是独立聚合可改(progress_service.py:654-657)，"七步都能回跳改"是错觉
  - 能力起点 reported_score 模式会触发 AbilityEstimateService.record_reported_score 写 learning.score_evidence + ability_estimate_versions(会请求岗位发布上下文，发布缺失时整个提交被拒 evidence_rejected)，不是简单存两个分数
  - 每步都是乐观锁 + 幂等命令，前端每个 controller 都配 pending-store 做断网恢复；新增一步要同时配 Idempotency-Key 长度校验(router.py:66-69 要求 8-160 字符)
- **关联**: 微信登录与会话、认证与 401 处理、岗位选择与资格域

#### 4. 认证与 401 处理

- **产品意图**:后端统一从 Authorization: Bearer 头取 token：require_bearer_token 解析头，get_current_user 再用 token 在 learner.app_sessions 里查(SHA256 比对、校验未撤销未过期且账号 active)，找不到就抛 401。绝大多数 v2 接口都用 Depends(get_current_user)。前端 401 处理三套并存，这是目前最散的地方：(1) api/client.ts 的全局 onUnauthorized(所有走 ApiClient 的请求 401 都会触发)，onLaunch 时用 installUnauthorizedReset 注册，但它只调 clearAuthSessionIfCurrent 清本地会话、不做跳转；(2) requireAuthenticatedClient(features/learning-brain/presentation.ts)无 token 时返回 null 并 reLaunch 登录，学习大脑相关页面用；(3) 大量页面自己裸拼 token 构造 ApiClient，靠页面守卫/控制器里 reLaunch 登录，如 today 页的 TodayController 检测 401/authentication_required 后 clearSession + reLaunch(loginUrlForReturn)，me/onboarding 各页在 mountController 时判 currentAuthSession 为空就 reLaunch。跳登录大多带 returnTo 以便登录后回到原页面。
- **调用链**:后端: request -> Depends(get_current_user) (auth/deps.py:75-85) -> require_bearer_token (deps.py:53-72) -> AuthRepository.get_user_by_access_token (auth/repository.py:403-441) 校验 app_sessions; 前端: api/client.ts:134 401 -> onUnauthorized(originSession, requestId) -> api/auth.ts:13 installUnauthorizedReset -> auth/session.ts:115 clearAuthSessionIfCurrent 仅清同属主会话; 跳转分散在: features/planning/today-controller.ts:320 routeToRealAuthentication(clearSession + reLaunch loginUrlForReturn) / features/learning-brain/presentation.ts:9 requireAuthenticatedClient(null + reLaunch) / pages/me/index.ts:157 守卫 reLaunch
- **关键文件**:
  - `backend/app/domains/auth/deps.py:53-85`
  - `apps/wechat/miniprogram/api/client.ts:36-40,134-136`
  - `apps/wechat/miniprogram/api/auth.ts:13-17`
  - `apps/wechat/miniprogram/auth/session.ts:101-133`
  - `apps/wechat/miniprogram/features/learning-brain/presentation.ts:9-13`
  - `apps/wechat/miniprogram/features/planning/today-controller.ts:230-345,365-370`
  - `apps/wechat/miniprogram/pages/me/index.ts:157`
  - `apps/wechat/miniprogram/app/navigation/auth-return.ts:32-49`
- **核心表**: `learner.app_sessions`, `learner.users`
- **易错点**:
  - 全局 onUnauthorized 只清本地会话不跳转，真正跳登录全靠各页面各自 reLaunch——新增页面如果漏了守卫，401 后会出现"请求失败但还停在原页"的中间态
  - 三套模式并存(requireAuthenticatedClient / 裸拼 token+页面守卫 / 全局 onUnauthorized)，新页面接入前先确认走哪套，尽量统一到 requireAuthenticatedClient + returnTo 链路
  - today 页对 401 之外还识别 payload.code==='authentication_required'；清理会话用 clearAuthSessionIfCurrent 保护"用户已重新登录"的场景，别改成无条件 clearAuthSession
- **关联**: 微信登录与会话、新手建档 onboarding

#### 5. 隐私与条款

- **产品意图**:用户协议(terms)是纯静态页(Page({}) + 写死的 wxml)，隐私页(privacy)则是登录后的"数据与隐私中心"：展示数据用途、可导出个人数据(createExport/exportStatus/exportPayload 生成文件并可转发)、申请删除账户(需输入"删除我的岸岸账户"确认语，成功后清会话跳回登录)。登录页的"我已阅读并同意"是本地 checkbox 门禁，勾选才可点登录；点"用户协议"跳 terms，点"隐私保护指引"调微信原生 wx.openPrivacyContract。后端 privacy schema 定义了 policy_versions(种子版本 personal-data-control@v1)、consent_events(用户同意/撤回流水)、data_export_requests、data_export_payloads、account_deletion_requests、deletion_events 等表；但全仓业务代码没有一行写 consent_events——登录页的服务端同意记录目前并不存在，表是空挂的。删除/导出走 data_rights 域(/api/v2/data-rights)，均有幂等键与 pending-store 断点恢复。
- **调用链**:pages/login/index.ts:60 login() 需 legalAccepted=true -> openTerms (index.ts:72) navigateTo pages/terms 或 openPrivacy (index.ts:77) wx.openPrivacyContract -> 登录后 me 页 routes.privacy -> pages/privacy/index.ts -> DataRightsApi overview/export/deletion -> data_rights 域落库 privacy.data_export_requests / privacy.account_deletion_requests / privacy.deletion_events; consent_events 表存在但无写入方
- **关键文件**:
  - `apps/wechat/miniprogram/pages/login/index.ts:60-82`
  - `apps/wechat/miniprogram/pages/terms/index.ts`
  - `apps/wechat/miniprogram/pages/privacy/index.ts:69-341`
  - `backend/app/domains/data_rights/repository.py:184,287-333,639`
  - `backend/migrations/versions/20260717_35_commerce_privacy_operations.py:630-672`
- **核心表**: `privacy.policy_versions`, `privacy.consent_events`, `privacy.data_export_requests`, `privacy.data_export_payloads`, `privacy.account_deletion_requests`, `privacy.deletion_events`
- **易错点**:
  - privacy.consent_events 表已建(含 accepted/withdrawn 动作约束)但业务代码零写入，登录页同意不落服务端；要合规留痕得新写写入方
  - privacy 页同时承担数据导出 + 账户删除，两者都是异步任务(queued/running/completed)，依赖 pending-store 断点续跑，误以为同步完成就会踩坑
  - 账户删除成功后清会话跳 /pages/login/index?reason=account_deletion_pending，后端对 deletion_pending 状态拒登(返回 409)，这条链路别和普通登出混
- **关联**: 微信登录与会话、数据权利与合规域

## 2. 今日 / 计划 / 调休

**本簇概览**:本簇是"每日计划"闭环:容量(今天能学多久)→候选与打分(今天学什么)→运行时执行(行测/申论/模考三类任务各自启动)→完成与证据结算(把真实学习写回,并让下一版计划基于它重排)→周视图/历史/日结算(把整个过程回看给用户),学习偏好作为版本化基线贯穿其中。所有写路径(确认容量、记录执行、完成任务)末尾都会调用 get_or_create_today 触发一次完整重排,因此"读今天"这个 GET 其实是有副作用的写操作,会在输入指纹变化时落新的 daily_plan_versions——这是整簇最需要记住的前提。另一条贯穿性警告:task_execution_versions 有四个独立写入方且完成命令只允许"权威所有者"写入,行测/申论/模考各自拥有完成写入器,通用 record_task_execution 路径拒绝 status=complete,任何新任务类型加入都必须自证 evidence 归属,否则契约校验会抛 409。

#### 1. 容量设置与调休(day-adjustment + today-controller + 容量 command)

- **产品意图**:用户告诉系统今天有多少可用时间、精力、压力和时间把握,系统据此重排今天的任务。今天页和调休页都能发起同一份容量命令,前后端都有幂等键与乐观并发保护:前端用 basedOnCapacityVersionId 检查容量没被别人改过,后端用 command_id + input_fingerprint 做重放识别。确认后写一条 user_override 的 daily_capacity_versions,并立即在同一事务里重排今天。2026-08 新加的行测当日聚焦(xingceOnly + preferredXingceModule)允许用户只学某一模块而不改写周偏好。
- **调用链**:day-adjustment/index.ts:487 confirmAdjustment -> OperationAttemptStore.begin(:525) 生成幂等键 -> PlanningApi.setTodayCapacity(:724) -> PUT /api/v2/plans/today/capacity -> router.py:147 -> planning/service.py:111 set_today_capacity(重放检查 :130,容量乐观校验 :170) -> repository.insert_user_capacity(:450) 落 daily_capacity_versions -> service.get_or_create_today(:210) 同事务重排 -> 返回新 dashboard。预览路径:day-adjustment:431 generatePreview -> POST /today/capacity/preview -> service.preview_today_capacity(:218) 用 begin_nested 事务试跑后回滚
- **关键文件**:
  - `apps/wechat/miniprogram/pages/day-adjustment/index.ts:487`
  - `apps/wechat/miniprogram/pages/day-adjustment/index.ts:431`
  - `apps/wechat/miniprogram/pages/day-adjustment/index.ts:155`
  - `apps/wechat/miniprogram/features/planning/today-controller.ts:90`
  - `apps/wechat/miniprogram/pages/today/capacity.ts:69`
  - `backend/app/domains/planning/service.py:111`
  - `backend/app/domains/planning/repository.py:450`
  - `backend/migrations/versions/20260804_95_daily_focus_override.py:21`
- **核心表**: `learning.daily_capacity_versions`
- **易错点**:
  - 今天页 today-controller 与调休页各自实现了一套独立的幂等机制(前者内存内 capacityAttempt,后者 OperationAttemptStore 持久化 7 天重放),改一处必须同步另一处;今天页只在 today 页用,day-adjustment 重写了约 300 行幂等逻辑。
  - set_today_capacity 前置要求当前容量已存在(必须先 getToday 生成 weekly_preference 兜底容量),且 based_on_capacity_version_id 必须等于当前头,否则 409「请重新载入」。
  - xingceOnly/preferred_xingce_module 是 2026-08-04 新迁移的字段,生产库未部署时该命令会插入失败;且 xingce_only 会清空当天的到期复盘/验证候选(candidates.py:183)。
- **关联**: 今日计划生成、学习偏好

#### 2. 今日计划生成(get_or_create_today 主链路)

- **产品意图**:每天的核心引擎:读取学习偏好、当日容量、岗位目标分差、行测与申论能力证据,生成候选任务并按五维加权打分挑选,落成一份不可变的日计划版本。行测候选按最弱模块、到期复盘、到期验证生成;申论候选按内容发布门禁推进闭环(作答/评阅/重写/迁移);模考日额外插入周度标准卷。打分权重目标0.30/能力0.30/验证0.25/时间0.15/负荷0.15,加上轨道平衡加分。所有输入版本号被算成 input_fingerprint,输入不变就复用旧计划,任何输入变化就产生新版本并记录 replan 原因。
- **调用链**:GET /api/v2/plans/today -> router.py:61 -> planning/service.py:488 get_or_create_today -> repository: current_or_create_preference(:151) + current_or_create_capacity(:276) + goal_and_ability(:483) + objective_snapshot(:639) + essay_snapshot(:878) + active_strategy_decisions(:582) -> candidates.build_dual_track_candidates(:548) -> engine.select_daily_tasks(:616) -> repository.insert_plan(:816) + insert_plan_task(:851) 落 daily_plan_versions/daily_plan_tasks -> 返回 TodayDashboard。若 fingerprint 命中已有 current_plan_for_fingerprint(:759) 直接返回缓存版
- **关键文件**:
  - `backend/app/domains/planning/service.py:488`
  - `backend/app/domains/planning/candidates.py:170`
  - `backend/app/domains/planning/candidates.py:244`
  - `backend/app/domains/planning/engine.py:88`
  - `backend/app/domains/planning/engine.py:200`
  - `backend/app/domains/planning/repository.py:639`
  - `backend/app/domains/planning/repository.py:878`
- **核心表**: `learning.daily_plan_versions`, `learning.daily_plan_tasks`
- **易错点**:
  - getToday 是『读』但有写副作用:每次输入指纹变化都会插入新 daily_plan_versions(version_no+1),13 个页面 new PlanningApi 各自拉 getToday,等于在并发下反复重排;指纹相同才会复用。
  - work_item_key 由 local_date+candidate_key+track+stage+kind+title+startUrl+evidenceRefs+completion 指纹化(:943),任务换标题/换证据链就会变 key,导致已执行任务与旧 key 失联。
  - 计划有效性 valid_until 是次日 0 点(上海时区),跨日后的新执行 transition 会被 lifecycle.py:29 判 PlanningTaskExpiredError 拒绝,但幂等重放放行。
  - recovery_day(恢复日)与 mock_day(模考日)判定藏在 get_or_create_today 里(repository 读的是周偏好的 regular_rest_weekday/regular_mock_weekday),调休只改容量不会改这两个。
- **关联**: 容量设置与调休、计划任务执行运行时、任务完成与证据结算

#### 3. 计划任务执行运行时(行测 task_session 生命周期 + 申论 essay workflow)

- **产品意图**:今日计划里的每个任务卡片点开后,行测、申论、模考三类分别启动各自的运行时。行测由 xingce_task_intro/practice/reflection 三个页面驱动 xingce_task_sessions 的 active→submitted→settled 状态机:启动时冻结题目包并写一条 planning-owned 的 in_progress 执行事实,答题保存 checkpoint(乐观 revision),交卷时一次性生成逐题 assessments + 复盘 review + pending 证据。申论任务走 essay-task 页的 essay workflow,材料标注/找点/提纲/作答/评阅/重写各阶段由 essay_intelligence 域独立落证据。模考任务在 mock_exam 会话与计划任务间建立 source_plan_task_id 绑定。
- **调用链**:行测:pages/xingce-task-intro 打开 -> POST /api/v2/xingce/task-sessions -> real_attempt/service.py:108 start_or_resume -> 冻结 bundle -> task_runtime/xingce_attempt/repository.py:185 insert_task_session(state=active) -> planning/xingce_start.py:62 写 in_progress 执行 -> practice 页 checkpoint 保存(:378 update_checkpoint revision++) -> submit(:541) -> submit_session(:378 active->submitted) -> 生成 assessments/review/pending evidence + complete_xingce_plan_task(:804)。申论:pages/essay-task -> essay_intelligence(learning_service.py:259 / drafting_service.py:154 / closure/service.py:359) -> PlanningService.complete_task_from_owner 收口。模考:mock_exam/service.py:144 bind_and_start_mock_plan_task -> mock_completion.py:75
- **关键文件**:
  - `backend/app/domains/xingce_learning/real_attempt/service.py:108`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:541`
  - `backend/app/domains/task_runtime/xingce_attempt/repository.py:185`
  - `backend/app/domains/task_runtime/xingce_attempt/repository.py:378`
  - `backend/app/domains/planning/xingce_start.py:16`
  - `apps/wechat/miniprogram/api/xingce-learning.ts:224`
  - `backend/app/domains/planning/mock_completion.py:26`
- **核心表**: `learning.xingce_task_sessions`, `learning.xingce_attempt_records`, `learning.xingce_assessments`, `learning.xingce_attempt_review_versions`
- **易错点**:
  - 行测启动是双重写入:先 insert task_session(active),再 insert task_execution(in_progress),两者通过 work_item_key 与 source_plan_task_id 绑定,任何一步失败都要整体回滚;completion 依赖『in_progress start fact 必须先存在』(xingce_completion.py:47)。
  - checkpoint 保存是 revision 乐观锁,submit 要求全部冻结题都答或显式跳过(set(answers)==set(question_ids)),漏答会 409 而不是静默通过。
  - session 一旦 submitted,settle_session(:418) 只能在 submitted 态推进;decide_cause 里 is_revision(验证模式)路径不调用 settle,状态机不对称(见完成与结算点)。
- **关联**: 任务完成与证据结算、今日计划生成

#### 4. 任务完成与证据结算(complete_xingce_plan_task / settle_planning_evidence / decide_cause 历史兼容)

- **产品意图**:这是整簇最绕的地方:一个行测任务的『完成』被拆成三段写,分属三个时机。交卷(submit)时已经写入 complete 执行事实和 pending_reflection 证据;错因决策(decide_cause)对 2026-08-04 之前的老会话做幂等补齐(老逻辑把计划完成错误地延后到错因决策),并把 pending 证据推进到 settled;复盘反思与证据 settle 是第三段。任务执行版本表 task_execution_versions 有四个写入方,完成命令只允许『权威所有者』(essay_intelligence)通过通用路径写;行测/模考用自己的完成器。decide_cause 通过 completion_payload 从 xingce_commands 响应体里弱握手读取已完成信息,读不到才回退到补写。
- **调用链**:交卷:real_attempt/service.py:541 submit -> record_pending_xingce_evidence(xingce_evidence.py:13,state=pending_reflection) -> complete_xingce_plan_task(xingce_completion.py:21,settle_planning_evidence=False,写 complete 执行事实+plan-task-state事件) -> _record_command 把 completion 存进 xingce_commands。错因决策:service.py:1089 decide_cause -> is_revision=review.state in {immediate_verification_due,mastery_recorded}(:1112) -> 若 !is_revision: task_session 必须 state=submitted(:1262) -> record_cause_decision -> 历史兼容块(:1280-1321):completion_payload 为 None 则 complete_xingce_plan_task 补齐,否则 evidence 仍 pending_reflection 则 settle_xingce_evidence(:1313) -> settle_session(submitted->settled) -> get_or_create_today 重排。执行写入方:service.py:430(通用)、xingce_completion.py:88、xingce_start.py:62、mock_completion.py:75(启动)+:171(完成)
- **关键文件**:
  - `backend/app/domains/planning/xingce_completion.py:21`
  - `backend/app/domains/planning/xingce_evidence.py:43`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:1089`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:1280`
  - `backend/app/domains/task_runtime/xingce_attempt/repository.py:450`
  - `backend/app/domains/planning/service.py:305`
  - `backend/app/domains/planning/service.py:430`
- **核心表**: `learning.task_execution_versions`, `learning.xingce_plan_evidence_versions`, `learning.xingce_commands`, `learning.xingce_cause_decision_versions`
- **易错点**:
  - 完成状态机三段写点:submit 的执行事实、decide_cause 的历史补齐、settle 的证据推进;新会话在交卷已写 complete,decide_cause 只对老会话补写——在 1280-1321 加新逻辑前先分清新旧会话。
  - completion_payload 是弱握手:按 sessionId 从 xingce_commands.response_payload 里倒序取最近一条含 completion 的记录,依赖 submit 时 projection 把 completion 序列化进去,任何响应结构改动都会让 decide_cause 走回补写分支。
  - is_revision 不对称:验证模式(复测)会话在 decide_cause 里不校验 submitted、不 settle_session、不走证据推进,只有非验证会话才有这套三写;给复测任务加结算逻辑不要动这套对称假设。
  - 通用 record_task_execution 拒绝 status=complete(service.py:263),完成必须由权威所有者注入;且 complete 的 evidence_refs 必须与任务 completion_contract 的 evidence_types 完全一致(:398-408),多一个少一个都 409。
  - task_execution_versions 有四个独立写入方、五种 command_id 前缀(xingce: / xingce-start: / mock-start: / mock-complete: / 通用),改 status 语义或 evidence_refs 结构要同时覆盖四家。
- **关联**: 计划任务执行运行时、今日计划生成

#### 5. 周计划 / 历史 / 日结算(weekly-plan / plan-history / daily-settlement 数据来源)

- **产品意图**:三个『回看』页面共用同一批版本化数据。周计划页展示 5+1+1 节奏(5 训练 + 1 模考 + 1 恢复),把七天每天的计划状态与完成数拼成 WeeklyPlanVersion,输入是偏好+各日计划+执行指纹,指纹不变则复用已有周版本。日结算页按当天最新计划+所有工作项的最近执行态汇总成完成/进行中/未开始/主动结束四类状态,并提示新风险(主动结束不算任务债)。计划历史页列出 daily_plan_versions 的变更链,比较相邻版本的 input_versions 得出 changedInputKeys 和 replan 原因。三个页面都是只读 GET,但内部都会先触发一次 get_or_create_today 保证数据新鲜。
- **调用链**:周计划:GET /api/v2/plans/week -> router.py:118 -> insights_service.py:156 weekly_plan -> get_or_create_today(:165) + insights_repository.daily_plan_summaries(:177) + preference_by_id(:168) -> 指纹比对(:213) -> insert_weekly_plan(:238)。日结算:GET /today/settlement -> router.py:98 -> insights_service.py:100 daily_settlement -> get_or_create_today(:106) + insights_repository.daily_settlement_task_facts(:107)。历史:GET /history -> router.py:135 -> insights_service.py:278 plan_history -> insights_repository.plan_history(:255)
- **关键文件**:
  - `backend/app/domains/planning/insights_service.py:156`
  - `backend/app/domains/planning/insights_service.py:100`
  - `backend/app/domains/planning/insights_service.py:278`
  - `backend/app/domains/planning/insights_repository.py:56`
  - `backend/app/domains/planning/insights_repository.py:144`
  - `backend/app/domains/planning/insights_repository.py:255`
  - `apps/wechat/miniprogram/pages/weekly-plan/index.ts:49`
  - `apps/wechat/miniprogram/pages/daily-settlement/index.ts:49`
- **核心表**: `learning.weekly_plan_versions`, `learning.daily_plan_versions`, `learning.daily_plan_tasks`, `learning.task_execution_versions`
- **易错点**:
  - 周计划的 completed 统计依赖 daily_plan_summaries 的 lateral 执行 join,按 work_item_key 取最新执行;同一天被重排多次时只统计最新计划版本的任务,历史版本任务不计入。
  - daily_settlement 的 status 是空任务->empty、全完成->complete、有完成/放弃->partial、有进行中->in_progress,判断顺序敏感(先判 empty 再判全完成)。
  - plan_history 只按 generated_at 倒序拉 50 条且不做分页(nextCursor 恒 null);changedInputKeys 是相邻版本 input_versions 的对称差,首版为空。
- **关联**: 今日计划生成、任务完成与证据结算、学习偏好

#### 6. 学习偏好设置(study-preference,weekly_study_preference_versions 版本链)

- **产品意图**:用户设定每周学习节奏:工作日/周末分钟数、固定休息日(恢复日)、固定模考日,形成 5+1+1 节奏的基线。写入是乐观并发命令,expected_preference_version 不匹配就 409 让前端重载。每条偏好是 weekly_study_preference_versions 里的一个版本,用 supersedes_preference_version_id 串成链,confirmed=true 的版本才是权威头。规划系统在没设过偏好时按 profile.daily_minutes 自动种一条 system_default 的未确认偏好,容量则从偏好分钟数派生;确认偏好后同一事务推进 onboarding progress,并写回 learner.profiles 的 daily_minutes 兼容投影。
- **调用链**:study-preference 页 -> features/onboarding/study-preference-controller.ts:203 submit -> pending-store 落 pending 记录 -> OnboardingApi.putStudyPreference(command, idempotencyKey) -> onboarding/router.py:420 -> progress_service.py:628 record_study_preference -> 版本冲突校验(:675) -> progress_repository.append_preference(:438) 落 weekly_study_preference_versions(confirmed=true,source_type=user_confirmed/user_revised) -> 更新 learner.profiles(:517) -> outbox weekly_study_preference.changed。规划侧读取:planning/repository.py:151 current_or_create_preference(未确认时按 profile 种子)
- **关键文件**:
  - `backend/app/domains/onboarding/progress_service.py:628`
  - `backend/app/domains/onboarding/progress_repository.py:438`
  - `backend/app/domains/onboarding/progress_repository.py:402`
  - `backend/app/domains/planning/repository.py:151`
  - `apps/wechat/miniprogram/pages/study-preference/index.ts:103`
  - `apps/wechat/miniprogram/features/onboarding/study-preference-controller.ts:203`
- **核心表**: `learning.weekly_study_preference_versions`, `learner.profiles`, `learning.daily_capacity_versions`
- **易错点**:
  - onboarding 里程碑冻结的是 progress 快照,不冻结偏好聚合:老用户改偏好会新增偏好版本但 progress 不再前进,read_study_preference 返回的是当前 confirmed 头。
  - planning 域也会种 system_default 的未确认偏好(planning/repository.py:238),与 onboarding 的 user_confirmed 版本并存;判定用 input_fingerprint 去重,两个域的 command_id 前缀不同。
  - writes 同时写 learner.profiles(兼容投影)与版本链,profile_version 不变但 daily_minutes 变,旧的读 profile 代码可能看到不一致。
  - 偏好改了不直接重排当天计划:只有下次 get_or_create_today 时 input_versions.weeklyPreference 变化才触发 replan,且 pending_study_preference 本地记录在客户端 7 天内可重放。
- **关联**: 容量设置与调休、今日计划生成、周计划/历史/日结算

## 3. 行测

**本簇概览**:行测簇是「刷题→复盘→错因→掌握→复测」的完整闭环：planning 域负责每天编排行测任务，xingce_learning/real_attempt 域负责会话执行与逐题判分，mastery 域用 FSRS 管理掌握状态，cause_agent 域以异步 Agent 做错因分析，retake 域则把往年成绩换算成训练优先级。所有写命令共享三件套防御：lock_user（learner.users 行锁）+ _replay（xingce_commands 幂等重放）+ revision 乐观锁；task_editing 域额外提供多设备编辑租约。最重要的跨功能警告：planning↔xingce_learning 是唯一域级双向依赖——xingce_learning 在交卷时内联调用 planning 的 complete/settle/start 落 task_execution_versions，而 planning 反过来读 xingce 的 assessments/mastery 快照排任务，改任何一边的契约都必须同时改另一边（service.py 中 PlanningContractError 会被 router._write 捕获并映射成 409 提示用户刷新今日计划）。

#### 1. 行测刷题会话（practice + start_or_resume/save_checkpoint）

- **产品意图**:用户从今日计划点进行测任务卡，先经 xingce-task-intro 任务说明页或直接进 practice 页。前端从今日计划解析出唯一的行测 planTaskId（baseline/practice/targeted/verification 四种模式），调用 POST /api/v2/xingce/task-sessions 发起或恢复会话。后端按计划任务的工作项唯一性判断是新建还是续作：新建时按模式选题目——verification 复测模式绑定一个到期掌握版本并排除已用题目，targeted 模式走知识树定向选题，否则按模块普通选题，再把选出的题目冻结成 safe_bundle+scoring_bundle 双份不可变内容写入会话行。作答过程通过 PUT checkpoint 断点续传：每题的选项、信心、标记、批注、动作事件都以不可变 answer_version 追加落库，checkpoint 以 revision 乐观锁整体覆盖。前端本地有 pending-store 草稿、断点续传与多设备编辑租约（task_edit_leases）机制，网络失败后重试同一幂等键即可安全续写。
- **调用链**:practice/index.ts:433 loadTask -> resolveXingcePlanTaskId(controller.ts:24) -> XingceLearningApi.startSession -> POST /api/v2/xingce/task-sessions -> real_attempt/router.py:118 -> service.start_or_resume(:108) -> runtime_repository.lock_user + _replay -> current_plan_task -> 按 mode 选题(select_frozen_questions/select_targeted_frozen_questions) -> _freeze_bundle -> insert_task_session -> start_xingce_plan_task(planning/xingce_start.py:16 落 in_progress execution) -> 前端 autosaveCheckpoint -> PUT .../checkpoint -> router.py:197(editing.authorize_write) -> service.save_checkpoint(:443) -> insert_answer_version + update_checkpoint
- **关键文件**:
  - `apps/wechat/miniprogram/pages/practice/index.ts:433`
  - `apps/wechat/miniprogram/pages/practice/index.ts:2039`
  - `apps/wechat/miniprogram/api/xingce-learning.ts:219`
  - `backend/app/domains/xingce_learning/real_attempt/router.py:114`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:108`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:443`
  - `backend/app/domains/task_runtime/xingce_attempt/repository.py:175`
  - `backend/app/domains/xingce_learning/contracts.py:9`
- **核心表**: `learning.xingce_task_sessions`, `learning.xingce_answer_versions`, `learning.xingce_commands`, `learning.daily_plan_tasks`, `learning.daily_plan_versions`, `learning.task_execution_versions`, `learning.task_edit_leases`, `product.knowledge_map_question_bindings`, `product.question_content_versions`
- **易错点**:
  - v1 的 practice 域（/api/v1/practice/tasks/today-xingce-guokao-weighted + learner.practice_answers）是遗留每日三题的轻量链路，与 v2 的 real_attempt 会话是两套并行体系，别把两者当一条链读
  - verification 复测模式的选题完全由 due mastery 版本驱动，plan_task 的 evidence_refs 里必须恰好有一条 xingce_mastery_state_version/due_verification 引用，否则 start_or_resume 直接 409 xingce_verification_source_missing
  - checkpoint 的 total_elapsed_seconds 与每题 elapsedSeconds 单调不降且各题之和不能超过总额，前端与后端双重校验；改答次数是拿 answer_versions 全历史数出来的，不是 checkpoint 里的计数
- **关联**: 交卷与判分、掌握度与能力估计、今日计划(planning)

#### 2. 交卷与判分（submit 主链路）

- **产品意图**:用户点交卷后，前端先把未答题在本地冻结为未作答并保存一次 checkpoint，再以 revision+编辑租约提交。后端逐题判分：正确答案来自 scoring_bundle 的冻结 correctOptionIds，判定 correct/incorrect/guessed_correct(答对但低信心)/skipped 四态，并记录改答次数、信心证据与超时证据（建议时长来自 estimated_minutes 均摊）。每道题同时写 attempt_record（不可变作答事实，带 submission_hash 与血统引用）和 assessment（判分快照，含 assessment_key_hash 保证同题同版本同一次作答不重复计数）。随后更新会话为 submitted、写 review_version 初版（reflection_due，全对时 requires_cause_decision=false）、登记待结算的 plan_evidence，并调用 planning 的 complete_xingce_plan_task 把今日计划任务闭环为 complete（含 outbox 状态事件）。交卷即完成计划；复盘证据在后续错因决策时单独 settle。
- **调用链**:practice/index.ts:1492 submitAttempt -> UNANSWERED_FINALIZED -> saveCheckpoint -> api.submitAttempt -> POST .../submit -> router.py:234(editing.authorize_write) -> service.submit(:541) -> 逐题 result_items + insert_attempt_record + insert_assessment -> submit_session(active->submitted) -> insert_review_version(reflection_due) -> record_pending_xingce_evidence(planning/xingce_evidence.py:13) -> complete_xingce_plan_task(planning/xingce_completion.py:21 落 complete execution) -> outbox 事件(attempt-recorded/assessment-recorded/state-changed)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/practice/index.ts:1492`
  - `backend/app/domains/xingce_learning/real_attempt/router.py:234`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:541`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:592`
  - `backend/app/domains/task_runtime/xingce_attempt/repository.py:324`
  - `backend/app/domains/xingce_learning/real_attempt/repository.py:597`
  - `backend/app/domains/planning/xingce_completion.py:21`
  - `backend/app/domains/planning/xingce_evidence.py:13`
- **核心表**: `learning.xingce_attempt_records`, `learning.xingce_assessments`, `learning.xingce_task_sessions`, `learning.xingce_attempt_review_versions`, `learning.xingce_plan_evidence_versions`, `learning.task_execution_versions`, `platform.outbox_events`, `learning.xingce_commands`
- **易错点**:
  - 提交要求 checkpoint 里所有冻结题目都有答案（含显式 skipped），否则 409 'every frozen question must be answered or explicitly skipped'；未达成的题在交卷时由前端 UNANSWERED_FINALIZED 批量落为 skipped
  - 交卷在事务内同步写 planning（complete 落 execution + outbox 事件），一旦 planning 契约失败整个会话提交回滚，错误映射为 409 xingce_planning_conflict 提示返回今日计划
  - plan_evidence 交卷时只是 pending_reflection 占位，evidence 真正 settle 到 execution 是在 decide_cause 时；2026-08-04 前历史会话的完成被延后到错因决策，现由 completion_payload 幂等补齐
- **关联**: 复盘与错因决策、行测刷题会话、今日计划(planning)

#### 3. 复盘与错因决策（reflection/cause_followup/decide_cause 状态机）

- **产品意图**:交卷后进入 reflection 页：先看训练总结，再写原话复盘（可选录音转写、结构化标签、'方法正确但计算错误'的逐题解读）。提交复盘后进入错因链路——全对高信心组直接短路为 evidence_insufficient 不调模型；否则 cause_agent 域为每个复盘创建异步后台任务（含额度预留），Agent 先加载作答证据、用户复盘、题解、错因分类、动作轨迹、墨迹公式六类工具，再调用大模型产出错因候选，候选有 candidate_ready/followup_required/evidence_insufficient/terminated 四种状态，用户按状态走确认/追问/改写/跳过。每次用户动作都把 review_version 追加一个新版本，revision 递增，前端用 1.5s 轮询 getSession 等待后台候选。最终 decide_cause 写入 cause_decision_version + confirmed_cause，并根据会话模式推进到 immediate_verification_due 或 mastery_recorded，同时结算 planning 证据并把会话 settle。
- **调用链**:reflection/index.ts:932 submitReflection -> PUT .../attempt-reviews/{review_id}/reflection -> router.py:275 -> service.submit_reflection(:884) -> insert_reflection + bind voice + cause_agent_workflow.start_after_reflection(cause_agent/service.py:89 入队后台 job) -> 前端 1.5s 轮询 getSession -> worker(process_next:_process_claim) 加载六类工具 -> provider 生成 -> _publish_output(worker.py:596 写 cause_candidate + review 状态) -> 用户 confirm/replace/reject/defer -> PUT .../cause-decision -> router.py:331 -> service.decide_cause(:1089) -> insert_cause_decision + insert_confirmed_cause + mastery.record_cause_decision -> settle_xingce_evidence + settle_session(->settled)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/reflection/index.ts:932`
  - `apps/wechat/miniprogram/pages/reflection/index.ts:1443`
  - `backend/app/domains/xingce_learning/real_attempt/router.py:275`
  - `backend/app/domains/xingce_learning/real_attempt/router.py:331`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:884`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:1089`
  - `backend/app/domains/xingce_learning/cause_agent/service.py:89`
  - `backend/app/domains/xingce_learning/cause_agent/worker.py:596`
- **核心表**: `learning.xingce_attempt_review_versions`, `learning.xingce_reflection_versions`, `learning.xingce_cause_candidate_versions`, `learning.xingce_cause_decision_versions`, `learning.xingce_confirmed_cause_versions`, `ai_audit.agent_run_versions`, `ai_audit.agent_tool_call_events`, `ai_audit.model_call_ledger`, `platform.background_jobs`, `commerce.feature_usage_reservations`, `learning.xingce_plan_evidence_versions`
- **易错点**:
  - 错因额度（xingce_cause_analysis）在入队时 reserve、模型调用成功后 consume、短路/终止时 refund，泄漏会导致用户永久失去当日额度；全对短路路径必须在 checkpoint 显式写 quotaState=not_charged
  - Agent 发布候选与 review 状态切换是强一致单事务（worker 用 pg_advisory_xact_lock + for_update），review 状态必须仍是 cause_candidate_pending，否则抛 JobContractError
  - decide_cause 对 revision 状态（immediate_verification_due/mastery_recorded）允许复测开始前改判，但一旦 verification_started_for_review 为真就 409 锁定；skip 只能用于历史 unavailable 候选，现代流程要选 confirm/replace/reject/defer
- **关联**: 掌握度与能力估计、收藏与复测、争议上报、规划完成的耦合点

#### 4. 掌握度与能力估计（mastery 域 + ability_estimation）

- **产品意图**:错因被确认后，mastery 域为该复盘创建一个掌握卡（xingce_mastery_state_versions），状态机为 reflected→provisionally_mastered→stable_mastered/decayed，内部用冻结版 FSRS 6.3.1 算法+固定参数排下一次复测时间。非复测会话走 _create_reflected（立即进入 immediate 验证档期），复测会话走 _advance_verification（用当次验证证据按 rating 推进档期）。能力估计则是另一条线：ability_estimation 域基于 score_evidence（外部模考/实考分数）产出 ability_estimate_versions 的推荐分区间，planning 的 objective_snapshot 把这两者合成——60 天行测真实 assessments 决定基线/定向/验证的题量与最弱模块，due mastery 决定复测任务，estimate 决定岗位分差是否已满足。xingce-mastery 页（/api/v2/xingce/mastery-queue）按待复盘/待复测/已稳定/已失效四个桶分页展示，行动入口要么跳复盘页要么跳任务说明页。
- **调用链**:decide_cause -> mastery.record_cause_decision(mastery/service.py:116) -> _create_reflected/_advance_verification -> insert xingce_mastery_state_versions + outbox xingce-mastery-state-changed@v1 -> 前端 /pages/xingce-mastery/index.ts:227 getMasteryQueue -> service.queue(mastery/service.py:53) -> repository queue_counts/queue_page -> planning 排任务时 objective_snapshot(planning/repository.py:639) 读 due mastery/assessments/estimate -> candidates.py:260 生成 xingce-due-verification 任务
- **关键文件**:
  - `backend/app/domains/xingce_learning/mastery/service.py:116`
  - `backend/app/domains/xingce_learning/mastery/service.py:152`
  - `backend/app/domains/xingce_learning/mastery/service.py:209`
  - `backend/app/domains/xingce_learning/mastery/scheduler.py:108`
  - `backend/app/domains/xingce_learning/mastery/repository.py:13`
  - `backend/app/domains/planning/repository.py:639`
  - `backend/app/domains/planning/repository.py:723`
  - `backend/app/domains/ability_estimation/service.py:608`
  - `apps/wechat/miniprogram/pages/xingce-mastery/index.ts:227`
- **核心表**: `learning.xingce_mastery_state_versions`, `learning.xingce_assessments`, `learning.ability_estimate_versions`, `learning.score_evidence`, `learning.daily_plan_tasks`, `learning.task_execution_versions`
- **易错点**:
  - FSRS 的 20 个参数在 scheduler.py:24 被显式冻结，库升级不能静默改变存量学习者的复习间隔——policy_version 不匹配直接 raise
  - stable_mastered 时 due_at 置 None，不再是'到期可验证'；验证队列只认 due_at<=now 且内容未失效（used_question_ids 引用的题必须仍 active/canonical，否则进 invalidated 桶）
  - ability_estimate 与 mastery 是两套独立状态：estimate 基于分数证据算分差，mastery 基于做题证据排复测，objective_snapshot 的 source_version_id 只在没有 due 项时才回落到 estimate；别指望一次交卷就刷新能力估计
- **关联**: 交卷与判分、复盘与错因决策、今日计划(planning)

#### 5. 收藏与复测（favorites + retake 页）

- **产品意图**:收藏是做题中的单题打标：practice 页收藏按钮调用 PUT /api/v2/xingce/question-favorites/{question_id}，服务端校验题目必须存在于该用户某个行测会话的冻结 bundle 且内容版本一致（source_session_contains），写 xingce_question_favorite_states 记录收藏时的内容版本。收藏列表页（/pages/xingce-mastery/favorites/）用 keyset 游标分页，每项带 stem 预览、内容是否已变更（contentChanged）、题目是否仍可用。复测（retake）是另一条链路：面向二战考生，从 onboarding 确认的二战经历 + score_evidence 里已核验的上一年国考成绩建立失分结构（行测六模块细分 vs 申论），写入 retake_program_versions 作为训练优先级输入——明确声明'往年成绩只用于失分结构与训练优先级，不参与退款/现金奖励/提分计算'，且与 mastery 的逐题复测队列（due_verification）是两个不同概念。
- **调用链**:practice 收藏 -> executeQuestionFavoriteCommand(features/xingce/favorites/command.ts:86) -> api.setState -> PUT /api/v2/xingce/question-favorites/{qid} -> favorites/router.py:124 -> service.set_state(:43) -> source_session_contains + set_favorited(learning.xingce_question_favorite_states) -> 收藏列表页 GET /api/v2/xingce/question-favorites -> retake 页 -> features/retake/controller.ts:89 load -> GET /api/v2/retake -> retake/router.py:39 -> service.workspace -> POST /api/v2/retake/programs -> service.enroll(:66) -> insert retake_program_versions
- **关键文件**:
  - `backend/app/domains/xingce_learning/favorites/router.py:124`
  - `backend/app/domains/xingce_learning/favorites/service.py:43`
  - `backend/app/domains/xingce_learning/favorites/service.py:169`
  - `backend/app/domains/xingce_learning/favorites/repository.py:97`
  - `apps/wechat/miniprogram/features/xingce/favorites/command.ts:86`
  - `apps/wechat/miniprogram/pages/xingce-mastery/favorites/index.ts:125`
  - `backend/app/domains/retake/router.py:39`
  - `backend/app/domains/retake/service.py:36`
  - `backend/app/domains/retake/service.py:66`
  - `backend/app/domains/retake/repository.py:125`
- **核心表**: `learning.xingce_question_favorite_states`, `learning.xingce_question_favorite_commands`, `learning.xingce_task_sessions`, `product.question_content_versions`, `learning.retake_program_versions`, `learning.score_evidence`
- **易错点**:
  - 收藏必须绑定作答来源会话和该题内容版本，不能凭空收藏；题目下架/改版后收藏状态仍是 favorited 但 availability 变 unavailable、contentChanged=true，需要按新版本重新收藏
  - retake 只有 GET workspace + POST programs 两个端点，创建后即 active 且不可撤销/无结算逻辑；训练优先级只读，不会改分数
  - 别把 retake 的'复测计划'和 mastery 的 due_verification 混为一谈：前者按往年成绩定失分结构，后者按 FSRS 到期时间定逐题复测
- **关联**: 行测刷题会话、掌握度与能力估计、能力估计/position 域

#### 6. 争议上报（report_question_dispute）

- **产品意图**:做题或复测结果页对某道题存疑时，用户可以上报争议：选择分类（题干错误/选项错误/答案错误/材料不符/图片不可读/其他）并写具体描述。服务端用幂等键去重后，校验上报必须绑定当前会话冻结 bundle 中的题目和内容版本（防止拿旧题骗过核对），并把当时的会话 revision、bundle 版本、作答状态哈希、材料位置作为 evidence_context 一并存档，写 xingce_question_disputes 并发出 outbox 事件供人工核对。争议对复测结果的直接影响是：该会话若有未决争议，verification_outcome 判定为 disputed_or_invalidated/pending_dispute，暂停其参与能力判断，前端会在复测结果页提示'这次复测结果已暂停参与能力判断'。
- **调用链**:practice/index.ts:1004 reportDispute -> POST .../task-sessions/{id}/disputes -> router.py:164 -> service.report_question_dispute(:330) -> 幂等键查重 -> 校验题目在 safe_bundle 内 -> insert xingce_question_disputes + outbox xingce-question-dispute-filed@v1 -> 复测页 _verification_outcome(:1567) 检测 session_has_question_dispute -> _verification_status -> disputed_or_invalidated
- **关键文件**:
  - `apps/wechat/miniprogram/pages/practice/index.ts:990`
  - `backend/app/domains/xingce_learning/real_attempt/router.py:164`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:330`
  - `backend/app/domains/xingce_learning/real_attempt/repository.py:110`
  - `backend/app/domains/xingce_learning/real_attempt/schemas.py:265`
  - `backend/app/domains/xingce_learning/real_attempt/service.py:1608`
- **核心表**: `learning.xingce_question_disputes`, `learning.xingce_task_sessions`, `platform.outbox_events`
- **易错点**:
  - 上报只接受会话冻结 bundle 内的题目且 content_version_id 必须 64 位精确匹配，复用幂等键发起不同争议会 409
  - 争议只在 verification 模式的结果里有判定语义（pending_dispute 暂停能力判断），普通刷题会话的争议不会改变判分结果
  - 争议上报在 router 里是独立 commit（不经 _write 包装），而交卷/复盘走 _write 的 PlanningContractError 映射——两处错误处理路径不同
- **关联**: 交卷与判分、掌握度与能力估计

## 4. 申论

**本簇概览**:申论是「考公搭子」里最重的 AI 域,全链路由 essay_intelligence 一个领域包承载:前端 pages/essay-task(3033 行)是所有申论交互的单一入口,业务逻辑已抽到 features/essay-task/(drafting/closure/pending-store 等),页面本身是编排壳;后端以「作答落库(learning)→ 批改入队(platform.background_jobs)→ 消费批改(review_worker)→ 改写/迁移闭环(closure+mastery)」为主线,生产者 workflow_service 与消费者 review_worker 靠 background_jobs 解耦,结构健康。整个域围绕「证据链可信」设计:每个 model 调用都有 model_call_ledger 计量、checkpoint 加密落盘、input/output/execution 三层指纹校验,评阅证据(grounding)在展示前经过锚点+哈希强校验,禁止展示不完整分数。跨功能警告:批改是异步解耦的,前端必须轮询 background_jobs 状态,任何时候都不能假设「提交完就有结果」;另外 essay_task 与 essay_review/essay_evidence/essay_rewrite/essay_compose/essay_material 多个页面共享 workspace 数据源,progression.reviewVersionId / rewriteVersionId 是各页面跳转的枢纽,动 workspace 契约会连锁影响所有页面。legacy 的 /api/v1/essay 已退役(写接口全部 410),只保留一个只读 task 查询。

#### 1. 申论作答工作台

- **产品意图**:用户从今日计划进入申论题后,作答工作台(essay-task 页)按阶段(材料阅读/列要点/写大纲/作答/复盘)展示 workspace:题目材料(带材料地图)、已保存的学习产物(要点/大纲/批注)、作答框、复盘反思原因选择、AI 评阅进度区。它支持三种作答输入:键盘打字(默认)、手写拍照 OCR(拍照或相册上传原图→云端识别→逐段核对修订→确认后回填为 answerText+ocr_confirmed 来源)、语音复盘(录音逐条保存→上传→安全扫描→随提交带上 voiceArtifactVersionIds)。作答、复盘和今日任务完成是「一个原子命令」提交,支持断点续传(本地草稿 + pending 状态机),提交后自动启动 AI 评阅,页面留在原地轮询评阅结果。页面虽大但只是编排壳,输入/持久化/状态机都抽到 features/essay-task/。
- **调用链**:essay-task/index.ts:489 loadWorkspace -> api.getWorkspace(/tasks/{id}/workspace) -> router.get_task_workspace -> learning_service.workspace:387 -> workspace_content/workspace_segments/material_scope 解析 + current_for_task + workspace_progression 拼 EssayTaskWorkspace;输入:打字 draft.answerText -> submitDrafting:1904 -> completeDraftingPlanTask:157 -> /drafting/complete-plan-task -> drafting_service.complete:71 -> create_attempt/create_answer/reflection artifact + planning.complete_task_from_owner + ensure_review_intent;手写:chooseHandwritingImage:1087 -> api/essay-handwriting.ts createRecognition -> /handwriting-recognitions -> handwriting_service.start -> confirmHandwriting:1154 -> confirmRecognition -> ocr_confirmed 回填;语音:startVoiceRecording:1740 -> uploadVoiceSegments:1806 -> ReflectionVoiceUploadCoordinator
- **关键文件**:
  - `apps/wechat/miniprogram/pages/essay-task/index.ts:489`
  - `apps/wechat/miniprogram/features/essay-task/drafting.ts:90`
  - `apps/wechat/miniprogram/features/essay-task/drafting-store.ts`
  - `apps/wechat/miniprogram/features/essay-task/page-runtime.ts`
  - `backend/app/domains/essay_intelligence/learning_service.py:387`
  - `backend/app/domains/essay_intelligence/drafting_service.py:71`
  - `backend/app/domains/essay_intelligence/handwriting_service.py:49`
  - `apps/wechat/miniprogram/api/essay-handwriting.ts:33`
- **核心表**: `learning.essay_attempts`, `learning.essay_answer_versions`, `learning.essay_learning_artifact_versions`, `learning.essay_review_intent_versions`, `product.essay_task_versions`, `product.essay_material_bundle_versions`, `product.essay_material_segments`, `product.essay_rubric_versions`, `learning.recognition_revisions`, `learning.source_artifact_versions`
- **易错点**:
  - 手写识别只接受 camera_image/image_import 来源,行测答题卡/文档来源会被 422 拒绝(essay-handwriting.ts:31),且回填前必须确认 recognitionKind==='handwriting_ocr'。
  - 作答与任务完成是单个原子事务(route 是唯一 commit 边界),本地 pending 状态机(completion_pending/review_pending)与 server 端幂等 key 必须一致,改字段契约会破坏恢复流程。
  - workspace 的 material 段被 material_scope 按题干裁剪(resolve_material_scope),不是全量材料;改题干正则会影响所有页面看到的材料。
  - 语音复盘走 learning_media 域(learning_media_upload_sessions 等),与 essay_intelligence 不是同一 repo,提交前必须全部 available 否则拒绝。
- **关联**: 素材库、提交与批改队列、评审证据链、今日计划(planning.complete_task_from_owner)

#### 2. 提交与批改队列

- **产品意图**:作答完成落库后,系统不会同步批改,而是把「评阅请求」投递到平台级异步任务队列:workflow_service 是生产者(入队时锁用户、算指纹、预留评阅额度、写 review_intent 意图),review_worker 是消费者(独立常驻进程循环 claim_next)。两者只通过 platform.background_jobs 的一张表解耦,靠 job_key 幂等、claim_token 防并发、max_attempts=3 重试、10 分钟 stale 回收。前端提交完成后调用 ensureCurrentGroundedReview(服务端按当前答案幂等入队),然后 2 秒轮询 getGroundedReviewJob,直到 job succeeded。这一层是申论可靠性的核心,不能改回同步。
- **调用链**:essay-task 提交 -> resumeDraftingSubmission:1968 -> api.completeDraftingPlanTask -> drafting_service.complete 落库 + ensure_review_intent -> resumeCurrentReview:2039 -> ensureCurrentGroundedReview -> workflow_service.ensure_current_review:378 -> queue_review:446 -> _require_worker_available:552(worker 未启用则 503) -> _queue_review_locked:881 -> _ensure_review_intent_locked + commerce.reserve_feature_usage + _jobs.enqueue(job_type=essay_grounded_review_v2, max_attempts=3) + outbox essay_review.requested -> 前端 pollGroundedReview:2125 -> getGroundedReviewJob(/review-jobs/{id})
- **关键文件**:
  - `backend/app/domains/essay_intelligence/workflow_service.py:446`
  - `backend/app/domains/essay_intelligence/workflow_service.py:378`
  - `backend/app/domains/essay_intelligence/workflow_service.py:881`
  - `backend/app/platform/jobs.py:535`
  - `backend/app/workers/essay_reviews.py:25`
  - `backend/app/domains/essay_intelligence/workflow_repository.py:273`
  - `apps/wechat/miniprogram/pages/essay-task/index.ts:2125`
- **核心表**: `platform.background_jobs`, `learning.essay_review_intent_versions`, `learning.essay_review_execution_commands`, `commerce.feature_usage_reservations`
- **易错点**:
  - worker 未启用(ESSAY_REVIEW_WORKER_ENABLED=false)时入队直接抛 EssayReviewWorkerUnavailable -> 503,前端必须能区分「暂时不可用」和「已排队」,别把 503 当失败重试导致刷队列。
  - 同一 input_fingerprint 的 queue 命令是幂等的(job_key=essay-grounded-review:{user}:{execution_fingerprint}),worker 已跑时新命令会复用同一 job 而非重复投递;不要手动绕过 job_key。
  - worker 依赖 essay_review_worker_poll_seconds/stale_recovery_seconds 两个设置,stale 回收 10 分钟(REVIEW_VALIDITY 30 天),改了任一都会影响恢复窗口。
  - ensure_current_review 用「服务端当前答案」而非前端传的答案,前端传的 answerVersionId 不是当前版本会 409。
- **关联**: 智能批改、申论作答工作台、commerce 额度域(reserve_feature_usage 429)

#### 3. 智能批改

- **产品意图**:批改本质是「把用户作答、题干、材料全文、评分标准(量表)打包成上下文集,交给模型打分并输出结构化 JSON」。review_worker 支持两套运行时:生产强制多模型方案——TokenRhythm 与混元两个独立 slot 各自打分,然后对比分差:一致则直接取,轻微/重大分歧则把候选匿名化(blind)交给 adjudicator 裁判,杜绝单个模型结论被直接采信;测试环境可退化为单模型 generate→repair 两段。每次模型调用都写 model_call_ledger(计量+计费)、checkpoint 加密保存、校验 provider_request_id 审计对,最终评审(分数区间/维度/要点覆盖/findings/改写建议)整体过 grounded 校验后才落 learning.essay_review_versions,失败或越界只落 blocked/failed 状态并退额度。
- **调用链**:review_worker.process_next:180 -> claim_next(platform.background_jobs) -> _process_claim:201 校验 input_fingerprint -> _process_multi_model_claim:381 -> EssayMultiModelGradingCoordinator.execute(multi_model_grading_coordinator.py:76) -> 每个 slot LedgeredEssayCandidateRunner.run_candidate(multi_model_candidate_runner.py:133) -> compare_essay_grading_candidates(multi_model_grading.py:138) 判 consistent/minor/major/unable -> 分歧时 build_blind_adjudication_request + LedgeredEssayBlindAdjudicator.adjudicate -> resolve_essay_grading -> _apply_candidate:854 -> _persist_result:896 insert_review + insert_grading_bundle + link_model_call + consume_quota + outbox
- **关键文件**:
  - `backend/app/domains/essay_intelligence/review_worker.py:381`
  - `backend/app/domains/essay_intelligence/multi_model_runtime.py:64`
  - `backend/app/domains/essay_intelligence/multi_model_candidate_runner.py:133`
  - `backend/app/domains/essay_intelligence/multi_model_grading_coordinator.py:76`
  - `backend/app/domains/essay_intelligence/multi_model_grading.py:138`
  - `backend/app/domains/essay_intelligence/blind_adjudicator.py:354`
  - `backend/app/domains/essay_intelligence/review_service.py:42`
  - `backend/app/domains/essay_intelligence/workflow_repository.py:565`
- **核心表**: `learning.essay_review_versions`, `ai_audit.essay_review_model_calls`, `ai_audit.essay_review_checkpoints`, `ai_audit.essay_grading_runs`, `ai_audit.essay_grading_candidates`, `ai_audit.essay_grading_adjudications`, `ai_audit.essay_review_grading_bindings`
- **易错点**:
  - 生产环境禁止单模型执行:构造 GroundedEssayReviewWorker 时若 provider 与 multi-model plan 同时传入直接抛 ValueError,且 production 强制走 multi-model;测试要单模型必须显式传 provider。
  - slot 相位表硬编码 3 处:review_worker.py(_provider_request_audit 的相位清单)、review_checkpoint.py(_CHECKPOINT_PHASE_PATTERN)、multi_model_runtime.py(slot 定义)。相位清单保留旧 slot 仅用于历史审计读取；当前正式写入 slot 为 tokenrhythm/hunyuan，新增或改 slot 必须三处同步。
  - 同一 job 的 provider 调用跨重试用固定 call_key(essay-review:{job_id}:{phase})保证幂等,provider 返回的 output_fingerprint 变了视为 idempotency violation 非重试;不要改 call_key 构成。
  - 评审结果无论成败都会退款/消费额度,且 checkpoint 在成功路径 delete_for_job;中途改动 quota 语义会破坏计费对账。
- **关联**: 提交与批改队列、评审证据链、ai/model_cost(价格卡)与 ai/governance(AI 治理证据)

#### 4. 改写与转移

- **产品意图**:这是申论学习闭环的「二次加工」:拿到 AI 评阅后,用户先对每条 finding 表态(接受/不接受,形成 review_acknowledgement 产物),然后进入改写(rewrite)阶段——针对接受的 finding 写新答案,系统按「接受的 finding 是否全部解决」判定 passed/needs_revision,并推进掌握度(mastery);通过后才能做转移(transfer)迁移练习——把源题的改写经验迁移到一道新题,先在新题独立作答→批改→形成新题证据,再核验源改写,写入迁移验证。两条线都是「先落新答案/新评阅,再原子完成计划任务」的多段状态机,前端 closure-flow.ts 串起 attempt→answer→review→decision→atomic completion 各步,每步幂等可恢复。
- **调用链**:前端 loadClosureContext:1250 -> submitRewrite:1519 -> buildRewritePlanTaskCompletionCommand -> completeRewritePlanTask -> router /rewrites/complete-plan-task -> closure.service.record_rewrite_and_complete_plan_task:341 -> record_rewrite:85(校验 accepted findings 全覆盖、create_answer、insert_rewrite、mastery.advance_rewrite) + planning.complete_task_from_owner;转移:submitTransfer:1599 -> closure-flow.advanceEssayTransferFlow:50(createAttempt→createAnswer→queueGroundedReview→getGroundedReview→createArtifact 决策→completeTransferPlanTask) -> closure.service.record_transfer_and_complete_plan_task:630 -> record_transfer:391(要求源改写 passed 且新题评阅未过期) -> insert_transfer + mastery.advance_transfer
- **关键文件**:
  - `apps/wechat/miniprogram/pages/essay-task/index.ts:1519`
  - `apps/wechat/miniprogram/pages/essay-task/index.ts:1603`
  - `apps/wechat/miniprogram/features/essay-task/closure-flow.ts:50`
  - `apps/wechat/miniprogram/features/essay-task/closure.ts:139`
  - `backend/app/domains/essay_intelligence/closure/service.py:85`
  - `backend/app/domains/essay_intelligence/closure/service.py:391`
  - `backend/app/domains/essay_intelligence/mastery/service.py`
  - `backend/app/domains/essay_intelligence/closure/repository.py`
- **核心表**: `learning.essay_rewrite_versions`, `learning.essay_transfer_verification_versions`, `learning.essay_mastery_state_versions`, `learning.essay_answer_versions`, `learning.essay_review_versions`, `learning.daily_plan_tasks`, `learning.task_execution_versions`
- **易错点**:
  - 改写必须用「当前拥有且已确认」的 grounded review + finding decision 作为 source,否则 EssayClosureNotFound;转移额外要求源改写 status=passed 且新题评阅 valid_until 未过期(30 天),过期必须 regenerate。
  - 改写 resolution 必须精确覆盖 accepted findings(多一个少一个都 409),且 current rewrite 已变化时会拒绝 based_on 过期版本——前端需在 terminalConflict 后清空本地草稿从今日计划重进。
  - 转移是 6 段状态机(closure-flow.ts),任何一段中断都靠本机 closureDraft + idempotencyKey 恢复;手动跳过阶段会抛『迁移恢复状态不可执行』。
- **关联**: 智能批改、评审证据链、掌握度(mastery)域、今日计划(planning)

#### 5. 素材库

- **产品意图**:申论材料阅读页,把题目材料按「材料→段落」分页展示,支持目录快速定位、段落选择、复制,以及两种回填:把选中文字加引号追加到作答框(引用材料),或追加到提纲。它是从作答页跳出的二级页(navigateTo + EventChannel),跳转时带 insertMode=drafting 与 anchor 定位,回填通过事件通道把文本传回作答页。回填只改本机 draft(不走网络),且 draftLocked/draftAvailable 时不回填,避免覆盖已提交内容。
- **调用链**:essay-task openMaterialPage:676 -> wx.navigateTo({essay-material?taskVersionId=&planTaskId=&anchor=&insertMode=drafting, events:{quoteMaterial/addMaterialToOutline}}) -> essay-material/index.ts onLoad:33 -> loadWorkspace:42 -> api.getWorkspace -> materialDirectory + materialTextParagraphs 拆段 + targetAnchor scrollTo -> selectSegment:137 -> emitSelection:169 -> eventChannel.emit(quoteMaterial|addMaterialToOutline, {text}) -> navigateBackOr -> essay-task 收到 payload 后 appendDraftText 追加到 answerText/outlineText
- **关键文件**:
  - `apps/wechat/miniprogram/pages/essay-material/index.ts:42`
  - `apps/wechat/miniprogram/pages/essay-material/index.ts:137`
  - `apps/wechat/miniprogram/pages/essay-material/index.ts:169`
  - `apps/wechat/miniprogram/pages/essay-task/index.ts:676`
  - `apps/wechat/miniprogram/features/essay-task/material-paragraphs.ts:7`
  - `apps/wechat/miniprogram/features/essay-workbench/presentation.ts:17`
- **核心表**: `product.essay_material_bundle_versions`, `product.essay_material_segments`, `learning.essay_learning_artifact_versions`
- **易错点**:
  - 回填靠 EventChannel,若用户从「今日页」直接进素材库(非从作答页跳出)则 insertMode=false 且 channel 无监听,quote/addToOutline 会 toast 提示返回作答页重开。
  - anchor 形如 material-N(材料级)或 paragraphId(段落级),scrollTo 用 CSS selector,材料顺序改变会失效。
  - 素材页读的是 workspace(含 material_scope 裁剪),不是全量题库材料,材料顺序以 workspace.material.segments 为准。
- **关联**: 申论作答工作台、评审证据链(材料原文引证)

#### 6. 评审证据链

- **产品意图**:面向「为什么这么评」的可审计页:把 AI 批改的每个维度/要点/finding 逐条展示评分理由、你的原文引用、材料原文引用,并公开本次评分用的版本绑定(题干/材料/量表)。后端在落库前用 GroundedReviewValidator 做硬校验:所有 evidence 引用必须指向当前 answer_version 与 material_bundle 版本,quote 的 normalized offset 必须落在对应源段切片内、quote_sha256 必须与切片哈希一致,评审的 input/output fingerprint 与审计字段必须自洽——任何一条不满足就降级为 blocked(不出分数)。评阅结果带 generated_at/valid_until(30 天)与 supersedes_review_version_id 版本链,改答案/材料不会悄悄改变已产出的评阅。
- **调用链**:essay-evidence/index.ts onLoad:16 -> loadEvidence:23 -> api.getWorkspace(取 progression.reviewVersionId) + api.getGroundedReview(reviewVersionId) -> reviewView(essay-workbench/presentation.ts:64) -> wxml 渲染维度+引用;后端 grounding:review_service.apply:42 -> _parse_provider_output -> GroundedReviewValidator.validate:70 -> _validate_version_bindings:118 / _validate_rubric:169 / _validate_all_anchors:259 -> _validate_quote_anchor:324(offset+hash) / _validate_audit:368 -> 通过则 EssayReviewResponse(status=completed) 否则 _blocked(code=exc.code) -> workflow_repository.insert_review:565 落 grounding_report(jsonb)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/essay-evidence/index.ts:23`
  - `apps/wechat/miniprogram/pages/essay-evidence/index.wxml:9`
  - `apps/wechat/miniprogram/features/essay-workbench/presentation.ts:64`
  - `backend/app/domains/essay_intelligence/grounding.py:70`
  - `backend/app/domains/essay_intelligence/grounding.py:324`
  - `backend/app/domains/essay_intelligence/review_service.py:42`
  - `backend/app/domains/essay_intelligence/workflow_repository.py:565`
- **核心表**: `learning.essay_review_versions`, `learning.essay_answer_versions`, `product.essay_material_segments`, `product.essay_rubric_versions`
- **易错点**:
  - 评分引用强制 NFKC_whitespace_v1 归一化,quote 必须与「归一化后的源段切片」逐字一致且 sha256 匹配,否则 blocked;改材料文本/标点归一化规则会大面积失效。
  - evidence 页不重新拉批改,只读已落库的 review_version(immutable),regenerate 会生成 supersedes 新版本而不是覆盖旧版。
  - 前端 reviewView 里 '未定位到答案原句' 是合法展示,不代表数据错误;真正错误是 backend grounding 校验抛异常导致 blocked。
- **关联**: 智能批改、改写与转移、ai/governance(审计证据)

## 5. 岸岸 AI

**本簇概览**:岸岸 AI 簇是"一个入口 + 三重防线"的形态:anan 页是唯一提问入口,后端用「命令账本幂等 + 授权检索 + 只读动作」三道防线约束模型输出——LLM 只负责生成受约束的文本回答,所有写动作与导航都由服务端从用户原文用正则推导,模型建议被彻底降级为不可执行的参考。核心设计是 platform.companion_paid_commands 命令账本:claim/执行分两事务,execution_payload 以 dict 字符串键冻结全部输入,重放时按 key 原样恢复,quota 与 provider 幂等 key 都由同一 idempotency_key 派生。跨功能警告:provider.py 反向 import companion/agent 造成 provider↔companion 循环依赖;execution_payload 的 8 处字符串键契约散落在 service 与 commands 两个文件;ff93a0e48 之后 requests=() 死链路仍在 provider 解析 requestedActions 未删干净;外部错题的 confirm 接口前端至今没有页面调用,链路可能只通了后端一半。

#### 1. 岸岸对话主链路

- **产品意图**:用户在 anan 页输入问题,前端先把问题和上下文(路由、工作区、对象引用、learnerStateVersion)冻结成 CompanionAskRequest 存进本地 OperationAttemptStore(7 天 TTL),再用幂等 key 调 POST /api/v2/companion/ask。后端把请求、路由决策、上下文、检索包全部序列化进 execution_payload 冻结为命令账本行,以 pg_advisory_xact_lock 保证并发投递串行;真正的模型调用前单独开一个 NullPool 连接把 provider_attempt_count 落库(防止 provider 成功后应用事务回滚丢状态)。任何 5xx/超时/未知返回都会以同一 idempotency_key 重放,前端拿到已提交的回答而不是二次扣费。状态机:processing → succeeded/failed/provider_uncertain,terminal 后任何重放直接返回冻结的 response_payload,并校验 response_fingerprint。
- **调用链**:pages/anan/index.ts:470 ask() 构建 companionAskCommand -> OperationAttemptStore.begin(companion-ask scope, 生成幂等key) -> api/companion.ts:8 ask() -> api/client.ts 带 Idempotency-Key 头 -> POST /api/v2/companion/ask -> companion/router.py:104 ask_companion_v2 -> service.ask (service.py:145) -> route_global_question -> get_context -> AuthorizedHybridRetriever.retrieve -> _prepare_paid_command claim(commands.py:87, pg_advisory_xact_lock) -> lock_for_execution(commands.py:192) -> begin_provider_attempt 独立事务(commands.py:217) -> provider.answer_prepared_companion_question -> succeed(commands.py:254) 冻结 response_payload
- **关键文件**:
  - `apps/wechat/miniprogram/pages/anan/index.ts:470 ask()`
  - `apps/wechat/miniprogram/pages/anan/index.ts:674 askCompanionWithOutcomeRecovery(仅 408/5xx 用同 key 重放)`
  - `apps/wechat/miniprogram/pages/anan/index.ts:200 restoreUnfinishedCompleAttempt(provider_pending 恢复)`
  - `apps/wechat/miniprogram/platform/operation-attempt-store.ts:74 begin(幂等key生成)`
  - `apps/wechat/miniprogram/api/companion.ts:8 ask(timeoutMs 45s)`
  - `backend/app/domains/companion/router.py:100 ask_companion_v2`
  - `backend/app/domains/companion/service.py:145 ask / :249 _prepare_paid_command / :276 begin_provider_attempt`
  - `backend/app/domains/companion/commands.py:87 claim / :192 lock_for_execution / :217 begin_provider_attempt / :254 succeed`
- **核心表**: `platform.companion_paid_commands`, `ai_audit.model_call_ledger`, `ai_audit.interaction_logs`
- **易错点**:
  - execution_payload 是纯 dict 字符串键契约(contractVersion/request/routeDecision/providerRequest/context/learnerStateVersion/retrievalBundle 共 7 键),重放与校验逻辑散在 service.py:1324 _companion_provider_request 与 commands 两处
  - begin_provider_attempt 用独立 NullPool 引擎另开事务,该 checkpoint 必须先于 provider I/O 提交,否则 provider 成功+主事务回滚会丢状态
  - 前端严格区分 transport_unknown(statusCode=0,锁7天)与 HTTP 5xx(已结束的失败,不锁),只有完全无响应才走 provider_pending
- **关联**: 检索增强上下文、模型供应商与降级、动作提案与安全

#### 2. 检索增强上下文

- **产品意图**:岸岸回答前把用户的学习事实收集成一个 CompanionContextRecord(目标岗位、错题数、模考、学习档案、今日计划、申论证据、方法卡、称号),再交给 AuthorizedHybridRetriever 与用户显式引用的对象(题目/计划/岗位/报告)及私人资料合并打分:词面 Jaccard + 语义分组 + source_weight 加权,MMR 去重后选最多 8 条作为 retrievalBundle。每条来源必须有 display_summary(产品语言的一句话摘要,共 5 类 citation summary 函数),模型只能引用 bundle 内 sourceRef,越界引用会被判 provider_grounding_invalid 整次失败。返回的 citations 由 display_summary 呈现,前端再套一层防 JSON 泄露的 publicCitationSummary。
- **调用链**:service.ask:199 AuthorizedHybridRetriever.retrieve -> governance/retrieval.py:105 _authorize_object_refs(先校验对象归属) -> _explicit_object_candidates(题目/计划/岗位/报告取当前版本) + _projected_private_materials(向量投影) + _context_retrieval_candidates(5类上下文) -> 打分+MMR -> AIGovernanceRepository.record_retrieval_bundle 落库 -> _provider_sources -> provider -> _validate_provider_source_refs -> _selected_citations 生成 CompanionCitation
- **关键文件**:
  - `backend/app/domains/companion/service.py:1528 _context_retrieval_candidates(learner_activity/learning_brain/daily_plan/essay_learning/target_position/method_card)`
  - `backend/app/domains/companion/service.py:1442 _activity_citation_summary / :1459 _learning_brain_citation_summary / :1511 _target_position_citation_summary / :1826 _essay_citation_summary(5个citation摘要)`
  - `backend/app/domains/companion/service.py:1657 _validate_provider_source_refs(越界引用整次失败)`
  - `backend/app/domains/ai/governance/retrieval.py:105 retrieve / :185 _authorize_object_refs / :426 _explicit_object_candidates / :1123 _projected_private_materials / :1224 _mmr_select`
  - `backend/app/domains/companion/repository.py:77 get_context(约10段SQL汇总上下文)`
  - `apps/wechat/miniprogram/features/companion/presentation.ts:93 publicCitationSummary(防JSON泄露的兜底文案)`
  - `backend/app/domains/ai/governance/repository.py:231 record_retrieval_bundle`
- **核心表**: `learner.profiles`, `learner.practice_answers`, `learner.mock_exam_sessions`, `learning.learner_brain_profile_versions`, `learning.daily_plan_versions`, `learning.private_knowledge_chunks`, `learning.private_knowledge_source_versions`, `ai_audit.retrieval_bundle_events`
- **易错点**:
  - 5 类 citation summary 各自手写字符串,新增上下文类型不补 display_summary 时前端会降级到 fallback 文案
  - object_refs 授权先于检索,question 授权会额外查 active task session 的 scoring_bundle(做题中未提交的题目也算当前用户)
  - _build_persona_prompt(service.py:1419)开头 del context 但调用方仍在传 context,是半吊子解耦——persona 实际不读入参
- **关联**: 岸岸对话主链路、AI 治理与审计

#### 3. 模型供应商与降级

- **产品意图**:后端通过 AI_COMPANION_TRANSPORT 选择供应商:openai_compatible(默认,可接任意 OpenAI 兼容网关,production 强制 HTTPS+host 白名单)或 cloudbase_hunyuan(腾讯云托管混元云函数,用 X-Request-Id 做请求审计)。模型分 small/high 两档,按路由决策的 modelTier 选,deepseek-v4 前缀自动切到 sourceIndexes 索引式输出适配器(不让模型抄内部 sourceRef)。调用失败走三分支:uncertain(超时/网络/408/429/5xx)记 provider_uncertain 等稳定 key 重放,不锁死;terminal(AIProviderResponseError 例如返回非 JSON/模型漂移/越界引用)记 failed 给用户 502;metered 特例——provider 已返回 usage 但内容不可用,记账 succeed 以反映真实消耗。模型调用全部进 model_call_ledger,只存指纹与用量,不存内容。
- **调用链**:companion/router.py:68 get_companion_service -> hunyuan_companion.py:198 companion_provider 按 AI_COMPANION_TRANSPORT 选型 -> service.ask:281 answer_prepared_companion_question -> provider.py:375 校验+发请求+解析 -> 异常走 service.py:290-390 三分支 -> _provider_uncertain / _terminal_provider_failure / _record_metered_provider_failure -> model_call_ledger succeed/fail
- **关键文件**:
  - `backend/app/domains/ai/hunyuan_companion.py:198 companion_provider(transpoirt 分派)`
  - `backend/app/domains/ai/hunyuan_companion.py:30 HunyuanCompanionProvider(混元适配器,retryable->NetworkError)`
  - `backend/app/domains/ai/provider.py:220 prepare_companion_question(构造系统提示+json_schema)`
  - `backend/app/domains/ai/provider.py:375 answer_prepared_companion_question`
  - `backend/app/domains/ai/provider.py:677 _companion_output_adapter(deepseek-v4 -> sourceIndexes)`
  - `backend/app/domains/ai/provider.py:587 _validate_production_security(https+白名单+审计契约)`
  - `backend/app/domains/companion/service.py:290-390(uncertain/terminal/metered 三分支)`
  - `backend/app/domains/companion/service.py:1170 _provider_uncertain / :1103 _terminal_provider_failure`
  - `backend/app/domains/ai/provider_contract.py:1 parse_provider_json_object / validate_provider_audit_contract`
- **核心表**: `ai_audit.model_call_ledger`, `ai_audit.model_call_attributions`
- **易错点**:
  - 三分支语义关键:uncertain 不扣额度可重放、terminal 的 quota_consumed=True 会真扣、metered 有 usage 就记 ledger succeed(表里记录真实消耗)
  - deepseek 适配器把 sourceIndexes 经 sourceRefMap 映射回 sourceRefs,映射越界直接 ValueError
  - provider.py:29 import companion.agent 造成 ai/provider 反向依赖 companion 域(循环依赖隐患)
- **关联**: AI 治理与审计、岸岸对话主链路

#### 4. 动作提案与安全

- **产品意图**:模型在回答里可以请求动作,但系统把它当纯建议:provider 解析 requestedActions/next_actions 后,service 直接用 requests=() 丢弃,导航/预览动作完全由服务端从用户原文推导(正则解析'今天 X 小时'、周几模考、目标岗位等)。执行端是 BoundedToolRuntime:12 个工具全部 read_only=True(硬约束,写工具直接拒绝),受 max_tool_calls=3 预算与无进展预算限制,重复调用去重。容量/周节奏/目标岗位等带参提案必须与用户原文显式匹配,模型编造的值(如把当前计划时长当用户要求)会被 PermissionError 丢弃,最后兜底用 default_companion_actions 按 answerType 生成入口。前端 resolveCompanionAction 再校验一次 targetRef 的 URL 参数合法性(避免被注入任意跳转)。
- **调用链**:service.ask:409 execute_companion_actions(requests=() 直接丢弃模型建议) -> companion/agent.py:171 提取用户显式意图 -> agent.py:333 建 BoundedToolRuntime -> agent_runtime/runtime.py:56 execute(read_only/预算/去重检查) -> fallback_actions=_allowed_actions -> default_companion_actions(agent.py:360) -> CompanionAllowedAction -> 前端 features/learning-brain/companion-action.ts:35 resolveCompanionAction 二次校验 targetRef
- **关键文件**:
  - `backend/app/domains/companion/agent.py:171 execute_companion_actions(requests 参数收下但调用方传空)`
  - `backend/app/domains/companion/agent.py:92 COMPANION_AGENT_DEFINITION(12 只读工具)`
  - `backend/app/domains/companion/agent.py:360 default_companion_actions(fallback 按 answerType)`
  - `backend/app/domains/companion/agent.py:440 requested_plan_minutes / :502 requested_plan_focus / :524 requested_weekly_schedule(正则意图提取)`
  - `backend/app/domains/ai/agent_runtime/runtime.py:39 BoundedToolRuntime / :56 execute / :89 read_only 硬约束`
  - `backend/app/domains/ai/agent_runtime/contracts.py:1 AgentRuntimeViolation`
  - `apps/wechat/miniprogram/features/learning-brain/companion-action.ts:35 resolveCompanionAction(URL 参数白名单校验)`
  - `backend/app/domains/companion/service.py:409 execute_companion_actions 调用处`
- **核心表**: `platform.companion_paid_commands(metadata.agentToolEvents 审计)`, `ai_audit.interaction_logs(agentRequestedToolCount/agentSucceededToolCount)`
- **易错点**:
  - 死链路:provider.py:520 仍把 parsed.requested_actions 填进 AICompanionAnswerResult,但 service.py:409 传 requests=() 直接丢弃——ff93a0e48 该删没删干净,provider 还白解析
  - preview_daily_capacity 等带参工具必须与用户原文显式匹配,模型复制的计划时长会被 PermissionError 拒绝
  - 前端 resolveCompanionAction 的 allowedTargets 是 kind→route 白名单,adjust_plan_preview 还校验 source=anan 且参数只能来自 4 个固定 key
- **关联**: 岸岸对话主链路、检索增强上下文

#### 5. 外部错题问答

- **产品意图**:外部(非题库内)错题经 OCR/识别管线提取题干、选项与标签后,调 POST /api/v1/companion/external-questions/resolve 入库。后端不信任模型的自由 tag,而是把提取出的 module/tag 经 product.knowledge_map_releases 的当前发布版本 canonicalize 成知识点节点候选:图片题、提取置信度<0.70、候选为空或非唯一或置信度<0.75 全部 rejected。唯一命中且文本支持的进入 pending_confirmation,前端确认后再由 confirm 接口把它写成 learner.external_wrong_questions 正式错题,并标记 knowledge_release_id/node_id 溯源。确认后的错题进入 memory 域的工作台,成为复盘与后续计划的知识来源。整条走 feature=external_wrong_intake 的独立配额(非 companion_ask)。
- **调用链**:POST /api/v1/companion/external-questions/resolve -> router.py:136 -> service.resolve_external_question(service.py:618) -> _resolve_candidates(service.py:1266) 遍历 extracted_tags -> repository.resolve_candidate(repository.py:626) 命中 knowledge_map_releases+knowledge_map_nodes -> create_external_intake(repository.py:717) 写 learner.external_question_intakes -> 前端确认 -> POST /external-questions/{intake_id}/confirm -> service.confirm_external_question(service.py:1234) -> confirm_external_wrong_question(repository.py:849) 写 learner.external_wrong_questions -> memory/repository.py:632 工作台展示
- **关键文件**:
  - `backend/app/domains/companion/router.py:136 resolve_external_question`
  - `backend/app/domains/companion/router.py:165 confirm_external_question`
  - `backend/app/domains/companion/service.py:618 resolve_external_question(拒绝规则在:644-663)`
  - `backend/app/domains/companion/service.py:1266 _resolve_candidates`
  - `backend/app/domains/companion/service.py:1234 confirm_external_question`
  - `backend/app/domains/companion/repository.py:626 resolve_candidate / :717 create_external_intake / :849 confirm_external_wrong_question`
  - `backend/app/domains/memory/repository.py:632 _list_external_wrong_workbench_objects(外部错题进入记忆工作台)`
- **核心表**: `learner.external_question_intakes`, `learner.external_wrong_questions`, `product.knowledge_map_releases`, `product.knowledge_map_nodes`, `ai_audit.interaction_logs(feature=external_wrong_intake)`
- **易错点**:
  - 拒绝是设计意图:图片题、置信度不足、候选非唯一一律 rejected,绝不用模型自由 tag 入库
  - 前端现状:全 miniprogram 没有页面调用 /external-questions 接口(仅 types.gen 生成类型),resolve 通了后端但 confirm 的 UI 链路可能还没接线
  - 配额 feature 是 external_wrong_intake(独立于 companion_ask),从 capture-import 外部识别管线进来而非 anan 页
- **关联**: 检索增强上下文、知识域(记忆)

#### 6. AI 治理与审计

- **产品意图**:岸岸所有提问先过三层治理:页面权限(route→workspace/objectType 白名单)、提示注入检测(中英文正则)、意图路由(战略/岗位/错因/计划关键词+workspace 判 answerType 与 modelTier)。每次模型调用产生 AICallAttribution,记录 scenario、operation、respondingAgent、impactLevel、证据引用、retrieval_bundle_id、reservation_id 与策略版本;生产环境会要求该 provider/model/prompt/schema 组合有已通过评测的 scenario_deployments 记录,但岸岸特意 enforce_evaluated_deployment=False(不卡真实用户)。扣费走 commerce 域:先 reserve_feature_usage(幂等 reservation_key),成功调用后 consume,失败 refund;同时 ai_audit.interaction_logs 记录每次交互的成功/失败/uncertain 全量轨迹,model_call_ledger 按 key 记账到元。
- **调用链**:service.ask:155 route_global_question -> governance/routing.py:139 validate_page_permission+注入检测+关键词路由 -> catalog.py:26 scenario_policy 取模型档位 -> AuthorizedHybridRetriever -> governance/repository.py:290 attribution(生产走 :344 require_current_deployment,岸岸:1369 关掉) -> model_cost.py:134 begin(ledger+attribution) -> provider 调用 -> audit.py:124 log_interaction -> commerce/service.py:110 reserve_feature_usage -> 成功 consume / 失败 refund_feature_usage
- **关键文件**:
  - `backend/app/domains/ai/governance/routing.py:139 route_global_question / :123 validate_page_permission / :15 _PAGE_PERMISSIONS 页面白名单 / :47 注入正则`
  - `backend/app/domains/ai/governance/catalog.py:26 _POLICIES(场景→agent/tier/entitlement_feature)`
  - `backend/app/domains/ai/governance/contracts.py:83 IntentDecision / :115 AICallAttribution`
  - `backend/app/domains/ai/governance/repository.py:290 attribution / :344 require_current_deployment(生产发布门禁)`
  - `backend/app/domains/ai/audit.py:124 log_interaction / :32 reserve_quota(旧路径)`
  - `backend/app/domains/ai/model_cost.py:134 begin(含 attribution+processor receipt)`
  - `backend/app/domains/commerce/service.py:110 reserve_feature_usage / :187 refund / :198 consume`
  - `backend/app/domains/companion/service.py:1369 enforce_evaluated_deployment=False(岸岸豁免企业式评测发布门禁)`
- **核心表**: `ai_audit.scenario_deployments`, `ai_audit.release_evaluation_runs`, `ai_audit.retrieval_bundle_events`, `ai_audit.model_call_attributions`, `ai_audit.model_call_ledger`, `ai_audit.interaction_logs`, `commerce.entitlement_usage_daily`, `ai_audit.usage_daily(audit.py 旧路径)`
- **易错点**:
  - audit.py 的 reserve_quota/refund_quota(ai_audit.usage_daily)是遗留路径,实际扣费已迁到 commerce 的 reserve/consume/refund;改配额要在 commerce 侧
  - 契约版本字符串散落 8+ 处且已不一致:provider.py:32 用 companion-grounded@v7/grounded-answer@v3,而 catalog.py:32 各场景仍标 companion-grounded@v4/grounded-answer@v2——路由声明的版本和 provider 实际使用的版本对不上
  - production 下 AICallAttribution 走 require_current_deployment 需要评测通过记录,一旦发布门禁收紧,岸岸的 enforce_evaluated_deployment=False 豁免点(service.py:1369)是唯一放行口,误删会全线 503
- **关联**: 模型供应商与降级、岸岸对话主链路、commerce 域

## 6. 岗位 / 选岗

**本簇概览**:本簇是「岗位决策」产品面。前端统一在 pages/positions 下的 6 个页面(岗位工作台 index、search、detail、compare、goal、briefing)内完成,控制器层在 features/positions/(workspace/discovery/detail/compare/goal/briefing 六个 controller + contracts/presentation/state-store)。后端真正的实现落在 qualification 域(资格核验/规则编译/岗位发布),positions 域只保留发现与目标分计算,position_decision 域已高度边缘化(两个 v1 推荐接口被 410 下线)。每个岗位请求都绑定一个「岗位发布」:position_rule_releases + 报名窗口决定当前能看哪一年度哪一版数据,目标分区间、资格规则、进面分样本全部围绕 position_data_version 版本化,换版本即所有证据失效。跨功能警告:前端展示的「冲稳保/推荐」是分带(fit.band)+证据门控派生,而非 position_decision 域旧的概率模型,接盘者勿把两个引擎混为一谈;position_decision/goals.py 的 confirm_primary_goal 会同步写 learner.profiles 的 v1 兼容字段,是历史包袱。

#### 1. 岗位浏览/搜索/详情

- **产品意图**:用户从「岗位」Tab(工作台)进入,可看到按资格+偏好推荐的主目标与候选岗位;搜索页按年度/地区/城市/关键词组合查询;详情页展示岗位事实、资格逐条核验结果、目标分区间与个人分差。所有岗位读接口都绑定当前 active 的国考岗位发布(年度+数据版本),没有发布就整体不可用。旧 v1 接口(positions/router.py)已全部 410 下线,现由 /api/v2/qualification/positions 承接。
- **调用链**:pages/positions/index.ts:112 workspace.load() -> features/positions/workspace-controller.ts:143 getPositionWorkspace -> api/qualification.ts:61 GET /api/v2/positions/workspace -> qualification/router.py:200 discover_positions -> positions/discovery.py:215 PositionDiscoveryService.workspace -> _workspace_page:308(候选池200,资格+偏好排序) + _DISCOVERY_SQL:1043(单条大SQL算资格状态);搜索页 pages/positions/search/index.ts:97 discovery.search() -> api/qualification.ts:42 GET /api/v2/positions -> router.py:162 -> discovery.py:94 search;详情 pages/positions/detail/index.ts:77 -> detail-controller.ts:118 getPositionDetail+evaluatePosition 双发 -> discovery.py:680 detail + :755 fit -> qualification/application.py:99 evaluate_position -> qualification/service.py:16 evaluate_eligibility
- **关键文件**:
  - `apps/wechat/miniprogram/features/positions/workspace-controller.ts:143`
  - `apps/wechat/miniprogram/features/positions/discovery-controller.ts:124`
  - `apps/wechat/miniprogram/features/positions/detail-controller.ts:118`
  - `apps/wechat/miniprogram/api/qualification.ts:42`
  - `backend/app/domains/positions/discovery.py:215`
  - `backend/app/domains/positions/discovery.py:1043`
  - `backend/app/domains/qualification/router.py:158`
  - `backend/app/domains/qualification/service.py:16`
  - `backend/app/domains/qualification/repository.py:141`
  - `backend/app/domains/qualification/repository.py:509`
- **核心表**: `product.positions`, `product.position_snapshots`, `product.position_import_batches`, `product.position_rule_releases`, `product.position_release_windows`, `product.position_requirements`, `product.position_rule_compilations`, `learner.qualification_facts`, `learner.qualification_requirement_confirmations`, `learner.eligibility_snapshots`, `learner.profiles`
- **易错点**:
  - 岗位数据不是直接读 product.positions,而是『有 import_batch 时读 position_snapshots,否则回退 positions』(repository.py:777 _effective_position_source),写 SQL 时最容易漏这个 CTE
  - 所有岗位接口都走 active_release()(qualification/repository.py:141),报名窗口未开/已过都会 409 报 position_release_* 系列错误,联调前先确认当前窗口
  - 旧接口 positions/router.py 与 position_decision/router.py 的 /recommendations 全部 410 下线,别被 grep 到的手写 SQL 误导
  - 详情页 getPositionDetail 与 evaluatePosition 双发后必须在客户端校验 ruleSetVersion/positionDataVersion/learnerProfileVersion 一致,否则报 versionConflict(detail-controller.ts:123)
- **关联**: 选岗推荐引擎(workspace 排序/分带)、目标岗位与偏好、进面分数据资产

#### 2. 岗位对比

- **产品意图**:用户在列表/工作台勾选 2-3 个岗位(本地 storage 记住,最多 3 个),进入对比页并排展示每个岗位的资格状态、招录人数、目标分区间、分差与额外考试负担,服务端算对比亮点(可报/招录最多/目标分最低/是否主目标)。前端会把对比接口与每个岗位的 fit 接口双发并交叉校验版本,任一不一致就整页报版本冲突。
- **调用链**:pages/positions/compare/index.ts:58 new PositionCompareController -> features/positions/compare-controller.ts:88 Promise.all([comparePositions, ...getPositionFit]) -> api/qualification.ts:80 POST /api/v2/positions/compare + :92 GET /{id}/fit -> qualification/router.py:284 compare_positions + :312 get_position_fit_v2 -> positions/discovery.py:587 compare(2-3唯一,算highlights) + :755 fit(复用 detail)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/positions/compare/index.ts:58`
  - `apps/wechat/miniprogram/features/positions/compare-controller.ts:88`
  - `apps/wechat/miniprogram/features/positions/compare-controller.ts:164`
  - `apps/wechat/miniprogram/api/qualification.ts:80`
  - `backend/app/domains/positions/discovery.py:587`
  - `backend/app/domains/positions/discovery.py:755`
- **核心表**: `product.positions`, `product.position_snapshots`, `product.position_rule_releases`, `product.position_score_snapshots`, `learner.eligibility_snapshots`, `learning.ability_estimate_versions`
- **易错点**:
  - 对比接口限制 2-3 个唯一岗位(discovery.py:595),前端 validateSelection 只做客户端兜底,超出会 409
  - fit 接口的 targetScore 依据必须与对比接口同版本,前端 fitResponsesAlign(compare-controller.ts:164)校验任一 fit 版本不一致即整页报 versionConflict
  - 对比的「亮点」只有三个维度(招录最多/目标分最低/可报),不要期待服务端给综合评分
- **关联**: 岗位浏览/搜索/详情、选岗推荐引擎

#### 3. 选岗推荐引擎(能力缺口计算)

- **产品意图**:这是本簇核心决策逻辑,分两层:资格门控层把每个候选岗位的用户事实(学历/专业/政治面貌/基层年限/证书)与岗位编译规则逐条比对,得出 eligible/needs_confirmation/ineligible;分带层把用户当年双科能力估计(总分区间)与岗位历史目标分区间比较,输出冲/稳/保分带与具体分差(recommended 目标分-当前总分)。工作台按 资格状态->偏好地区->目标分证据级别->分带->分差 的复合键排序,主目标(已确认岗位)永远置顶;证据不齐时明确不伪造推荐(decision_buckets/preparation_cost/score_gap 三类能力缺口明示)。position_decision 域的 v1 概率推荐已下线,但 learner_target_score 历史同类岗位目标分引擎仍被 planning 域消费。
- **调用链**:qualification/application.py:99 evaluate_position -> qualification/service.py:16 evaluate_eligibility(逐 requirement 匹配算子);列表侧 discovery.py:1043 _DISCOVERY_SQL 内联 CTE 一次算 eligibility_status;分带 fit.py:96 evaluate_position_fit(ability vs target_score 区间,三态 band) -> 排序 fit.py:37 position_recommendation_sort_key -> 前端 presentation.ts:244 positionRecommendationPresentation 映射 recommended/eligible_evidence_pending/needs_confirmation 桶
- **关键文件**:
  - `backend/app/domains/positions/fit.py:96`
  - `backend/app/domains/positions/fit.py:37`
  - `backend/app/domains/positions/discovery.py:1043`
  - `backend/app/domains/positions/discovery.py:308`
  - `backend/app/domains/qualification/compiler.py:136`
  - `backend/app/domains/qualification/service.py:16`
  - `apps/wechat/miniprogram/features/positions/presentation.ts:244`
  - `backend/app/domains/ability_estimation/service.py:81`
  - `backend/app/domains/position_decision/learner_target_runtime.py:36`
- **核心表**: `product.position_requirements`, `product.position_rule_compilations`, `learner.qualification_facts`, `learner.qualification_requirement_confirmations`, `learning.score_evidence`, `learning.ability_estimate_versions`, `product.position_score_snapshots`
- **易错点**:
  - 资格核验有 SQL 内联版(_DISCOVERY_SQL)和 Python 版(service.py evaluate_eligibility)两套实现,语义必须一致,改一处要同步另一处
  - hard_condition=true 的 requirement 才参与门控,软条件不阻断;source_conditions_incomplete 会额外挂 pending,很多『莫名 needs_confirmation』来自这里(service.py:47)
  - 分带依赖当年同版本能力估计(exam_year 不匹配即 evidence_unavailable,fit.py:170),隔年成绩不会参与分差
  - evidence 不足时前端会降级为『只展示岗位事实』,不要在产品上承诺冲稳保会永远出现
- **关联**: 岗位浏览/搜索/详情、岗位对比、目标岗位与偏好、进面分数据资产

#### 4. 进面分数据资产(60K 真实岗位)

- **产品意图**:60,471 条真实国考岗位(2024-2026)与 15,387 条岗位级真实进面分的源头在仓库外 /Users/zxg/Documents/公考选岗计划(33MB gwy.db + target-position-engine TS adapter,含 sqlite/api/mock 三种 adapter 与 59 项契约自测)。接入仓库的路径是脚本 import:gwy.db -> product.positions/position_snapshots + position_entry_score_evidence(官方2025/2026进面分)与 position_score_histories(gwy 历史),再经 position_score_snapshots 固化。目标分区间由 repository 的 L1-L4 四级样本找 cohort(岗位自身>同系统同城同条件>同城>同系统同地区),service.py 用最新年均分+安全余量得出,evidence_level 分 direct/strong/limited/unavailable;另有 learner_target_score 引擎(参考同类岗位 cohort 算个人目标分)被 planning 域当 fallback 目标。
- **调用链**:gwy.db -> backend/app/importers/gwy_positions.py:36 import_gwy_positions -> product.positions/position_snapshots/position_score_links;官方进面分 xlsx -> backend/app/importers/guokao_entry_scores.py:45 -> position_entry_score_evidence/links -> 迁移 20260730_79 的 refresh_position_score_snapshots 固化到 product.position_score_snapshots;读侧 positions/repository.py:871 _target_score_basis_condition 按 L1-L4 找 cohort -> service.py:184 target_score_bases_for_positions -> target_scores.py:229 target_score_range_from_basis 生成区间
- **关键文件**:
  - `backend/app/importers/gwy_positions.py:36`
  - `backend/app/importers/guokao_entry_scores.py:45`
  - `backend/app/domains/positions/repository.py:777`
  - `backend/app/domains/positions/repository.py:871`
  - `backend/app/domains/positions/service.py:184`
  - `backend/app/domains/positions/target_scores.py:229`
  - `backend/migrations/versions/20260730_79_position_import_snapshots.py:140`
  - `backend/migrations/versions/20260730_79_position_import_snapshots.py:245`
  - `/Users/zxg/Documents/公考选岗计划/README.md`
  - `/Users/zxg/Documents/公考选岗计划/target-position-engine/src/factory.ts`
- **核心表**: `product.positions`, `product.position_snapshots`, `product.position_import_batches`, `product.position_score_snapshot_batches`, `product.position_score_snapshots`, `product.position_score_histories`, `product.position_score_links`, `product.position_entry_score_evidence`, `product.position_entry_score_links`
- **易错点**:
  - 仓库外的 gwy.db 只是上游资产,线上数据版本(import_batch 的 position_data_version/source_sha256)才是权威,导入时必须与迁移里 guard 的 immutable 触发器一致
  - position_score_snapshots 是 immutable 且只允许 select(20260730_82 authority hardening),运行时角色 gongkao_api 连 insert/update 都被 revoke
  - L1 直接证据要求 ≥2 个年份才有 basis(service.py:380),单一旧年份只能走 L2-L4 相似岗位,目标分可能『看着像猜的』——这是设计而非 bug
  - target_score_range 的 valid_until 绑定报名窗口 closes_at,窗口一关目标分自动过期(target_scores.py:127)
- **关联**: 选岗推荐引擎、目标岗位与偏好

#### 5. 目标岗位与偏好

- **产品意图**:用户先在 onboarding 建档时填岗位偏好(偏好 1-5 个地区、是否接受调剂、接受岗位级别,写 learner.position_preference_versions 带版本号),之后可在岗位详情把已确认可报的岗位设为「主报目标」。设目标走两段式:先 preview(生成含目标分+计划影响摘要的预览),再 confirm(校验预览未过期/版本一致后落库并发 position_goal.changed 事件),preview 是一次性且带过期时间。确认后 planning 域把该岗位目标分作为计划目标,换主目标会 invalidate 当日/本周计划与分差。工作台的「确认主目标」入口通过 onboarding result 的 nextAction 闭环。
- **调用链**:onboarding/position-preference/index.ts 提交 -> api/onboarding.ts:56 PUT /api/v2/onboarding/position-preference -> onboarding/router.py:289 -> progress_service.py:333 record_position_preference -> progress_repository.py:317 append_position_preference(版本化写 position_preference_versions);设目标 pages/positions/goal/index.ts:107 -> features/positions/goal-controller.ts:287 previewPrimaryGoal -> api/qualification.ts:138 POST /api/v2/position-goals/primary/preview -> qualification/router.py:524(校验 eligible snapshot+目标分) -> position_decision/goals.py:89 create_primary_goal_preview;确认 -> api/qualification.ts:129 confirmPrimaryGoal -> router.py:614 -> goals.py:315 confirm_primary_goal(写 position_goal_versions + position_goal_events + 同步 profiles 兼容字段 + outbox 事件)
- **关键文件**:
  - `apps/wechat/miniprogram/features/positions/goal-controller.ts:147`
  - `apps/wechat/miniprogram/features/positions/goal-controller.ts:235`
  - `apps/wechat/miniprogram/api/qualification.ts:129`
  - `apps/wechat/miniprogram/api/onboarding.ts:56`
  - `backend/app/domains/onboarding/router.py:289`
  - `backend/app/domains/onboarding/progress_repository.py:317`
  - `backend/app/domains/onboarding/progress_service.py:333`
  - `backend/app/domains/qualification/router.py:520`
  - `backend/app/domains/qualification/router.py:610`
  - `backend/app/domains/position_decision/goals.py:89`
  - `backend/app/domains/position_decision/goals.py:315`
- **核心表**: `learner.position_preference_versions`, `learner.position_goal_previews`, `learner.position_goal_versions`, `learner.position_goal_events`, `learner.current_primary_position_goals`, `learner.eligibility_snapshots`, `learner.profiles`, `product.position_rule_releases`, `product.position_release_windows`
- **易错点**:
  - preview 是一次性且 expires_at 短于目标分 valid_until,confirm 时服务端逐字段比对 preview 与 command(goals.py:362),preview 用过/过期/改目标分都必须重新 preview
  - confirm_primary_goal 里还顺手 update learner.profiles 的 target_exam/target_position_id/target_score 兼容字段(goals.py:538),这是 v1 遗留,别删但别依赖
  - 主目标身份不是查 position_goal_versions 直接判断,而是依赖视图 learner.current_primary_position_goals(20260730_82),它要求目标分 valid_until 恰好等于当前报名窗口 closes_at,换窗口期目标就『隐身』
  - 偏好版本号是乐观锁:expected_preference_version 不匹配即 409(position-preference),并发建档会踩
- **关联**: 选岗推荐引擎(分带与分差消费该目标分)、岗位对比、进面分数据资产、planning(今日计划以主目标分差为标尺)

## 7. 拍照搜题 / 导入 / 私人知识库

**本簇概览**:本簇以「外来学习材料」为主线,共有三条互相关联但独立的链路:(1) 小程序内拍照/相册/PDF 导入题目,走 external_ingestion 域的「上传→识别→题库匹配/AI 解析→用户核对」流水线;(2) 微信「发图搜题」通道(ilink_channel 域),用户在微信直接给岸岸发单题图片或文字指令,由 OpenILink Hub 收发消息并把事件转给后端处理;(3) 私人知识库(private_knowledge 域),讲义/笔记/PDF/Markdown 经 OCR 核对后切片,投影到 WeKnora 做混合检索,微信「存资料」可把已答复图片转入该库。三者共用底层上传对象存储(TOS)、recognition_revisions 识别版本、source_artifact_versions 资料版本与幂等命令回执机制;答题通道还复用控制面指令(今天/调整今天/查资料)读写计划与资料库。跨功能警告:xfyun 域只是语音+公式识别的辅助能力,拍照搜题主 OCR 并不在 xfyun;ilink_channel 涉及 control_plane/plan_projection/workflow 三模块,而 companion 的 resolve_external_question(外部错题记忆)是另一条独立入口,勿混为一谈。

#### 1. 拍照上传识别(capture-import 页 + xfyun OCR)

- **产品意图**:用户在「拍照搜题」页拍题、选相册图或选 PDF,先裁剪/编辑,再上传服务端。文件先过安全扫描与内容校验,之后进入外部识别管线:把图片/PDF 切成题干、选项、材料、申论作答要求等分区(question unit),逐段置信度展示给用户,低于阈值的段落强制人工修正。识别结果确认后才会进入题库匹配。真正的 OCR 由 external_ingestion/recognition 的多 provider 管线承担(Grok Vision / Gemma Vision / MinerU OCR / Paddle OCR / 私有协议),可选 xfyun 公式识别对数学公式做 LaTeX 富化;xfyun 域本身另提供语音转写/合成接口,但不承载拍题主 OCR。
- **关键文件**:
  - `apps/wechat/miniprogram/pages/capture-import/index.ts:183,256,298,401`
  - `apps/wechat/miniprogram/features/external-learning/capture-controller.ts:115,152,313,438`
  - `apps/wechat/miniprogram/api/external-learning.ts:76,83,126,134`
  - `apps/wechat/miniprogram/api/source-artifact.ts:75,99,113`
  - `backend/app/domains/external_ingestion/router.py:90,122,256,283,300,329`
  - `backend/app/domains/external_ingestion/workspace.py:68,151,246,490`
  - `backend/app/domains/external_ingestion/recognition/service.py:64,214,752`
  - `backend/app/domains/external_ingestion/recognition_runtime.py:39,175`
  - `backend/app/domains/external_ingestion/recognition/mineru_ocr.py:74,346`
  - `backend/app/domains/xfyun/formula.py:15,40,52`
- **易错点**:
  - xfyun 域只有语音(speech.py)+公式识别(formula.py),拍题主 OCR 走 external_ingestion/recognition 的多 provider 管线,别在 xfyun 里找拍题识别
  - 上传不是识别前置的充分条件:文件须通过安全扫描达到 recognition_eligible,否则 process 会 409(workspace.py:77)
  - 识别/匹配都是严格版本化(recognize 幂等键、revision_no、supersedes),重复提交同幂等键但内容不同会抛 RecognitionIdempotencyConflict
- **关联**: 答题通道(ilink_channel)同一识别管线在 resolver.py 复用、私人知识库识别采用同 recognition_revisions 体系(printed_ocr)

#### 2. 答题通道(ilink_channel 域,微信发图搜题)

- **产品意图**:用户在小程序生成 OpenILink Hub 授权二维码并绑定岸岸账户。绑定后直接在微信里给岸岸发裁剪好的单题图片或文字；Hub 负责微信凭据、收发与媒体会话，岸岸后端负责账户隔离、识别、题库匹配、AI、计划控制、短期记忆和回复决策。原直连长轮询 worker 已退役，临时私有图片由无消息权限的独立删除 worker 到期清理。
- **调用链**:微信消息 -> OpenILink Hub -> POST /api/v2/ilink-channel/hub/events -> hub_bridge.py:process_hub_event -> 图片走 ILinkPhotoWorkflow.process_hub_photo / PhotoQuestionResolver，文字走 ILinkControlPlane 或 AccountScopedCompanionResponder -> OpenILinkHubClient 回发；临时图片到期 -> messaging.ilink_object_deletion_queue -> app.workers.ilink_object_deletions。
- **关键文件**:
  - `backend/app/workers/ilink_object_deletions.py`
  - `backend/app/domains/ilink_channel/hub_bridge.py`
  - `backend/app/domains/ilink_channel/workflow.py:43,72,130,316,461`
  - `backend/app/domains/ilink_channel/control_plane.py:60,100,225,325,337`
  - `backend/app/domains/ilink_channel/plan_projection.py:19,27,54`
  - `backend/app/domains/ilink_channel/resolver.py:77,165,303,402,426,504,641`
  - `backend/app/domains/ilink_channel/service.py:42,72,154,278,345,361`
  - `backend/app/domains/ilink_channel/hub_service.py:40,64,139`
  - `backend/app/domains/ilink_channel/material_intake.py:44,62,76`
  - `backend/app/domains/ilink_channel/repository.py:330,654,855`
  - `apps/wechat/miniprogram/components/ilink-bot-sheet/index.ts:88,121,160`
- **核心表**: `messaging.ilink_channel_links`, `messaging.ilink_hub_bot_links`, `messaging.ilink_photo_queries`, `messaging.ilink_photo_collections`, `messaging.ilink_command_receipts`, `messaging.ilink_channel_events`, `messaging.ilink_object_deletion_queue`
- **易错点**:
  - ilink_channel 是三模块分工:control_plane(文本指令)、plan_projection(今日计划只读投影)、workflow(照片主流程),改一处要先弄清职责边界
  - 微信通道的查询/收录 answer_kind 与 collection_state 都不产生学习证据(abilityImpact=none_until_independent_attempt),统计口径勿把「查过的题」当「做过的题」
  - companion 的 resolve_external_question(/api/v1/companion/external-questions/resolve)是另一条「外部错题记忆」入口,与 ilink 拍题通道无关,前端当前并无调用方,只在生成 API 契约里存在
  - OpenILink Hub 是唯一消息运输入口；禁止恢复直连长轮询 worker 或旧加密 cursor 表
- **关联**: planning 计划域(control_plane 读写今日容量)、private_knowledge 域(存资料/查资料)、companion 岸岸(回复兜底)

#### 3. 答案采集入库(answer_ingestion / external_ingestion 边界)

- **产品意图**:针对线下打印的学习包(offline packet),用户完成纸质作答后把答案回录:可逐题手输答案串,也可拍答题卡图片做 OMR 识别自动填入。服务端生成候选答案版本,逐题校验(行测必须是 A-H 选项字母),可人工修订低置信项,确认后才把每条答案作为 external_answer_facts 事实写入,并发出 outbox 事件供学习大脑统计为「外部确认·低权重」证据。与 external_ingestion 的分工:external_ingestion 管题目导入(上传/识别/匹配/AI 解析),answer_ingestion 管答案回录,两者共享识别与上传基础设施,且确认答案时会联动确认 OMR 识别版本。
- **调用链**:pages/answer-ingestion/index.ts:132 createCandidate -> features/external-learning/answer-controller.ts:195 -> api/external-learning.ts:227 createAnswerString / :309 createAnswerImage(OMR: upload -> POST /recognitions answer_sheet_omr -> entries 生成) -> POST /api/v2/external-learning/answer-ingestions -> answer_ingestion/router.py:53 create_candidate -> service.py:57 create_candidate -> :181 correct_candidate -> :250 confirm_candidate(联动确认 OMR recognition router.py:107-156) -> repository.py:291 insert learning.external_answer_facts + service.py:352 outbox 事件 -> learning_brain/repository.py:129,337 消费为 external_answer_count
- **关键文件**:
  - `apps/wechat/miniprogram/pages/answer-ingestion/index.ts:55,106,132,150`
  - `apps/wechat/miniprogram/features/external-learning/answer-controller.ts:177,195,264,302`
  - `apps/wechat/miniprogram/api/external-learning.ts:227,309,326,340`
  - `backend/app/domains/answer_ingestion/router.py:53,79,107,178,192`
  - `backend/app/domains/answer_ingestion/service.py:57,181,250,407,509`
  - `backend/app/domains/answer_ingestion/repository.py:41,78,291`
  - `backend/app/domains/external_ingestion/answering/worker.py:40,61,138`
  - `backend/app/domains/external_ingestion/answering/service.py:52,174`
  - `backend/app/domains/learning_brain/repository.py:129,337`
- **核心表**: `learning.answer_ingestion_versions`, `learning.external_answer_facts`, `learning.offline_packet_versions`, `learning.packet_answer_bindings`, `learning.source_artifact_versions`, `learning.recognition_revisions`, `learning.external_ai_answer_versions`, `ai_audit.model_call_ledger`, `commerce 相关 feature_usage_reservation(photo_explain 额度)`
- **易错点**:
  - answer_ingestion(答案回录)与 external_ingestion(题目导入)是两个域,前者落 external_answer_facts,后者落 recognition/content_match,别混用
  - 答案串长度必须与学习包该科目题数一致,行测答案必须是有效选项字母(service.py:509 _validate_entries);OMR 模式仅支持行测客观题
  - 确认答案会联动确认 OMR 识别版本;识别未确认或低置信项未修订都会拒绝提交(service.py:300-319)
- **关联**: learning_packages 学习包域(离线卷生成)、learning_brain 能力画像(低权重证据)、xingce_learning 行测作答

#### 4. 私人知识库(private-knowledge 页与域,检索与问答)

- **产品意图**:用户在小程序「私有阅读室」上传讲义、笔记、PDF,或拍手写笔记,也可从微信通道「存资料」把已答复的讲义图转入。文件先做 printed_ocr 识别,用户逐段核对(低置信强制修正),确认后才把文本切片成 source/pages/chunks 入库并建立检索索引;Markdown 来源则直接解析 frontmatter。入库后异步投影到 WeKnora 知识库(每用户独立 knowledge base),支持混合检索返回带版本页码的引用片段。资料可导出 Markdown 再导入作为新版本(round-trip),删除走后台任务连远端投影和私有对象一并清理。微信端「查资料 关键词」复用了同一套检索。
- **调用链**:pages/private-knowledge/index.ts:264 uploadAndProcess -> api/private-knowledge.ts:82 upload / :90 recognizePrinted -> 逐段核对 :334 confirmReview -> correct/confirmRecognition -> :356 createSource -> POST /api/v2/private-knowledge/sources -> private_knowledge/router.py:72 -> service.py:57 create -> repository.py:156 insert_source + chunks -> projection.py:555 enqueue_source -> worker.py:47 index_source -> weknora.py:136 create_knowledge_base / :213 ingest -> 搜索: router.py:114 search -> service.py:171 search -> projection.py:653 search(hybrid_search weknora.py:246) -> citations;微信存资料 -> workflow.py:270 -> material_intake.py:76 store_latest 生成 PRINTED_OCR candidate;删除 -> router.py:152 -> repository.py:488 request_delete -> worker.py:56 DELETE_JOB_TYPE -> weknora.py:160 delete + storage 清理
- **关键文件**:
  - `apps/wechat/miniprogram/pages/private-knowledge/index.ts:159,264,334,435,474`
  - `apps/wechat/miniprogram/pages/private-knowledge/detail/index.ts:69,111`
  - `apps/wechat/miniprogram/api/private-knowledge.ts:82,90,116,125,138,146,154`
  - `backend/app/domains/private_knowledge/router.py:72,103,114,152`
  - `backend/app/domains/private_knowledge/service.py:57,168,171,192,197`
  - `backend/app/domains/private_knowledge/repository.py:156,320,488,645`
  - `backend/app/domains/private_knowledge/projection.py:43,541,562,653,708`
  - `backend/app/domains/private_knowledge/weknora.py:103,136,213,246`
  - `backend/app/domains/private_knowledge/worker.py:22,36,47`
  - `backend/app/domains/ilink_channel/material_intake.py:62,76,104`
- **核心表**: `learning.private_knowledge_source_versions`, `learning.private_knowledge_page_versions`, `learning.private_knowledge_chunks`, `learning.private_knowledge_search_index`, `learning.private_knowledge_commands`, `learning.private_knowledge_projection_spaces`, `learning.private_knowledge_chunk_projections`, `learning.source_artifact_versions`, `learning.recognition_revisions`, `privacy.private_material_deletion_requests`, `privacy.private_material_deletion_events`
- **易错点**:
  - 检索与索引是异步投影:source 确认后须经 WeKnora 建库/入库并 parse 完成才可搜,未就绪会 503 retryable(projection.py:670)
  - 删除是异步的(删除请求→后台任务→删远端投影+私有对象),前端轮询 deletions/{id},别假设同步生效
  - ilink「存资料」生成的 PRINTED_OCR candidate 提示去小程序核对,但 private-knowledge 页只 review 本会话内自己创建的 revision,当前没有 pending 候选列表 API,闭环性需要人工确认
  - Markdown 版本更新用 frontmatter 记录 anan_source_id/version/content_sha256 做 round-trip 校验,外改元数据会导致 409(service.py:256)
- **关联**: 答题通道(存资料/查资料入口)、learning_media 素材域、data_rights 隐私删除

## 8. 商业 / 会员 / 社区

**本簇概览**:本簇是"钱与贡献"的闭环：两条支付轨（App Store IAP 遗留 + stage8 微信虚拟支付 xpay）把付款变成权益 entitlement，权益以 AI 配额（reserve/consume）形式被各业务域消耗；社区方法卡把用户"学习方法"沉淀为结构化卡片，经过 AI 候选生成、五维审核、曝光试用、反馈投递、延迟验证，逐步达成社区验证里程碑；里程碑事件经 RewardMilestoneSink 推入 community_rewards 判定发放 AI 配额或会员时长，AI 配额再回流到 reserve/consume 体系消费。跨功能警告：本项目处于 free_cold_start 冷启动，商业开关在 config commercial_mode，代码里全是"paid 才开闸"的分支；community_reward_awards 是唯一一张被两个域（community_rewards 发放、commerce 消费/激活）共同写操作的真双写表，修改任何一边都要同时核对另一边；wechat_pay 的普通 JSAPI 已退役（deps 里 _RetiredMiniProgramJsapiGateway 直接抛错），stage8 主走虚拟支付。

#### 1. 会员与权益（entitlement + AI 配额 reserve/consume）

- **产品意图**:产品以"Pro 会员 + 每日功能配额"定义付费价值：免费/Pro 两档，付费只解锁更高的配额上限（如岸岸 AI 问答免费 3 次/天、Pro 60 次/天），不隔离功能本身。任何 AI 功能调用前都要先 reserve 一笔配额（reservation），业务真正完成后 consume 落账，失败则 refund 归还，保证"未真正消费不扣数"。配额按日/月滚动。配额有三种来源通道：免费/Pro 的 catalog 通道（entitlement_usage_daily + feature_usage_reservations）、社区奖励通道（community_reward_awards 里的 ai_quota 额度，经 reserve_ai_usage 占用后消费）、冷启动 unlimited 模式（不做真正限制）。
- **调用链**:岸岸问答 pages/anan（前端）-> POST /api/v1/companion/ask -> companion/router.py -> companion/service.py:831 reserve_feature_usage(feature=companion_ask, commit=false) -> commerce/service.py:110 -> repository.py:280 pg_advisory_xact_lock + upsert entitlement_usage_daily -> 生成 feature_usage_reservations 行；模型调用成功 -> companion/service.py:401 consume_feature_usage；失败 -> :1056 refund_feature_usage
- **关键文件**:
  - `backend/app/domains/commerce/service.py:81 get_entitlement、:110 reserve_feature_usage、:198 consume_feature_usage、:187 refund_feature_usage`
  - `backend/app/domains/commerce/repository.py:149 get_entitlement（合并 catalog/reward/grant 三来源）、:280 reserve_feature_usage、:326 事务级 advisory lock、:522 reserve_reward_feature_usage、:660 consume、:564 refund`
  - `backend/app/domains/commerce/catalog.py:27 PRODUCTS、:60 FEATURE_QUOTAS（7 个功能的 free/pro 限额）`
  - `backend/app/domains/commerce/reward_entitlements.py:89 reserve_ai_usage、:237 consume_ai_usage、:223 release_ai_usage、:483 active_membership_period`
  - `backend/app/domains/companion/service.py:831 reserve（commit=false）、:401/:752 consume、:1056 refund`
  - `backend/app/domains/xingce_learning/cause_agent/service.py:116 reserve xingce_cause_analysis、worker.py:1003-1106 consume/refund`
  - `apps/wechat/miniprogram/pages/membership/index.ts:84 loadDashboard 读 /api/v2/commerce/dashboard`
  - `backend/app/domains/ai/governance/catalog.py:26-137 各场景场景 entitlement_feature 映射（companion_ask/xingce_cause_analysis/essay_review/external_wrong_intake/photo_explain）`
- **核心表**: `commerce.entitlement_usage_daily`, `commerce.feature_usage_reservations`, `commerce.user_entitlements`, `commerce.entitlement_grants`, `commerce.entitlement_audit_logs`, `commerce.community_reward_awards`, `commerce.community_reward_ai_usage_reservations`
- **易错点**:
  - reserve 时 commit=false 与最终 consume/refund 必须配对，reservation 状态机 reserved->consumed/refunded，已 consumed 不能 refund，否则抛 CommerceQuotaRefundError；reservation_key 同时是幂等键，同一业务重放会命中同一行，绝不重复扣数。
  - get_entitlement 会把 App Store 订阅、奖励会员激活、entitlement_grants 三来源合并成 tier=pro，判断 is_active 只看 subscription_end > now，到期瞬间自动掉回 free。
  - 冷启动模式下 usage_access_mode=unlimited，reserve 仍写 entitlement_usage_daily 但 limit 放大，前端 entitlementLabel 显示"冷启动免费"，不要以为没人在计数。
- **关联**: 支付（两条轨）、社区奖励发放的 ai_quota 回流消费

#### 2. 支付（legacy IAP + stage8 微信虚拟支付 xpay）

- **产品意图**:支付分两条轨：v1 是 iOS App Store IAP（app_store.py 校验签名交易，router.py /api/v1/commerce/app-store/*），v2 是 stage8 的微信虚拟支付 xpay（小程序内 wx.requestVirtualPayment 拉起，服务端 prepare/query/refund，事件回调 AES 加解密）。当前生产主轨是 xpay，v1 只在付费开关 paid 时用于 iOS 恢复/通知。两轨共享同一套权益落库（apply_paid_order -> entitlement_grants）。订单是幂等命令（command_key+request_fingerprint 防重放），支付结果以主动 reconcile（前端轮询 + 服务端定时任务）为准，回调仅作辅助。退款规则全部硬编码在 stage8_repository/stage8_service：refund_rate>=0.20 暂停招募、>=0.15 需人工复核、30 天窗口。
- **调用链**:membership 页 buy() -> api/commerce.ts createVirtualPaymentOrder -> POST /api/v2/commerce/virtual-payment/orders -> stage8_router.py:88 -> wechat_virtual_payment.py:637 create_order（校验政策接受+额度+openid）-> stage8_repository.py:297 create_order_and_reserve_slot 建单 -> 前端 wx.requestVirtualPayment 拉起支付 -> 前端 reconcileUntilSettled 轮询 POST /orders/{id}/reconcile -> wechat_virtual_payment.py:917 _reconcile_order 查 xpay query_order 按 status 对账 -> apply_paid_order 发 entitlement_grants
- **关键文件**:
  - `backend/app/domains/commerce/wechat_virtual_payment.py:262 WechatVirtualPaymentClient（prepare_goods_payment :294/query_order :335/refund_order :366/notify_goods_provided :403）、:488 WechatMessageCrypto AES 加解密、:917 _reconcile_order、:935-982 XPAY status {2,3,4,5,7,8} 状态机、:1054 _validate_provider_order（iOS order_type {7,8}）`
  - `backend/app/domains/commerce/stage8_service.py:102 create_order（JSAPI 轨）、:542 _refund_eligibility（30 天窗口）、:641 _reconcile_process_rewards、:424 _offer_view（recruitment_state：0.20 暂停/0.15 复核）`
  - `backend/app/domains/commerce/stage8_repository.py:297 create_order_and_reserve_slot（advisory lock+cohort 占位）、:337 refund_rate>=0.20 暂停、:609 apply_paid_order 发权益、:679 create_refund、:993 reward_facts`
  - `backend/app/domains/commerce/wechat_pay.py:111 WechatPayApiV3Client 纯协议样板（直连商户 APIv3 签名/验签/加密）、deps.py:99 _RetiredMiniProgramJsapiGateway 已退役`
  - `backend/app/domains/commerce/app_store.py:52 AppStoreSignedTransactionVerifier JWS 验签样板（纯协议无业务）`
  - `backend/app/domains/commerce/router.py:32/47/86 v1 App Store 三端点、stage8_router.py:223/235 xpay 事件回调 GET 验签+POST 解密`
  - `backend/app/domains/commerce/stage8_service.py:749（wechat_virtual_payment.py:749 request_refund：iOS 转 App Store reportaproblem.apple.com 人工退款，非 iOS 走 xpay refund_order）`
  - `apps/wechat/miniprogram/pages/membership/index.ts:189 buy、:236 reconcileUntilSettled、:257 requestRefund；apps/wechat/miniprogram/api/commerce.ts:108 requestWechatVirtualPayment`
- **核心表**: `commerce.wechat_orders`, `commerce.wechat_refunds`, `commerce.wechat_payment_notifications`, `commerce.founder_cohort_slots`, `commerce.product_offer_versions`, `commerce.commercial_policy_versions`, `commerce.app_store_transactions`, `commerce.app_store_notification_events`, `commerce.entitlement_grants`, `commerce.weekly_reflection_confirmations`
- **易错点**:
  - XPAY 状态数字 {2,3,4,5,6,7,8} 与 iOS order_type {7,8} 硬编码在 wechat_virtual_payment.py:935-1079，改业务前必须先对齐微信官方文档，顺序搞错会错误标记已支付或误判渠道。
  - 普通微信 JSAPI 支付已退役：deps.py:99 的 _RetiredMiniProgramJsapiGateway 所有方法直接抛 WechatPayConfigurationError，别再用 stage8_service.create_order 的 JSAPI 分支做新接入。
  - 退款有 process_rewards 抵扣逻辑：已返的每周复盘现金奖励（refund_kind=weekly_reflection_reward）会计入累计退款，退款资格计算 remaining=amount-refunded_minor 会把它减掉，容易误以为多退。
- **关联**: 会员与权益（apply_paid_order 后 get_entitlement 立即生效）、学习过程奖励（买后 20 有效日/4 周复盘/核心任务达成发奖）

#### 3. 商品目录（catalog、ProductConfig 与 ProductRecord）

- **产品意图**:商品目录分两层：代码里的 ProductConfig（catalog.py 硬编码 2 个 SKU：gongkao_ai_pro_monthly/yearly + 7 项 FEATURE_QUOTAS）和数据库表 commerce.iap_products（sync_product_catalog 每次请求 upsert，把代码配置同步成 active 行）。v1 老目录直接读 iap_products 给 IAP 用；stage8 目录以 commerce.product_offer_versions 为 offer 层（product_offer_versions 是版本化商品/offer 快照，含 amount_minor、service_days、cohort、退款率、四份政策版本号），offer 才真正决定价格与招募状态。价格不带真金白银，只有 price_band 档位（CNY_29_39/CNY_199_299）；金额统一用分（amount_minor）。
- **调用链**:GET /api/v1/commerce/catalog -> service.py:60 get_catalog -> sync_product_catalog() upsert commerce.iap_products -> list_products 读 active iOS 行；GET /api/v2/commerce/catalog -> stage8_service.py:85 catalog() -> stage8_repository.py:125 list_active_offers（读 product_offer_versions + founder_cohort_slots 实时计数）-> _offer_view 计算 recruitment_state
- **关键文件**:
  - `backend/app/domains/commerce/catalog.py:7 ProductConfig、:27 PRODUCTS（2 SKU）、:60 FEATURE_QUOTAS（7 功能配额）`
  - `backend/app/domains/commerce/repository.py:95 sync_product_catalog、:103 list_products、:124 get_product、:1184 _upsert_product（写 commerce.iap_products）`
  - `backend/app/domains/commerce/stage8_repository.py:24 OfferRecord、:125 list_active_offers、:163 offer_by_id、:202 policies（读 commercial_policy_versions）、:1391 _offer`
  - `backend/app/domains/commerce/stage8_service.py:424 _offer_view（cohort/refund_rate -> recruitment_state）`
  - `backend/app/domains/commerce/stage8_schemas.py:25-38 WechatOffer（offerCode/amountMinor/serviceDays/cohortRemaining/recruitmentState）`
- **核心表**: `commerce.iap_products`, `commerce.product_offer_versions`, `commerce.commercial_policy_versions`, `commerce.founder_cohort_slots`
- **易错点**:
  - 改商品价格/权益不能只改 catalog.py，还需同步发布 product_offer_versions 新版本（offer 是版本化的），否则目录读到了新 SKU 但下单校验用旧 offer 会冲突。
  - iap_products 由 sync_product_catalog 每次请求强同步覆盖，别再手改表；product_offer_versions 的 amount_minor 单位是分，前端 amountLabel 里 /100 才显示元。
  - offer 的 recruitment_state 是动态算的（refund_rate>=0.20 暂停、>=0.15 复核、cohort_remaining==0 售罄），不是库存字段，别去改表里的状态列。
- **关联**: 支付（下单前校验 expected_amount_minor == offer.amount_minor）、会员与权益

#### 4. 社区方法卡（创作/审核/曝光/试用/反馈）

- **产品意图**:让学员把"自己的学习方法"沉淀为结构化卡片（识别-步骤-适用/排除-坑），并让其他人可验证。创作链路：从文本/语音转写/手写提取/反思作文四种来源建私密草稿 -> 后端调 AI 候选生成器产出结构化候选 -> 作者确认新版本 -> 送审。审核是五维检查（重复/版权/安全/知识/答案一致性），任何一维 fail 即拒绝、needs_revision 即要求修改、全过进入试用态。曝光：学习者 discover 按个性化排序看到试用态卡片，opt-in 记录 exposure；试用：学习者用独立新题作答，后端核对 canonical attempt（作者与学习者不得同设备/同题），记录正确率前后、可延时验证；反馈：一次试用一个有效投票（helpful/not_helpful）。所有生效口径都写死进 published 政策资源（candidate_generation/automated_review/effect_policy 等），policy 版本不匹配即报错。
- **调用链**:community-draft 页 -> POST /api/v2/community-method-cards/drafts -> service.py:173 create_draft（稳定 ID+advisory lock+source binding）-> generate_candidate :249 -> candidate_provider.py:208 HTTP 调 AI -> confirm_candidate :273 -> submit_for_review :346 -> moderation.py:60 FiveDimensionReviewEngine 五维判定 -> 试用态 -> discover :997 个性化排序 -> record_exposure :431 -> complete_trial :490（canonical attempt 核对+actor cluster 反作弊+eligibility）-> record_feedback :646 一次投票 -> record_delayed_verification :698 到期后独立新题验证 -> _refresh_projection :1289 重算 wilson 置信分数 -> _append_milestone :1366 触发里程碑
- **关键文件**:
  - `backend/app/domains/community_method_cards/service.py:173 create_draft、:249 generate_candidate、:273 confirm_candidate、:346 submit_for_review、:431 record_exposure、:490 complete_trial、:646 record_feedback、:698 record_delayed_verification、:760 report、:827 decide_report、:997 discover、:1289 _refresh_projection、:1366 _append_milestone`
  - `backend/app/domains/community_method_cards/moderation.py:60 FiveDimensionReviewEngine、:112 VersionedFixedReportPolicy（举报分类即刻限制）`
  - `backend/app/domains/community_method_cards/effects.py:263 calculate_projection（wilson 下界三权重 -> conservative_score/validated）、:184 VersionedTrialEligibility（独立性/新题/基线校验）、:323 personalized_rank_score`
  - `backend/app/domains/community_method_cards/repository.py:552 insert_milestone、:611 insert_exposure、:663 insert_trial、:1017 discovery_rows（只看 trial/community_validated+rights 公开）、:1048 public_detail`
  - `backend/app/domains/community_method_cards/composition.py:1031 RewardMilestoneSink（append -> CommunityRewardService.evaluate_milestone）、:1039 build_community_method_card_service 装配`
  - `backend/app/domains/community_method_cards/candidate_provider.py:208 generate（AI 候选 HTTP provider）`
  - `apps/wechat/miniprogram/pages/community-draft/index.ts、community-structure/index.ts、community-trial/index.ts、community-contributions/index.ts`
- **核心表**: `product.community_method_cards`, `product.community_method_card_versions`, `product.community_method_card_source_bindings`, `product.community_method_card_candidates`, `product.community_method_card_validation_bindings`, `product.community_method_card_reviews`, `product.community_method_card_review_checks`, `product.community_method_card_milestone_events`, `product.community_method_card_exposures`, `product.community_method_card_trials`, `product.community_method_card_trial_session_evidence`, `product.community_method_card_eligibility_decisions`, `product.community_method_card_delayed_verifications`, `product.community_method_card_votes`, `product.community_method_card_reports`, `product.community_method_card_report_decisions`, `product.community_method_card_effect_projections`
- **易错点**:
  - 全部状态都从 published 政策资源读且做版本比对（review_policy_mismatch/effect_policy_unavailable），上线新策略必须先在表里发布对应 policy 行，否则服务启动即 503。
  - complete_trial 的 canonical attempt 核对 + actor_cluster 反作弊（作者与学习者同设备/同题会排除），新增题目来源时这些 Authority 都要同步支持，漏了会 trial 全被排除。
  - 版本是追加式（source->confirmed 每版 version_no+1），曝光/试用都绑定 card.head_version_id，卡片更新后旧版本不可再试用，易把"看不到旧卡"当成 bug。
- **关联**: 社区奖励（milestone 事件 -> evaluate_milestone 发奖）、Feed（task.kind=method_card 会推今日卡片入口）

#### 5. 社区奖励（community_rewards 里程碑判定/发放/消耗）

- **产品意图**:给方法卡作者按里程碑发奖励：方法卡每达成一个里程碑（automated_review_passed、independent_evidence_sufficient、conservative_effectiveness_reached、report_confirmed）就由 RewardMilestoneSink 把事件推进 community_rewards，按已发布政策（reward_unit=ai_quota 或 membership_seconds）判定发奖。发放要过四层 cap（author/card/milestone/global，按日/月/活动周期），政策全文 hash 到 rules_sha256 防篡改，事件幂等（同一 version+milestone 只发一次）。奖励落 ledger（grant/consume/reversal/expire），余额=sum(unused_value)-已预留。AI 配额由 commerce.reward_entitlements 从 award 里划扣消费，会员时长经 activate_membership_award 转换成 user_entitlements 的实际订阅期。
- **调用链**:community_method_cards _append_milestone :1366 -> composition.py:1035 RewardMilestoneSink.append -> CommunityRewardService.evaluate_milestone :81 -> SqlCommunityRewardRepository.evaluate_event :457（advisory lock + 四层 cap bucket 检查 + 幂等）-> _insert_award 落 community_reward_awards + grant ledger -> 前端 community-contributions 读 GET /api/v2/community-rewards/ledger -> repository.py:791 ledger_summary；AI 配额消费：commerce 侧 reserve_feature_usage 命中额度用尽 -> repository.py:522 reserve_reward_feature_usage -> reward_entitlements.py:89 reserve_ai_usage 从 award 划扣 -> :237 consume_ai_usage -> :508 _transition_ai_usage 落 reservation_events；会员激活：service.py:163 activate_reward_membership -> reward_entitlements.py:309 activate_membership_award（读 purchase_end+最新激活 -> activated_until 顺延 -> 写 user_entitlements 期间）
- **关键文件**:
  - `backend/app/domains/community_rewards/repository.py:457 evaluate_event（cap 判定核心）、:651 _change_lot（consume/reversal 状态机）、:791 ledger_summary（ai_quota 余额=unused-reserved）、:1045 _insert_award、:1086 _insert_ledger_entry、:1119 _period_start、:1136 _cap_bucket_specs`
  - `backend/app/domains/community_rewards/service.py:61 publish_policy、:81 evaluate_milestone、:95 consume_from_award、:24 reward_policy_rules_sha256（政策全文 hash）`
  - `backend/app/domains/community_rewards/models.py:9 里程碑枚举、:16 RewardUnit、:35 CommunityMilestoneEvent、:78 CommunityRewardAward`
  - `backend/app/domains/community_method_cards/composition.py:1031 RewardMilestoneSink（跨域接线点）`
  - `backend/app/domains/commerce/reward_entitlements.py:89 reserve_ai_usage（scope join + 预留占用）、:309 activate_membership_award（会员激活）、:483 active_membership_period（被 get_entitlement 读入）`
  - `backend/app/domains/commerce/service.py:163 activate_reward_membership、repository.py:847 activate_reward_membership`
  - `apps/wechat/miniprogram/pages/community-contributions/index.ts:135 loadContributions（读 ledger + 展示 aiQuotaBalance/membershipBalance）`
- **核心表**: `commerce.community_reward_policies`, `commerce.community_reward_policy_scopes`, `commerce.community_reward_cap_buckets`, `commerce.community_reward_awards`, `commerce.community_reward_ledger_entries`, `commerce.community_reward_ai_usage_reservations`, `commerce.community_reward_ai_usage_reservation_events`, `commerce.community_reward_membership_activations`, `product.community_method_card_milestone_events`, `commerce.entitlement_audit_logs`
- **易错点**:
  - community_reward_awards 是全项目唯一真双写表：community_rewards/repository.py 发奖写、commerce/reward_entitlements.py 消费和激活也写（update consumed_value），两边都对同一行做 for update，改一边必须同步核对另一边，否则余量对不上。
  - ai_quota 余额要减已预留（ledger_summary 里 outstanding），reserve 后未 consume/release 的额度在余额上不可见，容易误报余额比实际多。
  - 没有已发布政策时 evaluate_event 返回 no_policy 且记 terminal evaluation，前端显示"无政策不会显示虚构奖励"；rules_sha256 必须覆盖全部经济条款，发布时只改 cap 不改 hash 会直接 PolicyError。
- **关联**: 社区方法卡（事件来源）、会员与权益（ai_quota 回流 reserve/consume、membership_seconds 回流订阅期）

## 9. 我的 / 账户 / 合规

**本簇概览**:本簇把「我是谁、我的数据、我怎么被记住、我怎么被联系」收拢在一起:me 页是账号与档案的聚合入口(顺带承担资料编辑、会话管理、多级登出),privacy 页承载数据导出与账号删除两个后台作业驱动的合规流程,learning_brain/memory 负责把用户画像、能力证据、策略决策落库成可回溯的记忆,coach_notifications 是唯一主动触达用户的微信订阅消息通道。跨功能警告:data_rights 的导出清单(build_export_payload 37 段)与删除清单(_erase_personal_content 51 条)都是手工 SQL,新增任何个人数据表必须同时改两处,漏一处就是「能导出但不能删除」的残留;deletion_pending 状态散布在 7+ 个文件各自判断,没有唯一属主,改状态机要同步所有点;me 页与 profile/archive 页重复聚合同一组 API(两个页面各维护一份 Promise.allSettled 契约),新增档案字段要改两处。

#### 1. 我的聚合页(me 页)

- **产品意图**:「我的」Tab 是账号与备考档案的总入口:上半部展示账户概览(账户ID、微信绑定、建档日、备考第几天、头像昵称、设备会话列表、注销/登出),下半部按三段聚合档案卡片——报考资料(学历专业/应届身份/政治面貌/户籍/工作经历/基层项目)、学习时间(每周节奏/长块时长)、目标岗位(从今日计划 goalGap 取岗位名与招录机关)。加载策略是分两波 Promise.allSettled:第一波拉资格档案+建档进度,进度已就绪才发第二波拉学习偏好+今日计划,任何一支失败都各自降级为「待补充/重试」而不阻塞整页;已渲染过内容时切回 Tab 只静默刷新避免闪骨架屏。同一组 API 在 profile/archive(备考档案)页被再次聚合成「档案完整度」五模块视图,两份契约各自维护。
- **调用链**:pages/me/index.ts:148 onShow -> loadAccount(164)->AccountApi.getOverview(GET /api/v2/auth/account, auth/router.py:192) + loadProfileContext(211)->Promise.allSettled[QualificationApi.getProfile, OnboardingApi.getProgress](227)->studyPreferenceReady 时第二波 Promise.allSettled[getStudyPreference, PlanningApi.getToday](292);profile/archive/index.ts:61 loadArchive 重复聚合 getOverview+getProgress+getProfile+LearningBrainApi.getProfile(71)->再按 progress 条件拉 experience/positionPreference/studyPreference/abilityBaseline/planning(79-98)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/me/index.ts:148-204`
  - `apps/wechat/miniprogram/pages/me/index.ts:211-342`
  - `apps/wechat/miniprogram/pages/me/index.ts:595-616(finishLogout 清全部 pending-store)`
  - `apps/wechat/miniprogram/pages/profile/archive/index.ts:61-135`
  - `apps/wechat/miniprogram/pages/profile/archive/index.ts:166-241(buildModules 五模块完整度)`
  - `backend/app/domains/auth/router.py:192-230(get_account_overview+活跃会话)`
  - `apps/wechat/miniprogram/api/account.ts:14-15`
- **核心表**: `learner.users`, `learner.app_sessions`
- **易错点**:
  - me 页(227)与 archive 页(71)各维护一份聚合契约,新增域字段要同步改两处且字段名靠手写对齐
  - 第二波请求(me 292 / archive 81-95)都依赖 getProgress 的 studyPreferenceReady/experienceProfileVersion 等字段,这些布尔一变化前端请求树就变,契约脆弱
  - onShow 每次切回都全量刷新,me 页实际是 5 个请求/次,静默刷新只做降级文案不减少请求数
- **关联**: 能力页 ability(学习大脑消费方)、报考资格 qualification 域、学习时间 study-preference 域、目标岗位 planning 域

#### 2. 数据导出

- **产品意图**:合规的数据可携带权:用户在隐私页点「导出个人数据」,后端收到带幂等 key 的请求后只落一条 queued 记录并入队后台作业,worker 随后把账户相关的约 30+ 张表(档案、成绩、收藏、错因、私有素材、报告、AI 调用账本、通知偏好、订单等)逐段手工 SQL 汇总成一个 JSONB payload,算 sha256 存进导出表并标记 completed;前端轮询状态,完成后拉取 payload 写成本地 .txt 并用 wx.shareFileMessage 让用户转发保存。导出文件有 1 小时到 7 天的有效期,过期自动清理。前端对不确定的请求结果用 pending-store 保留幂等 key,重进页面自动恢复。
- **调用链**:pages/privacy/index.ts:160 createExport -> POST /api/v2/data-rights/exports(data_rights/router.py:51)->service.create_export->repository.create_export(164) 按 command_key 幂等+入队 personal_data_export_v1+落 privacy.data_export_requests('queued')->worker.process_next(worker.py:38)->_export(96) mark_running->build_export_payload(仓库 401-621, 37 段)->complete_export 存 privacy.data_export_payloads('completed')+payload_sha256+expires_at->前端 exportStatus 轮询(196)->exportPayload(219) 写文件+shareFileMessage
- **关键文件**:
  - `backend/app/domains/data_rights/repository.py:164-206(create_export 幂等入队)`
  - `backend/app/domains/data_rights/repository.py:401-621(build_export_payload 37 段手工 SQL)`
  - `backend/app/domains/data_rights/repository.py:623-729(complete_export/expire_export_payloads)`
  - `backend/app/domains/data_rights/worker.py:38-110(process_next/_export)`
  - `backend/app/domains/data_rights/router.py:51-96`
  - `apps/wechat/miniprogram/pages/privacy/index.ts:160-261`
  - `apps/wechat/miniprogram/api/data-rights.ts:13-31`
- **核心表**: `privacy.data_export_requests`, `privacy.data_export_payloads`, `privacy.policy_versions`
- **易错点**:
  - build_export_payload 的手工 SQL 清单是导出唯一真相源,新增个人数据表不会自动进入导出
  - create_export 只入队不执行,立刻查 export_status 永远 queued,要等 worker 轮询
  - payload 有 1h-7d 有效期(privacy_export_expiry_seconds 配置),openExport 前必须先确认 state==='completed'(privacy/index.ts:222),过期后必须 restartExport
- **关联**: 账号删除(同域共享 DataRightsRepository/worker)、learning_brain(导出含 learner_brain_profile_versions 画像)、coach_notifications(导出含 messaging 侧全部通知表)

#### 3. 账号删除

- **产品意图**:合规的「被遗忘权」:用户必须逐字输入「删除我的岸岸账户」确认短语才能发起。后端校验短语、检查是否还有未结清的支付/退款订单(有则拒绝),然后入队一个 account_erasure_v1 后台作业,并立即把账户置为 deletion_pending 状态、吊销全部设备会话——用户在作业跑完前就无法再登录。worker 执行时先收集所有私有上传对象的 object_ref 逐个删除,再跑一张手工维护的约 51 条 erase SQL 清单(能删的删行、有审计需求的把内容置为 '[deleted]'/'erased' 占位、支付表做去标识化),最后清掉微信/Apple 身份映射、把 users 置 disabled 并写一份删除清单 manifest 留档。deletion_pending 状态被 auth 登录、微信登录、identity 契约、前端登录控制器、隐私页 5+ 处各自判断拦截,没有唯一属主。
- **调用链**:pages/privacy/index.ts:292 requestDeletion(短语比对)->305 deleteAccount(幂等 key+401 处理)->POST /api/v2/data-rights/deletions(router.py:99)->repository.create_deletion(287) 短语hash+wechat_orders 待结算检查->入队 account_erasure_v1+users.account_status='deletion_pending'(358)+app_sessions 全吊销(365)->worker._delete(worker.py:112) mark_running->deletion_object_refs(685)->storage.delete_private 逐对象->complete_deletion(731)->_erase_personal_content(917, 51 条 SQL, statements 元组 941-1363)->删 wechat/apple_identities+app_sessions(765-780)->wechat_orders payer_open_id 去标识(784)->users 置 disabled+manifest 落库(791-841)
- **关键文件**:
  - `backend/app/domains/data_rights/repository.py:287-382(create_deletion: 短语校验/订单检查/置 pending/吊销会话)`
  - `backend/app/domains/data_rights/repository.py:917-1377(_erase_personal_content 51 条 erase 元组)`
  - `backend/app/domains/data_rights/repository.py:731-849(complete_deletion+manifest)`
  - `backend/app/domains/data_rights/repository.py:685-704(deletion_object_refs 收集私有对象)`
  - `backend/app/domains/data_rights/worker.py:112-132(_delete)`
  - `backend/app/domains/auth/repository.py:689-693(deletion_pending 拒绝登录), auth/router.py:74,153-157`
  - `apps/wechat/miniprogram/pages/privacy/index.ts:292-340`
  - `apps/wechat/miniprogram/features/identity/wechat-login-controller.ts:188(account_deletion_pending 文案)`
- **核心表**: `privacy.account_deletion_requests`, `privacy.deletion_events`, `privacy.external_processing_deletion_events`, `learner.users`, `learner.app_sessions`, `learner.wechat_identities`, `learner.apple_identities`, `commerce.wechat_orders`
- **易错点**:
  - erase 清单是手工维护的 51 条 (key, sql) 元组(repository.py:941-1363),新增表要人工补 SQL,漏一条=个人数据残留
  - deletion_pending 在 7+ 处各自判断(auth/repository:691, auth/router:74/153, identity+auth schemas, 前端 wechat-login-controller:188, privacy:323/330),改状态机要同步所有点
  - deleteAccount 成功即 clearSession+reLaunch,而作业仍异步跑,用户无法再查 deletion 状态,失败只能靠后台重试(worker 每 2 分钟重试,最多 5 次)
- **关联**: 数据导出(同域同 worker,导出+删除共用 DataRightsRepository)、登录/鉴权 auth 域(deletion_pending 拦截)、私人知识/素材对象存储 external_ingestion

#### 4. 学习大脑 / 记忆

- **产品意图**:学习大脑是把用户的学习状态「物化」成可读画像的域:每次请求 profile 都会对用户加行锁,聚合能力估计、行测/申论各模块掌握度、确认过的错因模式、策略决策、证据引用等,算出一个 input_fingerprint;如果指纹与当前版本一致就原样复用,变了就再插一条新版本(append-only,supersedes 链),并发 outbox 事件。能力页、岸岸 AI、备考档案页都消费这份画像。它上面还跑「策略候选/决策」:AI 或规则产出时间取舍候选,用户确认后写进 strategy_decision_versions 长期生效。记忆(memory)域是 v1 遗留:读 learner.profiles 与答题计数,写侧已废弃(PUT /profile 返回 410),现在主要活在 /api/v1/workbench/preview 工作台聚合里。
- **调用链**:pages/ability/index.ts:103 LearningBrainApi.getProfile -> GET /api/v2/learning-brain/profile(learning_brain/router.py:51)->service.get_or_create_profile(service.py:64): lock_user->ability_service.get_current->input_state(仓库 66-288 大查询)->xingce_modules(290)+essay_modules(402)+error_patterns(441)->fingerprint 比对(134)->写/复用 learning.learner_brain_profile_versions+outbox 事件;策略: POST /strategy-candidates(service.py:254)->inference_candidate_versions, POST .../decision(328)->strategy_decision_versions;memory: GET /api/v1/learner/memory(memory/router.py:26)->learner.profiles+计数, 工作台 GET /api/v1/workbench/preview(api/routes.py:103)->memory_service.list_workbench_sections(仓库 292-360)
- **关键文件**:
  - `backend/app/domains/learning_brain/service.py:64-197(get_or_create_profile+指纹)`
  - `backend/app/domains/learning_brain/service.py:254-445(strategy candidate/decision 幂等)`
  - `backend/app/domains/learning_brain/repository.py:66-288(input_state 聚合查询)`
  - `backend/app/domains/learning_brain/repository.py:290-620(xingce/essay modules+error_patterns)`
  - `backend/app/domains/learning_brain/router.py:51-68,129-185`
  - `backend/app/domains/memory/router.py:26-52(GET memory / PUT 410)`
  - `backend/app/domains/memory/repository.py:68-174(get_memory),292-360(workbench)`
  - `backend/app/api/routes.py:103-119(workbench_preview)`
- **核心表**: `learning.learner_brain_profile_versions`, `learning.inference_candidate_versions`, `learning.strategy_decision_versions`, `learning.ability_estimate_versions`, `learning.xingce_mastery_state_versions`, `learning.essay_mastery_state_versions`, `learner.profiles`, `learner.current_profile_position_projections`
- **易错点**:
  - get_or_create_profile 每次请求都全量重算(多张大 SQL)+对用户行加锁(lock_user),画像大时接口偏重
  - 指纹不一致就插新版本,supersedes 链持续增长;knowledge_point_weaknesses(service.py:448)是只读投影不进指纹,所以它变化不触发新版本
  - memory 域是 v1 遗留,update_profile 已 410 Gone(router.py:34-52),别再基于它做写逻辑,写建档一律走 v2 onboarding
- **关联**: 能力评估 ability_estimation、行测掌握 xingce_learning mastery、申论 mastery essay 域、我的聚合页(archive 页读 LearningBrainApi.getProfile 当 readiness)

#### 5. 通知与订阅消息

- **产品意图**:「岸岸 AI 督学提醒」的唯一外呼通道,走微信订阅消息(单次授权模式 one_time)。用户在 notification-settings 页可开关 5 类提醒(今日任务/复盘/复测/模考/周总结)、设接收时段、免打扰、每日频率上限;保存走版本化偏好表+幂等命令,前端还有 OperationAttemptStore 做「结果不确定时恢复重试」。微信侧授权通过 wx.requestSubscribeMessage 弹出,每次 accept 只增加一次可用授权(availableGrants),页面把它记录进订阅事件/授权表;用户也可去微信设置页改订阅,页面同步记录 settings_snapshot。后台 worker 定时评估该用户当下有没有应提醒的任务,决定建一条 delivery 并入队,再逐个调微信模板消息接口,投递状态机覆盖 queued→sending→platform_accepted/rejected→failed/outcome_unknown/canceled,点开通知只记 opened 事件、不把任务标完成。
- **调用链**:pages/notification-settings/index.ts:168 loadSettings -> GET /api/v2/coach-notifications/settings(coach_notifications/router.py:58)->service.settings(service.py:44): current_or_create_preference+available_grants+latest_subscription_event+近 10 条投递;save(334)->PUT /preferences(幂等)写 messaging.coach_preference_versions;requestWechatAuthorization(413)->wx.requestSubscribeMessage->persistSubscription(494)->POST /wechat-subscriptions 落 events+grants;后台 worker.run_once(worker.py:70)->evaluator.evaluate(evaluator.py:45) 判 5 类提醒->create_queued_delivery+入队 coach_notification_send_v1->process_next(95) 校验模板/开关/open_id->WechatSubscribeMessageClient.send->mark_accepted;点开: POST /deliveries/{id}/opened(router.py:151)->record_open(service.py:170, 打开≠完成)
- **关键文件**:
  - `apps/wechat/miniprogram/pages/notification-settings/index.ts:334-411(save+幂等重试)`
  - `apps/wechat/miniprogram/pages/notification-settings/index.ts:413-572(微信订阅授权/persistSubscription)`
  - `backend/app/domains/coach_notifications/router.py:58-178`
  - `backend/app/domains/coach_notifications/service.py:44-192`
  - `backend/app/domains/coach_notifications/template.py:41-110(模板字段契约 thing/time)`
  - `backend/app/domains/coach_notifications/worker.py:70-206(run_once/process_next 状态机)`
  - `backend/app/domains/coach_notifications/evaluator.py:45-173(判提醒/入队)`
- **核心表**: `messaging.coach_preference_versions`, `messaging.wechat_subscription_events`, `messaging.wechat_subscription_grants`, `messaging.coach_notification_deliveries`, `messaging.coach_notification_delivery_events`, `messaging.coach_notification_open_events`, `messaging.coach_notification_decisions`, `messaging.coach_evaluation_cursors`, `learner.wechat_identities`
- **易错点**:
  - 模板配置在环境变量 WECHAT_COACH_TEMPLATE_RELEASE_JSON,缺失或字段漂移时 GET /settings 直接 503(router.py:69),前端模板未配置就禁止发起订阅请求(index.ts:414)
  - 微信单次授权:每次 accept 只 +1 availableGrants,授权为 0 时发送会被取消或受限,不能假设授权永久有效
  - record_open 的 taskCompletionChanged 恒为 False(service.py:188),打开通知≠完成任务,前端逻辑/文案都别混淆
- **关联**: 我的聚合页(me 页 openNotificationSettings 入口)、今日计划 planning(worker 判提醒依据 daily_plan_versions/task_execution_versions)、数据导出(导出含 messaging 全部通知表)

## 10. 运维 / 基础设施

**本簇概览**:本簇是"考公搭子"的发布与治理地基:一台服务器(101.96.198.253/kg.xingguoxian.cn)跑生产与 staging 双环境,通过 verify 门禁链(full_verify → check_architecture + verify_backend + verify_wechat)保证改动质量,Alembic 单头 + OpenAPI 契约双校验锁定前后端同源,发布走"不可变制品 + 签名验签 + 数据库兼容契约"的严格流水线,权威事实由 .env、ops/ 目录与"两活一档"治理文档承载。跨功能最大警告:QBANK_IMPORT_DATABASE_URL 指向生产库是已实踩的历史坑(P12 指纹误写生产库 7361 条),任何回填/实验写入必须显式切到 staging 连接串;生产备份(BACKUP_ENABLED)至今未启用,是接真实用户前的 P0 上线阻塞。

#### 1. 服务器双环境拓扑:生产 gongkao_dazi vs staging gongkao_dazi_staging

- **产品意图**:一台服务器(101.96.198.253 / kg.xingguoxian.cn)同时跑两套隔离环境:生产环境是稳定权威基线(gongkao_dazi 库,5.5 万 canonical 题,生产 API 在读),staging 环境是修修改改的实验场(gongkao_dazi_staging 库,23.1 万新导入题,所有导入/去重/打标签/回填指纹的活都在这)。两套环境共用同一 release 代码,但各自独立 systemd 服务、独立 env 文件、独立数据库。运营铁律一句话:『_staging 库=干活的地方,gongkao_dazi 库=权威基线别乱动』。服务器有 SSH 限流,频繁连接会被 reset。
- **调用链**:开发写导入脚本 -> backend/scripts/import_pp_questions.py 默认连 postgresql:///gongkao_dazi_staging -> 数据落 staging 库 -> finalize_question_bank.py(require_staging_database_name 强制库名 *_staging)收口 -> ops/scripts/publish_commercial_data_release.sh 发布数据 -> 生产 API(gongkao-dazi-api.service :8000,连 gongkao_dazi)读取 canonical 基线
- **关键文件**:
  - `docs/ops/environment-topology.md:1-16(环境拓扑总表:生产/staging、端口、APP_ENV、题量)`
  - `docs/ops/environment-topology.md:53-69(§四易踩坑,含 QBANK_IMPORT 坑与 staging alembic 跑法)`
  - `backend/scripts/import_pp_questions.py:21(默认连 postgresql:///gongkao_dazi_staging)`
  - `backend/scripts/finalize_question_bank.py:111-115(require_staging_database_name:库名不以 _staging 结尾直接拒绝)`
  - `backend/scripts/generate_visual_fingerprints.py:72(--database-url 默认取 QBANK_IMPORT_DATABASE_URL 这个坑源)`
  - `ops/systemd/gongkao-dazi-api.service:6-15(生产 API 单元,EnvironmentFile=/etc/gongkao-dazi/{api,db}.env)`
  - `ops/env.example:252-264(DATABASE_URL 示例 + MIGRATION_DATABASE_URL 迁移专用连接串)`
  - `scripts/import_gwy_positions.py:44(导入岗位:URL 默认取 QBANK_IMPORT_DATABASE_URL 或 DATABASE_URL)`
- **核心表**: `product.questions`, `product.question_occurrences`, `product.question_content_heads`, `product.question_visual_fingerprints`, `platform.commercial_data_publication_state`
- **易错点**:
  - QBANK_IMPORT_DATABASE_URL 在 /etc/gongkao-dazi/db.env 里指向生产库,回填指纹/写实验数据必须显式换 staging 连接串(source /etc/gongkao-dazi/staging-db.env 后把 DATABASE_URL 的 +psycopg 去掉),否则就是 P12 复刻(7361 条指纹误写生产库)。
  - 两个 API 共用 release 代码但 staging 用的是 /srv/gongkao-dazi/staging/backend 独立活目录,别用生产 current 的 venv 跑 staging 的 alembic。
  - 服务器对频繁 SSH 连接限流(Connection reset/closed),合并命令、断连等 2-3 分钟、用后台循环重试。
- **关联**: 验证门禁、数据库迁移、发布与部署

#### 2. 验证门禁:verify_backend / verify_wechat / full_verify

- **产品意图**:这是所有改动的安全网,发布前必须全绿。full_verify 是总入口,依次跑三层:check_architecture.py 用 AST 静态扫描后端分层导入边界(platform/core/db 层禁止往上依赖 api/domains/importers)并校验前端生成契约文件的归属标记;verify_backend.sh 用 uv --locked 锁定运行时跑 ruff 全量 + pytest 全部用例,再加 alembic 单头校验(必须恰有一个 head 且带 (head) 标记);verify_wechat.sh 委托 npm run check 跑一大串前端门禁(prettier/tsc/eslint/十几种 validate 脚本/API 契约一致/vitest/云函数测试)。CI 里 architecture、backend(带 postgres 17 服务)、wechat 三个 job 分别跑这三段。
- **调用链**:提交代码 -> CI(.github/workflows/ci.yml)并行跑 3 job -> scripts/full_verify.sh -> scripts/verify.sh -> check_architecture.py(边界 AST 扫描 + 契约标记) -> verify_backend.sh(uv run ruff + pytest + alembic heads 单头) -> verify_wechat.sh -> npm run check(14 步:format:check→typecheck→lint→tokens/interactions/controls/design/navigation/structure/visual/interaction-coverage/assets/package 各 validate→api:check→test:node→test→test:cloud) -> 全绿才可进发布流水线(stage8-release.yml 先 verify.sh 再 rehearsal 再 build)
- **关键文件**:
  - `scripts/full_verify.sh:6(全量验证总入口,调 verify.sh)`
  - `scripts/verify.sh:6-8(三件套:check_architecture.py→verify_backend.sh→verify_wechat.sh)`
  - `scripts/verify_backend.sh:8-45(uv --locked 锁定运行时 + PYTHON 兼容回退在 CI 禁止)`
  - `scripts/verify_backend.sh:49-59(ruff check + pytest + alembic heads 必须 1 个 head 且带 (head))`
  - `scripts/verify_wechat.sh:6-7(委托 apps/wechat 下 npm run check)`
  - `scripts/check_architecture.py:31-35(分层禁导:platform 禁 api/domains/importers,core/db 更严)`
  - `scripts/check_architecture.py:48-61(generated 契约必须存在且首行为 @hey-api/openapi-ts 归属标记)`
  - `apps/wechat/package.json:7(npm run check 全链 14 步定义)`
  - `.github/workflows/ci.yml:23-74(architecture/backend(postgres 17)/wechat 三 job)`
- **易错点**:
  - verify_backend.sh 在本地若无 uv 会直接 exit 127,文档兼容路径必须显式设 PYTHON,而 CI 里设置 PYTHON 会被禁止(只能用锁定的 uv 运行时)。
  - design-file.tokens.wxss 被哈希锁定,对仓库跑 prettier 全量 glob 会碰坏它——只 prettier 明确改过的文件。
  - Alembic 单头检查是文本级(alembic heads 输出必须恰一行且含 (head)),新加迁移时若忘改 down_revision 或产生分叉,这里最先报。
- **关联**: 数据库迁移、API 契约生成、发布与部署

#### 3. 数据库迁移:Alembic + 单头约束 + 命名规范

- **产品意图**:数据库变更全部由 Alembic 从 20260712_00 基线开始管理,当前共 96 个迁移文件,最新 head 是 20260804_95。每个迁移按『日期_序号_语义名』命名(如 20260712_00_existing_schema_baseline.py、20260804_95_daily_focus_override.py),序号从 00 连续递增到 95。迁移强约束单头:verify_backend.sh 用 alembic heads 检查必须恰有一个 head;ops/release-db-compatibility.env 的 ARTIFACT_DATABASE_MIGRATION_HEAD 必须与 head 一致,否则激活/升级报 DatabaseCompatibilityError。迁移在 schema 维度落库(迁移文件里显式传 schema=,涉及 learner/learning/product/platform/commerce/ai_audit/privacy 等 schema,与『多 schema』架构一致)。
- **调用链**:改模型/加字段 -> 写迁移文件 backend/migrations/versions/YYYYMMDD_NN_*.py(revision 递增、down_revision 指向前一个) -> verify_backend.sh 跑 alembic heads 验证单头 -> 更新 ops/release-db-compatibility.env 的 ARTIFACT_DATABASE_MIGRATION_HEAD -> 本地与 staging 各一份 -> 发布时激活链路复验该契约
- **关键文件**:
  - `backend/migrations/versions/20260804_95_daily_focus_override.py:15-16(最新 head 20260804_95,down_revision=20260802_94)`
  - `backend/migrations/versions/20260712_00_existing_schema_baseline.py:1(存量生产库基线记录,stamp 起点)`
  - `backend/migrations/env.py:15-17(从 settings.database_url 取连接串,支持外部传入 connection 的原子边界)`
  - `backend/alembic.ini:2(script_location=migrations)`
  - `ops/release-db-compatibility.env:7(ARTIFACT_DATABASE_MIGRATION_HEAD=20260804_95,不可变制品契约)`
  - `scripts/verify_backend.sh:52-59(alembic heads 单头检查)`
  - `backend/README.md:157-164(alembic stamp 20260712_00 / upgrade head 操作说明)`
- **易错点**:
  - 新增迁移后必须同步更新 ops/release-db-compatibility.env 的 ARTIFACT_DATABASE_MIGRATION_HEAD,本地和 staging 各一份,否则 alembic upgrade 报 DatabaseCompatibilityError——但『门禁只拦迁移不拦读』,读取(staging 角色读 product 表)仍畅通无阻。
  - 迁移必须保持单链无分叉,verifier 以 backend/migrations/versions/ 最新文件为准(发布契约已解钉,勿再钉死旧 revision)。
  - schema 维度不能混:写迁移时 table 必须带正确 schema=(如 learning.daily_capacity_versions),否则表落错 schema 与 repository 的 schema 前缀 SQL 对不上。
- **关联**: 服务器双环境拓扑、验证门禁、发布与部署

#### 4. API 契约生成:openapi-ts 与 types.gen.ts

- **产品意图**:前后端同源的机制:后端 FastAPI 的 OpenAPI 文档是唯一真相,前端所有 API 类型都从它生成,不允许手写。api:generate(npm run)先以 uv --locked 隔离环境跑 backend/scripts/export_openapi.py 导出 openapi.json(固定 LANG/LC_ALL/PYTHONHASHSEED/TZ 保证确定性输出),再用 @hey-api/openapi-ts 生成 types.gen.ts,最后原子写入(临时目录 + rename)。api:check(--check)是门禁侧:重新生成一份并与已提交文件逐字节对比,有差异就报『Generated API types are stale』+ 首个差异行,要求跑 api:generate 并提交结果。前端各 api/*.ts 模块(account/commerce/planning/xingce-learning 等 30+)都从 generated/types.gen.ts import 类型。
- **调用链**:后端改接口/模型 -> npm run api:generate(apps/wechat/scripts/generate-api.mjs) -> uv run backend/scripts/export_openapi.py 导出 openapi.json -> node_modules/.bin/openapi-ts 生成 types.gen.ts(确定性环境) -> 原子写入 miniprogram/api/generated/types.gen.ts -> npm run check 里 api:check 复核字节一致 -> CI/verify 全绿后前端 api/*.ts import 新类型
- **关键文件**:
  - `apps/wechat/scripts/generate-api.mjs:15-18(输出路径 miniprogram/api/generated/types.gen.ts)`
  - `apps/wechat/scripts/generate-api.mjs:20-48(exportLockedOpenApiSchema:uv --locked 隔离导出,清 PYTHON* 环境变量)`
  - `apps/wechat/scripts/generate-api.mjs:135-159(确定性环境 LANG=C/PYTHONHASHSEED=0/TZ=UTC + openapi-ts 调用)`
  - `apps/wechat/scripts/generate-api.mjs:164-175(--check 模式逐字节对比 + 原子写入)`
  - `backend/scripts/export_openapi.py:10-16(create_app().openapi() 导出 canonical OpenAPI)`
  - `scripts/check_architecture.py:48-61(契约文件首行必须为 // This file is auto-generated by @hey-api/openapi-ts)`
  - `apps/wechat/package.json:8-9(api:check / api:generate 脚本定义)`
  - `apps/wechat/miniprogram/api/generated/types.gen.ts:1(32,813 行的自动生成契约,首行归属标记)`
  - `apps/wechat/miniprogram/api/planning.ts(示例:api 模块从 generated/types.gen.ts import 类型)`
- **易错点**:
  - 手改 types.gen.ts 必被两道门拦:api:check 字节对比 + check_architecture.py 归属标记检查,正确做法是改后端 OpenAPI 再 api:generate。
  - 生成必须走确定性环境,本地直接跑会因为 PYTHONHASHSEED/LANG/TZ 差异产出字节不一致,导致 api:check 假失败。
  - backend/README.md 里写的老 head(20260713_18)早已过期,别拿文档里的版本号当现状,以 alembic heads 实跑为准。
- **关联**: 验证门禁、数据库迁移

#### 5. 发布与部署:上传体验版 / 云函数 / 不可变制品流水线

- **产品意图**:发布分两条道:轻量 staging 道(ops/scripts/release_staging.sh,rsync 只同步 backend/app 源码到 /srv/gongkao-dazi/staging,重启 staging API 并健康检查)和正式发布道(商业数据与应用切换严格排序)。正式道:CI 先 full_verify,再从精确干净 commit 跑 build_immutable_artifact.sh 产出带 SHA-256 的不可变 tar 制品 + release manifest;activate_release.sh 验证签名/commit/环境/DB 兼容后切换控制面,但绝不执行 migration;rollback_release.sh 是唯一回滚入口。微信侧:upload_wechat_release.py 在隔离 macOS 上传机对钉住 SHA-256 的官方微信开发者工具,取 git archive 的 apps/wechat 私有提取交给 CLI 上传体验版,产出 RSA-SHA256 签名回执,微信证据五件套缺一即拒绝。云函数 hunyuan-essay-grader 在 apps/wechat/cloudfunctions/ 下,经微信云开发(CLOUDBASE_* env)部署。
- **调用链**:改动提交 -> CI(full_verify)全绿 -> 数据道 publish_commercial_data_release.sh(排空 checkout + advisory 锁 + forward-only migration + 数据发布) -> 微信道 upload_wechat_release.py(git archive apps/wechat -> 钉住工具 SHA-256 的官方 CLI -> 上传体验版 -> 签名回执) -> release_staging.sh(DATA_RELEASE_RECEIPT + 微信证据签名) -> activate_release.sh(重新解析 current + 验签 + DB 兼容契约 + 原子切换 release-runtime.env) -> 故障回滚 rollback_release.sh;staging 快捷道 release_staging.sh -> rsync backend/app -> 重启 gongkao-dazi-staging-api -> 健康检查 /staging/health
- **关键文件**:
  - `ops/RUNBOOK.md:1-14(发布与回滚全流程:CI 必须 full_verify + 精确 EXPECTED_GIT_COMMIT)`
  - `ops/scripts/build_immutable_artifact.sh:18-48(脏工作树拒绝 + git archive 出不可变制品 + SHA-256 sidecar)`
  - `ops/scripts/upload_wechat_release.py:4-33(git archive 私有提取,非工作树;RECEIPT_SCHEMA=anan-wechat-upload-receipt/v2,RSA-SHA256)`
  - `ops/scripts/upload_wechat_release.py:956-993(回执/signature/info/log 四件套原子发布)`
  - `ops/scripts/release_staging.sh:4-12(staging 轻量道:只更新隔离 staging app,生产走独立 release authority)`
  - `ops/scripts/activate_release.sh:86-120(commercial-data-publication-guard 能力契约复验,不执行 migration)`
  - `ops/scripts/rollback_release.sh:ROLLBACK 入口(数据库不做破坏性降级,否则 forward-fix)`
  - `apps/wechat/cloudfunctions/hunyuan-essay-grader/ 目录(runtime.js + config.json + runtime.test.js,test:cloud 覆盖)`
  - `apps/wechat/project.config.json:2-6(appid wx42824c7f7bfbf4b2,cloudfunctionRoot=cloudfunctions/)`
  - `.github/workflows/stage8-release.yml:25-27(verify.sh → rehearse_artifact_switch.sh → build_immutable_artifact.sh)`
- **核心表**: `platform.commercial_data_publication_state`, `platform.commercial_data_publication_transitions`
- **易错点**:
  - release 与 uploader 签名私钥都不得复制到部署机;微信证据五件套(WECHAT_UPLOAD_RECEIPT/RECEIPT_SIGNATURE/INFO_OUTPUT/RAW_LOG + TRUST_PUBLIC_KEY)缺一即拒绝,远端还要用 root 管理的 STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY 复验 detached 签名。
  - 生产上传只接受 identityMode=macOS-codesign,微信开发者工具三个真实文件(SHA-256)必须人工核准钉住,工具热更新后必须重新核准 policy,不能自动信任。
  - activate_release.sh 不跑 migration/业务写入,只验证并切换;迁移必须走数据发布的 forward-only mutation 路径,违规手工升级会被数据库兼容契约拦。
- **关联**: 服务器双环境拓扑、验证门禁、数据库迁移、治理文件体系

#### 6. 治理文件体系:.product-governance 与『两活一档』

- **产品意图**:产品治理用结构化 JSON 承载权威决策:.product-governance/ 下 manifest.json 定义产品名(岸岸公考搭子)、权威优先级(user_confirmed > applicable_policy > frozen_decision > code_observed > existing_document > inferred > proposed)与 17 个 artifacts(domains/contracts/requirements/decisions/evidence-ledger/release-units/work-packages 等)。execution/ 存 release-unit 快照与 qbank 收口结果,reports/ 存每个发布单元的 review/freeze 评审。文档侧是『两活一档』:global-backlog.md(活待办,唯一进度真相)+ docs/product/2026-07-18-canonical-product-boundaries.md(产品边界,产品负责人已确认,契约性质)+ docs/engineering/_archive/(已完结历史,只供追溯不再作现状依据);operations-playbook.md 是『怎么做』的活知识唯一汇总,交接文档只作历史追溯。
- **调用链**:产品决策 -> decisions.json(decision register)+ evidence-ledger.json(证据台账) -> requirements.json(REQ-* 条目 + acceptance) -> work-packages.json(WP-* 拆包) -> release-units.json(RU-* 冻结,closure 关联) -> execution/reports 产出 RU-* review/freeze 快照;运维真相走两活一档:global-backlog.md(当前状态+待办,如 T2 生产备份 P0 阻塞) -> operations-playbook.md(怎么做的活知识) -> _archive/(只追溯)
- **关键文件**:
  - `.product-governance/manifest.json:1-17(产品名/status=review/权威优先级 precedence/17 个 artifacts 映射)`
  - `.product-governance/domains.json(域归属与不变式,如 question-bank-finalization 只写 *_staging 库)`
  - `.product-governance/requirements.json(REQ-20260728-runtime-blockers 等,statement/acceptance/owner)`
  - `.product-governance/release-units.json(RU-* 冻结单元,dependsOnReleaseUnits + closure)`
  - `.product-governance/execution/(release-unit-snapshots + qbank-finalization-aborted.json)`
  - `.product-governance/reports/(RU-* review/freeze 评审快照)`
  - `docs/engineering/global-backlog.md:6-12(两活一档权威来源声明 + 惯例:v1/v2 标注、fail-closed 守卫)`
  - `docs/engineering/global-backlog.md:28(T2 生产备份启用 P0 阻塞,BACKUP_ENABLED 未开)`
  - `docs/engineering/operations-playbook.md:1-5(活知识唯一汇总,历史交接只作追溯)`
  - `docs/engineering/2026-07-26-session-handoff.md:1-5(大会话交接文档,作历史追溯用)`
  - `docs/product/2026-07-18-canonical-product-boundaries.md(产品边界,契约性质)`
- **易错点**:
  - 文档有新旧之别:global-backlog.md 与 operations-playbook.md 才是活真相,_archive/ 与 2026-07-26-session-handoff.md 只供追溯,别拿历史文档当现状(如 README 里的旧迁移 head 已过期)。
  - 跨端字段改动必须标 v1/v2、旧数据缺省合法、前后端必须同源(OpenAPI 生成类型);遇到 403/409/429 先怀疑 fail-closed 守卫在拦(内容发布/PII 出口/对象归属/版本不可变),别当 bug 排查。
  - .product-governance 的 JSON 由工具生成、体积大(release-units.json 约 403KB),手工编辑易破坏 schemaVersion 与引用一致性,改契约应走对应权威渠道而非直接改 JSON。
- **关联**: 发布与部署、验证门禁、服务器双环境拓扑

---
## 附录 A:跨簇易踩坑位速查

| 坑 | 位置 | 一句话 |
|---|---|---|
| 完成状态机三写入点 | `real_attempt/service.py:1280-1321` | 行测完成事实由 submit / decide_cause 历史补齐 / settle 三方写入,弱握手,改「完成」相关必看 |
| `task_execution_versions` 四写入方 | planning/service.py:430、xingce_completion.py:88、xingce_start.py:62、mock_completion.py:171 | 新任务类型加入必须自证 evidence 归属,否则 409 |
| 半吊子解耦残迹 | `ai/provider.py` + `companion/service.py` | provider 解析 requestedActions 后被传空参数丢弃;`_build_persona_prompt` 的 `del context` 但调用方还在传 |
| provider↔companion 循环依赖 | `ai/provider.py:29` | provider 反向 import companion/agent,方向坏了但运行时不炸 |
| 平台判断写死 | `api/commerce.ts:100-104` | 虚拟支付不识别平板,平板被当成 iOS |
| 考点枚举三份拷贝 | ability / day-adjustment | 同一业务枚举三处维护,后端 schema 变一处漏两处 |
| 私有函数跨域 | `ilink_channel/plan_projection.py:16` | import planning 的 `_goal_gap`,私有 API 被外部消费 |
| 唯一真双写表 | `commerce.community_reward_awards` | community_rewards 发放、commerce 消费/激活,改一边必核另一边 |
| 业务规则在 SQL 里 | stage8_repository、data_rights | 退款率/时间窗口/47 条 erase 全是手工清单,新增个人字段要同步两处 |
| 生产备份未启用 | ops | BACKUP_ENABLED 未开,接真实用户前 P0 |

## 附录 B:常驻脑内的文件清单(<20 个)

| 文件 | 为什么必记 |
|---|---|
| `backend/app/domains/planning/repository.py` | 2888 行,7 个责任簇,`execution_evidence_exists`(450 行)内联 essay 证据契约 |
| `backend/app/domains/planning/service.py` | `get_or_create_today` 是 13 个页面/4 个域共用的事实公共 API |
| `backend/app/domains/planning/engine.py` | 打分权重 = 「今天为什么排这些题」的答案 |
| `backend/app/domains/xingce_learning/real_attempt/service.py` | 交卷/复盘状态机,内联 6 处 planning 调用 |
| `backend/app/domains/companion/service.py` | ask 主链路 + execution_payload 幂等重放 |
| `backend/app/domains/ai/provider.py` | 模型供应商/adapter/降级,含死链路残迹 |
| `backend/app/domains/essay_intelligence/workflow_service.py` / `review_worker.py` | 申论批改生产者/消费者,结构健康 |
| `backend/app/domains/commerce/wechat_virtual_payment.py` | xpay 协议+业务混合,状态数字硬编码 |
| `backend/app/domains/data_rights/repository.py` | 导出 37 段 + 删除 51 条手工 SQL |
| `apps/wechat/miniprogram/features/planning/today-controller.ts` | 高质量共享 controller,但只有 today 页用 |
| `apps/wechat/miniprogram/pages/day-adjustment/index.ts` | 914 行,页面内重写 300 行幂等逻辑 |
| `apps/wechat/miniprogram/api/client.ts` | 统一网络层,401 统一回调 |
| `apps/wechat/miniprogram/auth/session.ts` | token 会话单例(generation 防并发覆盖) |
