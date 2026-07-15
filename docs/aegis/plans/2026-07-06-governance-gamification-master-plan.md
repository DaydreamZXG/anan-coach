# 治国 Gamification Master Plan

## Goal

将考公搭子从"刷题工具"升级为"治国理政"游戏化体验。核心壁垒不变（5.5万题库 + 岗位进面分 + AI解析），但通过世界观包装、官场升迁系统、星露谷式UI架构，让刷题变成"披阅奏折、治理一方"。

注册选岗流程保留不动——选岗的目的是锚定**目标分数**（治绩目标），贯穿全App。不做真实地区古风映射（不搞"赣州府"这种），只做术语和视觉层的世界观包装。

## 行测6大板块（不可更改）

| 板块 | 题库存量 | 古风映射 | 底部快捷栏图标 |
|------|---------|---------|-------------|
| 常识判断 | 7,876 | 体察民情 | 民情 |
| 言语理解与表达 | 11,820 | 释义圣旨 | 圣旨 |
| 数量关系 | 6,217 | 统筹工程 | 工程 |
| 判断推理 | 14,368 | 堪破冤案 | 冤案 |
| 资料分析 | 8,205 | 核算钱粮 | 钱粮 |
| 政治理论 | 860 | 研读朝政 | 朝政 |

这6个板块是国考行测的固定结构，所有UI、快捷栏、能力雷达、抽题逻辑都必须基于这6个，不能多不能少。

## Architecture

### 现有基础（不重建）

| 现有能力 | 文件/表 | 复用方式 |
|---------|--------|---------|
| 5.5万题库 | `product.questions` + 16张关联表 | 直接复用，只加世界观标签 |
| 加权抽题 | `knowledge/repository.py` `select_weighted_practice_questions` | 复用，包一层"政务卡片"格式 |
| 规则引擎计划 | `taskstream/service.py` `plan_basis="rules_v1"` | 复用，输出包装为"今日政务" |
| 岗位搜索+进面分 | `positions/` domain + `position_entry_score_evidence` | 复用，目标分=治绩目标 |
| AI provider + 审计 | `ai/` domain + `ai_audit` schema | 复用，加幕僚prompt模板 |
| 摸底测试 | `onboarding/` domain + `learner.placement_attempts` | 复用，包装为"初任考评" |
| 模考系统 | `mock_exam/` domain | 复用，包装为"会试" |
| 申论批阅 | `essay/` domain | 复用，包装为"平灾奏折" |
| 用户画像 | `learner.profiles` | 复用，加官职字段 |

### 新增模块

| 新模块 | 后端domain | 数据库表 | 说明 |
|-------|-----------|---------|------|
| 官场升迁系统 | `rank_system` | `learner.official_ranks` + `product.official_rank_config` + `learner.rank_events` | 九品到一品，经验值/民心/品级 |
| 政务卡片 | `government_cards` | 无新表（复用taskstream输出） | 把今日任务包装成"政务卷宗"格式 |
| 御史台巡视 | `censor` | `learner.censor_events` | 不活跃惩罚机制 |
| 智能熔断 | 融入 `rank_system` | `learner.module_locks` | 模块胜率达标后锁库 |
| AI幕僚话术 | 融入 `ai/` | 无新表 | prompt模板库 |
| 排行榜 | `leaderboard` | 物化视图或缓存表 | 同榜进士 |
| 变现系统 | `billing` | `learner.subscriptions` + `learner.consumables` + `learner.consumable_usages` | IAP订阅+微交易 |

## Tech Stack

不变。现有技术栈完全支撑本方案：

| Layer | Choice | 说明 |
|-------|--------|------|
| 后端 | Python + FastAPI + SQLAlchemy（原生SQL） | 新domain沿用四件套模式 |
| 前端 | SwiftUI（原生，iPhone+iPad） | 新UI组件用SwiftUI实现 |
| 数据库 | PostgreSQL | 新表加在 learner / product schema |
| AI | Gemini 3.5 Flash + 审计 | 幕僚话术复用现有provider |
| 部署 | Caddy + systemd | 不变 |

## 设计原则

### 1. 世界观包装层（不是重写）

所有古风元素是**视觉+文案层**的改造，不触碰数据逻辑：
- 术语映射表（见下文），前后端各维护一份
- UI配色/字体/图标替换
- AI话术用幕僚口吻

