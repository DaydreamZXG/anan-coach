# 服务器题库源身份恢复报告

日期：2026-08-12 至 2026-08-13（Asia/Shanghai）  
发布单元：`RU-QUESTION-BANK-SOURCE-IDENTITY-RECOVERY-008C`  
范围：`qbank-import-20260801`、`sydw-public-import-20260801`

## 结论

服务器已有题目没有重新导入，也没有删除、合并或改写题文。本次把两批历史导入时丢失的卷属、题号、地区、年份、模块和出现键恢复到了活动运行库：

| 数据集 | 源卷 | 源题 | 恢复后卷面出现 | 新增出现 | 隔离重复 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 国考/省考/市考/选调行测 | 902 | 51,215 | 104,762 | 13,651 | 0 |
| 事业编公共基础知识 | 1,630 | 118,720 | 123,812 | 5,091 | 5 |

写后 `product.questions` 仍为 233,696 道；`product.question_occurrences` 从 317,502 条增至 336,244 条，净增 18,742 条，恰好等于两批源清单相对运行库缺失的卷面出现。5 条事业编历史重复记录没有删除，只把 `source_dataset_id` 移入 `sydw-public-import-20260801-duplicate-quarantine`，且不在活动知识发布中。

## 不可变证据

- 源清单指纹：`sha256:40d88080878a6400039781ee269eb533610ed9710d7d37955016726e5ab2e741`
- 写前运行库快照指纹：`sha256:0a55e934484c2d00558937b0af30d753bd3e84cfa6167ca362da4cd8cccb0dae`
- 写前 dry-run 指纹：`sha256:189ec9608865b66c6591a833f459ddad77368a7cb205a35524c2468af4d5ffec`
- 提交计划 CSV：`sha256:448dec8e596928fa002668524d75b52b990111935a37a31b6d6e32dceb1fb28c`
- 写后运行库目标快照：`sha256:e049ed96813aee8dba1d2c7394e6c8a7126caf989dd591ff40859ff3831482a1`
- 写后期望状态指纹：`sha256:5d3ff31ebeadd26c777242ca1d2c17636e7aa13b2e6d3eda3001b7ae66d4e9fd`

运行事实：

- API 实际读取 `gongkao_dazi_staging`，但健康端点明确报告 `environment=production`；没有按数据库名称猜环境。
- Alembic 版本为 `20260809_104`。
- 提交前后 API 都为 `status=ok`，`gongkao-dazi-api.service` 未重启，ActiveEnterTimestamp 保持 `2026-08-09 21:28:27 CST`。
- 写后两批活动数据集每条 occurrence 均有非空卷行 ID、卷键、出现键、地区、年份、题号和模块；出现键重复数为 0。

## 备份与回滚

写入前在服务器 `/srv/gongkao-dazi/question-bank-recovery-20260812/` 创建并验证：

- 完整运行库 custom dump：`pre-source-identity-20260812_2345.dump`，约 1.1GB，SHA-256 `27d2634f708bfa2f358a2fd4da18dc0453e8fa1576423c412a82d05c00560cda`；`pg_restore --list` 通过。
- 三个目标数据集的完整 occurrence CSV 快照：`pre-target-occurrences-20260812_2345.csv.gz`，SHA-256 `2b0d049f18418935c23504ec1eb363b4ef0e61883bb0bf511ade87866fd1895e`；gzip 校验通过，共 209,837 条数据行。

第一次 apply 因源统计存在 `sample_size=0` 被数据库正数约束拒绝，整个事务自动回滚，写后计数仍保持旧值。修正为“无有效样本 = NULL”后重新生成源、dry-run 和提交计划指纹，第二次事务提交成功。

## 写后验收

- 国省考：104,762 条 occurrence、902 个源卷行、104,762 个唯一出现键、51,215 个源题。
- 事业编公共基础：123,812 条 occurrence、1,630 个源卷行、123,812 个唯一出现键、118,720 个源题。
- 隔离集：精确 5 条；保留原 occurrence/question/source-question 身份；活动知识发布命中 0。
- 未映射源题：0。
- 跨考试冲突：0。
- `cls` 明确隔离：国省考全部为 `行测`，事业编公共基础全部为 `公共基础知识`。
- 幂等重放：国省考 104,762 unchanged、事业编 123,812 unchanged；两批 `inserted=0`、`updated=0`。
- 当前两批题没有用户行测作答引用，恢复未改写用户作答事实。

## 仍未完成，不能冒充可投放

卷属恢复完整不等于每道题都可以给用户练：

- 国省考有 119 个源题没有活动内容头；它们已恢复真实卷属，但未进入活动知识发布。
- occurrence/题目答案缺失共 124 条：国省考 8 条、事业编公共基础 116 条；它们未进入活动知识发布。需要后继内容质量单元补证或隔离，不能用默认答案兜底。
- 当前活动知识图谱仍只有国考行测；本次没有把省考或事业编伪装成已经发布的知识图谱。
- 资料分析有 845 份卷、14,280 条出现被恢复到模块；9 份源卷的资料分析数量不是 5 的倍数，使用整组取题前还应按原材料分组复核，不能只按题号硬凑 5 题。

## 其他题库源

以下三批源文件和 manifest 哈希均已验证，但当前服务器没有对应独立 dataset，未在本单元强塞入国省考或事业编公共基础：

| 目录 | 类型 | 卷 | 出现 | 去重源题 | 资产 | 源标记缺解析资产 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `yhzp` | 银行招聘笔试 | 153 | 18,373 | 17,217 | 22,756 | 11 |
| `szyf` | 三支一扶公共基础 | 104 | 8,514 | 8,505 | 648 | 0 |
| `szyfzc` | 三支一扶职测 | 94 | 7,287 | 6,839 | 9,538 | 5 |

下一单元应先做内容指纹与已有题目映射、图片对象逐项 HEAD、独立考试范围与发布身份，再决定只补 occurrence 还是创建确实缺少的题目；不得复用本次两批 dataset ID。

## Git 与本地文件

多 GB 原始题库、图片、恢复 SQLite、CSV 和数据库备份都没有进入 Git。仓库只保留：

- `backend/scripts/restore_question_bank_source_identity.py`
- `backend/tests/scripts/test_restore_question_bank_source_identity.py`
- 本报告

本地恢复工作区位于 `/Users/zxg/Downloads/question-bank-recovery-20260812.DzQCKm/`；原始下载仍位于 `/Users/zxg/Downloads/`。`.gitignore` 已加入 `data/question-bank-sources/`、`data/question-bank-work/`、`data/question-bank-recovery/` 和 `.local-question-bank/`，避免以后把大源包放进仓库时被 Git 扫描。
