#!/usr/bin/env python3
"""
Run this once a day (e.g. via cron) to refresh tonight's slate.

Outputs:
  - mlb_tonight_edates.csv   (full data + model picks, for Excel)
  - mlb_dashboard.html       (visual dashboard, open in any browser)

Usage:
  pip install -r requirements.txt
  python generate_dashboard.py
"""
import csv
import json
import os
from datetime import datetime

from mlb_data import (
    get_today_eastern_date, get_schedule, get_standings, get_season_era,
    get_last_n_starts_era, get_bullpen_recent_ip, load_park_factors,
    get_park_factor, EASTERN,
)
from mlb_model import game_home_win_prob, confidence_label, backtest

# When OUTPUT_DIR is set (e.g. by the GitHub Action -> "site"), files are
# written there and the dashboard is named index.html so GitHub Pages serves it
# at the site root. Locally, with OUTPUT_DIR unset, it writes to the current dir.
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", ".")
CSV_FILE = os.path.join(OUTPUT_DIR, "mlb_tonight_edates.csv")
HTML_FILE = os.path.join(OUTPUT_DIR, "index.html" if OUTPUT_DIR != "." else "mlb_dashboard.html")


def format_first_pitch(game_date_utc):
    if not game_date_utc:
        return "TBD"
    dt = datetime.fromisoformat(game_date_utc.replace("Z", "+00:00")).astimezone(EASTERN)
    return dt.strftime("%I:%M %p ET").lstrip("0")


def find_bdl_game_id(bdl_games, home_team_name, away_team_name):
    """Return the balldontlie game id whose teams match a Stats API game, so we
    can request that game's player props. Uses the same canonical-name matcher
    as the odds, so 'New York Yankees' resolves to balldontlie's 'Yankees' and
    'Red Sox' never collides with 'White Sox'."""
    try:
        from mlb_odds import _game_matches
        for g in bdl_games:
            if _game_matches(g, home_team_name, away_team_name):
                return g.get("id")
    except Exception:
        pass
    return None


