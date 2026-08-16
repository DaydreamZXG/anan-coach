# 事业单位联考 A/B 类《综合应用能力》私有题源入库记录

> 执行日期：2026-08-11  
> 源目录：`data/sydw-liankao-zongying`  
> 数据集：`sydw-zongying-ab-private-20260811`  
> 目标库：`gongkao_dazi_staging`（当前生产 API 实际读取的运行库）

## 入库结果

- **Verified**：45 套卷面，2015—2026 年，A/B 类各考次来源保留。
- **Verified**：解析出 212 道顶层作答任务、209 段材料、31 张图片。
- **Verified**：212 道任务全部写入生产运行库，212 条卷面 occurrence，1,197 条任务/材料 knowledge chunk。
- **Verified**：31 张图片全部以私有 ACL 上传，数据库建立 128 条按题关联的资产绑定。
- **Verified**：重放导入时 212 题和 212 occurrence 全部识别为已存在，未重复造题。

## 隔离与真实性边界

源清单明确标注 `user_private`，且卷面不含参考答案和解析。当前公共申论目录又没有账号级题源隔离字段，因此本次按以下安全边界入库：

- **Verified**：212 题全部为 `quarantined`，意味着“已保存，不进入公共抽题”。
- **Verified**：未生成任何 content head，未写入任何公共图片 URL。
- **Verified**：未写入任何 `essay_task_versions` 或 `essay_rubric_versions`。
- **Verified**：答案非空数为 0；没有用 AI 伪造标准答案或评分规程。
- **Verified**：对象存储 ACL 仅有账号所有者的 Full Control，无公开读取 grant。

## 卷面完整度

| 检查项 | 任务数 | 处理 |
| --- | ---: | --- |
| 未能唯一提取单题分值 | 4 | 保留源文，禁止自动发布 |
| 未提取到单题分值 | 45 | 保留源文，禁止自动发布 |
| 出现多个字数限制 | 13 | 视为复合任务，待人工拆分 |
| 未提取到字数限制 | 42 | 保留源文，禁止自动发布 |

这些不影响卷面、材料和任务的私有归档，但会阻止其直接变成可评分、可公共抽取的练习任务。

## 尚未完成的产品化工作

- **Unknown / blocked**：尚未得到应绑定的精确用户账号 ID，不能安全实现只对该账号可见。
- **Unknown / blocked**：综应 A/B 类的私有目录路由和账号级过滤尚未实现。
- **Unknown / blocked**：评分规程需基于卷面材料、作答要求及可追溯评分依据单独编译与校验，不能从缺失答案中猜测。

## 可重放工具

- 导入器：`backend/scripts/import_sydw_zongying_private.py`
- 定向测试：`backend/tests/scripts/test_import_sydw_zongying_private.py`
- 本地复验：5 个用例通过，Ruff 通过。

