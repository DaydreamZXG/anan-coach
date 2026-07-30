# 岸岸小程序值班与客服手册

本手册只描述生产操作。任何人不得在数据库里手工改订单、退款、权益、删除任务或支付通知状态。

## 发布与回滚

1. CI 必须通过 `scripts/full_verify.sh`，并生成带 SHA-256 的不可变制品；调用者必须从受保护流水线传入精确 `EXPECTED_GIT_COMMIT`。
2. 每个可发布、可激活或可回滚制品都必须包含不可变的 `ops/commercial-data-publication-guard.json`，并由离线签名的 release manifest v4 `release-authority/release.json(.sig)` 通过 `guardCapabilities.commercialDataPublication` 精确绑定 marker 路径、SHA-256 与 `commercial-data-publication-guard@v1`。脚本同时复验 manifest 签名、schema、marker 内容和磁盘摘要；只有旧式签名 manifest 而没有该能力契约的 legacy 制品仍不合格，不能激活、回滚、获授 rollback eligibility 或在失败恢复中重启。不得由部署机自签、猜测能力或跳过复验。
3. 商业数据与应用切换是两个严格排序的发布单元。先从精确、干净的候选 commit 运行 `ops/scripts/publish_commercial_data_release.sh`：它验签当前应用和全局数据 pointer、确认当前应用声明兼容候选 migration，把精确 request 以 `publishing` 持久化，再用固定 PostgreSQL advisory key 取得 session-level exclusive lock。锁会话本身会被复制为不可由 ready 文件伪造的继承 fd 8，锁获取进程随后以同一 PID `exec` 进入受控 mutation controller；所有 `sudo` mutation 都保留 fd 8 和全局文件锁 fd 9。controller 被 kill 时外层脚本只能失败退出，不能继续下一项 mutation；已经在途的子命令则继续携带原 PostgreSQL 会话，直到该项写入结束前 exclusive lock 都不会提前释放。API 与所有 worker 的每次数据库 checkout 都持对应 shared lock，并只接受一行、`active`、request digest/receipt digest/数据库 revision 完全自洽且同时匹配本进程 `APP_ENV`、`APP_GIT_COMMIT`、`APP_DATA_RECEIPT_SHA256` 的权威；因此 publisher 会先排空既有 checkout，新的 checkout 在 `publishing`、缺表、空表、未知状态、摘要或应用—数据配对不符时失败关闭。exclusive lock 横跨 forward-only migration、职位发布、知识图谱发布、同库回执捕获和签名、全局 pointer 原子推进以及 DB `active` 完成转换。职位、知识图谱、migration 和回执只允许使用 `MIGRATION_DATABASE_URL` 指向的同一 cluster/database；配置第二个 `GONGKAO_KNOWLEDGE_MAP_DATABASE_URL` 会在任何业务数据写入前被拒绝。
4. `MIGRATION_DATABASE_URL` 必须是无密码的显式 libpq URL，唯一绑定 host、port、database 和 user；不得使用 socket 默认值、`hostaddr`、service、options 或 URL 内密码。密码只放在 migration OS 用户拥有的绝对路径 mode `0600` `MIGRATION_PGPASSFILE`。只有 migration credential 可以写 publication singleton/transition ledger；API/worker role 对表只有 `SELECT`、对 transition sequence 无权限，且默认授权也必须显式撤销。自定义 writer GUC 只是 migration 路径内的协议断言，不是数据库身份或授权边界，绝不能靠设置 GUC 绕过 ACL。`api.env`、`db.env`、各 worker 专属 env、窗口 manifest、信任公钥和环境标识必须是 root 所有、非软链且 group/other 不可写。
5. 数据发布成功后，把全局当前的 detached data receipt 及签名作为 `DATA_RELEASE_RECEIPT`/`DATA_RELEASE_RECEIPT_SIGNATURE` 交给 `ops/scripts/release_staging.sh`。脚本使用受控 `RELEASE_SIGNING_PRIVATE_KEY` 对应用 manifest 签名。微信证据只能全部提供或全部省略：`WECHAT_UPLOAD_RECEIPT`、`WECHAT_UPLOAD_RECEIPT_SIGNATURE`、`WECHAT_UPLOAD_INFO_OUTPUT`、`WECHAT_UPLOAD_RAW_LOG` 和 `WECHAT_UPLOADER_TRUST_PUBLIC_KEY` 缺一即拒绝；远端还必须使用 root 管理的 `STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY` 复验 detached receipt 签名。远端和 `activate_release.sh` 都会用部署机外置公钥、钉住摘要的 verifier、精确 commit、制品摘要及完整 manifest 复验；激活端在解压前再次复验，避免调用端验签后的替换窗口。release 与 uploader 签名私钥都不得复制到部署机。
6. `activate_release.sh` 不执行 migration 或业务数据写入。它取得同一全局锁后必须重新解析 `current`，并重新验证当前 release id、签名 manifest、commit、环境和数据库兼容性；若等待锁期间另一个发布已切换 `current`，本次操作必须失败，不能继续使用锁外缓存。随后它要求候选 receipt 与全局当前数据权威逐字一致，以当前数据库实时重算职位/知识图谱证据并断言 DB 中精确的 receipt/environment/revision/两个 compatible commits 配对，才安装控制面和切换应用。切换后、重启任何 runtime 前，以原子替换方式写入 root 管理的只读 `/etc/gongkao-dazi/release-runtime.env`；失败恢复时也必须先恢复旧 commit 与同一 active receipt 的三元组。当前周期合法关闭时 receipt 记录 `lifecycleState=closed` 且 `currentBusinessWindow=null`，不得伪造开放报名窗口。
7. publisher 的 request digest 精确绑定 candidate/rollback commit、环境、预期 revision、职位 rule/data/year、窗口 manifest SHA-256，以及知识 release/source/freshness。失败或进程被 kill 不会自动写 `active`，数据库保持 `publishing`。只可用逐字相同输入重跑：pointer 仍指向旧 receipt 时重做 publication；pointer 已指向该 request 已记录的新签名 receipt 时，只复验签名和 live 数据并完成 DB `active`；相同 request 已 `active` 时复验后 no-op。不同 request 一律拒绝。不得因重新 capture 产生新的 `observedAt`，把 pointer 已推进的发布冒充成另一次幂等发布；不得手工改 pointer、手工签 receipt 或单独重跑知识/职位 mutation。
8. `/ready` 只在数据库版本精确等于制品 `REQUIRED_DATABASE_REVISION`，必需表存在，已启用 worker 的完整 job 拓扑可消费，且所需外部 AI 能力有可运行 release 时返回成功。
9. 应用故障只可执行当前控制器中的 `ops/scripts/rollback_release.sh <release-id>`。取得全局锁后必须重新解析 `current` 和目标，重新验证两个签名 manifest、两端 guard capability、rollback marker、两个 commit、环境、live revision 兼容性及 DB 中的精确应用—数据配对；等待者发现 `current` 已由先行操作改变时必须失败。选择 marker 时必须先找目标下精确的 `rollback-eligible/<current-release-id>.json`；只有该文件不存在时才可使用目标自己的成功 activation marker，且无论哪一种 marker，其 `dataReceiptSha256` 都必须等于当前全局 receipt。eligibility 存在但被篡改、授权者不符或 receipt 过期时必须失败，不能回退 activation。目标与当前签名 manifest 的 commit 都必须被全局数据 receipt 允许，且目标制品声明兼容 live revision。数据库不做破坏性降级；不满足任一条件时采用 forward-fix。

