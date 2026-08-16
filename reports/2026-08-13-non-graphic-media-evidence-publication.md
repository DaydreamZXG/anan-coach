# 非图推题目媒体证据全量接入报告

## 结论

2026-08-13 已将图形推理之外的普通题目图片与题面公式图接入线上结构化证据发布链。两批均经过线上当前题目头、内容版本、运行图片身份和 TOS 原图 SHA-256 的重算与独立 `verify`；题干、选项、答案、解析、图片和卷属均未修改。

| 批次 | 候选 | 发布证据 | 当前绑定 | 隔离 | 状态 |
|---|---:|---:|---:|---:|---|
| `non-graphic-real-images-20260813-v1` | 1,449 | 1,352 | 1,873 | 97 | `active` / `verify` 通过 |
| `non-graphic-formula-question-critical-20260813-v2` | 5,442 | 5,440 | 21,425 | 2 | `active` / `verify` 通过 |
| 合计 | 6,891 | 6,792 | 23,298 | 99 | 已生效 |

## 普通题目实图

- `annotated_raster`：121
- `data_visual`：185
- `perceptual_grid`：189
- `reasoning_diagram`：857
- 49 张 `needs_review` 精确隔离，不将未定语义喂给岸岸。
- 48 张仅指向已合并或非活动题，不强制绑定到其他题。

## 题面公式图

- 5,386 个保留 `machine_verified` 审核身份，不冒充人工复核。
- 54 个人工修正结果保留 `verified` 身份。
- 1 个原图哈希与当前运行资产不一致，1 个公式仍为待复核占位，两者均隔离。
- 已剔除 29 个仅属于图形推理轨道的公式候选；图推仍走独立专项链。

## 教学与岸岸消费边界

- 题干、材料和选项图片：可进入学生端岸岸与后台验证器。
- 解析、解法和答案图片：仅可进入后台验证器，不会在用户“教 AI”前泄露答案。
- 发布读取器依照每条当前 `question_assets.part_kind` 重算受众，离线标签不拥有超越线上运行资产的权威。
- 岸岸学生请求只序列化 `student` 证据；核验器可使用 `student + verifier` 的精确白名单。
- 任一题目版本、图片哈希、当前头或证据引用不一致时，整个教学包失败关闭，不交付部分结果。

## 可回滚与校验

- 发布前数据备份：`/var/backups/gongkao-dazi-structured-visual-before-non-graphic-20260813T1553CST.sql.gz`
- 备份 SHA-256：`c1eda280c3d64f86abd3eb83222e397ad8515faee4212a82dfbe6eafd1faf464`
- 实图计划 SHA-256：`cbe0f5c6a0babfce2299750e5de2864572f47c26b50a5662c6a6441ca07f216d`
- 公式计划 SHA-256：`678e5383cb34329bc5418ed30769d8fe7ed20349119988526a280dd3c902d564`
- 两个批次均可通过发布回执精确撤回本批绑定与当前头；不删除不可变历史版本。
- 生产健康检查：`ok`。

## 后续解析媒体接入

解析图片没有伪造成 `question_assets`。后续批次 `analysis-media-verifier-evidence-20260813-v2` 已新增独立的当前解析身份绑定，按 `content head + analysis_source + source image ref + SHA-256` 接入 verifier-only 消费。完整计数、隔离原因和回滚信息见 [解析媒体验证者证据接入报告](./2026-08-13-analysis-media-verifier-evidence-publication.md)。
