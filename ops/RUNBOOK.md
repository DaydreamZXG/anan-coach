# 岸岸小程序值班与客服手册

本手册只描述生产操作。任何人不得在数据库里手工改订单、退款、权益、删除任务或支付通知状态。

## 发布与回滚

1. CI 必须通过 `scripts/full_verify.sh`，并生成带 SHA-256 的不可变制品。
2. `ops/scripts/release_staging.sh` 上传制品，远端 `activate_release.sh` 核对摘要、建独立虚拟环境、执行 Alembic，再原子切换 `current` 软链接。
3. `/ready` 只在数据库版本精确等于 `REQUIRED_DATABASE_REVISION`，且阶段 8 与步骤 10 的必需表都存在时返回成功。
4. 应用故障可执行 `ops/scripts/rollback_release.sh <release-id>` 切回上一个已验收制品。数据库迁移不做破坏性降级；本阶段迁移为向后兼容的增量迁移。

## 支付与退款

- **当前状态（2026-07-18）：免费冷启动。** 生产必须设置 `COMMERCIAL_MODE=free_cold_start`，不得配置四个 `WECHAT_VIRTUAL_PAY_*` 密钥，`gongkao-dazi-commerce-worker` 必须保持 stopped；客户端不得提供购买动作。服务端目录返回空商品，即使伪造客户端订单请求也必须拒绝。以下内容仅在产品负责人重新启用支付后作为上线门禁执行。
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
- 启用前必须冻结 `BACKUP_RETENTION_DAYS`、跨故障域 `BACKUP_TARGET_ID`、独立 age 收件人、制品签名密钥和演练报告签名密钥。`BACKUP_DATABASE_URL` 只接受 libpq URL，不得内嵌密码；密码由 mode `0600` 的 `PGPASSFILE` 提供。制品签名私钥、演练报告签名私钥与 age 解密身份不得和备份制品放在同一故障域。
- `backup.sh` 在同一 PostgreSQL 可重复读快照中导出自容器 dump，将精确迁移版本、dump 摘要和 9 张关键表行数放入加密 manifest；然后对 age 密文做 SHA-256 和独立非对称签名。任一检查失败不发布 `.backup.age` 完成标记。
- 恢复演练必须新建空数据库，设置 `RESTORE_REHEARSAL_ACK=isolated-disposable-database`，且 `RESTORE_TARGET_ENVIRONMENT_ID` 不能等于制品的生产环境 ID。`restore_rehearsal.sh` 先验摘要、签名、age 完整性、安全成员集和空库，恢复后精确比对迁移版本与 9 张表行数，最后产出同时带 SHA-256 和非对称签名的 JSON 演练报告；同一制品的多次演练不得覆盖历史报告。
- 生产启用门禁：人工核对目标复制策略，手工执行一次备份，在另一故障域执行恢复演练，将制品、签名和报告摘要归档，再 enable timer。不得先开 timer 后补演练。

## AI 供应商发布

- 文本与视觉调用只能使用 `ai_audit.processor_release_records` 中处于 `approved` 且与发布版本、HTTPS 端点指纹、用途和数据类别精确匹配的处理商；密钥只存在 root-only `/etc/gongkao-dazi/api.env`。
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
