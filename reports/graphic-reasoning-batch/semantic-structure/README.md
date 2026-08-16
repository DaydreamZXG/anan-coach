# AI 可读图推语义结构

本目录把“解题语义结构化”作为主交付，把原图作为前端展示真相。矢量重绘仅是可选增强；失败不会阻塞语义 JSON，也不能替换或污染原图。

每个资产写入三个相互隔离的文件：

- `assets/<assetId>/solver.json`：给解题 AI，包含面板序列、图元类别/数量、直线与曲线、空心/实心、相对位置、连接拓扑、跨面板变化候选；不含题库答案。
- `assets/<assetId>/evaluation.json`：仅供离线验收，保存题目引用和答案，不能作为 solver 输入。
- `assets/<assetId>/acceptance.json`：来源哈希、CV/Qwen 面板与选项计数一致性、字段完整性和答案隔离门禁。

`solver.json.presentation.mode=original_image` 表示前端直接展示原题图片；`vectorEnhancement.blocking=false` 明确重绘不是主流程门禁。

```bash
python3 reports/graphic-reasoning-batch/semantic-structure/prepare_queue.py
python3 reports/graphic-reasoning-batch/semantic-structure/run_batch.py --limit 5
```

模型端点可通过环境变量切换，默认值仍兼容原 Qwen 工作站：

```bash
GRAPHIC_REASONING_VLM_ENDPOINT=http://127.0.0.1:18000/v1/chat/completions \
GRAPHIC_REASONING_VLM_MODEL=gemma-4-31b-it \
python3 reports/graphic-reasoning-batch/semantic-structure/run_batch.py \
  --limit 5 \
  --output-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it

python3 reports/graphic-reasoning-batch/semantic-structure/evaluate_solver.py \
  --limit 5 \
  --input-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it
```

脚本默认单请求串行、最多一次纠错重试，并按 checkpoint 断点复用。Qwen 只能输出语义和相对位置，任何坐标、边界框、SVG、路径、控制点或答案字段都会被拒绝。

不同模型必须写入独立 `model-benchmarks/<model>/`，避免覆盖前一模型证据。通过 JSON schema 不是质量完成：新候选一律保持 `repair_pending` 和 `qualityStatus=unreviewed`，直到逐图核对可见事实；提取阶段的 `qualityAccepted` 固定为 0。

31B 模型的整题长 JSON 基准过慢且会漏掉选项内部线条，因此正式代表样本改用逐面板聚焦提取，再单独汇总题干关系：

```bash
GRAPHIC_REASONING_VLM_ENDPOINT=http://127.0.0.1:18000/v1/chat/completions \
GRAPHIC_REASONING_VLM_MODEL=gemma-4-31b-it \
python3 reports/graphic-reasoning-batch/semantic-structure/run_panel_semantics.py \
  --limit 5 \
  --output-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it
```

`panel-candidates/` 保存每个放大面板的紧凑 JSON 和独立 checkpoint；`raw-relations.json` 只记录可观察的跨面板关系。旧的整题一次性输出保存在 `gemma-4-31b-it-whole-question-v1/`，不作为成功结果。

## 当前采用的窄语法处理器

通用整题 JSON、通用逐面板 JSON 和纯 contour/Hough 结果都不能作为几何真相。当前改为先识别窄语法，再运行对应处理器：

```bash
GRAPHIC_REASONING_VLM_ENDPOINT=http://127.0.0.1:18000/v1/chat/completions \
GRAPHIC_REASONING_VLM_MODEL=gemma-4-31b-it \
python3 reports/graphic-reasoning-batch/semantic-structure/build_simple_regular.py \
  --limit 5 \
  --output-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it

python3 reports/graphic-reasoning-batch/semantic-structure/evaluate_solver.py \
  --input-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it \
  --asset gr_09a239f1f5c89bec86ff315d \
  --asset gr_814f04ca63ec75e0fd69a9fb

python3 reports/graphic-reasoning-batch/semantic-structure/review_simple_regular.py \
  --output-root reports/graphic-reasoning-batch/semantic-structure/model-benchmarks/gemma-4-31b-it
```

`build_simple_regular.py` 当前只支持两种互斥窄语法：

- 四个独立空心直角三角形：从现有确定性多边形顶点计算四象限、直角顶点、斜边方向和规范签名；模型不碰坐标。
- 4×4 网格、每面板两个符号：CV 只删除经过全宽/全高投影验证的网格中心线，确定符号所在单元格；Gemma 只标 `star/triangle/pentagon/arrow` 及方向。

网格删除不得直接使用形态学 opening 的整张 mask；否则五边形的水平底边会被误删并被模型错认成三角形。当前实现先定位 5 条横线和 5 条竖线，再重新构造网格 mask，保留符号自身直线。

提取器仍只产生 `repair_pending/fact_review_pending`。只有 `review_simple_regular.py` 同时通过来源哈希、逐项可见事实、答案隔离和语义 JSON 盲解答案保持后，才标记为 `semantic_solver_validated`；该状态仅代表样本和原图展示方案，不代表全量、矢量重绘或产品接入完成。

当前 5 道代表样本中：

- 2 道 `semantic_solver_validated`：四直角三角形、4×4 双符号网格；Gemma 仅依据 JSON 盲解均答对。
- 3 道继续 `repair_pending`：直线+曲线、纯直线网络、圆+线+点。
- 原图盲解只有 1/2 答对，因此原图模型正确率不被用来替代 JSON 事实门禁。

状态证据见 `model-benchmarks/gemma-4-31b-it/representative-state.json`、`fact-review-state.json` 和 `solver-evaluation-state.json`。

## 同类批量前的 CPU 预筛

```bash
python3 reports/graphic-reasoning-batch/semantic-structure/audit_simple_regular.py \
  --subtype closed_polygon_composition
python3 reports/graphic-reasoning-batch/semantic-structure/audit_simple_regular.py \
  --subtype grid_partition
```

预筛不调用模型。315 个 `closed_polygon_composition` 粗分类中只有 1 个满足“四个空心直角三角形”，330 个 `grid_partition` 粗分类中只有 1 个满足“4×4、每面板两个符号”。其余 643 个保持待重新细分，不能强行套用这两个处理器。纯直线网络的定向模型计数仍漏掉密集扇形射线，失败证据保存在 `line-network-debug/audit.json`，尚未生成可接纳的 `solver.json`。
