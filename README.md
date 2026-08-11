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


