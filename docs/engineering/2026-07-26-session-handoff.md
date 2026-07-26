# 2026-07-26 大会话交接文档（UI 统一 + 平板架构 + AI 数据地基）

本文档是当日 23+ commit 的续作指南。接手者（人类或任何 AI）从这里继续，
配合 memory 索引与 docs/design/2026-07-26-tablet-layout-master-plan.md。

## 一、已收口（全部落库、门禁全绿）

1. **UI 体系**：青色主系（语义层映射，靛蓝只剩 AI 渐变）、灰阶 4 档、字号
   13 档单一家族、按钮五档、彩字白名单、卡片无竖线；validate-tokens 五条
   退役规则防回流。规范：apps/wechat/DESIGN_SPEC.md。
2. **平板架构**：三断点（<768 手机 / 768 轨+单列 / 1024 多栏）、悬浮胶囊
   Tab、--rpx 去放大开关（注意其两步载体写法，见 tokens/index.wxss 注释，
   有编译圆整与 calc 内 rpx 两个坑，e07a96d1 有完整教训）、五类页面模板
   全量铺开、粉笔式做题选项。
3. **采集契约增量 v1**（a9f5844d）：毫秒 atMs/sessionAtMs、question_entered/
   left、material_scrolled、笔画逐点 t、擦除墓碑 erasedAtMs、溢出标记、
   模考同套采集。全部可选字段零迁移。
4. **2A**（08764a66）：知识点级能力估计（calibration knowledgePointEstimates
   + learning_brain 弱项 Top3）、争议题过滤进错误模式聚合与错因证据。
5. **杂修**：错误话术中枢兜底、今日空状态三分支人话、聊天气泡语义、
   hideTabBar 弹层方案、资料编辑居中对话框、隐私入口合并。

## 二、在途（本会话后台，未提交）

- **2B 工作流**（错因 AI 消费）：load_action_trace/load_ink_formulas 冻结
  工具 + prompt v2 → 单题级错因候选（展示级）→ user_replacement 结构化
  回流。收口动作：全量门禁（前端 vitest + 后端 pytest xingce）→ 提交。
- **知识树 v2 工作流**：词表治理资产（42 脏模块名→6+隔离、材料形态/考点
  拆面）+ 生成器四级父链与自适应折叠（<30 题上收）。**不执行生产刷新**，
  留发布流程。审计事实：知识族 325（100%）/知识点 247（85%）/微技能 334
  （100%），生产库只读审计已做（本文档 git log 附近的会话记录）。
- **视觉双采集链**：手机基线全量 --update → iPad Pro 终审 capture → 桌面
  「岸岸手机端截图-定稿」「岸岸平板端截图-iPad/全量最新」。

## 三、待办排序（下一会话从这里领任务）

1. 在途三项的收口提交（如未完成）
2. 平板做题大件：材料题组连续滚动（memory: tablet-material-group-scroll，
   方案已写）→ 可拖分栏把手 → 模考答题卡侧挂 → 旋转状态验证
3. iPad 认证机型进视觉流水线（平板防回归基线）
4. 知识树生产刷新执行 + taskstream 按知识点选题（产品拍板项）
5. 申论材料轨迹（memory: essay-material-trajectory-backlog，backend-first）
6. 全量文案巡礼：按 docs/design/2026-07-26-anan-voice-guide.md 的岸岸声音扫 58 页关键时刻（旗舰 6 处已改，见 voice guide commit）
7. M0 发布清单：生产部署积压迁移、真机验收、微信提审材料（见
   docs/engineering/2026-07-18-functional-gap-and-delivery-plan.md §6）

## 四、操作备忘

- 视觉套件：认证机型 iPhone 12/13 mini；设备切换写 WeappLocalData 两个
  localstorage 的 device.current；DevTools 单实例互斥，连接死循环时
  pkill + cli auto --auto-port 9420 + 等端口 + 20s 再连；c10 场景天生慢
  （90s 预算）；--update 是唯一基线权威。
- 门禁四件套：npm run check / vitest（491）/ 后端 pytest -k "xingce or
  mock"（93）/ tokens+design validate。design-file.tokens.wxss 哈希锁定，
  prettier 全仓 glob 会碰坏它，只 prettier 明确改过的文件。
- 生产库只读审计走 scripts/server_import_product_knowledge.sh 的 ssh 约定。
