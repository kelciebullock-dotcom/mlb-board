# MLB Tonight — Model Board

Pulls tonight's MLB slate from free public data, scores each game with a
transparent win-probability model, compares it to live sportsbook odds to find
value, adds player-prop leans, and produces both a CSV (for Excel) and a visual
HTML dashboard with recommended picks.

## Want the live daily URL?

See **[SETUP_GUIDE.md](SETUP_GUIDE.md)** — a step-by-step, no-experience-needed
walkthrough to put this online at your own `github.io` URL that rebuilds every
morning automatically. About 15 minutes, all in your browser.

The rest of this README explains how it works and how to run it locally.

## Setup

```bash
pip install -r requirements.txt

# Set your balldontlie API key (needed for betting odds).
# Do NOT hardcode it in the source.
export BALLDONTLIE_API_KEY="your-key-here"

python generate_dashboard.py
```

If the key isn't set or the odds call fails, the script still runs — it just
falls back to straight-winner picks instead of value picks.

Outputs land in the same folder:
- `mlb_tonight_edates.csv` — full data + picks + odds/edge, opens in Excel
- `mlb_dashboard.html` — open in any browser

Run it once each morning (or wire it to cron / Task Scheduler) to refresh.

> **Security note:** treat your API key like a password. If it's ever been
> pasted somewhere shared (chat, email, a commit), rotate it in your
> balldontlie account. This project reads it only from the environment.

## Data sources (all free, no API key)

All endpoints below were verified against the public MLB Stats API
documentation ([pseudo-r/Public-MLB-API](https://github.com/pseudo-r/Public-MLB-API)).
The API requires no key, token, or registration.

| Input | Source | Endpoint |
|---|---|---|
| Schedule, probable pitchers, teams, venue | MLB Stats API | `/schedule?hydrate=probablePitcher,team,venue` |
| Team records / win% (incl. **as-of-date**) | MLB Stats API | `/standings?date=YYYY-MM-DD` |
| Season ERA + last-3-starts ERA | MLB Stats API | `/people/{id}/stats?stats=season\|gameLog` |
| Recent bullpen usage (relief IP, 3 days) | MLB Stats API | `/game/{gamePk}/boxscore` |
| Park factors | Baseball Savant (Statcast) | `/leaderboard/statcast-park-factors?csv=true` |
| **Betting odds (moneyline / total)** | **balldontlie MLB API** | `/mlb/v1/odds` |

The balldontlie API needs a free API key (set via `BALLDONTLIE_API_KEY`). It
provides the one thing the MLB Stats API can't: real sportsbook lines. Its
`/odds/opening` endpoint also exposes historical opening lines, but that
requires balldontlie's paid "GOAT" tier — see the roadmap note below.

## Value picks — model vs. market

This is what makes it a betting model rather than a winner predictor. For each
game the pipeline:

1. computes the model's win probability for each side (log5 + pitching + park + bullpen),
2. pulls the moneyline from balldontlie, averaged across sportsbooks,
3. **de-vigs** the line (removes the book's margin) to get the market's fair probability,
4. computes **edge** = model probability − market probability.

The recommended pick is the side with the largest positive edge. If neither
side shows positive edge, the pick is **"No value (pass)"** — declining to bet
is a feature, not a gap. The dashboard shows the moneylines, per-side edge, and
a green edge badge on value picks.

Park factors are now **fetched automatically at runtime** from Baseball Savant's
free Statcast park-factors leaderboard. If that request fails for any reason,
the code falls back to a built-in static table so the pipeline never breaks —
you'll see a message in the console when the fallback is used.

## How the model works

`mlb_model.py` uses **log5** (a standard sabermetric method) to combine:
- starting-pitcher quality (season ERA blended with last-3-starts form),
- team win%,
- home-field advantage,
- a small park-factor nudge,
- a small bullpen-fatigue adjustment.

It's intentionally simple and readable so you can tune the weights yourself.

## The backtest (now point-in-time)

`backtest()` replays the last 14 days of completed games. For each game day it
pulls the standings **as they stood the day before** (using the Stats API's
`date` parameter), so every prediction uses only information available at the
time — no end-of-season record leaking backward into past picks. This is a
genuine walk-forward validation, not the earlier shortcut.

It reports two numbers: overall straight-up winner accuracy, and accuracy on the
subset of games where the model was most confident (the games you'd actually
lean on). Both show in the dashboard header.

It still does **not** measure profit against the betting market — free,
reliable historical closing-line data isn't in the current pull — so treat
these as model-validation figures, not an ROI. Beating ~50% on winners is easy
(favorites win more than half the time); beating the *price* is the hard part
this backtest can't yet speak to.

### Roadmap: a real profitability backtest

Now that odds are wired in, the missing piece is *historical* odds. balldontlie's
`/mlb/v1/odds/opening` endpoint returns opening lines for past games (GOAT tier).
With that, the backtest could replay each past slate, compare the model's edge
to the opening line, "place" flat-stake bets on positive-edge sides, and report
actual units won/lost and closing-line value. That — not winner accuracy — is
the number that tells you whether the model beats the market. The code is
structured so this slots into `backtest()` without disturbing the daily flow.

### Files
- `mlb_data.py` — MLB Stats API + park factors
- `mlb_odds.py` — balldontlie odds, de-vig, and edge math
- `mlb_model.py` — win-probability model + point-in-time backtest
- `generate_dashboard.py` — ties it together, writes CSV + HTML

## Honest limitations — please read

- This is **not a proven betting edge.** Sportsbook lines already price in
  records, starters, park, and bullpen state. Beating the market consistently
  is very hard, and nothing here is calibrated against real odds.
- "Confidence" labels are *relative* (how far from a coin flip the model lands),
  not true probabilities.
- The picks are model output for your own research — not advice to place a bet.
- Only wager what you can afford to lose. If betting stops being fun or you're
  chasing losses, stop. **National Problem Gambling Helpline: 1-800-522-4700.**
