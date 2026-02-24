# Define database connection here

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src import constants


engine = create_engine(
    constants.config["DATABASE_URL"],
    echo=True,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



def get_db_session():
    """Get a database session (manual - remember to close!)"""
    return SessionLocal()
