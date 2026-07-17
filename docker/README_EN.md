# Docker Deployment Guide

Container deployment files for **NanZi AI Agent Platform**.

## Files

| File | Purpose |
| :--- | :--- |
| `Dockerfile` | API image (Python 3.10-slim), includes frontend + backend build |
| `docker-compose.yml` | Full stack (API + Redis) |
| `docker-compose.ai-agent.yml` | API only; connect to external DB/cache via env |
| `build_linux_x86.sh` | Build for **x86_64** Linux (`linux/amd64`), requires version parameter |
| `build_linux_arm.sh` | Build for **ARM64** Linux (`linux/arm64`), requires version parameter |
| `build_native.sh` | Build for host CPU arch, requires version parameter (local testing only) |
| `install-buildx.sh` | Fix missing/broken `docker buildx` on Homebrew docker + Colima |
| `_build_common.sh` | Internal shared build logic (do not run directly) |
| `start-nanzi-ai-agent.sh` | Check config and start the API container |
| `stop-nanzi-ai-agent.sh` | Stop and remove the API container |
| `.env` | Environment variables (copy from `../env.example`) |

## Quick start

### 1. Configure environment

```bash
cp ../env.example .env
vim .env
```

Use host IP instead of `localhost` for MySQL/Redis from inside the container. On Mac/Windows you can use `host.docker.internal`.

### 2. Build image and export tar

```bash
cd docker

# x86 Linux servers (most common; use on Mac when deploying to x86)
./build_linux_x86.sh 1.2.0

# ARM64 Linux (Kunpeng / Ampere, etc.)
./build_linux_arm.sh 1.2.0

# Local native arch only
./build_native.sh 1.0.0
```

Artifacts are written to **`docker/release/`**, e.g.:

- `docker/release/nanzi-ai-agent_1.2.0_linux-amd64_20260529.tar`
- `docker/release/nanzi-ai-agent_1.2.0_linux-arm64_20260529.tar`

> On **Apple Silicon Macs** targeting **x86 servers**, use `build_linux_x86.sh`, not `build_native.sh`. The first cross-platform build may run for a long time with little console output while pulling amd64 base images.

**If build fails with “docker buildx not available”** (common when `~/.docker/cli-plugins/docker-buildx` points at uninstalled Docker Desktop):

```bash
./install-buildx.sh
./build_linux_x86.sh
```

**If `vite build` fails with `Killed` / `cannot allocate memory`** (common when cross-building x86 on Apple Silicon): the build script pre-builds the frontend on the **host** automatically. Ensure Node.js is installed locally. Alternatively, increase Docker memory (Docker Desktop → Settings → Resources, ≥ 8GB recommended).

Offline deploy on the target host:

```bash
docker load -i docker/release/nanzi-ai-agent_1.2.0_linux-amd64_YYYYMMDD.tar
```

### 3. Start services

```bash
./start-nanzi-ai-agent.sh
./stop-nanzi-ai-agent.sh
```

Or manually:

```bash
docker-compose -f docker-compose.ai-agent.yml up -d
```

### 4. Verify

```bash
docker ps | grep nanzi-ai-agent
docker logs -f nanzi-ai-agent
```

- Admin UI: http://localhost:8001/
- API docs: http://localhost:8001/docs
- Health: http://localhost:8001/health

## Troubleshooting

| Issue | What to try |
| :--- | :--- |
| `docker buildx` missing | Run `./install-buildx.sh` |
| Build seems stuck | Often pulling images / first cross-build; check `docker stats` |
| Container exits immediately | Check `.env` DB/Redis host (not `localhost`) |
| API Key auth fails | Set `ENCRYPTION_KEY` in `.env` |

Chinese version: [README.md](README.md)
