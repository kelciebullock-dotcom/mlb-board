"""
Line-shopping and discrepancy scanning — the honest retail edges.

This module does NOT try to predict games better than the market. It does two
things that are genuinely useful to a retail bettor and don't require beating
sharp modelers:

1. BEST PRICE (line shopping): for each game and side, find which sportsbook
   offers the best number. Betting the best available price instead of a random
   book's price is the one edge that is unambiguously real and repeatable —
   over a season it measurably improves results, because you stop leaving money
   on the table. No prediction required.

2. DISCREPANCY SCAN: flag games where one book's price sits well off the
   consensus of the others. A book far from the market MAY be slow to update
   (a "possibly stale" line) — or may simply have a different opinion, or be
   about to get steamed. This is a place to LOOK, not a signal to bet.

Honest limits, stated plainly:
- balldontlie's odds are periodic, not tick-by-tick. By the time a price
  reaches you it may already be the updated one at sharp books. This catches
  SLOW books, and slow books limit/ban winning bettors fastest — so any edge
  here is real but short-lived per account.
- "Off-market" is not "wrong." Sometimes the outlier book is right and the
  consensus is stale. Treat flags as prompts to check the game, never as picks.
- We show each book's `updated_at` age so you can see how fresh the price is.

Vendors covered by /odds: betmgm, betrivers, caesars, draftkings, fanatics,
fanduel (whichever the feed returns for a given game).
"""
import os
from datetime import datetime, timezone
import requests

BDL_BASE = "https://api.balldontlie.io/mlb/v1"


def _headers():
    key = os.environ.get("BALLDONTLIE_API_KEY")
    if not key:
        raise RuntimeError("BALLDONTLIE_API_KEY not set.")
    return {"Authorization": key}


def american_to_implied(odds):
    if odds is None:
        return None
    odds = int(odds)
    return (-odds) / ((-odds) + 100) if odds < 0 else 100 / (odds + 100)


def better_price(a, b):
    """Return the better American moneyline for a bettor (higher payout).
    +150 is better than +120; -110 is better than -130."""
    if a is None:
        return b
    if b is None:
        return a
    # Lower implied probability = better price for the bettor.
    return a if american_to_implied(a) < american_to_implied(b) else b


def _age_minutes(updated_at):
    if not updated_at:
        return None
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return round(delta.total_seconds() / 60)
    except Exception:
        return None


def get_all_book_odds(date_str):
    """Return per-book odds rows grouped by game_id (all vendors, not averaged).
    { game_id: [ {vendor, home_ml, away_ml, total, over, under, age_min}, ... ] }"""
    rows = []
    cursor = None
    for _ in range(20):
        params = {"dates": date_str, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor
        resp = requests.get(f"{BDL_BASE}/odds", headers=_headers(), params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("data", []))
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break

    by_game = {}
    for o in rows:
        gid = o.get("game_id")
        if gid is None:
            continue
        by_game.setdefault(gid, []).append({
            "vendor": o.get("vendor"),
            "home_ml": o.get("moneyline_home_odds"),
            "away_ml": o.get("moneyline_away_odds"),
            "total": o.get("total_value"),
            "over": o.get("total_over_odds"),
            "under": o.get("total_under_odds"),
            "age_min": _age_minutes(o.get("updated_at")),
        })
    return by_game


def analyze_game_books(book_rows):
    """Given all books' rows for one game, compute best price per side and a
    discrepancy flag. Returns a dict summarizing the shopping picture."""
    home_books = [(b["vendor"], b["home_ml"]) for b in book_rows if b["home_ml"] is not None]
    away_books = [(b["vendor"], b["away_ml"]) for b in book_rows if b["away_ml"] is not None]

    def best(side_books):
        if not side_books:
            return None, None
        best_v, best_o = side_books[0]
        for v, o in side_books[1:]:
            if better_price(o, best_o) == o and o != best_o:
                best_v, best_o = v, o
        return best_v, best_o

    best_home_v, best_home_o = best(home_books)
    best_away_v, best_away_o = best(away_books)

    # Discrepancy: how far is the best price from the median implied prob?
    def spread_flag(side_books):
        probs = [american_to_implied(o) for _, o in side_books if o is not None]
        if len(probs) < 3:
            return None  # need a few books to call something an outlier
        probs_sorted = sorted(probs)
        median = probs_sorted[len(probs_sorted) // 2]
        best_prob = min(probs)  # best price = lowest implied prob
        gap = median - best_prob  # positive gap = best book is cheaper than market
        return round(gap * 100, 1) if gap >= 0.02 else None  # >=2 pts to flag

    home_gap = spread_flag(home_books)
    away_gap = spread_flag(away_books)

    return {
        "num_books": len(book_rows),
        "best_home_book": best_home_v, "best_home_ml": best_home_o,
        "best_away_book": best_away_v, "best_away_ml": best_away_o,
        "home_discrepancy_pts": home_gap,
        "away_discrepancy_pts": away_gap,
        "min_age_min": min([b["age_min"] for b in book_rows if b["age_min"] is not None], default=None),
        "all_books": book_rows,
    }


def get_injuries(team_ids):
    """Current injuries for a set of teams, for the scratch/availability flag.
    Returns { team_id: [ {name, status, detail}, ... ] }."""
    out = {}
    if not team_ids:
        return out
    for tid in team_ids:
        try:
            resp = requests.get(f"{BDL_BASE}/player_injuries", headers=_headers(),
                                params=[("team_ids[]", tid), ("per_page", 100)], timeout=20)
            if resp.status_code != 200:
                continue
            items = []
            for inj in resp.json().get("data", []):
                player = inj.get("player") or {}
                items.append({
                    "name": player.get("full_name") or
                            f'{player.get("first_name","")} {player.get("last_name","")}'.strip(),
                    "status": inj.get("status"),
                    "detail": inj.get("detail") or inj.get("short_comment"),
                })
            if items:
                out[tid] = items
        except Exception:
            continue
    return out
