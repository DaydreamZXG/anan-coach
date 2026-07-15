# 34 期方舟 coding 长程众测 — 表单粘贴稿

用途：在「新建任务」弹窗中直接复制。  
Harness：选 **云沙箱 Claude Code**。  
任务来源：选 **工作任务（推荐）**。

字体已 vendored（云端无需访问 GitHub）：

- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Regular.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Medium.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Bold.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/OFL-MapleMono.txt`
- 说明：`third_party/maple-font/README.md`

权威规格：

- `docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md`
- `docs/aegis/plans/2026-07-11-longrun-eval-ai-driven-an-an-brief.md`

---

## 字段：任务描述（必填）

下面整段复制到「任务描述」：

```text
【工作任务】考公搭子（iOS SwiftUI + FastAPI）产品向 AI 上岸顾问重构

背景：
我是这款公考学习 App 的负责人。当前仓库已有可运行的 iOS 客户端与 Python 后端、题库/岗位/练习/AI 问答等能力，但产品叙事偏「官场 RPG」（品级、XP、仕途、师爷沈砚、授品仪式、竹简古风 UI），与真实商业目标不一致。真实目标是做成「纯 AI 驱动的考公智能决策 + 私教」：选岗决策、目标分差、个性化训练、错因讲解、模考复盘；陪伴只要能解惑的轻角色，不要养成/解锁经济。

请你在本仓库内完成长程改造（禁止换技术栈，禁止新建 Flutter/RN 工程），严格遵守仓库内已写好的定案与任务书：

1) docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md
2) docs/aegis/plans/2026-07-11-longrun-eval-ai-driven-an-an-brief.md

锁定决策（不得自行改判）：
- 去掉用户可见的游戏化 RPG：品级/XP/仕途/民心/精力/授品/升迁解锁等
- AI 伙伴改名为「岸岸」：现代吉祥物形象 + 学长感解惑助教话术；禁止沈砚/师爷/主公/县令叙事
- UI 改为现代 AI 产品风（非竹简朱砂默认主题）
- 字体必须使用仓库已下载的本地 Maple Mono CN（见 apps/ios/Gongkao/Gongkao/Resources/Fonts/），嵌入 iOS 并附 OFL；禁止依赖外网再下字体；不要嵌入 Nerd Font 图标包
- 域数据档位 B：岗位全量见 import-staging/positions/；题结构抽样见 data/question-bank-sample/（约 1500 题 + taxonomy，无全量题图）。读 data/DOMAIN-DATA-README.md。分差/选岗/讲解须对接真实字段语义，禁止假造无关模型；禁止要求下载 5GB 题图
- 保留并强化 AI 解惑能力（companion 问答、citations、额度）；功能解锁走会员/配额，不走官阶
- 顶栏/首页以「距目标分差 / 今日训练」为英雄指标，而不是品级条

建议按任务书 Phase 0→6 推进：
Phase0 测绘差距 → Phase1 岸岸人设与文案 → Phase2 去 RPG 可见层 → Phase3 现代主题 + 本地 Maple 字体接入 → Phase4 岸岸解惑 UI → Phase5 Onboarding/信息架构 → Phase6 全量验证与证据包。

验收（必须）：
- 用户可见路径无官场 RPG 与旧人设
- 岸岸解惑主路径可编译可走通
- Maple 三字重已从本地 Resources/Fonts 注册使用
- backend：pytest（backend/tests）通过
- iOS：iPhone 与 iPad Simulator xcodebuild 成功（CODE_SIGNING_ALLOWED=NO）
- 产出证据目录：docs/aegis/work/2026-07-11-degamify-anan/<你的run-id>/（含 gap、phase log、evidence、risks）

约束：
- 真实改代码，禁止只写文档交差
- 禁止无目标全仓 rename / 空转刷工具调用
- 本环境可能无法访问外网下载大资源：字体已在仓库本地，必须用本地路径
- 不要部署生产、不要提交密钥

请从阅读上述 Spec 与任务书、搜索仓库中的 rank/师爷/Guzhu/Investiture 等符号开始，再分阶段实现并持续跑测试与构建。
```

---

## 字段：题目来源 / 背景说明（必填）

> 平台说明：本栏**不会**进入模型 Prompt。只写工作场景、仓库真实性、上次用何模型及完成效果。  
> **开发指令写在「任务描述」里**，不要堆在本栏。

下面整段复制：

```text
上传内容为本人在研商业项目「考公搭子」的真实代码仓库（iOS SwiftUI 客户端 + FastAPI 后端 + 题库/岗位/练习/AI 问答等业务域），非课程作业、非演示玩具仓。数据仅用于本次模型评测，请按平台约定使用。

我的工作内容：
我是该 App 的产品与研发负责人，负责公考备考场景下的选岗辅助、每日训练、错因讲解、模考复盘与 AI 陪伴能力的设计与落地。近期产品方向已明确为「AI 上岸顾问」（选岗决策、目标分差、个性化训练、解惑），需要把仓库里先前叠加上去的官场 RPG 叙事（品级/XP/仕途/师爷人设/古风壳）拆掉，换成现代 AI 产品体验，并接入本地已准备好的品牌字体与新 AI 伙伴「岸岸」。本次众测提交的即是该重构任务的真实工程现场。

上次尝试解决同类/前置问题时使用的模型与效果：
- 主要使用 Claude（Claude Code / 长会话）及 Codex 类编程助手，在 2026-07 上旬完成过：后端学习闭环与部分 AI 域接口、iOS 壳与练习流、游戏化 P0–P3（品级条、升迁反馈、师爷人设、授品 onboarding）以及服务端部署联调。
- 完成效果：功能面能跑、内测包可装，但产品主轴被游戏化带偏——分差/选岗决策未成为首页英雄指标，人设与视觉仍是官场古风，和已定的「纯 AI 驱动上岸」方案不一致；去 RPG、换「岸岸」、现代 UI + 本地 Maple 字体嵌入等工作尚未系统做完，需要一次长程、可验证的工程改造（正是本次任务描述中的范围）。

说明：具体改造步骤、验收命令与禁止项已写在「任务描述」中，本栏仅作题目来源与工作背景说明。
```

---

## 表单其它选项（对照截图）

| 表单项 | 填写 |
| --- | --- |
| 加入评测任务 | 34期方舟coding长程任务众测（4 个模型…） |
| Harness | **云沙箱 Claude Code** |
| 任务描述 | 用上一节「任务描述」整段 |
| 任务来源 | **工作任务（推荐）** |
| 背景说明 | 用上一节「背景说明」整段 |

若还有「仓库地址 / 代码包上传」：

- 优先上传或挂载本仓库（含 `Resources/Fonts` 三字重，约 53MB）
- 确认云端能看到 `apps/ios/Gongkao/Gongkao/Resources/Fonts/*.ttf`
- 不要用「仅文档、无代码」的空仓库

若表单有「是否需要 GPU / 联网」：

- 联网：最好有（跑 pytest 一般不需要外网；若模型想查文档可开），但**字体不得依赖联网**
- GPU：不需要

---

## 提交前自检

- [ ] 任务描述未直接粘贴众测场景模板原文当 Prompt  
- [ ] 已写明工作任务与真实产品  
- [ ] Spec / 任务书路径写进描述  
- [ ] 字体本地路径写进描述  
- [ ] 验收命令写清  
- [ ] 本地三字重文件存在且将随代码包进沙箱  
