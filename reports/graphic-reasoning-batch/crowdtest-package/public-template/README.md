# 图形推理图片语义结构化任务

本附件包含 3,601 个按内容哈希去重的真实图形推理题干/选项图片资产。任务目标是把每张图片转换为下游推理模型可读取的语义场景 JSON；原图继续作为前端展示与人工核对依据，不要求也不接受像素轮廓追踪式重绘。

原始扫描曾得到 4,857 个候选，其中 1,255 个只出现在题库解析/解答中，可能含红框、红线等答案标注，已从公开任务移除；另有 1 个用户明确放弃的传统人物画资产。公开清单不含这些资产及其答案信息。

## 输入

- `input/images/`：按 `assetId` 命名的原图。
- `input/inventory-public.jsonl`：无答案资产清单、来源哈希、题目引用、视觉语法提示和确定性分框候选。
- `contract/semantic-scene-v2.schema.json`：逐题输出契约。
- `contract/output-manifest.schema.json`、`run-state.schema.json`、`error-record.schema.json`：汇总、断点和错误清单契约。
- `examples/`：合格结构片段与已知反例说明；示例不含正确答案。

`layoutCandidate` 是已有 CV 候选，不是不可修改的真相。必须看原图核对；若修改面板数、角色或顺序，需在输出的 `layoutIssues` 中说明。

## 输出

必须写到 `output/`：

```text
output/
  assets/<assetId>.json
  manifest.jsonl
  errors.jsonl
  run-state.json
  README.md
```

每个未排除资产都必须有一条 manifest 记录和一个逐题 JSON。无法可靠识别的资产仍需生成保守记录，将 `extraction.qualityStatus` 设为 `repair_pending`，列出明确原因；不得把空模板、复制原图、base64、像素轮廓或任意 path 当作结构化完成。

执行后运行：

```bash
python3 tools/validate_submission.py --root . --output output
```

验证器只检查结构、覆盖、哈希引用和答案隔离，不会替代可见事实的人工质检。

## 关键原则

1. 严格区分直线、折线、平滑曲线、圆弧、圆和椭圆；直角、方向和相交关系不能用模糊描述代替。
2. 空心线稿保持 `hollow` 或 `none`，只有清晰实心区域写 `solid`；点阵、网格、斜线、交叉线纹理必须保留。
3. 汉字、字母、数字写为 `textGlyph` 并保存 `textContent`；不能退化成任意轮廓。
4. 生活物体与人物剪影要保留可区分部件及其连接、持有、包含等关系。
5. 折叠与立体题写清面、边、相邻/相对、前后遮挡、折叠候选关系。
6. `visualFacts` 只写可见事实；`reasoningCandidates` 可写候选规律，但不得选择答案。
7. 禁止任何正确答案、最终选项、题库解析或等价暗示进入输出。
