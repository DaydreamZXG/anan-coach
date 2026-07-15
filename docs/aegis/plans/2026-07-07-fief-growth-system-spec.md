# 封地成长系统 — 落地设计（Mechanic Specification）

> 配套：2026-07-06-governance-gamification-master-plan.md
> 替代：原方案"民心值（fame）"惩罚型机制
> 作者：GameDesigner ｜ 日期：2026-07-07 ｜ 版本：v0.1（待 playtest 校验）

## 0. 设计支柱（本系统依此衡量）

1. **投资而非惩罚**：玩家守护自己养大的东西，不为躲惩罚而回来。
2. **可见变强**：每次成长都肉眼可见、且指向考试目标。
3. **有意义的选择**：玩家决定先发展哪片辖区（agency）。
4. **零羞辱**：断更只是暂停，不痛缩、不降级、不变暗红。

## 1. Mechanic Specification

### Mechanic: 封地成长系统 (Fief Growth)

**Purpose**
用"成长型封地"替代原方案的"衰减型民心"，把惩罚型留存改为投资型留存；同时把 6 大板块练习变成"治理辖区"，让变强可见、让选择有意义。

**Player Fantasy**
"我主政一方，勤政则辖地繁荣、疆域随政绩拓展；即便暂离，我的治绩仍在，归来即可续政。"

**Input**
- 每次高质量练习（单模块滚动胜率达标 / 当日有效练习）
- 分差缩小事件（模考超目标分、阶段测评进步）
- 归来事件（断更后重新打开 App）

**Output**
- 封地繁荣度 (prosperity) 提升（或暂停，不痛缩）
- 对应辖区 (district) 等级提升（6 模块各一片）
- 品级晋升时封地疆域拓展（解锁新辖区 / 新视觉）
- 幕僚点评文案 + 动画反馈

**Success Condition**
- 玩家能在 7 日内感受到封地"从荒县到繁府"的变化；
- 断更 1 周后归来无负面反馈，且愿意继续；
- 玩家能说出"我哪片辖区最弱，今天去补" —— 说明 agency 成立。

**Failure State**
- 繁荣度因断更暴跌 → 重现民心式焦虑（= broken）；
- 辖区升级与真实胜率脱节 → 失去"可见变强"的信任；
- 玩家无视辖区差异、无选择感 → agency 失效。

**Edge Cases**
- 玩家只刷单一模块：其余辖区停留在低等级，封地呈现"偏科"形态 —— 这是预期的战略可视化，非 bug。
- 6 辖区全满级：繁荣度改由品级 / 分差继续承接，辖区进入"精修"微成长（建筑外观细节）。
- 断更 >30 天：繁荣度停在地板值，品级（分差驱动）不受影响 —— **绝不降级**。
- 同日多次练习：繁荣度单日增益设上限，避免一日爆肝刷满。

**Tuning Levers**
- 单模块胜率 → 辖区等级的阈值带
- 繁荣度单次 / 单日增益上限
- 断更宽限期与极缓衰减率、地板值
- 晋升时的繁荣度庆祝加成

**Dependencies**
- `rank_system`（品级 = 疆域层级）
- `knowledge/repository.py`（模块胜率数据源）
- `taskstream`（今日政务）
- `ai/`（幕僚点评模板）
- 现有 `learner.official_ranks`（替换 fame 语义）

## 2. 双轨模型（关键：消除"双引擎冲突"）

- **疆域层级 (Territory Tier)** = 官品，由 **分差** 驱动（英雄指标，主引擎）→ 决定"你管多大"。
- **繁荣度 (Prosperity)** + **辖区等级 (District Level)** = 由 **学习行为与质量** 驱动，只增 / 暂停 → 决定"你管多好"。
- 两轨正交：分差决定疆域，行为决定治理质量。无冲突、无虚高。原方案"XP 与分差双引擎互相矛盾"在此彻底消解。

## 3. 辖区映射（6 模块 → 6 辖区）

| 板块 | 辖区 | 满级意象 |
|------|------|---------|
| 常识判断 | 民风 | 百姓安居、乡学林立 |
| 言语理解与表达 | 教化 | 文书通达、书院兴盛 |
| 数量关系 | 工程 | 水利成网、城池坚固 |
| 判断推理 | 刑名 | 治安清明、冤案绝迹 |
| 资料分析 | 钱粮 | 仓廪充实、市集繁荣 |
| 政治理论 | 德政 | 朝政清朗、万民称颂 |

辖区等级 0–5，由该模块**滚动胜率**映射（见调参表）。

## 4. 数据模型（SQL）

