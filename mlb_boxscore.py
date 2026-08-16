"""
Predicted (expected-value) box score for a game.

IMPORTANT — what this is and isn't:
These are EXPECTATIONS derived from season rate stats, not predictions of what
will actually happen. A batter's "0.9 expected hits" means that's his season
average per game against a league-average opponent, lightly adjusted for the
opposing starter and park — NOT a claim he'll get a hit tonight. Individual
single-game outcomes are mostly noise; no model can call a real box score line.
We show decimals on purpose so the numbers read as averages, not as a fake
"2-for-4" prediction. Treat this as context, not a betting signal. It is
unbacktested and lower-confidence than the game model.

Data: balldontlie /season_stats (rates), /lineups (projected lineup, 2026+),
falling back to each team's most-used batters when no lineup is posted yet.
"""
import os
import requests

BDL_BASE = "https://api.balldontlie.io/mlb/v1"
LEAGUE_AVG_RUNS = 4.5   # ~league average runs/team/game; rough, update yearly


def _headers():
    key = os.environ.get("BALLDONTLIE_API_KEY")
    if not key:
        raise RuntimeError("BALLDONTLIE_API_KEY not set.")
    return {"Authorization": key}


def get_lineup(game_id, team_id):
    """Projected batting order for one team in a game, if balldontlie has posted
    it (2026+ and usually only 1-2 hours before first pitch). Returns a list of
    {player_id, name, order} or [] if not available."""
    try:
        resp = requests.get(f"{BDL_BASE}/lineups", headers=_headers(),
                            params={"game_id": game_id}, timeout=20)
        if resp.status_code != 200:
            return []
    except Exception:
        return []
    out = []
    for entry in resp.json().get("data", []):
        tid = entry.get("team_id") or (entry.get("team") or {}).get("id")
        if tid != team_id:
            continue
        player = entry.get("player") or {}
        pid = entry.get("player_id") or player.get("id")
        if pid is None:
            continue
        name = player.get("full_name") or \
            (f'{player.get("first_name","")} {player.get("last_name","")}'.strip())
        out.append({"player_id": pid, "name": name or f"Player {pid}",
                    "order": entry.get("batting_order") or entry.get("order") or 99})
    out.sort(key=lambda r: r["order"])
    return out


def get_team_regulars(team_id, season, limit=9):
    """Fallback lineup: a team's most-used batters this season, by games played.
    Always available even when no lineup is posted.

    NOTE: the /season_stats endpoint filters by the SINGULAR `team_id` param
    (not team_ids[]). Passing the wrong param name makes the API ignore the
    filter and return unfiltered players — which put the wrong players on each
    team. We also defensively re-check each row's team id below."""
    try:
        resp = requests.get(f"{BDL_BASE}/season_stats", headers=_headers(),
                            params=[("season", season), ("team_id", team_id),
                                    ("per_page", 100)], timeout=20)
        if resp.status_code != 200:
            return []
    except Exception:
        return []
    players = []
    dropped_wrong_team = 0
    for s in resp.json().get("data", []):
        gp = s.get("batting_gp") or 0
        if gp < 1:
            continue
        player = s.get("player") or {}
        pid = player.get("id")
        if pid is None:
            continue
        name = player.get("full_name") or \
            (f'{player.get("first_name","")} {player.get("last_name","")}'.strip())
        # The corrected `team_id` query param (above) is what actually filters by
        # team. We record the player's own team id when present, but we do NOT
        # hard-drop on it here: in some responses player.team is absent, and a
        # hard filter would wrongly discard everyone and blank the box score.
        row_team_id = (player.get("team") or {}).get("id")
        if row_team_id is not None and row_team_id != team_id:
            dropped_wrong_team += 1
            continue
        players.append({"player_id": pid, "name": name or f"Player {pid}",
                        "gp": gp, "stat": s})

    # Safety net: if the team-id check dropped players but left us with nothing
    # (e.g. player.team is populated with an unexpected id shape), fall back to
    # trusting the API's own team_id filter and keep the raw batters.
    if not players and dropped_wrong_team:
        for s in resp.json().get("data", []):
            gp = s.get("batting_gp") or 0
            if gp < 1:
                continue
            player = s.get("player") or {}
            pid = player.get("id")
            if pid is None:
                continue
            name = player.get("full_name") or \
                (f'{player.get("first_name","")} {player.get("last_name","")}'.strip())
            players.append({"player_id": pid, "name": name or f"Player {pid}",
                            "gp": gp, "stat": s})
    players.sort(key=lambda p: p["gp"], reverse=True)
    if not players:
        print(f"    box score: team {team_id} returned no batters "
              f"(season_stats empty for season {season}?)")
    return players[:limit]


def _batter_expected_line(stat, gp, opp_factor):
    """Per-game expected batting line from season totals, scaled by a matchup
    factor (opposing starter quality x park). Returns expected decimals."""
    if not gp:
        return None

    def per_game(key):
        return (stat.get(key, 0) or 0) / gp

    return {
        "AB": round(per_game("batting_ab"), 1),
        "H":  round(per_game("batting_h") * opp_factor, 2),
        "R":  round(per_game("batting_r") * opp_factor, 2),
        "RBI": round(per_game("batting_rbi") * opp_factor, 2),
        "HR": round(per_game("batting_hr") * opp_factor, 2),
        "BB": round(per_game("batting_bb"), 2),
        "K":  round(per_game("batting_so"), 2),
    }


