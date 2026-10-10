from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime

class TranscriptResponse(BaseModel):
    id: int
    timestamp: datetime
    role: str
    content: str
    
    model_config = ConfigDict(from_attributes=True)

class ToolExecutionResponse(BaseModel):
    id: int
    timestamp: datetime
    tool_name: str
    arguments_json: Optional[str] = None
    success: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class CallResponse(BaseModel):
    id: int
    direction: str
    remote_identity: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: str
    
    # These will automatically fetch related rows from the database!
    transcripts: List[TranscriptResponse] = []
    tool_logs: List[ToolExecutionResponse] = []

    model_config = ConfigDict(from_attributes=True)