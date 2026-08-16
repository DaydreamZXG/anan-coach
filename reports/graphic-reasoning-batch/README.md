# 图形推理题全量结构化与视觉积木重绘

本目录是独立批处理任务的唯一写入边界。它不接入小程序业务页，也不修改后端、数据库、部署配置、`reports/data-analysis-batch/**` 或 `.product-governance/**`。

## 数据与边界

- 输入题库：`data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl`。
- 输入图片根目录：同目录下的 `fenbi-final-analysis-v1-20260613-assets/`。
- 扫描范围：所有 `graphic_reasoning` 题目的材料、题干、选项、解析和解法真实图片；公式图片不纳入矢量批处理。
- 去重键：图片内容 SHA-256；`inventory.jsonl` 保存全部引用关系，不截断多题/多字段引用。
- 正式渲染契约只读自 `apps/wechat/miniprogram/design-system/structured-visuals/**`，输出以 `graphic-primitives-v2` 为目标。

## 运行

```bash
python3 reports/graphic-reasoning-batch/pipeline.py scan --cpu-only
python3 reports/graphic-reasoning-batch/cpu_audit.py
python3 reports/graphic-reasoning-batch/pipeline.py classify --limit 12
python3 reports/graphic-reasoning-batch/pipeline.py route
python3 reports/graphic-reasoning-batch/pipeline.py regular-route
python3 reports/graphic-reasoning-batch/pipeline.py regular-qwen --qwen-limit 12
python3 reports/graphic-reasoning-batch/pipeline.py process --regular-representative
```

当前阶段必须使用 `scan --cpu-only`。它不会探测 `127.0.0.1:18000`，也不会导入、安装或启动任何视觉模型；只有主窗口明确通知工作站空闲后，才允许另行执行受双重 GPU 门禁保护的模型脚本。

当前按子类逐个运行，禁止直接执行无差别全量 `process`。单个子类的首轮命令为：

```bash
python3 reports/graphic-reasoning-batch/pipeline.py process \
  --subtype closed_polygon_composition
python3 reports/graphic-reasoning-batch/pipeline.py validate \
  --manifest reports/graphic-reasoning-batch/subtype-batches/closed_polygon_composition/manifest.jsonl
python3 reports/graphic-reasoning-batch/pipeline.py audit-subtype \
  --subtype closed_polygon_composition
```

代表样本应使用独立 manifest，不覆盖全量 manifest：

```bash
python3 reports/graphic-reasoning-batch/pipeline.py process --representative \
  --asset gr_11402c834514d26870f740fb \
  --asset gr_52036129fb44f1fade1f19ea
python3 reports/graphic-reasoning-batch/pipeline.py validate \
  --manifest reports/graphic-reasoning-batch/representative-manifest.jsonl
```

对于纯 CV 无法可靠区分直线与曲线切线的样本，`model-benchmark/` 提供“专用 image-to-SVG 模型候选 → 类型化解析 → 确定性像素门禁”的独立基准。模型结果不直接进入最终场景；当前工作站共享负载期间只准备小格输入，不安装、重启或批量调用新模型。详见 `model-benchmark/README.md`。

默认单进程、无重试循环，避免占满工作站。`process --asset gr_<sha 前 24 位>` 可定向重跑。每个资产输出到 `assets/<assetId>/`：

- `structured.json`：确定性矢量场景，`fidelityTier=deterministic_vector`。
- `redrawn.svg` / `redrawn.png`：不以原图作为背景的重绘。
- `comparison.png`：原图与重绘并排对比；原图只出现在证据对比图中。
- `acceptance.json`：来源 SHA-256、面板/选项、拓扑、文字/方向证据、相对位置和输出哈希。

## CPU 盘点快照（2026-08-11）

- 图推题 6,302 道：6,270 道引用图像，32 道是纯文字字形题。
- 真实引用关系 11,470 条；物理文件路径 8,935 个；按内容 SHA-256 去重后 4,857 个资产。
- 题干/选项/解析/解法引用分别为 6,303 / 199 / 2,484 / 2,484；材料引用为 0；缺失文件为 0。
- 4,857 个资产全部进入 inventory 和 classification；1,126 个已有完整候选产物，3,731 个尚无产物。
- 当前 manifest 中 12 项全部是 `repair_pending`，最终接纳数为 0。生成过文件不等于处理成功。
- 完整证据见 `cpu-audit/rescan-summary.json`、`cpu-audit/processing-coverage.json` 与 `cpu-audit/type-coverage.json`。

