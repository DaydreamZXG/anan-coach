# 岸岸 AI 备考教练

岸岸是一位用真实学习证据持续形成、验证并修正判断的千人千面 AI 备考教练。当前产品形态
是微信小程序与 FastAPI 后端，当前产品收口优先服务事业编 A 类职测与综应；仓库中仍有
大量国考/行测/申论兼容实现，不能把它们直接视为事业编内容已经完成。

正式产品定义、用户闭环、系统边界、当前差距和实施顺序：

- [岸岸 AI 备考教练｜正式产品与系统说明](docs/product/anan-product-system-spec.md)
- [产品文档入口与权威规则](docs/product/README.md)

## 当前代码快照

2026-08-12 对 `main@d42761ce4485` 的机械盘点：

- 微信小程序包含“今日、能力、岸岸、我的”四个主 Tab，共注册 61 个页面；
- 后端生成的 OpenAPI 包含 246 个路径；
- Alembic 迁移链包含 107 个迁移文件，最新文件为 `20260810_106`；
- 认证、建档、岗位、计划、职测地基、申论/综应地基、识别、私人资料、学习大脑、陪伴、
  商业和数据权利等能力已经存在。

这些数字只表示代码规模，不表示测试全绿、功能已闭环或已经部署。审计时工作区存在大量
未提交改动；继续开发前应先按发布单元恢复可重复验证。

## 验证入口

```bash
# 后端、迁移、OpenAPI 契约、小程序结构/类型/测试和架构边界
./scripts/verify.sh

# 同一套全量入口，供自动化脚本调用
./scripts/full_verify.sh
```

分项运行：

```bash
./scripts/verify_backend.sh
./scripts/verify_wechat.sh
```

后端开发说明见 [backend/README.md](backend/README.md)，小程序视觉与实现约束见
[apps/wechat/DESIGN_SPEC.md](apps/wechat/DESIGN_SPEC.md)。

仓库不保存 secrets。任何“已部署、可公测、已上线”结论都必须附目标环境的可重复证据。
