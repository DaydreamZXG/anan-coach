# 资料分析图表/表格批量结构化候选

## 结论与边界

本目录是离线题库资产的候选结构化批处理结果，不是产品接入，也不代表可上线。处理过程只读取题库 JSONL 与本地图像；没有修改小程序、后端、数据库、对象存储、部署配置或既有报告，也没有写入线上服务。唯一写入范围是本目录。

实际盘点 `1486` 个按 SHA-256 内容去重的资料分析图像，来自 `14280` 个题目出现位置和 `15214` 次图片引用。当前状态：成功 `1486`、需人工复核 `0`、不支持 `0`、失败 `0`、待处理 `0`。

## 实际执行方法

- `qwen3-vl-30b-a3b` 只提取标题、单位、坐标轴、分类、图例、系列、数值、明确缺失值和有限样式；没有请求或保存模型生成的绝对像素坐标。
- 全图抽取后，柱图、折线图、组合图按横向重叠局部裁图复核；表格按纵向重叠裁图复核；负号、小数、百分号、相邻数值与缺失值均纳入复核提示。缺失值使用 `null`，确定性检查拒绝缺失值转 `0`。
- 饼图的 Qwen 输出只用于文字标签与顺时针顺序；圆周选择、分界线与角度由 OpenCV Hough 线/圆、圆周边缘覆盖评分和径向灰度剖面完成。角度合计必须为 360°，share 合计必须为 1。
- 原先无法归入标准图表的视觉资产不再保留为“不支持”：`convert_visual_bricks.py` 直接量化源像素并追踪轮廓，写入小程序现有的 `graphic-primitives-v2` 确定性矢量契约；每张都保存来源哈希、重绘哈希、前景 IoU、MAE、复杂度与逐图对比证据，没有位图背景兜底。
- 表格在模型候选后执行行标签列分离、行列长度一致和数值类型检查，未把 OCR 文本串直接作为最终拓扑。
- 每项执行 JSON Schema、系列长度、表格拓扑、数值有限性、坐标轴范围、饼图守恒和缺失值保留检查。所有模型结果仍标记为 candidate。
- 外部通用 OCR 与 Apple Vision 均未作为处理器。SSH 密码仅在 PTY 中输入，没有写入仓库、缓存或本报告。

## 产物

- `inventory.json`：总量、分类、状态、来源与确定性校验汇总。
- `manifest.jsonl`：每个稳定资产 ID 的来源、分类、状态、结构化路径与复核信息。
- `structured/*.json`：逐图候选；schema 为 `structured-schema.json`。
- `errors.jsonl`：失败、不确定、不支持和未处理项及原因；不会静默兜底。
- `review-crops/` 与 `cache/qwen/`：断点续跑所需的局部复核证据与模型缓存。
- `previews/`：实际成功类型的标准 SVG 重绘及原图对比。已生成类型：combo, grouped_bar, line, pie_donut, scatter, stacked_bar, structured_visual, table。
- `structured-visual-conversion.json`：原“不支持”资产的逐图矢量转换参数、质量指标和证据路径。

## 精确命令

```bash
cd /Users/zxg/Documents/新项目
python3 reports/data-analysis-batch/pipeline.py scan
ssh -N -L 18000:127.0.0.1:8000 -p 54180 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 root@47.121.172.38
python3 reports/data-analysis-batch/pipeline.py batch --limit 20 --workers 1
python3 reports/data-analysis-batch/pipeline.py full --workers 3
python3 reports/data-analysis-batch/convert_visual_bricks.py
python3 reports/data-analysis-batch/finalize.py
```

SSH 隧道命令会交互式要求密码；不要把密码加入命令行、脚本或报告。请求目标始终是本地隧道 `http://127.0.0.1:18000/v1/chat/completions`，没有使用代理访问国内服务。

## 耗时与速率

- 只读扫描：`17.494` 秒。
- 首批 20 张最终复跑：`45.529` 秒；状态 `{"success": 13, "unsupported": 7}`。
- 全量本轮：`6385.189` 秒。
- 请求启动间隔至少 1.25 秒，低于全局 50 RPM；首批单并发验证，全量稳定后最多 3 个小并发。

## 继续运行与断点续跑

保持 SSH 隧道后，重复运行：

```bash
python3 reports/data-analysis-batch/pipeline.py full --workers 3
python3 reports/data-analysis-batch/convert_visual_bricks.py
python3 reports/data-analysis-batch/finalize.py
```

`work/results/`、`structured/` 与 `cache/qwen/` 已完成项会直接复用。失败项保留原因；要重试某个失败项，只删除本目录内对应的 `work/results/<asset_id>.json`，保留 Qwen 整图缓存即可。不要删除或修改题库源文件。

## 未做事项

没有发布、部署或写数据库/对象存储。本批次已把现行 1486 个资产全部收敛为成功，且 `needs_manual_review`、`failed`、`unsupported` 与 `pending` 均为零；这些结构化结果仍需由业务接入层消费后再随产品版本发布。
