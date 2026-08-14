"""
Free data sources used by this project (all public, no API keys):

1. MLB Stats API (statsapi.mlb.com) — official, free, no API key required.
   Confirmed via the public endpoint documentation. Provides:
     - schedule + probable pitchers  (/schedule?hydrate=probablePitcher)
     - standings, incl. AS-OF-DATE via the `date` param  (/standings?date=...)
       -> this is what makes the point-in-time backtest possible
     - player season stats + game logs  (/people/{id}/stats)
     - boxscores  (/game/{gamePk}/boxscore)  -> recent bullpen usage
   Docs: https://github.com/pseudo-r/Public-MLB-API  (undocumented but stable)

2. Baseball Savant park factors (baseballsavant.mlb.com) — free, no key.
   Fetched at runtime as CSV from the Statcast park-factors leaderboard.
   If that fetch fails (site change, offline), we fall back to the static
   table below so the pipeline never breaks.
"""
import csv as _csv
import io
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://statsapi.mlb.com/api/v1"
SAVANT_PF_URL = "https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
EASTERN = ZoneInfo("America/New_York")

# Fallback only. 100 = neutral run environment. Auto-fetch (below) is preferred.
STATIC_PARK_FACTORS = {
    "Coors Field": 112, "Great American Ball Park": 108, "Yankee Stadium": 105,
    "Fenway Park": 103, "Chase Field": 102, "Citizens Bank Park": 102,
    "Rate Field": 101, "American Family Field": 101, "Oriole Park at Camden Yards": 100,
    "Wrigley Field": 100, "Truist Park": 100, "Daikin Park": 99,
    "Rogers Centre": 99, "Angel Stadium": 99, "Nationals Park": 99,
    "Target Field": 98, "Kauffman Stadium": 98, "Dodger Stadium": 98,
    "Globe Life Field": 97, "Comerica Park": 97, "Progressive Field": 97,
    "Sutter Health Park": 100, "Busch Stadium": 97, "PNC Park": 96,
    "Citi Field": 96, "Petco Park": 95, "loanDepot Park": 93,
    "T-Mobile Park": 92, "Oracle Park": 92,
}
DEFAULT_PARK_FACTOR = 100

# Populated once per run by load_park_factors(); falls back to the static map.
PARK_FACTORS = dict(STATIC_PARK_FACTORS)


def load_park_factors(season):
    """Fetch current park factors from Baseball Savant (free CSV). Returns a
    {venue_name: factor} dict. On any failure, returns the static fallback so
    the rest of the pipeline keeps working. Called once at startup."""
    global PARK_FACTORS
    try:
        resp = requests.get(SAVANT_PF_URL, params={
            "type": "year", "year": season, "batSide": "", "stat": "index_wOBA",
            "condition": "All", "rolling": "", "parks": "mlb", "csv": "true",
        }, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        reader = _csv.DictReader(io.StringIO(resp.text))
        fetched = {}
        for row in reader:
            name = (row.get("venue_name") or row.get("name") or "").strip()
            # Savant exposes an index (100 = neutral) under one of these keys.
            raw = (row.get("index_wOBA") or row.get("park_factor")
                   or row.get("index") or "").strip()
            if name and raw:
                try:
                    fetched[name] = float(raw)
                except ValueError:
                    continue
        if fetched:
            # Merge: fetched values win, static fills any gaps in name matching.
            merged = dict(STATIC_PARK_FACTORS)
            merged.update(fetched)
            PARK_FACTORS = merged
            print(f"Loaded {len(fetched)} park factors from Baseball Savant.")
            return PARK_FACTORS
    except Exception as e:
        print(f"Park-factor fetch failed ({e}); using static fallback table.")
    PARK_FACTORS = dict(STATIC_PARK_FACTORS)
    return PARK_FACTORS


def get_park_factor(venue_name):
    return PARK_FACTORS.get(venue_name, DEFAULT_PARK_FACTOR)


def get_today_eastern_date():
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def get_schedule(date_str):
    resp = requests.get(f"{BASE}/schedule", params={
        "sportId": 1, "date": date_str,
        "hydrate": "probablePitcher,team,venue,linescore",
    }, timeout=20)
    resp.raise_for_status()
    games = []
    for d in resp.json().get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_standings(season, as_of_date=None):
    """Team records. If as_of_date ('YYYY-MM-DD') is given, returns standings
    as they stood on that date — the key to an honest point-in-time backtest,
    since it avoids leaking end-of-season records into past predictions."""
    params = {"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"}
    if as_of_date:
        params["date"] = as_of_date
    resp = requests.get(f"{BASE}/standings", params=params, timeout=20)
    resp.raise_for_status()
    records = {}
    for record in resp.json().get("records", []):
        for team in record.get("teamRecords", []):
            name = team["team"]["name"]
            wins, losses = team["wins"], team["losses"]
            records[name] = {
                "wins": wins, "losses": losses,
                "win_pct": wins / max(wins + losses, 1),
            }
    return records


def get_season_era(pitcher_id, season):
    if pitcher_id is None:
        return None
    resp = requests.get(f"{BASE}/people/{pitcher_id}/stats", params={
        "stats": "season", "group": "pitching", "season": season,
    }, timeout=20)
    if resp.status_code != 200:
        return None
    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        return None
    era = splits[0]["stat"].get("era")
    try:
        return float(era)
    except (TypeError, ValueError):
        return None


def get_last_n_starts_era(pitcher_id, season, n=3):
    """Average ERA over a pitcher's last n starts, from their game log."""
    if pitcher_id is None:
        return None
    resp = requests.get(f"{BASE}/people/{pitcher_id}/stats", params={
        "stats": "gameLog", "group": "pitching", "season": season,
    }, timeout=20)
    if resp.status_code != 200:
        return None
    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    starts = [s for s in splits if str(s["stat"].get("gamesStarted")) == "1"]
    starts = (starts or splits)[-n:]
    if not starts:
        return None
    total_er = sum(float(s["stat"].get("earnedRuns", 0)) for s in starts)
    total_ip = sum(float(s["stat"].get("inningsPitched", 0)) for s in starts)
    if total_ip == 0:
        return None
    return round((total_er * 9) / total_ip, 2)


def get_bullpen_recent_ip(team_id, before_date_str, days=3):
    """Relief innings pitched by a team's bullpen over the previous `days` days
    (a rough fatigue proxy — heavier recent usage suggests a taxed bullpen)."""
    start = (datetime.strptime(before_date_str, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    end = (datetime.strptime(before_date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    resp = requests.get(f"{BASE}/schedule", params={
        "sportId": 1, "teamId": team_id, "startDate": start, "endDate": end,
    }, timeout=20)
    if resp.status_code != 200:
        return 0.0
    total_relief_ip = 0.0
    for d in resp.json().get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            box_resp = requests.get(f"{BASE}/game/{g['gamePk']}/boxscore", timeout=20)
            if box_resp.status_code != 200:
                continue
            box = box_resp.json()
            for side in ("home", "away"):
                team_box = box.get("teams", {}).get(side, {})
                if team_box.get("team", {}).get("id") != team_id:
                    continue
                pitcher_ids = team_box.get("pitchers", [])[1:]  # skip the starter
                players = team_box.get("players", {})
                for pid in pitcher_ids:
                    p = players.get(f"ID{pid}", {})
                    ip = p.get("stats", {}).get("pitching", {}).get("inningsPitched")
                    if ip:
                        total_relief_ip += float(ip)
    return round(total_relief_ip, 1)
