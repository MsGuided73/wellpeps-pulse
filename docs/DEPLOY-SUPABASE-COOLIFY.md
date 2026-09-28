# WellPeps Pulse: Supabase and Coolify setup guide

A step-by-step walkthrough for connecting Pulse to its Supabase database,
running it locally against that database, and deploying it on Coolify.

> **Golden rule:** passwords, keys and connection strings go into `.env` (on
> your machine) or Coolify's environment variables (on the server). Never
> paste them into code, chat, commits or screenshots.

---

## 0. Supabase terms, in plain English

| Term | What it means for Pulse |
|---|---|
| **Organization** | Your Supabase account's billing group. Pulse lives in **SimpleAI4You**. |
| **Project** | One hosted Postgres database plus Supabase's extras. Pulse's project is **`wellpeps-pulse`** (ref `sgzundcnvsmvqdwshcxp`, region us-east-1). |
| **Postgres** | The database engine. Pulse uses SQLite on your laptop for tests and Postgres on Supabase for real use. |
| **Schema** | A folder of tables inside the database. Pulse's tables live in the **`pulse`** schema, not the default `public` one. |
| **Migration** | A SQL file that creates or changes tables. Pulse's are the numbered files in `db/postgres/`: `0001_pulse_schema.sql` and `0002_password_management.sql` are **already applied** (see step 1); apply any newer ones the same way, in order. |
| **Row-Level Security (RLS)** | Per-row access rules. On for every Pulse table, with no access for the public API roles. |
| **anon / publishable key** | A browser-safe key for Supabase's public data API. **Pulse doesn't use it.** The `pulse` schema isn't exposed to that API. |
| **service_role / secret key** | A key that bypasses security on the data API. **Pulse doesn't use it either. Don't put it anywhere.** |
| **Database password** | The password for the database's `postgres` user. This is the one Pulse needs. |
| **Connection string** | One line holding host, user, password and database, e.g. `postgresql://user:password@host:5432/postgres`. Pulse reads it from `PULSE_DATABASE_URL`. |
| **Pooler (Session / Transaction)** | Supabase's connection gateway. **Use "Session pooler".** It works over IPv4, which most Coolify servers need. |

---

## 1. Already done for you

- [x] Created the Supabase project `wellpeps-pulse` (us-east-1, $10/month).
- [x] Applied the schema (`0001` + `0002`): 18 tables in `pulse`, schema version 6.
- [x] Row-level security on for all 18 tables; the `anon` role has no access.
- [x] Supabase security advisor: **0 warnings**.
- [x] App code can run on Postgres (`PULSE_DATABASE_URL`) or SQLite (default).
- [x] Docker/Coolify setup: worker + dashboard services, health checks, proxy-aware login throttle (step 6).

---

## 2. Get the database password and connection string (Supabase dashboard)

The project was created programmatically, so you've never seen its database
password. Set a new one.

1. Go to **supabase.com/dashboard** and open project **wellpeps-pulse**.
2. Left sidebar: **Project Settings** (gear icon), then **Database**.
3. Find **Database password** and click **Reset database password**.
   - Use a long password with **letters and numbers only**. Symbols such as
     `@ : / ? # %` break connection strings unless URL-encoded.
   - Save it in your password manager.
4. Click the **Connect** button at the top of the project page.
5. Choose the **Session pooler** tab (not "Direct connection", and not
   "Transaction pooler").
