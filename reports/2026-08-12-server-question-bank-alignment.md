# 服务器运行题库只读对账

日期：2026-08-12  
范围：国考、省考、事业单位（职测 A/B + 综应 A/B）  
结论性质：只读差异基线，不是发布完成声明，不授权写库、上传、切换发布头或重启服务。

## 结论

服务器上的题库不是“没有对齐”，但也不能宣布“已经完全对齐”。实际情况是：

1. 服务器运行服务使用的数据库已通过 `/etc/gongkao-dazi/db.env` 只读解析确认；报告只保存单向数据库指纹 `sha256:f50d49b3959204bb4ecea14e48a67c4d4c31fce7c6a167969da8b4b66f88d9bb`，未保存用户名或密码。
2. 国考主来源几乎完整：本地 4,790 条出现记录中，服务器缺 1 条来源 occurrence / 1 道对应题；另有 8 道题处于 `needs_identity_review`，没有内容头，不会进入当前可作答集合。
3. 省考与国考复用同一行测来源，但按卷属保持独立范围身份。服务器还有 `qbank-import-20260801` 的 91,111 条 occurrence / 58,091 道运行题，但当前没有足够的国考/省考卷属证据。工具不再猜测它们属于省考，而是整批列入 `unclassifiedRuntimeDatasets`，避免串库。
4. 事业单位来源已经实际入库：A/B 职测 3,000 条 occurrence、综应 212 条 occurrence 与本地稳定身份一致；综应仍为私有隔离，且没有 content head，符合当前权利与答案/评分依据边界。
5. 事业单位范围共有 470 道 `quarantined`，其中职测 A/B 258 道、私有综应 212 道。另有 3 个本地应存在的内容寻址图片对象键在数据库资产绑定中缺失，以及 4 个服务器历史资产绑定不属于当前参考包的内容寻址键；它们需要逐项复核，不能靠“数量补齐”自动处理。
6. 当前只存在 `guokao/xingce` 的活动知识图谱发布；省考和事业单位没有独立活动知识发布身份。这是“题已经在库”和“教练能按考试范围稳定调度”之间最大的产品闭环缺口。

## 权威输入与活动发布

本次对账使用三份不可变本地来源：

| 本地来源 | SHA-256 |
| --- | --- |
| `data/question-bank-export/db/question-bank-subset-20260702.dump` | `7d07c3296f047ffa038d56dcefc406fbc6ad78ac3a9bc57144e4c24ef9e572f5` |
| `/Users/zxg/Desktop/fenbi_papers_final_complete(4).zip` | `c9486dd3382458ed5a37dd873e60acd9e526964dcdc98d64e1b17e122367a2da` |
| `data/sydw-liankao-zongying/`（相对路径 + 文件哈希复合清单） | `78729860c83dc4f5d5760d35b6354bdc4f00885a6861ea2a4ebf0b51486c2297` |

服务器活动发布身份：

- 商业数据发布：`0ab1b5ea-4d86-4cdc-9007-a367a1e592c0`，digest `1ba2c02b5279c1d4c632f63384ea4bbcfe39f8f7bccbfd93e718922fc745f889`。
- 知识图谱：`guokao:xingce:knowledge-map@20260805T000000Z-v5`，source data `product-qbank@20260727`，vocabulary `vocab@v1`，fingerprint `38f8c22cfe9c0891af918b15961f2e84bd82a385bcae250384697623c6270959`。
- 未发现省考或事业单位独立的活动知识图谱发布。

## 分范围对账

### 国考

本地清单：3,427 道来源题、4,790 条 occurrence、3,427 个应有 content head、6,849 个从旧导出 HTML 解析出的图片对象键。

服务器：3,426 道来源题、4,789 条 occurrence、3,418 个 content head；其中 3,418 道 active、8 道 `needs_identity_review`。

精确缺口：

- 缺题及 occurrence：`qbq_cc57ab0b01603432f7a3ae98f5d5` / `qbo_cc57ab0b01603432f7a3ae98f5d5`。
- 8 道 `needs_identity_review`：
  `qbq_06c256467c5d822c24853a7b089f`、
  `qbq_14d54b567457d0f7c3e256a59dc4`、
  `qbq_4b4f582f5ac534354def070c5d1a`、
  `qbq_74680268ecdc5b20858bb1aede28`、
  `qbq_8db966b48f1acf7ca6053cc65110`、
  `qbq_b114213202e37824c05ce59efcf2`、
  `qbq_c6d9606f9cb5041495cb3b9d12c0`、
  `qbq_fa46c7b333642855e31acdc533c2`。
- 上述 8 道均没有 content head；缺失题也没有 content head，因此国考合计缺 9 个应有 content head。

