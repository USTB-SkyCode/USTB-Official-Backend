# Official-backend

基于 Docker Compose 的 Flask 后端 + Caddy 网关部署堆栈。

## Dokploy 生产部署

当前推荐部署入口是 [deploy/prod/docker-compose.yml](deploy/prod/docker-compose.yml)。
前端会由同一份 Compose 通过 `FRONTEND_BUILD_CONTEXT` 直接构建

### 1. 准备生产环境变量

先从模板复制：

```bash
cp deploy/prod/env.example deploy/prod/.env
```

当前生产 Compose 约定使用的就是 `deploy/prod/.env`，不是仓库根目录 `.env`。
手动执行时也建议在 `deploy/prod` 目录下运行，或者显式带上 `--env-file deploy/prod/.env`，避免出现“插值读取根目录 `.env`、而 `env_file` 读取 `deploy/prod/.env`”的混用。

重点只看两类变量：

1. 域名与前端运行时配置：`APP_SITE_HOST`、`API_SITE_HOST`、`API_BASE_URL`、`AUTH_BASE_URL`、`APP_BASE_URL`、`CORS_ALLOWED_ORIGINS` 等。
2. 宿主机资源路径：主要是 `MCA_STORAGE_ROOT`。

`SECRET_KEY`、`FILE_DOWNLOAD_TOKEN_SECRET`、`PGSQL_PASSWORD` 在生产 Compose 首次启动时会自动生成并持久化；如果你想固定值，再手动填写覆盖。

### 2. 在 Dokploy 中创建 Compose 应用

Dokploy 这一段按当前仓库配置应理解为：

1. 在 Dokploy 中创建一个基于本后端仓库的 Compose 应用。
2. Compose 文件指向 [deploy/prod/docker-compose.yml](deploy/prod/docker-compose.yml)。
3. 将填好的 `deploy/prod/.env` 内容作为该应用的环境变量来源。
4. 在 Dokploy 的域名 / 路由设置中，把你的前端域名和 API 域名都路由到这份 Compose 暴露出来的 Caddy HTTP 服务。

README 不再假定 Dokploy UI 的固定字段名或固定表单布局，因为不同版本界面会变；但要求是明确的：公网流量先到 Dokploy / Traefik，再转到 Caddy，Caddy 在 Compose 内继续分发前后端请求。

### 3. 前端推送如何触发重部署

前端仓库已经提供了对应 workflow：
[USTB-Official-Website/.github/workflows/dokploy-redeploy.yml](https://github.com/USTB-SkyCode/USTB-Official-Website/blob/main/.github/workflows/dokploy-redeploy.yml)

它只在前端仓库里存在 `DOKPLOY_REDEPLOY_HOOK_URL` 这个 GitHub Secret 时才会触发。实际做法是：

1. 在 Dokploy 应用里拿到 deploy webhook。
2. 把它填到前端仓库的 `DOKPLOY_REDEPLOY_HOOK_URL` Secret。
3. 前端仓库推送后，workflow 会 POST 这个 webhook，触发同一份 Compose 重新部署，从而重建 `frontend` 服务。

### 4. 手动 Traefik 实验入口（非 Dokploy）

如果你要在类似 `world-dev` 的机器上临时模拟“Traefik -> Caddy -> 前后端”的链路，不要改主 Compose，也不要把这个实验入口交给 Dokploy。请额外叠加 [deploy/prod/docker-compose.traefik-lab.yml](deploy/prod/docker-compose.traefik-lab.yml)。

启动实验入口：

```bash
docker compose \
	-f deploy/prod/docker-compose.yml \
	-f deploy/prod/docker-compose.traefik-lab.yml \
	--profile traefik-lab \
	up -d caddy traefik-lab
```

停止实验入口：

```bash
docker compose \
	-f deploy/prod/docker-compose.yml \
	-f deploy/prod/docker-compose.traefik-lab.yml \
	--profile traefik-lab \
	stop traefik-lab
```

注意：

1. Dokploy 仍然只应使用 [deploy/prod/docker-compose.yml](deploy/prod/docker-compose.yml)，不要额外带这个 override。
2. `traefik-lab` 同时使用了 `profiles: ["traefik-lab"]` 和 `restart: "no"`，不会因为普通 `docker compose up` 或 Docker 守护进程重启而自启动。
3. 默认监听宿主机 `80/443`；如果只是本机实验，可临时设置 `TRAEFIK_LAB_HTTP_PORT`、`TRAEFIK_LAB_HTTPS_PORT` 改成别的端口。
4. 如果实验域名已经被浏览器记成 HSTS（例如 `app-dev.nand.cloud`），就不能再用默认自签证书；当前 override 已改为让 Traefik 自动申请公开证书。首次启动时给它几十秒完成 ACME 校验即可。
5. 如需登记 ACME 联系邮箱，可在环境中额外提供 `TRAEFIK_LAB_ACME_EMAIL`；不提供也不影响这条实验链路默认不自启动。

## 路径规则

生产配置里有两类路径，不要混淆：

1. 容器内固定路径：例如 `/data/file-data`、`/data/mca`。这些已经由 Compose 接管，你不需要再在 `.env` 里填写。
2. 宿主机路径：例如 `MCA_STORAGE_ROOT`。这类路径需要你自己决定并创建。

当前 Dokploy 生产部署里，通常只有 `MCA_STORAGE_ROOT` 需要你显式提供宿主机绝对路径。

### MCA 资源怎么填

- `MCA_BASE_URL` 是对外 URL 前缀，必须以 `/` 开头，例如 `/resource/mca/ustb`。
- `MCA_STORAGE_ROOT` 是宿主机上的实际目录，例如 `/data/official/mca`。
- Caddy 会把 `MCA_STORAGE_ROOT` 只读挂载到容器内固定路径 `/data/mca`。
- 请求 `/resource/mca/ustb/world/region/r.0.0.mca` 时，Caddy 实际读取的是 `{MCA_STORAGE_ROOT}/world/region/r.0.0.mca`。

部署前这一步需要你自己做：

```bash
sudo mkdir -p /data/official/mca
```

目录权限 `755`，文件 `644` 。

没有 MCA 文件，建议先创建一个空目录并填入 `MCA_STORAGE_ROOT`，避免 Compose 挂载时报错。

### 哪些路径不用填

- 下载文件路径已经固定挂载到容器内 `/data/file-data`，不需要再填写 `FILE_STORAGE_ROOT`。
- 宿主机侧的下载目录默认用 `FILE_DATA_HOST_PATH=../../file-data`。这里的 `../../` 是相对 `deploy/prod/docker-compose.yml` 所在目录计算的，也就是回到仓库根目录后使用 `file-data/`。如果你不喜欢相对路径，直接在 `deploy/prod/.env` 里把 `FILE_DATA_HOST_PATH` 改成绝对路径即可。
- Caddy 内部的 MCA 挂载点已经固定为 `/data/mca`，不需要再填写 `MCA_STORAGE_MOUNT`。
- `FRONTEND_BUILD_CONTEXT` 默认就是官方前端仓库；只有你要换 fork、本地目录或其他 Git 仓库时才需要改。若改成宿主机路径，则该目录需要由你自己准备，并且必须能被 Dokploy 所在主机访问。

## 本地开发部署

如果只是本地 Docker 联调：

```bash
cp deploy/dev/env.example .env
docker compose -f deploy/dev/docker-compose.yml up -d --build
```
