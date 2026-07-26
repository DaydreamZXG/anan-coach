# 岸岸小程序 · Figma 接入全面审计报告

**日期**：2026-07-24　**分支**：`codex/ui-ux-remediation`（未提交 working tree，apps/wechat 范围 137 文件 +14411/−8291）
**基线**：`design/figma-delivery`（45 屏 PNG + React 上下文 + 令牌 + route-map）、`DESIGN_SPEC.md`、交付包 README「Fable 接入规则」
**方法**：全局基础核验 + 10 组并行分屏审计（45 屏全覆盖）+ 全部 P0 逐条人工复核。审计中途 Codex 做过一轮视觉修复（约 60 文件），本报告所有结论均以其停手后的当前代码**重验为准**。

---

## 一、总体结论

**最担心的事没有发生**：全库无 mock/演示数据泄漏、无 `setTimeout` 假流程（21 处逐一判定全部合法）、路由零死链（routes.ts ↔ app.json 58/58 一致）、typecheck 0 错、写操作幂等键体系完整、旧规范要求的「demo 兜底」已彻底清除。**工程骨架是健康的。**

**真正的问题是三类**：
1. **16 个 P0**：状态机漏渲染出口（白屏/静默）、设计屏未迁移、映射反转、数据丢失风险、平板布局破坏——全部经人工核验属实；
2. **页面成熟度断层**：一部分页面按交付包高保真接入（登录、资料库、plan-basis、day-adjustment、拍照搜题骨架），另一部分**根本没迁移**（weekly-plan、daily-settlement、申论三屏）；
3. **系统性欠账**：8+ 组页面自造第二套 Tailwind 色板、动效体系定义了却近乎零使用、约 38/57 页可点元素零按压反馈。

### 分组成熟度一览

| 分组（屏数） | 成熟度 | P0 | 概要 |
|---|---|---|---|
| 登录与建档（9） | ★★★★☆ | 1 | 像素还原度最高，深链恢复合规；平板 transform 破坏 |
| 私人资料库（4） | ★★★★☆ | 1 | 状态机真实、上传链路扎实；导入弹层盖死处理页 |
| 拍照搜题（11） | ★★★☆☆ | 3 | 状态机 10/11 真实可达、工程质量高；failed 白屏、多命中缺出口 |
| 今日+能力+tabBar（2+bar） | ★★★☆☆ | 1 | 结构对齐、动效最好；冲突提示无渲染出口、第二套色板 |
| 岸岸+我的（2） | ★★★☆☆ | 1 | 零假数据、入口齐；客服链路死、多处错接 |
| 岗位决策（6） | ★★★☆☆ | 0 | 幂等/状态机全对；数量失真、筛选降级、第二套色板 |
| 行测练习与复盘（4） | ★★☆☆☆ | 3 | API/幂等满分；summary 状态丢失、复盘缺题干、胶囊违规 |
| 计划组（4） | ★★☆☆☆ | 2 | plan-basis/day-adjustment 认真接了；weekly-plan/daily-settlement 未迁移 |
| 申论工作台（3） | ★☆☆☆☆ | 6 | **三屏全部未按交付包实现**，另有映射反转、无自动保存等真 bug |

---

## 二、P0 清单（16 条，全部人工核验）

### A. 白屏 / 静默失败（最高优先）

**P0-1　拍照搜题：AI 解析失败态白屏**
`pages/capture-import/index.wxml:182,191,207,241`——四个分支条件全部漏掉 `aiAnswer.state === 'failed'`（合同 `features/external-learning/contracts.ts:61` 明确有此状态；wxml 中 `failed` 出现 0 次）。AI 解题失败→整页空白死路。
修复：补 failed 分支（可复用 no-hit 屏 + 失败提示 + 重新申请按钮）。同类：`activeQuestionUnit` 为 null 时同样无分支可渲染（`capture-controller.ts:439` 允许 null）。

**P0-2　今日页：版本冲突/降级提示无渲染出口**
`features/planning/today-controller.ts:288-315` 发布「这份计划刚在别处改过…」「重新载入失败，仍显示上次确认的计划（问题编号 xx）」到 `capacityMessage`，且有旧数据时清空 `errorMessage`；但 `pages/today/index.wxml` 中 `capacityMessage` 绑定 0 处——用户毫无提示地看着过期计划。违反 DESIGN_SPEC §4。
修复：任务区上方渲染 capacityMessage（conflict/error 态）；同时清理已移交 day-adjustment 的不可达 capacity 编辑分支（`index.ts:220-307`）。

