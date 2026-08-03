import time
import sys
import os
import threading
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from api.database import SessionLocal
from api.models import Task
from api.config import settings

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# --- Weighted round-robin schedule (fixes priority starvation) ---
# A naive worker that always checks queue:high first, then queue:normal, then queue:low
# will starve low-priority tasks completely as long as high-priority tasks keep arriving,
# since queue:normal and queue:low are never even reached.
#
# Instead, we build a fixed schedule that visits "high" more often than "normal", and
# "normal" more often than "low" - but every queue gets guaranteed turns. This is a classic
# weighted round-robin: high-priority tasks are still processed faster on average, but
# low-priority tasks are guaranteed forward progress instead of being blocked forever.
QUEUE_WEIGHTS = {"high": 5, "normal": 3, "low": 1}
QUEUE_SCHEDULE = []
for priority, weight in QUEUE_WEIGHTS.items():
    QUEUE_SCHEDULE.extend([f"queue:{priority}"] * weight)
# QUEUE_SCHEDULE now looks like:
# ['queue:high', 'queue:high', 'queue:high', 'queue:high', 'queue:high',
#  'queue:normal', 'queue:normal', 'queue:normal', 'queue:low']

MAX_RETRIES = 3          # Max attempts before a task goes to the Dead Letter Queue
BASE_BACKOFF_SECONDS = 2  # Base delay used for exponential backoff
STUCK_TASK_TIMEOUT_SECONDS = 30  # If a task sits in PROCESSING longer than this, assume the worker crashed
REAPER_INTERVAL_SECONDS = 15     # How often the reaper checks for stuck tasks

DELAYED_QUEUE_KEY = "queue:delayed"  # Redis sorted set holding tasks waiting out their backoff delay
DELAY_CHECK_INTERVAL_SECONDS = 1     # How often we check for delayed tasks whose wait time is up



def compute_backoff_delay(retry_count: int) -> float:
    """
    Exponential backoff: delay doubles with each retry attempt.
    Attempt 1 fails -> wait 2s before retry
    Attempt 2 fails -> wait 4s before retry
    Attempt 3 fails -> wait 8s before retry (then goes to DLQ since MAX_RETRIES=3)
    This spaces out retries so we don't hammer a struggling downstream service immediately.
    """
    return BASE_BACKOFF_SECONDS * (2 ** (retry_count - 1))


def send_slack_alert(message: str):
    """Posts the alert to a Slack (or Discord/other webhook-compatible) channel."""
    if not settings.ALERT_WEBHOOK_URL:
        return
    try:
        requests.post(settings.ALERT_WEBHOOK_URL, json={"text": message}, timeout=5)
    except Exception as e:
        print(f"⚠️ Failed to send Slack alert webhook: {e}")


def send_email_alert(subject: str, message: str):
    """
    Sends a real email via SMTP - this is the worker directly connecting to an email
    server (e.g. Gmail's) and sending mail itself, rather than depending on Slack's own
    notification/email settings. This gives guaranteed, immediate delivery under our
    own control, which is why it's the stronger option for demoing "real" alerting.

    Requires SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, and ALERT_EMAIL_TO to be set in .env.
    If they're not set, this silently does nothing (email alerting is optional).
    """
    if not (settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD and settings.ALERT_EMAIL_TO):
        return

    try:
        email_msg = MIMEText(message)
        email_msg["Subject"] = subject
        email_msg["From"] = settings.SMTP_USERNAME
        email_msg["To"] = settings.ALERT_EMAIL_TO

        # SMTP = Simple Mail Transfer Protocol - the standard protocol email servers use
        # to send mail. We open a connection to the mail server, upgrade it to an encrypted
        # connection (starttls), log in, and send the message - just like Gmail's own web
        # app does behind the scenes when you hit "send".
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(email_msg)

        print(f"📧 DLQ alert email sent to {settings.ALERT_EMAIL_TO}")

    except Exception as e:
        # Same principle as the Slack webhook: a failure here must NEVER crash the worker
        # or stop task processing. Alerting is a side-effect, not a critical dependency.
        print(f"⚠️ Failed to send DLQ alert email: {e}")


def send_dlq_alert(task):
    """
    Called whenever a task exhausts all retries and gets moved to the Dead Letter Queue.
    This is the piece that closes the "what happens after DLQ" gap: without this, a failed
    task would just sit silently in queue:dead until someone happened to check manually.

    Fires on TWO independent channels - Slack webhook and real SMTP email - so a human is
    notified through whichever channel they're more likely to see quickly. Each channel is
    wrapped in its own try/except, so if one is unconfigured or fails, the other still runs,
    and a console log always happens regardless, so the alert is never fully silent.
    """
    message = (
        f"🚨 TaskFlow ALERT: Task [{task.id}] (type={task.type}, priority={task.priority}) "
        f"failed {MAX_RETRIES} times and was moved to the Dead Letter Queue. "
        f"Manual investigation needed - retry via POST /tasks/{task.id}/retry once the root cause is fixed."
    )

    print(f"\n{'='*60}\n{message}\n{'='*60}\n")

    send_slack_alert(message)
    send_email_alert(subject="🚨 TaskFlow: Task moved to Dead Letter Queue", message=message)


