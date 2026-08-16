# 2026-08-15 生产 104→113 发布预检

## 结论

当前服务器可进入一次有停写窗口、可从 104 备份恢复的后端升级，但不能使用仓库中尚未在该服务器初始化的签名商业发布控制面。此次只授权把已冻结并完成机器验收的 105～113 后端链作为一个不可变候选制品部署；不把本次操作描述为完整商业生产发布、微信正式版发布或长期备份体系已经启用。

## 当前线上事实

- 主机：`101.96.198.253`。
- 当前指针：`/srv/gongkao-dazi/current` → `/srv/gongkao-dazi/releases/20260809T132805Z-c0424564f`。
- 当前源码身份：`c0424564f00e235bdd59d2ba220983eee4373d6a`。
- 当前数据库：`gongkao_dazi_staging`，Alembic `20260809_104`，PostgreSQL 16.14。
- API 与 cause-agent、coach-notification、data-rights、essay-review、external-learning、ilink-object-deletion 七个服务均为 `active`；本机 `/health` 与 `/ready` 在迁移前均返回 200。
- 根文件系统尚余约 31 GiB。
- `gongkao-dazi-backup.timer` 为 disabled/inactive。
- `/etc/gongkao-dazi/release-trust.pem`、`release-runtime.env`、签名 data-release current pointer、`backup.env` 与 backup target authority 均不存在；因此 `activate_release.sh` 的严格签名发布链不能在本次被诚实地声明为可用。

## 候选源边界

- 024 成功报告：`.product-governance/execution/reports/8b759899313bed45.cdc2bcc6a5fdeda8c481.json`。
- 024 完整测试快照：`.product-governance/execution/tested-snapshots/8b759899313bed45.cdc2bcc6a5fdeda8c481.json`。
- 从该快照筛选 `backend/**`、`apps/wechat/**`、`ops/**`、`scripts/**`、`.github/**` 与 `audit/api-contract/openapi.json` 共 2,704 个源文件；与当前工作区比较结果为：缺失 0、额外 0、摘要漂移 0。
- 相对线上 `c0424564f`，后端发布面有 90 个新增或修改路径。此前完成发布单元直接精确覆盖 63 个；其余 27 个虽然已包含在 024 的完整测试快照中，但没有各自成为旧单元的最新 owned-state，因此 025 必须把完整发布面作为一个只读候选重新封存和验证，不能只拼接旧 owned-state。
- 105～113 已在真实 104 逻辑备份副本完成迁移；113 修复后 dump 恢复到第二个空库零错误，核心行数和活动发布不变，详见 `reports/2026-08-15-knowledge-map-restore-rehearsal.md`。

## 本次执行顺序

1. 从 024 的逐字节一致发布面生成一个独立、干净的临时 Git 提交和 SHA-256 制品；主工作区不提交、不清理、不打包未选择的数据目录。
2. 在服务器保留当前 release 指针，安装但不激活候选目录并完成 import/compile 预检。
3. 记录迁移前关键事实；停止 API、全部数据库 worker 与 OpenILink 写入入口，确认进程停止。
4. 生成服务器本地 104 custom-format dump、SHA-256，并下载一份到本机；两份摘要必须相同。
5. 只用候选代码执行 `104→113`；核对 Alembic head、孤儿为 0、历史发布为 1、后继链恢复、外键已验证、活动 v5 身份与关键行数不变。
6. 候选在独立端口读取同一环境启动，验证 `/health`、`/ready` 和 023 需要的正式路由；失败则保持旧指针并在停写窗口内从 104 dump 恢复。
7. 候选通过后原子切换 `current`，重启同一组服务，复验进程、数据库、路由与真实测试账号黄金链。
8. 任一用户事实、题库事实、活动知识发布或关键行数出现计划外变化即停止；数据库不做 Alembic downgrade。

## 回滚边界

- 切换前失败：保持旧应用指针；若数据库已迁移，则在所有写入服务仍停止时从本次 104 dump 恢复，再启动旧 release。
- 切换后仅应用故障且 113 数据事实正确：优先 forward-fix；若无法立即修复且仍处于全停写窗口，恢复 104 dump 与旧指针。
- 恢复用户流量后不允许用旧 dump 覆盖新写入；此时只能前向修复。
- 本次手工备份是该发布窗口的恢复制品，不等于跨故障域、定时、签名的长期备份能力已经启用。
