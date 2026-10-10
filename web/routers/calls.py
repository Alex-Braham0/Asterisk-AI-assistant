from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from core.database import SessionLocal, CallRecord
from core.schemas import CallResponse

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[CallResponse])
def get_history(db: Session = Depends(get_db)):
    try:
        # Notice how simple this is now! Pydantic handles the serialization entirely.
        return db.query(CallRecord).order_by(CallRecord.id.desc()).limit(50).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))