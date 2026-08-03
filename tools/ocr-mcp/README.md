# OCR MCP server(答题卡 / 图片文字识别)

给 Claude Code 提供 `ocr_image` 工具:传入图片路径,自动压缩后调用
`https://tp.ergou.pro/v1/ocr`,返回识别文本和置信度。

## 使用

配置见项目根 [`.mcp.json`](../../.mcp.json) 的 `ocr` 条目。重新加载后即可用:

```
ocr_image(imagePath: "/path/to/答题卡.jpg")
```

工具会自动把图片最长边压到 2000px 内(JPEG q88)再上传。手机拍的大图 OCR 服务处理
较慢,单个调用可能耗时 20~60 秒,属正常。

## 环境变量(可选)

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OCR_URL` | `https://tp.ergou.pro/v1/ocr` | 接口地址 |
| `OCR_API_KEY` | 无，必须配置 | Bearer token |
| `OCR_TIMEOUT_MS` | `120000` | 请求超时(毫秒) |

## 手动调试

```bash
node tools/ocr-mcp/server.mjs   # 以 stdio 协议启动
```

## 重新安装依赖

```bash
cd tools/ocr-mcp && npm install --include=optional
```

注意:sharp 依赖平台预编译二进制,装完务必自测一次(见下)。若报
`Could not load the "sharp" module`,说明预编译包损坏,删除 `node_modules` 和
`package-lock.json` 后重装。

自测:

```bash
cd tools/ocr-mcp && npm test
```
