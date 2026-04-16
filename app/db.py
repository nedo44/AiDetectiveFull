from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Kurim Mystery")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    characters: Mapped[list["Character"]] = relationship(back_populates="game_session", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="game_session", cascade="all, delete-orphan")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_session_id: Mapped[int] = mapped_column(ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    suspect_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    secret_role: Mapped[str] = mapped_column(String(40), nullable=False, default="podezřelý")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    game_session: Mapped[GameSession] = relationship(back_populates="characters")
    messages: Mapped[list["Message"]] = relationship(back_populates="character", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_session_id: Mapped[int] = mapped_column(ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    game_session: Mapped[GameSession] = relationship(back_populates="messages")
    character: Mapped[Character] = relationship(back_populates="messages")


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Keep local development DB compatible after model changes.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE characters ADD COLUMN IF NOT EXISTS suspect_id VARCHAR(40)"))
        connection.execute(text("ALTER TABLE characters ADD COLUMN IF NOT EXISTS secret_role VARCHAR(40) DEFAULT 'podezřelý'"))
        connection.execute(text("ALTER TABLE characters ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_characters_suspect_id ON characters (suspect_id)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