**术语映射表：**

| 现有术语 | 古风术语 |
|---------|---------|
| 今日任务 | 今日政务 |
| 行测练习 | 批阅奏折 |
| 资料分析 | 核算钱粮 |
| 判断推理 | 堪破冤案 |
| 言语理解与表达 | 释义圣旨 |
| 数量关系 | 统筹工程 |
| 常识判断 | 体察民情 |
| 政治理论 | 研读朝政 |
| 申论 | 平灾奏折 |
| 模考 | 会试 |
| 错题 | 误判案卷 |
| 每日目标 | 治绩目标 |
| 学习时长 | 理政时长 |
| 目标分数 | 治绩目标 |
| 摸底测试 | 初任考评 |
| 岗位 | 官职 |
| 选岗 | 择官选任 |
| 完成引导 | 领旨赴任 |

### 2. 星露谷UI架构参考（非像素风）

参考星露谷的**信息架构和反馈机制**，视觉皮肤用古风而非像素风：

| 星露谷元素 | 公考映射 | 实现方式 |
|-----------|---------|---------|
| 顶部状态栏（体力+金币+时钟） | 治绩栏（品级徽章+民心值+精力值+今日进度） | SwiftUI固定顶部条 |
| 底部快捷栏（12格hotbar） | 6大板块快捷栏 | SwiftUI底部TabView定制 |
| 体力值系统 | 精力值：做题消耗，每日恢复 | 后端`learner.official_ranks.stamina` |
| 技能面板（5技能等级条） | 6板块能力条 | 复用`baseline_weak_modules`+练习记录 |
| 任务板信件 | 今日政务卷宗（翻开动画） | SwiftUI卡片+动画 |
| 季节系统（春夏秋冬变色） | 备考阶段（基础/强化/冲刺）UI渐变 | 根据考试日期计算阶段 |
| 对话框打字机效果 | AI幕僚说话逐字显示 | SwiftUI TextRenderer |
| 升级动画（金光） | 品级晋升动画（印章盖章+金光） | SwiftUI动画 |