### 微信上传 v2 回执与工具钉住

1. 生产上传只接受 `identityMode=macOS-codesign`。先在隔离的 macOS 上传机安装人工核准的官方微信开发者工具，执行 `/usr/bin/codesign --verify --deep --strict --verbose=2 <bundlePath>`，再由双人复核冻结 bundle identifier、short/bundle version、TeamIdentifier，以及下列三个真实文件的 SHA-256：`Contents/MacOS/cli`、`Contents/MacOS/cli.js`、`Contents/Resources/app.asar.unpacked/js/common/cli/index.js`。工具升级、热更新或任一摘要变化都必须重新核准 policy，不能沿用旧值或根据当前机器自动信任。
2. policy 必须是 schema `anan-wechat-devtools-policy/v1` 的严格 JSON，仅含 `schemaVersion`、绝对 `bundlePath`、`bundleIdentifier`、`shortVersion`、`bundleVersion`、`teamIdentifier`、值为 `macOS-codesign` 的 `identityMode`，以及 `files` 中 `cli`、`cliJs`、`cliModule` 各自的精确 `relativePath` 和小写 SHA-256。将复核后的 policy 安装为例如 `/etc/gongkao-dazi/wechat-devtools-policy.json`，所有者 `root:wheel`、mode `0644` 或更严，且父目录不可由上传账户、group 或 other 写入。不得在生产使用隐藏的 `--allow-test-tool-identity`。
3. uploader RSA 私钥由独立上传签名账户持有、mode `0600`，不得提交仓库或复制到部署机；对应公钥以 `root:root`、非软链、group/other 不可写的方式安装到 staging 主机，例如 `/etc/gongkao-dazi/wechat-uploader-trust.pem`。发布配置的 `STAGING_WECHAT_UPLOADER_TRUST_PUBLIC_KEY` 必须指向这份独立信任锚，不能从 receipt 自带或临时生成。
4. 从精确、干净且等于候选 commit 的仓库根目录执行以下命令；脚本只把该 commit 的 `apps/wechat` 私有 `git archive` 交给官方 CLI，不读取工作树产物：

   ```sh
   python3 ops/scripts/upload_wechat_release.py \
     --project-root "$PWD" \
     --expected-git-commit "<full-candidate-commit>" \
     --expected-app-id "<approved-wx-appid>" \
     --upload-version "<approved-version>" \
     --description "<approved-description>" \
     --devtools-cli "<bundlePath>/Contents/MacOS/cli" \
     --tool-policy /etc/gongkao-dazi/wechat-devtools-policy.json \
     --signing-private-key /secure/wechat-uploader-private.pem \
     --output-prefix /secure/evidence/wechat-upload-<release-id>
   ```

   成功后必须同时得到 `.json`、`.json.sig`、`.info.json`、`.log` 四个文件；receipt v2 绑定 commit/tree/listing、AppID/project config、版本与描述、CLI info/log 摘要、codesign 身份、三个工具文件摘要、policy 摘要和 uploader key id。现存任一输出都会拒绝覆盖；异常终止若留下部分文件，应整组隔离留证并换用新的空 output prefix，不能删除后把同一前缀当作幂等重试。
