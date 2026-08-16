# 非图推 296 个非成功项消歧报告（2026-08-11）

## 结果

- 初始：`success 1153 / needs_review 279 / failed 17`。
- 最终：`success 1400 / needs_review 49 / failed 0`。
- 原 296 个非成功项中，247 个已通过二次裁决、重抽取、路由修正或确定性校验转为 `success`；49 个仍保留 `needs_review`。
- 所有 1,449 个资产现在都有结构文件；没有 `pending`、`unsupported` 或无结构 `failed`。

## 原 296 项是什么

下表为互斥主因分类；每个资产只计入一项。

| 原通道 | 主因 | 数量 |
| --- | --- | ---: |
| annotated_raster | 仅语义/身份/缺失信息说明 | 25 |
| annotated_raster | OCR 或可见字形歧义 | 1 |
| data_visual | 仅语义不确定性 | 71 |
| data_visual | 图表类型、表格行列或系列长度验证错误 | 35 |
| perceptual_grid | 字符/token 歧义 | 53 |
| perceptual_grid | CV 行列聚类或非矩形分组误判 | 48 |
| perceptual_grid | 结构验证错误 | 3 |
| perceptual_grid | 低置信或其他 | 2 |
| perceptual_grid | 模型 JSON 失败 | 8 |
| reasoning_diagram | 仅语义不确定性 | 32 |
| reasoning_diagram | 连接引用等结构错误 | 5 |
| reasoning_diagram | CV/拓扑冲突 | 4 |
| reasoning_diagram | 模型 JSON 失败 | 6 |
| 路由阶段 | 模型 JSON 失败、未建立正式通道 | 3 |
| **合计** |  | **296** |

## 已解决的主要问题

- 将“图片本来没有标题、比例尺、完整刻度或身份专名”从抽取错误改记为 `observed_limitations`；只保留可见事实和题目上下文声明。
- 将“错误答案选项不符合数学、物理或题干规律”从 `topology_conflicts` 改记为选项/上下文差异，不再误判为抽取拓扑冲突。
- 将 25 张定性函数图、运动关系图和柱形答案选项从 `data_visual` 改路由到 `reasoning_diagram`，抽取曲线分段、方向和拓扑，而非不存在的数据值。
- 修复词云路由：从统计图改为保留原图的 `annotated_raster`。
- 修复九张表格的行头重复、纵向键值表、多层表头和系列分类问题；另对五张复杂表格/组合图逐图核对并保留源图中的空值、遮挡与异常印刷值。
- 对 71 张残余知觉字符图使用最近邻像素放大复核；27 张达到逐 token 完全一致、无多候选、置信度不低于 0.98 和确定性占墨检查后转为成功。
- 对无法可靠映射 Unicode 的最后一张失败图片，生成两组各 10 个源 glyph 的裁剪引用、位置和逐裁剪 SHA-256；状态改为有结构的 `needs_review`，不再是 `failed`。

## 最终 49 项为什么仍需复核

| 通道 | 互斥原因 | 数量 |
| --- | --- | ---: |
| perceptual_grid | 放大后仍确认或无法排除多个字符/token 候选 | 35 |
| perceptual_grid | 放大复核 JSON 反复不完整，但已有结构候选 | 4 |
| perceptual_grid | 新旧转录或确定性网格证据未达成一致 | 4 |
| perceptual_grid | 源 glyph 可精确引用，但无法可靠映射唯一 Unicode | 1 |
| annotated_raster | OCR/可见文字仍有真实歧义 | 3 |
| annotated_raster | 原图过于模糊且模型置信度低 | 1 |
| reasoning_diagram | 低置信且存在真实区域/拓扑指向歧义 | 1 |
| **合计** |  | **49** |

这些项目全部保留原图、原始 SHA-256 和结构候选。未对 `0/O`、`1/l/I`、实心/空心符号、形近汉字或未知 glyph 做静默归一化。

## 独立终检

- 唯一资产：1,449。
- 结构文件、独立源哈希、schema、模型无坐标：均为 1,449/1,449。
- 通道契约：annotated-raster 155；data-visual 191；perceptual-grid 233；reasoning-diagram/graphic-primitives-v2 870。
- 资料分析批次精确 SHA-256 重叠：0。
- 图形推理独立批次处理：否。

