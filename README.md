# Universal Downloader

SaaS-managed Windows client for downloading publicly available media from creator sources
(TikTok / Instagram usernames or profile URLs, YouTube channel handles / URLs).

- **Backend (control plane):** Python 3.11+ / FastAPI — identity, subscriptions, sources,
  discovery orchestration, batch scheduling, job queue, provider adapters, admin API.
- **Windows client (data plane):** C# / .NET 8 / WPF — login, dashboard, source management,
  network-aware download worker, local SQLite cache.
- **Deploy:** Docker Compose (PostgreSQL + Redis + API + workers + scheduler + Caddy).

Spec: `docs/Universal_Downloader_SRS.docx` (v1.1) and `docs/Universal_Downloader_SDS.docx` (v1.1).

## Layout

```
server/        FastAPI backend (app/, migrations/, tests/, Dockerfile)
client/        WPF desktop client (UniversalDownloader.sln)
admin-web/     Admin web panel (phase 2)
deploy/        docker-compose.yml, reverse proxy, monitoring
docs/          SRS v1.1, SDS v1.1
```

## Quickstart (backend)

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set DATABASE_URL, JWT secrets
alembic upgrade head
python -m app.seed --username admin --password <pw>   # create super admin
uvicorn app.main:app --reload
```

Tests (SQLite, no external services needed): `pytest` from `server/`.

## Compliance

Operates only against content and access methods permitted by the relevant platform,
content owner, and applicable law. No DRM / CAPTCHA / paywall / rate-limit circumvention
(SRS §2.2, SDS §19). Provider adapters must honor platform quotas and cooldowns.
