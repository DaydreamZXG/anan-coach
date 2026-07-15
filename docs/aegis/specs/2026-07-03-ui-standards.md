# UI 规范：考公搭子 iOS 客户端

- 日期：2026-07-03
- 作者：fable。适用：所有改动 apps/ios 的 agent（含 codex 协助 UI 时）
- 原则：教练感（主判断先行）、诚实（数据来源如实标注）、执行纯净（做题时无干扰）

## 1. 设计令牌（唯一来源：CoachComponents.swift 的 DaziPalette）

- brand（主色，靛蓝）：CTA、选中态、进行中状态。
- positive（绿）：答对、完成、达标。
- alert（橙红）：答错、超时、差距为负。仅表意，不装饰。
- 中性色一律用系统语义色（.primary/.secondary/Color(.systemGroupedBackground) 等），保证深色模式免费正确。
- 禁止新增第四种表意色；禁止渐变；禁止直接写十六进制色值到组件里。

## 2. 字体与文案

- 系统字体全套（SF Pro + PingFang），数字一律 .monospacedDigit()（倒计时/分数/题号）。
- 层级：页面标题 .title3.bold；卡片主判断 .headline；正文 .subheadline；辅助 .caption。不超过四级。
- 措辞纪律：搭子判断必须引用真实数据来源；数据缺失时用诚实文案（"还没有你的数据"），禁止"AI 已为你分析"式虚饰；planBasis = rules_v1 时不得自称"智能分析"。

## 3. 布局

- iPhone：单栏，NavigationStack 推入执行页（沉浸：.toolbar(.hidden, for: .tabBar)）。
- iPad（宽 >= 1180）：三栏（左 280 导航 / 中自适应执行画布 / 右 340-400 搭子面板）；竖屏两栏 + 搭子浮钮 sheet。
- 间距用 4 的倍数；卡片圆角 14-16；页面级 padding 18-24。
- 一屏只有一个主 CTA（brand 实底胶囊/圆角条），次要动作一律 .plain 文本按钮。

## 4. 关键组件（复用，勿重造）

- GoalStripView：目标条（岗位 + 倒计时 + 差距）。差距必须来自真实计算，无基线时显示"待摸底"而非数字。
- CoachBriefCard：搭子主判断卡，每页至多一张。
- TaskPathView：垂直步骤流（compact 变体用于 iPad 左栏）。
- CoachFeedbackCard：错因优先（一句话错因 → 同类识别法 → 折叠长解析），methodCard 字段就绪后接 recognition/method_steps。
- ScratchPadView：改造为透明蒙版 overlay（onboarding 规格第 5 节），不再用 sheet。
- 计时器：进行中 .secondary，超时 alert 色；反馈出现即隐藏。

## 5. 状态完备性（每个数据页四态必做）

加载（LoadingPanel）/ 空（诚实空态文案 + 下一步动作）/ 错误（可重试）/ 正常。禁止空白屏，禁止用假数据填充空态。

## 6. 可访问性与细节

- 图标按钮必须带 .accessibilityLabel；触控目标不小于 44pt。
- 深色模式必须验证（系统语义色基本免费，但自定义色要过一遍）。
- 中文排版：行距 lineSpacing 2-4；题干正文可读性优先，不小于 .subheadline。