6. Copy the **URI**. It looks like:
   ```
   postgresql://postgres.sgzundcnvsmvqdwshcxp:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
7. Replace `[YOUR-PASSWORD]` (brackets too) with your new password.
8. Add `?sslmode=require` to the end. This is your final **`PULSE_DATABASE_URL`**.

---

## 3. Recommended: a limited-access login for the app

Connecting as `postgres` works, but `postgres` can do anything, including
bypassing row-level security. Before launch, give the app its own login that
can only use the `pulse` tables.

1. In Supabase, open **SQL Editor** and click **New query**.
2. Paste the query below. Replace `CHOOSE_A_LONG_PASSWORD` with a new
   letters-and-numbers password. Don't reuse the database password.
   ```sql
   create role pulse_login login password 'CHOOSE_A_LONG_PASSWORD' in role pulse_app;
   alter role pulse_login set search_path = pulse;
   ```
3. Click **Run**.
4. In your connection string, change the user part from
   `postgres.sgzundcnvsmvqdwshcxp` to `pulse_login.sgzundcnvsmvqdwshcxp` and
   use the new password.

Even this login can't edit or delete the audit log. It can only add rows.

---

## 4. Test it from your computer (the codebase)

In PowerShell, from `C:\dev\wellpeps-pulse`:

1. Create your local `.env` from the template (skip this if `.env` exists):
   ```powershell
   Copy-Item .env.example .env
   ```
2. Open `.env` in your editor and add this line with your string from step 2
   (or 3):
   ```
   PULSE_DATABASE_URL=postgresql://...your string...?sslmode=require
   ```
   `.env` is git-ignored, so it never gets committed.
3. Check the connection:
   ```powershell
   .venv\Scripts\python -m harvey status
   ```
   You should see mention counts, all 0 on a fresh database. An error names
   the problem; see **Troubleshooting** below.
4. Create your admin login in the Supabase database. It asks for the password
   twice, minimum 12 characters:
   ```powershell
   .venv\Scripts\python -m harvey user add you@wellpeps.com --role admin --name "Your Name"
   ```
5. Optionally, load the fixture posts and open the dashboard:
   ```powershell
   .venv\Scripts\python -m harvey ingest --fixture
   .venv\Scripts\python -m harvey dashboard
   ```
   Open http://127.0.0.1:5555 and sign in.

> **Switching back to the demo:** remove or comment out `PULSE_DATABASE_URL`
> in `.env`, and Pulse uses local SQLite again. The demo script
> (`scripts/seed_demo.py`) only ever writes to SQLite.

> **Running the tests** never touches Supabase. They force SQLite even when
> `.env` has a database URL.

---

## 5. Put the code on GitHub (Coolify deploys from Git)

Coolify pulls the code from a Git repository. The plan is a **private** repo,
`MsGuided73/wellpeps-pulse`. Claude will create it and push once you say go.
After that:

1. In Coolify: **Sources**, then connect GitHub through the **GitHub App**,
   and give it access to `wellpeps-pulse` only.

---

## 6. Code changes for Coolify (done)

The Docker files came from Harvey and assumed a laptop. These changes are
made, tested and committed:

| Change | Why |
|---|---|
| `docker-compose.yml` now has two services from one image: **worker** (`python -m harvey run`) and **dashboard** (`python -m harvey dashboard --host 0.0.0.0 --port 5555`, `expose: 5555`, no published ports) | The web UI runs next to the heartbeat; Coolify's proxy routes the domain to port 5555 |
| All settings come from `environment:` entries (`${VAR}` / `${VAR:-default}`) that Coolify fills from its UI. No `env_file`, no bind mounts of `~/.claude`, `harvey.yaml`, prompts, skills or data | The image carries code, config, prompts and skills; data lives in Supabase |
| New health checks per service: the dashboard answers `GET /healthz` (public, only `{"ok": true}` or 503 `{"ok": false}`); the worker runs `python -m harvey health --worker` | The old check looked at the SQLite file's age, which is meaningless with Supabase |
| The heartbeat writes a liveness timestamp (`heartbeat_at`) every cycle, idle and quiet hours included. `pulse health --worker` fails if it's older than 40 minutes (2 x the 15-minute heartbeat + 10) | Coolify can see a stuck or crashed worker |
| The Claude CLI in the image uses `ANTHROPIC_API_KEY`; a failed CLI install now fails the build | A server has no Claude login to mount |
| The login throttle reads the real visitor IP from `X-Forwarded-For`, but only when the request comes from a trusted proxy (`PULSE_TRUSTED_PROXIES`, preset to the private Docker networks) | Otherwise everyone appears to come from the proxy, and 5 bad logins would lock out the whole team. Visitors can't fake the header to dodge the limit |
| `PULSE_SECURE_COOKIES`, `PULSE_DASHBOARD_URL` and `PULSE_TRUSTED_PROXIES` override `harvey.yaml` | No need to edit files on the server |
| `PULSE_REQUIRE_POSTGRES=true` (preset in the compose file) makes every command stop with a clear error when `PULSE_DATABASE_URL` is missing | Instead of silently writing SQLite inside a container, where it vanishes on the next deploy |
| `.dockerignore` keeps `.env`, `data/`, `.venv`, `.git` and caches out of the image | No secrets or local data in the build |

For a laptop, `docker-compose.local.yml` adds `./data` (SQLite allowed), your
`~/.claude` login, and publishes `127.0.0.1:5555`:
`docker compose -f docker-compose.yml -f docker-compose.local.yml up`, then
open http://localhost:5555.

---

## 7. Coolify setup

### 7a. Create the resource
1. Coolify: **Projects**, then **+ Add**, name it `WellPeps`.
2. Inside it: **+ New Resource**, then **Private Repository (with GitHub App)**,
   then pick `wellpeps-pulse`, branch `main`.
3. Build pack: **Docker Compose** (it reads `docker-compose.yml`).
4. Coolify lists the two services. On the **dashboard** service, set
   **Domains** to your domain **with the container port appended**, e.g.
   `https://pulse.wellpeps.com:5555`. The `:5555` tells Traefik which
   container port to route to; visitors still use plain
   `https://pulse.wellpeps.com`. Coolify issues the HTTPS certificate
   automatically. Point that DNS record at your Coolify server first.
5. The **worker** service gets **no** domain.

### 7b. Environment variables
Open the resource, then the **Environment Variables** tab. Add these as
**runtime** variables (leave "Build variable" off) and mark the secret ones
as locked/secret:

