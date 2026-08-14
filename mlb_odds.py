"""
BALLDONTLIE MLB API integration — real betting odds.

This is the piece the MLB Stats API can't provide: actual sportsbook lines.
With live moneyline odds we can compare the model's win probability against
the market's implied probability and compute an EDGE — the core of any real
betting model. Without this, we were only predicting winners; with it, we can
ask the only question that matters for betting: is the price wrong?

API key: read from the BALLDONTLIE_API_KEY environment variable.
Set it once in your shell (do NOT hardcode it):
    export BALLDONTLIE_API_KEY="your-key-here"

Docs/spec: https://www.balldontlie.io/openapi/mlb.yml
"""
import os
import requests

BDL_BASE = "https://api.balldontlie.io/mlb/v1"


def _headers():
    key = os.environ.get("BALLDONTLIE_API_KEY")
    if not key:
        raise RuntimeError(
            "BALLDONTLIE_API_KEY not set. Run:  export BALLDONTLIE_API_KEY='your-key'"
        )
    return {"Authorization": key}


def american_to_implied_prob(odds):
    """Convert American moneyline odds to implied win probability (0-1).
    Note: this includes the sportsbook's vig; see devig_two_way() to remove it."""
    if odds is None:
        return None
    odds = int(odds)
    if odds < 0:
        return (-odds) / ((-odds) + 100)
    return 100 / (odds + 100)


def devig_two_way(home_odds, away_odds):
    """Remove the bookmaker's margin from a two-way moneyline market, returning
    (home_fair_prob, away_fair_prob). The raw implied probs sum to >1 (the vig);
    normalizing gives the book's true estimate to compare the model against."""
    ph = american_to_implied_prob(home_odds)
    pa = american_to_implied_prob(away_odds)
    if ph is None or pa is None:
        return None, None
    total = ph + pa
    if total == 0:
        return None, None
    return ph / total, pa / total


def get_games_for_date(date_str):
    """Return balldontlie MLB games for a date. Used to map games to their odds,
    since the odds endpoint keys on balldontlie's game_id."""
    resp = requests.get(f"{BDL_BASE}/games", headers=_headers(),
                        params={"dates[]": date_str, "per_page": 100}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_odds_for_date(date_str):
    """Return moneyline/spread/total odds for all games on a date, grouped by
    balldontlie game_id. Averages moneylines across sportsbooks (vendors) so a
    single book's outlier doesn't skew the market estimate.

    Handles pagination: a full slate (many games x several books) can exceed one
    page of 100 rows, which would otherwise silently drop games' odds."""
    rows = []
    cursor = None
    for _ in range(20):  # safety cap on pages
        params = {"dates": date_str, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor
        resp = requests.get(f"{BDL_BASE}/odds", headers=_headers(),
                            params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("data", []))
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break

    def to_float(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    by_game = {}
    for o in rows:
        gid = o.get("game_id")
        if gid is None:
            continue
        by_game.setdefault(gid, {"home_ml": [], "away_ml": [], "total": []})
        if o.get("moneyline_home_odds") is not None:
            by_game[gid]["home_ml"].append(o["moneyline_home_odds"])
        if o.get("moneyline_away_odds") is not None:
            by_game[gid]["away_ml"].append(o["moneyline_away_odds"])
        tv = to_float(o.get("total_value"))
        if tv is not None:
            by_game[gid]["total"].append(tv)

    consensus = {}
    for gid, v in by_game.items():
        home_ml = round(sum(v["home_ml"]) / len(v["home_ml"])) if v["home_ml"] else None
        away_ml = round(sum(v["away_ml"]) / len(v["away_ml"])) if v["away_ml"] else None
        total = round(sum(v["total"]) / len(v["total"]), 1) if v["total"] else None
        fair_home, fair_away = devig_two_way(home_ml, away_ml)
        consensus[gid] = {
            "home_ml": home_ml, "away_ml": away_ml, "total": total,
            "market_home_prob": fair_home, "market_away_prob": fair_away,
            "num_books": max(len(v["home_ml"]), len(v["away_ml"])),
        }
    return consensus


def _canonical_team(name):
    """Reduce any team name format ('New York Yankees', 'Yankees', 'NYY',
    'NY Yankees') to a single canonical key by matching the MLB nickname.
    This avoids the 'Red Sox' vs 'White Sox' last-word collision that a naive
    match hits (both end in 'Sox')."""
    n = (name or "").lower().replace("é", "e").strip()
    # Order matters: check multi-word nicknames before single words.
    nicknames = [
        "red sox", "white sox", "blue jays", "diamondbacks",
        "yankees", "mets", "dodgers", "giants", "padres", "angels", "athletics",
        "mariners", "rangers", "astros", "royals", "twins", "guardians",
        "tigers", "brewers", "cubs", "cardinals", "pirates", "reds", "braves",
        "marlins", "nationals", "phillies", "rockies", "orioles", "rays",
    ]
    for nick in nicknames:
        if nick in n:
            return nick
    # Fall back to the raw normalized string if no nickname matched.
    return n


def _bdl_team_names(g, side):
    """Pull whatever team-name fields a balldontlie game exposes for one side."""
    flat = g.get(f"{side}_team_name")
    obj = g.get(f"{side}_team", {}) or {}
    return [flat, obj.get("name"), obj.get("display_name"), obj.get("location")]


def _game_matches(g, home_name, away_name):
    home_key, away_key = _canonical_team(home_name), _canonical_team(away_name)
    bdl_home = {_canonical_team(x) for x in _bdl_team_names(g, "home") if x}
    bdl_away = {_canonical_team(x) for x in _bdl_team_names(g, "away") if x}
    return home_key in bdl_home and away_key in bdl_away


def match_odds_to_game(bdl_games, consensus, home_team_name, away_team_name):
    """Find the balldontlie game whose teams match a Stats API game and return
    its consensus odds, or None if no match / no odds."""
    for g in bdl_games:
        if _game_matches(g, home_team_name, away_team_name):
            return consensus.get(g.get("id"))
    return None


def edge(model_prob, market_prob):
    """Model probability minus market (de-vigged) probability. Positive = the
    model sees more value than the market is pricing. This is the bet signal."""
    if model_prob is None or market_prob is None:
        return None
    return round(model_prob - market_prob, 3)