def build_games(date_str, season, odds_available=False, bdl_games=None,
                consensus=None, props_fn=None):
    print(f"Fetching schedule for {date_str}...")
    schedule = get_schedule(date_str)
    standings = get_standings(season)

    rows = []
    for i, g in enumerate(schedule, 1):
        teams = g.get("teams", {})
        home, away = teams.get("home", {}), teams.get("away", {})
        home_team = home.get("team", {})
        away_team = away.get("team", {})
        home_name, away_name = home_team.get("name", "TBD"), away_team.get("name", "TBD")
        venue = g.get("venue", {}).get("name", "")
        park_factor = get_park_factor(venue)

        home_rec = standings.get(home_name, {"wins": 0, "losses": 0, "win_pct": 0.5})
        away_rec = standings.get(away_name, {"wins": 0, "losses": 0, "win_pct": 0.5})

        home_p, away_p = home.get("probablePitcher", {}), away.get("probablePitcher", {})
        home_p_name = home_p.get("fullName", "TBD")
        away_p_name = away_p.get("fullName", "TBD")

        print(f"  [{i}/{len(schedule)}] {away_name} @ {home_name} - pulling pitcher/bullpen data...")
        home_era = get_season_era(home_p.get("id"), season)
        away_era = get_season_era(away_p.get("id"), season)
        home_recent = get_last_n_starts_era(home_p.get("id"), season, n=3)
        away_recent = get_last_n_starts_era(away_p.get("id"), season, n=3)
        home_bp_ip = get_bullpen_recent_ip(home_team.get("id"), date_str, days=3) if home_team.get("id") else 0.0
        away_bp_ip = get_bullpen_recent_ip(away_team.get("id"), date_str, days=3) if away_team.get("id") else 0.0

        home_win_prob = game_home_win_prob(
            home_rec["win_pct"], away_rec["win_pct"], home_era, away_era, park_factor,
            home_recent_era=home_recent, away_recent_era=away_recent,
            home_bullpen_ip=home_bp_ip, away_bullpen_ip=away_bp_ip,
        )
        away_win_prob = 1 - home_win_prob

        # --- Odds / edge (balldontlie) ---------------------------------------
        home_ml = away_ml = total = None
        mkt_home = mkt_away = home_edge = away_edge = None
        pick = home_name if home_win_prob >= away_win_prob else away_name
        pick_type = "winner"  # falls back to straight winner if no odds
        conf_source = abs(home_win_prob - 0.5)

        if odds_available:
            odds = match_odds_to_game(bdl_games or [], consensus or {}, home_name, away_name)
            if odds:
                home_ml, away_ml, total = odds["home_ml"], odds["away_ml"], odds["total"]
                mkt_home, mkt_away = odds["market_home_prob"], odds["market_away_prob"]
                home_edge = edge(home_win_prob, mkt_home)
                away_edge = edge(away_win_prob, mkt_away)
                # Value pick: the side where the model most outpaces the market.
                if home_edge is not None and away_edge is not None:
                    if max(home_edge, away_edge) <= 0:
                        pick, pick_type = "No value (pass)", "pass"
                        conf_source = 0.0
                    elif home_edge >= away_edge:
                        pick, pick_type = home_name, "value"
                        conf_source = home_edge
                    else:
                        pick, pick_type = away_name, "value"
                        conf_source = away_edge

        # --- Player props (balldontlie) --------------------------------------
        prop_leans = []
        if props_fn is not None:
            bdl_game_id = find_bdl_game_id(bdl_games or [], home_name, away_name)
            if bdl_game_id is not None:
                try:
                    prop_leans = props_fn(bdl_game_id, season)
                except Exception as e:
                    print(f"    props unavailable for this game ({e})")

        rows.append({
            "Date": date_str,
            "First Pitch (ET)": format_first_pitch(g.get("gameDate", "")),
            "Away Team": away_name, "Away Record": f'{away_rec["wins"]}-{away_rec["losses"]}',
            "Home Team": home_name, "Home Record": f'{home_rec["wins"]}-{home_rec["losses"]}',
            "Venue": venue, "Park Factor": park_factor,
            "Away SP": away_p_name, "Away SP Season ERA": away_era, "Away SP Last-3 ERA": away_recent,
            "Home SP": home_p_name, "Home SP Season ERA": home_era, "Home SP Last-3 ERA": home_recent,
            "Away Bullpen IP (3d)": away_bp_ip, "Home Bullpen IP (3d)": home_bp_ip,
            "Model Away Win%": round(away_win_prob * 100, 1),
            "Model Home Win%": round(home_win_prob * 100, 1),
            "Away ML": away_ml, "Home ML": home_ml, "O/U Total": total,
            "Market Away%": round(mkt_away * 100, 1) if mkt_away is not None else None,
            "Market Home%": round(mkt_home * 100, 1) if mkt_home is not None else None,
            "Away Edge": round(away_edge * 100, 1) if away_edge is not None else None,
            "Home Edge": round(home_edge * 100, 1) if home_edge is not None else None,
            "Recommended Pick": pick,
            "Pick Type": pick_type,
            "Confidence": confidence_label(conf_source),
            "Props": prop_leans,
        })
    return rows


def write_csv(rows, path):
    if not rows:
        return
    # Props are nested (a list per game); flatten them out of the CSV and into
    # a companion props CSV so Excel stays clean.
    game_fields = [k for k in rows[0].keys() if k != "Props"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=game_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} games to {path}")

    # Separate props file (one row per prop), only if any props exist.
    prop_rows = []
    for r in rows:
        for p in r.get("Props", []):
            prop_rows.append({
                "Game": f'{r["Away Team"]} @ {r["Home Team"]}',
                "Player": p.get("player"),
                "Prop": p.get("prop_type"),
                "Line": p.get("line"),
                "Model Est.": p.get("model_estimate"),
                "Lean": p.get("lean"),
                "Over Odds": p.get("over_odds"),
                "Under Odds": p.get("under_odds"),
                "Book": p.get("vendor"),
            })
    if prop_rows:
        props_path = path.replace(".csv", "_props.csv")
        with open(props_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(prop_rows[0].keys()))
            writer.writeheader()
            writer.writerows(prop_rows)
        print(f"Saved {len(prop_rows)} props to {props_path}")