5. `cli upload` 成功只证明该版本上传请求被开发者工具接受，不证明微信体验版切换、审核或正式发布完成。manifest v4 在缺少完整微信证据时只能是 candidate/incomplete；旧 v1、无签名、部分证据、自签公钥或任一字节篡改都不能成为 deployable。体验版/审核/正式版的控制面证据仍是外部门禁，必须另外人工复核并归档。

### 商业数据权威首次采用（签名 bootstrap 桥接）

1. 这是一次停服桥接。先在隔离主机用 `data_release_authority.py bootstrap-capture` 对迁移前 live database 捕获 `anan-data-bootstrap-baseline/v1`，再用离线 data-release 私钥签名，并以 receipt SHA-256 为目录名安装到 `/etc/gongkao-dazi/data-releases/revisions/<sha256>/receipt.json(.sig)`；`current` 只可原子指向该目录。bootstrap 精确绑定环境、database name、cluster system identifier 的哈希、旧 Alembic revision、当前应用 commit，以及当时 active 的职位/知识版本和可重算内容摘要。部署机不得持有离线签名私钥的长期副本，不能用普通 data receipt、空 receipt、占位摘要或当前时间伪造 baseline。
2. 在负载均衡/反向代理停止新流量后，停止 `gongkao-dazi-api.service` 以及所有 `gongkao-dazi-*-worker.service`（包括 cause-agent、coach-notification、commerce、data-rights、essay-review、external-learning、ilink）；核对进程和数据库会话均已排空。保持所有 unit 停止，离线安装一个已签名且含上述 guard capability 的 bridge release，复验后原子把应用 `current` 指向 bridge，但绝不在此时启动它。无 guard 的 legacy current 不得直接进入 publisher；也不得为 legacy 写 eligibility 或在失败路径重启 legacy。bridge 必须由新数据 receipt 声明兼容，才能在候选激活失败时成为合法恢复目标。
3. 保持停服，人工复核入口、bridge pointer 和上述每个 unit 后，仅对这次首次采用设置 `COMMERCIAL_DATA_BOOTSTRAP_ACK=traffic-and-all-workers-stopped`，再从精确候选 checkout 运行一次 publisher。publisher 会验签 bridge manifest 并核对其 guard marker，还会逐项拒绝任何仍 active 的 API/worker/openilink unit；ack 不能替代人工核对反向代理和数据库会话。它在一个 PostgreSQL `REPEATABLE READ` 事务和 transaction-level advisory lock 内，对同一连接复验签名 bootstrap 与 live DB、从旧 revision 升级到候选 head，并插入 exact durable `publishing` singleton/transition；升级和首次 `publishing` 不存在可观察的提交缝隙。
4. 若进程在升级后、首次状态插入前被强制终止，整个事务回滚到原旧 revision，publication 表也不会残留；保持 API、全部 worker 和入口流量停止，保存日志和 pointer，使用同一份签名 bootstrap、同一 candidate/rollback commit 及完全相同的数据输入重跑。若事务已提交为 `publishing`，则按普通 exact-resume 规则恢复；不同 bootstrap 或 request 不能接管。
5. 只有在全局 receipt 验签和 live 校验通过、DB singleton 为 `active`、request/publication/active receipt 摘要一致、revision 精确为候选 head，且 `assert-pairing` 证明 receipt SHA、环境和恰好两个 application commits 完全匹配后，才激活包含 checkout guard 的应用制品。随后把 API 与六个已注册 worker 作为同一个 runtime set 启动；`/health` 只可用于确认 API 进程已监听，不能作为发布就绪结论。不得在 worker 启动前等待聚合 `/ready`，因为 mandatory consumer heartbeat 本来就是 `/ready` 的输入。待六个 worker 均产生与自身 principal 匹配的新鲜 heartbeat 后，再验证聚合 `/ready` 并恢复入口流量。任一检查失败继续停服并采用 exact resume 或 forward-fix，不得绕过 guard。

