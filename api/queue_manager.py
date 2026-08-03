import redis
from .config import settings

# 1. Establish the Connection to Docker's Redis
# decode_responses=True means we get normal strings back, not byte-code
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# 2. The function to push tasks into the waiting room
def push_task_to_queue(task_id: str, priority: str):
    """
    Takes a task ID and pushes it into the correct priority queue.
    """
    # Force priority to lowercase (e.g., "HIGH" becomes "high")
    # This creates queue names like: queue:high, queue:normal, queue:low
    queue_name = f"queue:{priority.lower()}"
    
    # LPUSH (Left Push) inserts the task_id into the Redis list
    redis_client.lpush(queue_name, task_id)
    
    return queue_name