# Deploy runbook: Supabase (Sydney) + Railway (API) + Vercel (web)

Nothing secret is in git. Every key below is created by you in a vendor dashboard and pasted into that vendor's settings (or, for the
service scripts, into a local `.env`). Templates: `deploy/env.api.example` (API and scripts) and `deploy/env.web.example` (web).
The dotted `.env.example` name is blocked by this project's permission rules, so copy the templates: `cp deploy/env.api.example .env`.

Time: about 90 minutes the first time. Cost: Supabase Free is enough for a demo; use Pro (about US$25/month) for a pilot with real
firms (daily backups, no pausing, custom SMTP limits). Railway Hobby (about US$5/month plus usage). Vercel Hobby is free.

## What the pieces do

| Piece | Where | Why there |
|---|---|---|
| Postgres + Auth + Storage | Supabase, region **Southeast Asia (Sydney) / ap-southeast-2** | Australian data residency; row-level security is the safety net |
| API (FastAPI, rule engine) | Railway, built from `deploy/api/Dockerfile` | holds the service connection; the browser never gets it |
| Web (Next.js) | Vercel, root `apps/web` | proxies `/share-api/*` to the API so the certifier's cookie is first-party |

## Step 0. Accounts you create by hand

1. **Supabase** (supabase.com): sign up, create an organisation.
2. **Railway** (railway.com): sign up with GitHub; allow it to read the `mridungeorge/MEP_AI` repository.
3. **Vercel** (vercel.com): sign up with GitHub; allow the same repository.
4. **An e-mail sender for sign-in links.** Supabase's built-in mailer only sends a few e-mails an hour and only to your own team, which
   breaks magic-link sign-in for engineers. Use **Resend** (resend.com, free tier) or any SMTP provider: create an account, verify
   a sending domain you own (DNS records), create an SMTP user. Keep host, port, user, password.
5. **A password manager** entry for each key below.

## Step 1. Supabase project

1. New project: name `mep-copilot`, **Region: Southeast Asia (Sydney)**, a strong database password (save it).
2. Project Settings > API: copy **Project URL**, the **anon (public) key**, and keep the **service_role key** secret (master key).
3. Project Settings > Database > Connection string > **Session pooler**: copy it, put your database password in. This is `MEP_DB_URL`
   (port 5432, host `aws-0-ap-southeast-2.pooler.supabase.com`). Do not use the transaction pooler (6543); do not use the direct string
   (IPv6 only from Railway).
4. Authentication > Sign In / Providers: **disable "Allow new users to sign up"** (people are invited by the seed/admin scripts only).
   Email provider stays on; "Confirm email" can stay on.
5. Authentication > SMTP Settings: enable custom SMTP with your Resend/SMTP details and a From address on your verified domain.
6. Authentication > URL Configuration: set **Site URL** to your Vercel production URL (you get it in Step 4; come back and set it) and add
   `https://<your-app>.vercel.app/**` to Redirect URLs.
7. Leave "JWT signing keys" on the default (asymmetric): the API verifies tokens against the project's JWKS.

## Step 2. Database migrations (once, then on every release)

Follow `docs/runbooks/migrate.md`. First time:

```
npm i -g supabase                      # or: npx supabase ...
supabase login                         # opens a browser; creates an access token on this machine
supabase link --project-ref <project-ref>      # the ref is the part of the Project URL before .supabase.co
supabase db push --dry-run             # lists 0001 ... 0015: nothing else should appear
supabase db push                       # applies them
```

Then check in the dashboard: Storage has a private bucket **uploads**; Table Editor shows the tables; Authentication > Users is empty.

## Step 3. Seed the demo firm and the three users

On your machine (needs `uv` and a checkout; the devcontainer has both):

```
cp deploy/env.api.example .env         # fill MEP_DB_URL, MEP_JWT_SECRET (openssl rand -hex 32), MEP_SUPABASE_URL, MEP_SUPABASE_SERVICE_KEY
set -a; . ./.env; set +a
uv run python scripts/seed_demo.py \
  --designer you+designer@yourdomain --checker you+checker@yourdomain --approver you+approver@yourdomain
```

Three different addresses you can read (plus-addressing works with most providers). One person alone: add `--mode small_firm`
(every package then says NOT INDEPENDENTLY CHECKED). Afterwards **delete `MEP_SUPABASE_SERVICE_KEY` from `.env`**.
The approver gets the placeholder registration `DEMO-0001`. It is not a real registration. For a real approver follow
`docs/runbooks/register-approver.md`.

## Step 4. API on Railway

