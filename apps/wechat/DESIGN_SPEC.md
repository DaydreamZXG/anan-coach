# 上岸计划小程序 · 前端设计规范

交接文档：沉淀自 v0/frontend-rebuild 分支的视觉与布局重构，后续开发新增/修改页面时必须遵守。最后更新：2026-07-23

## 一、设计原则

1. **粉笔风格明快工具层**：白卡 + 明快蓝，信息密度高但不拥挤。
2. **暖色即岸岸**：暖橙（--warm）只允许出现在「岸岸」（AI 陪伴角色）相关元素上。用户看到暖色 = 有人在陪。严禁把暖橙用于普通按钮、普通标签。
3. **永不暴露机器字段**：rules_v1、product.questions、后端枚举原文等调试/契约字段绝不渲染给用户。展示层一律翻译成人话（参考各页 FACT_LABEL / MODE 映射表做法）。
4. **每页必有四态**：loading、正常、空态（人话引导 + 下一步动作）、演示兜底（接口失败 usingDemo=true + 页脚「演示数据（后端未连接）」）。禁止白屏。

## 二、设计令牌（app.wxss，全部用 var() 引用，禁止硬编码色值）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| --bg | #f5f7fa | 页面背景 |
| --surface | #ffffff | 卡片背景 |
| --ink | #202a3a | 主文字 |
| --ink-2 | #6b7686 | 次级文字 |
| --ink-3 | #9ea8b5 | 辅助文字 |
| --line | #eef1f5 | 分隔线 |
| --primary | #3d7eff | 品牌蓝（按钮/选中/链接） |
| --primary-deep | #2f66e0 | 按钮按压态 |
| --primary-soft | #ecf2ff | 蓝色浅底（标签/选中背景） |
| --warm | #e8833a | 岸岸专用橙 |
| --warm-soft | #fbf0e6 | 岸岸卡浅底 |
| --good | #22b573 | 成功/完成 |
| --good-soft | #e8f8f0 | 成功浅底 |
| --danger | #f0524c | 危险操作（注销等） |
| --shadow | 0 6rpx 24rpx rgba(41,72,125,0.06) | 卡片投影 |
| --radius | 28rpx | 卡片圆角 |
| --radius-sm | 20rpx | 小组件圆角 |

补充色（局部允许）：模考橙标签 #b45309 / #fef3e2；岸岸卡正文 #5c4a35。

## 三、组件规范

### 卡片 .card
白底、圆角 --radius、padding 32rpx、无边框、投影 --shadow。禁止 1rpx 边框卡。

### 按钮 .btn
高 96rpx、胶囊圆角 999rpx。
- .btn-primary：蓝底白字 + 投影 0 8rpx 20rpx rgba(61,126,255,0.28)
- .btn-ghost：--primary-soft 底 + 蓝字
- .btn-warm：仅岸岸场景
- 危险按钮（注销）：#fdecec 底 + --danger 字，参考 privacy 页

### 顶部安全区 safe-top 组件
铁律：所有页面顶部第一个元素必须是 safe-top 组件（app.json 已全局注册）。
原理：JS 读 wx.getMenuButtonBoundingClientRect() 胶囊底部 + 8px；兜底 statusBarHeight + 44px。
严禁用 CSS env(safe-area-inset-top) 做顶部占位（开发者工具里为 0，标题会顶进状态栏）。

### 页内导航
子页面统一：返回键 ‹（56rpx，--ink-2）+ 32rpx/600 标题。返回 wx.navigateBack 失败时兜底 wx.switchTab。

### 悬浮胶囊 tabBar（custom-tab-bar/）
- 距底部 safe-area-inset-bottom + 24rpx 悬浮，宽 100% 减 48rpx，max-width 420px 居中
- 白底 0.96 + backdrop-filter blur + 投影 0 12rpx 48rpx rgba(41,72,125,0.18)
- 选中态：内嵌小胶囊，--primary-soft 底蓝字；岸岸 tab 用 --warm-soft 底橙字
- 页面底部留白必须不小于 200rpx + safe-area-inset-bottom（app.wxss .page 已带）

### 蓝色 hero 头部（今日页模式）
主 tab 页可用出血式渐变头部：linear-gradient(160deg, #4a89ff, #3d7eff 55%, #3670ef)，底部圆角 40rpx。白字分层：纯白主信息、rgba(255,255,255,0.65~0.8) 次要信息。

### 岸岸元素
头像圆形 --warm 底白字「岸」；岸岸卡 --warm-soft 底无边框；岸岸链接/CTA 用 --warm 色。

## 四、布局与排版

- flexbox 为主，双栏用 grid，禁止 float/绝对定位（除非必要）
- 间距用 gap，同一元素不混用 margin/padding 做间距
- 字号阶梯：h1 44rpx/700 · h2 34rpx/700 · 正文 30rpx · text-2 28rpx · text-3 24rpx；行高 1.5~1.7
- 数字加 .num 类（等宽数字）
- 标签/chip：22rpx 字、999rpx 圆角、浅底深字

## 五、平板适配（必须）

1. app.json 已开 "resizable": true，不得移除
2. 全局：屏宽不小于 700px 时 .page 限宽 860px 居中（app.wxss 已有）
3. 页面级双栏：加 @media (min-width: 700px) grid 双栏，参考模式：
   - me 页：概览卡 + 数据条并排，菜单跨栏
   - ability 页：能力地形 + 知识卡广场并排，估分卡/报告跨栏
   - 原则：主内容 + 次内容可并排；表单/对话/做题页保持单栏限宽
4. 出血式元素（hero）在大屏下改独立四角圆角卡（参考 today 页媒体查询）

## 六、工程铁律

1. 新页面必须在 app.json pages 注册
2. 旧版路径跳转必须经 utils/route-map.ts：有真实页面登记 REAL_PAGES，没有的映射到最近替代页
3. 接口封装全部走 api/endpoints.ts，类型从 api/generated/types.gen.ts 引入，不手写响应类型
4. 写操作带幂等 commandKey（前缀-时间戳-随机串），参考 requestAccountDeletion
5. 文案全中文人话，禁止「提交失败」式机器口吻，用「没提交成功，再试一次」式表达
6. Enter 提交输入框处理 CJK 输入法合成态（isComposing / keyCode 229）
7. 改动后必须过 npm run typecheck（apps/wechat 目录）
8. 提交信息用中文，说明改了什么、为什么

## 七、页面清单与已知缺口（2026-07-23）

30 个已注册页面全部可达。留给下迭代：
- Pro 购买流程（entitlement 展示已通，微信虚拟支付下单未接）
- 拍照导入成绩 / 申论手写上传（依赖相机 + OCR，现用自报和文字输入替代）
- 资格补资料目前重进 onboarding 引导流，未做独立表单
- 客服消息（open-type=contact）需在小程序后台开通客服功能才生效
