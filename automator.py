import urllib.request
import json
import time
import random

# Your API endpoint
URL = "http://127.0.0.1:8001/tasks"

# Random data to simulate real-world traffic
TASK_TYPES = ["WELCOME_EMAIL", "MONTHLY_REPORT", "PAYMENT_PROCESS", "IMAGE_COMPRESS"]
PRIORITIES = ["HIGH", "NORMAL", "LOW"]

print("🤖 Chaos Automator Started! Simulating live traffic with 20% failure rate...")
print("Press Ctrl+C in this terminal to stop the robot.\n")

while True:
    # 1. Roll the dice! 20% chance to create a "poison" task that will crash the worker
    is_poison = random.random() < 0.20 
    
    # 2. Randomly generate a task
    payload = {
        "type": random.choice(TASK_TYPES),
        "priority": random.choice(PRIORITIES),
        "data": {
            "user_id": random.randint(1000, 9999),
            "bot_generated": True,
            "force_fail": is_poison  # <--- HERE IS THE CHAOS
        }
    }
    
    # 3. Package it into an HTTP POST request
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(URL, data=data, headers={'Content-Type': 'application/json'})
    
    try:
        urllib.request.urlopen(req)
        status = "☠️ POISON" if is_poison else "✅ CLEAN"
        print(f"🚀 Sent: [{payload['priority']}] {payload['type']} - {status}")
    except Exception as e:
        print(f"⚠️ API Error: {e}")
        
    # 4. Sleep for a random time (between 1 and 4 seconds)
    time.sleep(random.uniform(1.0, 4.0))