from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class JobResponse(BaseModel):
    job_id: int
    message: str

class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    summary: Optional[Dict[str, Any]] = None 

    class Config:
        from_attributes = True