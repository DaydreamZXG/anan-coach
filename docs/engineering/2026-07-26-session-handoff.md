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

## 三·增补（2026-07-27 晚，额度收口时的小本本）

后端扎实化会话的完整状态见 docs/engineering/2026-07-27-backend-launch-audit.md（中期
止损版审计，P0/P1/修复顺序都在那里）。未开工/缓一缓的按优先级：

1. **P0 双雄**：发布管线解钉（activate_release.sh 钉在 20260721_52，积压 11 迁移）+
   生产备份启用（BACKUP_ENABLED=false 裸奔）——不修不能部署、不敢接用户。
2. **错因证据投影三连修**（审计 §一）：放开版本链头过滤保留确认前识别、投影补
   detail/sessionAtMs、trace/ink 时钟锚点互通。采集层不用动。
3. **缺失 worker systemd 单元补齐**（错因 agent 在生产会永远 queued）。
4. **审计续跑**：`wf_fc571df8-37f`（上线审计，5/6 腿有缓存）、`wf_a9dd83de-ba1`
   （功能全扫，mapper 有缓存）——resumeFromRunId 续跑，已完成腿免费。
5. **视觉指纹回填生成器（未产出即停，需重启）**：搜题图片匹配已核实为「架子全、弹药
   零」——匹配双通道在（fingerprint.py 文本 bigram+视觉 256 位 dHash）、拍照侧指纹在算
   （grok_vision.py:467），但生产 product.question_visual_fingerprints **0 行**。重启工作流
   脚本：workflows/scripts/visual-fingerprint-backfill-wf_befa101c-b6d.js（任务书完整：同源
   哈希生成器+契约化落库 SQL+页级 vs 题图哈希域差的裁剪方案；顺带
   representative_correct_ratio→occurrence_correct_ratio 难度锚 SQL，53,011 题覆盖）。
6. **图题供给地基（未产出即停，需重启）**：脚本
   workflows/scripts/visual-question-serving-prep-wf_dbd894b2-299.js（任务书完整：资产
   结构化透出+VISUAL_QUESTION_MODULES 白名单开关默认关+错因诚实降级）。产品决策已定
   （任务 #14/#15）：批次一图形推理 4,023 题（99.3% 有图），前端渲染就绪后拨闸；
   图推多模态错因排内测后。已核实：practice/mock 现排除全部 visual_required
   （real_attempt/repository.py:179、mock_exam/repository.py:113，1.5 万题=题库 27%）。
7. **前端窗口重开清单**：题面图片渲染 → 笔锋渲染（#13）→ 平板做题大件（原 §三 2）。
8. **frontend_orphan 断链核实**：GET /api/v2/goals/workspace、GET /api/v2/learning-profile
   ——前端在调、后端没有，若页面可达即线上 404。
9. **定位短测（#12）意外线索**：/api/v1/onboarding/placement-test* 后端骨架已存在
   （对账发现的 backend_orphan），实现可能比预想近。
10. 生产域 /coding-tools-mcp/* 无鉴权反代需立即核实关停（安全）。

## 四、操作备忘

- 视觉套件：认证机型 iPhone 12/13 mini；设备切换写 WeappLocalData 两个
  localstorage 的 device.current；DevTools 单实例互斥，连接死循环时
  pkill + cli auto --auto-port 9420 + 等端口 + 20s 再连；c10 场景天生慢
  （90s 预算）；--update 是唯一基线权威。
- 门禁四件套：npm run check / vitest（491）/ 后端 pytest -k "xingce or
  mock"（93）/ tokens+design validate。design-file.tokens.wxss 哈希锁定，
  prettier 全仓 glob 会碰坏它，只 prettier 明确改过的文件。
- 生产库只读审计走 scripts/server_import_product_knowledge.sh 的 ssh 约定。
