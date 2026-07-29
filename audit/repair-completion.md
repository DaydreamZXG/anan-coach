# 全局质量审计修复收口报告

状态：`COMPLETE_WITH_KNOWN_RESIDUAL`  
生成时间：2026-07-29（Asia/Shanghai）

## 1. 收口结论

原始审计的 15 条去重 finding 均已有可追溯修复提交并进入干净集成树。后端原始复现、
前端原始复现、后端总门禁、小程序唯一总门禁和最终五档微信开发者工具真实渲染均已完成。
原始三条 UI finding 已通过五档复核；另有两条状态/流程 finding 保持“逻辑已验证、原问题
精确可见状态未渲染”的诚实边界。

最终真实渲染新确认 1 条 P2 公共响应式根因
`ROOT-RESPONSIVE-METRICS-001`。它已单独进入修复后残余台账，不倒灌或改写原始 finding。

本报告不宣称已发布、生产就绪、物理设备验收或真实跨服务 E2E 完成。报告完成后停止，
不继续修改业务代码。

## 2. 版本、范围与源码冻结

- 审计基线：`01752f1d7b8b606b6900160eebba0888d8def294`
- 业务集成：`acae9dd0156ffa2960669bc5f1ca5367d1bc16d7`
- 最终集成：`3f7b2e703098b9fbb053f9838929633c5a03ed0b`
- 最终集成相对业务集成只新增 `tools/audit/c/**` 五个审计工具文件；
  `apps/wechat/**`、`backend/**`、`ops/**` 和依赖锁文件零差异。
- 正式集成工作树：clean、detached HEAD；未 push、未建 PR、未部署。
- 最终渲染及报告阶段未修改正式业务源码。
- 未访问生产环境、真实用户数据、真实支付或真实外部 AI provider。
- SwiftUI、iOS、Xcode：永久 N/A。

完整集成证据：
`audit/repair-evidence/integration-final-3f7b2e703.md`。

## 3. 自动门禁

### 后端

- `scripts/verify_backend.sh`：1060 passed、0 failed、0 skipped；
- Alembic：`20260728_74 (head)`，单 head；
- 耗时：4014.87 秒；
- 3 条第三方弃用警告，无业务失败；
- 日志 SHA-256：
  `342a4adb989ebd58fe8329e41a5389988811d071616455c125013830ad3a6014`。

原始后端复现重放：

- migration：24/24；
- verdict command：8/8，实际为 `verdict=1`、`XP=10`、`xp_gain=1`；
- jobs：20/20，合法 data-rights 成功路径 1/1；
- method-card replay：9/9；
- frozen-release 测试节点：52/52，factory 自检 1/1。

### 微信小程序

- Prettier、TypeScript、ESLint：通过；
- OpenAPI 生成类型只读检查：通过；
- Node：20/20；
- Vitest：105 files、767/767；
- cloud：9/9；
- token：86 个运行时样式表；
- interaction：90 个模板、480 个按钮；
- fixed-height control：321 个；
- design disposition：59 条；
- navigation：34 个自定义导航页面；
- 注册入口：65 = 59 个业务页面 + 6 个资产入口；
- visual contract：95 个场景覆盖 59 个业务页面；
- interaction contract：59 个业务页面。

前端五条原始复现共 39/39：

- `FLOW-MOCK-001`：2/2；
- `ROOT-AUTH-001`：10/10；
- `ROOT-IDEMP-CLIENT-001`：12/12；
- `ROOT-REQUEST-001`：5/5；
- `STATE-ANAN-001`：10/10。

## 4. 页面与真实渲染口径

### “88 个页面”的纠正

仓库基线没有 88 个注册页面。`88` 是覆盖 58 个业务页面的视觉状态数：

- 基线注册路由：64 = 58 个业务页面 + 6 个资产入口；
- 基线 page-ready 打开：64/64 成功，0 阻塞；
- 基线视觉状态：88 个；
- 修复后注册路由：65 = 59 个业务页面 + 6 个资产入口；
- 修复后视觉状态：95 个。

因此 505 张最终截图是 `5 profiles × 101 scenarios`，不是 505 个页面。最终真实业务页面
仍为 59 个。

### 最终五档结果

正式 source：
`3f7b2e703098b9fbb053f9838929633c5a03ed0b`。

隔离 runtime：
`audit/repair-evidence/runtime-project-final-3f7b2e703-20260729T003229`。

输出：
`audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229`。

| Profile | 截图 | 业务场景 | 资产 | 路由 | 失败/运行错误/警告 | 独立复核 |
|---|---:|---:|---:|---:|---:|---|
| 320×568 portrait | 101/101 | 95 | 6 | 65 | 0/0/0 | 完成 |
| 390×844 portrait | 101/101 | 95 | 6 | 65 | 0/0/0 | 完成 |
| 363×817 portrait | 101/101 | 95 | 6 | 65 | 0/0/0 | 完成 |
| 768×1024 portrait | 101/101 | 95 | 6 | 65 | 0/0/0 | 完成 |
| 1024×768 landscape | 101/101 | 95 | 6 | 65 | 0/0/0 | 完成 |