def _pitcher_expected_line(stat, opp_factor):
    """Expected start line for a probable pitcher from season rates."""
    gs = stat.get("pitching_gs") or 0
    ip = stat.get("pitching_ip") or 0
    if not gs or not ip:
        return None
    ip_per_start = ip / gs
    k_per_ip = (stat.get("pitching_k", 0) or 0) / ip
    bb_per_ip = (stat.get("pitching_bb", 0) or 0) / ip
    era = stat.get("pitching_era")
    try:
        era = float(era)
    except (TypeError, ValueError):
        era = 4.5
    exp_er = (era / 9.0) * ip_per_start * opp_factor
    return {
        "IP": round(ip_per_start, 1),
        "H":  round((stat.get("pitching_h", 0) or 0) / gs, 1),
        "ER": round(exp_er, 2),
        "K":  round(k_per_ip * ip_per_start, 1),
        "BB": round(bb_per_ip * ip_per_start, 1),
    }


def build_boxscore(game_id, home_team_id, away_team_id, season,
                   home_park_factor=100, home_sp_era=4.5, away_sp_era=4.5,
                   get_season_rates=None):
    """
    Build an expected-value box score for one game. Returns a dict with per-team
    batter lines, pitcher lines, and expected team runs. Degrades to {} if data
    isn't available. `get_season_rates` is passed in from mlb_props to avoid a
    duplicate season-stats fetch where possible.
    """
    result = {"available": False, "lineup_source": None, "home": {}, "away": {}}

    # --- lineups (projected -> season-regular fallback) ------------------
    home_lineup = get_lineup(game_id, home_team_id) if game_id else []
    away_lineup = get_lineup(game_id, away_team_id) if game_id else []
    source = "projected lineup"
    if not home_lineup:
        home_lineup = get_team_regulars(home_team_id, season)
        source = "season regulars"
    if not away_lineup:
        away_lineup = get_team_regulars(away_team_id, season)
        source = "season regulars"
    if not home_lineup and not away_lineup:
        return result

    # matchup factors: a batter faces the OPPOSING starter, so home batters are
    # scaled by the away starter's quality, and vice versa. Park nudges both.
    park = home_park_factor / 100.0
    away_sp_factor = _era_to_factor(away_sp_era)   # affects HOME batters
    home_sp_factor = _era_to_factor(home_sp_era)   # affects AWAY batters
    home_bat_factor = away_sp_factor * park
    away_bat_factor = home_sp_factor * park

    # We need full batting/pitching stat rows. If a rates provider wasn't given,
    # fetch stats for the lineup players directly.
    def stats_for(lineup):
        return {p["player_id"]: p.get("stat") for p in lineup if p.get("stat")}

    home_stats = stats_for(home_lineup)
    away_stats = stats_for(away_lineup)
    # If lineups came from /lineups (no stat rows attached), fetch them.
    missing = [p["player_id"] for p in (home_lineup + away_lineup)
               if not p.get("stat")]
    if missing:
        fetched = _fetch_stats(missing, season)
        home_stats.update({k: v for k, v in fetched.items()
                           if k in [p["player_id"] for p in home_lineup]})
        away_stats.update({k: v for k, v in fetched.items()
                           if k in [p["player_id"] for p in away_lineup]})

    result["home"]["batters"] = _team_batters(home_lineup, home_stats, home_bat_factor)
    result["away"]["batters"] = _team_batters(away_lineup, away_stats, away_bat_factor)

    result["home"]["runs"] = round(sum(b["R"] for b in result["home"]["batters"]) or 0, 1) \
        if result["home"]["batters"] else None
    result["away"]["runs"] = round(sum(b["R"] for b in result["away"]["batters"]) or 0, 1) \
        if result["away"]["batters"] else None

    result["lineup_source"] = source
    result["available"] = bool(result["home"]["batters"] or result["away"]["batters"])
    return result


def _team_batters(lineup, stats, factor):
    out = []
    for p in lineup:
        stat = stats.get(p["player_id"])
        if not stat:
            continue
        gp = stat.get("batting_gp") or 0
        line = _batter_expected_line(stat, gp, factor)
        if line:
            out.append({"name": p["name"], **line})
    return out


def _fetch_stats(player_ids, season):
    """Fetch full season stat rows for a set of players (batch of 100)."""
    out = {}
    ids = list(player_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        params = [("season", season), ("per_page", 100)]
        params += [("player_ids[]", pid) for pid in chunk]
        try:
            resp = requests.get(f"{BDL_BASE}/season_stats", headers=_headers(),
                                params=params, timeout=20)
            if resp.status_code != 200:
                continue
            for s in resp.json().get("data", []):
                pid = (s.get("player") or {}).get("id")
                if pid is not None:
                    out[pid] = s
        except Exception:
            continue
    return out


def _era_to_factor(era):
    """Turn an opposing-starter ERA into a batting multiplier: a good starter
    (low ERA) suppresses the batter's expected output (<1), a poor one lifts it
    (>1). Centered on a 4.5 league-average ERA and clamped to a sane range."""
    try:
        era = float(era)
    except (TypeError, ValueError):
        era = 4.5
    factor = era / 4.5
    return max(0.7, min(factor, 1.4))
