from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import Team, Game


def _to_utc_dt(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    # ESPN often returns ISO timestamps like "2026-02-11T00:00Z" or with offset
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def _status_from_event(event: dict[str, Any]) -> str:
    # Typical: event["status"]["type"]["name"] == "STATUS_SCHEDULED"/"STATUS_IN_PROGRESS"/"STATUS_FINAL"
    try:
        name = event["status"]["type"]["name"]
    except Exception:
        return "scheduled"
    if "FINAL" in name:
        return "final"
    if "IN_PROGRESS" in name:
        return "in_progress"
    return "scheduled"


def _safe_int(x) -> int | None:
    try:
        return int(x)
    except Exception:
        return None


def upsert_team(db: Session, provider: str, provider_team_id: str, name: str) -> Team:
    q = select(Team).where(Team.provider == provider, Team.provider_team_id == provider_team_id)
    team = db.execute(q).scalar_one_or_none()
    if team:
        if team.name != name:
            team.name = name
        return team
    team = Team(provider=provider, provider_team_id=provider_team_id, name=name)
    db.add(team)
    db.flush()
    return team


def upsert_game(
    db: Session,
    provider: str,
    provider_game_id: str,
    start_time_utc: datetime | None,
    date_key: str,
    home_team: Team,
    away_team: Team,
    home_score: int | None,
    away_score: int | None,
    status: str,
    neutral_site: bool,
) -> Game:
    q = select(Game).where(Game.provider == provider, Game.provider_game_id == provider_game_id)
    game = db.execute(q).scalar_one_or_none()
    if not game:
        game = Game(
            provider=provider,
            provider_game_id=provider_game_id,
            start_time_utc=start_time_utc,
            date_key=date_key,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            home_score=home_score,
            away_score=away_score,
            status=status,
            neutral_site=neutral_site,
            elo_applied=False,
        )
        db.add(game)
        return game

    # Update existing (DO NOT reset elo_applied)
    game.start_time_utc = start_time_utc
    game.date_key = date_key
    game.home_team_id = home_team.id
    game.away_team_id = away_team.id
    game.home_score = home_score
    game.away_score = away_score
    game.status = status
    game.neutral_site = neutral_site
    return game



def ingest_scoreboard_json(db: Session, payload: dict[str, Any]) -> dict[str, int]:
    provider = "espn"
    events = payload.get("events") or []

    inserted_or_updated_games = 0
    inserted_or_updated_teams = 0

    for ev in events:
        provider_game_id = str(ev.get("id", "")).strip()
        if not provider_game_id:
            continue

        start_time_utc = _to_utc_dt(ev.get("date"))
        date_key = (start_time_utc.date().isoformat() if start_time_utc else "unknown")

        status = _status_from_event(ev)

        # ESPN structure: ev["competitions"][0]["competitors"] is usually [home, away]
        competitions = ev.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]
        neutral_site = bool(comp.get("neutralSite") or False)

        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        home_team_id = str(home.get("team", {}).get("id", "")).strip()
        away_team_id = str(away.get("team", {}).get("id", "")).strip()
        home_name = str(home.get("team", {}).get("displayName") or home.get("team", {}).get("name") or "").strip()
        away_name = str(away.get("team", {}).get("displayName") or away.get("team", {}).get("name") or "").strip()
        if not home_team_id or not away_team_id or not home_name or not away_name:
            continue

        ht = upsert_team(db, provider, home_team_id, home_name)
        at = upsert_team(db, provider, away_team_id, away_name)
        inserted_or_updated_teams += 2

        home_score = _safe_int(home.get("score"))
        away_score = _safe_int(away.get("score"))

        upsert_game(
            db=db,
            provider=provider,
            provider_game_id=provider_game_id,
            start_time_utc=start_time_utc,
            date_key=date_key,
            home_team=ht,
            away_team=at,
            home_score=home_score,
            away_score=away_score,
            status=status,
            neutral_site=neutral_site,
        )
        inserted_or_updated_games += 1

    db.commit()
    return {
        "events_seen": len(events),
        "teams_touched": inserted_or_updated_teams,
        "games_upserted": inserted_or_updated_games,
    }
