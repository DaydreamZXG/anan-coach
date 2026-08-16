# 结构化视觉积木并行开发小本本

日期：2026-08-10

## 已确认现状

- 用户体验问题检测窗口已经完成一批修改并提交；当前主线 `HEAD` 为 `b8bb0855c`。
- “批量结构化资料分析图表”任务继续独立运行，负责资产盘点、图表分类、结构化提取与质量报告。
- 已有可复用的只读原型输入：
  - `reports/data-analysis-chart-structure-test/`
  - `reports/pie-chart-structure-test/`
  - `reports/graphic-reasoning-structure-test/`
- 已验证的方向：
  - 统计图表采用“视觉模型抽取数据与元数据 + 确定性布局引擎渲染”。
  - 图推采用“CV 分框/定位 + 规则或矢量结构 + 前端标准组件渲染”。
  - 模型不负责像素级坐标、面板框架和最终排版。

## 本轮并行任务

单开一个小程序任务，搭建“结构化视觉积木”的独立原型组件，不接入现有业务页面。

允许写入：

- `apps/wechat/miniprogram/design-system/structured-visuals/**`
- `apps/wechat/tests/structured-visuals/**`
- 如需演示数据，仅可写 `reports/structured-visuals-wechat-prototype/**`

只读输入：

- `reports/data-analysis-chart-structure-test/**`
- `reports/pie-chart-structure-test/**`
- `reports/graphic-reasoning-structure-test/**`

禁止修改：

- `app.json`、现有页面、现有业务组件和路由
- `features/**`、`task-runtime/**`、API 生成文件
- 后端、数据库、部署配置
- `package.json` 与依赖；主包不得引入 ECharts 等大型运行时
- 正在批处理的 `reports/data-analysis-batch/**`

## 首批积木范围

1. 统一 schema 校验、错误态和尺寸适配。
2. 轻量统计图渲染器：分组柱状图、柱线组合图、饼图。
3. 图推渲染器：二值网格、标准面板与内部矢量路径。
4. 手机与平板自适应；数值标签需要避免重叠。
5. 无效或不完整数据必须显示明确占位态，不能静默画错。

## 验收边界

- 覆盖有效数据、空值/缺失值、百分比或角度不闭合、9×9 二值网格、畸形输入。
- 完成定向单测、TypeScript 检查、结构与主包边界检查。
- 输出组件 API、样例和验证结果，但不得声称已经接入产品或可以发布。
- 如果接入必须改共享清单、路由或现有业务页面，立即停止并回报，不自行扩范围。

## 后续接入顺序

待资料分析批处理产出稳定 schema 后，再进行一次接口对齐；通过后另开接入任务，把积木接到真实题目页。图推真实样本仍单独验收，不与资料分析批处理混在一起。
