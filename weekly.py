"""Weekly recap for both weather bots -> weekly/ on the hub site.

Run by the hub workflow every Monday afternoon, after the 2:07 p.m. run has settled Sunday's
picks. Reads the two bot repos (checked out next to this one) and the GitHub run history, and
writes one page per Monday–Sunday week plus weekly/index.html (the latest week and an archive).

    python weekly.py --forecast _fc --live _live [--week 2026-11-16]
"""
import argparse
import glob
import html
import json
import os
import re
import urllib.request

import numpy as np
import pandas as pd

OUT = "weekly"
STAKE = 100
TARGET = 300
OWNER = "lucaslin1-collab"
ET = "America/New_York"
CUR = ' aria-current="page"'
HEALTH_START = pd.Timestamp("2026-11-22")   # cron-job.org began starting the runs on time


def esc(x):
    return html.escape(str(x))


def usd(x, cents=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    s = f"{abs(x):,.2f}" if cents else f"{abs(x):,.0f}"
    return ("−$" if x < 0 and s.strip("0.,") else "$") + s


def cents(r):
    return "–" if r is None or np.isnan(r) else f"{'+' if r >= 0 else '−'}{abs(r) * 100:.1f}¢"


# --- data ------------------------------------------------------------------------------

def read_csv(path):
    return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()


def bets(fc, live):
    """Every logged bet from both bots, one row each, with profit per $1 staked (`ret`)."""
    frames = []
    for f, name in (("data/picks_log.csv", "8 a.m. picks"), ("data/picks_log_dayahead.csv", "Day-before picks")):
        d = read_csv(os.path.join(fc, f))
        if len(d):
            d = d.assign(bot=name, contracts=1.0, cost=d["price"], profit=d["pnl"])
            frames.append(d)
    d = read_csv(os.path.join(live, "data/paper_positions.csv"))
    if len(d):
        d = d.assign(bot="Live bot", cost=d["price"] * d["contracts"], profit=d["pnl"])
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    b = pd.concat(frames, ignore_index=True)
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    b["ret"] = b["profit"] / b["cost"]
    return b


def expectations(fc, live):
    """Backtest profit per $1 staked (mean and spread) for each strategy."""
    out = {}
    t = read_csv(os.path.join(fc, "results/trades.csv"))
    if len(t):
        r = t["pnl"] / t["price"]
        out["8 a.m. picks"] = (float(r.mean()), float(r.std()))
    p = os.path.join(fc, "results/insights.json")
    if os.path.exists(p):
        e = json.load(open(p))["entry_times"].get("d1_12", {})
        if e.get("ret") is not None:
            out["Day-before picks"] = (e["ret"], e["ret_sd"])
    t = read_csv(os.path.join(live, "results/trades.csv"))
    if len(t):
        r = t["pnl"] / t["price"]
        out["Live bot"] = (float(r.mean()), float(r.std()))
    return out


def official_highs(fc):
    highs = {}
    for f in glob.glob(os.path.join(fc, "data/weather/*.csv")):
        w = pd.read_csv(f, usecols=["date", "observed_high"])
        s = os.path.basename(f)[:-4]
        for d, h in zip(w["date"], w["observed_high"]):
            if pd.notna(h):
                highs[(s, pd.Timestamp(d))] = h
    return highs


def verdicts(fc, live):
    """Current backtest verdict per city for each strategy (snapshotted weekly)."""
    v = {}
    p = os.path.join(fc, "results/backtest.json")
    if os.path.exists(p):
        v["8 a.m. picks"] = {c["city"]: c["verdict"] for c in json.load(open(p))["cities"].values()}
    p = os.path.join(fc, "results/timing.json")
    if os.path.exists(p):
        e = json.load(open(p))["entry_times"].get("d1_12", {})
        v["Day-before picks"] = {c["city"]: c["verdict"] for c in e.get("cities", {}).values()}
    p = os.path.join(live, "results/backtest.json")
    if os.path.exists(p):
        v["Live bot"] = {c["city"]: c["verdict"] for c in json.load(open(p))["cities"].values()}
    return v


def github_runs(repo, start, end):
    """Workflow runs created in [start, end] (UTC dates), via the public API."""
    runs, page = [], 1
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "weekly-recap"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    while page <= 15:
        url = (f"https://api.github.com/repos/{OWNER}/{repo}/actions/runs?per_page=100&page={page}"
               f"&created={start:%Y-%m-%d}..{end:%Y-%m-%d}")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                batch = json.load(r)["workflow_runs"]
        except Exception as err:  # noqa: BLE001
            print(f"  run history for {repo} unavailable: {err}")
            return None
        runs += batch
        if len(batch) < 100:
            break
        page += 1
    return runs


def health(start, end):
    """Missed or failed runs during the week, one line per problem."""
    issues, notes = [], []
    days = pd.date_range(max(start, HEALTH_START), end)
    if not len(days):
        return [], ["Health checks start with the week of November 16, when on-time scheduling began."]
    fc = github_runs("lucaslinweather", start, end + pd.Timedelta(days=1))
    lv = github_runs("weather-live", start, end + pd.Timedelta(days=1))
    if fc is None or lv is None:
        return None, ["Run history couldn’t be read this time."]

    def local(r):
        return pd.Timestamp(r["created_at"]).tz_convert(ET)

    for name, h0, h1 in (("8 a.m. picks", 7.9, 9.5), ("Day-before picks", 13.9, 15.5)):
        missing = []
        for d in days:
            ok = [r for r in fc if r["name"] == "Update site" and r["event"] in ("workflow_dispatch", "schedule")
                  and local(r).normalize().tz_localize(None) == d
                  and h0 <= local(r).hour + local(r).minute / 60 <= h1 and r["conclusion"] == "success"]
            if not ok:
                missing.append(f"{d:%a}")
        if missing:
            issues.append(f"{name}: no successful on-time run on {', '.join(missing)}.")
    per_day = {}
    for r in lv:
        if r["name"] == "Live bot" and r["event"] in ("workflow_dispatch", "schedule") and r["conclusion"] == "success":
            per_day[local(r).normalize().tz_localize(None)] = per_day.get(local(r).normalize().tz_localize(None), 0) + 1
    thin = [f"{d:%a} ({per_day.get(d, 0)} of 79)" for d in days if per_day.get(d, 0) < 70]
    if thin:
        issues.append("Live bot: fewer 10-minute checks than expected on " + ", ".join(thin) + ".")
    first = pd.Timestamp(days[0]).tz_localize(ET)
    failed = [r for r in fc + lv if r["conclusion"] == "failure" and pd.Timestamp(r["created_at"]) >= first]
    if failed:
        kinds = {}
        for r in failed:
            kinds[r["name"]] = kinds.get(r["name"], 0) + 1
        issues.append("Failed runs: " + ", ".join(f"{k} ×{v}" for k, v in kinds.items()) + ".")
    total = sum(per_day.get(d, 0) for d in days)
    span = f"{days[0]:%a %b %-d}" + (f" to {days[-1]:%a %b %-d}" if len(days) > 1 else "")
    notes.append(f"Checked {span}. The live bot completed {total} ten-minute checks.")
    return issues, notes


# --- one week ---------------------------------------------------------------------------

BOTS = ["8 a.m. picks", "Day-before picks", "Live bot"]


def compare(rows, exp):
    settled = rows[rows["won"].notna()]
    n = len(settled)
    res = {"n": n, "actual": float(settled["ret"].mean()) if n else np.nan,
           "profit": float(settled["profit"].sum()) if n else 0.0,
           "usd100": float(settled["ret"].sum() * STAKE) if n else 0.0}
    if exp:
        m, sd = exp
        half = 1.645 * sd / np.sqrt(max(n, 1))
        res.update(expected=m, band=(m - half, m + half))
        res["status"] = ("No settled bets yet" if n == 0 else "Too early to tell" if n < 30 else
                         "Behind the backtest" if res["actual"] < m - half else
                         "Behind, but within luck" if res["actual"] < 0 else "On track")
    return res


def build_week(start, b, exp, highs, verd, prev_verd, hl):
    end = start + pd.Timedelta(days=6)
    wk = b[(b["date"] >= start) & (b["date"] <= end)] if len(b) else b
    upto = b[b["date"] <= end] if len(b) else b
    rec = {"week": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}", "bots": {}, "running": {}}

    for bot in BOTS:
        r = wk[wk["bot"] == bot] if len(wk) else wk
        s = r[r["won"].notna()] if len(r) else r
        rec["bots"][bot] = {"bets": int(len(r)), "won": int((s["won"] == 1).sum()) if len(s) else 0,
                            "lost": int((s["won"] == 0).sum()) if len(s) else 0,
                            "open": int(r["won"].isna().sum()) if len(r) else 0,
                            "profit": float(s["profit"].sum()) if len(s) else 0.0,
                            "usd100": float(s["ret"].sum() * STAKE) if len(s) else 0.0}
        rr = upto[upto["bot"] == bot] if len(upto) else upto
        rec["running"][bot] = compare(rr, exp.get(bot))

    # best and worst settled bets of the week
    s = wk[wk["won"].notna()].copy() if len(wk) else wk
    picks = []
    if len(s):
        s["usd100"] = s["ret"] * STAKE
        s = s.sort_values("usd100")
        chosen = pd.concat([s.tail(3).iloc[::-1], s.head(3)]).drop_duplicates(subset=["bot", "ticker"])
        for r in chosen.itertuples():
            picks.append({"bot": r.bot, "date": f"{r.date:%a %b %-d}", "city": r.city, "label": r.label, "side": r.side,
                          "price": float(r.price), "won": bool(r.won == 1), "usd100": float(r.usd100),
                          "high": highs.get((r.series, r.date))})
    rec["picks"] = picks

    # verdict changes since last week's snapshot
    changes = []
    if prev_verd:
        for bot, cities in verd.items():
            for city, v in cities.items():
                was = prev_verd.get(bot, {}).get(city)
                if was and was != v:
                    changes.append(f"{bot}, {city}: {was} → {v}")
    rec["changes"] = changes if prev_verd else None

    # drift: last 28 days vs backtest
    drift = {}
    for bot in BOTS:
        rr = upto[(upto["bot"] == bot) & (upto["date"] > end - pd.Timedelta(days=28)) & upto["won"].notna()] \
            if len(upto) else upto
        drift[bot] = {"n": int(len(rr)), "ret": float(rr["ret"].mean()) if len(rr) else np.nan,
                      "expected": exp.get(bot, (np.nan,))[0]}
    rec["drift"] = drift
    rec["health"], rec["health_notes"] = hl if hl else (None, [])
    return rec


# --- html --------------------------------------------------------------------------------

def style():
    s = open("index.html", encoding="utf-8").read()
    css = re.search(r"<style>(.*?)</style>", s, re.S).group(1)
    return css + (".summary{font-size:1.15rem;max-width:62ch;margin:14px 0 0}"
                  ".weeks{display:flex;flex-wrap:wrap;gap:8px 18px;list-style:none;padding:0;margin:0}"
                  ".weeks a[aria-current]{color:var(--ink);font-weight:600;text-decoration:none}"
                  ".issues{margin:0;padding-left:20px;color:var(--ink-2)}.issues li{margin:4px 0}"
                  "h3.sub{margin:30px 0 10px}")


def nav(current):
    m = open("index.html", encoding="utf-8").read()
    brand = re.search(r'(<a class="brand".*?</a>)', m, re.S).group(1)
    links = [("/lucaslinweather/", "Forecast bot"), ("/weather-live/", "Live bot"), ("/weekly/", "Weekly")]
    items = "".join(f'<li><a class="nav" href="{h}"{CUR if h == current else ""}>{t}</a></li>'
                    for h, t in links)
    return f'<nav class="site-nav" aria-label="Site">{brand}<ul>{items}</ul></nav>'


def badge(status):
    cls = {"On track": "good", "Behind the backtest": "bad"}.get(status, "warn")
    ico = {"good": "✓", "bad": "✕", "warn": "○"}[cls]
    return f'<span class="badge {cls}"><span class="ico">{ico}</span>{esc(status)}</span>'


def summary_line(rec):
    parts = []
    for bot in BOTS:
        x = rec["bots"][bot]
        if x["bets"]:
            parts.append(f'{bot.lower() if bot != "Live bot" else "the live bot"} '
                         f'{usd(x["usd100"])} on {x["won"] + x["lost"]} settled')
    if not parts:
        return "No bets this week."
    s = "At $100 a bet: " + "; ".join(parts) + "."
    return s[0].upper() + s[1:]


def week_html(rec, weeks):
    start, end = pd.Timestamp(rec["week"]), pd.Timestamp(rec["end"])
    o = [nav("/weekly/"), f'<header class="top"><h1>Weekly recap<span class="date">{start:%B %-d} – {end:%B %-d, %Y}</span></h1>'
         f'<p class="summary">{esc(summary_line(rec))}</p>'
         '<p class="lede">Paper results only; no real money. One week is mostly luck, so the running total '
         'against the backtest is the line to watch.</p></header>']

    rows = []
    for bot in BOTS:
        x = rec["bots"][bot]
        own = usd(x["profit"], cents=True) if bot == "Live bot" else f'{usd(x["profit"], cents=True)}'
        rows.append(f'<tr><td class="city">{bot}</td><td class="num">{x["bets"]}</td><td class="num">{x["won"]}</td>'
                    f'<td class="num">{x["lost"]}</td><td class="num">{x["open"]}</td><td class="num">{own}</td>'
                    f'<td class="num strong">{usd(x["usd100"])}</td></tr>')
    o.append('<section><h2>This week</h2><div class="scroll"><table class="cities"><thead><tr><th>Bot</th>'
             '<th class="num">Bets</th><th class="num">Won</th><th class="num">Lost</th><th class="num">Still open</th>'
             f'<th class="num">Profit, as logged</th><th class="num">At ${STAKE} a bet</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div><p class="fine">“As logged” is per $1 contract for the '
             'forecast picks and at the live bot’s own position sizes. Bets dated this week that settle later are '
             'counted when they do.</p></section>')

    rows = []
    for bot in BOTS:
        c = rec["running"][bot]
        band = f'{cents(c["band"][0])} to {cents(c["band"][1])}' if c.get("band") else "–"
        rows.append(f'<tr><td class="city">{bot}</td><td class="num">{c["n"]} of {TARGET}</td>'
                    f'<td class="num strong">{cents(c["actual"])}</td><td class="num">{cents(c.get("expected", np.nan))}</td>'
                    f'<td class="num">{band}</td><td class="num">{usd(c["usd100"])}</td>'
                    f'<td>{badge(c.get("status", "No backtest"))}</td></tr>')
    o.append('<section><h2>Running total vs. the backtest</h2><div class="scroll"><table class="cities"><thead><tr>'
             '<th>Bot</th><th class="num">Settled</th><th class="num">Profit per $1</th><th class="num">Backtest expects</th>'
             f'<th class="num">Normal luck range</th><th class="num">Total at ${STAKE} a bet</th><th>Status</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div><p class="fine">The luck range is where the running result '
             'lands 90% of the time if the backtest is right; it narrows as bets settle. About '
             f'{TARGET} settled bets are needed to tell skill from luck.</p></section>')

    if rec["picks"]:
        rows = []
        for p in rec["picks"]:
            res = ('<span class="badge good"><span class="ico">✓</span>Won</span>' if p["won"] else
                   '<span class="badge bad"><span class="ico">✕</span>Lost</span>')
            hi = "–" if p["high"] is None else f'{p["high"]:.0f}°'
            rows.append(f'<tr><td>{esc(p["date"])}</td><td class="city">{esc(p["city"])}</td><td>{esc(p["bot"])}</td>'
                        f'<td class="num">{esc(p["label"])}</td><td><span class="side {p["side"].lower()}">Buy {p["side"]}</span></td>'
                        f'<td class="num">{p["price"] * 100:.0f}¢</td><td class="num">{hi}</td><td>{res}</td>'
                        f'<td class="num strong">{usd(p["usd100"])}</td></tr>')
        o.append('<section><h2>Best and worst bets</h2><div class="scroll"><table class="log"><thead><tr><th>Day</th>'
                 '<th>City</th><th>Bot</th><th class="num">Bracket</th><th>Bet</th><th class="num">Paid</th>'
                 f'<th class="num">Official high</th><th>Result</th><th class="num">At ${STAKE}</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table></div></section>')

    o.append('<section><h2>Changes</h2>')
    if rec["changes"] is None:
        o.append('<p class="fine">First week tracked. City verdict changes will show from next week.</p>')
    elif rec["changes"]:
        o.append('<ul class="issues">' + "".join(f"<li>{esc(c)}</li>" for c in rec["changes"]) + '</ul>')
    else:
        o.append('<p class="fine">No city changed its backtest verdict this week.</p>')
    drift = []
    for bot in BOTS:
        d = rec["drift"][bot]
        if d["n"] >= 20:
            drift.append(f'<li>{bot}: last 4 weeks {cents(d["ret"])} per $1 over {d["n"]} bets, against '
                         f'{cents(d["expected"])} in the backtest.</li>')
        else:
            drift.append(f'<li>{bot}: {d["n"]} settled {"bet" if d["n"] == 1 else "bets"} in the last 4 weeks, '
                         'too few to judge a trend (needs 20).</li>')
    o.append('<h3 class="sub">Last 4 weeks vs. the backtest</h3><ul class="issues">' + "".join(drift) + '</ul></section>')

    o.append('<section><h2>Health</h2>')
    if rec["health"] is None:
        o.append(f'<p class="fine">{esc(" ".join(rec["health_notes"]))}</p>')
    elif rec["health"]:
        o.append('<ul class="issues">' + "".join(f"<li>{esc(i)}</li>" for i in rec["health"]) + '</ul>')
        o.append(f'<p class="fine">{esc(" ".join(rec["health_notes"]))}</p>')
    else:
        o.append(f'<p>Every scheduled run happened on time. {esc(" ".join(rec["health_notes"]))}</p>')
    o.append('</section>')

    items = "".join(f'<li><a href="/weekly/{w}.html"{CUR if w == rec["week"] else ""}>'
                    f'{pd.Timestamp(w):%b %-d}</a></li>' for w in sorted(weeks, reverse=True))
    o.append(f'<section><h2>Past weeks</h2><ul class="weeks">{items}</ul></section>'
             '<footer><p>A research project, not financial advice. Not affiliated with Kalshi.</p></footer>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Weekly recap</title><meta name="description" content="Week of {start:%B %-d}: results for both weather bots.">'
            '<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&'
            'family=Barlow+Condensed:wght@500;600&display=swap">'
            f'<style>{style()}</style></head><body><main>{"".join(o)}</main></body></html>')


# --- run -------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", default="_fc")
    ap.add_argument("--live", default="_live")
    ap.add_argument("--week", help="Monday of the week to (re)build; default: the last complete week")
    ap.add_argument("--no-health", action="store_true")
    a = ap.parse_args()
    today = pd.Timestamp.now(tz=ET).normalize().tz_localize(None)
    start = pd.Timestamp(a.week) if a.week else today - pd.Timedelta(days=today.dayofweek + 7)
    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)

    b = bets(a.forecast, a.live)
    exp = expectations(a.forecast, a.live)
    verd = verdicts(a.forecast, a.live)
    snaps = sorted(glob.glob(os.path.join(OUT, "data", "*.json")))
    prev = [p for p in snaps if os.path.basename(p)[:10] < f"{start:%Y-%m-%d}"]
    prev_verd = json.load(open(prev[-1])).get("verdicts") if prev else None
    hl = None if a.no_health else health(start, start + pd.Timedelta(days=6))
    rec = build_week(start, b, exp, official_highs(a.forecast), verd, prev_verd, hl)
    rec["verdicts"] = verd
    rec["generated"] = pd.Timestamp.now(tz=ET).isoformat(timespec="minutes")
    json.dump(rec, open(os.path.join(OUT, "data", f"{rec['week']}.json"), "w"), indent=2, default=float)

    weeks = sorted(os.path.basename(p)[:10] for p in glob.glob(os.path.join(OUT, "data", "*.json")))
    for w in weeks:   # rebuild every page so each archive list includes all weeks
        r = json.load(open(os.path.join(OUT, "data", f"{w}.json")))
        page = week_html(r, weeks)
        open(os.path.join(OUT, f"{w}.html"), "w", encoding="utf-8").write(page)
        if w == weeks[-1]:
            open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
    print(f"Weekly recap for {rec['week']}: {summary_line(rec)}")


if __name__ == "__main__":
    main()