**P0-3　资料库：导入弹层盖死处理页**
`pages/private-knowledge/index.wxml:199` ui-sheet 常驻渲染；`index.ts:460-463` busy 时 `closeImportPanel` 直接 return；`index.ts:242` 上传置 busy 后不关面板——处理期间遮罩（z-70）盖住设计的全屏处理页，用户面对关不掉的静止面板疑似卡死。
修复：进入 `uploadAndProcess` 即 `importPanelOpen:false`，失败回填面板。

### B. 真实功能缺陷

**P0-4　申论：可信度映射反转**
`features/essay-workbench/presentation.ts:66`：`confidence === "medium" ? "中等可信" : "低可信"`——三值枚举漏了 `high` 分支，**评分最可信时显示「低可信」**。
修复：补 high→「较高可信」分支。

**P0-5　申论：作答正文零自动保存**
`pages/essay-task/index.ts:2470-2472` `markDraftChanged` 是空函数；正文仅存内存，提交时才持久化——小程序被系统回收即丢最多 10000 字。改写/迁移阶段有本地草稿（`index.ts:1472,1492`），唯独主作答没有。违反 §4/§7。
修复：输入防抖后按 userId+planTaskId 落地本地草稿。

**P0-6　我的：客服链路整体死亡**
`pages/me/index.wxml:107` 联系客服 → support 页 → `config/environment.ts:25` `supportGroupQrImageUrl: null` 永久占位（「客服群暂未开放」）；且原 `open-type="contact"` 原生客服按钮随 pages/about 删除，全仓 wxml 0 命中。既违反 §1「稍后开放死入口」，也是微信审核合规风险。
修复：service-row 恢复 `<button open-type="contact">`（微信后台需开通客服）。

### C. 设计状态机被破坏

**P0-7　行测：训练总结成"一次性状态"**
route-map 规定 13:2「本次训练总结」= reflection 路由 `summary` 状态，但 `pages/reflection/index.ts:67-74` `ReflectionPhase` 枚举无 summary；总结被放进 practice 页 result-workspace（`pages/practice/index.wxml:178`），跨页 navigateTo 迁移。交卷离开后经深链回 reflection 直接落错因表单，总结屏永不可回看。违反 README 规则 5。
修复：summary 作为 reflection 首相态（review.state=reflection_required 且未进错因表单时展示），数据 `presentXingceResult` 两页通用。

**P0-8　行测：错题复盘看不到题目**
`pages/reflection/index.wxml:48-63` 只渲染「第 N 题」+ 答案对比 + 3 个 fact；题干（`session.bundle.questions[].stemContent`）、共享材料入口、排除/存疑 chips（`session.answers[].eliminatedOptionIds/marked`）数据俱在却全未渲染。设计 14:2 明确要求完整题干 + 材料入口 + 4 枚事实 chips。
修复：复用 content-renderer 渲染题干、接 material-reader 收起态、从 session.answers 派生 chips。

**P0-9　拍照搜题：多命中屏缺「都不是」出口**
设计 06（44:2）底部有「都不是我要找的题？」直达未命中/AI 解题；实现 `index.wxml:207-239` 无此按钮，controller 强制选一个候选（`capture-controller.ts:335-337`）——用户可能误确认错题或只能整单重拍。
修复：补「都不是」→ 拒绝候选/降级 AI 解题（需后端拒绝命令支持）。

**P0-10　拍照搜题：候选卡无题面，无法消歧**
`index.wxml:220-232` 候选卡只有「原题精确匹配/相似题目」徽章 + 未格式化原始分数；设计每卡有题干预览+来源+「匹配度 99%」。根因：`MatchCandidate` 合同无题面字段（`api/external-learning-contracts.ts:123-128`）。
修复：合同补题面摘要字段；短期至少渲染 evidenceRefs 片段、分数格式化为百分比。

### D. 设计屏未迁移（结构级缺失）

