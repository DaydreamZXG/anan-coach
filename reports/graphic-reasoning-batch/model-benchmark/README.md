# 专用矢量模型候选基准

这里不是新的渲染旁路。它只为现有 `graphic-primitives-v2` 流程提供非权威的几何候选；最终 JSON 仍必须经过确定性像素拟合、拓扑检查和只读前端契约验证。

## 为什么先做这一层

当前纯 contour/Hough 试验已经证明：它能分框，却会把曲线局部切线误认为直线，也会漏掉真实直线或改变曲率。`gr_7b00c758d0ab44e2c464e04f` 因此继续保持 `repair_pending`，不能靠降低像素阈值变成完成。

专用 image-to-SVG 模型只负责提出每个小格内部的候选。面板数、题干/缺失位/选项角色、外层排版以及最终坐标都不交给模型。

## 两阶段样本

- 第一阶段：直线网络、圆、闭合多边形、网格、直线+曲线；默认只准备这些低风险样本。
- 第二阶段：标准文字、纹理扇区和不规则曲线。文字必须成为 `textGlyph`；纹理必须成为扇区角度及 dots/grid/diagonal 语法，不能保留成大量任意路径。
- 用户已放弃的人物画、连续灰阶生活图不在此基准中。

## 低负载准备

以下命令只使用本机 OpenCV 做 SHA 校验、确定性分框、去除裁片边缘的外层题目框并保存单格输入，不调用 Qwen 或任何 GPU 模型：

```bash
python3 reports/graphic-reasoning-batch/model-benchmark/prepare_benchmark.py --phase 1
```

默认模型比较顺序是 OmniSVG 小模型优先、StarVector 小模型作为对照；在工作站空闲前不安装、不重启、不发批量请求。模型应输出无 `<image>`、无脚本、无 CSS、无 transform 的独立 SVG。

## 候选解析

```bash
python3 reports/graphic-reasoning-batch/model-benchmark/candidate_ingest.py \
  --svg candidate.svg \
  --asset-id gr_xxx \
  --panel-id panel-1 \
  --source-sha256 <64位来源哈希> \
  --provider omnisvg \
  --model <实际模型名> \
  --semantics panel-semantics.json \
  --output candidate.json
```

解析器执行以下硬规则：

- 拒绝 `<image>`、脚本、外部对象、`use`、CSS/filter/mask 等可能隐藏位图或不可验证几何的元素。
- 把 SVG 的线、矩形、圆/椭圆、折线/多边形、二次/三次贝塞尔、椭圆弧和文字转换为明确类型；文字不能用路径冒充。
- 模型 fill 永远不是实心判定真相，必须回到源像素做高置信闭合区域验证。
- 模型坐标永远不直接进入最终场景；候选的 `finalAcceptance` 固定为 `blocked`，只有后续确定性拟合生成的新场景才能接受。
- 任何 transform 暂时拒绝，避免未展开变换造成大偏差；模型提示应要求直接坐标。

候选解析后还要逐格运行确定性像素门禁：

```bash
python3 reports/graphic-reasoning-batch/model-benchmark/verify_candidate.py \
  --candidate candidate.json \
  --source-panel inputs/gr_xxx/panel-01.png \
  --output verification.json
```

该门禁会测量每个明确图元到源墨迹的距离、整体覆盖率，检查重复直线/圆，并用内部实心像素比例阻止空心轮廓被填黑。即使全部通过，它也只得到 `verified_refinement_seed`，仍不能成为最终坐标。

随后以候选为初值，对源像素做有界确定性拟合：

```bash
python3 reports/graphic-reasoning-batch/model-benchmark/refine_geometry.py \
  --candidate candidate.json \
  --source-panel inputs/gr_xxx/panel-01.png \
  --output refinement.json
```

当前实现保持图元类型不变：直线使用鲁棒像素拟合并在证据支持时精确吸附到水平/垂直，矩形保持四个直角，圆使用覆盖角检查后的代数圆拟合，二次/三次贝塞尔使用最多 6 像素漂移的固定步长优化。未实现的椭圆、圆弧、多边形顶点和文字位置会继续 `repair_pending`，不会沿用模型坐标冒充确定性结果。

拟合后使用 `verify_topology.py` 对源图与重绘的连通组件、端点、交点和闭环数做精确比较，并检查候选中的近直角是否已经成为精确 90°。文字内容与方向没有 OCR/语义证据时仍保持待定。

## 验收顺序

1. 来源 SHA 与确定性面板/选项数一致。
2. 候选 SVG 安全且所有元素可以类型化。
3. 对源像素重新拟合：直线做共线支持与 NMS，圆/椭圆做几何支持与 NMS，曲线只优化已识别贝塞尔的控制点，fill 只在闭合实心证据充分时保留。
4. 检查端点、交点、闭环、文字内容、方向和相对位置。
5. 生成 `graphic-primitives-v2`、重绘、原图对比和验收证据，并通过正式契约。

在第 5 步完成前，错误文件为空也不代表完成，状态仍是 `repair_pending`。