1. New Project > Deploy from GitHub repo > `MEP_AI`. Railway reads `railway.json` (Dockerfile build, health check `/healthz`).
2. Service > Variables: add exactly these (values from Steps 1 and 3; never paste the service_role key here):
   `MEP_DB_URL`, `MEP_JWT_SECRET`, `MEP_SUPABASE_URL`, `MEP_SUPABASE_ANON_KEY`, `MEP_CORS_ORIGINS` (set after Step 5), `MEP_SKILL_EXECUTOR=queue`, and
   `ANTHROPIC_API_KEY` (for the drafting assistant and for reading PDF drawings; the app says so if it is missing). Do not set `MEP_COOKIE_SECURE` (the cookie is Secure unless it is `0`).
3. Service > Settings > Networking > **Generate Domain**. Note `https://<name>.up.railway.app`.
4. Check: `curl https://<name>.up.railway.app/healthz` returns `{"status":"ok"}`; `curl -i https://<name>.up.railway.app/me` returns 401.

### Step 4b. The drafting worker (optional, needs a Docker host)

Drafting builds run in an isolated container started by a small dispatcher, not on Railway: follow `docs/runbooks/skill-worker.md`.
Skip it for a first look: everything except Drafting works without it.

## Step 5. Web on Vercel

1. Add New > Project > import `MEP_AI`. **Root Directory: `apps/web`.** Framework: Next.js.
2. Settings > General: Install Command `cd ../.. && pnpm install --frozen-lockfile`; Build Command `pnpm exec next build`;
   enable "Include source files outside of the Root Directory".
3. Environment Variables (Production): `NEXT_PUBLIC_API_URL` (the Railway URL, no trailing slash),
   `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Deploy.
4. Back in Railway set `MEP_CORS_ORIGINS=https://<your-app>.vercel.app` and redeploy the API. Back in Supabase set the Site URL and the
   redirect URL to the Vercel address (Step 1.6).

## Step 6. Smoke test (10 minutes)

Automatic first (about 1 minute). The script confirms everything it is shown and, with the checker and approver tokens, FREEZES and SIGNS the
revision, so it only works on a throwaway project: create one separately from the demo project (do not run it on the demo revision):

```
uv run python scripts/seed_demo.py --smoke --designer you+designer@yourdomain --checker you+checker@yourdomain --approver you+approver@yourdomain
```

It prints the throwaway revision id. You need a signed-in access token for each role (browser dev tools > Application > local storage > the Supabase
`access_token` after a magic-link sign-in). Put them in environment variables, not on the command line (history and `ps` would keep them):

```
export SMOKE_DESIGNER_TOKEN=... SMOKE_CHECKER_TOKEN=... SMOKE_APPROVER_TOKEN=... SMOKE_REGISTRATION=DEMO-0001
uv run python scripts/staging_smoke.py --api https://<name>.up.railway.app --revision <the throwaway revision id>
```

It signs in, uploads the fixture IFC, does Gate 1, runs the rules and checks the report, signs Gates 2 and 3 and makes, uses and revokes a share
link (with only SMOKE_DESIGNER_TOKEN it stops after the report). It refuses a revision whose project is not the SMOKE TEST one. Then the manual steps:


1. Open the Vercel URL; you are sent to Sign in. Enter the designer address; the link arrives; the header shows `role: designer`.
2. Revisions lists "1 Demo Street, Melbourne VIC (synthetic office)". Open Gate 1; upload `docs/demo-assets/office-rev-a.dxf`; the ingest
   health score shows.
3. Run the whole of `docs/demo-script.md` once. The last step proves the certifier link: open it in a private window with no sign-in.
4. `curl -i https://<railway>/ledger/verify` returns 401 (needs sign-in); signed in, the web header's package view shows
   "Audit ledger hash chain verified".

## Operating notes

- **Rollback**: Railway > Deployments > redeploy the previous one; Vercel > Deployments > Promote. Database changes roll forward only
  (a new migration), see `migrate.md`.
- **Backups**: Free has none; Pro has daily backups. Before any migration on real data take one (Dashboard > Database > Backups, or
  `pg_dump` with the direct connection from a machine that has IPv6).
- **Keys**: if the service_role key or the database password was ever pasted anywhere public, rotate it in Supabase immediately
  (Settings > API / Database) and update Railway.
- **Real data**: do not put a real firm's project in until the firm has agreed to it in writing. The DRAFT banner stays on every report
  while the rules are drafts; an engineer approves rules in the repository, never in the app.
- **Cookies/CORS**: the certifier page uses `/share-api/*` on the Vercel domain, so no third-party cookie is involved. If you put the API
  on a custom domain later, nothing about the share link changes.