**P0-11　申论三屏全部未按交付包实现**
essay-task drafting（17:2）无写作工作台结构、essay-material（29:2）无沉浸阅读器（段号/缩进/划线/工具条全无）、essay-review（30:2）无大分卡/证据 chips/表达升格，且**全页无一个按钮**——批改结果是死路，「再写一次/加入明日专项/问岸岸」出口全缺（`pages/essay-review/index.wxml` 全文）。三页仍是旧工作台表单风格。
修复：按 17:2/29:2/30:2 重写三页布局（controller/API 层保留，质量很高）。

**P0-12　weekly-plan：设计核心区块缺失**
标题仍是「这一周，只追三个能验证的变化」，但设计中部的「本周三个目标」卡（3 目标+验证状态签，`contexts/02-today-plan/02__node-10-2.tsx:78-218`）与「岸岸建议调整”AI 卡（双操作）整体缺失；顶部三 chips 被自创 summary 卡替换。`pages/weekly-plan/index.wxml` 全文 80 行无 goals 区块。
修复：按 10:2 补齐；无数据时按 §4 给真实空态而非删块。

**P0-13　daily-settlement：下半屏缺失 + CTA 改写**
「没完成的这一项」（含处置项）与「明天从哪里开始」两区块 0 命中；设计吸底「确认结算，结束今天」被改为三个内联按钮（`index.wxml:70-72`）。
修复：补吸底 footer 与两区块；处置命令无后端支持的部分与设计侧对齐裁剪，不做假按钮。

### E. 布局破坏

**P0-14　建档 4 页平板内容平移出屏**
`pages/onboarding/index.wxss:499-507`（另 preparation-experience:363、position-preference:343、profile/archive:277 同模式）：媒体查询把 `left:50%; width:560px; transform:translateX(-50%)` 同时套在 fixed 底栏和**静态流内容容器**上——静态元素忽略 left 但 transform 生效，内容左移 280px 半屏出界。
修复：内容容器改 `max-width:560px; margin:0 auto`（登录页 `login/index.wxss:176-181` 是正确示范），仅 fixed dock 保留 left/transform。

**P0-15　task-shell 顶部安全区违反胶囊铁律（3 个核心做题页）**
`task-runtime/task-shell/index.wxss:10,19` 仅用 `env(safe-area-inset-top)` 猜高度，全组件无 `getMenuButtonBoundingClientRect`；practice / mock-exam / essay-task 三页均 `navigationStyle:custom` 依赖它——开发者工具与部分安卓 `env()=0`，工具栏顶进状态栏；essay-task 头部右侧按钮更处在胶囊悬浮区被盖住不可点（设计上下文明确预留 `pr-[96px]`）。违反 §3；此前提交已为其余 29 页接 safe-top，唯 task-shell 漏改。
修复：task-shell 复用 safe-top 胶囊测量，头部右侧预留胶囊宽度。

**P0-16　收录成功屏"半假可达"**
11 状态可达性核验：10 个真实可达；recorded（49:2）仅「仅本次查看」纯本地 `setData({receiptVisible:true})`（`index.ts:315-317`）可达，两条真实收录路径直接 navigateTo 离开永不经过此屏；设计的「撤销收录/回到首页」无对应物。
修复：真实收录路径完成后落此屏；「撤销收录」若产品不支持则回流设计侧修改 49:2（README 规则 1），不得代码侧自行改写。

---

## 三、P1 系统性主题（代表性证据）

**T1　第二套色板（跨 8+ 组，最大视觉欠账）**
各页大量硬编码 Tailwind 色替代令牌：`#111827`(vs `--ink #101828`，四 Tab 现存 25 处)、`#6366f1/#4f46e5`(vs `--primary #615fff`)、`#16a34a/#22c55e/#059669`(vs `--good #00a63e`)、`#ef4444/#e11d48`(vs `--danger #fb2c36`)、`#f97316`(vs 交付警示 `#ff6900`)、`#667085/#98a2b3/#374151/#4b5563/#9ca3af`(vs ink 系)、`#06b6d4/#22d3ee`(vs cyan `#00d3f3/#00b8db`)；建档链路错误态自造 `#c70036/#fff1f2`（交付包 0 命中）；能力页 `#6d28d9` 实色替代设计的靛→紫渐变文字（`bg-clip-text from-[#4f39f6] to-[#9810fa]`）；岸岸 `#9333ea` 偏离 `--ai-violet #ad46ff`。违反 §2「不得自造第二套颜色」。
※ Codex 中途修复只完成了 app.wxss 别名合并与令牌导入链，**页面级硬编码基本未动**（复验确认）。
修复策略：建 sed 映射表批量替换 + stylelint 禁止页面 wxss 出现十六进制色值。

