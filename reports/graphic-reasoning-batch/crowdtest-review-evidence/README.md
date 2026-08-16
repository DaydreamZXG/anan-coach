# 众测产物验收截图包

本目录包含实际下载产物的本地复核工具。请优先使用 `review.html` 亲自抽查并截图。

## 推荐：真实交互验收

```bash
python3 build_review.py
open review.html
```

页面直接嵌入公开任务原图和四个原始 ZIP 中的 JSON 摘要，不预填通过/失败结论。开发者逐题查看原图、切换模型、勾选实际观察结果后，点击“复制本题记录”或“下载验收记录”。浏览器画面应同时包含原图、模型名、资产 ID、JSON 原文和开发者的勾选结果，可作为真实验收截图。

## 自动契约验证

```bash
python3 validate_archives.py
```

该脚本重新解压四个 ZIP 并运行公开任务自带验证器，输出可截图的终端结果。它验证数量、结构和必需文件，不验证视觉语义。

## 旧版摘要图（不要作为测试过程截图提交）

以下 PNG 是分析索引，不是开发者亲手测试过程：

- `00-four-model-validation.png`：四个压缩包的文件数量、状态和独立验证结果。
- `01-summit-missing-errors-file.png`：Summit 的交付文件缺失证据。
- `02-rudder-dot-count-error.png`：Rudder 的可见黑点数量错误。
- `03-pelican-dot-type-error.png`：Pelican 将实心黑点写成 `circle` 的类型错误。
- `04-quarry-option-grouping-error.png`：Quarry 将四组选项错误拆成八个序列面板。

这些摘要图由 `build_evidence.py` 生成，只用于快速定位案例。脚本不会修改 ZIP，不包含模型正确答案，也不代替开发者最终评分。

运行：

```bash
python3 build_evidence.py
```
