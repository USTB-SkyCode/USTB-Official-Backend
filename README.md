# USTB Official Backend

单 Docker Compose 部署后端 + 前端 + 网关 + 数据库全栈。

## 架构

```
Dokploy / Traefik (提供 TLS + 域名路由)
  └─ Caddy (Compose 内网关, :80)
       ├─ /api*, /auth*, /config.js  → Flask backend
       ├─ /packs/*                   → frontend 静态资源包
       ├─ /resource/mca/*            → MCA 只读挂载
       └─ /*                         → frontend SPA
```

**服务：** secrets-init · postgres · redis · backend · worker · frontend · caddy

## 部署

### 1. 配置

```bash
cp deploy/prod/env.example deploy/prod/.env
vi deploy/prod/.env
```

**必填：**

| 变量 | 说明 | 示例 |
|---|---|---|
| `APP_SITE_HOST` | 前端域名 | `app.example.com` |
| `API_SITE_HOST` | API 域名 | `api.example.com` |
| `API_BASE_URL` | 前端 API 地址 | `https://api.example.com` |
| `AUTH_BASE_URL` | 前端 Auth 地址 | `https://api.example.com` |
| `APP_BASE_URL` | 前端站点地址 | `https://app.example.com` |
| `CORS_ALLOWED_ORIGINS` | CORS 白名单 | `https://app.example.com` |
| `MCA_BASE_URL` | MCA URL 前缀 | `/resource/mca/ustb` |
| `MCA_STORAGE_ROOT` | MCA 宿主机目录 | `/data/official/mca` |

`SECRET_KEY`、`FILE_DOWNLOAD_TOKEN_SECRET`、`PGSQL_PASSWORD` 首次启动自动生成，无需填写。

### 2. 准备宿主机目录

```bash
mkdir -p /data/official/mca   # MCA，无数据时留空即可
```

### 3. 启动

```bash
cd deploy/prod
docker compose up -d
```

Caddy 在容器内监听 :80。在 Dokploy 中将 `APP_SITE_HOST` + `API_SITE_HOST` 路由到 Caddy 服务即可。

### 4. 资源包编译（首次部署时）

前端引擎需要编译后的资源包（纹理、方块数据）。源包放在 `FRONTEND_RESOURCEPACK_HOST_PATH`（默认 `/data/official/front-resourcepack`）。

```bash
docker compose --profile frontend-resource-build run --rm frontend-resource-builder
docker compose restart frontend
```

编译产物写入 `frontend_packs` 卷，frontend 重启后自动挂载。源包更新时重新运行即可。

### 5. 前端自动重部署（可选）

1. 在 Dokploy 面板复制 Compose 应用的 Deploy Webhook URL。
2. 前端 GitHub 仓库 Settings → Secrets → Actions 中新建 `DOKPLOY_REDEPLOY_HOOK_URL`。
3. 前端推送 `main` 后自动触发 Compose 重部署。

## 本地开发

```bash
cp deploy/dev/env.example .env
docker compose -f deploy/dev/docker-compose.yml up -d --build
```
