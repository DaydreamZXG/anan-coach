# 岸岸微信小程序资产包 v1

这是可回接主产品的独立角色资产包，不包含主产品代码修改。

## 内容

- 四阶段成长：初遇、熟悉、默契、并肩。
- 六种 `CompanionIntent`：陪伴、思考、提醒、鼓励、休息、降权建议。
- 32 px 专用头像。
- 六条小程序专用动画；单元格 240×240，与 Codex 192×208 图集无运行时关系。
- 静态、动画、运行时映射、SHA-256、alpha QA 和接触表。

## 权威文件

1. `runtime-manifest.json`：业务语义到视觉资产的唯一映射入口。
2. `asset-manifest.json`：静态资产尺寸、体积和校验值。
3. `animation-manifest.json`：动画帧数、帧率、播放方式和帧坐标。
4. `qa/alpha-validation.json`、`qa/animation-validation.json`：确定性 QA。
5. `qa/static-contact-sheet.png`、`qa/animation-contact-sheet.png`：视觉 QA。

## 发布约束

- 主包只预载 `loadingGroups.core`，当前 266,874 bytes，低于 350 KiB 预算。
- 成长大图按需加载；其他动画按 `CompanionIntent` 单独加载。
- 连续动画最大常规展示为 120 CSS px；160–240 px 使用对应高分辨率静态图或单次动画。
- 关闭动效时使用该语义专门的静态关键帧，不使用随机首帧。
- 不得把错误、风险、支付、隐私或网络状态图标替换为岸岸。

## 重新构建

使用工作区绑定的 Python 运行：

```bash
python tools/build_assets.py
python tools/build_animations.py
```

构建只处理本目录源图，不读取或修改主产品业务代码。
