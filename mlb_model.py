"""
A simple, transparent win-probability model (built on the sabermetric log5
method) plus a lightweight backtest against recently completed games.

IMPORTANT: this is a starting point, not a validated betting edge.
Sportsbook lines already price in almost everything here (records, starters,
park). A model like this is a way to form your own opinion and sanity-check
lines you see — not a guarantee of profit. Bet only what you can afford to
lose, and treat "confidence" labels as relative, not calibrated probabilities.
"""
from datetime import datetime, timedelta
from mlb_data import get_schedule, get_standings

LEAGUE_AVG_ERA = 4.20  # rough MLB league-average starter ERA; update yearly
HOME_FIELD_ADV = 0.035  # ~3.5% home win-prob bump, standard rule of thumb


def log5(pa, pb):
    denom = pa + pb - 2 * pa * pb
    if denom == 0:
        return 0.5
    return (pa - pa * pb) / denom


def pitcher_factor(season_era, recent_era=None):
    """Turn ERA into a 0-1 'quality' score, blending season ERA with the
    pitcher's last-3-starts ERA (recent form) when available."""
    era = season_era if season_era is not None else LEAGUE_AVG_ERA
    if recent_era is not None:
        era = 0.6 * era + 0.4 * recent_era
    quality = LEAGUE_AVG_ERA / max(era, 1.5)
    return max(0.25, min(quality, 1.75)) / 2


def game_home_win_prob(home_win_pct, away_win_pct, home_sp_era, away_sp_era,
                        park_factor, home_recent_era=None, away_recent_era=None,
                        home_bullpen_ip=0.0, away_bullpen_ip=0.0):
    p_home = pitcher_factor(home_sp_era, home_recent_era)
    p_away = pitcher_factor(away_sp_era, away_recent_era)

    matchup_prob = log5(p_home, p_away)
    record_prob = log5(home_win_pct, away_win_pct)

    # Weighted toward the starting-pitcher matchup, since one starter is a
    # big share of a single game's outcome; team record fills in the rest.
    combined = 0.55 * matchup_prob + 0.45 * record_prob
    combined += HOME_FIELD_ADV
    combined += (park_factor - 100) * 0.0005  # small nudge, not a big lever
    combined -= home_bullpen_ip * 0.003        # tired home bullpen -> small penalty
    combined += away_bullpen_ip * 0.003        # tired away bullpen -> small home boost

    return max(0.05, min(combined, 0.95))


def confidence_label(signal):
    """signal is either |win_prob - 0.5| (winner mode) or the model-vs-market
    edge as a fraction (value mode). Thresholds are tuned so a few points of
    real edge reads as Medium/High — meaningful value is rare."""
    if signal >= 0.06:
        return "High"
    if signal >= 0.03:
        return "Medium"
    return "Low"


def backtest(season, days_back=14):
    """
    POINT-IN-TIME backtest. For each past game day, it pulls the standings as
    they stood the day BEFORE (via the Stats API `date` param), so predictions
    use only information available at the time — no end-of-season leakage.

    It measures straight-up winner accuracy, plus how the model does on the
    subset of games where it was most confident. It still does NOT measure
    profit vs. the betting market: free historical closing-line data isn't
    reliably available, so treat this as model validation, not an ROI figure.
    """
    from mlb_data import get_standings, get_schedule  # local import avoids cycle
    today = datetime.now()
    correct, total = 0, 0
    conf_correct, conf_total = 0, 0
    standings_cache = {}

    for i in range(1, days_back + 1):
        game_day = today - timedelta(days=i)
        date_str = game_day.strftime("%Y-%m-%d")
        prior_str = (game_day - timedelta(days=1)).strftime("%Y-%m-%d")

        if prior_str not in standings_cache:
            try:
                standings_cache[prior_str] = get_standings(season, as_of_date=prior_str)
            except Exception:
                standings_cache[prior_str] = {}
        standings = standings_cache[prior_str]

        for g in get_schedule(date_str):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            teams = g.get("teams", {})
            home, away = teams.get("home", {}), teams.get("away", {})
            home_name = home.get("team", {}).get("name")
            away_name = away.get("team", {}).get("name")
            home_score, away_score = home.get("score"), away.get("score")
            if home_score is None or away_score is None:
                continue

            home_wp = standings.get(home_name, {}).get("win_pct", 0.5)
            away_wp = standings.get(away_name, {}).get("win_pct", 0.5)
            prob_home = log5(home_wp, away_wp) + HOME_FIELD_ADV
            predicted_home_win = prob_home >= 0.5
            actual_home_win = home_score > away_score
            hit = int(predicted_home_win == actual_home_win)

            correct += hit
            total += 1
            if abs(prob_home - 0.5) >= 0.08:  # "confident" games only
                conf_correct += hit
                conf_total += 1

    return {
        "games_checked": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "confident_games": conf_total,
        "confident_accuracy": round(conf_correct / conf_total, 3) if conf_total else None,
        "method": "point-in-time (standings as of day before each game)",
    }