总计：

- profile：5/5；
- canonical 截图：505/505；
- 业务场景渲染：475/475；
- 资产入口渲染：30/30；
- 业务页面-profile 打开：295/295；
- 实际渲染页面覆盖：59/59 业务页面，五档均覆盖；
- 0 横向越界，0 viewport 横向溢出；
- 27 个窄屏 profile-scenario 主操作低于 44px；
- 1 个已证实产品纵向越界；另 1 个 small c14 是 fixture 固定位置 hypothesis，不进入
  正式 finding。

标准档首次 attempt 因 DevTools 截图能力从第 83 个场景起失效，只得到 82/101；负证据
保留。完整重启后的 `standard-390x844-retry-01` 为唯一 canonical 标准档，101/101；
没有用失败批次拼接或覆盖。

长安卓 canonical `c18` 没有呈现 fixture 声明的工具 sheet。后续在同一 363×817 profile
对正式 `.question-tool--more` 执行真实 `Element.tap()`，证实
`toolsOpen:false → true` 且完整 sheet 可见；因此该差异归为 fixture 注入/捕获异常，
不登记产品 Bug。探针是本地 mock-backed controller，不是跨服务或物理设备 E2E。

最终结构验证：

- source 复核前后均为同一 clean HEAD；
- runtime 669 个文件，全树 SHA-256：
  `40f769b1ba65237e74ffc45bca664f01e4735447932eaf94b7702521ab302aa8`；
- PNG CRC、尺寸、完整解码、manifest、路由、runtime identity 与允许变换全部通过；
- 结构验证器 SHA-256：
  `eae6d6bbd75ae523780b463913835c289c010b6ba3b004163e01b40cd30557bd`。

机器可读结果：
`audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229/validation.json`。

五档独立报告：

- `audit/subagents/c/final-small-visual-review-3f7b2e703.md`
- `audit/subagents/c/final-standard-visual-review-3f7b2e703.md`
- `audit/subagents/c/final-long-visual-review-3f7b2e703.md`
- `audit/subagents/c/final-ipad-portrait-visual-review-3f7b2e703.md`
- `audit/subagents/c/final-ipad-landscape-visual-review-3f7b2e703.md`

## 5. 问题数量与验证状态

### 原始审计台账

原始严重度保持审计时口径，不因修复完成而改写：

| 严重度 | 数量 |
|---|---:|
| P0 | 1 |
| P1 | 2 |
| P2 | 11 |
| P3 | 1 |
| 合计 | 15 |

原始 `audit/findings.jsonl` 保持不可变；逐条修复状态见
`audit/repair-status.jsonl`。

关键边界：

- `FLOW-MOCK-001`：自动逻辑复现通过，但五档场景未精确复现“409 后读取 latest
  submitted/local terminal”的原问题终态；
- `STATE-ANAN-001`：自动逻辑复现通过，但五档场景未精确复现“profile 成功 + today
  503”的局部成功/错误组合；`e04` 是正常 ready，不能替代；
- `UI-CAPTURE-001`、`UI-PRACTICE-ANSWER-CARD-001`、
  `UI-ESSAY-TITLE-001`：五档真实渲染通过。

### 修复后残余台账

| 严重度 | 数量 |
|---|---:|
| P0 | 0 |
| P1 | 0 |
| P2 | 1 |
| P3 | 0 |

唯一残余为 `ROOT-RESPONSIVE-METRICS-001`：

- small 22/26 个登记主操作低于 44px；
- long 5/26 个低于 44px；
- long `h08-capture-crop` 的确认按钮超出 viewport `16.171875px`；
- 标准手机、iPad 竖屏和 iPad 横屏均为通过对照；
- 三个症状按同一 `--rpx` 物理/纵向指标误用根因去重为一条。

详情：
`audit/post-repair-findings.md`。

## 6. 核心业务链路覆盖

- 用户指定链路：13/13 已识别；
- 当前版本实际存在：12；
- 公告：feature flag 明确关闭，N/A；
- 页面/controller/契约动态覆盖：11/12，即 91.7%；
- 隔离小程序 → FastAPI → PostgreSQL 跨服务 E2E：0/12。

启动、登录、首页、今日任务、刷题、提交、结果、解析、错题、模考、岗位筛选和 AI 批改
均有代码、状态或自动合同证据。没有安全的非生产 API、数据库、可重置账号和 provider，
因此没有把真实跨服务链路虚报为通过。

## 7. 五个最重要的系统性根因

1. **质量权威分裂。** 路由、视觉场景、交互、OpenAPI、README 与 CI 口径分叉。修复后
   统一为 65/59/6 路由口径和唯一 `npm run check` 门禁。
