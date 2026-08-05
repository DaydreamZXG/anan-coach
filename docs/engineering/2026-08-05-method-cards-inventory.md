# 2026-08-05 行测方法卡体系 — 交接记录

> 目标:把课程笔记 PDF 提炼成"刷题当场能用"的方法卡,接入考公搭子。
> 状态:5 个模块 392 张方法卡已落地并通过 schema 校验,老师/机构标识已中性化。
> 素材来源:用户下载的薛睿/花生十三等课程图文笔记(有文本层)。

---

## 一、成果总览(392 张方法卡)

| 模块 | 素材 | 卡数 | 覆盖 | 文件 |
|---|---|---|---|---|
| 判断推理·逻辑 | 63 份薛睿笔记 | 83 | 论证六步法/因果推理/削弱支持/秒杀/形式逻辑 | panduan_fuluoji_batch01_draft.yaml |
| 判断推理·图推 | 56 份薛睿笔记 | 111 | 图推24诀,每张带 knowledge_tree_mapping | panduan_tutui_batch01_draft.yaml |
| 判断推理·一拖五 | 23 份薛睿笔记 | 46 | 队列/分组/数独分析推理 | panduan_yituowu_batch01_draft.yaml |
| 资料分析 | 28 份花生十三笔记 | 71 | 尾数法/415/假设分配/截位直除/各增长率 | ziliao_huasheng_batch01_draft.yaml |
| 数量关系 | 19 份笔记 | 57 | 方程/和差倍比/经济利润/行程/排列组合/几何 | shuliang_batch01_draft.yaml |
| 判断推理·试点 | (手动) | 1 | 由因推果(试点) | panduan_youyintuiguo_draft.yaml |
| 原有 | (手动) | 23 | 言语10 + 资料分析13 | yanyu/ziliao_batch01_draft.yaml |

**总计 392 张,全部通过 method_cards schema 校验(可导入)。**

## 二、方法卡格式(刷题当场能用)

每张卡包含:识别特征(看到什么用这方法) / 适用与排除 / 方法步骤(具体可操作) / 常见错误 / 改正动作 / 限时 / 正确率 / 易错点。

**图推特有**:每张卡带 `knowledge_tree_mapping`,把薛睿"图推24诀"(图形长相)映射到知识树"规律分类"(数量/位置/属性/样式)——衔接两套体系,这是用户担心的"体系对不上"的解法。

## 三、合规处理(已做)

- 老师名(薛睿/花生十三/牟立志)在方法卡的 `author_note` 已全部清除(312+处),统一为中性描述"依据公开行测方法论整理,AI生成,等待人工回测"。
- 方法卡目录 data/method_cards/ 无老师/机构名残留。
- 前端后端代码无硬编码老师名。
- ⚠️ 待用户决策:题库原始导入文件(data/question-bank-export/)里的来源标识("fenbi"等),是否也要处理。

## 四、素材提取原则(已定)

- 只提炼**有文本层的 AI 图文笔记**(如"xx_笔记.pdf"),截图不用管(文本已描述方法)。
- 纯截图 PDF(如解析篇/题本篇)→ 是题库素材,不走方法卡管线。
- 下载素材优先选带文字总结的图文笔记。

## 五、待办

1. **言语+常识方法卡**(任务#46):用户正在准备素材,下载好发路径。
2. **方法卡接入应用**(关键):392 张卡怎么让用户看到——按考点查(字典式)/错因触发(做错推卡)/两者都要,待产品决策。
3. **解析硬板优化**:现有解析是题库 `preferred_analysis` 原文,机械术语化。建议①展示层润色成朋友鼓励风 ②与错因/方法卡联动。待产品确认。
4. ✅ **题库 fenbi 标识清理(已完成 2026-08-05)**:
   - 方法卡 author_note 老师名清除(312处)
   - API 脱敏:QuestionOccurrence schema 删 source_dataset_id/source_path/source_question_id,前端 types.gen.ts 已重新生成
   - 数据库:canonical_question 53011 + question_occurrences 195564 + option_html 2475处,全部替换为 qbank 中性标识
   - 备份:ops.fenbi_backup_20260805 + ops.option_html_fenbi_backup_20260805(可回退)
   - 保留:remote_asset_root 服务器内部路径、存档 JSON(内部实现细节,用户不可见)

## 六、关键词提取管线(可复用)

```
课程笔记 PDF(有文本层)
  → 分组(按课/诀/考点)
  → 并行 agent 读全文,提炼结构化方法卡(Workflow)
  → 转 method_cards YAML(data/method_cards/xingce/)
  → schema 校验(可导入)
  → 中性化 author_note
```
