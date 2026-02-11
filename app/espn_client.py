from __future__ import annotations
import httpx
from typing import Any

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
)

# Notes:
# - `groups=50` is often used to get “all D1” instead of just ranked/top games.
# - `limit=500` helps get all games for a day in one shot.
# These patterns are widely used in community references. :contentReference[oaicite:2]{index=2}

async def fetch_scoreboard(dates: str) -> dict[str, Any]:
    params = {
        "dates": dates,      # "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"
        "groups": 50,
        "limit": 500,
    }
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(ESPN_SCOREBOARD_URL, params=params, headers={"User-Agent": "cbb-tracker/1.0"})
        r.raise_for_status()
        return r.json()
