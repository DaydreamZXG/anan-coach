# 岸岸学习智能闭环 v1 交接

本阶段以 `a46e655cd` 为基线，只收口 Companion 做题上下文、行测 SelectionIntent、ScratchEvidenceProjection/Cause 冻结证据和 provider-neutral Xfyun 公式识别代码路径。它不代表后端已部署、小程序已上传或讯飞公网联调已通过。

## 新边界

- Companion retrieval 从当前用户 active 的 `xingce_task_sessions.safe_bundle` 读冻结题目和关联材料，并只从最新 checkpoint 投影用户选择、排除、信心、标记、跳过、用时、草稿、材料位置和带 anchor 的批注。无 anchor 笔迹只表达 `present_but_semantic_unknown`，不猜文字。
- `answering` 只给模型 safe bundle 和用户自己的当前轨迹；正确答案、标准解析和评分只在数据库真实状态为 `submitted/settled` 后二次查询。学习问题会进入 Tutor 推理，只有提示词注入/密钥/越权请求直接拦截；答题期输出 guard 只拦截明确宣布最终选项的回复。
- Planning 生成并持久 `xingce-selection-intent@v1`；Task Runtime 只消费冻结 Intent 并返回 `xingce-selection-result@v1`。新路径的稳定排序键为 `intentId + userId + questionId`，历史 null Intent 仍走原 selector，`xingce-module-selection@v1` 仍可提交。
- 固定抽题层级是 `exact_knowledge_and_cause → same_knowledge → same_micro_skill → module → high_frequency_cold_start`。高频冷启动始终限定在 Intent 的 `targetModule`；样本充足但节点不可用时不会退到高频。真实 Cause `candidate` 题型/微技能会改变最终过滤，跨模块绑定 fail closed。
- 知识点弱项投影下沉到 Calibration 中立服务，Planning 不再依赖 Learning Brain。样本门槛、known/unknown 难度、grounded cause、曝光/已答去重和稳定回放都有直接测试。
- 新提交按题生成不可变 `scratch-evidence-projection@v1`。Cause run 冻结精确 projection ref 和 `formula-version:<id>:<latex sha256>`；新 run 不动态读 formula head，旧 run 未冻结 formula ref 时保留历史动态读兼容。
- 公式证据策略固定为 `formula-confidence-policy@v1`：用户确认为 strong；仅完整回执、指纹可验且 provider-reported 置信度不低于 `0.95` 的未确认候选为 medium；低置信、未知或回执不完整均为 weak。该阈值未经学习效果校准，不得解读为正确率。
- Math Ink 公开 endpoint 不变，当前新识别路径使用 Xfyun adapter 和确定性 vector-to-PNG 渲染，写入 `xfyun_candidate`。历史 `myscript_candidate` 值、版本链和读路径保留，没有篡改历史事实。

## 验证结果

- Companion 工作包：96 个唯一用例通过（学习闭环 9、AI 治理 20、外部链路 34、公共契约/Agent/Hunyuan 26、Task Content 7）；`py_compile`、Ruff、diff-check 和 golden JSON 校验通过。
- Selection/Scratch/Xfyun 冻结 selectors：36 passed；Cause 相关矩阵：19 passed。
- 旧 targeted selector/题库发布兼容文件：20 passed；其中锁定知识发布退役后，新 Intent 与旧 selector 一样禁止已计划任务启动，不会从退役 release 抽题。
- v103 迁移聚焦矩阵：11 passed，覆盖单一 head、线性、空库 `103 → 102 → head`、nullable 历史兼容、SelectionIntent 严格 CHECK、Xfyun source/metadata 约束和新事实降级阻断。
- 商业发布协议：12 passed；OpenAPI `api:check` 通过，生成类型与后端契约一致。
- 聚焦 Python 源码和测试的 Ruff、`compileall`、`git diff --check` 通过；禁改的小程序 page host/WXML/WXSS/交互路径零 diff。
- 确定性评测资产：`learning_loop_companion_v1.json`、`learning_loop_selection_v1.json`、`learning_loop_scratch_v1.json`。它们锁定上下文/防泄题/证据引用、命中层级、回放和置信分级；本机未调真实模型或讯飞，因此 provider latency 和单次成本明确为未实测，没有伪造数值。
- 本窗口按协调要求没有跑 backend 全量或微信全量 check；三路提交合并后由主协调窗口只跑一次最终全量。

## 前端精确集成清单

`practice/index.ts` 已有正确的 question/material object refs，但点击询问前还必须由前端宿主完成如下接线，本包因文件所有权禁止不修改页面：

1. 询问前先刷新本地 checkpoint；若已有 save in flight，必须 await 同一 promise，不能再建事实。
2. 复用现有 pending checkpoint 的 idempotency key、revision conflict 和断网重试机制；只有服务端回执确认当前 revision 已保存后才调 `/api/v2/companion/ask`。
3. 保存失败、冲突未解或账号/编辑权变更时停止询问并保留本地草稿，不允许退回上一版 checkpoint。
4. Companion context 继续传 `question(id=questionId, version=contentVersionId)`；有共享材料时另传 `material(id=materialId, version=question.contentVersionId)`。不传正确答案或解析。
5. 与前端并行提交 `9b2307039069a5644bb3d50c938a59580195ffd5` 和 `3e52c39d6` 合并后，要重跑 practice 的 checkpoint-before-ask、手机/竖屏平板/横屏平板交互与微信完整 check。

## 已知限制与停止条件

- 本机无 `XFYUN_APP_ID`/API key/secret，也无 provider price/release/deployment 回执；只证明本地 adapter、错误映射、超时失败事实、审计元数据和 router factory，不宣称公网可用。
- 没有真实手写公式识别率、延迟和成本证据，所以依停止条件保留 MyScript 代码、配置名、历史类型、测试和历史文档；没有删除或弱化历史事实。
- 普通画布的“提交后异步公式/视觉识别 worker”尚未接线；已闭合的是显式公式 endpoint → Xfyun candidate → 用户确认 successor → 后续 Cause run 冻结并消费精确版本。
- 真实模型“反人工智障”评测未在无凭据环境执行；当前证据是确定性 context/routing/output-guard 测试和 golden 场景。真实模型回复、延迟与成本必须在有发布回执的集成环境单独记录。

## 回滚

先回退应用代码并停止新 v103 事实写入。nullable Selection/Scratch 列与 source 枚举扩展可留在数据库中，不影响旧应用。若必须 schema downgrade，必须先通过 v103 `_has_new_facts` 门槛证明不存在 SelectionIntent、Scratch projection、`xfyun_candidate` 或 recognition metadata 事实；任一存在都禁止降级，不删除或改写历史 MyScript 版本。
