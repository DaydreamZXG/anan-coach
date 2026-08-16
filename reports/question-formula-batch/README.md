# 题库公式图片结构化

这条流水线只处理公式，不参与统计图、图推或普通图片重绘。

## 当前盘点

- `55,342` 个资产来自原题 HTML 的 `flag="tex"` 明确标记。
- 资料分析视觉尾单另外补捞出 `3` 个未标 `flag="tex"` 的公式资产。
- 题目关键位置（材料、题干、选项）优先；解析与解法中的公式排在第二批。

## 2026-08-10 全量状态

- 55,345 个唯一资产已经全部生成结构化结果，待处理为 0。
- Texo 负责离线初识别；KaTeX 负责语法与异常重复门槛；Qwen3-VL 只复核低置信候选。
- 最终为 52,219 条自动候选、3,122 条需复核和 4 条人工确认。
- 41 条 KaTeX 无效与 1 条重复疑似均未进入自动候选。
- 具体证据见 `2026-08-10-run-summary.md` 与 `verification-summary.json`。

## 结构化方式

1. 原图按 SHA-256 去重，调用现有讯飞印刷公式识别能力。
2. 讯飞返回的 LaTeX 是结构化真源，原图只作为可追溯证据和识别失败时的显示保底。
3. 小程序消费 `formula` 块；不把公式交给轮廓描摹，也不让通用视觉模型猜坐标。
4. 只做三项必要校验：源哈希一致、LaTeX 非空、括号结构基本闭合。低置信候选进入人工复核，但不会阻塞其他已通过公式。

## 使用

```bash
python3 reports/question-formula-batch/build_manifest.py
backend/.venv/bin/python reports/question-formula-batch/recognize.py --priority question_critical --limit 100
python3 reports/question-formula-batch/verify.py
```

识别脚本从环境变量读取 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`，不会在报告中保存或打印密钥。默认全局限速为 50 RPM，支持断点续跑。

识别命令使用后端 Python 3.12 虚拟环境，因为它直接复用生产代码中的讯飞签名与响应解析器；不复制第二份接口实现。

## Texo 离线候选

`alephpi/Texo` 是 20M 参数的公式图片转 LaTeX 模型，可用 PyTorch 或 ONNX 在本地批处理。它适合承担大批量初筛，讯飞只复核低置信与关键位置，避免 500 次额度成为全库阻塞点。

接入前必须完成一轮 100～500 张盲测，覆盖分式、根式、上下标、矩阵、中文混排和低清小图；验收同时比较 LaTeX 结构、KaTeX 可渲染性与渲染图差异，不能只看字符串非空。

Texo 代码与模型为 AGPL-3.0。本项目只考虑把它部署为隔离的离线数据清洗工具，不直接打包进闭源小程序或生产后端；正式商用前仍需完成许可证审查或取得额外授权。

项目内的 `recognize_texo.py` 直接使用 Texo 官方 ONNX 权重，不安装训练依赖，支持批处理、断点续跑、按优先级或按资产试跑。示例：

```bash
/path/to/python reports/question-formula-batch/recognize_texo.py \
  --model-dir /path/to/texo/model/onnx \
  --priority question_critical \
  --batch-size 16
```

结果会写入同一份 `formula-asset-v1` 合约，`provider=texo_local`；已有结果（包括人工确认）默认跳过，只有显式传入 `--overwrite` 才会重算。

多进程可用 `--shard-count N --shard-index I` 分割任务。各分片的资产 ID 互不重叠，可安全写入同一个结果目录。
