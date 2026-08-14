"""
Player prop predictions from the balldontlie MLB API.

Pulls live player prop lines (/mlb/v1/odds/player_props) — strikeouts, hits,
total bases, home runs, etc. — for each game, and pairs them with a simple
model estimate built from the player's season rate stats (/mlb/v1/season_stats).

A note on honesty, because props deserve it: props are HARDER to beat than game
lines. The lines are sharp, the samples are small, and a naive season-rate model
like this one will look confident and be wrong a lot. Treat every "lean" here as
a conversation starter for your own research, not a signal. There is no backtest
behind these prop leans yet, so they carry less weight than the game model.

API key: read from BALLDONTLIE_API_KEY (see mlb_odds.py).
"""
import os
import requests

BDL_BASE = "https://api.balldontlie.io/mlb/v1"

# Prop types we can form a rough season-rate estimate for, and the per-game
# stat we derive it from. Anything not in here is still shown (line only),
# just without a model estimate or lean.
SUPPORTED_PROPS = {
    "pitcher_strikeouts": "k_per_start",
    "hits": "hits_per_game",
    "total_bases": "tb_per_game",
    "home_runs": "hr_per_game",
    "runs_scored": "runs_per_game",
    "rbis": "rbi_per_game",
}


def _headers():
    key = os.environ.get("BALLDONTLIE_API_KEY")
    if not key:
        raise RuntimeError("BALLDONTLIE_API_KEY not set.")
    return {"Authorization": key}


def get_player_props(game_id):
    """All live player props for one game, as returned by balldontlie."""
    resp = requests.get(f"{BDL_BASE}/odds/player_props", headers=_headers(),
                        params={"game_id": game_id}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_season_rates(player_ids, season):
    """Fetch season stats for a set of players and reduce to simple per-game
    (or per-start) rates we can compare against prop lines. Returns
    {player_id: {rate_key: value}}. Also returns a {player_id: full_name} map
    so callers can show real names instead of numeric IDs."""
    if not player_ids:
        return {}, {}
    rates = {}
    names = {}
    # balldontlie paginates; request in one page up to 100 ids at a time.
    ids = list(player_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        # NOTE: balldontlie expects repeated player_ids[]=1&player_ids[]=2 style.
        # requests encodes a list value under a key ending in [] exactly that way.
        params = [("season", season), ("per_page", 100)]
        params += [("player_ids[]", pid) for pid in chunk]
        resp = requests.get(f"{BDL_BASE}/season_stats", headers=_headers(),
                            params=params, timeout=20)
        if resp.status_code != 200:
            continue
        for s in resp.json().get("data", []):
            player = s.get("player", {})
            pid = player.get("id")
            if pid is None:
                continue
            full = player.get("full_name") or \
                (f'{player.get("first_name","")} {player.get("last_name","")}'.strip())
            if full:
                names[pid] = full
            gp = s.get("batting_gp") or 0
            gs = s.get("pitching_gs") or 0
            rates[pid] = {
                "k_per_start": (s.get("pitching_k", 0) / gs) if gs else None,
                "hits_per_game": (s.get("batting_h", 0) / gp) if gp else None,
                "tb_per_game": (s.get("batting_tb", 0) / gp) if gp else None,
                "hr_per_game": (s.get("batting_hr", 0) / gp) if gp else None,
                "runs_per_game": (s.get("batting_r", 0) / gp) if gp else None,
                "rbi_per_game": (s.get("batting_rbi", 0) / gp) if gp else None,
            }
    return rates, names


def get_player_names(player_ids):
    """Authoritative name lookup from /players. Season stats miss players with
    no stats yet (call-ups, pitchers with no prop-relevant batting line), so we
    use this to fill any names those didn't resolve. Returns {id: full_name}."""
    if not player_ids:
        return {}
    names = {}
    ids = list(player_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        params = [("per_page", 100)] + [("player_ids[]", pid) for pid in chunk]
        resp = requests.get(f"{BDL_BASE}/players", headers=_headers(),
                            params=params, timeout=20)
        if resp.status_code != 200:
            continue
        for p in resp.json().get("data", []):
            pid = p.get("id")
            if pid is None:
                continue
            full = p.get("full_name") or \
                (f'{p.get("first_name","")} {p.get("last_name","")}'.strip())
            if full:
                names[pid] = full
    return names


def build_prop_leans(game_id, season, player_name_lookup=None):
    """For one game, return a list of prop rows: the line, the model's season-rate
    estimate, and a lean (over/under/—). Only props in SUPPORTED_PROPS get a lean;
    others are returned with the line only so you still see the market."""
    props = get_player_props(game_id)
    if not props:
        return []

    player_ids = {p.get("player_id") for p in props if p.get("player_id")}
    rates, names = get_season_rates(player_ids, season)

    # Fill any names the season-stats call didn't resolve, using /players.
    missing = [pid for pid in player_ids if pid not in names]
    if missing:
        names.update(get_player_names(missing))
    if player_name_lookup:
        # caller-supplied names win if provided
        names.update({k: v for k, v in player_name_lookup.items() if v})

    leans = []
    for p in props:
        prop_type = p.get("prop_type")
        line_raw = p.get("line_value")
        pid = p.get("player_id")
        try:
            line = float(line_raw) if line_raw is not None else None
        except (TypeError, ValueError):
            line = None

        estimate, lean, diff = None, "—", None
        rate_key = SUPPORTED_PROPS.get(prop_type)
        if rate_key and line is not None:
            est = rates.get(pid, {}).get(rate_key)
            if est is not None:
                estimate = round(est, 2)
                diff = round(est - line, 2)
                # Only lean when the gap is meaningful vs. the line, to avoid
                # calling a lean on essentially coin-flip differences.
                if abs(diff) >= max(0.15 * line, 0.3):
                    lean = "OVER" if diff > 0 else "UNDER"

        market = p.get("market") or {}
        leans.append({
            "player_id": pid,
            "player": names.get(pid) or (f"Player {pid}" if pid else "Unknown"),
            "prop_type": prop_type,
            "line": line,
            "vendor": p.get("vendor"),
            "over_odds": market.get("over_odds"),
            "under_odds": market.get("under_odds"),
            "odds": market.get("odds"),  # milestone-type markets use this
            "model_estimate": estimate,
            "diff": diff,
            "lean": lean,
        })

    # Surface the strongest leans first; lines-only rows sink to the bottom.
    leans.sort(key=lambda r: (r["lean"] == "—", -abs(r["diff"] or 0)))
    return leans

