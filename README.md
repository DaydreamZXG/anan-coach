# 考公搭子

考公搭子是由岗位、解题、规划三个 Agent 共享学习大脑的国考学习产品。当前产品形态是微信小程序 + FastAPI 后端；历史 iOS 探索端已从本仓库移除，交叉审计不要按原生 App 路线评估。

Current initial slice:

- FastAPI backend with health, auth, onboarding, position, planning, practice, essay, recognition, privacy, and daily-opening endpoints.
- WeChat Mini Program with 今日、岗位、岸岸、我的四栏主路径 and 64 registered routes
  (58 business routes and 6 asset entries).
- 行测、申论、岗位决策、拍照搜题、私人资料库 and AI 督学 are first-class product tracks.
- Production candidate server at `https://kg.xingguoxian.cn`.

统一验证入口：

```bash
# 后端、迁移、OpenAPI 契约、小程序结构/类型/测试和架构边界
./scripts/verify.sh

# 同一套当前产品全量检查入口，保留给自动化脚本调用
./scripts/full_verify.sh
```

分项验证：

```bash
./scripts/verify_backend.sh
./scripts/verify_wechat.sh
```

`scripts/verify_wechat.sh` 是唯一 WeChat 验证入口，并委托 `npm run check` 执行格式、类型、
ESLint、设计/导航/结构/覆盖/资产门禁、OpenAPI 只读检查、Node contract/gate tests、
完整 Vitest 与云函数 Node tests。

Secrets are not stored in the repository.
