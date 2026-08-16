# 2026-08-15 生产 104→113 发布记录

## 发布结论

后端候选版 `20260815T031823Z-113-04405a4b88` 已切换到生产，数据库已从 `20260809_104` 前向迁移到 `20260815_113`。公网健康检查、全部已启用 worker、新增岸岸教练上下文路由和真实学习账号的今日读取链路均已通过。发布前 104 备份已在本机重组，大小、SHA-256 和 PostgreSQL 目录解析三项全部通过。

本次是后端与数据库激活，不是微信正式版上传；服务器尚未安装仓库严格签名发布控制面所需的 trust/runtime authority 文件，因此不把它冒充为完整商业发布。

## 不可变候选版

- 临时干净 Git 提交：`04405a4b8872c919aa78e174abdd53c599f32df1`。
- 制品 SHA-256：`e6f5391e4ddbe1c16b22b65ca31df880940efbb6b1810d7de342829cedc9e25d`。
- manifest SHA-256：`ad96840745f88f43e2a0902715fd3231fb45cd90e07b2030639f3cea3281e06e`。
- 候选源与 024 完整测试快照中 2,704 个发布路径逐字节一致：缺失 0、额外 0、摘要漂移 0。
- 治理单元 `RU-PRODUCTION-104-TO-113-RELEASE-025` 在 Python 3.12 正式运行中 6/6 通过，源码差异和完整性失败均为 0。

## 数据库结果

| 事实 | 迁移前 104 | 迁移后 113 |
| --- | ---: | ---: |
| 题目 | 233,696 | 233,696 |
| 题目出现位置 | 336,244 | 336,244 |
| 内容版本 | 238,038 | 238,038 |
| 内容 head | 232,660 | 232,660 |
| 知识节点 | 4,305 | 4,305 |
| 知识绑定 | 416,141 | 416,141 |
| release-scope 孤儿 | 2,462 | 0 |
| 历史兼容发布 | 0 | 1 |

活动知识图谱 `knowledge-map@20260805T000000Z-v5` 的发布 ID、640 个节点、158,366 个题目绑定及绑定指纹全部未变。迁移补回了历史 release manifest、后继父链和已验证的 release-scope 外键，没有改写题目和用户学习事实。

## 运行时验收

- `/srv/gongkao-dazi/current` 已原子指向新 release。
- 公网 `/health` 返回 200，`/ready` 返回 200 且 migration 为 `20260815_113`。
- API、cause-agent、coach-notification、data-rights、essay-review、external-learning、ilink-object-deletion 与 OpenILink Hub 共 8 个已启用服务均为 `active`。
- 切换后 API 无 error 级 journal 记录。
- 专用现有学习账号的 4 个只读端点全部返回 200：
  - `/api/v2/learning-brain/coach-context`
  - `/api/v2/learning-brain/profile`
  - `/api/v2/plans/today`
  - `/api/v2/plans/today/basis`
- 今日返回 1 个任务、6 个证据引用和 6 个失效/重排条件；教练上下文已返回 recognition 与 strategy directive。
- 验收仅创建短时会话，未触发任何写学习状态的端点；会话已撤销，活跃验收会话为 0。

## 备份与回滚边界

- 停写后生成的服务器 104 custom-format dump：`1,167,615,021` bytes。
- dump SHA-256：`80f3711d7f0dd4fd88d3b6c62f98c825433b462a2955293e7216999838c158bc`，`pg_restore --list` 已通过。
- 服务器内完整 dump 仍在发布证据目录中。
- 本机异地副本：`/Users/zxg/Downloads/anan-production-pre-104-20260815T031823Z.dump`。
- 本机副本大小同为 `1,167,615,021` bytes，SHA-256 同为 `80f3711d7f0dd4fd88d3b6c62f98c825433b462a2955293e7216999838c158bc`，`pg_restore --list` 已通过；备份闭环完成。
- 用户流量已恢复，不允许用 104 dump 覆盖此后新写入；如有问题只做前向修复。

## 证据

- 服务器证据目录：`/srv/gongkao-dazi/release-evidence/20260815T031823Z-113-04405a4b88`。
- 本机安全证据包：`/Users/zxg/Downloads/anan-release-evidence-20260815T031823Z-113-04405a4b88`，不包含环境变量或用户响应正文。
- 激活汇总 SHA-256：`c2c1ade368f0daed99f71f508202d13a8ef0757b9bb2c533ba963a3a96d11875`。
- 真实账号验收证据 SHA-256：`ccb004fb9eda3d9c4141ecf7c57769b1c98783d67709e3fa768026678778ce25`。
- 025 正式执行报告：`.product-governance/execution/reports/6a2b579a690990f6.e092730a169845f539ca.json`。
- 025A 备份转移顺序报告：`.product-governance/execution/reports/2b15734f3f8e9dc2.a4fbe76d353fcb113826.json`。