```sql
-- 在 official_ranks 上替换 fame 语义（fame 列保留但前端不再展示，后续大版本可 DROP）
ALTER TABLE learner.official_ranks
  ADD COLUMN prosperity         int NOT NULL DEFAULT 20,   -- 封地繁荣度
  ADD COLUMN prosperity_floor   int NOT NULL DEFAULT 10,   -- 极缓衰减收敛地板
  ADD COLUMN prosperity_updated_at timestamptz DEFAULT now();

-- 辖区等级（每用户 6 行）
CREATE TABLE learner.fief_districts (
  user_id         uuid NOT NULL REFERENCES learner.users(user_id),
  module          text NOT NULL,   -- 6 板块原名（常识判断 / 言语... ）
  district_level  int  NOT NULL DEFAULT 0,
  last_improved_at timestamptz,
  created_at      timestamptz DEFAULT now(),
  PRIMARY KEY (user_id, module)
);

-- 辖区配置（产品配置）
CREATE TABLE product.fief_district_config (
  module      text PRIMARY KEY,
  district_name text NOT NULL,
  winrate_t1  float NOT NULL,  -- 升到 1 级所需滚动胜率
  winrate_t2  float NOT NULL,
  winrate_t3  float NOT NULL,
  winrate_t4  float NOT NULL,
  winrate_t5  float NOT NULL,
  visual_t0   text, visual_t1 text, visual_t2 text,
  visual_t3   text, visual_t4 text, visual_t5 text
);

-- 封地事件（可并入 rank_events，event_type 增 fief_prosperity / fief_district_up）
CREATE TABLE learner.fief_events (
  event_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES learner.users(user_id),
  event_type text NOT NULL,
  delta      int,
  module     text,
  reason     text,
  created_at timestamptz DEFAULT now()
);
```

## 5. 调参表（平衡电子表，公式化，待 playtest）

| 变量 | 基准值 | 最小 | 最大 | 调参说明 |
|------|--------|------|------|---------|
| 初始繁荣度 | 20 | 10 | 30 | 新手即见"可生长" |
| 单模块达标会话增益 | +3 | +1 | +6 | [PLACEHOLDER] 按日均 2 会话估 |
| 当日活跃一致性增益 | +2 | 0 | +4 | 鼓励日更，不与会话增益叠加惩罚 |
| 单日繁荣度增益上限 | +8 | +4 | +12 | 防一日爆肝刷满 |
| 连击 / mastery 瞬间加成 | +1 | 0 | +3 | 小惊喜，锦上添花 |
| 晋升庆祝加成 | +10 | +5 | +20 | 大爽点，强化主引擎正反馈 |
| 断更宽限期(天) | 7 | 3 | 14 | 宽限内零衰减 |
| 宽限后衰减率/天 | 1% | 0% | 3% | 极缓，向地板收敛 |
| 繁荣度地板(峰值%) | 40% | 20% | 60% | 投资不归零 |
| 辖区升级胜率带 t1..t5 | 55/65/75/85/92% | - | - | [PLACEHOLDER] 按模考分位定 |

**broken 定义（先于开发写明）**：断更 7 天内繁荣度下跌 > 0，或玩家因封地"变差"产生流失情绪 —— 任一发生即回滚衰减逻辑，改为纯暂停。

## 6. 反馈与包装

- **辖区升级**：该片区播放"营建"动画 + 幕僚："水利既成，百姓称便。"
- **繁荣度 +**：封地整体亮度 / 绿意提升。
- **晋升**：疆域拓展 + 印章金光 + "治绩既显，朝廷扩你疆土。"
- **归来（断更后）**："封地尚安，百业待兴，大人请续新政。"（无羞辱、无暗红）

## 7. 与 Master Plan 的差异（Changelog）

- **删除**：民心衰减、<30 暗红、民心<0 降级判定。
- **修改**：御史台由"降级惩罚"改为"归来提醒"（无惩罚）；精力值建议删除或改正向 buff（另案处理）。
- **新增**：封地繁荣度 + 6 辖区等级 + 调参表。
- **排行榜**：指标由 XP 改为 分差缩小速度 / 稳定度（配合 lever⑥ 社会性证明）。

## 8. Onboarding（接手必做）

- 首答正确后，封地由"荒县"点亮第一片辖区 → 即时骄傲感（替代原"首答盖准字"纯反馈）。
- 前 3 次练习分别点亮 3 片辖区，让玩家发现"不同板块 = 不同辖区"（探索式发现，非纯文案）。
- 首日结束钩子："今日已兴 3 业，明日可拓钱粮。"（lever④ 会话钩子）

## 9. Playtest 成功标准（先于开发定义）

- **观察**：玩家是否主动说"我 XX 辖区弱，去补" → **解释**：agency 成立。
- **观察**：断更 1 周归来后次日留存率 vs 原民心版 → **解释**：投资留存优于惩罚留存。
- **观察**：7 日封地视觉变化被多少玩家注意到 → **目标 > 70%**。
