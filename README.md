# USTB Official Backend

Flask backend、worker 与整站部署合同仓库。前端源码位于独立仓库 `USTB-Official-Website`，而本仓库负责 API、认证、下载鉴权、运行时配置以及 `deploy/dev` / `deploy/prod` 两条部署链路。

## 文档入口

- [deploy/README.md](deploy/README.md)：`deploy/dev` 与 `deploy/prod` 的总体结构和边界。
- [deploy/dev/env.example](deploy/dev/env.example)：唯一有效后端开发链路的环境模板。
- [deploy/prod/env.example](deploy/prod/env.example)：生产部署环境模板。

后端开发链路只保留 `deploy/dev`；生产部署合同见 `deploy/prod`。

## 架构

```
┌─ 入口层 ──────────────────────────────┐
│  A. 外部入口 (k8s / 云 LB / 平台)     │  ← 平台提供 TLS，默认模式
│  B. Caddy ACME (Let's Encrypt)       │  ← 单机直接签发
└───────────────────────────────────────┘
          ↓ HTTP
  Caddy (Compose 内网关, 默认 :80)
       ├─ {API_SITE_HOST}                → Flask backend (全部流量)
       └─ {APP_SITE_HOST}
            ├─ /api*, /auth*, /config.js → Flask backend
            ├─ /downloads/*              → forward_auth + file_server
            ├─ /packs/*                  → frontend 静态资源包
            ├─ /resource/mca/*           → MCA 只读挂载
            └─ /*                        → frontend SPA
```

**服务：** secrets-init · postgres · redis · backend · worker · frontend · caddy

---

## 部署

### 1. 配置

```bash
cp deploy/prod/env.example deploy/prod/.env
vi deploy/prod/.env    # 完整字段说明见 env.example 注释
```

**必填：**

| 变量 | 说明 | 示例 |
|---|---|---|
| `APP_SITE_HOST` | 前端域名 | `app.example.com` |
| `API_SITE_HOST` | API 域名 | `api.example.com` |
| `API_BASE_URL` | 前端 API 地址 | `https://app.example.com` |
| `AUTH_BASE_URL` | 前端 Auth 地址 | `https://app.example.com` |
| `APP_BASE_URL` | 前端站点地址 | `https://app.example.com` |
| `SKIN_API_BASE_URL` | 皮肤服务 API | `https://skin.example.test/skinapi` |
| `CORS_ALLOWED_ORIGINS` | CORS 白名单 (逗号分隔) | `https://app.example.com` |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | OAuth 回调允许 host | `app.example.com` |
| `APP_ALLOWED_RETURN_HOSTS` | 登录跳转允许 host | `app.example.com` |
| `TRUSTED_HOSTS` | 可信 Host 头白名单 | `app.example.com,localhost` |
| `MCA_BASE_URL` | MCA URL 前缀 | `/resource/mca/ustb` |
| `MCA_STORAGE_ROOT` | MCA 宿主机目录 | `/data/official/mca` |
| `FRONTEND_RESOURCEPACK_HOST_PATH` | 资源包源文件宿主机目录 | `/data/official/front-resourcepack` |
| `FILE_DATA_HOST_PATH` | 后端文件下载宿主机目录 | `/data/official/file-data` |

`SECRET_KEY`、`FILE_DOWNLOAD_TOKEN_SECRET`、`PGSQL_PASSWORD` 首次启动由 `secrets-init` 容器自动生成并持久化到 `runtime_secrets` 卷；如需固定值（如迁移数据库），可在首启前填写。

**选填（按需启用）：**

