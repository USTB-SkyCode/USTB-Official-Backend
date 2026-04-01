# Deploy Layout

`deploy/` 只描述当前仍然有效的三条后端链路：`dev`、`dev/prodlike`、`prod`。

```
deploy/
├── common/          # dev / prodlike / prod 共用的镜像构建材料
├── dev/             # 日常后端开发：源码挂载 + 热重载 + 可选本地 HTTPS
│   ├── docker-compose.yml
│   ├── Caddyfile
│   ├── env.example
│   └── prodlike/    # 本地生产形态验证：拓扑对齐 prod
│       ├── docker-compose.yml
│       ├── Caddyfile
│       └── env.example
└── prod/            # 生产部署合同：面向真实部署
    ├── docker-compose.yml
    ├── Caddyfile
    ├── env.example
    └── scripts/
```

## 当前定义

### dev

`deploy/dev/` 是日常后端开发入口，目标是让 Python 源码改动立即生效。

- 本地仓库源码挂载到容器
- `backend` 使用 `GUNICORN_RELOAD=true`
- `postgres` / `redis` 暴露宿主机端口，便于调试
- `caddy` 只在 `profiles: ["https"]` 下启动，用于把本地 HTTPS 域名转到本机 Vite 和容器内后端
- 不要求 `/srv/ustb`，开发路径通过本地仓库和 `.env` 决定

启动：

```bash
cd deploy/dev
cp env.example .env
docker compose up -d
```

如需同域 HTTPS 联调：

```bash
docker compose --profile https up -d
```

### dev/prodlike

`deploy/dev/prodlike/` 是本地生产形态验证入口，不是历史上的前端原子发布容器。

- 服务拓扑对齐 `prod`
- 包含 `secrets-init`、`frontend`、`frontend-resource-builder`、`backend`、`worker`、`postgres`、`redis`、`caddy`
- 使用自己的 local volumes，不与任何 `prod_*` 卷共享
- 前端来自容器和 `frontend_packs` volume，不再是 `front-static/live`
- 用途是验证部署合同、资源包编译和 Caddy 路由，不承担日常前端开发

启动：

```bash
cd deploy/dev/prodlike
cp ../../prod/env.example .env
docker compose up -d
docker compose --profile frontend-resource-build run --rm frontend-resource-builder
docker compose restart frontend
```

`prodlike/env.example` 只补充 `prodlike` 自己的说明；真正完整字段以 `prod/env.example` 为准。

### prod

`deploy/prod/` 是生产部署合同。

- 默认由外部入口层终止 TLS，再把 HTTP 转到 Caddy
- 如需单机直出 HTTPS，可改为 Caddy ACME 模式
- 前端由 `frontend` 容器提供，资源包由 `frontend-resource-builder` 编译到 `frontend_packs` volume
- 宿主机只需要按 `.env` 提供 `file-data`、MCA、resourcepack 源目录等外部数据

生产部署细节见 [README.md](../README.md)。

## 已废弃语义

下面这些词如果在旧笔记、旧截图或旧聊天里出现，都按历史遗留处理，不再视为当前事实：

- `front-static/live`
- `publish_front_release.sh`
- `Official-front`
- `prodlike` 与 `prod_*` 卷共享
- `/srv/ustb` 作为开发链路的必需根路径
- `prod` 文档里的 `traefik-lab`

## 环境文件约定

| 文件 | 用途 |
|------|------|
| `deploy/*/.env` | 实际使用的环境文件（git 忽略） |
| `deploy/*/env.example` | 版本化模板，只保留字段和注释 |