# 2026-08-04/05 考公搭子 — 冲刺交接记录

> 目标:上线前把检查出来的问题全部修好,并推进产品负责人"中控大脑"愿景。
> 状态:主分支 `codex/commercial-selection-repair` 已含 19+ commit(地基修复 + 阶段1 + 阶段2 + 文案),全量套件最终验证中。
> 核心文档:[结构健康报告](2026-08-04-structure-health-assessment.md) / [系统掌握地图](2026-08-04-system-mastery-map.md) / [愿景vs现状](2026-08-04-learning-loop-vision-vs-current.md) / [中控大脑草案](2026-08-04-central-brain-design-draft.md)

---

## 一、今天做了什么(总览)

| 阶段 | 内容 | 状态 |
|---|---|---|
| 地基 | 36 基线失败清零、完成状态机收敛、死代码清理、前端硬编码、companion 解耦、planning 双轨容量 | ✅ 全合并 |
| 阶段 1 | 任务数放宽、申论作答评阅为主、内场模考接能力、错因喂回选题 | ✅ 全合并 |
| 阶段 2 | **中控大脑 agent(会推断策略)+ 岸岸接入(能解释 rationale)** | ✅ 全合并 |
| 文案 | 用户可见文案重写为「朋友鼓励」笔触 | ✅ 高频页 + 剩余铺全中 |

---

## 二、地基修复(第一二波,净删 1250+ 行)

### 第一波
1. **收敛完成状态机**:`decide_cause` 历史兼容块 → `_settle_historical_completion`(real_attempt/service.py:1366),新增 3 测试
2. **清理死代码**(-670 行):社区幽灵类、consume/reverse 死链路、real_attempt 影子包装;核实保留 3 项(positions/contracts、_link_late_evidence、notification 页)
3. **修前端硬编码**:考点枚举收敛 `domain/module-baselines.ts`;支付平台判断抽 `platform/payment-platform.ts`(平板不再当 iOS)
4. **收尾 companion 解耦**:删 provider requestedActions/next_actions 死链路,消除逆向依赖;修 `del context`

### 第二波
5. **收敛剩余枚举+死值**:day-adjustment/companion-action 枚举改共享源;删 3 个死枚举值
6. **修 _goal_gap 跨域**:私有函数公开改名 `goal_gap`
7. **收敛 essay 指纹+slot**:`_fingerprint` 7份→1份;slot 相位表 3处→1真值源
8. **修 cause_agent 36 失败**:3 个独立根因
   - evidence_refs `[:50]` 截断(33 个失败)
   - planning 双轨容量 `_cap_xingce_block`(4 个失败,真实用户 bug:45 分钟日行测被饿死)
   - verification 测试窗口 30→60(1 个,测试 setup 与契约不符)

---

## 三、阶段 1 — 数据桥(中控大脑的输入)

9. **每日任务数**:上限 12→16,行测块 60→30 分钟(全职 480 分钟 16 个任务)
10. **申论流程(2A)**:首次走完整流程(材料→要点→提纲→作答→评阅),之后作答评阅为主,材料/要点/提纲按需补(评阅发现薄弱才补)
11. **内场模考→能力估计**:`record_controlled_mock_score` 新增,模考出分写回能力(controlled_mock 权重 0.9,幂等+版本链)
12. **错因喂回选题**:`cause_training.py` 把 8 类错因码映射到训练方向(知识缺口→基础题、方法不当→方法题、计算→计算题、时间压力→限时等),有错因时按错因选题,无则回退最弱模块

---

## 四、阶段 2 — 中控大脑(愿景灵魂)

13. **中控大脑 agent**(阶段2-A):`learning_brain/agent.py` `LearningBrainAgentService`
    - 读全部状态(profile+目标差距+错因+能力)→ LLM strict JSON 推断策略指令(focus/cause_direction/essay_emphasis/mock_schedule/rationale + action 取舍)
    - 通过 `create_strategy_candidate` 落库,producer_type=agent(该字段首次有生产者)
    - 用户确认后 planning 读 active_strategy_decisions 影响排题;按天幂等
    - 新增迁移 96 + 独立 LLM 配置
14. **岸岸接入**(阶段2-B):`current_strategy_directive` 读 active/pending 策略 rationale → 注入岸岸上下文(`strategyDirective`)+ persona 引导
    - 用户问"为什么这样排"→ 岸岸引用 rationale 回答
    - pending 策略存在时 → 岸岸主动提议,请用户确认
    - 前端零改动

---

## 五、文案重写(朋友鼓励笔触)

15. **风格**:像朋友鼓励——把自己人、先安抚再给行动、不评判、短句口语带温度;禁术语(证据版本/写回/重新载入/受控基线/评分规程)
16. **第一轮**:后端 planning 全部任务文案 + 前端 4 高频页(today/day-adjustment/me/ability),29 文件,14 测试断言同步
    - 样板:"这几道错题该回炉啦""先来摸个底""昨天调的计划已经过期啦,没关系,按你现在的状态重新定一份就好。""哎,刚才没存上,别急,再点一次就好。"
17. **第二轮**:剩余 ~40 文件(essay 系列/positions/onboarding/companion/mock-exam/practice/reflection 等)铺全中
    - 文案风格已存项目记忆(copy-tone-friend-encouragement)

---

## 六、验证状态

| 验证项 | 结果 |
|---|---|
| 阶段1 回归 | ✅ 162 passed |
| 中控大脑 agent | ✅ 6+27+33+27+18 passed |
| 岸岸接入 | ✅ 65 passed(2 失败为基线既有) |
| 文案第一轮 | ✅ 89+62 passed,tsc 0 错误 |
| 全量后端 pytest | ⏳ 后台最终验证中(b4c0fhv33) |

---

## 七、遗留待办

1. **文案剩余页铺全**(第二轮,agent 进行中)
2. **补隐私同意记录**(consent_events 建表无写入,合规,上线前必做)
3. **生产备份启用**(BACKUP_ENABLED,P0 上线阻塞,需服务器配合)
4. **知识树升级**(按薄弱排题,阶段3,需产品决策)
5. **待验证弱项闭环**(弱项假设→验证→收口,阶段3,需产品决策)
6. 前端 `test_openapi_auth_contract.py` 231vs230(既有)
