import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, event
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DB_PATH = os.environ.get("DB_PATH", "/home/gemini/Asterisk-AI-assistant/data/call_history.db")

# Create engine with a 5-second lock timeout
engine = create_engine(
    f"sqlite:///{DB_PATH}", 
    connect_args={"timeout": 5.0}
)

# Enforce WAL mode AND Foreign Keys on every new connection
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# =====================================================================
# CORE SCHEMA (Immutable Foundation)
# =====================================================================

class CallRecord(Base):
    __tablename__ = "calls"
    
    id = Column(Integer, primary_key=True, index=True)
    direction = Column(String, nullable=False, index=True)       # "inbound" or "outbound"
    remote_identity = Column(String, nullable=False, index=True) # Caller ID or Destination
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)                      # "completed", "dropped", etc.
    
    # SQLAlchemy Relationships
    transcripts = relationship("TranscriptLine", back_populates="call", cascade="all, delete-orphan")
    tool_logs = relationship("ToolExecution", back_populates="call", cascade="all, delete-orphan")


# =====================================================================
# MODULAR EXPANSIONS
# =====================================================================

class TranscriptLine(Base):
    __tablename__ = "transcripts"
    
    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    role = Column(String, nullable=False) # "user" or "model"
    content = Column(String, nullable=False)
    
    call = relationship("CallRecord", back_populates="transcripts")


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    
    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    tool_name = Column(String, nullable=False)
    arguments_json = Column(String) 
    success = Column(Integer) # 1 for True, 0 for False
    
    call = relationship("CallRecord", back_populates="tool_logs")