上面的“最终接纳数 0”指旧的全矢量批处理门禁。后续按用户确认调整为“原图展示 + AI 可读语义 JSON”为主交付后，5 道固定语义基准中已有 2 道通过逐项可见事实和 JSON 盲解答案保持；另外 3 道仍为 `repair_pending`。两者不可混写成全量完成，详见 `semantic-structure/model-benchmarks/gemma-4-31b-it/representative-state.json`。

## 双层结构化契约

`contracts/dual-layer-contract-v1.json` 同时约束两层信息：

1. 可推理语义层：面板序列、元素类别与数量、相对位置、连接/相交/包含、端点/交点/闭环、方向、旋转/翻转/移动/叠加/增减等跨面板关系。
2. 可渲染图元层：只使用正式 `graphic-primitives-v2` 支持的直线、折线、多边形、矩形、圆/椭圆/圆弧、贝塞尔、文字字形、线框、面和纹理扇区等类型化图元。

AI 只提供语义候选，不能以 SVG path 代替语义层，也不能把预测坐标当最终几何；CV 和确定性规则负责分框、坐标、精确角、拓扑与前端契约验证。解题输入不暴露答案，答案只参与独立的答案保持验收。

## 模型边界

默认 `scan` 能检查已授权的语义模型健康状态；当前共享工作站阶段必须使用 `scan --cpu-only`，不访问任何模型端点。`qwen_client.py` 仅能提供视觉语义候选，禁止输出绝对坐标、面板框架和最终路径；模型不可用时，分类使用题库知识点先验与 CV 特征，并明确标记 `semanticSource`，不会伪造模型结果。

`model-benchmark/runtime/run-queue.jsonl` 是 46 格可断点队列：45 格等待工作站明确解锁，1 格是问号缺失标记并跳过。首批固定为直线+曲线、纯直线、圆、闭合多边形、网格各一格。安装和调用脚本同时要求 `GRAPHIC_REASONING_GPU_READY=1` 与运行目录中的 `GPU_READY` 文件，当前不得创建该文件或执行这些脚本。

## 失败与待修复

- `errors.jsonl` 只记录真正的扫描、结构生成或契约错误；理想值为 0。
- `repair-queue.jsonl` 记录检测复杂、面板不足或密度异常的样本。它们仍生成 `deterministic_vector` 输出，不把 `needs_review` 或 `unsupported` 当最终结构化类型；待修复项必须依据对比和验收证据继续收口。
- `partial-output-repair-queue.jsonl` 记录被暂停的 generic contour 临时输出；`semantic-repair-queue.jsonl` 记录 Qwen 候选字段校验失败的样本。两者都保持 `repair_pending`，不会进入最终接纳。
- `summary.json` 区分扫描、分类、处理、错误和待修复数量；不能据此声称产品已接入或已部署。
- `grammar-summary.json` 是语法分类与 renderer 路由统计；`batch-state.json` 在语法专用 renderer 尚未关闭验收前保持 `paused_for_grammar_renderer_review`。
- `regular-subtype-summary.json` 保存规则几何互斥子类、代表样本与排除统计；`subtype-batches/<subtype>/` 保存一类一次的 manifest、修复队列和首轮审计。
- `exclusions.jsonl` 只保存有逐资产证据的排除项；不得再整类排除人物剪影或生活化图形。144 个此前错误排除的生活化剪影已恢复到待结构化范围，必须保留对象、部件、持有物、姿态和拓扑；29 个连续灰阶候选逐资产复核是否属于自然照片，只有确认是自然图像才移出本批。

特别规则：纹理扇区/饼图必须输出扇区角度以及 `arc`/`face`/`dot`/对角纹理原语，禁止以数百条 contour 线段替代；规则几何、曲线圆弧、汉字/生活化图形和立体折叠分别走独立路由。Qwen 只提供语义候选，CV/确定性几何负责坐标，所有代表样本在 `representative-review.json` 中保持 `repair_pending`，直到专用验收闭合。

## 验收层级

最终接纳必须同时通过来源哈希、面板数、选项数、连通组件、端点/交点/闭环、空心/实心、箭头、文字、方向、精确角、像素贴合、答案保持和前端契约。任一门禁缺失、待定或失败都保持 `repair_pending`。

当前脚本提供静态结构/契约子集校验和代表样本视觉对比，属于验证阶梯 1–2。它不替代小程序 TypeScript 编译、真实题目页 E2E、部署或生产验证，也不声称产品已接入。
