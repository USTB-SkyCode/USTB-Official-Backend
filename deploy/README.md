# Deploy Layout

部署配置按用途分为三层：

```
deploy/
├── common/          # 开发与生产共用的镜像构建材料 (Dockerfile, requirements)
├── dev/             # 开发环境
│   ├── docker-compose.yml   # 热重载后端 + postgres + redis (+ Caddy HTTPS profile)
│   ├── Caddyfile            # 开发用反向代理，前端指向本机 Vite dev server
│   ├── env.example          # 开发环境变量模板
│   └── prodlike/            # 本地生产模拟
│       ├── docker-compose.yml   # 完整生产链路：secrets-init + 前端容器 + Caddy
│       ├── Caddyfile            # 与 prod 一致的路由
│       └── env.example          # prodlike 环境变量模板
└── prod/            # 生产部署
    ├── docker-compose.yml
    ├── Caddyfile
    ├── env.example
    └── scripts/
```

## dev — 开发环境

用于日常后端开发，Python 源码热重载：

```bash
cd deploy/dev
cp env.example .env   # 填入或保持默认值
docker compose up -d
# 后端监听 127.0.0.1:5000，postgres :5432，redis :6379
```

如需本地 HTTPS（Caddy 自签证书 + 反向代理到 Vite）：

```bash
docker compose --profile https up -d
```

## dev/prodlike — 本地生产模拟

完整复现生产部署链路（secrets-init、前端容器、Caddy 路由），用于上线前验证：

```bash
cd deploy/dev/prodlike
cp env.example .env   # 基于 deploy/prod/env.example 填写完整变量
docker compose up -d
```

## prod — 生产部署

详见 [Official-backend/README.md](../../README.md)。

## 环境文件约定

| 文件 | 用途 |
|------|------|
| `deploy/*/.env` | 实际使用的环境文件（git 忽略） |
| `deploy/*/env.example` | 空模板，只保留字段和注释 |