# 审计阻塞项与未覆盖范围

这些项目不计入正式 findings 的严重度数量。

## BLOCK-E2E-001：没有隔离的非生产 API/数据库/账号环境

- `develop`、`trial`、`release` 当前都解析到仓库记录为 production 的
  `kg.xingguoxian.cn`。
- 本轮禁止接触生产，因此小程序 → FastAPI → PostgreSQL 的真实跨服务 E2E 为
  `0/12` 条现存核心链路。
- 影响：无法用真实服务验证正常、空、错误、慢请求、网络断开、登录过期和写入后的 DB
  一致性；当前证据来自页面/controller/契约测试与本地临时 PostgreSQL。
- 解阻条件：独立 staging origin、隔离 PostgreSQL、可重置审计账号/种子数据、禁用真实
  支付和受控 AI provider。

## BLOCK-AI-001：真实 AI worker/provider 未在安全环境运行

- essay review worker 默认关闭，没有安全 provider 配置。
- cause-agent 标准部署拓扑缺失已进入正式 finding，但真实 provider 质量、延迟、配额和
  外部错误语义仍未覆盖。
- 解阻条件：隔离 provider 账号、预算上限、DPA/host allowlist、可审计 ledger。

## BLOCK-PROD-001：生产状态按要求未检查

本轮没有读取生产 PostgreSQL、用户数据、支付记录、对象存储、真实 worker/systemd、
CDN/WAF 或线上 queue。因而不能声称：

- 生产当前 revision；
- 是否已经存在 stuck privacy/iLink/cause jobs；
- gateway 是否阻断匿名知识接口；
- 生产 schema/role/extension 是否漂移；
- 是否发生真实重复扣费或真实用户数据损坏。

## BLOCK-POLICY-001：题库公开读取政策未在仓库中建立

匿名 knowledge search/detail 返回答案与解析是已证实技术事实，但既有 contract test
明确接受，无产品/安全政策能证明它是授权缺陷。该项保留在 hypotheses；政策 owner
确认“答案不得匿名/提交前返回”后，应按本轮 P0 安全定义进入正式 findings。

## BLOCK-NAV-001：缺参、返回栈和用户入口没有逐路由完整运行

真实 UI 审计以有效视觉 fixture `reLaunch` 覆盖页面和状态；它不等同于：

- 17 个必需参数页的全部缺参/错参组合；
- 每页 navigateBack、Tab 栈、循环跳转；
- 从真实上游入口逐步到达；
- 真实账号数据恢复。

A 子审计最初的通道被 DevTools 启动竞态阻塞；C 随后恢复 page-ready 并完成真实渲染。
最终覆盖数字以 C 的 manifest 为准，但上述导航行为仍需独立 E2E。

## BLOCK-SWIFT-001：仓库没有当前 SwiftUI 客户端

根 README 表示历史 iOS 原生客户端已移除；当前工作树没有可审计的 SwiftUI app/project。
因此 SwiftUI 构建、页面、接口合同和真实 UI 覆盖为 N/A/未覆盖，不凭空构造结论。

## COVERAGE-UI-001：跨设备深度与真实硬件能力未全量覆盖

- 标准手机、iPad 竖屏和 iPad 横屏已分别真实渲染 88/88 个业务状态；小屏和长安卓各
  只交叉采样 16/88 个核心状态，不能外推为所有手机档位都全量通过。
- iPad 横竖屏证据来自微信开发者工具模拟器（`768×1024`、`1024×768`），没有连接
  实体平板或真实手机摄像头。
- 未覆盖系统超大字体、多语言、完整软键盘、读屏和全量无障碍遍历。
- 解阻条件：隔离测试账号、真实 iOS/Android 手机和平板、允许的相机/键盘/无障碍设备
  矩阵，并继续保持 API 与支付 fail closed。

## COVERAGE-DB-001：没有完整 declarative ORM graph

后端主要使用 raw SQL，Alembic `target_metadata = None`，无法用 `alembic check` 完成
“ORM model vs migration”对比。本轮改用可执行 migration replay、schema contracts、
repository tests；其中 baseline → head 已确认失败并进入 finding。
