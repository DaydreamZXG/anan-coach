# 高概率问题与假设清单

以下条目没有进入 `findings.jsonl`，因为只有静态证据、缺少当前 commit 的真实运行证据，
或需要产品/边缘基础设施信息才能判断。它们不计入 P0/P1/P2/P3 数量。

## H-R001：私人知识详情冷启动缺参时可能无法安全返回

- 置信度：medium
- 静态证据：`pages/private-knowledge/detail/index.ts` 缺少 `id` 时直接
  `wx.navigateBack()`，之后页面仍可进入 `onShow -> loadDetails()`。
- 风险：若该 URL 是 reLaunch 的唯一栈页，navigateBack 无上一页；页面可能继续以空
  `sourceId` 加载。
- 未证实原因：当前真实渲染场景使用合法参数，没有执行冷启动缺参栈。
- 待验证：登录后
  `reLaunch("/pages/private-knowledge/detail/index")`，记录最终路由、栈深度、toast、
  请求与页面状态。

## H-R002：9 个业务路由可能缺少直接用户入口

- 置信度：medium。
- 静态扫描未找到直接入站引用：
  `retake`、`membership`、`support`、`report-overview`、`global-report`、
  `community-draft`、`community-trial`、`community-contributions`、
  `onboarding/ability-baseline`。
- 未证实原因：它们都有 typed route、视觉场景和交互合同，入口可能来自复合导航函数、
  后端下发 URL 或动态映射；真实 page-ready 也不能证明用户路径可达。
- 待验证：在非生产端到端环境采集所有 navigate/reLaunch 事件与服务端动作 URL。

## H-C001：Hub Bot 手写响应合同可能继续偏离 OpenAPI

- 置信度：medium。
- 静态证据：当前 OpenAPI 新增 Hub Bot authorization view 和四个 operation，活跃组件
  仍经 `api/ilink-channel.ts` 使用手写合同；部分可选/nullable 语义不同。
- 未证实原因：当前组件使用方式不必然崩溃，且本轮禁止请求唯一的生产 API。
- 待验证：以隔离 contract fixture 分别返回字段缺失、null、完整响应。

## H-C002：缺少运行时 response validation 可能把合同错误推迟到页面

- 置信度：medium。
- 静态证据：`ApiClient<T>` 只有编译期泛型；6 个 endpoint wrapper 使用手写合同，
  生成类型路径同样没有统一运行时 schema 校验。
- 未证实原因：没有获得违反合同的非生产真实响应。
- 待验证：对高价值 envelope 注入缺字段、错误枚举和错误 error envelope，确认 fail
  closed 行为。

## H-A001：匿名知识接口返回答案/解析是否违反产品权限政策

- 置信度：技术暴露 high；Bug/政策判断未定。
- 已确认事实：无 bearer 请求
  `/api/v1/knowledge/questions/search` 与 detail 可返回 `answer`，detail 还返回
  explanation/provenance；既有 contract test 明确接受这一行为。
- 未进入正式 finding 的原因：当前仓库没有“该 corpus 必须私有/答案仅提交后可见”的
  产品或安全政策；不能把审计者偏好当 Bug。
- 潜在影响：若这些是受治理题库，匿名批量枚举会绕过答题时序并暴露内容资产。
- 待决策：产品/安全 owner 明确 public corpus policy；若确认为 authorization/security
  缺陷，按用户严重度定义应以 P0 进入正式台账，而不是任意降为 P1。
- 证据：`audit/subagents/e/evidence/fastapi-route-auth-inventory.csv`、
  `audit/subagents/e/evidence/anonymous-content-contract-tests.log`。

## H-W001：更多 fresh-key 写操作可能存在未知结果重试问题

- 置信度：medium。
- 静态候选：ability-module 策略候选/确认、iLink Bot 授权/撤销、隐私导出/注销、
  私人资料 OCR/删除、外部成绩 OCR/确认、部分申论命令。
- 未证实原因：服务端可能还有业务键、CAS 或 successor replay；不能只凭前端新建 key
  认定重复写。
- 待验证：逐操作执行“服务端已提交、响应丢失、同输入重试”，检查写入、额度、外部调用
  和返回语义。

