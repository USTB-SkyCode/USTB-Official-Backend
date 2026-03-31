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
| `FRONTEND_RESOURCEPACK_HOST_PATH` | 资源包源文件宿主机目录 | `/data/official/front-resourcepack` |

`SECRET_KEY`、`FILE_DOWNLOAD_TOKEN_SECRET`、`PGSQL_PASSWORD` 首次启动自动生成，无需填写。

### 2. 准备宿主机目录

```bash
mkdir -p /data/official/mca                  # MCA 区域文件，无数据时留空即可
mkdir -p /data/official/front-resourcepack   # 资源包源文件
```

将资源包源文件上传到 `front-resourcepack/` 目录，结构如下：

```
front-resourcepack/
├── minecraft16.pack.json
├── hybrid128.pack.json
├── minecraft/          # 源纹理目录
├── 05cube/
├── 05pbr128/
├── 05redstone/
└── 05glasspane/
```

pack.json是实际的资源包集合配置,格式示例:

```jsonc
{
  "key": "hybrid128",                // 唯一标识，用于运行时选包
  "label": "立方构想V4028(128px)",     // 前端 UI 显示名称
  "description": "当前默认的 128px 混合 PBR 资源组合。",
  "order": 1,        // 多个pack.json的序号,以最小的为网页默认材质包
  "directory": "05pbr",              // 编译产物输出子目录（/packs/{directory}/compiled/）
  "maxTextureSize": 128,             // 材质包分辨率（px）
  "labPbr": true,                    // 是否启用 labPBR 法线/高光分析
  "packs": ["05cube", "05redstone", "05glasspane", "05pbr128", "minecraft"]
  // ↑ 源纹理目录合并顺序，右侧优先级更低（minecraft 为基线兜底）
}
```

### 3. 启动

```bash
cd deploy/prod
docker compose up -d
```

Caddy 在容器内监听 :80。在 Dokploy 中将 `APP_SITE_HOST` + `API_SITE_HOST` 路由到 Caddy 服务即可。

### 4. 资源包编译（首次部署时）

前端引擎需要编译后的资源包（纹理、方块数据）。resource-builder 容器从 `FRONTEND_RESOURCEPACK_HOST_PATH` 读取源文件（只读），编译产物写入 `frontend_packs` Docker 卷。

```bash
docker compose --profile frontend-resource-build run --rm frontend-resource-builder
docker compose restart frontend   # 重启后 entrypoint 自动挂载编译产物
```

源包更新后重新运行以上两条命令即可。

### 5. 前端自动重部署（可选）

1. 在 Dokploy 面板复制 Compose 应用的 Deploy Webhook URL。
2. 前端 GitHub 仓库 Settings → Secrets → Actions 中新建 `DOKPLOY_REDEPLOY_HOOK_URL`。
3. 前端推送 `main` 后自动触发 Compose 重部署。

## 本地开发

```bash
cp deploy/dev/env.example .env
docker compose -f deploy/dev/docker-compose.yml up -d --build
```