### Worker heartbeat 与 job 注册权威（迁移 84、86）

1. 迁移账户不应拥有 `CREATEROLE`。DBA 必须先在目标 cluster 执行经复核且幂等的 `ops/sql/provision_worker_heartbeat_roles.sql`；脚本保留迁移 84 所需的 legacy pair，并为 data-rights、cause-agent、external-learning、essay-review、coach-notification、ilink 分别建立独立的无特权 `NOLOGIN` group 和 `LOGIN`，不设置密码。六个 LOGIN 必须各自只继承对应 group，group 不得继承其他角色；密码或客户端证书必须由数据库秘密管理流程逐一配置且不得复用。迁移 86 会核验全部角色属性和精确 membership，并拒绝 API、legacy login 或任一 worker 跨组；缺失、提权或多余 membership 一律 fail-fast。
2. 升级前查询 `platform.background_jobs` 的 distinct `job_type`，并逐项与候选制品中的 13 类 registry 对照。迁移 84 遇到未注册历史行会完整回滚并列出类型；不得删除、改名或把任务伪装成已知类型。先审计 producer、payload、状态和恢复责任人，再用新的 forward migration 正式注册，或按获批的数据处置流程处理后重试。
3. 首次安装 unit 前，把 `ops/sysusers.d/gongkao-dazi-workers.conf` 以 `root:root`、mode `0644` 安装到 `/etc/sysusers.d/`，再以 root 执行 `systemd-sysusers /etc/sysusers.d/gongkao-dazi-workers.conf`。核对 data-rights、cause-agent、external-learning、essay-review、coach-notification、ilink 六个账号的 UID 两两不同、shell 为 nologin，且没有一个 UID 等于 API 的 `deploy`。候选 release tree 与 venv 必须由可信部署主体拥有、对这些 runtime 账号只读且可遍历；任何 worker 账号对 release、`/etc/gongkao-dazi` 或兄弟 worker 配置有写权限都阻断发布。
4. 将 `ops/worker-{data-rights,cause-agent,external-learning,essay-review,coach-notification}.env.example` 和 `ops/ilink-{channel,worker}.env.example` 分别安装为 `/etc/gongkao-dazi/` 下去掉 `.example` 的文件，全部设置为 `root:root`、mode `0600`。每份文件只能填入该 worker 所需的 workload/provider secret；TOS、AI、scanner、renderer 与微信凭据按可用的上游权限分别签发，不能复制整份 `api.env` 或复用兄弟 worker credential。六个 worker unit 都不得加载 `api.env`；缺少自己的 workload env 必须在进程启动前失败。
5. 将六份 `ops/worker-heartbeat-*.env.example` 分别安装为同名的 `/etc/gongkao-dazi/worker-heartbeat-*.env`（去掉 `.example`），逐份填入示例声明的唯一 LOGIN URL，并全部设置为 `root:root`、mode `0600`。每个 systemd worker 只能加载自己的文件；API 和其他 worker 不得加载该文件。生产 worker 缺少 URL、复用 API/兄弟 worker URL 或 username 与自身 unit/entrypoint 绑定不符时会拒绝启动。
6. 每个 heartbeat LOGIN 不得获得 heartbeat、registry 或 job 表 DML，只可通过自身 group 继承 `platform.pulse_background_job_consumer_heartbeat(text,text,uuid)` 的 `EXECUTE`。迁移 86 冻结 `session_user → group → unit/entrypoint` 映射；SECURITY DEFINER 函数先按真实 `session_user` 取唯一绑定，再比较请求参数，并以数据库 `statement_timestamp()` 写入。跨 unit/entrypoint、未知 principal、legacy shared login、API 与 `PUBLIC` 全部在数据库层拒绝。任何共享密码、共享 env、跨组 membership、直接写 heartbeat 或放宽函数 ACL 都属于发布阻断。
7. 以同一次 `systemctl start/restart` 提交 API 与目标制品清单中的六个 worker；不要先等待 `/ready` 再启动 worker。启动后同时验证 `/ready` blackbox probe、`anan_background_job_registry_exact=1`、`anan_background_jobs_unregistered_active=0`，以及每个 enabled/deployed job type 的 `anan_job_consumer_heartbeat_healthy=1`。registry exact 同时核验六行 principal 映射、角色属性、精确 membership、函数 ACL 和无表写权限；必需 worker 即使空队列也必须有新鲜 heartbeat。conditional worker 关闭且空队列时豁免，但关闭状态存在 backlog 必须报警。