**T2　动效体系闲置（"友好感"最大缺口）**
`tap-tactile`/`shimmer-sweep`/`auroraScroll` 定义后全库 0 使用；约 38/57 页可点元素零反馈（`:active` 全库 0 条，capture-import 49 个 bindtap/0 反馈）；ui-sheet 与全部手写 mask 弹层 `wx:if` 生切无过渡；入场动画仅今日/能力两页。Figma Make 源码几乎每个按钮 `active:scale-[0.98]` + 抽屉 spring 上滑 + 每页入场 motion——设计有意图清单共 7 项（按压/弹层/入场/展开高度/tab 光晕/进度条过渡/高光扫过）。
修复策略：ui-sheet 加统一出入场（一处改全局收益）；主要按钮批量挂 `hover-class="item--hover"`。

**T3　错误/空态呈现断点**
我的页三路读取失败被 `Promise.allSettled` 静默吞掉伪装成空数据（`pages/me/index.ts:179-206`）；资料库检索空结果零反馈（`index.wxml:137`）、error 态漏传 request-id（组件支持、能力页有示范）；今日 opening 失败整块静默消失（`index.wxml:28` 仅 ready）；岸岸 context 失败后状态胶囊永远「正在读取」（`index.ts:48-51,133-143`）；无匹配任务空态缺「去今日计划」入口。
修复策略：以 state-view + request-id 为标准件逐页补齐。

**T4　入口错接/重复**
授权管理→通知设置（`me/index.wxml:106`，应 `wx.openSetting` 或独立页）；申论记录与模考成绩同指报告中心（`:95-96`）；能力页「查看怎么算的」→岗位工作台首页（`ability/index.ts:167-169`，语义错位）；整套退出登录/多设备管理已实现但无任何入口（`me/index.ts:352-459`）；资料库 exportMarkdown 等真实 API 死代码无入口。

**T5　交互降级于设计**
拍摄屏无 `<camera>` 取景器（伪快门实际拉系统相机，用户经历两层相机 UI）；裁剪四角手柄纯装饰不绑事件、真裁剪走 `wx.editImage`，直接确认会送原图 OCR；行测排除按钮从设计的 48px 独立图标列降级为 19rpx 灰字；岗位搜索筛选从四个下拉 pill 降级为三个自由文本输入；权限门屏时序偏离（进页直落拍摄、拒绝后经两层 modal 才见门屏）。

**T6　数量/口径失真**
岗位工作台 `limit:8` 却宣称「已筛出 N 个可报岗位」（`workspace-controller.ts:134` + `positions/index.wxml:50`）；申论字数分母用技术上限 10000 而非题目限字（`essay-task/index.wxml:319`）；计时 MM:SS 申论 90 分钟显示「96:34」；信心校准四档语义与设计不符（错题未按信心拆档，`presentation.ts:461-505`）；匹配度直出原始 number。

**T7　文案违规（§6 工程词/枚举直出）**
「问题 1 · high」severity 英文枚举直出（`essay-review/index.wxml:8`，severityLabel 映射已存在未用）；「对比地址必须包含 2–3 个非空且互不重复的岗位 ID；客户端不会去重、截断或补齐」（`compare-controller.ts:242`）；「当前行测题目与作答进度不一致」等工程腔。

