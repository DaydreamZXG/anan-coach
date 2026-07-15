# AI 国考上岸教练小程序

原生微信小程序 + TypeScript。当前工程以正式商业交付为目标，采用类型化路由、可复用设计系统、统一 API 边界和结构门禁。

## 本地验证

```bash
cd apps/wechat
npm install
npm run check
```

后端 OpenAPI 是小程序 API 类型的唯一来源：

```bash
npm run api:generate  # 后端契约变更后更新生成类型
npm run api:check     # CI 检查生成结果是否过期
```

生成目录 `miniprogram/api/generated` 禁止手工修改。

微信开发者工具导入 `apps/wechat` 目录。AppID 可以随项目配置提交；AppSecret、会话密钥和服务端环境变量禁止进入小程序或 Git。

真实编译验证：

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli preview \
  --project /absolute/path/to/apps/wechat \
  --qr-format terminal
```

## 代码边界

- `app/navigation`：类型化路由与注册清单。
- `design-system`：token、基础组件、岸岸和后续图表组件。
- `api`：唯一允许直接访问微信网络 API 的边界。
- `domain`：领域契约；页面不得在 WXML 事件中实现状态机。
- `pages`：当前路由装配层，后续工作区和任务运行时按路线图逐步迁移。

`npm run structure:check` 会检查路由一致性、组件完整性、设计系统入口和页面绕过 API 层的直接网络请求。

## 数据模式

- `prototype`：显式使用可复现原型数据，用于交互审核。
- `api`：使用 API v2。发布构建必须使用该模式，生产不会在请求失败后切换为原型数据。

实施依据：[商业交付路线图](../../docs/engineering/2026-07-12-wechat-commercial-delivery-roadmap.md) · [完整实施方案](../../docs/engineering/2026-07-12-wechat-mini-program-implementation.md)