| Variable | Value | Needed? |
|---|---|---|
| `PULSE_DATABASE_URL` | Your Session pooler string from step 2 or 3, ending `?sslmode=require` | **Required.** Secret. |
| `ANTHROPIC_API_KEY` | WellPeps' Anthropic API key (console.anthropic.com, WellPeps organization) | **Required.** Secret. |
| `PULSE_DASHBOARD_URL` | Your dashboard's https address, e.g. `https://pulse.wellpeps.com` (no `:5555`) | **Recommended.** Used for the links in Slack pages and briefs. Must start with `https://`. |
| `PULSE_ADMIN_EMAIL` | Your email | **First deploy only**, then delete |
| `PULSE_ADMIN_PASSWORD` | A 12+ character password | **First deploy only**, then delete. Secret. |
| `SLACK_WEBHOOK_URL` | PepRite Slack incoming-webhook URL | When Slack is ready. Secret. |
| `APIFY_TOKEN` | Apify API token | Phase 9, after legal sign-off. Secret. |
| `META_ACCESS_TOKEN` | Meta Graph token for WellPeps' own IG/FB | Phase 9. Secret. |
| `PULSE_SECURE_COOKIES` | Leave unset (the compose file defaults it to `true`) | No. Never set it to `false` on the server. |
| `PULSE_TRUSTED_PROXIES` | Leave unset (defaults to the private Docker networks `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32`, where Coolify's proxy lives) | No. |

`PULSE_REQUIRE_POSTGRES=true` is already set inside `docker-compose.yml`;
you don't add it.

Do **not** add `PULSE_DB_PATH` or any Supabase anon/service keys. Pulse
doesn't use them.

### 7c. Deploy and first login
1. Click **Deploy** and watch the logs until both services are running.
2. Open your domain and sign in with `PULSE_ADMIN_EMAIL` / `PULSE_ADMIN_PASSWORD`.
3. **Delete** `PULSE_ADMIN_EMAIL` and `PULSE_ADMIN_PASSWORD` from Coolify,
   then redeploy. The admin account stays in the database.
4. In the dashboard: **Users** tab, add teammates with the right roles:

| Role | Can do |
|---|---|
| viewer | Read only |
| reviewer | Edit, approve and reject replies; acknowledge non-clinical escalations |
| clinical | Acknowledge adverse-event escalations (the clinical owner) |
| admin | Everything, including user management |

---

## 8. Before real use (not setup, but required)

- [ ] Compliance/physician sign-off on `config/claims.yaml`. Until then, approving any reply is blocked by design.
- [ ] Name the clinical owner and backup in `harvey.yaml` under `escalation.owners`.
- [ ] Decide rule R38 (medication names in replies) and the minors policy.
- [ ] Check with compliance whether to add Supabase's HIPAA BAA (paid add-on).
- [ ] Phase 9: real collectors (F5Bot/Syften, Apify, Meta).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `password authentication failed` | Wrong password, or it has symbols. Reset it to letters and numbers only (step 2). |
| Connection times out | You used "Direct connection". Use the **Session pooler** string (IPv4). |
| `schema version ... expected 6` | The schema isn't applied to the database you're pointing at. Check the project ref in the URL: `sgzundcnvsmvqdwshcxp`. |
| `Refusing to bind ... secure_cookies` | `PULSE_SECURE_COOKIES` was set to `false` in Coolify. Delete it (the default is `true`) and serve over HTTPS (Coolify domain). |
| `Refusing to bind ...: no active admin user exists` | Set `PULSE_ADMIN_EMAIL` and `PULSE_ADMIN_PASSWORD` for the first deploy (7b), or run `pulse user add` against the database from your computer (step 4). |
| Login says "too many attempts" (429) | Wait 15 minutes. If the whole team is locked out, check that `PULSE_TRUSTED_PROXIES` is unset or still includes the network Coolify's proxy uses (the dashboard logs show nothing about it; the default covers all private Docker ranges). |
| `PULSE_REQUIRE_POSTGRES is on but PULSE_DATABASE_URL is not set` | Add `PULSE_DATABASE_URL` in Coolify (7b), as a **runtime** variable, and redeploy. Both services need it. |
| Dashboard unhealthy / `https://.../healthz` returns 503 `{"ok": false}` | The dashboard can't reach the database or the schema is too old. Its logs say `healthz: database check failed (...)`. Check `PULSE_DATABASE_URL` (Session pooler, `?sslmode=require`, password) and the schema version (row above). Coolify's proxy stops routing to an unhealthy container, so the site shows "no available server". |
| Worker unhealthy | Open a terminal on the worker in Coolify and run `python -m harvey health --worker`. `no heartbeat recorded yet` or `heartbeat is stale` means `pulse run` is stuck or crashing: read the worker logs. A database error means the same fixes as the row above. |
| `PULSE_DASHBOARD_URL: ... must start with https://` | Use the full `https://` address of the dashboard, without `:5555`. |
| `PULSE_SECURE_COOKIES must be true/false` or `PULSE_TRUSTED_PROXIES: ... is not an IP address` | Fix or delete the variable in Coolify. |
| `PULSE_DATABASE_URL must be a postgres URL` | The value must start with `postgresql://` or `postgres://`. |
