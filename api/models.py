from sqlalchemy import Column, String, Integer, DateTime, JSON
from datetime import datetime
import uuid

from .database import Base

class Task(Base):
    # 1. The Table Name in PostgreSQL
    __tablename__ = "tasks"

    # 2. The Columns
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String, nullable=False)          # e.g., "EMAIL", "REPORT"
    priority = Column(String, default="NORMAL")    # "HIGH", "NORMAL", "LOW"
    status = Column(String, default="PENDING")     # "PENDING", "PROCESSING", "SUCCESS", "FAILED"
    
    # 3. Flexible Payload
    data = Column(JSON, nullable=True)             # Any extra data the worker needs
    
    # 4. Tracking
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)    # Set when worker picks it up; used to detect stuck tasks
    completed_at = Column(DateTime, nullable=True)