2. **测试数据合同落后于生产内容模型。** 旧 fixture 在业务断言前失败。修复后统一
   frozen-release factory，原 52 个节点全部到达并通过业务断言。
3. **会话、请求 generation 和逻辑写操作缺少统一 owner。** 迟到 401、迟到响应与
   unknown outcome 被页面分散处理。修复后建立 session generation、请求生命周期和
   可恢复 operation attempt 合同。
4. **后端命令与事务边界不原子。** 同一答案可只落一条 verdict 却重复发 XP。修复后
   使用不可变 command identity、精确重放和单一事务 owner。
5. **异步任务缺少可执行部署合同。** producer、consumer、lease、recovery 与 readiness
   脱节。修复后加入 lease/fencing/recovery、部署 consumer 与合同测试；真实部署 E2E
   仍是边界。

最终渲染还确认了一个后续公共根因：宽度相对 `--rpx` 被同时当作物理触控下限和纵向
固定布局指标。它不改写上面五项原始根因排序，进入 Residual Wave R1。

## 8. 修复波次与下一步范围

| Wave | IDs | 结果 |
|---|---|---|
| Wave 0 | `ROOT-MIGRATION-001`、`ROOT-CONTRACT-001`、`ROOT-GATE-001`、`ROOT-TESTDATA-001` | automated verified |
| Wave 1 | `BACKEND-COMMAND-001`、`ROOT-JOBS-001`、`BACKEND-REPLAY-001`、`ROOT-AUTH-001`、`ROOT-IDEMP-CLIENT-001`、`ROOT-REQUEST-001` | automated verified；外部 E2E 边界保留 |
| Wave 2 | `FLOW-MOCK-001`、`STATE-ANAN-001` | logic verified；精确可见状态未渲染 |
| Wave 3 | `UI-CAPTURE-001`、`UI-PRACTICE-ANSWER-CARD-001` | five-profile real render verified |
| Wave 4 | `UI-ESSAY-TITLE-001` | five-profile real render verified |

第一修复波次的明确范围始终是 Wave 0 的四个可信基线问题：migration/revision authority、
OpenAPI 生成合同、统一路由/测试门禁和 canonical frozen-release 测试工厂。它不包含
业务 UI、状态机或新产品能力。

若之后开启下一次修复，唯一优先范围是：

### Residual Wave R1 — shared responsive physical metrics

- ID：`ROOT-RESPONSIVE-METRICS-001`
- 涉及：公共尺寸 token、公共/页面专用按钮、capture crop 弹性高度与 safe area；
- 影响：7 条路由、22 个唯一场景；
- 测试：small/long 全部主操作 `>=44px`，long h08 完整位于 viewport；五档全量真实
  渲染回归；
- 回滚：按公共 token 与 crop 布局两个独立提交回滚，不触碰 API、状态或数据合同；
- 并行：token owner 与 crop owner 可分工，但最终必须由单一 UI 集成负责人合并后跑
  五档门禁。

本轮不执行 R1。

## 9. 独立产品项

`PF-001` 与 `PF-002` 不进入原始 15 条 finding 或严重度数量：

- `PF-001`：低成本答后纠偏 MVP 已集成、自动门禁通过、五档真实渲染完成；真实备考
  效果、跨服务 E2E 和发布未验证。
- `PF-002`：收藏/标记分离、做题页动作层级、后端持久化、OpenAPI、客户端和收藏页已
  集成，自动门禁与五档真实渲染完成；真实双设备同步和发布未验证。

详情：
`audit/product-followups.md`。

## 10. 未覆盖范围与证据边界

- 无隔离 staging、真实微信登录、可重置测试账号和真实 provider；
- 未访问 production、真实 worker/queue、CDN/WAF、真实用户或支付；
- 未验证真实手机/平板硬件、真实网络性能、键盘/系统手势和完整无障碍；
- 五档证据是微信开发者工具真实渲染 + deterministic fixture，不是跨服务 E2E；
- 隔离 runtime 已本地化业务 API、字体、支持入口并安装请求及支付 guard；这是预防性
  证据，不是抓包，也不证明微信开发者工具自身零远程流量；
- `node_modules` 链接目标锁到正式工作树依赖目录，但 Git clean 不证明 ignored 依赖
  字节；依赖目录字节没有独立 Git 证明；
- 最终验证器证明验证时的 runtime 全树与 final source 加允许变换一致。较早 small/
  standard manifest 没有“捕获时全树 digest”，所以不能描述为每张图捕获瞬间的密码学
  快照；
- 未 push、未创建 PR、未部署，也未合并到用户当前脏主工作树。

最终 JSON/JSONL、证据路径、哈希、源码冻结和报告口径的独立一致性复核为 PASS，记录见
`audit/qa/final-repair-closure-validation.md`。

审计与已授权修复收口到此停止。
