# 事业单位联考 A/B 类职测参考包导入记录

> 执行日期：2026-08-11  
> 来源包：`/Users/zxg/Desktop/fenbi_papers_final_complete(4).zip`  
> 目标：`gongkao_dazi_staging`（当前线上 `gongkao-dazi-api` 实际读取的运行库）  
> 数据集：`sydw-ab-zhice-reference-20260811`

## 结果

- 卷面题目出现记录：3,000 条（30 套卷，A/B 各15套）。
- 可信关联现有题：6 条 occurrence，对应 5 道不同现有题。
- 跨卷同题去重：588 条。
- 新建题：2,406 道。
  - `active`：2,148 道，已生成可作答内容版本与 content head。
  - `quarantined`：258 道，已保留来源记录，但不向用户发题。
- 题图与材料图：495 个不同来源路径，494 个唯一图片对象，全部发布至对象存储。
- 数据库资产行：1,664 条（题干图 315 条，材料 HTML 图 1,349 条）。
- 原始材料 HTML 内的相对图片地址已全部改写为对象存储地址；未遗留 `images/...` 本地引用。

## 隔离原因

下列数字按原始 occurrence 统计，一题可同时命中多个原因：

| 原因 | 出现数 |
| --- | ---: |
| 缺选项 | 155 |
| 缺答案 | 118 |
| 缺共享材料 | 84 |
| 缺必需题图 | 30 |
| 题干缺失 | 13 |

不完整题禁止跨卷自动合并，避免把两道同样“缺图/缺材料”的通用题干误认为同题。

## 卷面结构

| 类别 | 原生板块 | occurrence |
| --- | --- | ---: |
| A 类 | 常识判断 | 300 |
| A 类 | 言语理解与表达 | 375 |
| A 类 | 数量关系 | 75 |
| A 类 | 判断推理 | 525 |
| A 类 | 资料分析 | 225 |
| B 类 | 常识判断 | 300 |
| B 类 | 言语运用 | 300 |
| B 类 | 数学运算 | 75 |
| B 类 | 资料分析 | 150 |
| B 类 | 判断推理 | 525 |
| B 类 | 综合分析 | 150 |

B 类原生板块仍保存在 occurrence 与标签中；为兼容当前作答运行时，
`言语运用`、`数学运算`、`综合分析` 分别映射到现有客观题模块，没有改写原始考试分类。

## 复验

- staging 题量从 231,078 变为 233,484。
- 2,148 道 active 新题全部有 content head，无缺题干/答案/选项。
- 258 道 quarantined 题全部无 content head。
- 518 道题保留材料 HTML，其中 274 道含材料图；活跃内容中本地相对图片地址为 0。
- 抽查 10 个公开题图/材料图 URL，均返回 HTTP 200。
- 重放导入结果：0 新题、0 新 occurrence；2,406 题和 3,000 occurrence 均识别为已存在。
- 修复前后题目 ID 集合逐项对照：2,406 对 2,406，双向差异均为 0，未重复造题。
- 线上 `gongkao-dazi-api` 服务状态为 active，其 `/etc/gongkao-dazi/db.env` 中的
  `DATABASE_URL` 当前指向 `gongkao_dazi_staging`；服务器现无独立
  `gongkao_dazi` 数据库。因此这批 active 内容已进入生产 API 实际读取的题库，
  无需再复制一遍数据或重启服务。

## 可重放工具

- 匹配工具：`backend/scripts/match_reference_papers.py`
- 导入工具：`backend/scripts/import_sydw_ab_reference.py`
- 导入测试：`backend/tests/scripts/test_import_sydw_ab_reference.py`