**视觉风格：**
- 边框/面板：羊皮纸/木纹质感（非像素）
- 图标：精简线条画（SF Symbols风格）
- 字体：宋体/楷体（标题）+ 黑体（正文）
- 进度条/能力条：星露谷式填充条
- 配色：竹简色(#D4C5A9)、朱砂红(#C73E1D)、水墨黑(#2C2C2C)、青瓷绿(#7BA7BC)

### 3. Generative UI替代方案（混合模式）

不做AI实时生成卡片，用混合模式：

| 环节 | 技术 | 延迟 |
|------|------|------|
| 抽题 | 规则引擎（复用`select_weighted_practice_questions`） | 毫秒 |
| 卡片渲染 | SwiftUI本地模板（3-5种布局） | 毫秒 |
| 幕僚点评 | 预生成（每日批量）或模板化 | 零 |
| 错题解析 | AI实时（仅错题时触发） | 2-3秒 |
| 御史台事件 | 规则触发 + 模板文案 | 毫秒 |

### 4. 选岗=定目标分（贯穿全局）

注册选岗流程不删不减，只换皮。选岗的目的是锚定**目标分数**：

```
选考试年份 → 选学历/专业/政治面貌 → 选省份/城市 → 搜索岗位 → 选定岗位 →
摸底测试(估行测分) → 确认目标分 → 领旨赴任（进入App）
```

目标分（治绩目标）贯穿全App：
- 目标分 - 预估分 = 分差（距离上岸还差多少）
- 分差缩小 = 品级经验增长
- 模考超目标分 = 直接升迁 + 民心大涨
- 分差100%消除 = 一品首辅（上岸）

## 九品官制体系

| 品级 | 名称 | 经验阈值 | UI主题 | 解锁特权 |
|-----|------|---------|--------|---------|
| 九品 | 县令 | 0 | 竹简 | 基础做题 |
| 八品 | 县丞 | 500 | 竹简 | 错题本 |
| 七品 | 知县 | 1,500 | 粗纸 | 每日政务卡片 |
| 六品 | 知州 | 3,000 | 粗纸 | AI幕僚(基础) |
| 五品 | 知府 | 6,000 | 水墨 | 雷达图+AI总结 |
| 四品 | 道员 | 10,000 | 水墨 | 申论Boss战 |
| 三品 | 布政使 | 16,000 | 水墨 | 上帝视角(部分) |
| 二品 | 巡抚 | 25,000 | 黑金 | AI无限解析 |
| 一品 | 首辅 | 40,000 | 黑金 | 全部解锁 |

**经验值规则：**
- 答对1题行测：+10 XP
- 答对1题申论：+30 XP
- 连击5题：额外+20 XP
- 每日完成全部政务：+50 XP
- 模考完成：+100 XP
- 模考超目标分：+200 XP

**民心值规则：**
- 答对：+1民心
- 答错：-1民心
- 连续3天不刷题：-10民心/天
- 民心<30：UI背景变暗红
- 民心<0：触发降级判定

## 数据库设计

### 新增表

```sql
-- 官职等级（每个用户一行）
CREATE TABLE learner.official_ranks (
    user_id            uuid PRIMARY KEY REFERENCES learner.users(user_id),
    rank_code          text NOT NULL DEFAULT 'nine_pin_xianling',
    rank_level         int NOT NULL DEFAULT 9,
    xp                 int NOT NULL DEFAULT 0,
    fame               int NOT NULL DEFAULT 50,       -- 民心值，初始50
    stamina            int NOT NULL DEFAULT 100,      -- 精力值
    stamina_updated_at timestamptz DEFAULT now(),
    consecutive_days   int NOT NULL DEFAULT 0,
    last_active_date   date,
    demerits           int NOT NULL DEFAULT 0,
    promoted_at        timestamptz DEFAULT now(),
    created_at         timestamptz DEFAULT now(),
    updated_at         timestamptz DEFAULT now()
);

-- 品级配置（产品配置表）
CREATE TABLE product.official_rank_config (
    rank_code         text PRIMARY KEY,
    rank_level        int NOT NULL,
    rank_name         text NOT NULL,
    xp_threshold      int NOT NULL,
    ui_theme          text NOT NULL,         -- bamboo / rough_paper / ink / gold
    ai_privilege      text,
    unlock_features   text[] DEFAULT '{}'
);

-- 升降级事件日志
CREATE TABLE learner.rank_events (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES learner.users(user_id),
    event_type  text NOT NULL,               -- promotion / demotion / demerit / fame_change / xp_gain
    old_rank    text,
    new_rank    text,
    xp_delta    int,
    fame_delta  int,
    reason      text,
    created_at  timestamptz DEFAULT now()
);

-- 模块锁定（智能熔断）
CREATE TABLE learner.module_locks (
    lock_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES learner.users(user_id),
    module_name  text NOT NULL,              -- 常识判断 / 言语理解与表达 / 数量关系 / 判断推理 / 资料分析 / 政治理论
    locked_at    timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    trigger_accuracy float,                  -- 触发时的胜率
    ai_message   text,                       -- 幕僚话术
    created_at   timestamptz DEFAULT now(),
    UNIQUE(user_id, module_name)
);

-- 御史台事件
CREATE TABLE learner.censor_events (
    event_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES learner.users(user_id),
    trigger_date   date NOT NULL,
    question_ids   text[] NOT NULL,          -- 5道高难题
    status         text NOT NULL DEFAULT 'pending', -- pending / passed / failed / expired
    deadline       timestamptz NOT NULL,     -- 48小时
    resolved_at    timestamptz,
    penalty_applied boolean DEFAULT false,
    created_at     timestamptz DEFAULT now()
);

-- 订阅
CREATE TABLE learner.subscriptions (
    subscription_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              uuid NOT NULL REFERENCES learner.users(user_id),
    plan_code            text NOT NULL,      -- chief_counselor_monthly / annual
    status               text NOT NULL,      -- active / expired / cancelled
    started_at           timestamptz,
    expires_at           timestamptz,
    apple_transaction_id text,
    created_at           timestamptz DEFAULT now()
);

-- 消耗品（锦囊等）
CREATE TABLE learner.consumables (
    consumable_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES learner.users(user_id),
    type          text NOT NULL,             -- jiang_nang / mian_si_gold
    quantity      int NOT NULL DEFAULT 0,
    source        text,                      -- purchase / reward / rank_promotion
    created_at    timestamptz DEFAULT now()
);

-- 消耗品使用记录
CREATE TABLE learner.consumable_usages (
    usage_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES learner.users(user_id),
    consumable_type  text NOT NULL,
    related_event_id uuid,                   -- 关联到censor_event等
    used_at          timestamptz DEFAULT now()
);
```

### 品级配置初始数据

```sql
INSERT INTO product.official_rank_config VALUES
('nine_pin_xianling',    9, '九品县令',     0,     'bamboo',      '基础做题',         '{}'),
('eight_pin_xiancheng',  8, '八品县丞',     500,   'bamboo',      '错题本',           '{"wrong_question_book"}'),
('seven_pin_zhixian',    7, '七品知县',     1500,  'rough_paper', '每日政务卡片',     '{"government_cards"}'),
('six_pin_zhizhou',      6, '六品知州',     3000,  'rough_paper', 'AI幕僚(基础)',     '{"ai_counselor_basic"}'),
('five_pin_zhifu',       5, '五品知府',     6000,  'ink',         '雷达图+AI总结',    '{"radar_chart","ai_summary"}'),
('four_pin_daoyuan',     4, '四品道员',     10000, 'ink',         '申论Boss战',       '{"essay_boss"}'),
('three_pin_buzhengshi', 3, '三品布政使',   16000, 'ink',         '上帝视角(部分)',   '{"god_view_partial"}'),
('two_pin_xunfu',        2, '二品巡抚',     25000, 'gold',        'AI无限解析',       '{"ai_unlimited"}'),
('one_pin_shoufu',       1, '一品首辅',     40000, 'gold',        '全部解锁',         '{"all_unlocked"}');
```

## 落地路线图

### 阶段1：世界观骨架（2-3周）

**目标：** App看起来像"治国"，用起来还是刷题

| 任务 | 后端 | 前端 | 依赖 |
|------|------|------|------|
| 官场升迁系统后端 | 新建`rank_system` domain，3张表，品级配置 | - | 无 |
| 品级展示组件 | - | 顶部状态栏（品级徽章+民心+精力+今日进度） | 后端完成 |
| 古风UI主题 | - | 配色/字体/术语映射表替换 | 无 |
| 底部6格快捷栏 | - | 6板块切换（复用现有模块筛选） | 无 |
| 今日政务卡片 | taskstream输出包装为"政务卷宗"格式 | 卡片UI（翻开式动画） | 无 |
| AI幕僚话术 | prompt模板库（`ai/prompt_templates.py`） | 对话框打字机效果 | 无 |
| 注册流程换皮 | 术语映射 | "领旨赴任"首屏 | 品级系统完成 |

**验证标准：**
- 新用户注册后看到"领旨赴任"卷轴，品级=九品县令
- 顶部状态栏显示品级、民心、精力、今日进度
- 底部6格快捷栏可切换6大板块
- 今日政务以卡片形式展示，AI幕僚用古风口吻说话

### 阶段2：游戏化闭环（2周）

**目标：** 答题有正反馈，不答题有惩罚

| 任务 | 后端 | 前端 | 依赖 |
|------|------|------|------|
| 心流滑动交互 | - | Tinder-like卡片滑动（左滑跳过/右滑作答） | 阶段1卡片UI |
| 答题反馈动画 | - | 盖章动画（准/驳）+连击水墨火焰 | 阶段1 |
| 经验值+民心实时变动 | 答题后更新`official_ranks` | 数字滚动动画 | 阶段1品级系统 |
| 智能熔断(恩准休沐) | `rank_system/fuse_service.py`检查胜率→锁库 | 锁定UI+幕僚劝退弹窗 | 阶段1 |
| 御史台巡视 | 定时任务检查不活跃用户→创建censor_event | 御史台事件卡片+推送 | 阶段1 |
| 民心红线 | 民心<30时返回标记 | UI背景渐变暗红 | 阶段1 |
| 品级升降级动画 | 品级变更时记录`rank_events` | 印章盖章+金光升迁动画 | 阶段1 |

**验证标准：**
- 答对题盖"准"字绿印，答错盖"驳"字红印
- 连续答对5题屏幕边缘燃起水墨火焰
- 某模块胜率≥85%时触发熔断，题库锁定3天，幕僚劝退
- 连续3天不刷题触发御史台，5道高难题，48小时内不答则降级
- 民心低于30时UI变暗红

### 阶段3：差异化功能（2周）

**目标：** 比粉笔更懂"护短"和"做减法"

| 任务 | 后端 | 前端 | 依赖 |
|------|------|------|------|
| 上帝视角解析 | `question_explanations`增加`god_view_tip`字段 | 解析页增加"幕僚献计"区块 | 无 |
| 出题人审判 | 全网正确率<20%的题标记为"怪题" | "焚毁(跳过)"按钮+AI陪骂话术 | 无 |
| 申论Boss战包装 | 复用`essay` domain | 30分钟倒计时+"平灾奏折"主题 | 阶段1 |
| 五品知府UI进化 | - | 水墨雷达图+精致仪表盘 | 阶段1品级到五品 |
| 同榜进士排行榜 | 物化视图或缓存表（按XP排序） | 排行榜页面+品级光环 | 阶段1 |
| 备考阶段变色 | 根据考试日期计算阶段 | UI色调随阶段渐变 | 无 |

**验证标准：**
- 错题解析有"幕僚献计"秒杀技巧
- 正确率<20%的怪题可"焚毁"跳过
- 申论以"平灾奏折"形式呈现，30分钟倒计时
- 五品以上用户看到水墨雷达图
- 排行榜显示同榜进士，高品级有光环

### 阶段4：变现（3-4周）

**目标：** SaaS订阅 + 微交易

| 任务 | 后端 | 前端 | 依赖 |
|------|------|------|------|
| Apple IAP接入 | StoreKit 2收据验证 | 购买页面 | 无 |
| SaaS订阅(首席幕僚) | `subscriptions`表+权益管控 | 订阅页面(38元/月) | IAP完成 |
| 锦囊微交易 | `consumables`表+消耗逻辑 | 购买锦囊(2元/个) | IAP完成 |
| 赛季战令 | 战令进度+奖励发放 | 战令页面+黑金UI皮肤 | 订阅完成 |
| 二品巡抚黑金UI | - | 黑金UI皮肤（仅二品以上可见） | 阶段3 |

**验证标准：**
- 可通过Apple IAP购买月度订阅（38元）
- 订阅用户解锁无限AI解析+申论深度批阅
- 御史台降级时可花2元买锦囊保命
- 赛季战令有进度条和奖励

## 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 古风UI劝退现代用户 | 高 | 保留现代风作为切换选项，A/B测试 |
| 御史台惩罚太重导致流失 | 高 | 第一次只警告不降级，给3天宽限期 |
| AI话术重复感强 | 中 | 预制50+模板随机组合，不是每次都调AI |
| 政治理论题量少(860题) | 低 | 正常使用，不够就不够 |
| 支付审核周期长 | 高 | 阶段4才做，先验证留存 |
| UI进化工作量大 | 高 | 先只做竹简+水墨两套，黑金等用户到二品再开发 |
| 精力值系统可能让用户不爽 | 中 | 精力值只限制单次时长，不限制每日总量 |

## 不做清单

- 不做真实地区古风映射（不搞"赣州府"，选岗只为定目标分）
- 不做AI实时生成卡片（用混合模式替代）
- 不做像素风UI（用古风质感+星露谷信息架构）
- 不删减任何现有注册流程
- 不改6大行测板块的分类
- 不做打卡天数系统（用官场品级替代）

## Compatibility Boundary

- `product.questions` 及关联16张表：只读，不改结构
- `learner.profiles`：只加字段不改语义
- `taskstream/service.py`：输出格式兼容，加包装层不改核心逻辑
- `knowledge/repository.py`：`select_weighted_practice_questions`不改签名
- 现有API端点全部保留，新功能加新端点
- iOS现有页面结构保留，新UI组件以新增为主

## Verification

每个阶段完成后的验证方式：

**阶段1：** 新用户走完注册→看到"领旨赴任"→顶部状态栏→底部6格→今日政务卡片
**阶段2：** 做题→盖章动画→连击火焰→3天不刷→御史台→熔断锁库
**阶段3：** 错题→幕僚献计→怪题焚毁→申论Boss→排行榜
**阶段4：** 购买订阅→解锁权益→买锦囊→战令进度

## Authority

本计划是"治国"游戏化方案的唯一执行权威。后续具体实施时，每个阶段可拆分为子计划，但必须引用本计划为父计划。
