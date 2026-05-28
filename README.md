# FinSight

FinSight is an official-source, citation-first financial research copilot for Indian
listed companies (MVP document types listed below).

## MVP scope (frozen)

**In scope (official sources only):**

- Annual reports
- Financial results / quarterly results
- Investor presentations

**Out of scope for MVP:** concall/earnings-call transcripts, DRHP/RHP, arbitrary filings,
user uploads, investment advice (buy/sell/hold), ratings/scores, and target prices.

## Local setup (Python 3.12.13, uv preferred)

1. Copy `.env.example` to `.env` (Profile B host URLs are active by default).
2. From this directory (`FinSight/`), pin Python and install dependencies:

```powershell
uv python pin 3.12.13
uv sync --dev
```

3. Start backing services (Mongo is required for auth):

```powershell
docker compose up -d mongodb redis qdrant
```

4. Run API:

```powershell
uv run uvicorn app.main:app --reload
```

5. Open health endpoint: `http://127.0.0.1:8000/health/live`

6. Verify readiness (all three dependencies must be `true`):

```powershell
curl http://127.0.0.1:8000/health/ready
```

If a dependency is down, the app still starts but logs warnings at startup and returns HTTP 503 from `/health/ready` with a `dependencies` map.

## Runtime profiles (dependency URLs)

Pick **one** profile in `.env`. A fresh copy from `.env.example` uses **Profile B** (host).

| Profile | When to use | MongoDB | Redis | Qdrant |
|---------|-------------|---------|-------|--------|
| **B — Host** (default) | PyCharm, `uv run uvicorn` on your machine | `mongodb://127.0.0.1:27017` | `redis://127.0.0.1:6379/0` | `http://127.0.0.1:6333` |
| **A — Compose** | `docker compose` `web` / `worker` services | `mongodb://mongodb:27017` | `redis://redis:6379/0` | `http://qdrant:6333` |

- **Profile B:** Docker Desktop publishes container ports to `127.0.0.1` on the host. Leave `QDRANT__API_KEY` empty for local Qdrant.
- **Profile A:** Comment Profile B lines and uncomment Profile A in `.env` before `docker compose up web`.

If you already have a `.env` with compose service names (`mongodb`, `redis`, `qdrant`) but run Uvicorn on the host, switch to Profile B manually — the app will warn at startup and `/health/ready` will stay degraded until URLs match your runtime.

### `RUNTIME__PROFILE` setting

| Value | Meaning |
|-------|---------|
| `host` | App runs on your machine; dependencies use `127.0.0.1` / `localhost` |
| `compose` | App runs inside `docker compose` `web`/`worker`; dependencies use service names |
| `auto` | Infer profile from configured dependency URLs (default in code if unset) |

Set `RUNTIME__STRICT_PROFILE_VALIDATION=true` to fail fast on mixed host/compose URLs or declared/detected mismatch. In `local`/`development`, inconsistent profiles log warnings at startup when strict mode is off.

Startup log events (safe targets only, no secrets):

- `runtime_profile_detected` — before dependency probes
- `runtime_profile_ready` — all dependencies reachable
- `runtime_profile_degraded` — one or more dependencies down

`/health/live` and `/health/ready` include `runtime_profile`, `runtime_profile_declared`, and `runtime_profile_detected`.

At `LOGGING__LEVEL=INFO`, `httpx` wire logs are suppressed (set `DEBUG` for verbose HTTP traces).

## Why FinSight does not use config.json for environment settings

- **`.env`** holds environment-specific values and secrets (DB URLs, API keys, session secret).
- **`pydantic-settings`** loads and validates settings from `.env` in [`app/core/config.py`](app/core/config.py).
- **`config.json` must not store secrets** — no passwords, tokens, JWT secrets, cookies, or production credentials in JSON config files.
- **Non-secret policy files** (static product policy or feature flags) may be added later as optional JSON/YAML; they will not replace `.env` for runtime connectivity or credentials.

### Optional pip fallback

If you do not use uv:

```powershell
python -m venv .venv
# activate .venv, then:
pip install -e .[dev]
```

Optional snapshot list (non-authoritative): `pip install -r ../docs/requirements-python312.txt`

## Run tests

```powershell
uv run pytest
```

## Auth routes (T-004 foundation)

- Register page: `GET /auth/register`
- Login page: `GET /auth/login`
- Logout action: `POST /auth/logout`
- Protected dashboard: `GET /dashboard`

## Docker compose skeleton

- `docker compose up --build`
- `web` and `worker` use `python:3.12-slim`, aligned with `requires-python` and local **uv** on **3.12.13** (compose alignment delivered in T-008; T-006 was not a separate implementation).
- When running the API inside Compose, switch `.env` to **Profile A** (service hostnames).

## Dependency source of truth

- `pyproject.toml` is the only authoritative dependency definition.
- `requirements.txt` is a compatibility shim and is not maintained as source of truth.
- Snapshot export: `../docs/requirements-python312.txt` (regenerate with `uv export` after dependency changes).
