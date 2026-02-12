from __future__ import annotations
from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from datetime import date, datetime, timezone

from .db import Base, engine, get_db
from .models import Game, Team, TeamRating
from .espn_client import fetch_scoreboard
from .ingest import ingest_scoreboard_json
from .elo import expected_score, get_or_create_rating, HOME_ADVANTAGE_ELO, apply_elo_to_final_games

app = FastAPI(title="CBB Tracker", version="0.2.0")

Base.metadata.create_all(bind=engine)


def _default_dates_range() -> str:
    today_utc = datetime.now(timezone.utc).date()
    yday_utc = today_utc.fromordinal(today_utc.toordinal() - 1)
    return f"{yday_utc.strftime('%Y%m%d')}-{today_utc.strftime('%Y%m%d')}"


def _default_season() -> str:
    # For now: hardcode; later derive from date or config
    return "2025-26"


async def _run_ingest_and_recalc(dates: str, season: str) -> dict:
    payload = await fetch_scoreboard(dates=dates)
    from .db import SessionLocal
    db = SessionLocal()
    try:
        ingest_stats = ingest_scoreboard_json(db, payload)
        elo_stats = apply_elo_to_final_games(db, season=season)
        return {"ingest": ingest_stats, "elo": elo_stats}
    finally:
        db.close()


@app.get("/")
def root():
    return {
        "name": "CBB Tracker API",
        "docs": "/docs",
        "try": {
            "POST update (bg)": "/admin/update?dates=YYYYMMDD-YYYYMMDD",
            "POST update_sync": "/admin/update_sync?dates=YYYYMMDD-YYYYMMDD",
            "POST recalc_elo": "/admin/recalc_elo",
            "GET teams": "/teams",
            "GET games": "/games?game_date=YYYY-MM-DD",
            "GET predict": "/predict?home_team_id=1&away_team_id=2&neutral=0",
        },
    }


@app.post("/admin/update")
async def admin_update(background_tasks: BackgroundTasks, dates: str | None = None, season: str | None = None):
    dates = dates or _default_dates_range()
    season = season or _default_season()
    background_tasks.add_task(_run_ingest_and_recalc, dates, season)
    return {"queued": True, "dates": dates, "season": season}


@app.post("/admin/update_sync")
async def admin_update_sync(dates: str | None = None, season: str | None = None):
    dates = dates or _default_dates_range()
    season = season or _default_season()
    payload = await fetch_scoreboard(dates=dates)

    from .db import SessionLocal
    db = SessionLocal()
    try:
        ingest_stats = ingest_scoreboard_json(db, payload)
        elo_stats = apply_elo_to_final_games(db, season=season)
    finally:
        db.close()

    return {"dates": dates, "season": season, "stats": {"ingest": ingest_stats, "elo": elo_stats}}


@app.post("/admin/recalc_elo")
def admin_recalc_elo(season: str | None = None, db: Session = Depends(get_db)):
    season = season or _default_season()
    stats = apply_elo_to_final_games(db, season=season)
    return {"season": season, "stats": stats}


@app.get("/stats")
def stats(db: Session = Depends(get_db)):
    team_count = db.execute(select(func.count()).select_from(Team)).scalar_one()
    game_count = db.execute(select(func.count()).select_from(Game)).scalar_one()
    rated_count = db.execute(select(func.count()).select_from(TeamRating)).scalar_one()
    return {"teams": team_count, "games": game_count, "rated_teams": rated_count}


@app.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    teams = db.execute(select(Team).order_by(Team.name.asc())).scalars().all()
    return [{"id": t.id, "name": t.name, "provider_team_id": t.provider_team_id} for t in teams]


@app.get("/games")
def list_games(game_date: date | None = None, db: Session = Depends(get_db)):
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
                "elo_applied": g.elo_applied,
                "home_team": {"id": g.home_team.id, "name": g.home_team.name},
                "away_team": {"id": g.away_team.id, "name": g.away_team.name},
                "home_score": g.home_score,
                "away_score": g.away_score,
            }
        )
    return out


@app.get("/ratings")
def list_ratings(limit: int = 50, season: str | None = None, db: Session = Depends(get_db)):
    season = season or _default_season()
    rs = db.execute(
        select(TeamRating, Team)
        .join(Team, Team.id == TeamRating.team_id)
        .where(TeamRating.season == season)
        .order_by(TeamRating.elo.desc())
        .limit(limit)
    ).all()

    return [
        {"team_id": t.id, "team_name": t.name, "season": r.season, "elo": r.elo, "games_played": r.games_played}
        for (r, t) in rs
    ]


@app.get("/predict")
def predict(
    home_team_id: int,
    away_team_id: int,
    neutral: int = 0,
    season: str | None = None,
    db: Session = Depends(get_db),
):
    season = season or _default_season()
    home = get_or_create_rating(db, home_team_id, season)
    away = get_or_create_rating(db, away_team_id, season)

    home_adj = 0.0 if neutral else HOME_ADVANTAGE_ELO
    p_home = expected_score(home.elo + home_adj, away.elo)

    return {
        "season": season,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "neutral": bool(neutral),
        "home": {"elo": home.elo, "games_played": home.games_played},
        "away": {"elo": away.elo, "games_played": away.games_played},
        "home_win_prob": p_home,
        "away_win_prob": 1.0 - p_home,
        "explain": {
            "elo_gap": (home.elo + home_adj) - away.elo,
            "home_advantage_elo": 0.0 if neutral else HOME_ADVANTAGE_ELO,
        },
    }
