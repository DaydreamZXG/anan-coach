# 2026-08-17 真机错因恢复修复与发布门禁报告

## 结论

- **已验证实现**：提交 `46fc3fb67` 已实现岸岸错因整理的三路有序恢复、独立路由幂等账本、单次业务额度结算、113→115 两条预算迁移，以及答题卡/等待反馈/宽屏横屏修复。
- **未执行生产激活**：生产发布控制面缺少 Runbook 要求的离线签名 `release.json(.sig)`；当前线上只有 legacy candidate 文件，不能把一次手工目录切换冒充合格发布。
- **未安装两把新密钥**：生产数据库没有官方 DeepSeek 或用户中转站的真实用户处理器批准记录。现有数据库契约只允许“合同明确禁止训练、零留存”的处理器；当前公开证据不满足该条件，不能伪造批准。
- **未上传新体验版**：本机官方微信开发者工具通过 macOS codesign 校验，但缺少 root 管理的工具策略和独立 uploader 签名密钥，无法生成 Runbook 要求的 v2 上传回执。

## 已完成改动

1. 模型路由顺序固定为 TokenRhythm → 官方 DeepSeek → 用户中转站；每个业务轮次每条路由最多调用一次。
2. 只对网络错误、超时、HTTP 408/429/5xx 切换后备；确定性 4xx 立即停止。
3. 每条路由使用不同的稳定幂等键并记录真实 provider/release/model；中转站不伪装成官方 DeepSeek。
4. 一个错因业务任务只预留和结算一次额度；完成 checkpoint 重放不再次调用，已计费但响应无效的请求不重放计费。
5. 新 Agent 定义预算扩大到六次模型调用，历史两次定义保持不变；运行计数约束同步扩大到六。
6. 答题卡不再出现重复的“继续第 15 题”语义，只显示一个当前题；错因等待页明确证据已保存、可离开、可稍后刷新；双栏只在至少 1024px 的横屏出现。

## 可复算验证

- 产品治理发布单元：`RU-CAUSE-MULTI-ROUTE-RECOVERY-036`
- 冻结哈希：`sha256:77f83087d63f2418c5e5f01b49c908f34218dfcdf36af1c1a405b1383facb51c`
- 后端 Ruff：通过。
- 后端相关测试与 PostgreSQL 完整迁移链：55 passed，1 条第三方弃用警告。
- 前端相关测试：5 files / 22 tests passed。
- 微信工程完整门禁：格式、TypeScript、ESLint、设计/交互/导航/分包/API 一致性全部通过；243 files / 1189 Vitest、21 Node、9 云函数测试全部通过。
- 密钥模式扫描：Git diff 与暂存区均未发现长 `sk-` 密钥。

## 生产只读检查

- 当前制品：`/srv/gongkao-dazi/releases/20260815T031823Z-113-04405a4b88`
- 数据库修订：`20260815_113`
- API 与 cause worker：active。
- `/health`：200；`/ready`：200（仅代表当前 113 制品）。
- cause worker 仍由 `deploy` 用户运行并加载 `api.env`；尚未切换到仓库定义的独立 worker 用户和专属 root-only env。
- `ai_audit.processor_release_records` 当前没有可用于这两条新路由的批准记录。

## 外部处理门禁证据

- 官方 DeepSeek 当前支持 `deepseek-v4-pro` 和 JSON Output：[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。
- DeepSeek 当前公开隐私政策说明输入可能用于技术改进/训练，且留存期按目的和数据类型变化，不是项目数据库所要求的“合同禁止训练 + 零留存”：[DeepSeek Privacy Policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html)。
- 用户中转站的模型兼容性不等于数据处理批准；目前没有可核验的运营者身份、零留存、禁止训练和删除边界证据。

## 解除门禁所需材料

1. **处理器批准二选一**：
   - 提供官方 DeepSeek 与中转站分别签署/确认的禁止训练、零留存、运营者身份、传输和删除边界证据；或
   - 通过新的产品/隐私决策和数据库迁移正式修改当前“只有零留存才能批准”的边界，并同步用户隐私告知与同意。仅口头允许接 Key 不能替代这一步。
2. **生产发布权威**：由离线 release authority 为精确 commit 生成签名 manifest/数据配对回执，并安装受信公钥；不能由部署机临时自签。
3. **微信上传权威**：人工复核并安装 root-owned DevTools policy、独立 uploader 私钥及对应信任公钥，随后用 `upload_wechat_release.py` 生成完整 v2 回执。

## 门禁解除后的执行顺序

1. 对生产 113 数据库生成可校验备份并记录 SHA-256。
2. 停止 API 和全部数据库 worker，确认连接排空。
3. 用精确制品执行 113→114→115 前向迁移并验证约束。
4. 安装独立 cause worker 用户、专属 root-only env、heartbeat env 和签名 release runtime。
5. 原子切换制品，同时启动 API/worker，等待 heartbeat 后验证 `/health`、`/ready` 和指标。
6. 用合成题目分别验证主路由和允许的后备路由，不使用真实用户数据做探针。
7. 通过官方微信 CLI 上传新版本，归档 `.json/.sig/.info.json/.log` 四件套；CLI accepted 不等于体验版已切换或正式发布。

## 本次明确未做

- 未停止生产服务，未执行数据库写入或迁移。
- 未把官方或中转密钥写入本机/服务器文件。
- 未修改真实用户数据、处理器批准记录或 AI 发布记录。
- 未直接调用微信 `cli upload`，未声称体验版已更新。
