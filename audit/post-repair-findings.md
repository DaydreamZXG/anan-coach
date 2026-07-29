# 修复后残余问题台账

本台账与原始 `audit/findings.jsonl` 分离。原始 15 条 finding 及严重度保持不可变；本文件
只记录最终集成真实渲染中新确认的残余。

## 数量

- P0：0
- P1：0
- P2：1
- P3：0
- 合计：1

## ROOT-RESPONSIVE-METRICS-001 — 窄屏响应式指标同时造成触控不足与主操作越界

- severity：`P2`
- confidence：`high`
- category：UI/UX / accessibility / responsive-layout
- 功能域：岗位、刷题、复盘、模考、申论材料、拍照搜题、私人资料库
- 页面或业务链路：窄屏上的确认、提交、返回、发送、关闭、恢复和图片裁剪
- 状态：`confirmed`
- 去重：触控不足与 crop 纵向越界合并为同一个公共响应式根因
- 推荐后续波次：`Residual Wave R1`

### 前置条件与复现

1. 正式 source 为 `3f7b2e703098b9fbb053f9838929633c5a03ed0b`；
2. 在微信开发者工具依次使用 `320×568`、`363×817`、`390×844`、`768×1024`、
   `1024×768`；
3. 运行正式 visual delivery contracts 和 deterministic visual scenarios，每档完成
   101 个场景；
4. 在 small/long manifest 中筛选 `primaryTouchHeightPass=false` 或
   `primaryGeometry.bottom > viewportHeight`；
5. 检查对应原始 PNG、运行时几何、独立视觉复核与正式 WXSS。

### 期望、实际与用户影响

- 期望：直接可点击控件的视觉或透明热区至少为 `44×44px`；固定主操作在所有支持视口
  与安全区内完整可见。
- 实际：
  - `320×568`：26 个登记主操作中 22 个高度仅
    `36.953125–43.671875px`；
  - `363×817`：5 个高度为 `42.234375px`；
  - 长安卓 `h08-capture-crop` 的确认按钮
    `top=790.9375,height=42.234375,bottom=833.171875`，超出 817px viewport
    `16.171875px`，可见命中高度约 26px；
  - `390×844` 和两档 iPad 均无上述异常；
  - 相关场景全部成功渲染、路由匹配，且无 runtime error 或 actionable warning。
- 影响：窄屏用户在确认交卷、恢复、发送、返回、拍照确认和关闭操作时更易误触；长安卓
  crop 的确认主操作还会部分落在 viewport 外，降低可发现性和可点击面积。仍有可完成
  路径，未证明核心流程完全不可用，因此为 P2 而非 P1。

small 高度分布：

| 高度 | 场景数 |
|---:|---:|
| 36.953125px | 5 |
| 38.625px | 4 |
| 40.3125px | 9 |
| 42px | 1 |
| 43.671875px | 3 |

long 的 5 个触控异常均为 `42.234375px`：

1. `c15-xingce-anan-context` — `.anan-context__send`
2. `f10-essay-material-map` — `.material-return`
3. `h08-capture-crop` — `.crop-actions__confirm`
4. `h17-private-knowledge-processing` — `.processing-background`
5. `h18-private-knowledge-citation` — `.citation-sheet__close`

### 证据

- `audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229/validation.json`：
  五档共 505/505 截图，27 个 profile-scenario 触控异常，long h08 有一处已证实纵向
  越界；
- `audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229/logs/small-320x568-manifest.json`：
  22/26 主操作低于 44px；
- `audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229/logs/long-android-363x817-manifest.json`：
  5/26 低于 44px，h08 bottom 超出 viewport；
- `audit/repair-evidence/final-real-render-3f7b2e703-20260729T003229/screenshots/`：
  五档微信开发者工具原始渲染；
- `audit/subagents/c/final-small-visual-review-3f7b2e703.md` 与
  `audit/subagents/c/final-long-visual-review-3f7b2e703.md`：
  联系表和目标原图独立复核；
- `apps/wechat/DESIGN_SPEC.md` 第 2.2 节：项目规定 44px 触控底线；
- `apps/wechat/miniprogram/design-system/tokens/index.wxss`：手机尺寸使用随屏宽变化的
  `--rpx`，没有独立物理触控 floor；
- `apps/wechat/miniprogram/pages/capture-import/index.wxss`：crop 纵向布局把宽度相对尺寸
  与 viewport 高度预算组合使用。

### 推定根因与受影响范围

同一响应式尺寸体系把随屏宽缩放的 `--rpx` 同时用于物理触控下限和纵向固定布局，并把
`88rpx` 近似当作跨设备恒定 `44px`。窄屏缺少独立 44px 热区 token；crop 又让相同的
宽度相对垂直尺寸参与 `100vh` 高度预算，因此同时出现触控不足和底部主操作越界。

受影响路由：

1. `/pages/positions/goal/index`
2. `/pages/practice/index`
3. `/pages/reflection/index`
4. `/pages/essay-material/index`
5. `/pages/mock-exam/index`
6. `/pages/capture-import/index`
7. `/pages/private-knowledge/index`

公共或关键模块：

- `apps/wechat/miniprogram/design-system/tokens/index.wxss`
- `apps/wechat/miniprogram/design-system/stage9/screen.wxss`
- `apps/wechat/miniprogram/features/positions/page-shell.wxss`
- `apps/wechat/miniprogram/pages/capture-import/index.wxss`

### 推荐修复方向

1. 建立不随手机宽度缩小的 44px 物理触控 token，由公共按钮层统一消费；
2. 页面专用按钮采用 `max(设计高度, 44px)` 或独立透明热区；
3. 把 crop 改为弹性可用高度布局并显式计入 safe area，不再用宽度相对 token 硬减
   `100vh`；
4. 修正规范中 `88rpx == 44px` 的跨设备表述；
5. 把五档真实渲染的触控尺寸和主操作 viewport 几何设为强制门禁。

### 推荐回归测试

1. `320×568` 与 `363×817` 下 26 个登记主操作全部 `height >= 44px`；
2. long h08 确认按钮 `bottom <= viewportHeight` 且完整命中高度至少 44px；
3. 22 个唯一受影响场景逐一真实渲染与原图复核；
4. `390×844`、`768×1024`、`1024×768` 无异常放大、横溢或安全区冲突；
5. 真实触发 c14 文本选择，断言工具条位于 viewport 内且三个操作均达 44px；
6. 公共 s9、页面专用按钮和 crop 的静态合同与运行时几何一致。

### 证据边界

确认范围是本地微信开发者工具五档真实渲染、DOM 几何与原图；未在物理设备统计实际
误触率。

small 的 `c14-xingce-selection` fixture 硬编码 `toolbarTop=545`，所以该截图中的纵向
越界只保留为 hypothesis，不进入正式 finding；按钮自身 `38.625px` 的触控高度仍是
有效证据。
