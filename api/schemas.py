from pydantic import BaseModel
from typing import Optional, Dict, Any

# This defines exactly what a user is allowed to send to our API
class TaskCreate(BaseModel):
    type: str                                # REQUIRED: e.g., "EMAIL", "REPORT"
    priority: str = "NORMAL"                 # OPTIONAL: Defaults to "NORMAL"
    data: Optional[Dict[str, Any]] = None    # OPTIONAL: Any extra JSON data