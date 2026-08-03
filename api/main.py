from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from redis.exceptions import RedisError
from prometheus_fastapi_instrumentator import Instrumentator
from .database import engine, get_db
from . import models, schemas
from .queue_manager import push_task_to_queue  # NEW: Import the queue tool

# 1. Generate the Tables
models.Base.metadata.create_all(bind=engine)

# 2. Initialize the Server Application
app = FastAPI(
    title="TaskFlow API",
    description="A Distributed Task Processing Engine",
    version="1.0.0"
)
Instrumentator().instrument(app).expose(app)
# 3. The Health Check Route
@app.get("/")
def health_check():
    return {"status": "ok", "message": "TaskFlow API is up and running!"}

# 4. The Task Submission Route
@app.post("/tasks")
def create_task(task_in: schemas.TaskCreate, db: Session = Depends(get_db)):
    # Step 1: Save it to PostgreSQL.
    # If Postgres itself is unreachable (down, network issue, etc.), this raises
    # OperationalError. We catch it and return a clean 503 "service unavailable"
    # instead of letting a raw, unhandled exception produce an ugly 500 error with
    # an internal stack trace exposed to the caller.
    try:
        new_task = models.Task(
            type=task_in.type,
            priority=task_in.priority,
            data=task_in.data
        )
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
    except OperationalError:
        raise HTTPException(
            status_code=503,
            detail="Database is temporarily unavailable. Please retry in a moment."
        )

    # Step 2: Push the ID to Redis.
    # This is the trickier failure case: the task already exists safely in Postgres
    # at this point, but if Redis is down, it will NEVER be picked up by a worker -
    # nothing is watching for "saved but never queued" tasks yet. We still return a
    # clear error to the caller rather than pretending it succeeded, but this is a
    # known gap: a production fix would be a periodic reconciliation job (the "outbox
    # pattern") that finds PENDING tasks missing from Redis and re-queues them.
    try:
        queue_name = push_task_to_queue(str(new_task.id), new_task.priority)
    except RedisError:
        raise HTTPException(
            status_code=503,
            detail=f"Task {new_task.id} was saved but could not be queued because the queue "
                   f"service is temporarily unavailable. It will need to be manually re-queued."
        )

    return {
        "message": "Task successfully recorded and queued",
        "task_id": new_task.id,
        "status": new_task.status,
        "queue": queue_name  # We return this to prove it worked!
    }

# ... (Keep all your existing code above this) ...

# 5. ADMIN: View the Dead Letter Queue
@app.get("/tasks/dead")
def get_dead_tasks(db: Session = Depends(get_db)):
    # Query the database for all tasks that have permanently failed
    dead_tasks = db.query(models.Task).filter(models.Task.status == "FAILED").all()
    return {"dead_tasks": dead_tasks, "count": len(dead_tasks)}

# 6. ADMIN: Resurrect a Dead Task
@app.post("/tasks/{task_id}/retry")
def retry_dead_task(task_id: str, db: Session = Depends(get_db)):
    # Find the task in the database
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    
    if not task:
        return {"error": "Task not found"}
    if task.status != "FAILED":
        return {"error": "Only FAILED tasks can be manually retried"}
    
    # Reset the task back to factory settings
    task.status = "PENDING"
    task.retry_count = 0
    db.commit()
    
    # Remove it from the Redis dead queue and put it back in the live queue
    from .queue_manager import redis_client # Import here to avoid circular imports
    redis_client.lrem("queue:dead", 0, task.id) 
    queue_name = push_task_to_queue(str(task.id), task.priority)
    
    return {
        "message": "Task resurrected from DLQ and re-queued!",
        "task_id": task.id,
        "new_queue": queue_name
    }

# 7. Look up a single task by ID (check status, retry count, timestamps)
@app.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        return {"error": "Task not found"}
    return task

# 8. Queue stats - shows how many tasks are waiting in each priority queue right now
@app.get("/stats/queues")
def get_queue_stats():
    from .queue_manager import redis_client
    return {
        "queue_high": redis_client.llen("queue:high"),
        "queue_normal": redis_client.llen("queue:normal"),
        "queue_low": redis_client.llen("queue:low"),
        "queue_dead": redis_client.llen("queue:dead"),
    }

# 9. Task status breakdown - counts of PENDING/PROCESSING/SUCCESS/FAILED in Postgres
@app.get("/stats/tasks")
def get_task_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    results = db.query(models.Task.status, func.count(models.Task.id)).group_by(models.Task.status).all()
    return {status: count for status, count in results}