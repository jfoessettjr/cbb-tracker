from __future__ import annotations
from dataclasses import dataclass
from math import pow
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from .models import TeamRating, Game


DEFAULT_ELO = 1500.0

# Simple, solid starting knobs:
K_BASE = 20.0
HOME_ADVANTAGE_ELO = 65.0  # ~ typical home edge; tune later


def expected_score(r_a: float, r_b: float) -> float:
    # logistic curve
    return 1.0 / (1.0 + pow(10.0, (r_b - r_a) / 400.0))


def k_factor(games_played: int) -> float:
    # Higher K early season, lower later
    if games_played < 10:
        return K_BASE * 1.25
    if games_played < 25:
        return K_BASE
    return K_BASE * 0.85


def get_or_create_rating(db: Session, team_id: int, season: str) -> TeamRating:
    r = db.execute(
        select(TeamRating).where(TeamRating.team_id == team_id, TeamRating.season == season)
    ).scalar_one_or_none()
    if r:
        return r
    r = TeamRating(team_id=team_id, season=season, elo=DEFAULT_ELO, games_played=0)
    db.add(r)
    db.flush()
    return r


def apply_elo_to_final_games(db: Session, season: str) -> dict[str, int]:
    """
    Finds games that are final and not yet elo_applied, applies updates, and marks them applied.
    """
    games = db.execute(
        select(Game).where(Game.status == "final", Game.elo_applied == False)  # noqa: E712
    ).scalars().all()

    applied = 0

    for g in games:
        if g.home_score is None or g.away_score is None:
            continue

        home = get_or_create_rating(db, g.home_team_id, season)
        away = get_or_create_rating(db, g.away_team_id, season)

        # Result
        if g.home_score > g.away_score:
            s_home = 1.0
        elif g.home_score < g.away_score:
            s_home = 0.0
        else:
            s_home = 0.5

        # Home advantage (skip if neutral)
        home_adj = 0.0 if g.neutral_site else HOME_ADVANTAGE_ELO

        e_home = expected_score(home.elo + home_adj, away.elo)
        e_away = 1.0 - e_home

        k_h = k_factor(home.games_played)
        k_a = k_factor(away.games_played)

        # Update
        home.elo = home.elo + k_h * (s_home - e_home)
        away.elo = away.elo + k_a * ((1.0 - s_home) - e_away)

        home.games_played += 1
        away.games_played += 1
        now = datetime.utcnow()
        home.last_updated_utc = now
        away.last_updated_utc = now

        g.elo_applied = True
        applied += 1

    db.commit()
    return {"final_games_found": len(games), "elo_games_applied": applied}
