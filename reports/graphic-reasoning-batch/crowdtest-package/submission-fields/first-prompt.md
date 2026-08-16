# 任务：全量图形推理图片语义结构化

你收到一个名为 `graphic-reasoning-semantic-public-v1` 的附件目录。本任务只有这一轮：必须在本次任务中处理清单内全部 3,601 个图片资产，不要只做抽样、原型或方案说明。

## 目标

逐张查看 `input/images/` 中的真实图形推理图片，为每个资产生成一份符合 `contract/semantic-scene-v2.schema.json` 的 AI 可读语义场景 JSON。下游模型应能仅根据 JSON 理解题目版式、各面板、元素、数量、位置、方向、连接/相交/包含关系、空心/实心、纹理、文字、生活物体部件和折叠立体关系。

原图继续作为前端展示依据。本任务不要求 SVG、Canvas 或像素级重绘，也不要求解题；不要为了追求视觉还原而输出任意像素轮廓。

## 必须执行的工作

1. 先阅读 `README.md`、两个 schema、合成示例和已知失败模式，核对 `input/inventory-public.jsonl` 与图片文件完整性。
2. 按 inventory 顺序实际打开并查看每一张原图。`layoutCandidate` 和视觉语法字段只是导航候选，必须依据原图纠正。不得只根据文件名、哈希、候选分类或复制模板推测内容。
3. 为每个资产写入 `output/assets/<assetId>.json`。字段必须具体到面板级，且元素实例数与 `count` 一致。
4. 持续更新 `output/manifest.jsonl`、`errors.jsonl`、`run-state.json` 和 `output/README.md`。可定期运行验证器和保存断点，但不要在完成少量样本后停止。
5. 全量完成后运行：

   ```bash
   python3 tools/validate_submission.py --root . --output output
   ```

   修复所有结构、覆盖、哈希和答案隔离错误；保留最终 `output/validation-report.json`。

## 视觉事实的硬性标准

- 直线就是 `line`，平滑曲线就是 `curve`，圆弧就是 `arc`，圆/椭圆分别使用 `circle`/`ellipse`；不要把曲线离散成大量短直线，也不要把直角写成模糊的斜角。
- 空心线稿保持 `hollow` 或 `none`；仅清晰实心区域使用 `solid`。点阵、网格、斜线、交叉线等纹理必须写入 `texture`，纹理扇区使用 `textureSector`，不能展开成数百像素线。
- 记录元素的相对九宫格位置、方向、数量和连接、接触、相交、包含、重叠、平行、垂直、相邻、相对等关系。不要把面板边框算作题内元素。
- 汉字、字母、数字使用 `textGlyph` 并保留可辨认的 `textContent`。如果文字无法可靠辨认，写明不确定性，不能当作任意轮廓。
- 人物、动物、器物等生活化图形使用 `lifeObject` 与 `objectPart`，保留能影响题意的部件及持有、连接、包含、朝向关系。不要只追外轮廓。
- 折叠/立体图使用 `wireframe`、`face` 等语义，记录面、边、相邻/相对、前后遮挡及可见折叠候选关系。
- `visualFacts` 只写原图中能观察到的事实。`reasoningCandidates` 只能写跨面板规律候选，并给出置信度与不确定性；绝对不能选择答案。

## 严禁事项

- 不得写入正确答案、最终选项、题库解析或等价暗示；不得尝试从附件外寻找答案。
- 不得输出 SVG/path、绝对坐标、bbox、points、像素数组、base64 或原图副本来冒充语义结构化。
- 不得把 `sourceInspected` 批量设为 true 而未实际查看图片；不得把同一空模板复制到不同资产。
- 不得把 `repair_pending` 改名或清空状态来掩盖低质量。确实无法判清的局部必须列出具体 `repairReasons` 和 `uncertainties`，但仍应完成其余可见事实。
- 不得修改输入图片、inventory、contract、examples 或验证器。
- 不得调用外部模型、外部 API 或网络服务代替本任务模型的视觉检查。

## 完成条件

只有同时满足以下条件才能结束：

- 3,601 个资产全部有独立 JSON 和 manifest 记录；
- 验证器报告覆盖率 100%，结构/哈希/答案隔离错误为 0；
- 每份 JSON 均来自实际图片检查，且对可见事实保持诚实；
- `errors.jsonl` 为空，或仅保留无法自动消除且已在对应 JSON 明确标注的真实视觉不确定性；
- 最终回复简要报告处理数量、structured_candidate/repair_pending 数量、各视觉语法数量、验证结果和剩余风险，不声称已接入产品或已完成前端重绘。
