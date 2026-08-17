# 2026-08-17 真机错因恢复基线

## 真机与生产观察

- 真机错因整理在约一分钟等待后终止，页面把模型线路故障误写成“未能在冻结证据和预算内形成候选”。
- 对应生产任务为 `cause-job-9a3cae01`，唯一模型调用请求 TokenRhythm OpenAI 兼容端点，以 `provider_http_504` 终止。
- 当前 CauseAgentProvider 只读取通用单路配置，worker 账本结算假定每任务最多一次模型调用，无法在现有两次模型预算内完整审计跨路由恢复。
- 答题卡额外渲染“继续第 N 题”，并把当前题图例硬编码为 1。
- 练习页 `1024px` 双栏媒体查询未限定横屏，纵向大屏会被错认为工作台布局。

## 路由兼容性观察

- DeepSeek 官方文档的 OpenAI 兼容基地址为 `https://api.deepseek.com`，目标备用模型为 `deepseek-v4-pro`。
- 用户自有 NewAPI 中转站的 `/v1/models` 受控探测返回 HTTP 200，当前 DeepSeek 路由标识为 `deepseek-v4-flash-free`。
- 该中转站的 `/v1/chat/completions` 接受 JSON Object 请求，已返回可解析 JSON。它只能按真实模型标识作为最终应急线路，不得冒充 `deepseek-v4-pro`。

## 密钥约束

两份新密钥均不进入源码、Git、测试、报告或日志；只进入生产机 root-only 的 cause-agent worker 环境文件。
