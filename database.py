from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# قاعدة البيانات المحلية للتطوير (SQLite)
SQLALCHEMY_DATABASE_URL = "sqlite:///./hojrat_bladi.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} # خاص بـ SQLite
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency لإدارة الجلسات في FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()