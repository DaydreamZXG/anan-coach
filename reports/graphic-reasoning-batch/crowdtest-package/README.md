# 图形推理语义结构化众测交付

本目录准备一个且仅一个全量众测任务。四个匿名模型读取同一公开附件，逐图产生语义 JSON；正确答案、排除证据、人工抽样和 DeepSeek 盲测工具单独保存在私有包。

## 可交付物

- `dist/graphic-reasoning-semantic-public-v1.zip`：上传到众测任务。
- `dist/graphic-reasoning-semantic-private-evaluation-v1.zip`：只在本地评分时使用，严禁上传。
- `submission-fields/task-title.txt`：任务标题。
- `submission-fields/first-prompt.md`：唯一任务的首次 Prompt。
- `submission-fields/rubrics.md`：人工评分 Rubrics。
- `submission-fields/UPLOAD_CHECKLIST.md`：创建前核对清单。
- `dist/package-manifest.jsonl`：两个压缩包的字节数和 SHA-256。

## 范围结论

原始候选 4,857 个。公开包保留 3,601 个真实题干/选项图片；1,255 个只在解析/解答中出现且可能携带红线、红框等答案标注，已隔离；1 个传统人物画资产按用户明确要求排除。公开 inventory 使用内容 SHA-256 去重，不包含题库答案、解析、原始绝对路径或原始题目 ID。

## 重建

压缩包可由下列命令在当前仓库重新生成：

```bash
python3 reports/graphic-reasoning-batch/crowdtest-package/prepare_packages.py
python3 reports/graphic-reasoning-batch/crowdtest-package/test_packages.py
```

脚本只读取现有图推清单和题库图片，且只写 `reports/graphic-reasoning-batch/crowdtest-package/`。