## 支付与退款

- **当前状态（2026-07-18）：免费冷启动。** 生产必须设置 `COMMERCIAL_MODE=free_cold_start`，不得配置四个 `WECHAT_VIRTUAL_PAY_*` 密钥，`gongkao-dazi-commerce-worker` 必须保持 disabled/inactive；客户端不得提供购买动作。服务端目录返回空商品，即使伪造客户端订单请求也必须拒绝。以下内容仅在产品负责人重新启用支付后作为上线门禁执行。
- 免费冷启动同时设置 `USAGE_ACCESS_MODE=unlimited`。系统继续记录实际调用次数用于成本、安全和滥用识别，但不得按会员日额度拒绝正常用户；反爬、并发、恶意文件和账号共享控制独立生效。
- 重新收费必须作为独立发布：先完成商品与规则审核，再同时设置 `COMMERCIAL_MODE=paid` 和全部虚拟支付密钥；仅配置密钥不会启动商业对账 worker。
- 固定 30 天会员属于虚拟商品，生产只允许微信小程序虚拟支付。API 与商业对账 worker 必须同时配置 `WECHAT_VIRTUAL_PAY_ENV=0`、`WECHAT_VIRTUAL_PAY_OFFER_ID`、`WECHAT_VIRTUAL_PAY_APP_KEY`、`WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN`、`WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY`；密钥不得提交到仓库。
- 微信后台商品 ID 必须为 `founder_experience_30d`，价格必须为 6900 分。消息推送采用 JSON、安全模式，地址为 `https://kg.xingguoxian.cn/api/v2/commerce/wechat/virtual-payment/events`。
- 小程序虚拟支付上线前必须按当时微信平台政策重新核对客户端版本、类目、商品、退款和审核要求；当前免费冷启动状态不得提供购买或退款入口。
- `WechatPaymentOutcomeUncertain` 报警：确认商业对账 worker 存活和微信 xpay API 连通性。worker 必须按 `out_trade_no` 查询后推进状态，禁止重新创建同一逻辑订单。
- `WechatRefundStale` 报警：按支付订单调用 xpay 查单核对退款结果；结果不明时保持原状态，不得再生成退款意图。
- 客户端的 `requestVirtualPayment:ok` 不是到账依据，只有安全模式加密推送或 xpay 主动查单能发放权益。
- 30 天“不合适”退款与成绩无关，客服不得要求提分证明、任务完成率或访谈。

