# Weekly AMFI Dashboard — Deployment Runbook

Concise operational checklist for deploying the backend API (FastAPI + SQLite)
and the static frontend (Vite/React). Read this top to bottom before a
production rollout.

> **Architecture constraint up front:** the backend stores all data in a single
> **SQLite** file. It must run as a **single process / single worker on local
> persistent disk**. See [SQLite operational rules](#5-sqlite-operational-rules).

---

## 1. Required environment variables

| Variable | Dev default | Production | Notes |
|---|---|---|---|
| `ENVIRONMENT` | `development` | **`production`** | Enables all production guards. |
| `ALLOWED_ORIGINS` | _(empty; localhost auto-allowed)_ | **required** | Comma-separated. Each entry **must** include `http://` or `https://`. `*` is **rejected** in production. |
| `TRUSTED_HOSTS` | _(empty; all hosts allowed)_ | **required** | Comma-separated Host header allow-list. `*` is **rejected** in production. |
| `WEEKLY_DB_PATH` (or `DB_PATH`) | `backend/weekly_amfi.db` | **required** | Absolute path on persistent local disk, **outside** the app/source tree. |
| `REQUIRE_PROXY_IDENTITY` | `false` | recommended `true` | When true (production only), requests without the identity header are rejected `401`. |
| `IDENTITY_HEADER` | `X-Forwarded-User` | as configured at proxy | Header the trusted proxy injects with the authenticated user. |
| `ENABLE_DOCS` | `true` | `false` (default in prod) | `/docs`, `/redoc`, `/openapi.json`. Leave **off** in production. |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MiB) | as needed | Upload size cap; oversized uploads get `413`. |
| `LOG_LEVEL` | `INFO` | `INFO` | Structured access/audit logs at INFO. |

The app **fails closed at startup** (`Settings.validate()`): if `ENVIRONMENT=production`
and any required value is missing/unsafe, `create_app()` raises and the process
will not start.

Example production env:

```bash
export ENVIRONMENT=production
export ALLOWED_ORIGINS="https://dashboard.company.internal"
export TRUSTED_HOSTS="dashboard.company.internal"
export WEEKLY_DB_PATH="/var/lib/weekly-amfi/weekly_amfi.db"
export REQUIRE_PROXY_IDENTITY=true
export IDENTITY_HEADER="X-Forwarded-User"
export ENABLE_DOCS=false
```

---

## 2. Production CORS / host settings

- `ALLOWED_ORIGINS` must be the **explicit** dashboard origin(s), with scheme.
  Wildcard `*` and scheme-less values are rejected in production.
- `TRUSTED_HOSTS` enforces the `Host` header (`TrustedHostMiddleware`).
  A request with an unexpected `Host` gets `400 Invalid host header`.
- Local development needs no CORS/host config: `localhost`/`127.0.0.1` origins
  are auto-allowed and all hosts pass.

---

## 3. Proxy identity / SSO assumptions

This service does **not** implement login/SSO screens. In production it expects
to sit **behind a trusted company reverse proxy / SSO gateway** that:

1. Authenticates the user (SSO/OIDC/SAML/etc.), and
2. Injects the authenticated username into the configured `IDENTITY_HEADER`
   (default `X-Forwarded-User`).

When `REQUIRE_PROXY_IDENTITY=true`, any request missing that header (other than
`OPTIONS` preflight and the exempt paths `/`, `/healthz`, `/readyz`) is rejected
with `401`.

> ### ⚠️ CRITICAL: the proxy MUST strip client-supplied identity headers
> The app **trusts the identity header blindly.** The reverse proxy is the only
> trust boundary. It **must remove/overwrite any client-supplied copy** of
> `X-Forwarded-User` (and any configured `IDENTITY_HEADER`) on inbound requests
> **before** injecting the real authenticated identity. If it does not, a client
> can spoof any identity by sending the header themselves.
> Do **not** expose this service directly to clients with identity enforcement on.

---

## 4. Identity / audit logging

- Every request emits a structured JSON **access** log: method, path, status,
  duration, `request_id`, and identity (if present).
- Sensitive actions emit a structured **audit** log: `upload`, `dashboard.read`,
  `archive.read`, `download.summary`, `download.mom` — with action, outcome,
  `request_id`, identity, and (for uploads) the sanitized filename.
- **Workbook contents are never logged** — only metadata (filename, FY, period).
- Each response carries an `X-Request-ID` header for correlation; an inbound
  `X-Request-ID` is honored if provided.

---

## 5. SQLite operational rules