def write_html(rows, backtest_result, date_str, path):
    data_json = json.dumps(rows)
    bt_json = json.dumps(backtest_result)
    updated = datetime.now(EASTERN).strftime("%b %-d, %Y at %-I:%M %p ET") \
        if os.name != "nt" else datetime.now(EASTERN).strftime("%b %d, %Y at %I:%M %p ET")
    html = HTML_TEMPLATE.replace("__GAMES_JSON__", data_json) \
                         .replace("__BACKTEST_JSON__", bt_json) \
                         .replace("__DATE__", date_str) \
                         .replace("__UPDATED__", updated)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved dashboard to {path}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MLB Tonight — Model Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0B1C17; --panel:#122720; --panel-2:#0F211B; --line:#274539;
    --ink:#EDEAE0; --ink-dim:#9FB3AA; --amber:#F2A93B; --green:#4C9A6A; --red:#C1443B;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:'IBM Plex Mono',monospace;padding:32px 16px 80px;}
  header{max-width:1000px;margin:0 auto 28px;border-bottom:1px solid var(--line);padding-bottom:18px;}
  h1{font-family:'Oswald',sans-serif;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;
     font-size:clamp(28px,5vw,44px);margin:0 0 4px;color:var(--amber);}
  .sub{color:var(--ink-dim);font-size:13px;}
  .bt-bar{max-width:1000px;margin:0 auto 28px;background:var(--panel);border:1px solid var(--line);
          border-radius:6px;padding:14px 18px;font-size:13px;color:var(--ink-dim);display:flex;gap:24px;flex-wrap:wrap;}
  .bt-bar b{color:var(--ink);}
  .board{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:14px;}
  .game{background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden;}
  .game-head{display:flex;justify-content:space-between;align-items:center;padding:10px 18px;
             background:var(--panel-2);border-bottom:1px solid var(--line);font-size:12px;color:var(--ink-dim);}
  .matchup{display:grid;grid-template-columns:1fr auto 1fr;gap:14px;padding:18px;align-items:center;}
  .team{display:flex;flex-direction:column;gap:4px;}
  .team.home{text-align:right;align-items:flex-end;}
  .team-name{font-family:'Oswald',sans-serif;font-size:19px;font-weight:700;}
  .team-rec{font-size:11px;color:var(--ink-dim);}
  .sp{font-size:12px;color:var(--ink-dim);}
  .sp b{color:var(--ink);}
  .at{font-family:'Oswald',sans-serif;color:var(--amber);font-size:14px;}
  .prob-row{display:flex;align-items:center;gap:10px;padding:0 18px 14px;}
  .prob-bar{flex:1;height:10px;border-radius:5px;overflow:hidden;display:flex;background:#0A1913;border:1px solid var(--line);}
  .prob-away{background:var(--red);}
  .prob-home{background:var(--green);}
  .prob-label{font-size:12px;width:64px;color:var(--ink-dim);}
  .pick-row{display:flex;justify-content:space-between;align-items:center;padding:10px 18px;
            background:var(--panel-2);border-top:1px solid var(--line);font-size:13px;}
  .pick{color:var(--amber);font-weight:600;}
  .conf{padding:2px 8px;border-radius:3px;font-size:11px;letter-spacing:.04em;text-transform:uppercase;}
  .conf-High{background:rgba(76,154,106,.2);color:var(--green);}
  .conf-Medium{background:rgba(242,169,59,.2);color:var(--amber);}
  .conf-Low{background:rgba(159,179,170,.2);color:var(--ink-dim);}
  .odds-row{display:flex;justify-content:space-between;align-items:center;padding:8px 18px;
            font-size:12px;color:var(--ink-dim);border-top:1px dashed var(--line);}
  .odds-cell b{color:var(--ink);font-weight:600;}
  .odds-total{color:var(--ink-dim);font-size:11px;}
  .odds-none{color:var(--ink-dim);font-style:italic;font-size:11px;}
  .edge{margin-left:4px;font-weight:600;}
  .edge-pos{color:var(--green);}
  .edge-neg{color:var(--red);}
  .edge-badge{background:rgba(76,154,106,.18);color:var(--green);padding:1px 7px;border-radius:3px;
              font-size:10px;margin-left:6px;letter-spacing:.03em;}
  .props{display:none;padding:0 18px 16px;}
  .props.open{display:block;}
  .props-table{width:100%;border-collapse:collapse;font-size:12px;}
  .props-table th{text-align:left;color:var(--ink-dim);font-weight:500;padding:4px 8px;
                  border-bottom:1px solid var(--line);font-size:11px;}
  .props-table td{padding:4px 8px;border-bottom:1px solid rgba(39,69,57,.4);}
  .prop-type{color:var(--ink-dim);text-transform:capitalize;}
  .lean-over{color:var(--green);font-weight:600;}
  .lean-under{color:var(--red);font-weight:600;}
  .lean-none{color:var(--ink-dim);}
  .props-note{font-size:10px;color:var(--ink-dim);margin-top:8px;font-style:italic;line-height:1.5;}
  .details{display:none;padding:0 18px 16px;font-size:12px;color:var(--ink-dim);gap:6px;}
  .details.open{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;}
  .toggle{cursor:pointer;color:var(--amber);font-size:11px;text-decoration:underline;background:none;border:none;
          font-family:inherit;padding:0 18px 12px;}
  footer{max-width:1000px;margin:36px auto 0;font-size:11px;color:var(--ink-dim);border-top:1px solid var(--line);
         padding-top:16px;line-height:1.6;}
</style>
</head>
<body>
<header>
  <h1>Tonight's Board</h1>
  <div class="sub">__DATE__ &middot; log5 model vs. live market odds &middot; free public data</div>
  <div class="sub" style="margin-top:2px;font-size:11px;">Updated __UPDATED__ &middot; rebuilt daily</div>
</header>

<div class="bt-bar" id="btBar"></div>
<div class="board" id="board"></div>

<footer>
  Picks labelled "value" are where the model's win probability exceeds the
  de-vigged market price — that gap is the signal, not a guarantee. The edge
  shown is only as good as the model, which is simple and unproven; a positive
  number means the model disagrees with the market, not that it's right. Odds
  move, and closing lines are sharp. Treat this as a research tool, not advice,
  and never bet more than you can afford to lose. If betting stops being fun or
  you're chasing losses, that's a sign to stop. National Problem Gambling
  Helpline: 1-800-522-4700.
</footer>

<script>
const games = __GAMES_JSON__;
const bt = __BACKTEST_JSON__;

document.getElementById('btBar').innerHTML =
  `<span><b>Backtest — point-in-time, last 14 days:</b></span>
   <span><b>${bt.games_checked ?? 0}</b> games</span>
   <span><b>${bt.accuracy != null ? (bt.accuracy*100).toFixed(1)+'%' : 'n/a'}</b> overall accuracy</span>
   <span><b>${bt.confident_accuracy != null ? (bt.confident_accuracy*100).toFixed(1)+'%' : 'n/a'}</b> on ${bt.confident_games ?? 0} confident games</span>`;

const board = document.getElementById('board');

function fmtML(v){ return v == null ? "—" : (v > 0 ? "+"+v : ""+v); }

function oddsRow(g){
  if (g["Home ML"] == null && g["Away ML"] == null) {
    return `<div class="odds-row"><span class="odds-none">No market odds for this game</span></div>`;
  }
  const at = g["Away Edge"], ht = g["Home Edge"];
  const eClass = (e) => e == null ? "" : (e > 0 ? "edge-pos" : "edge-neg");
  const eFmt = (e) => e == null ? "" : (e > 0 ? "+"+e+"%" : e+"%");
  return `<div class="odds-row">
    <span class="odds-cell">Away ML <b>${fmtML(g["Away ML"])}</b>
      <span class="edge ${eClass(at)}">${eFmt(at)}</span></span>
    <span class="odds-cell odds-total">${g["O/U Total"] != null ? "O/U "+g["O/U Total"] : ""}</span>
    <span class="odds-cell">Home ML <b>${fmtML(g["Home ML"])}</b>
      <span class="edge ${eClass(ht)}">${eFmt(ht)}</span></span>
  </div>`;
}

function pickPrefix(g){
  if (g["Pick Type"] === "value") return "Value pick:";
  if (g["Pick Type"] === "pass")  return "";
  return "Model pick:";
}

function edgeTag(g){
  if (g["Pick Type"] !== "value") return "";
  const e = Math.max(g["Home Edge"] ?? -99, g["Away Edge"] ?? -99);
  return `<span class="edge-badge">+${e}% edge vs line</span>`;
}

function propsSection(g, idx){
  const props = g["Props"] || [];
  if (!props.length) return "";
  const leaned = props.filter(p => p.lean && p.lean !== "—");
  const rows = props.slice(0, 12).map(p => {
    const leanClass = p.lean === "OVER" ? "lean-over" : (p.lean === "UNDER" ? "lean-under" : "lean-none");
    const est = p.model_estimate != null ? p.model_estimate : "—";
    return `<tr>
      <td>${p.player}</td>
      <td class="prop-type">${(p.prop_type||"").replace(/_/g," ")}</td>
      <td>${p.line != null ? p.line : "—"}</td>
      <td>${est}</td>
      <td class="${leanClass}">${p.lean || "—"}</td>
    </tr>`;
  }).join("");
  const count = leaned.length ? `${leaned.length} lean${leaned.length>1?"s":""}` : "lines only";
  return `<button class="toggle props-toggle" data-p="${idx}">+ player props (${count})</button>
    <div class="props" id="props-${idx}">
      <table class="props-table">
        <thead><tr><th>Player</th><th>Prop</th><th>Line</th><th>Model</th><th>Lean</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="props-note">Prop leans use a simple season-rate estimate and are unbacktested — lower confidence than the game model. Research before betting.</div>
    </div>`;
}

games.forEach((g, idx) => {
  const homeWin = g["Model Home Win%"], awayWin = g["Model Away Win%"];
  const card = document.createElement('div');
  card.className = 'game';
  card.innerHTML = `
    <div class="game-head"><span>${g["Venue"]}</span><span>${g["First Pitch (ET)"]}</span></div>
    <div class="matchup">
      <div class="team away">
        <div class="team-name">${g["Away Team"]}</div>
        <div class="team-rec">${g["Away Record"]}</div>
        <div class="sp">SP: <b>${g["Away SP"]}</b></div>
      </div>
      <div class="at">@</div>
      <div class="team home">
        <div class="team-name">${g["Home Team"]}</div>
        <div class="team-rec">${g["Home Record"]}</div>
        <div class="sp">SP: <b>${g["Home SP"]}</b></div>
      </div>
    </div>
    <div class="prob-row">
      <div class="prob-label">${awayWin}% away</div>
      <div class="prob-bar">
        <div class="prob-away" style="width:${awayWin}%"></div>
        <div class="prob-home" style="width:${homeWin}%"></div>
      </div>
      <div class="prob-label" style="text-align:right">${homeWin}% home</div>
    </div>
    ${oddsRow(g)}
    <div class="pick-row">
      <span>${pickPrefix(g)} <span class="pick">${g["Recommended Pick"]}</span> ${edgeTag(g)}</span>
      <span class="conf conf-${g["Confidence"]}">${g["Confidence"]}</span>
    </div>
    <button class="toggle" data-i="${idx}">+ pitcher / park / bullpen detail</button>
    <div class="details" id="details-${idx}">
      <span>Away SP season ERA: ${g["Away SP Season ERA"] ?? "N/A"}</span>
      <span>Home SP season ERA: ${g["Home SP Season ERA"] ?? "N/A"}</span>
      <span>Away SP last-3 ERA: ${g["Away SP Last-3 ERA"] ?? "N/A"}</span>
      <span>Home SP last-3 ERA: ${g["Home SP Last-3 ERA"] ?? "N/A"}</span>
      <span>Away bullpen IP (3d): ${g["Away Bullpen IP (3d)"]}</span>
      <span>Home bullpen IP (3d): ${g["Home Bullpen IP (3d)"]}</span>
      <span>Park factor: ${g["Park Factor"]} (100 = neutral)</span>
      <span>Market (de-vig): ${g["Market Away%"] != null ? g["Market Away%"]+"% / "+g["Market Home%"]+"%" : "no odds"}</span>
    </div>
    ${propsSection(g, idx)}
  `;
  board.appendChild(card);
});

board.addEventListener('click', (e) => {
  if (e.target.classList.contains('props-toggle')) {
    const d = document.getElementById('props-' + e.target.dataset.p);
    d.classList.toggle('open');
    const base = e.target.textContent.replace(/^[-+] /, '');
    e.target.textContent = (d.classList.contains('open') ? '- ' : '+ ') + base;
    return;
  }
  if (e.target.classList.contains('toggle')) {
    const d = document.getElementById('details-' + e.target.dataset.i);
    d.classList.toggle('open');
    e.target.textContent = d.classList.contains('open')
      ? '- hide detail' : '+ pitcher / park / bullpen detail';
  }
});
</script>
</body>
</html>
"""


def main():
    date_str = get_today_eastern_date()
    season = date_str[:4]
    if OUTPUT_DIR != ".":
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    load_park_factors(season)  # fetch live park factors (falls back if offline)

    # Try to load real betting odds from balldontlie. If the key is missing or
    # the call fails, we degrade gracefully to straight-winner picks.
    odds_available, bdl_games, consensus = False, None, None
    props_fn = None
    try:
        from mlb_odds import get_games_for_date, get_odds_for_date
        print("Fetching betting odds from balldontlie...")
        bdl_games = get_games_for_date(date_str)
        consensus = get_odds_for_date(date_str)
        odds_available = bool(consensus)
        print(f"Loaded odds for {len(consensus)} games." if odds_available
              else "No odds returned; using straight-winner picks.")
    except Exception as e:
        print(f"Odds unavailable ({e}); using straight-winner picks.")

    # Player props are optional; only wire them in if the module imports and a
    # key is present. Each game fetches its own props inside build_games.
    try:
        from mlb_props import build_prop_leans
        props_fn = build_prop_leans
        print("Player props enabled.")
    except Exception as e:
        print(f"Player props unavailable ({e}).")

    rows = build_games(date_str, season, odds_available=odds_available,
                       bdl_games=bdl_games, consensus=consensus, props_fn=props_fn)
    if not rows:
        print("No games found for tonight.")
        return
    write_csv(rows, CSV_FILE)
    print("Running point-in-time backtest (last 14 days)...")
    bt = backtest(season, days_back=14)
    write_html(rows, bt, date_str, HTML_FILE)
    print(f"Done. Dashboard: {HTML_FILE}")


if __name__ == "__main__":
    main()