## 客服数据权限

- **当前状态（2026-07-18）：群二维码占位。** 小程序只展示尚未开放的客服群入口；配置受控二维码后才能开放预览。企业 IdP 客服后台保持关闭。
- 客服不得拥有生产数据库账号，也不得使用 `/internal/metrics` 的运维密钥查用户数据。
- 只能使用企业 IdP 签发的 RS256 JWT，且 token 必须包含 `commerce_support` 角色。
- 查订单必须提供 `X-Support-Ticket` 和 `X-Support-Reason`。接口只返回订单/退款状态、金额和时间，不返回 `user_id`、微信 OpenID、学习记录或付款时条款快照。
- 每次读取都写入 `privacy.support_access_audit_events`，审计事件不可更改或删除。

## 个人数据任务

- `DataRightsJobFailed` 报警必须当班处理。先修复对象存储或数据库故障，再由任务系统按原 `job_key` 重试；不手工伪造 `completed`。
- 删除任务会先冻结登录，再删对象文件、擦除原始表达/派生画像/AI 摘要，最后删身份映射和会话。只保留不含用户内容的法定账务和安全审计事实。

## AI 督学订阅消息

- 普通小程序订阅模板按微信“一次授权、一次可发送”处理。`accept` 每次只增加一条可消费授权；不得把一次授权展示成永久订阅。
- 启动 `gongkao-dazi-coach-notification-worker` 前，必须在微信公众平台发布真实模板，并配置精确 `WECHAT_COACH_TEMPLATE_RELEASE_JSON`、小程序密钥和目标版本 `formal`。缺少任一项时 worker 必须拒绝启动。
- 首次发布步骤 7 时，先将制品内 `ops/systemd/gongkao-dazi-coach-notification-worker.service` 安装到 `/etc/systemd/system/`，执行 `systemctl daemon-reload && systemctl enable gongkao-dazi-coach-notification-worker`；后续发布与回滚脚本仅在目标制品包含该 worker 时重启它。
- 微信消息只使用通用标题、提醒时间和“打开计划”动作，不写岗位、资格、分数、错题或私人资料。模板字段变化必须发布新的 `releaseId` 并通过合同测试。
- 用户在小程序关闭全部督学后，服务端立即取消未发送投递并作废剩余授权；微信设置页只用于同步平台侧选择，不能替代服务端关闭。
- `platform_accepted` 只表示微信接口受理；`opened` 与任务 `complete` 分开记录。发送请求出现网络中断时记为 `outcome_unknown`，不得自动重发造成重复通知。
- 告警至少覆盖 `outcome_unknown`、连续 `platform_rejected`、可用授权耗尽率和 worker stale claim；不得在日志中输出 OpenID、模板载荷或学习事实。

## 备份与恢复

