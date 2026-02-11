from __future__ import annotations
from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import date, datetime, timezone
from .db import Base, engine, get_db
from .models import Game, Team
from .espn_client import fetch_scoreboard
from .ingest import ingest_scoreboard_json

app = FastAPI(title="CBB Tracker", version="0.1.0")

Base.metadata.create_all(bind=engine)

@app.get("/")
def root():
    return {
        "name": "CBB Tracker API",
        "docs": "/docs",
        "try": {
            "POST update": "/admin/update?dates=YYYYMMDD-YYYYMMDD",
            "GET teams": "/teams",
            "GET games": "/games?game_date=YYYY-MM-DD",
        },
    }



def _default_dates_range() -> str:
    # Default: today (UTC) and yesterday (UTC) to catch late-night games cleanly
    today_utc = datetime.now(timezone.utc).date()
    yday_utc = today_utc.fromordinal(today_utc.toordinal() - 1)
    return f"{yday_utc.strftime('%Y%m%d')}-{today_utc.strftime('%Y%m%d')}"


async def _run_ingest(dates: str) -> dict:
    payload = await fetch_scoreboard(dates=dates)
    # new session per background task run
    from .db import SessionLocal
    db = SessionLocal()
    try:
        return ingest_scoreboard_json(db, payload)
    finally:
        db.close()


@app.post("/admin/update")
async def admin_update(background_tasks: BackgroundTasks, dates: str | None = None):
    """
    Triggers ingestion in the background and returns immediately.
    dates can be:
      - YYYYMMDD
      - YYYYMMDD-YYYYMMDD
    """
    dates = dates or _default_dates_range()

    # fire-and-forget; results are not returned to the caller in this MVP
    background_tasks.add_task(_run_ingest, dates)
    return {"queued": True, "dates": dates}


@app.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    teams = db.execute(select(Team).order_by(Team.name.asc())).scalars().all()
    return [{"id": t.id, "name": t.name, "provider_team_id": t.provider_team_id} for t in teams]


@app.get("/games")
def list_games(game_date: date | None = None, db: Session = Depends(get_db)):
    """
    game_date: YYYY-MM-DD (UTC date_key)
    """
    q = select(Game).order_by(Game.start_time_utc.desc())
    if game_date:
        q = q.where(Game.date_key == game_date.isoformat())

    games = db.execute(q).scalars().all()
    out = []
    for g in games:
        out.append(
            {
                "id": g.id,
                "provider_game_id": g.provider_game_id,
                "start_time_utc": g.start_time_utc.isoformat() if g.start_time_utc else None,
                "date_key": g.date_key,
                "status": g.status,
                "neutral_site": g.neutral_site,
                "home_team": {"id": g.home_team.id, "name": g.home_team.name},
                "away_team": {"id": g.away_team.id, "name": g.away_team.name},
                "home_score": g.home_score,
                "away_score": g.away_score,
            }
        )
    return out
