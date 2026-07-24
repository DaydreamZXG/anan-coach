# 步骤 10：每日开场、WeatherKit 与咖啡助手边界

> 工程基线：Git `2ea39314e`
> 本地实现日期：2026-07-21
> 数据库修订：`20260719_47`（基础）+ `20260723_56`（微信模糊定位）

## 结论

每日首次打开的产品合同已实现：岸岸问候、今日权威计划和天气共用一张开场卡，主动作始终是开始学习。咖啡只是 07:00–17:00 的次要建议，支持“今天不喝”和可从“我的”恢复的“以后别问”。

这个单元已完成本地代码、数据、隐私和审计合同，但不宣称 Apple Weather 或瑞幸已在生产可用。缺少外部授权时界面不会编造天气，也不会显示一个无法完成下单的咖啡按钮。

## 产品合同

- 服务端按 `Asia/Shanghai` 自然日记录展示事实，同一用户每日只展示一次。
- 开场任务引用当日计划版本和当前未完成任务，不在客户端另造一份计划。
- 天气位置只在用户主动点击“使用当前位置”后，通过微信模糊定位读取一次；服务端只保存三位小数经纬度，不持续定位，也不传给咖啡下单。
- WeatherKit 只由后端生成 ES256 JWT 并请求 Apple 官方 HTTPS 端点；客户端展示 Apple 返回的归属链接。
- 天气缓存绑定位置精度、语言、时区、读取/失效时间和内容指纹。
- 瑞幸适配器只接受官方 `https://gwmcp.lkcoffee.com/order/user/mcp`，浏览工具白名单与 `createOrder` 分离，付款不属于自动化范围。
- 用户 bearer token 必须用 AES-256-GCM 加密，密文绑定 key ID、用户和连接身份；导出不包含密文或 token 指纹。
- `createOrder` 必须匹配当前预览指纹、未过期且未使用的确认事实；数据库原子消费一次后不能重放。
- 每日开场偏好、位置版本、展示和咖啡决定进入个人数据导出；注销会按外键顺序删除全部相关个人数据。

## 实现面

- 后端 API：`GET /api/v2/plans/daily-opening`、偏好读写、天气位置写入、展示回执和“今天不喝”。
- 迁移 47：偏好/位置版本、天气缓存、瑞幸连接版本、咖啡决定、展示事实和下单确认。
- 小程序：今日页每日开场卡、微信模糊定位、Weather 归属页、主动作跳转和“我的”中的可逆设置。
- 审计：52 个注册页全部进入一页一项交互矩阵，57 个视觉场景覆盖全部页面。

## 外部启用门禁

### Apple Weather

1. 创建并授权 WeatherKit Service ID 和服务端密钥，以 root-only 文件挂载私钥。
2. 在真实域名上验证签名、配额、归属链接和缓存失效。
3. 在微信后台配置 Apple 归属页所需的业务域名，用 Android/iOS 真机验证 `web-view`。
4. 在小程序后台“开发管理 → 接口设置”开通 `wx.getFuzzyLocation`，并在用户隐私保护指引中声明模糊位置信息。
5. 完成后才设 `WEATHERKIT_ENABLED=true`。

### 瑞幸咖啡

1. 取得官方商业允许、每用户授权方案和稳定的工具 schema，不把开发者公共 token 当成用户授权。
2. 补充并审核授权回调、商店/商品浏览、预览和确认页的真实页面/API，以真实账号跑通。
3. 验证预览变更、确认过期、重复点击、网络结果未知和取消下单；付款始终由用户在官方链路完成。
4. 未完成上述证据时，即使配置 MCP 凭据，后端也只返回 `provider_unavailable`，小程序不显示咖啡动作。

## 本地验收

```bash
cd backend
./.venv/bin/ruff check app tests
./.venv/bin/pytest -q tests/test_daily_opening.py tests/test_daily_opening_providers.py

cd ../apps/wechat
npm run structure:check
npm run visual:coverage
npm run interaction:coverage
node scripts/generate-api.mjs --check
npm run typecheck
npm run lint
npm run test
```