- **当前状态（2026-07-19）：工程合同已完成，生产仍暂缓启用。** `BACKUP_ENABLED=false`，`gongkao-dazi-backup.timer` 必须保持 disabled/inactive；没有真实备份制品和签名恢复报告前，不宣称有生产恢复保障。
- 启用前由发布/回滚控制器通过唯一 `ops/sysusers.d/gongkao-dazi-workers.conf` 建立并复核不可登录、且 UID 不同于 `deploy` 和所有 worker 的 `gongkao-backup` 独立身份；同一控制器用 root-owned `ops/tmpfiles.d/gongkao-dazi-backup.conf` 建立由该身份拥有的 `/var/lib/gongkao-backup/metrics`，node_exporter 必须额外配置读取这个目录，不能假设 `ReadWritePaths=` 会授予 Unix DAC 写权限。备份仍使用现有 `gongkao` 数据库角色；独立最小权限备份数据库角色尚未在仓库 provision，属于生产启用前的外部 ACL 工作，不能在本修复中声称完成。mode `0600` 的 `PGPASSFILE` 与签名私钥、mode `0700` 的备份目录都只能归 `gongkao-backup` 所有，API 的 `deploy` 身份不得触达。必须冻结 `BACKUP_RETENTION_DAYS`、root 所有且 exact mode `0444`、不可由备份调用者改写的 `BACKUP_TARGET_AUTHORITY_FILE`、独立 age 收件人、制品签名密钥和演练报告签名密钥；`BACKUP_TARGET_ID` 已禁止。`anan-backup-target/v1` authority 精确绑定源环境、源 `/etc/machine-id` 摘要、目标 ID、规范目录以及内核实时 mount point/root/device/filesystem/mount-source 摘要，任一漂移均拒绝备份；同一绑定与摘要会进入签名备份 manifest，供恢复与审计复验。authority 必须由 root 在可信主机上根据 `/proc/self/mountinfo` 和 `/etc/machine-id` 采集后安装，`gongkao-backup` 只能读取、不得生成或修改；字段模板见 `ops/backup-target-authority.json.example`。此绑定不能替代对目标确实位于另一物理故障域的外部验收回执。`BACKUP_RETENTION_DAYS` 仅是历史备份观测/告警阈值；`backup.sh` 只输出 `anan_backup_retention_candidate_sets` 与 `anan_backup_legacy_retention_candidate_files`，绝不因新备份成功而删除任何已发布历史。未来如需销毁，必须由另行授权、独立审计且可核对精确对象的生命周期流程承担，不得重新塞回备份创建路径。`BACKUP_DATABASE_URL` 只接受显式绑定单一 host、port、database 和 user 的 libpq URL，不得内嵌密码、hostaddr 或 service；其他 `PG*` 环境覆盖全部拒绝。制品签名私钥、演练报告签名私钥与 age 解密身份不得和备份制品放在同一故障域。
- `backup.sh` 在同一 PostgreSQL `REPEATABLE READ READ ONLY` 事务中，先用发布协议固定 key 取得 transaction-level shared advisory lock，再读取 publication singleton。只有恰好一行 `active`，且 request digest、publication digest、active receipt、部署环境、两个兼容 commit 和 Alembic revision 全部自洽，并与 `BACKUP_SOURCE_ENVIRONMENT_ID` 精确一致时才导出同一 snapshot；`publishing` 或任何不一致均拒绝。`anan-postgres-backup/v4` manifest 同时记录 active receipt、publication digest、目标 authority 全量证据及其 SHA-256、精确迁移版本、dump 摘要和 9 张关键表行数；然后对 age 密文做 SHA-256 和独立非对称签名。shared lock 持有到 dump 和 manifest 完成，因此 publisher 的 exclusive lock 与备份双向互斥。每次运行使用纳秒时间与 128-bit 安全随机数组成不可复用 identity；dump、manifest、tar、密文和 sidecar 全部只在 `BACKUP_DIRECTORY` 同一文件系统的隐藏 staging set 内生成。密文、签名、摘要逐个 `fsync`，隐藏目录 `fsync` 后，用内核 no-replace 目录 rename 一次性发布完整 `anan-<run-id>.backup-set`，再 `fsync` 父目录；已存在目标一律拒绝覆盖。SIGKILL 最多遗留隐藏 staging，恢复入口会在读取摘要前拒绝该路径，不存在可选择的部分 `.backup.age`。
- 恢复演练必须新建空数据库，设置 `RESTORE_REHEARSAL_ACK=isolated-disposable-database`，且 `RESTORE_TARGET_ENVIRONMENT_ID` 不能等于制品的生产环境 ID。隔离主机还必须由 root 安装不可写的 `RESTORE_TARGET_IDENTITY_FILE`，其 `anan-restore-target/v1` JSON 精确绑定 isolated purpose、环境 ID、`/etc/machine-id` SHA-256、目标 database 名和 PostgreSQL `pg_control_system().system_identifier` SHA-256；连接与任一字段不符、或位于备份源 cluster（即使换空库名）均拒绝。`restore_rehearsal.sh` 先验摘要、签名、age 完整性、安全成员集和空库，恢复后除迁移版本与 9 张表行数外，还必须从 restored DB 重算并逐字段匹配 manifest 中的 active publication receipt/digest/revision/commits；即使篡改后的 manifest 内部摘要自洽，只要不等于 dump 内权威也必须失败。最后产出同时带 SHA-256 和非对称签名的 JSON 演练报告；同一制品的多次演练不得覆盖历史报告。
- 生产启用门禁：人工核对目标复制策略，手工执行一次备份，在另一故障域执行恢复演练，将制品、签名和报告摘要归档，再 enable timer。不得先开 timer 后补演练。

