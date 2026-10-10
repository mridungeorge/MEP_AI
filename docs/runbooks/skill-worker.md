# The drafting worker (isolated skill builds)

Why it is separate: a drafting skill builds CAD/IFC files from a spec card. The API never runs one itself when deployed. It puts a job in
the `skill_job` queue (Postgres) and waits; the **dispatcher** takes the job and runs the build and the independent re-check as two
separate, throw-away containers:

- non-root user (10001), **no network** (`--network none`), read-only root filesystem, no Linux capabilities, `no-new-privileges`;
- the job's input mounted **read-only**; `/job/out` is the only writable place for the build (and read-only for the re-check); a private 256 MB `/tmp`;
- CPU, memory, process-count, file-size and wall-clock limits.

`tests/skills/test_worker_isolation.py` proves the network and the writes outside `/job/out` fail, using the exact command the dispatcher runs
(CI runs it against the real image). The container holds no credentials; only the dispatcher holds the database URL.

## What you need

A machine with Docker where you can leave a process running: a small VPS (2 vCPU / 4 GB is enough), or your own computer for a demo.
**Railway cannot host it** (a Railway service cannot start sibling containers). Without a worker everything else works; drafting answers
"no drafting worker is running".

## Set up (about 20 minutes)

```
git clone https://github.com/mridungeorge/MEP_AI && cd MEP_AI
docker build -f deploy/worker/Dockerfile -t mep-skill-worker .          # ~10 minutes the first time (the CAD kernel is large)
uv sync --frozen --no-dev
export MEP_DB_URL='<the same Supabase session-pooler string as the API>'
export MEP_WORKER_IMAGE=mep-skill-worker
uv run python -m mep.skills_runner.dispatcher                            # leave running; use systemd or `docker compose` to keep it alive
```

The dispatcher needs access to the Docker socket on that host and nothing else. In Railway set `MEP_SKILL_EXECUTOR=queue` on the API.

## Check it

- API side: `curl` is not needed; open Drafting in the web app, build the plant-room example: a released IFC and DXF appear within seconds.
- Isolation: `MEP_WORKER_IMAGE=mep-skill-worker uv run pytest tests/skills/test_worker_isolation.py -v`.
- A job stuck in `running` for more than 15 minutes is failed by the dispatcher's housekeeping; finished jobs are deleted after an hour.

## Limits

One dispatcher runs one job at a time (start more for more throughput: jobs are claimed with `for update skip locked`). The queue holds the
spec card and the produced files (a few MB at most) until they are collected.
