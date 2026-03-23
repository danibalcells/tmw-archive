from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipeline.config import DATABASE_URL


def _sqlite_pragma_on_connect(dbapi_conn: object, _: object) -> None:
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _make_engine() -> Engine:
    kwargs: dict = {}
    if DATABASE_URL.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        db_path = DATABASE_URL.removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(DATABASE_URL, **kwargs)

    if DATABASE_URL.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragma_on_connect)

    return engine


engine: Engine = _make_engine()

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autocommit=False, autoflush=False
)


def get_session() -> Generator[Session, None, None]:
    """Yield a DB session, closing it when the caller is done.

    Use as a context manager or dependency:

        with get_session() as db:
            db.query(Track).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