图片资产的 5,102 个“缺失”是旧 dump 中 HTML 引用与运行时独立资产表之间的迁移差，不等价于 5,102 张用户必需题图坏掉。服务器另行只读统计显示，15,117 道 `visual_required` 题中 27 道没有任何资产绑定；这 27 道才是后续用户可见性修复的优先审计集合。

### 省考

2026-07-02 基线含 50,153 道来源题、99,972 条 occurrence。服务器同一 `qbank-final-analysis-dedup-v2-20260613` 来源相对基线存在：

- 缺 305 道来源题、308 条 occurrence、957 个应有 content head；
- 652 道题状态由旧基线的 active 变成服务器的 `merged` 或 `needs_identity_review`，其中 490 道 merged、162 道 needs identity review；这些属于后续去重/身份治理结果，不能直接回滚成 active。

另有 `qbank-import-20260801` 被单独记为待归类运行来源。它不进入省考或国考差异计数，也不得因为“本地 manifest 没有”而被删除。下一阶段应从卷属、年份、地区与既有稳定题身份恢复可证明的范围。

### 事业单位

本地和服务器稳定身份一致的范围：

- A/B 职测：3,000 条 occurrence；
- 综应 A/B：212 条 occurrence；
- 当前本地判定：2,742 道 active、470 道 quarantined；服务器状态计数一致；
- 综应的 212 道全部 quarantined 且不创建 content head，符合 `user_private`、无答案解析、无评分 rubric 的边界。

资产差异：3 个当前参考包的内容寻址对象键缺数据库绑定；服务器有 4 个旧路径式绑定不属于当前参考包的内容寻址清单。后续应 HEAD 对象、核对题目引用后决定补绑定或清理；本次不做任何对象存储操作。

服务器另有 `sydw-public-import-20260801`：118,726 条 occurrence、118,720 道来源题及 content head。该源是散题池，缺少可还原套卷所需的完整卷属字段；它可以作为额外练习来源，但不能冒充 A/B 整卷，也不能替代综应。

## 确定性 dry-run 结果

三个已证明考试范围的合同计数（包含服务器存在但本地 manifest 未覆盖的事业单位公开散题池，以及旧 dump HTML 图片迁移差）：

```json
{
  "reconciliationFingerprint": "sha256:003a977d3e4a9da3b8f45162933c12e694ddab2114e2e92b2ea64fda9cf83f9c",
  "missingCount": 77296,
  "extraCount": 356170,
  "mismatchCount": 660,
  "quarantinedCount": 470,
  "withdrawnCount": 0,
  "mutationCount": 0
}
```

待归类来源不计入上述三范围合同：

```json
{
  "datasetId": "qbank-import-20260801",
  "occurrenceCount": 91111,
  "questionCount": 58091
}
```

相同本地 catalog、运行时 catalog 和差异 catalog 重放后，以上计数与 reconciliation fingerprint 完全一致。JSON 报告文件仅因 `detailDatabase` 临时路径不同而字节不同；合同输出相同。

## 工具与安全边界

新增 `backend/scripts/reconcile_runtime_question_bank.py`，支持：

- `build-local`：流式读 PostgreSQL custom dump，调用既有事业编导入器作为身份/隔离规则权威，输出 SQLite 本地清单；
- `collect-remote`：远端只读解析实际 `DATABASE_URL`，只返回数据库名的单向指纹；使用 `BEGIN TRANSACTION READ ONLY`、语句超时、gzip 流式传输和最终 `ROLLBACK`；无法证明范围的来源只记入待归类元数据；
- `compare`：按 `examScope + kind + immutable identity` 比较 question、occurrence、content head、asset，生成可复现指纹和独立差异 SQLite；
- 没有 apply、upload、publish、restart 或 delete 模式。

自动验收覆盖凭据脱敏、国考/省考/事业单位三范围、未知范围不猜测、四类实体、missing/extra/mismatch/quarantined/withdrawn、确定性重放、批量去重、旧 dump 转义图片引用及只读 SQL。当前 8 项对账专项测试通过；与 A/B 职测、私有综应两套导入器合跑共 19 项通过。

## 下一发布单元建议

不要直接把这份差异表变成自动写库脚本。下一包只处理依赖闭合的最小修复：

1. 为 `qbank-import-20260801` 与 `sydw-public-import-20260801` 生成正式本地不可变 manifest，先消除“本地权威缺失”，不删服务器数据；
2. 单独审计国考缺失 1 题、8 道身份复核题及 27 道视觉必需但无资产绑定题；
3. 核对事业编 3 缺 / 4 多资产键的对象可达性和题目引用；
4. 为省考、事业单位建立独立可发布的知识/选题范围身份；
5. 只有在上述证据闭合后，再冻结一个明确授权写入与可回滚的发布单元。
