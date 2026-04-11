# Deploy Layout

`deploy/` 只描述当前仍然有效的两类合同：

- `dev`：唯一有效的后端开发链路
- `prod`：生产部署合同

如果你是在做日常后端开发，默认只需要读 `deploy/dev`；只有上线或生产排障时才需要进入 `deploy/prod`。

```
deploy/
├── common/          # dev / prod 共用镜像构建材料
├── dev/             # 唯一后端开发链路：源码挂载 + Gunicorn 热重载 + 可选 HTTPS 入口
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── env.example
└── prod/            # 生产部署合同
    ├── docker-compose.yml
    ├── Caddyfile
    ├── env.example
    └── scripts/
```

## dev

`deploy/dev/` 是当前唯一有效的后端开发入口。

它的目标很明确：

- 本机 `Official-backend` 通过 Mutagen 单向同步到 `/srv/ustb/dev/Official-backend`
- 容器直接挂载这份远端工作树到 `/app`
- `backend` 在容器内用 `GUNICORN_RELOAD=true` 完成 Python 热重载
- `postgres` / `redis` / `backend` / `worker` 作为常驻开发服务固定存在
- 只有在需要和本机前端做同域 HTTPS 联调时，才额外启用 `caddy` 的 `https` profile

关键约束：

- `backend` 与 `worker` 都读取 `deploy/dev/.env`
- `deploy/dev/.env` 被 Mutagen 显式忽略，不会从本机源码目录自动同步到远端
- 当前标准远端工作树是 `/srv/ustb/dev/Official-backend`
- `backend` 通过 `../../:/app` 挂载直接读取这份由 Mutagen 同步到远端的工作树
- `postgres` / `redis` 默认暴露宿主机端口，便于调试和直连
- `worker` 不会自动热重载，改动后仍需手动重启
- 如果你改了远端根路径，必须同时保持 Mutagen 远端根目录与 `deploy/dev` 的相对挂载关系一致

启动顺序：

1. 先在本机确保 Mutagen 同步已经就绪。
2. 再在远端执行：

```bash
cd /srv/ustb/dev/Official-backend/deploy/dev
cp env.example .env
docker compose up -d --build
```

如需同域 HTTPS 联调：

```bash
cd /srv/ustb/dev/Official-backend/deploy/dev
docker compose --profile https up -d caddy
```

启用这个 profile 前，应先在 `deploy/dev/.env` 中提供可访问的 `APP_SITE_HOST`、`API_SITE_HOST`、真实可达的 `DEV_FRONTEND_UPSTREAM`，以及真实存在的 `MCA_STORAGE_ROOT`。当前 dev 机器使用的是 `/srv/ustb/dev/mca`；如果这里留空，Caddy 将无法提供 MCA 文件。

与前端本地同域联调时，还要配合前端仓库 `world/`：

- 首次初始化执行 `npm run setup:local-app`
- 日常启动执行 `npm run app-dev`
- 前端运行时 `APP_BASE_URL`、`API_BASE_URL`、`AUTH_BASE_URL` 保持同一个同域 HTTPS 开发入口
- 前端 `.env.local` 中 `LOCAL_APP_DEV_PROXY_REMOTE_ORIGIN` 使用本机 SSH tunnel 入口
- 前端 `.env.local` 中 `LOCAL_APP_DEV_PROXY_BACKEND_HOST_HEADER` / `LOCAL_APP_DEV_PROXY_BACKEND_SERVERNAME` 保持浏览器主入口对应的域名
- 远端直连调试入口只保留给排障，不作为浏览器运行时默认入口

## prod

`deploy/prod/` 是生产部署合同。

- 默认由外部入口层终止 TLS，再把 HTTP 转到 Caddy
- 如需单机直出 HTTPS，可改为 Caddy ACME 模式
- 前端由 `frontend` 容器提供，资源包由 `frontend-resource-builder` 编译到 `frontend_packs` volume
- 宿主机只需要按 `.env` 提供 `file-data`、MCA、resourcepack 源目录等外部数据

生产部署细节见 [README.md](../README.md)。

## 已废弃语义

下面这些词如果在旧笔记、旧截图或旧聊天里出现，都按历史遗留处理，不再视为当前事实：

- `deploy/dev/prodlike`
- `front-static/live`
- `publish_front_release.sh`
- `Official-front`
- `/srv/ustb` 作为开发链路的必需根路径
- `prod` 文档里的 `traefik-lab`

## 环境文件约定

| 文件 | 用途 |
|------|------|
| `deploy/*/.env` | 实际使用的环境文件（git 忽略） |
| `deploy/*/env.example` | 版本化模板，只保留字段和注释 |