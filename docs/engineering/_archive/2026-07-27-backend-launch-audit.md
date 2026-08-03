# 2026-07-27 后端上线扎实度审计（中期止损版）

审计编队因额度收口提前叫停，本文汇总**已到账**的 5/6 维度体检 + 实证探针 + API 对账。
未跑完的部分见文末「续跑断点」。发现均**未经对质反驳**（原计划的对质阶段被砍），
判级以保守为准，修复前建议先花十分钟证伪。

## 一、核心判决：AI 能不能看懂用户干了啥？

实证探针（真数据端到端：造了「进题→滚材料×2→写三笔→擦一笔→选 A→40 秒改选 C→470 秒
离开」的完整现场，原样抓取冻结工具给 LLM 的文本）：

**判决：部分能。** 行为骨架完整可重建——进入、两次滚动、三笔书写、一次擦除、A→C 改选
及其 40 秒间隔、离开时刻，`actionEventsTruncated=false` 可当完整前缀。这已超出
「只剩最终答案」的水平。**但语义细节全部丢失**，不足以支撑「计算错误 vs 审题错误」级
的错因定论：

1. **【P0 级缺口·错因直接相关】确认前的原始识别被证据层抹掉。**
   `load_ink_formulas` 只返回版本链头（repository.py:436-441 的 `not exists successor`
   过滤）：探针里用户实际写的是 `\frac{120}{8}=13`（计算错误的唯一直接证据），确认订正
   后 AI 只能看到 `=15`。**系统性地把计算错误误判成审题/方法问题。**
2. **【P1】动作细节被投影剥光。** `load_action_trace`（tools.py:163-174）只投影
   `{type, atMs, optionId}`：滚动丢位置（scrollTop 0→420 没了）、擦除丢目标（不知擦的
   哪一笔）、信心丢值；`sessionAtMs` 也被丢弃，跨题节奏不可见——且有测试把这个投影
   固化成了契约（test_xingce_cause_agent_action_evidence.py:51-61）。
3. **【P1】两套时钟不互通。** trace 是相对 atMs、ink 是绝对 ISO 墙钟，无绝对锚点互指，
   公式写在改选前还是改选后无法判断；笔画与公式来自两条互不引用的记录。
4. **【P1】AI 看不到题面。** 无题干/选项文本/材料/correctOptionIds/advisorySeconds，
   无解析的题实质无法区分 stem_misread/knowledge_gap/method_selection。

**结论**：采集层（契约增量 v1）记的东西是够的，丢失发生在**证据投影层**——修投影和
时钟锚点即可解锁，不用改采集。这是「记录后的识别」最划算的下一刀。

## 二、P0 上线阻塞（不修不能接真实用户）

1. **发布管线钉死旧版本**：`ops/scripts/activate_release.sh` 把 DB 版本硬编码在
   `20260721_52`，仓库单头已到 `20260727_63`（积压 11 个迁移）——当前代码**无法通过
   既有管线部署**；且失败路径会先迁库再中止，留下「旧代码+新 schema」使旧版 /ready 503。
2. **生产零备份**：`ops/RUNBOOK.md` 记录 BACKUP_ENABLED=false、backup.timer 保持
   disabled——单机 Postgres 裸奔接首批用户。
3. **（探针 P0 级缺口）** 见上文第一条：错因证据层抹掉确认前识别。

## 三、P1 首批用户会撞上的

**采集/识别侧**
- 客户端 checkpoint 入选过滤不看 annotations/actionEvents（controller.ts:177）——
  「只有轨迹没有作答动作」的题永不上传，重启即永久丢失。
- 会话恢复剥离压感 p（controller.ts:268），恢复后下一次保存以无 p payload 覆盖。
- 答题卡照片一题空白/无法识别 → 整卷 422，人工核对回路打不开（answer_ingestion/service.py）。
- 识别供应商开关关闭（断供/配额的正规降级）会连带瘫痪已有候选的确认/拒绝回路，7 天过期报废。

**AI 消费侧**
- 冻结证据无裁剪策略，超 token 硬终止整个 run 而非降级（cause_agent/tools.py）。
- 错因确认从不写 supersedes 链，被用户改判的决定永久计入错误模式聚合（**假数据**，
  learning_brain/repository.py）。
- 「争议题暂停归因」只在聚合层，候选生成层无 disputed 语义、出参不拦截。

**基建侧**
- 全栈无限流（应用层无中间件、Caddy 裸反代、USAGE_ACCESS_MODE=unlimited）；登录端点
  直通微信 API，同步 LLM 端点可被刷成本。
- data_rights worker claim 后即 commit、无 stale-claim 恢复——删除/导出任务崩溃即永久
  running 且无告警覆盖。
- iLink 照片删除队列 'deleting' 状态无崩溃恢复（repository.py:827 只捞 pending/failed）
  ——用户照片可能永不删除（隐私承诺违约风险）。
- **多个会被 API 入队的任务类型生产无运行单元**（xingce 错因 agent、learning_media、
  community_method_cards、申论 grounded review）：ops/systemd 仅 7 个单元——错因分析
  在生产会**永远 queued**。
- 生产域 kg.xingguoxian.cn 上 `/coding-tools-mcp/*` 反代本机 18765，查无鉴权配置——
  疑似开发工具链暴露公网，需立即核实。

## 四、API 对账（244 端点，72 孤儿）

- **frontend_orphan（前端调、后端无——断链嫌疑，最高优先核实）**：
  `GET /api/v2/goals/workspace`、`GET /api/v2/learning-profile`。
- **backend_orphan** 大宗是 v1 遗留面（auth/apple、commerce、companion、feed、docket、
  method-cards、learner 等）——处置建议：确认 v1 面是否已废弃，废弃则下线路由；
  其中两处值得留意：`GET /api/v1/knowledge/questions/search`（现成的文本搜题端点）与
  `GET/POST /api/v1/onboarding/placement-test*`（**定位测端点已有骨架**，与任务 #12
  自适应定位短测直接相关）。

## 五、健康面（已确认扎实，别重复加固）

各维度 healthy 清单共 42 项到账（详见工作流日志），要点：append-only 版本表纪律、
命令回执幂等模式、检查点契约增量的可选字段零迁移、冻结工具 definition_release 审计链、
lowSample 口径在 calibration 一致执行。

## 六、修复顺序建议

1. 发布管线解钉 + 迁移积压演练（P0.1，不修无法部署）
2. 生产备份启用（P0.2，一条 systemd timer 的事，先于一切数据修复）
3. 错因证据投影三连修：版本链头过滤放开确认前识别 + 投影补 detail/sessionAtMs +
   时钟锚点互通（P0.3+P1，解锁「AI 看懂用户」的核心价值）
4. 缺失 worker 单元补齐（否则错因修完也永远 queued）
5. checkpoint 入选过滤补 annotations/actionEvents（每天都在丢数据）
6. 限流最小集（登录 + LLM 端点）
7. 其余 P1 按用户撞上的概率排

## 续跑断点（额度恢复后）

- 上线审计 run `wf_fc571df8-37f`：铁律诚实维度未跑完；对质+汇总未跑。
  `Workflow({scriptPath: ".../backend-launch-audit-wf_fc571df8-37f.js", resumeFromRunId: "wf_fc571df8-37f"})`
  已完成 5 腿走缓存免费重放。
- 功能链路全扫 run `wf_a9dd83de-ba1`：API 对账已到账（本文§四）；八族走查只起了 4-5 族
  未收口，对质+汇总未跑。同法续跑，mapper 腿免费。
