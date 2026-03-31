# Atomic Frontend Static Deploy

This document defines the production contract for frontend static atomic
publishing.

The goal is to make two deploy entrypoints share one release model:

- manual push from the frontend development environment
- future CI or Dokploy-triggered automated frontend publish

Development backend deployment is explicitly out of scope. Dev should not be
coupled to production static atomic release handling.

## Current State

The production entrypoint now serves static files from the atomic release model.
Migration from the old `front-dist` fixed-root model is complete.

Current live system:

- host static root: `/data/official/front-static/`
- Caddy root: `/data/front-static/live` (symlink resolved inside the container)
- latest release: `releases/release-20260322-184224`

The target contract described below is the active production contract.

## Target Contract

The target production model is:

```text
/data/official/front-static/
  live -> releases/release-20260321-210044
  releases/
    release-20260321-210044/
    release-20260322-101500/
```

Rules:

- `front-static/` must be a real directory
- `front-static/live` is the only symlink that changes during publish
- each `releases/release-*` directory is immutable after publish
- the frontend release must be self-contained under that release root
- production static serving must never depend on replacing the host bind-mount
  source path itself

## Why This Contract Exists

If Docker bind-mounts a host path such as `/data/official/front-dist` directly
and that host path is itself replaced, repointed, or repurposed during publish,
running containers may keep serving the old inode view.

The stable-parent model avoids that problem:

- Docker mounts the stable parent directory once
- the `live` symlink changes inside the mounted filesystem
- the entrypoint container keeps seeing the new release through the same mount

## Backend And Caddy Requirements

The backend entrypoint container must mount the stable parent directory, not the
live target path and not a host path that is itself being atomically swapped.

### Required Docker Compose Shape

```yaml
services:
  caddy:
    volumes:
      - /data/official/front-static:/data/front-static:ro
```

### Required Caddy Static Roots

All frontend static handlers must read from `/data/front-static/live`.

That includes:

- immutable hashed assets
- `/packs/*`
- `/resource/*`
- file-like requests
- SPA fallback `/index.html`

Reference shape:

```caddy
handle @immutable_assets {
    root * /data/front-static/live
    encode gzip zstd
    header Cache-Control "public, max-age=2592000, immutable"
    file_server
}

handle @resource_assets {
    root * /data/front-static/live
    encode gzip zstd
    header Cache-Control "public, max-age=2592000, immutable"
    file_server
}

handle @file_like_requests {
    root * /data/front-static/live
    file_server
}

handle {
    root * /data/front-static/live
    encode gzip zstd
    header Cache-Control "no-store"
    try_files {path} {path}/ /index.html
    file_server
}
```

No handler should keep reading `/data/front-dist` once the migration is complete.

## Runtime Config Contract

`/config.js` is not part of the static atomic release payload.

Rules:

- the frontend always requests `/config.js`
- production continues to serve `/config.js` from the backend route
- `config.template.js` is not a live runtime dependency for production deploy

## Release Content Contract

Each release directory must contain the full static site payload required by the
entrypoint container.

At minimum:

- `index.html`
- `assets/`
- `packs/`
- `resource/` if the site depends on that subtree

If any static subtree is meant to be part of the frontend site contract, it must
exist under the release root that `live` points to.

## Publish Algorithm

Both manual and automatic frontend publish flows should follow the same server-
side steps:

1. upload build artifact to a staging directory
2. create a new immutable `releases/release-*` directory
3. extract or sync the artifact into that release
4. validate the release structure before switching traffic
5. atomically move `live.__new` to `live`
6. run smoke checks against the real production domain
7. keep only the newest N releases

The only thing that should differ between manual and automated deploy is who
triggers the publish, not the release logic itself.

## Server-Side Publisher

The server-side source of truth should be a shell script that performs the live
switch. Manual scripts and CI jobs should only upload artifacts and call it.

Reference location:

- `deploy/prod/scripts/publish_front_release.sh`
- `deploy/prod/scripts/run_frontend_resource_build.sh`

Reference invocation with a tarball already uploaded to the server:

```bash
deploy/prod/scripts/publish_front_release.sh \
  --archive /home/m1k/front-publish/static.tar.gz \
  --static-root /data/official/front-static \
  --site-url https://app-dev.nand.cloud \
  --keep 3
```

Reference invocation with an extracted directory already present on the server:

```bash
deploy/prod/scripts/publish_front_release.sh \
  --source-dir /home/m1k/front-publish/extracted \
  --static-root /data/official/front-static \
  --site-url https://app-dev.nand.cloud
```

The `--static-root` value is a host path, not a container path. The parent
directory should already exist or be creatable by the deployment user. The
publisher script will create and manage `releases/` and `live` under it.

## Manual Resource Build From The Backend Deploy Side

Resource pack build is now explicitly treated as a deployer-triggered action,
not something the production frontend image does automatically.

The production compose file exposes a one-shot `frontend-resource-builder`
service. It uses the frontend repository as build context, but reads the actual
resource-pack source tree from a host-mounted directory so deployment-local
`*.pack.json` files can stay outside Git.

Reference location:

- `deploy/prod/scripts/run_frontend_resource_build.sh`

Reference invocation:

```bash
deploy/prod/scripts/run_frontend_resource_build.sh \
  --resourcepack-host /data/official/front-resourcepack
```

Rules:

- `--resourcepack-host` points to the deployer-managed production equivalent of
  `world/resource/resourcepack`
- intermediate build files live in a compose-managed Docker volume
- final packs output is written to a sibling host directory automatically
  derived by the helper script: `<resourcepack-host>-build-output`
- deploy-time resource builds always use `/packs` as runtime public base path
  and do not rewrite frontend source files in the container image
- if pack catalog membership changes, a normal frontend image rebuild is still
  required because runtime code imports `src/generated/resourcePackCatalog.ts`

The resource-build helper only generates packs artifacts. Atomic publish and
live switching remain the responsibility of `publish_front_release.sh`.

## Smoke Checks

At minimum, the publish step should verify:

- `/` returns `200`
- one hashed asset from `dist/assets` returns `200`

Do not rely on a single homepage check for frontend deploy success.

## Migration Rule

Do not mix the old fixed-root model and the new `live` model.

Migration is complete only when all three are true:

- Docker mounts the stable parent directory
- Caddy reads static files from `/data/front-static/live`
- the publish job writes immutable releases and only switches `live`

Before that point, the system is in compatibility mode and the new atomic script
is only a reference, not the live production source of truth.