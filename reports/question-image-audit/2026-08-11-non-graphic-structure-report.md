# 非图推题目图片全量结构化报告（2026-08-11）

## 精确状态

| 状态 | 数量 |
| --- | ---: |
| success | 1400 |
| needs_review | 49 |
| unsupported | 0 |
| failed | 0 |
| pending | 0 |

## 正式通道分布

| 通道 | 数量 |
| --- | ---: |
| annotated_raster | 155 |
| data_visual | 191 |
| perceptual_grid | 233 |
| reasoning_diagram | 870 |

## 通道与状态交叉分布

| 通道 | success | needs_review | unsupported | failed | pending | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| annotated_raster | 151 | 4 | 0 | 0 | 0 | 155 |
| data_visual | 191 | 0 | 0 | 0 | 0 | 191 |
| perceptual_grid | 189 | 44 | 0 | 0 | 0 | 233 |
| reasoning_diagram | 869 | 1 | 0 | 0 | 0 | 870 |

## 验证边界

- 公式、资料分析独立批次和图形推理批次均未在本批处理。
- 视觉模型只输出语义候选；坐标、区域、网格和矢量图元由本地确定性 CV 生成或校验。
- 原图路径与 SHA-256 始终保留；低置信、歧义字符与拓扑冲突进入 `needs_review`。

## 照片通道小批证据

- 样本数：12；门禁：通过。
- 状态：`{"needs_review": 4, "success": 8}`。
- `non-graphic-review-evidence/qi_0263ac94b1dad9a1cf379dac-source-vs-cv.png`
- `non-graphic-review-evidence/qi_03cd0c03c464b47dc6c24747-source-vs-cv.png`
- `non-graphic-review-evidence/qi_045f7906528672020b7aa40d-source-vs-cv.png`
- `non-graphic-review-evidence/qi_046056ef439ba8064f812ac8-source-vs-cv.png`
- `non-graphic-review-evidence/qi_05e772a989608587c064bfb1-source-vs-cv.png`
- `non-graphic-review-evidence/qi_06d745791427b40e1e08d8ea-source-vs-cv.png`
- `non-graphic-review-evidence/qi_092ae0a02dfe23be5fca3b92-source-vs-cv.png`
- `non-graphic-review-evidence/qi_0e65aaaf94557b80eb4e97e0-source-vs-cv.png`
- `non-graphic-review-evidence/qi_10b7f5ab5aa4ca2c567f3284-source-vs-cv.png`
- `non-graphic-review-evidence/qi_124692ca14873f1dbdb5b72d-source-vs-cv.png`
- `non-graphic-review-evidence/qi_137e6065bec59f505445ef4f-source-vs-cv.png`
- `non-graphic-review-evidence/qi_15d591a4bb0a7e6c674fc5c2-source-vs-cv.png`

## 独立终检

- 终检：通过；唯一资产 1449。
- 结构文件：1449；独立哈希通过 1449；schema 通过 1449；模型无坐标通过 1449。
- 资料分析 SHA-256 精确重叠：0；图形推理处理：False。

## 剩余问题

- 仍有 49 个非成功项，逐项原因见 `non-graphic-structure-errors.jsonl`。
- `needs_review` 49：包括仍无法可靠映射的原始 glyph、放大后仍有多候选的字符、低置信 OCR/原图细节或真实拓扑冲突；有结构候选 49，无结构候选 0。
- `failed` 0：其中 0 条为模型反复返回不完整或畸形 JSON，经过全量后单独重试仍未收敛；没有用近似结果替代。
- `unsupported` 0，`pending` 0。