def reap_stuck_tasks():
    """
    Runs in a background thread. If a worker crashes (or is killed) while a task
    is in PROCESSING, that task would otherwise sit stuck forever since nothing
    ever moves it out of PROCESSING. This function periodically finds tasks that
    have been PROCESSING for too long and puts them back in their queue.
    """
    while True:
        time.sleep(REAPER_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(seconds=STUCK_TASK_TIMEOUT_SECONDS)
            stuck_tasks = db.query(Task).filter(
                Task.status == "PROCESSING",
                Task.started_at != None,
                Task.started_at < cutoff
            ).all()

            for task in stuck_tasks:
                print(f"🧟 Reaper found stuck task [{task.id}] (processing since {task.started_at}). Requeueing.")
                task.status = "PENDING"
                task.started_at = None
                db.commit()
                queue_name = f"queue:{task.priority.lower()}"
                redis_client.lpush(queue_name, task.id)
        except Exception as e:
            print(f"⚠️ Reaper error: {e}")
        finally:
            db.close()

def promote_delayed_tasks():
    """
    Runs in its own background thread, checking every second for tasks whose backoff
    delay has finished. This replaces time.sleep(delay) - instead of the worker loop
    freezing, a failed task is parked in a Redis sorted set (score = when it should
    become available again), and this thread moves it back into its real queue once
    that time passes - without ever blocking the main loop.
    """
    while True:
        time.sleep(DELAY_CHECK_INTERVAL_SECONDS)
        now = time.time()
        due_task_ids = redis_client.zrangebyscore(DELAYED_QUEUE_KEY, 0, now)

        for task_id in due_task_ids:
            removed = redis_client.zrem(DELAYED_QUEUE_KEY, task_id)
            if not removed:
                continue
            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    queue_name = f"queue:{task.priority.lower()}"
                    redis_client.lpush(queue_name, task_id)
                    print(f"⏰ Backoff delay finished for task [{task_id}] - moved back into {queue_name}")
            finally:
                db.close()

def process_tasks():
    print("👷 Advanced Worker started. Waiting for tasks...")
    schedule_position = 0

    while True:
        task_processed = False

        # Walk forward through the weighted schedule, one slot per loop iteration.
        # If that slot's queue happens to be empty right now, we don't block - we just
        # move on to the next slot in the same pass so we're not sitting idle while
        # other queues have work waiting.
        for _ in range(len(QUEUE_SCHEDULE)):
            queue_name = QUEUE_SCHEDULE[schedule_position % len(QUEUE_SCHEDULE)]
            schedule_position += 1

            task_id = redis_client.rpop(queue_name)

            if task_id:
                task_processed = True
                db = SessionLocal()

                try:
                    task = db.query(Task).filter(Task.id == task_id).first()
                    if not task:
                        continue

                    print(f"\n📥 Picked up Task [{task_id}] (Attempt {task.retry_count + 1})")
                    task.status = "PROCESSING"
                    task.started_at = datetime.utcnow()
                    db.commit()

                    # --- SIMULATE HEAVY WORK OR FAILURE ---
                    time.sleep(2)

                    # If the user passed {"force_fail": true} in the JSON data, we simulate a crash!
                    if task.data and task.data.get("force_fail") == True:
                        raise Exception("Simulated External API Crash!")

                    # If it didn't crash, mark it success!
                    task.status = "SUCCESS"
                    task.completed_at = datetime.utcnow()
                    db.commit()
                    print(f"✅ Task {task_id} completed successfully!")

                except Exception as e:
                    print(f"⚠️ Error processing task: {e}")
                    task.retry_count += 1

                    if task.retry_count >= MAX_RETRIES:
                        print(f"💀 Task failed {MAX_RETRIES} times. Moving to Dead Letter Queue (DLQ).")
                        task.status = "FAILED"
                        task.completed_at = datetime.utcnow()
                        # Push to the graveyard queue
                        redis_client.lpush("queue:dead", task.id)
                        send_dlq_alert(task)
                    else:
                        
                        delay = compute_backoff_delay(task.retry_count)
                        print(f"⏳ Scheduling task for Retry {task.retry_count + 1} in {delay}s (non-blocking)")
                        task.status = "PENDING"
                        task.started_at = None
                        db.commit()
                        available_at = time.time() + delay
                        redis_client.zadd(DELAYED_QUEUE_KEY, {task.id: available_at})

                    db.commit()

                finally:
                    db.close()
                break

        if not task_processed:
            time.sleep(1)


if __name__ == "__main__":
    # Start the reaper in a background thread so it runs alongside the main processing loop
    reaper_thread = threading.Thread(target=reap_stuck_tasks, daemon=True)
    reaper_thread.start()

    # Start the delayed-task promoter - this is what makes retry backoff non-blocking
    promoter_thread = threading.Thread(target=promote_delayed_tasks, daemon=True)
    promoter_thread.start()

    process_tasks()
