# 考公搭子

考公搭子是由岗位、解题、规划三个 Agent 共享学习大脑的国考学习产品。当前优先交付微信小程序，iPhone/iPad 客户端在核心闭环打通后继续开发。

Current initial slice:

- FastAPI backend with health, bootstrap, taskstream preview, and workbench preview endpoints.
- Native SwiftUI iPhone/iPad shell with 今日、工作台、我的.
- 今日 and 工作台 show 行测 + 申论 as first-class preparation tracks.
- Production candidate server at `https://kg.xingguoxian.cn`.

统一验证入口：

```bash
# 后端、迁移、OpenAPI 契约、小程序结构/类型/测试和架构边界
./scripts/verify.sh

# 在上述检查之外编译 iPhone 和 iPad 客户端（需要 macOS + Xcode）
./scripts/full_verify.sh
```

分项验证：

```bash
./scripts/verify_backend.sh
./scripts/verify_wechat.sh
```

Secrets are not stored in the repository.