**T8　lint 实质错误（~134 项中的风险子集）**
private-knowledge 两页 7 处 floating promises（异步失败静默）；模板拼接 `string|undefined` 可能渲染"undefined"给用户（`detail/index.ts:182`、`index.ts:611`）；scripts/*.mjs 与 vitest.config.ts 未纳入 lint project（配置缺口）。

**其他独立 P1**：建档入口页返回键"闪跳循环"（redirectTo 栈深 1 + 登录页自动 resume，`entry/index.ts:11-17` + `login/index.ts:48-52`）；day-adjustment 幂等键每次点击新建、409 未按冲突态处理（`index.ts:223-236`）；plan-basis 判断卡首条证据与标题重复渲染同一字符串（`index.ts:99-104`）；对比页 2 列 768rpx>750rpx 产生无意义横向微滚；材料页↔作答页 navigateTo 循环压栈逼近 10 层上限；平板双栏 1024px 阈值过高（iPad 竖屏 768px 落空，§7）；申论/材料页主 CTA 大面积靛蓝越权（§2 普通主操作应石墨黑）；建档 8 页零 token 化；7 页缺 ≥768px 限宽。

---

## 四、P2 摘要（修完 P0/P1 后统一打磨）

字符冒充图标（⌁/✦/↗/▥/⌖ 等，交付包 184 个 SVG 未充分使用）；徽章 12rpx 方角 vs 设计全圆角胶囊（系统性）；阴影 blur 普遍放大一倍；tabBar 未选中灰三处不一致（`#9ca3af/#9AA3AE/#99a1af`）+ `anan` 死样式钩子 + `z-index:40` 魔法数字；6 个非 tab 页误继承 200rpx 底部留白；positions/briefing 原生导航与簇内 custom 不一致；me 页 `type-data` 类无全局定义；weather attribution 已算好未渲染（供应商署名合规）；`coffeePrompt` 服务端文案被写死覆盖；岸岸 loading 骨架无 animation；membership 幂等键非稳定重放语义；「帮我看看」咖啡按钮点击弹「尚未启用」modal（§9 应关入口而非弹窗）；建档步骤按钮禁用策略不一致；onShow 不刷新（岸岸 context、详情页对比计数）；各组报告中另有 40+ 条细节，修复时按组领取。

---

## 五、Codex 中途修复状态（审计期间那一轮）

| 项 | 状态 |
|---|---|
| 我的页底部 tabBar 遮挡 | ✅ 已修（`--figma-bottom-navigation-height + space-32` 公式正确，导入链完好） |
| app.wxss 双套令牌定义 | ✅ 已合并为别名引用 |
| design-file.tokens.wxss 接入 | ✅ 已导入 design-system |
| onboarding/profile/anan 图标资产 | ✅ 已补一批 SVG |
| 页面级硬编码色（T1） | ❌ 基本未动（四 Tab 仍 25 处 `#111827` 等） |
| 全部 16 个 P0 | ❌ 一个未修 |

---

## 六、做得对的（修复时不得推倒）

- **零假数据纪律**：demo 兜底彻底清除；读取失败一律 error 态 + Request ID（「问题编号」）+ 重试；设计稿演示数据全部换真实绑定；后端无数据时诚实替代（倒计时→主目标、完成屏不伪造知识卡数量）并多处主动声明边界（「上传不等于已掌握 · 不计分」「分差不是录取概率」）。
- **写入链路工程质量高**：UUIDv4 幂等键经 `Idempotency-Key` header；行测/申论/拍照/岗位确认均有待决命令持久化重放、operationEpoch 防串台、409 走版本合并/编辑权；上传带 SHA-256 与真实分片进度。
- **路由治理**：唯一权威 + 白名单校验 + 任务深链参数强校验；旧页面/route-map.ts/endpoints.ts 删除零残留。
- **状态机大部分真实**：拍照 10/11、资料库 4/4、岗位 goal 2/2、reflection 相态由服务端 `review.state` 单一驱动。
- **登录建档链路**：光晕逐值还原、深链恢复（returnTo 校验+按用户隔离暂存+result 页恢复原目的页）教科书级。
- **合规与隔离**：隐私页数据清单/导出/注销确认在；数据按 learner ID 隔离；safe-top（除 task-shell 外）读真实胶囊。

---

## 七、修复路线图

**第一层（1-2 天）——功能硬伤**：P0-1~6（白屏/静默/反转/丢稿/客服）+ T8 风险 lint；
**第二层——设计覆盖**：P0-11 申论三屏 → P0-12/13 计划两页 → P0-7/8 行测状态机 → P0-9/10/16 拍照 → P0-14/15 布局；
**第三层——体验**：T2 动效（ui-sheet 全局过渡 + hover-class 批量）→ T1 色板批量替换 → T3 错误/空态 → T4 错接 → T5/T6/T7；
**第四层——防回归**：eslint 清零入门禁；stylelint 禁页面级十六进制色；pixelmatch 截图 vs PNG 基线对照（依赖已装）；route-map routeState 可达性测试（可拦 P0-7 类问题）；`npm run check` 纳入合入流程。

---

*审计执行：Claude（Fable 5），10 个分组审计代理 + 全局/横切人工核验；45/45 屏全覆盖，16 个 P0 全部逐条人工复核于 Codex 停手后的代码状态。*