## AI 供应商发布

- 文本与视觉调用只能使用 `ai_audit.processor_release_records` 中处于 `approved` 且与发布版本、HTTPS 端点指纹、用途和数据类别精确匹配的处理商；API 密钥只存在 root-only `/etc/gongkao-dazi/api.env`，worker 密钥只存在其精确 workload env，不得横向复制。
- 发布前在 API 主机执行 `python /srv/gongkao-dazi/ops/scripts/audit_external_ai_boundary.py --verify-private-object`；它会读取一个真实私有对象核验 SHA-256，并检查迁移切点后所有模型、OCR 和方法卡调用都有处理凭证。输出不包含密钥、原文或对象地址。
- 每次改 provider、model、prompt 或 schema，必须先执行对应真实黄金集脚本：`release_recognition_ai.py`、`release_text_ai.py` 和 `release_external_question_answer_ai.py`。
- 只有脚本实际调用供应商、通过合同/grounding/禁用语/成本门禁并写入匹配的 `ai_audit.scenario_deployments` 后，场景才允许接流量。不得手工插入 deployment 或用假响应发布。
- Gemini gateway 不重放相同 `Idempotency-Key`；网络超时和不确定 5xx 不自动重试，须保留失败调用审计并由新命令显式重做。
- MyScript 必须同时配置精确 HTTPS 识别端点、host 白名单、provider/model/price 版本和已验收 deployment；`MYSCRIPT_APPLICATION_KEY` 与 `MYSCRIPT_HMAC_KEY` 只存在 API 主机的 root-only env，不进入小程序、日志或仓库。
- 真实笔迹启用前，`processor_release_records` 必须对精确端点指纹、`handwriting_math_recognition` 用途和 `handwriting_strokes` 类别有效批准；现有 blocked 记录不可手工改成 approved，须追加有完整证据的新发布。

## 申论多模型批改 worker

- `gongkao-dazi-essay-review-worker` 只允许执行三候选加盲仲裁的正式运行计划，不得改成单模型或静态结果兜底。
- 启用前必须冻结带版本号的 `AI_ESSAY_GRADING_PLAN_RELEASE_ID`、Gemini/Grok/混元/仲裁四张精确价格卡、各供应商发布与幂等合同、混元云函数发布，以及独立 checkpoint 加密密钥。任一项缺失时保持 `ESSAY_REVIEW_WORKER_ENABLED=false` 且 unit disabled/inactive。
- 配置完成后先在隔离环境直接构造 `GroundedEssayReviewWorker`，再设置 `ESSAY_REVIEW_WORKER_ENABLED=true`。worker 启动时会再次构造完整运行计划并失败关闭；不得通过注入 legacy provider 绕过。
- 常驻进程按 `ESSAY_REVIEW_WORKER_STALE_RECOVERY_SECONDS` 扫描历史 claim，并由领域 worker 原子回收；单次任务异常会写入正式失败/重试状态后进入下一轮，不手工修改 job。
- 正式开放前必须保存真实四模型黄金集、checkpoint 中断恢复、失败退款、重复命令和重启后续跑的验收证据；只有 systemd active 不代表批改链可用。

## 私人资料与删除

- `gongkao-dazi-external-learning-worker` 同时负责私人资料删除任务；API 发布 `20260719_44` 后若不启动该 worker，用户删除会停留在 `queued`，不得宣称已删除。
- 删除成功标准是对象存储删除、OCR/匹配载荷擦除、source/page/chunk/index 级联清理和 `privacy.private_material_deletion_requests.state=completed` 同时成立；不得只隐藏页面条目。
- Markdown 导回必须携带当前导出版本的 `anan_*` 血缘字段；冲突时拒绝覆盖，由用户重新导出当前版本。

## 上线前外部门禁

以下不能用代码伪造完成：微信虚拟支付权限与商品审核、iOS 虚拟支付权限、真实 Android/iOS 支付和退款演练、企业 IdP 角色配置、staging 主机及密钥、跨故障域备份目标与真实恢复演练、教培广告/退款文案律师审核。
