# 众测任务上传清单

只创建 **1 个任务**，四个匿名模型使用同一任务、同一附件和同一首轮 Prompt。

## 表单填写

- 众测框架：云端 Claude Code
- 任务标题：复制 `task-title.txt`
- 首轮 Prompt：复制 `first-prompt.md` 全文
- Rubrics 描述：复制 `rubrics.md` 全文
- 上传附件：只上传 `graphic-reasoning-semantic-public-v1.zip`

## 绝对不要上传

- `graphic-reasoning-semantic-private-evaluation-v1.zip`
- `question-key-private.jsonl`
- `asset-answer-key-private.jsonl`
- `excluded-assets-private.jsonl`
- 任何已有题库解析、正确答案或 DeepSeek 评分结果

## 创建前最后确认

1. 页面显示只有 1 个任务。
2. 附件名含 `public`，不含 `private`。
3. 标题、首次 Prompt 和 Rubrics 已完整粘贴；任务创建后这些信息不能二次修改。
4. Prompt 没有要求人为凑工具调用次数；3,601 张逐图检查是真实任务本身，会自然产生大量工具调用。
5. 任务运行后以终端/WebUI 过程为主观察，不要只看刷新较慢的 session 汇总页。
6. 四个模型完成后再解压私有包，严格隔离答案并由你手动评分。
