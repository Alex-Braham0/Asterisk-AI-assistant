from fastapi import APIRouter, Depends, HTTPException, Query
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
def get_history(limit: int = Query(50, le=50), db: Session = Depends(get_db)):
    try:
        return db.query(CallRecord).order_by(CallRecord.id.desc()).limit(limit).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))