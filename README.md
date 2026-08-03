# TaskFlow

A distributed task processing engine — similar in spirit to Amazon SQS — built to demonstrate
asynchronous task processing, priority queuing, retry logic with exponential backoff, dead letter
queues, and observability.

## Architecture

```
                    ┌─────────────┐
   POST /tasks ───► │  FastAPI    │ ───► writes task metadata ───► PostgreSQL
                    │  (api)      │
                    └──────┬──────┘
                           │ pushes task_id
                           ▼
                    ┌─────────────┐
                    │    Redis    │  queue:high / queue:normal / queue:low / queue:dead
                    └──────┬──────┘
                           │ pops task_id
                           ▼
                    ┌─────────────┐
                    │   Worker    │ ───► processes task, updates status in PostgreSQL
                    └─────────────┘
                           │
                     on failure: retries with exponential backoff,
                     after MAX_RETRIES moves task to queue:dead

              Prometheus scrapes /metrics from the API
              Grafana visualizes the Prometheus data
```

## Features

- **Priority queues** — tasks are routed into `high` / `normal` / `low` Redis queues; the worker
  always drains `high` first.
- **Retry with exponential backoff** — a failed task is retried with a doubling delay
  (2s, 4s, 8s...) rather than immediately, so a struggling downstream dependency isn't hammered.
- **Dead Letter Queue (DLQ) + dual-channel alerting** — after `MAX_RETRIES` failed attempts, a
  task is moved to `queue:dead` and marked `FAILED` in Postgres, and an alert fires on **two
  independent channels**: a Slack webhook AND a real SMTP email sent directly by the worker
  (not dependent on Slack's own notification settings). Either channel can be configured alone,
  both together, or neither (falls back to a console log). Admin endpoints let you inspect and
  manually resurrect dead tasks once the root cause is fixed.
- **Stuck-task reaper** — if a worker crashes while a task is mid-processing, that task would
  otherwise be stuck in `PROCESSING` forever. A background thread periodically finds tasks that
  have been processing too long and requeues them.
- **Observability** — Prometheus scrapes request metrics from the API; Grafana is wired up for
  dashboards.
- **Chaos testing script** (`automator.py`) — generates continuous fake traffic with a
  configurable failure rate, useful for watching retries/DLQ behavior live.

## Tech stack

FastAPI · PostgreSQL (SQLAlchemy) · Redis · Docker Compose · Prometheus · Grafana

## Running it

### Option A: Everything in Docker (recommended)

```bash
docker-compose up --build
```

This starts Postgres, Redis, the API, the worker, Prometheus, and Grafana — all wired together.

- API: http://localhost:8001
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (login: admin / admin)

### Option B: Infra in Docker, API/worker run locally

```bash
docker-compose up -d redis postgres prometheus grafana
pip install -r requirements.txt
cp .env.example .env   # uses localhost URLs by default

# terminal 1
uvicorn api.main:app --reload --port 8001

# terminal 2
python worker/main.py
```

### Generate test traffic

```bash
python automator.py
```

## API endpoints

| Method | Path                  | Description                                    |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                    | Health check                                    |
| POST   | `/tasks`               | Submit a new task                               |
| GET    | `/tasks/{id}`          | Look up a single task's status                  |
| GET    | `/tasks/dead`          | List all failed (DLQ) tasks                     |
| POST   | `/tasks/{id}/retry`    | Manually resurrect a failed task                |
| GET    | `/stats/queues`        | Current length of each Redis queue              |
| GET    | `/stats/tasks`         | Count of tasks by status (PENDING/SUCCESS/etc.) |

## Design decisions 

**Why doesn't a burst of high-priority tasks starve low-priority ones?**
The worker doesn't check `queue:high` → `queue:normal` → `queue:low` in strict order every
loop (that would let `high` traffic block `normal`/`low` forever). Instead it cycles through a
weighted round-robin schedule (`high:5, normal:3, low:1` turns) built in `worker/main.py`. Every
queue is guaranteed regular turns, so low-priority tasks always make forward progress, while
high-priority tasks are still serviced more often on average.

**Could two workers ever process the same task twice (double-processing)?**
No. Task IDs are handed out via Redis `RPOP`, which is atomic — Redis processes commands
single-threaded, so if two workers call `RPOP` on the same list at the same moment, Redis
guarantees each of them gets a *different* item (or one gets `nil` if the list only had one
item left). This means the design is safe to scale horizontally by running more worker
containers/replicas without any additional locking.

**What happens if a worker crashes mid-task?**
Before processing, the worker sets `status = PROCESSING` and `started_at = now()`. A background
"reaper" thread checks periodically for tasks stuck in `PROCESSING` longer than
`STUCK_TASK_TIMEOUT_SECONDS` (default 30s) and requeues them — so a crashed worker doesn't leave
orphaned tasks stuck forever.

**Is there authentication on the API?**
No — this is a portfolio/demo project. A real deployment would need auth (API keys or OAuth) on
the task-submission and admin endpoints before going anywhere near production traffic.

**What happens after a task lands in the Dead Letter Queue - does it just get lost?**
No - three things happen, in order: (1) `send_dlq_alert()` fires immediately on both a Slack
webhook (if `ALERT_WEBHOOK_URL` is set) and a real SMTP email (if `SMTP_*` and `ALERT_EMAIL_TO`
are set), plus always logs to the console - so a human is notified in real time through
whichever channel they're likely to see fastest, rather than finding out by accident later.
(2) The task stays visible via `GET /tasks/dead` and `GET /stats/queues` for as long as needed -
it is never silently dropped. (3) Once a human identifies and fixes the root cause (bad input
data, a downstream outage, a bug), they call `POST /tasks/{id}/retry`, which resets the retry
counter and re-queues it for a fresh set of attempts. Nothing retries automatically forever, and
nothing is retried blindly without a human deciding it's safe to - a task that fails due to a
real bug would just fail the same way 3 more times if retried automatically without a fix.

**Why send alerts on two separate channels (Slack AND email)?**
Redundancy - if the Slack webhook is down or misconfigured, the email still goes out and vice
versa. Each channel (`send_slack_alert`, `send_email_alert`) is implemented independently with
its own `try/except`, so a failure in one never prevents the other from firing, and neither can
ever crash the worker or block task processing. Alerting is treated as a "best effort" side
concern layered on top of the core reliability logic, not a dependency the core logic can be
broken by.

**Why does the email use an "App Password" instead of a normal Gmail password?**
Google blocks direct SMTP logins using a regular account password as a security measure (it
would mean the password is stored in plaintext config somewhere). An App Password is a separate,
revocable 16-character credential scoped only to program-based mail sending - if it ever leaked,
you could revoke just that credential without changing your actual Gmail password.

## Tests

```
pytest tests/
```

- `tests/test_backoff.py` — unit tests for the exponential backoff calculation and the
  weighted queue schedule. No dependencies needed, runs anywhere.
- `tests/test_api_integration.py` — integration tests that hit the real running API over HTTP.
  Requires the stack to be running (`docker-compose up`) first; tests are skipped automatically
  (not failed) if the API isn't reachable, so the unit tests can still run standalone.
