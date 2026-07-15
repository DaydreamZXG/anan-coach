# AI 驱动去游戏化 + 伙伴「岸岸」产品定案

日期：2026-07-11  
状态：**Accepted（用户确认）**  
取代 / 降级：

- `docs/aegis/specs/2026-07-07-bureaucratic-survival-architecture-plan.md` → 官场 RPG **不再作为产品方向**
- `docs/aegis/specs/2026-07-09-ai-shangan-mascot-ui-spec.md` 中「鹿鸣 / 古风延续」→ 由本文件 **岸岸 + 现代 UI** 取代
- 治国/仕途/品级相关 plans 中与本文件冲突的条款 → 以本文件为准

## 1. 一句话定位

> **考公搭子是 AI 上岸顾问：选岗、算分差、定计划、解惑纠错、复盘冲刺。**  
> **岸岸是有吉祥物形象的学长感 AI 助教，负责讲清楚「为什么」和下一步；不是官场 NPC，也不是养成宠物。**

## 2. 用户确认项（Locked）

| 项 | 决定 |
| --- | --- |
| 产品主轴 | 纯 AI 驱动（对齐桌面 docx《纯 AI 驱动考公 APP 产品方案》） |
| 游戏化 RPG | **去掉**（品级 / XP 展示 / 仕途 / 民心 / 精力 / 授品 / 升迁解锁 / 熔断锁库展示） |
| 人设 | **C**：有名字的虚拟助教，**学长/同伴感**，无官职世界观 |
| 伙伴定位 | **B1**：固定现代吉祥物形象 + **教练式解惑话术** |
| 名字 | **岸岸** |
| 旧形象 | 沈砚 / 师爷 / 主公 / 县令 / 入仕授品 **整套退役** |
| 视觉 | **B**：现代 AI 产品风（非竹简朱砂官场） |
| 字体 | [Maple Font / Maple Mono](https://github.com/DaydreamZXG/maple-font)（OFL-1.1） |

## 3. 架构主从

```text
AI 中枢（主）     选岗决策 · 能力/分差 · 计划 · 错因教学 · 模考复盘
执行层（硬）      今日任务 · 刷题 · 申论 · 模考 · 错题
陪伴层（辅）      岸岸：解惑对话 + 轻状态反馈 + 提醒
                  ❌ 无 XP  ❌ 无升级树  ❌ 无功能解锁经济
```

### 3.1 岸岸职责

**必须：**

- 解惑：为什么错、知识点、避坑、下一步练什么
- 有据：引用方法卡 / 知识点 / 用户数据 / 岗位目标（沿用 companion citations）
- 学长语气：清楚、克制、可执行；可偶尔轻松，不卖萌抢戏
- 关键位：题目旁「让岸岸讲」、AI 伙伴页、首页一句建议气泡

**禁止：**

- 官场称呼与叙事（主公、师爷、批奏折、县令）
- 编造上岸概率或无依据分数
- 养成数值（喂食、忠诚、掉级）
- 用宠物奖励诱导刷舒适区题

**轻状态（允许的最轻陪伴）：**

- 今日任务完成 → 表情/短句「今天该练的做完了」
- 断更提醒仍以 **考试倒计时 + 分差** 为主，岸岸只是壳

### 3.2 话术示例

| 场景 | 岸岸 |
| --- | --- |
| 讲错题 | 「这题错在审题：问的是‘不正确’的一项。知识点是比重比较，下一步做 3 道同类限时。」 |
| 今日建议 | 「你距目标岗大约还差 12 分，资料分析性价比最高，今天优先 5 题。」 |
| 完成任务 | 「今日计划完成。分差要靠弱项堆出来，明天继续资料。」 |
| 额度用尽 | 「今天的免费讲解次数用完了，可在会员里继续问我。」 |

## 4. 视觉系统（现代 AI）

### 4.1 原则

- 干净、可信、数据优先；动效短且功能性
- **英雄指标：距上岸分差**，不是品级条
- 岸岸吉祥物统一形象；不出现官印/竹简主视觉

### 4.2 Token 方向（实现时新建 `BrandPalette` / `AppTheme`）

| Token | 方向 |
| --- | --- |
| background | 近白 / 浅灰；Dark 深灰 |
| surface | 白卡片，轻阴影或细边，圆角 12–16 |
| brand | 冷静蓝或青绿（可信/进步）— 实现时定 1 个主色 |
| positive / alert | 语义色，不与 brand 抢戏 |
| text | 高对比正文 + 次级灰 |
| module colors | 六大行测模块可区分，**去掉**「圣旨/体察民情」等别名 |

**退役：** `GuzhuPalette` 竹简/朱砂作为默认主题；过渡期可桥接，目标删除官场语义色名。

### 4.3 字体：Maple Font

- 来源：用户 fork `https://github.com/DaydreamZXG/maple-font`（上游 Maple Mono，**OFL-1.1**）
- iOS 嵌入须带 **OFL 许可副本**（App 资源或 Acknowledgements）

**工程用法（强制）：**

| 用途 | 字体选择 | 说明 |
| --- | --- | --- |
| 中文 UI 正文 / 标题 | **Maple Mono CN**（或用户 fork 构建出的 CN 静态 TTF） | App 必须覆盖中文；不要用仅 Latin 的最小包当唯一字体 |
| 数字 / 分差 / 代码感标签 | Maple Mono（Regular/Medium/Bold） | 等宽利于分差、正确率对齐 |
| Nerd Font / 图标字形包 | **不要**嵌入 App UI | 体积与无关图标；UI 用 SF Symbols + 岸岸插画 |
| 回退 | 系统 `SF Pro` / `PingFang SC` | 缺字或加载失败时 |

**注意（实现时遵守）：**

- Maple 是 **等宽** 字体：长段落行宽、字重层级与非等宽 UI 字不同；标题与正文用字重区分，控制字号与行高（建议 lineSpacing 略增）。
- CN 包体积较大：只嵌入实际用到的 weight（建议 Regular + Medium + Bold，至多再加 Light）。
- Mac 模拟器优先 unhinted；若测低分屏再评估 hinted——iOS 通常用 unhinted TTF/OTF。
- 用户 fork 无 Release 时，可从上游 [subframe7536/maple-font releases](https://github.com/subframe7536/maple-font/releases) 取 **CN** 构建，或按 fork 的 `build.py --cn` 产出后放入 `apps/ios/.../Resources/Fonts/`。

### 4.4 信息架构（目标）

```text
今日     作战室：目标岗 · 分差 · 今日任务 · 岸岸一句话
训练     智能练习 / 错题 / 模考
岸岸     解惑对话 · 讲解历史 · AI 额度
我的     目标与计划 · 会员 · 设置
```

顶栏：`目标岗短名 | 距上岸约 X 分 | 今日 n/m | 岸岸头像`  
禁止：品级徽章、XP 进度、民心/精力。

Onboarding 收官：目标组合 + 分差 → 认识岸岸 → 第一条任务。  
**禁止**授九品 / 入仕仪式。

## 5. 文案词典（强制替换）

| 旧 | 新 |
| --- | --- |
| 今日政务 | 今日训练 / 今日计划 |
| 会试 | 模考 |
| 仕途 | 删除；进度用「我的进度 / 能力」 |
| 主公 / 县令 / 师爷 / 沈砚 | 你 / 岸岸 |
| 民心 / 精力 / 恩典 | 删除展示 |
| +N XP | 错因摘要 / 正确率或弱项进度 |
| 领旨上任 | 开始今日训练 |
| 批阅 / 案牍 | 删除 |

## 6. 工程退役与保留

### 6.1 产品可见层 — 删除或停用

- iOS：`RankDetailView`、`InvestitureView`（授官）、升迁向 `CelebrationView`、StatusBar 品级/XP、战报民心
- 导航仕途入口、官场 onboarding 收官
- 任务反馈 XP 胶囊

### 6.2 保留并改壳

- `companion` 解惑 API、citations、quota（人设 prompt 改为岸岸）
- `position_decision` / `calibration` / `taskstream` / practice / essay / mock_exam / knowledge / method_cards / commerce

### 6.3 后端 rank_system

1. **Phase A（快）**：iOS 不请求、不展示；练习路径不强调 XP；companion 上下文去掉 rank/fame  
2. **Phase B**：停写 XP/升迁；路由标记废弃  
3. **Phase C（无用户可狠）**：删除或归档表与配置  

功能解锁只走 **commerce 会员/配额**，不走官阶。

## 7. 实施切片顺序

1. 本 Spec 入库（本文件）  
2. 岸岸人设 prompt + 文案词典落地（backend companion + iOS 字符串）  
3. 嵌入 Maple CN 字体 + `AppTheme` / `BrandPalette` 底座  
4. 顶栏分差 + 去品级；首页作战室去官场  
5. 岸岸页 / 讲解入口现代对话 UI + 吉祥物占位图  
6. 停 rank 展示与 XP 反馈；Onboarding 新收官  
7. 删除死代码与古风残留；更新 README 产品一句话  

## 8. 验收标准

- [ ] 用户路径中无品级/XP/授官/师爷文案  
- [ ] 顶栏可见分差（或明确加载/无目标态），而非官阶  
- [ ] 问岸岸可解惑且带 citations（或等价依据）  
- [ ] UI 为现代浅色/深色 AI 风，非竹简默认主题  
- [ ] Maple 字体在关键界面生效；OFL 许可随包  
- [ ] 会员/AI 额度不依赖「升品」  
- [ ] 后端关键 pytest 与 iOS simulator build 通过  

## 9. 非目标（本阶段不做）

- Android 客户端  
- 封地 / 排行榜 / 御史惩罚玩法  
- 重做题库算法大改  
- 为烧调用次数的无目标全仓 rename  

## 10. 权威引用

- 用户产品方案：`/Users/zxg/Desktop/未命名文件夹/纯AI驱动考公APP产品方案.docx`  
- 改造计划：`docs/aegis/plans/2026-07-09-ai-shangan-advisor-transformation.md`  
- 字体：`https://github.com/DaydreamZXG/maple-font`（OFL-1.1）  
- 代码：`apps/ios/Gongkao`、`backend/app/domains/*`  
