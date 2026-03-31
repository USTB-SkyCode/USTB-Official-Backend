# deploy/prod/.env 成功案例详解

这份说明对应当前在 aliyun-dev 上跑通的生产实验链路：

- 应用入口：deploy/prod/docker-compose.yml
- 入口代理：deploy/prod/docker-compose.traefik-lab.yml（仅手动实验时启用）
- 默认 override：不启用；`docker-compose.override.yml` 必须保持禁用或改名，prod 入口通过 `COMPOSE_FILE=docker-compose.yml` 锁定主编排
- 已验证访问：
  - https://app-dev.nand.cloud
  - https://api-dev.nand.cloud/healthz

## 1. prod 和 prodlike 是否共用同一组数据卷

是，共用。

prodlike 在 compose 中把自己的卷声明成 external，并显式绑定到 prod 命名卷：

- prod_runtime_secrets
- prod_postgres_data
- prod_redis_data
- prod_caddy_data
- prod_caddy_config

当前 prod 栈实际也使用这一组卷名，所以从 prodlike 切到 prod 时：

- PostgreSQL 数据没有迁移
- Redis 数据没有迁移
- runtime secrets 没有迁移
- Caddy 的状态目录也没有迁移

这就是为什么切换编排入口后，服务仍能直接起来。

## 2. PostgreSQL 数据在 Docker Compose 情况下到底存在哪里

当前运行中的 postgres 容器把数据写在容器内：

- /var/lib/postgresql/data

这个路径由 Docker named volume 持久化到宿主机：

- 卷名：prod_postgres_data
- 宿主机 mountpoint：/var/lib/docker/volumes/prod_postgres_data/_data

也就是说：

- 你不要去容器临时文件系统里找数据库数据
- 真正长期保存的是 Docker volume
- 只要 prod_postgres_data 不删，重建容器不会丢库

宿主机上可用下面的方式确认：

- docker volume inspect prod_postgres_data
- docker inspect official-backend-postgres

## 3. 为什么当前这份 .env 能跑通

核心原因有四个：

1. 域名分工是清晰的。
   - APP_SITE_HOST=app-dev.nand.cloud
   - API_SITE_HOST=api-dev.nand.cloud

2. 前端运行时仍然走同站点主入口。
   - API_BASE_URL=https://app-dev.nand.cloud
   - AUTH_BASE_URL=https://app-dev.nand.cloud
   - APP_BASE_URL=https://app-dev.nand.cloud

   这意味着前端默认向 app-dev 同站点发请求，再由 Caddy 在站内分发到后端接口。

3. MCA 资源路径是完整的。
   - MCA_BASE_URL=/resource/mca/ustb
   - MCA_STORAGE_ROOT=/srv/ustb/prod/mca/ustb

4. prod 和 prodlike 共用 runtime secrets 卷。
   - SECRET_KEY / FILE_DOWNLOAD_TOKEN_SECRET / PGSQL_PASSWORD 的最终生效值，优先取 prod_runtime_secrets 里的持久化结果
   - 所以这类值不是每次都单纯从 .env 现读现用

## 4. secrets-init 的优先级，为什么很重要

当前 deploy/prod/scripts/ensure_runtime_secrets.py 的逻辑是：

1. 先读持久化文件
2. 持久化文件没有时，再读 .env
3. .env 也没有时，才自动生成

因此要特别注意：

- 如果 prod_runtime_secrets 已经存在，那么你后来改 .env 里的 SECRET_KEY、FILE_DOWNLOAD_TOKEN_SECRET、PGSQL_PASSWORD，不会自动覆盖卷里已有值
- 如果你想让新值真正生效，需要先理解并处理好 prod_runtime_secrets 的持久化内容
- 当前成功案例里，FILE_DOWNLOAD_TOKEN_SECRET 留空也能运行，原因正是“已有卷时会复用旧值；新卷时会自动生成”

## 5. 当前成功案例建议保留的关键值

下面是当前成功案例的结构化结论。敏感值请继续以 deploy/prod/.env 为准，不要把真实密钥复制进文档或提交到仓库。

### 核心必填

- SECRET_KEY：当前为显式固定值
- PGSQL_PASSWORD：当前为显式固定值
- CORS_ALLOWED_ORIGINS=https://app-dev.nand.cloud,https://api-dev.nand.cloud
- OAUTH_ALLOWED_REDIRECT_HOSTS=app-dev.nand.cloud,api-dev.nand.cloud
- APP_ALLOWED_RETURN_HOSTS=app-dev.nand.cloud
- TRUSTED_HOSTS=app-dev.nand.cloud,api-dev.nand.cloud,localhost,127.0.0.1
- MCA_BASE_URL=/resource/mca/ustb
- MCA_STORAGE_ROOT=/srv/ustb/prod/mca/ustb
- API_BASE_URL=https://app-dev.nand.cloud
- AUTH_BASE_URL=https://app-dev.nand.cloud
- APP_BASE_URL=https://app-dev.nand.cloud
- SKIN_API_BASE_URL=https://skin.ustb.world/skinapi
- APP_SITE_HOST=app-dev.nand.cloud
- API_SITE_HOST=api-dev.nand.cloud

### 成功案例中的附加显式覆盖

这些值不在精简版 env.example 主体中，但当前成功配置显式保留了：

- SESSION_LIFETIME=2592000
- OAUTH_ALLOW_HTTP_LOCALHOST=false
- APP_ALLOW_HTTP_LOCALHOST=false
- RSS_SOURCE_URL=https://docs.ustb.world/api/rss?lang=zh
- BACKEND_UPSTREAM=official-backend-app:5000

## 6. 哪些值这次可以不再写进 .env

当前 .env 已经按 env.example 的格式做过整理，下面这些原先写在 .env 里、但现在不再显式保留：

- FLASK_DEBUG
- FLASK_HOST
- FLASK_PORT
- PGSQL_HOST
- PGSQL_PORT
- PGSQL_DB
- PGSQL_USER
- REDIS_URL
- SECURE_COOKIES
- DEFAULT_LOGIN_SUCCESS_URL
- MAX_CONTENT_LENGTH
- PROXY_FIX_X_FOR
- PROXY_FIX_X_PROTO
- PROXY_FIX_X_HOST
- WTF_CSRF_SSL_STRICT
- MCA_ACCESS_LEVEL
- SAME_ORIGIN_ASSET_PROXY_PATH
- ASSET_PROXY_ALLOWED_HOSTS
- DEV_BACKEND_PROXY_ENABLED
- DEV_AUTH_* 全部

原因不是这些配置无效，而是：

- 要么 compose 已经固定注入
- 要么应用代码里已有相同默认值
- 资源包路径现在固定遵循 /packs/<目录>/{compiled,assets,metadata.json} 约定
- 要么当前成功案例并不依赖它们的显式覆盖

## 7. 对未来修改 .env 的建议

1. 不要随意动 SECRET_KEY、FILE_DOWNLOAD_TOKEN_SECRET、PGSQL_PASSWORD，除非你先处理对应持久卷。
2. 如果只是改域名、CORS、OAuth 回调、MCA 路径，这类值直接改 .env 即可。
3. 如果切回 prodlike，再切回 prod，不需要迁移数据库，只要 prod_* 这些命名卷还在即可。
4. 如果要换一台全新机器重建环境，再考虑是否把 FILE_DOWNLOAD_TOKEN_SECRET 也显式写入 .env，避免首次自动生成后的值不可预期。
