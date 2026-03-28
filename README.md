# Official-backend

Flask 后端 + Caddy 边缘 + Docker Compose 一体化部署。

## 技术栈

- Python 3.12 / Flask / Gunicorn
- PostgreSQL 16 / Redis 7
- Caddy 2（边缘反代、TLS、COOP/COEP）
- Docker Compose

## 快速开始（本地开发）

```bash
cp .env.example .env
python generate_key.py          # 生成 SECRET_KEY / FILE_DOWNLOAD_TOKEN_SECRET
# 将生成值写入 .env

docker compose -f deploy/dev/docker-compose.yml up -d --build
```

---

## 生产部署全流程

### 架构总览

```
Client
  └─ Caddy (边缘, :80/:443, 自动 TLS)
       ├─ @backend (/api*, /auth*, /config.js, /diagnostics*) → backend:5000
       ├─ @mca (MCA 大文件) → forward_auth + file_server
       ├─ @downloads (/downloads/*) → forward_auth + file_server
       └─ 其余所有请求 → frontend:80 (前端容器)
```

前端以独立容器（Dokploy Application）运行，边缘 Caddy 通过 `reverse_proxy` 将非后端请求转发到前端。

### 1. 准备环境变量

```bash
cd deploy/prod
cp env.example .env
```

必填的最小配置项：

| 变量 | 说明 | 示例 |
|---|---|---|
| `SECRET_KEY` | Flask 会话签名密钥。`python -c "import secrets; print(secrets.token_hex(32))"` 生成 | `a1b2c3...` |
| `FILE_DOWNLOAD_TOKEN_SECRET` | 文件下载令牌签名密钥。建议独立于 SECRET_KEY | `d4e5f6...` |
| `PGSQL_PASSWORD` | PostgreSQL 密码。需与 compose 中 `POSTGRES_PASSWORD` 一致 | `your-db-pass` |
| `APP_SITE_HOST` | 前端站点域名（Caddy 自动申请 TLS） | `app.your-domain.com` |
| `API_SITE_HOST` | 后端 API 域名（Caddy 自动申请 TLS） | `api.your-domain.com` |
| `CORS_ALLOWED_ORIGINS` | CORS 允许来源，逗号分隔 | `https://app.your-domain.com` |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | OAuth 回调允许的 host | `app.your-domain.com` |
| `APP_ALLOWED_RETURN_HOSTS` | 登录成功跳转允许的 host | `app.your-domain.com` |
| `TRUSTED_HOSTS` | 信任的 Host 头白名单 | `app.your-domain.com,api.your-domain.com` |
| `API_BASE_URL` | 前端运行时 → 后端 API 入口 | `https://app.your-domain.com` |
| `AUTH_BASE_URL` | 前端运行时 → 认证入口 | `https://app.your-domain.com` |
| `APP_BASE_URL` | 前端运行时 → 应用入口 | `https://app.your-domain.com` |
| `SKIN_API_BASE_URL` | 前端运行时 → 皮肤 API | `https://skin.ustb.world/skinapi` |
| `MCA_BASE_URL` | MCA 资源 URL 前缀（前端运行时 + Caddy 路由） | `/resource/mca/ustb` |
| `MCA_STORAGE_ROOT` | MCA 文件宿主机路径 | `/srv/mca` |
| `MCA_STORAGE_MOUNT` | MCA 挂载到 Caddy 容器内的路径 | `/srv/mca` |

可选但建议填写：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `FRONTEND_UPSTREAM` | 前端容器上游地址 | `http://frontend:80` |
| `BACKEND_UPSTREAM` | 后端上游地址 | `http://backend:5000` |
| `STRICT_ENV` | 严格校验，缺少必填变量时启动报错 | `true`（生产自动开启） |
| `SECURE_COOKIES` | HTTPS cookie | `true` |
| `REDIS_URL` | Redis 连接 | `redis://redis:6379/0` |
| `SESSION_LIFETIME` | 会话有效期（秒） | `3600` |

OAuth Provider 按需填写（至少启用一种登录方式）：

| 组 | 变量 |
|---|---|
| GitHub | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_REDIRECT_URI` |
| MUA | `MUA_CLIENT_ID`, `MUA_CLIENT_SECRET`, `MUA_REDIRECT_URI` |
| USTB | `USTB_CLIENT_ID`, `USTB_CLIENT_SECRET`, `USTB_BASE_URL`, `USTB_REDIRECT_URI` |

完整变量说明见 [deploy/prod/env.example](deploy/prod/env.example)。

### 2. 部署前端容器

前端作为独立 Dokploy Application 部署，详见 [world/README.md](../world/README.md) 中的「生产构建与容器化」部分。

前端容器启动后，需确保它在后端 compose 的同一 Docker 网络中可达，其地址即为 `.env` 中的 `FRONTEND_UPSTREAM`。

如果前端尚未容器化，可临时使用 `docker-compose.override.yml` 挂载静态文件作为过渡：

```yaml
# docker-compose.override.yml (临时，正式上线后删除)
services:
  frontend:
    image: caddy:2
    volumes:
      - /path/to/inner-caddyfile:/etc/caddy/Caddyfile:ro
      - /path/to/front-static:/srv:ro
```

### 3. 启动后端栈

```bash
cd deploy/prod
docker compose up -d --build
```

这会启动 5 个服务：`backend`、`worker`、`postgres`、`redis`、`caddy`。

如果有 `docker-compose.override.yml`，`frontend` 也会一并启动。

### 4. 验证

```bash
# 容器状态
docker compose ps

# 后端健康检查
curl -sSk https://app.your-domain.com/healthz

# 前端 SPA
curl -sSk -o /dev/null -w '%{http_code}' https://app.your-domain.com/

# 运行时配置
curl -sSk https://app.your-domain.com/config.js

# 安全头
curl -sSk -D- -o /dev/null https://app.your-domain.com/ | grep -i cross-origin
```

### 5. 更新部署

```bash
cd deploy/prod
docker compose pull        # 如果使用预构建镜像
docker compose up -d --build --remove-orphans
```

后端代码更新只需 rebuild `backend` + `worker`，数据库和 Redis 持久化在 Docker volumes 中。

---

## 项目结构

```
app/              Flask 应用与业务代码
deploy/common/    公共 Docker 构建入口 (Dockerfile, requirements.txt)
deploy/dev/       开发部署入口
deploy/prod/      生产部署入口 (docker-compose.yml, Caddyfile, env.example)
templates/        模板文件
tests/            测试代码
worker.py         周期任务进程入口
wsgi.py           Gunicorn Web 入口
```

## 公开协作约定

- 基于 `.env.example` 或 `deploy/*/env.example` 准备本地环境变量
- 不要提交真实密钥、数据库密码或 OAuth secret
- 不要把缓存目录、历史草稿和内部文档纳入版本控制