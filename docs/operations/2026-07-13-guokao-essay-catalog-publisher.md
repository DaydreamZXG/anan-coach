# 国考申论 v2 内容目录发布器

发布器把现有 `product.questions` 与 `product.knowledge_chunks` 编译为
`product.essay_task_versions`、`product.essay_material_bundle_versions`、
`product.essay_material_segments`。评分规程采用独立的后续编译门禁。

## 发布边界

- 只处理 `domain = 'essay'`、`status = 'active'`，且材料 chunk 的
  `metadata.paperTitle` 包含“国考”或“国家公务员”的题目。
- 完整 prompt 必须来自唯一的 `chunk_kind = 'task'` 知识块；
  `product.questions.stem` 不作为发布兜底。
- 每道题必须有唯一可映射题型、唯一字数要求和唯一总分。
- 每个材料 chunk 必须有明确的 `material_order`、`materialPartIndex`、
  `materialPartCount`，分片序号必须完整且无重复。
- 一道题的全部材料必须来自同一个国考 `paperTitle`。
- 当前源数据没有答案要点或评分规程，因此发布器不会把材料段落伪装成得分点，
  也不会写入可被评阅器使用的 rubric。结果中的 `rubricPendingCount` 与每个版本的
  `rubricStatus = pending_ai_compilation` 明确要求后续 AI rubric 编译、材料引用校验、
  分值守恒校验通过后，再另行追加 `system_compiled + unverified` rubric。

任一入选国考题不满足门禁时，整次发布失败且事务不写入任何目录版本。

## 命令

先执行只读编译和完整性检查：

```bash
backend/.venv/bin/python scripts/publish_guokao_essay_catalog.py \
  --database-url "$DATABASE_URL" \
  --dry-run
```

确认输出后执行发布：

```bash
backend/.venv/bin/python scripts/publish_guokao_essay_catalog.py \
  --database-url "$DATABASE_URL"
```

## 一致性与幂等

- 发布事务先获取 PostgreSQL transaction advisory lock，并发运行会串行化。
- prompt、材料、有序 segment、来源 metadata 与聚合目录均使用规范化
  SHA-256 指纹；当前发布不包含 rubric。
- 当前目录与源内容一致时返回 `unchanged`，不插入重复行。
- 任一源内容变化时，追加新的 task successor，并为该 task 创建相互一致的
  material bundle 与 segments；历史版本不修改。
- UUID 由契约版本、内容哈希和版本号确定性生成，事务重试不会改变身份。

该脚本不会执行 Alembic、权限变更或服务器部署。运行前应独立确认数据库已经
应用 essay intelligence v2 migration，并为执行账号配置所需的 SELECT/INSERT 权限。