- **Single worker only.** Run exactly one process. Do **not** use
  `gunicorn -w N` or `uvicorn --workers N` (>1). Multiple writers contend on the
  single SQLite write lock; under concurrent uploads the loser raises
  `SQLITE_BUSY` after a 10s timeout → sanitized `500`. (No data corruption, but
  degraded.)
- **Local disk only.** The DB must live on **persistent local disk**. Never place
  `WEEKLY_DB_PATH` on a networked filesystem (NFS/SMB) — SQLite locking is unsafe
  there.
- **Outside the source tree.** Keep the DB at e.g. `/var/lib/weekly-amfi/` so
  redeploys never overwrite it and it is never accidentally committed.
- Writes use `BEGIN IMMEDIATE` + rollback and are atomic. **Same-month
  replacement** is preserved: re-uploading a period in an existing FY+month
  replaces that month's rows.
- To scale beyond one process (HA/multi-node), migrate to a central DB
  (PostgreSQL) — out of scope for this phase.

---

## 6. Persistent DB path, backup & restore

```bash
# Backup (online-safe with WAL): use the SQLite backup API / .backup
sqlite3 "$WEEKLY_DB_PATH" ".backup '/backups/weekly_amfi-$(date +%F).db'"

# Restore (stop the service first)
cp /backups/weekly_amfi-YYYY-MM-DD.db "$WEEKLY_DB_PATH"
# then restart the single worker
```

- Back up `WEEKLY_DB_PATH` on a schedule (e.g. daily) and before each deploy.
- WAL sidecar files (`*.db-wal`, `*.db-shm`) are transient; the `.backup`
  command produces a consistent single-file snapshot.

---

## 7. Frontend build & the dev-only npm audit exception

```bash
npm ci
npm run build      # outputs to dist/ (gitignored)
```

- `npm audit` reports **3 high** findings in the **dev/build** chain
  (`esbuild` → `vite` → `@vitejs/plugin-react`). These are **build-time only**
  and are **not** shipped in the runtime bundle.
- **`npm audit --omit=dev` is clean (0 vulnerabilities).** This is the accepted
  compliance posture for now; the Vite 6→8 upgrade that clears the dev advisory
  is tracked as a separate maintenance task.

---

## 8. Health / readiness endpoints

| Endpoint | Purpose | Healthy | Unhealthy |
|---|---|---|---|
| `GET /healthz` | Liveness (process up) | `200 {"status":"ok"}` | n/a |
| `GET /readyz` | Readiness (DB reachable + prod config present) | `200 {"status":"ready", ...}` | `503 {"status":"not ready", ...}` |

- Point your load balancer liveness probe at `/healthz`, readiness probe at
  `/readyz`. Both are exempt from identity enforcement.
- `/readyz` fails (`503`) if the DB is unreachable, or in production if
  `db_path` / `allowed_origins` / `trusted_hosts` are missing.

---

## 9. Smoke test commands

Backend (against a **temp DB**, never the real one):

```bash
cd backend
# Liveness / readiness
curl -fsS localhost:8000/healthz
curl -fsS localhost:8000/readyz
# Identity enforcement (production + REQUIRE_PROXY_IDENTITY=true)
curl -i  localhost:8000/dashboard-data                         # expect 401
curl -fsS localhost:8000/dashboard-data -H 'X-Forwarded-User: smoke'  # expect 200
# Host validation
curl -i  localhost:8000/healthz -H 'Host: evil.example'        # expect 400
```

Full automated suite (uses temporary SQLite DBs only):

```bash
cd backend
python -m unittest test_weekly_engine test_api_hardening test_phase2_hardening -v
```

---

## 10. Security / dependency tooling

```bash
# Runtime deps are fully pinned in backend/requirements.txt
python -m pip install -r backend/requirements.txt
python -m pip check                                  # dependency consistency

# Dev/security tooling (not in the runtime image)
python -m pip install -r backend/requirements-dev.txt
pip-audit -r backend/requirements.txt                # runtime CVE scan
bandit -r backend -x backend/test_api_hardening.py,backend/test_phase2_hardening.py
```

---

## 11. Rollback steps

1. **Stop** the single backend worker.
2. **Restore** the previous app version (redeploy prior build / git revert).
3. **Restore** the DB only if the rollback requires it (see §6); otherwise leave
   `WEEKLY_DB_PATH` intact — it is outside the source tree and unaffected by an
   app redeploy.
4. **Restart** the single worker.
5. **Verify**: `GET /healthz` → 200, `GET /readyz` → 200 (`ready`), and one
   authenticated `GET /dashboard-data` → 200.
6. Frontend rollback: redeploy the previous `dist/` artifact.