## H-W002：刷题内岸岸可能复现主页岸岸的重复计额风险

- 置信度：high。
- 静态证据：`pages/practice/index.ts` 对每次发问新建 `practice-anan` key，并调用同一
  `/api/v2/companion/ask` 合同。
- 未证实原因：本轮动态用例只在岸岸主页完整复现；刷题上下文需要合法 session/task
  fixture。
- 待验证：第一次 provider command 可能已提交但响应丢失，再次提交同一问题，比较
  body、key、quota 和 provider ledger。

## H-S001：账号切换时可能短暂显示前一账号的迟到响应

- 置信度：low。
- 静态证据：公共请求不可取消，多个页面没有 session owner/generation。
- 未证实原因：页面 unload/reLaunch 的微信运行时回收时序会影响旧 setData 是否可见。
- 待验证：staging 用两个审计账号，延迟 A 响应，登录 B 后释放 A 响应并录屏。

## H-P001：认证 last_seen 写入可能成为数据库热点

- 置信度：medium。
- 静态证据：每个成功 bearer request 都更新并 commit
  `learner.app_sessions.last_seen_at`；路由清点中 225/246 具有 bearer dependency。
- 未证实原因：没有并发、锁等待、WAL 或 p95/p99 数据。
- 待验证：单 token/多 token 的代表性压测，采集 transaction、row-lock、WAL、连接池。

## H-P002：实际边缘层全局限流覆盖未知

- 置信度：low。
- 仓库没有统一应用限流，Caddy 配置也未显示全局策略；部分接口有自己的 quota/signature。
- 未证实原因：真实 CDN/WAF/微信网关策略不在仓库，本轮不检查生产。
- 待验证：读取授权的实际 edge policy，并在非生产环境执行 burst test。

## H-S002：metrics token 普通字符串比较的实际时序风险未知

- 置信度：low。
- 代码证据：`/internal/metrics` 使用普通 `!=`，内部 runtime 使用
  `hmac.compare_digest`。
- 未证实原因：没有远程 timing attack 证据，网络抖动通常远大于信号。
- 待验证：先确认网络可达性与 token entropy，再做威胁建模。

## H-T001：其他 repository-owned commit 可能存在部分写入

- 置信度：medium。
- 静态证据：54 个 domain 文件包含显式 `commit()`；已确认的 verdict/XP 路径证明该
  模式可能破坏用例原子性。
- 未证实原因：部分 commit 是合法 worker checkpoint，不能把 54 个文件都当缺陷。
- 待验证：优先 commerce、entitlement、essay grading、account deletion、upload，
  在每个 durable step 后注入失败并验证不变量。

## H-UI001：`scope.camera` 的无效 permission 声明可能影响真实设备授权或审核

- 置信度：medium。
- 真实运行证据：微信开发者工具控制台记录
  `无效的 app.json permission["scope.camera"]`；声明位于
  `apps/wechat/miniprogram/app.json:167`。
- 未进入正式 finding 的原因：模拟器“未找到摄像头”不能证明真实手机的首次授权、
  系统设置往返或平台审核已经失败。
- 待验证：在隔离测试账号的真实 iOS/Android 设备执行首次授权、拒绝、再次请求、
  系统设置开启后返回和提审静态检查。

## H-UI002：组件 WXSS 中被禁止的 tag selector 可能在部分组件丢失控件居中规则

- 置信度：medium。
- 真实运行证据：DevTools 警告
  `Some selectors are not allowed in component wxss`；导入链最终指向
  `design-system/primitives/interaction.wxss:15` 的 `button.control-center`。
- 未进入正式 finding 的原因：三档真实截图没有观察到 workbench-nav 或已抽样控件
  样式缺失；不能只凭 warning 推断所有组件都受影响。
- 待验证：清点导入该 primitive 且实际消费 `.control-center` 的自定义组件，并在真实
  DevTools 中比较规则生效/被忽略后的几何与垂直居中。

详细来源：

- `audit/subagents/a/route-evidence.md`
- `audit/subagents/c/report.md`
- `audit/subagents/d/hypotheses.md`
- `audit/subagents/e/hypotheses.md`
