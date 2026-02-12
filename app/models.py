from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from .db import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String, default="espn", index=True)
    provider_team_id: Mapped[str] = mapped_column(String, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_team_id", name="uq_team_provider_id"),
    )


class TeamRating(Base):
    """
    Current season rating snapshot (MVP).
    Later we can add daily history with (team_id, date).
    """
    __tablename__ = "team_ratings"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    season: Mapped[str] = mapped_column(String, default="2025-26", index=True)

    elo: Mapped[float] = mapped_column(Float, default=1500.0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)

    last_updated_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    team: Mapped["Team"] = relationship()


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    provider: Mapped[str] = mapped_column(String, default="espn", index=True)
    provider_game_id: Mapped[str] = mapped_column(String, index=True)

    start_time_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    date_key: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM-DD (UTC date of start)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String, default="scheduled", index=True)  # scheduled/in_progress/final
    neutral_site: Mapped[bool] = mapped_column(Boolean, default=False)

    # Elo bookkeeping (so we don't double-apply updates)
    elo_applied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id])

    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_game_id", name="uq_game_provider_id"),
    )