| 分类 | 关键变量 | 说明 |
|---|---|---|
| OAuth: GitHub | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_REDIRECT_URI` | GitHub 登录 |
| OAuth: USTB Skin | `USTB_CLIENT_ID`, `USTB_CLIENT_SECRET`, `USTB_BASE_URL`, `USTB_REDIRECT_URI` | Blessing Skin 登录 |
| OAuth: MUA | `MUA_CLIENT_ID`, `MUA_CLIENT_SECRET`, `MUA_REDIRECT_URI` | MUA 登录 |
| Bilibili | `BILIBILI_SESSDATA`, `BILIBILI_JCT`, `BILIBILI_DEDEUSERID`, `BILIBILI_BUVID3` | 视频抓取 |
| RSS | `RSS_SOURCE_URL` | RSS 来源 |
| Compose 覆盖 | `FRONTEND_BUILD_CONTEXT`, `BACKEND_BUILD_CONTEXT`, `FILE_DATA_HOST_PATH` | 自定义构建源/路径 |

完整字段及默认值参见 [env.example](deploy/prod/env.example)。

### 2.1 三个 /data 路径配置 (必须先确认)

以下 3 个路径都是宿主机绝对路径；建议在 `.env` 里显式填写，不要依赖隐式默认值。

| 变量名 | 推荐值 | 是否必填 | 容器内挂载点 | 作用 |
|---|---|---|---|---|
| `MCA_STORAGE_ROOT` | `/data/official/mca` | 是 | `/data/mca` (caddy, ro) | 提供 MCA 区域文件给 `/resource/mca/*` |
| `FRONTEND_RESOURCEPACK_HOST_PATH` | `/data/official/front-resourcepack` | 否 (强烈建议填) | `/build/resource/resourcepack` (frontend-resource-builder, ro) | 资源包源文件输入目录 |
| `FILE_DATA_HOST_PATH` | `/data/official/file-data` | 否 (强烈建议填) | `/data/file-data` (backend/worker rw, caddy ro) | 下载文件和后端文件目录 |

### 2. 准备宿主机目录

部署前须手动创建以下目录，并确保运行 Docker 的用户有权访问。

`/data/...` 只是推荐值；如果你在 `.env` 中改了路径，下面命令要改成对应值。

| 宿主机路径 (默认) | 容器内映射 | 用途 | 备注 |
|---|---|---|---|
| `/data/official/mca` | `/data/mca` (Caddy) | MCA 区域存档 | 只读。存储 `r.x.y.mca` 文件。 |
| `/data/official/front-resourcepack` | `/build/resource/resourcepack` | 材质包源文件 | 只读。用于 `frontend-resource-builder` 编译。 |
| `/data/official/file-data` | `/data/file-data` (Backend) | 后端持久化文件 | 读写。存储用户上传、公告图片等。 |

```bash
mkdir -p /data/official/mca                  # MCA 区域文件，无数据时留空即可
mkdir -p /data/official/front-resourcepack   # 资源包源文件
mkdir -p /data/official/file-data            # 后端持久化文件目录
chmod 755 /data/official/mca /data/official/front-resourcepack /data/official/file-data
```

将材质包源文件上传到 `front-resourcepack/` 目录，结构如下：

```
front-resourcepack/
├── minecraft16.pack.json
├── hybrid128.pack.json
├── minecraft/          
├── 05cube/
├── 05pbr128/
├── 05redstone/
└── 05glasspane/
```
文件夹为解压后的资源包文件夹,结构为xx/assets/minecraft/blockstates,models,textures...
pack.json 是实际的资源包集合配置，格式示例：

```jsonc
{
  "key": "hybrid128",                // 唯一标识，用于运行时选包
  "label": "立方构想V4028(128px)",     // 前端 UI 显示名称
  "description": "当前默认的 128px 混合 PBR 资源组合。",
  "order": 1,        // 多个 pack.json 的序号，以最小的为网页默认材质包
  "directory": "05pbr",              // 编译产物输出子目录（/packs/{directory}/compiled/）
  "maxTextureSize": 128,             // 材质包分辨率（px）
  "labPbr": true,                    // 是否启用 labPBR 法线/高光分析
  "packs": ["05cube", "05redstone", "05glasspane", "05pbr128", "minecraft"]
  // ↑ 资源包合并顺序，右侧优先级更低（minecraft 为兜底）
}
```

### 3. 启动

```bash
cd deploy/prod
docker compose up -d
```

默认状态下 Caddy 在容器内监听 **:80**，不暴露宿主机端口。你需要选择一种入口层来提供外部访问和 TLS（见下节）。

### 4. 资源包编译（首次部署 / 源包更新后）

前端引擎需要编译后的资源包（纹理、方块数据）。resource-builder 容器从 `FRONTEND_RESOURCEPACK_HOST_PATH` 读取源文件（只读），编译产物写入 `frontend_packs` Docker 卷。

```bash
docker compose --profile frontend-resource-build run --rm frontend-resource-builder
docker compose restart frontend   # 重启后 entrypoint 自动挂载编译产物
```

也可使用 helper 脚本（支持 `--no-build` 跳过镜像重构建）：

```bash
FRONTEND_RESOURCEPACK_HOST_PATH=/data/official/front-resourcepack \
  bash deploy/prod/scripts/run_frontend_resource_build.sh
```

---

## 入口层（TLS 配置）

Caddy 默认以 `http://` 监听，不持有 TLS 证书。外部 HTTPS 接入有两种方式：

### A. 外部入口（默认）

适用于 k8s Ingress、云负载均衡、托管平台等已有 TLS 终结的环境。

Caddy 容器 :80 不暴露到宿主机，由平台自行路由到 Caddy 服务。Compose 无需额外操作，平台侧将 `APP_SITE_HOST` + `API_SITE_HOST` 指向 Caddy 容器即可。

如果你使用 Dokploy 的 Docker Compose 域名功能，请保留 `caddy` 服务上的项目默认网络。Dokploy 会为被选中的服务额外挂载 `dokploy-network` 供 Traefik 访问；若 `caddy` 只剩该代理网络，则会失去对 `frontend` / `backend` 的服务发现能力，并对站点根路径与 `/config.js` 同时返回 `502 Bad Gateway`。

Dokploy 的环境变量编辑器会把“留空”的键也当成显式空字符串注入容器，而不是让应用回退到代码默认值。因此：

- 对确实依赖默认值的变量，优先不要在 Dokploy 中创建该键。
- 对必须存在但允许首启自动生成的密钥（如 `SECRET_KEY` / `FILE_DOWNLOAD_TOKEN_SECRET` / `PGSQL_PASSWORD`），按对应初始化流程处理，不要把其他默认值变量机械地全部留空保存。
- 当前 Python 后端已经统一把空字符串视为“未配置”并回退默认值，但 Compose / Caddy / 其他入口层不一定具备同样语义，所以部署侧仍然应以“省略默认值变量”为主。

### B. Caddy ACME（单机直接签发）

适用于单机部署，Caddy 自动向 Let's Encrypt 申请并续期证书。

1. **Caddyfile**：将 `http://{$APP_SITE_HOST}` / `http://{$API_SITE_HOST}` 改为 `{$APP_SITE_HOST}` / `{$API_SITE_HOST}`（去掉 `http://` 前缀，Caddy 自动启用 ACME）。
2. **docker-compose.yml**：取消注释 caddy 的 `ports` 段，暴露 80 和 443。
3. 确保域名 DNS 已指向本机。

Caddy 会自动处理证书签发、续期和 HTTP→HTTPS 重定向。

---

## 运维

### 查看日志

```bash
cd deploy/prod
docker compose logs -f backend         # 后端
docker compose logs -f worker          # 后台任务
docker compose logs -f caddy           # 网关
docker compose logs -f frontend        # 前端
docker compose logs -f --tail=100      # 全部服务最近 100 行
```

### 更新部署

```bash
cd deploy/prod
git pull
docker compose up -d --build           # 重构建后端/前端镜像并滚动重启
```

如只更新前端仓库（远程构建源），后端代码无变化：

```bash
docker compose build frontend && docker compose up -d frontend
```

### 数据备份

PostgreSQL 数据存储在 Docker named volume `prod_postgres_data`：

```bash
docker exec official-backend-postgres pg_dump -U postgres ustbhome > backup.sql
```

---

## 本地开发

当前标准开发链路是：本机 `Official-backend` 工作树通过 Mutagen 同步到开发服务器 `/srv/ustb/dev/Official-backend`，然后在远端 `deploy/dev` 启动 compose。也就是说，日常改 Python 代码时，不是本地直接起一个 Flask 进程，而是改本地源码、同步到远端工作树，再由容器内 `GUNICORN_RELOAD=true` 感知变更。

推荐顺序：

1. 在本机确保 Mutagen 会话 `official-backend-dev` 已经把 `Official-backend` 同步到远端工作树。
2. 在远端 `deploy/dev` 准备 `.env` 并启动基础 dev 栈。
3. 如需和本机前端做同域 HTTPS 联调，再额外启动 `https` profile 的 `caddy`。

```powershell
cd D:\vueCode\OfficalWorld
.\world\scripts\privacy\remote\ensure_backend_mutagen_sync.ps1 -Action ensure
```

```bash
cd /srv/ustb/dev/Official-backend/deploy/dev
cp env.example .env
docker compose up -d --build
```

如需前端同域 HTTPS 联调：

```bash
cd /srv/ustb/dev/Official-backend/deploy/dev
docker compose --profile https up -d caddy
```

然后在前端仓库 `world/` 中执行：

```powershell
cd D:\vueCode\OfficalWorld\world
npm run app-dev
```

联调时的关键约定：

- 页面入口 `APP_BASE_URL`、`API_BASE_URL`、`AUTH_BASE_URL` 保持同一个同域 HTTPS 开发入口
- 前端 `.env.local` 中 `LOCAL_APP_DEV_PROXY_REMOTE_ORIGIN` 使用本机 SSH tunnel 入口
- 前端 `.env.local` 中 `LOCAL_APP_DEV_PROXY_BACKEND_HOST_HEADER` / `LOCAL_APP_DEV_PROXY_BACKEND_SERVERNAME` 保持浏览器主入口对应的域名
- 远端直连调试入口只保留给排障，不作为日常浏览器入口

对这条链路，需要记住：

- Python 热重载依赖 `Mutagen -> /srv/ustb/dev/Official-backend -> ../../:/app -> GUNICORN_RELOAD=true`
- `worker` 不会自动热重载，改动后仍需手动 `docker compose restart worker`
- `deploy/dev/.env` 被 Mutagen 显式忽略，远端 `.env` 需要单独维护，不会跟随源码热同步
- 当前 dev 的 MCA 宿主机目录是 `/srv/ustb/dev/mca`；启用 `https` profile 前必须保证 `MCA_STORAGE_ROOT` 指向真实目录
- 生产和远端直连环境里的 `/config.js` 由后端返回；本机 `app-dev` 接管时，前端 Vite 会从 `world/.env` 生成同形态的 `window.APP_CONFIG`
- `npm run app-dev` 会同时启动本机 Vite、本地 HTTPS 代理和后端 SSH tunnel
