# TaskFlow

A distributed task processing engine built with FastAPI, PostgreSQL, and Redis. Clients submit tasks over HTTP; a separate worker process pulls them off priority queues, executes them, and handles retries, backoff, stuck-task recovery, and dead-letter alerting automatically.

![Python](https://img.shields.io/badge/Python-FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Task%20History-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Priority%20Queues-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-Metrics-E6522C?logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-Dashboards-F46800?logo=grafana&logoColor=white)

## Table of Contents

- [Overview](#overview)
- [Flow](#flow)
- [Key Mechanisms](#key-mechanisms)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [API Reference](#api-reference)
- [Chaos Testing](#chaos-testing)
- [Running Tests](#running-tests)
- [Design Notes](#design-notes)

## Overview

TaskFlow splits task processing into two independent pieces:

- **API** (`api/`) — accepts a task over HTTP, persists it in PostgreSQL, and pushes its ID onto the appropriate Redis priority queue.
- **Worker** (`worker/`) — a standalone process that pulls task IDs off those queues, executes them, and manages the full failure-handling lifecycle: retries with exponential backoff, a dead-letter queue for tasks that fail permanently, and a reaper that recovers tasks left stuck if a worker crashes mid-execution.

The two communicate only through PostgreSQL (task state) and Redis (queues) — the API never talks to the worker directly, so either side can be scaled or restarted independently.

## Flow

```
Client
  │  POST /tasks  {type, priority, data}
  ▼
FastAPI (api/main.py)
  │
  ├─► Save task to PostgreSQL (status: PENDING)
  │
  └─► Push task ID to Redis   →  queue:high / queue:normal / queue:low
                                        │
                                        ▼
                        Worker (worker/main.py) — weighted round-robin
                        picks a queue slot, RPOPs a task ID
                                        │
                        status → PROCESSING, started_at = now
                                        │
                              ┌─────────┴─────────┐
                              ▼                   ▼
                          Succeeds             Raises exception
                              │                   │
                    status → SUCCESS      retry_count += 1
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                                ▼
                        retry_count < MAX_RETRIES          retry_count >= MAX_RETRIES
                                    │                                │
                     status → PENDING, schedule via         status → FAILED
                     exponential backoff (queue:delayed)     pushed to queue:dead
                                    │                        Slack + email alert fired
                                    ▼
                        promote_delayed_tasks() moves it
                        back into its priority queue once
                        the backoff delay has elapsed

Meanwhile, in a background thread:
reap_stuck_tasks() — every 15s, finds tasks stuck in PROCESSING
for >30s (worker crashed mid-task) and requeues them as PENDING.
```

## Key Mechanisms

- **Weighted round-robin scheduling** — the worker doesn't simply drain `queue:high` before ever touching `queue:normal` or `queue:low` (which would starve them completely). Instead it builds a fixed schedule (`high:normal:low = 5:3:1`) so high-priority tasks get more turns on average, but every queue is guaranteed forward progress.
- **Exponential backoff retries** — a failed task's retry delay doubles each attempt (2s → 4s → 8s), computed in `compute_backoff_delay()`. Rather than blocking the worker with `time.sleep(delay)`, the task is parked in a Redis sorted set (`queue:delayed`) with its "available again" timestamp as the score, and a separate background thread (`promote_delayed_tasks`) moves it back into its real queue once that time passes — so the main loop never stalls.
- **Dead-letter queue (DLQ)** — after `MAX_RETRIES` (3) failed attempts, a task is marked `FAILED`, pushed to `queue:dead`, and an alert fires on two independent channels — a Slack/Discord webhook and direct SMTP email — each wrapped in its own try/except so one failing doesn't block the other, and a console log always happens as a last-resort trail.
- **Stuck-task reaper** — if a worker crashes or is killed while a task is `PROCESSING`, nothing would normally ever move it out of that state. A background thread checks every 15 seconds for tasks that have been `PROCESSING` for longer than 30 seconds and puts them back in their queue as `PENDING`.
- **Graceful degradation on infra failure** — if PostgreSQL is unreachable when a task is submitted, the API returns a clean `503` instead of a raw 500. If Postgres succeeds but Redis is down right after, the task already exists but will never be picked up — the API surfaces this explicitly in its error response rather than pretending the request succeeded (a documented gap: a production fix would add a periodic reconciliation job to re-queue orphaned `PENDING` tasks).
- **Metrics** — `prometheus-fastapi-instrumentator` exposes request metrics automatically; Prometheus scrapes them and Grafana visualizes them, both wired up via Docker Compose.

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI + Uvicorn |
| Task Persistence | PostgreSQL + SQLAlchemy |
| Queue / Broker | Redis (priority lists + a sorted set for delayed retries) |
| Worker | Standalone Python process, threaded (reaper + delay-promoter run alongside the main loop) |
| Alerting | Slack/Discord webhook + SMTP email |
| Metrics | Prometheus + Grafana |
| Containerization | Docker Compose (api, worker, postgres, redis, prometheus, grafana) |
| Testing | Pytest (unit tests for pure logic + HTTP integration tests) |

## Project Structure

```
Taskflow/
├── api/
│   ├── main.py           # FastAPI app: task submission, lookup, DLQ admin, stats endpoints
│   ├── models.py         # SQLAlchemy Task model
│   ├── schemas.py        # Pydantic request schema (TaskCreate)
│   ├── database.py       # SQLAlchemy engine/session setup
│   ├── config.py         # Env-driven settings (DB, Redis, webhook, SMTP)
│   └── queue_manager.py  # Pushes task IDs onto the correct Redis priority queue
├── worker/
│   └── main.py           # Weighted scheduler, retry/backoff, reaper, DLQ alerting
├── tests/
│   ├── test_backoff.py           # Unit tests: backoff math, queue schedule weighting
│   └── test_api_integration.py   # HTTP integration tests against a running stack
├── automator.py           # Chaos-testing script: fires random tasks with a 20% simulated failure rate
├── Dockerfile.api
├── Dockerfile.worker
├── docker-compose.yml      # api, worker, postgres, redis, prometheus, grafana
├── prometheus.yml
└── requirements.txt
```

## Getting Started

### Run the full stack with Docker Compose

```bash
git clone https://github.com/Gunjan-gupta-205/Taskflow_New.git
cd Taskflow_New
docker-compose up --build
```

This starts Postgres, Redis, the API, the worker, Prometheus, and Grafana together.

| Service | URL |
|---|---|
| API | http://localhost:8001 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (default login: `admin` / `admin`) |

### Environment Variables

Set these in a `.env` file (used by both the API and worker):

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `ALERT_WEBHOOK_URL` | No | Slack/Discord webhook for DLQ alerts |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` | No | SMTP config for DLQ email alerts |

If neither webhook nor SMTP is configured, DLQ alerts are still printed to the worker's console.

## API Reference

**Submit a task:**
```bash
curl -X POST http://localhost:8001/tasks \
  -H "Content-Type: application/json" \
  -d '{"type": "WELCOME_EMAIL", "priority": "HIGH", "data": {"user_id": 42}}'
```
```json
{
  "message": "Task successfully recorded and queued",
  "task_id": "a1b2c3d4-...",
  "status": "PENDING",
  "queue": "queue:high"
}
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Health check |
| `/tasks` | POST | Submit a new task |
| `/tasks/{task_id}` | GET | Look up a task's current status, retry count, timestamps |
| `/tasks/dead` | GET | List all tasks that permanently failed (DLQ) |
| `/tasks/{task_id}/retry` | POST | Manually resurrect a `FAILED` task back into `PENDING` and re-queue it |
| `/stats/queues` | GET | Current length of each Redis queue (high/normal/low/dead) |
| `/stats/tasks` | GET | Count of tasks by status (PENDING/PROCESSING/SUCCESS/FAILED) |

## Chaos Testing

`automator.py` fires a continuous stream of random tasks at the API, with a 20% chance per task of setting `force_fail: true` — a flag the worker checks and deliberately raises an exception for. This is used to exercise the retry/backoff/DLQ/alerting path under sustained load rather than relying on real failures showing up naturally:

```bash
python automator.py
```

## Running Tests

```bash
# Unit tests — no infra required, test the backoff math and queue scheduling logic directly
pytest tests/test_backoff.py

# Integration tests — require the full stack running (docker-compose up)
pytest tests/test_api_integration.py
```

Integration tests skip themselves (rather than failing) if the API isn't reachable at `localhost:8001`, so a CI run without the stack up doesn't report false failures.

## Design Notes

- **Two-phase task acceptance (DB write, then Redis push)** creates a narrow but real gap: if Redis is unreachable right after the Postgres write succeeds, the task exists but nothing will ever pick it up. The API returns an explicit error rather than a false "success," and this is documented as a known limitation — the production-grade fix would be a periodic reconciliation job (outbox pattern) that finds orphaned `PENDING` tasks and re-queues them.
- **Weighted round-robin over strict priority queues** was a deliberate choice to prevent starvation: a strict "always drain high first" scheduler would let a steady stream of high-priority tasks block low-priority ones indefinitely.
- **Non-blocking backoff via a delayed queue + promoter thread**, instead of `time.sleep()` inside the worker loop, so a single slow retry doesn't stall the whole worker from picking up other tasks.
