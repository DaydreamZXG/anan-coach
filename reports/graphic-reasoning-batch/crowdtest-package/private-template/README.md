# 私有验收包（不得上传到众测任务）

本目录包含正确答案、公开排除证据、210 张分层人工抽样和 400 题 DeepSeek JSON-only 盲测清单。它只用于任务结束后的人工评分，绝不能作为众测附件，也不能交给待评匿名模型。

## 验收顺序

1. 在原公开附件根目录放入待评模型的 `output/`，运行公开包的验证器。保存 `output/validation-report.json`。
2. 用 `manual-review-sample-private.csv` 逐项同时查看公开包原图和模型 JSON。各事实列填 `1`（正确）、`0`（错误）或 `NA`（原图不适用），另填 `critical_error` 与备注。
3. 运行 `tools/build_deepseek_cases.py`，将模型 JSON 组合为无答案盲测输入；使用固定的 `DEEPSEEK_BLIND_PROMPT.md` 让 DeepSeek 只读 JSON 作答。
4. 将 DeepSeek 结果保存为 JSONL，再运行 `tools/score_deepseek_predictions.py`。评分工具才会在本地私下与答案表比对。
5. 结合自动报告、人工报告、盲测报告和 Rubrics，在众测页面对四个匿名模型分别手动打 0–10 分。

## 人工抽样字段

- `layout_panel_option_order`：面板数、题干/缺失/选项角色和顺序。
- `line_curve_right_angle`：直线、曲线、圆弧、圆/椭圆和直角/精确方向。
- `text_glyph`：汉字、字母、数字及其内容。
- `fill_texture`：空心/实心、点阵、网格、斜线、交叉纹理。
- `count_direction_position`：数量、方向和相对位置。
- `topology_parts_3d`：连接/相交/闭环、生活物体部件、面边/折叠关系。

如果字段适用但 JSON 遗漏，必须填 `0`，不能填 `NA`。`critical_error=1` 用于会直接改变题意的错误，例如直线写成曲线、空心写成实心、选项错位、汉字误读或折叠相邻面写反。
