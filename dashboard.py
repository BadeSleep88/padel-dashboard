"""Analysis + HTML dashboard rendering (adapted from padel_dashboard.py).

Public API: render_dashboard(payload, me=None) -> (html, meta)
"""
import json, re
from collections import Counter, defaultdict
from datetime import datetime, date

NO_LEVEL_PREFIXES = ("rating session",)  # activities that never count towards levels
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
clean = lambda s: " ".join(str(s or "").split())
g = lambda x: f"{x:g}"


def num(s):
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return None


def levels(s):
    v = [num(x) for x in re.split(r"\s*-\s*", clean(s)) if x]
    v = [x for x in v if x is not None]
    return (v[0], v[-1]) if v else (None, None)


def court(c):
    m = re.search(r"court\s*(\d+)", c or "", re.I)
    return f"Court {m.group(1)}" if m else (clean(c) or "Unknown")


def parse(raw):
    items = raw.get("items", []) if isinstance(raw, dict) else raw
    out = []
    for it in items:
        try:
            h, m = map(int, it["time"].split("-")[0].split(":"))
            dt = datetime.strptime(it["date"], "%d/%m/%Y").replace(hour=h, minute=m)
        except (KeyError, ValueError, AttributeError, TypeError):
            continue  # incomplete record from the scraper - skip it
        lo, hi = levels(it.get("levels"))
        rs = clean(it.get("name")).lower().startswith(NO_LEVEL_PREFIXES)
        if rs:
            lo = hi = None
        out.append(
            dict(
                it,
                dt=dt,
                hours=(it.get("duration_minutes") or 0) / 60,
                lo=lo,
                hi=hi,
                rs=rs,
                court_n=court(it.get("court")),
            )
        )
    out.sort(key=lambda x: x["dt"])
    return out, (raw.get("scraped_at") if isinstance(raw, dict) else None)


def stacked(items, keyf, order=None):
    c = defaultdict(Counter)
    for i in items:
        c[i["tag"]][keyf(i)] += 1
    labels = order or sorted({k for t in c.values() for k in t}, key=lambda k: -sum(t[k] for t in c.values()))
    return dict(labels=labels, sets=[dict(label=t, data=[c[t][l] for l in labels]) for t in sorted(c)])


def simple(counter, label="Sessions"):
    ks = [k for k, _ in counter.most_common()]
    return dict(labels=ks, sets=[dict(label=label, data=[counter[k] for k in ks])])


def analyse(items, me, today):
    ms = [i for i in items if i["tag"] == "Match"]
    acts = [i for i in items if i["tag"] == "Activity"]
    first, last = items[0]["dt"], items[-1]["dt"]
    total_h = sum(i["hours"] for i in items)

    # ---- hours per month / year
    months, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    tags = sorted({i["tag"] for i in items})
    mh, yh = defaultdict(float), defaultdict(float)
    for i in items:
        mh[(i["dt"].strftime("%Y-%m"), i["tag"])] += i["hours"]
        yh[(str(i["dt"].year), i["tag"])] += i["hours"]
    years = sorted({str(i["dt"].year) for i in items})
    monthly = dict(
        labels=months, sets=[dict(label=t, data=[round(mh[(mo, t)], 2) for mo in months]) for t in tags]
    )
    yearly = dict(
        labels=years, sets=[dict(label=t, data=[round(yh[(yr, t)], 2) for yr in years]) for t in tags]
    )
    mtot = {mo: sum(mh[(mo, t)] for t in tags) for mo in months}
    cum, run = [], 0
    for i in items:
        run += i["hours"]
        cum.append(round(run, 2))
    cumulative = dict(
        labels=[i["dt"].strftime("%d %b %y") for i in items], sets=[dict(label="Cumulative hours", data=cum)]
    )

    # ---- hours by level band
    lh = defaultdict(lambda: [0.0, 0])
    for i in items:
        k = (i["lo"], i["hi"]) if i["lo"] is not None else (999, 999)
        lh[k][0] += i["hours"]
        lh[k][1] += 1
    ks = sorted(lh)
    lab = lambda k: "No level" if k[0] == 999 else g(k[0]) + (f" – {g(k[1])}" if k[1] != k[0] else "")
    by_level = dict(
        labels=[lab(k) for k in ks], sets=[dict(label="Hours", data=[round(lh[k][0], 2) for k in ks])]
    )

    # ---- level journey (upgrade = first session in a higher level band than ever before)
    steps, cur = [], None
    for i in items:
        if i["lo"] is not None and (cur is None or i["lo"] > cur):
            steps.append(dict(level=i["lo"], start=i["dt"].date()))
            cur = i["lo"]
    for k, s in enumerate(steps):
        end = steps[k + 1]["start"] if k + 1 < len(steps) else today
        s["days"] = (end - s["start"]).days
        inside = [
            i
            for i in items
            if not i["rs"] and i["dt"].date() >= s["start"] and (k + 1 == len(steps) or i["dt"].date() < end)
        ]
        s["sessions"], s["hours"] = len(inside), round(sum(i["hours"] for i in inside), 1)
        s["matches"] = sum(i["tag"] == "Match" for i in inside)
        s["acts"] = sum(i["tag"] == "Activity" for i in inside)
        s["since_start"] = (s["start"] - steps[0]["start"]).days
    journey = [
        [
            g(s["level"]),
            s["start"].strftime("%d %b %Y"),
            f'{s["days"]}' + (" (ongoing)" if k == len(steps) - 1 else ""),
            s["since_start"],
            s["sessions"],
            s["matches"],
            s["acts"],
            s["hours"],
        ]
        for k, s in enumerate(steps)
    ]
    ups = [
        dict(
            label=f"{g(steps[k-1]['level'])} → {g(steps[k]['level'])}",
            days=steps[k - 1]["days"],
            date=steps[k]["start"].strftime("%d %b %Y"),
        )
        for k in range(1, len(steps))
    ]
    updays = [u["days"] for u in ups]
    level_counts = dict(
        labels=[u["label"] for u in ups],
        sets=[
            dict(label="Matches", data=[steps[k - 1]["matches"] for k in range(1, len(steps))]),
            dict(label="Activities", data=[steps[k - 1]["acts"] for k in range(1, len(steps))]),
        ],
    )

    # ---- people
    me_c = clean(me)
    mates, opps, rat, lastp, myr = Counter(), Counter(), defaultdict(list), {}, []
    for mt in ms:
        pl = mt.get("players") or []
        mine = next((p for p in pl if clean(p["name"]) == me_c), None)
        if not mine:
            continue
        if num(mine.get("rating")):
            myr.append((mt["dt"].strftime("%d %b %y"), num(mine["rating"])))
        for p in pl:
            if p is mine:
                continue
            n = clean(p["name"])
            (mates if p["team"] == mine["team"] else opps)[n] += 1
            if num(p.get("rating")):
                rat[n].append(num(p["rating"]))
            lastp[n] = mt["dt"].strftime("%d %b %Y")
    allp = mates + opps
    top = [n for n, _ in allp.most_common(10)]
    top_players = dict(
        labels=top,
        sets=[
            dict(label="As teammate", data=[mates[n] for n in top]),
            dict(label="As opponent", data=[opps[n] for n in top]),
        ],
    )
    top_table = [
        [n, allp[n], mates[n], opps[n], round(sum(rat[n]) / len(rat[n]), 2) if rat[n] else "-", lastp[n]]
        for n in top
    ]
    my_rating = dict(
        labels=[a for a, _ in myr], sets=[dict(label="Your rating in matches", data=[b for _, b in myr])]
    )
    mate_r = [r for n in mates for r in rat[n] for _ in range(mates[n])]
    opp_r = [r for n in opps for r in rat[n] for _ in range(opps[n])]
    avg = lambda v: round(sum(v) / len(v), 2) if v else "-"

    # ---- habits
    hrs = [i["dt"].hour for i in items]
    hour_order = [f"{h:02d}:00" for h in range(min(hrs), max(hrs) + 1)]
    heat = [[0] * 24 for _ in range(7)]
    for i in items:
        heat[i["dt"].weekday()][i["dt"].hour] += 1
    courts_sorted = sorted({i["court_n"] for i in items}, key=lambda s: (len(s), s))
    play_days = sorted({i["dt"].date() for i in items})
    gaps = [(b - a).days for a, b in zip(play_days, play_days[1:])]
    wk = sorted({(d - date(1970, 1, 5)).days // 7 for d in play_days})
    best = cur_s = 1
    for a, b in zip(wk, wk[1:]):
        cur_s = cur_s + 1 if b == a + 1 else 1
        best = max(best, cur_s)
    n_weeks = max(1, (last.date() - first.date()).days / 7)

    # ---- coaches
    def kind(a):
        n = a["name"] or "Activity"
        return (
            "Private Class"
            if n.lower().startswith("private")
            else "Train and Play" if n.lower().startswith("train and play") else n
        )

    cc = defaultdict(Counter)
    for a in acts:
        if a.get("instructor"):
            cc[a["instructor"]][kind(a)] += 1
    coaches = sorted(cc, key=lambda c: -sum(cc[c].values()))
    kinds = sorted({k for c in cc.values() for k in c})
    coach_all = dict(labels=coaches, sets=[dict(label=k, data=[cc[c][k] for c in coaches]) for k in kinds])

    def one(k):
        cs = sorted([c for c in cc if cc[c][k]], key=lambda c: -cc[c][k])
        return dict(labels=cs, sets=[dict(label=k, data=[cc[c][k] for c in cs])])

    rest = [k for k in kinds if k not in ("Private Class", "Train and Play")]
    oc = sorted([c for c in cc if any(cc[c][k] for k in rest)], key=lambda c: -sum(cc[c][k] for k in rest))
    coach_other = dict(labels=oc, sets=[dict(label=k, data=[cc[c][k] for c in oc]) for k in rest])

    top_of = lambda c: c.most_common(1)[0][0] if c else "-"
    cur_level = steps[-1] if steps else None
    kpis = [
        ["Total hours", f"{total_h:.1f}", f"{len(items)} sessions"],
        [
            "Avg hours / month",
            f"{total_h / len(months):.1f}",
            f"over {len(months)} months · {total_h / n_weeks:.1f} h/week",
        ],
        ["Matches", len(ms), f"{sum(i['hours'] for i in ms):.1f} h"],
        ["Classes & events", len(acts), f"{sum(i['hours'] for i in acts):.1f} h"],
        [
            "Court bookings",
            sum(i["tag"] == "Booking" for i in items),
            f"{sum(i['hours'] for i in items if i['tag'] == 'Booking'):.1f} h",
        ],
        [
            "Current level",
            g(cur_level["level"]) if cur_level else "-",
            f"{cur_level['days']} days at this level" if cur_level else "",
        ],
        ["Level-ups", len(ups), f"avg {sum(updays) / len(updays):.0f} days each" if ups else "none yet"],
        ["Unique co-players", len(allp), f"{len(mates)} teammates · {len(opps)} opponents"],
        ["Avg teammate rating", avg(mate_r), f"opponents {avg(opp_r)}"],
        [
            "Longest weekly streak",
            f"{best} wks",
            f"avg {sum(gaps) / len(gaps):.1f} days between play days" if gaps else "",
        ],
        [
            "Favourite slot",
            (
                f"{DAYS[Counter(i['dt'].weekday() for i in ms).most_common(1)[0][0]][:3]} {Counter(i['dt'].hour for i in ms).most_common(1)[0][0]:02d}:00"
                if ms
                else "-"
            ),
            f"{top_of(Counter(i['court_n'] for i in ms))} is your main court",
        ],
        ["Busiest month", max(mtot, key=mtot.get), f"{max(mtot.values()):.1f} h"],
    ]
    recent = [
        [
            i["dt"].strftime("%d %b %Y %H:%M"),
            i["tag"],
            i["name"] or "Court booking",
            i["court_n"],
            i["levels"] or "-",
            i.get("instructor") or "-",
            f"{i['hours']:.1f}",
        ]
        for i in items[-15:][::-1]
    ]

    return dict(
        kpis=kpis,
        monthly=monthly,
        yearly=yearly,
        cumulative=cumulative,
        by_level=by_level,
        journey=journey,
        ups=ups,
        level_counts=level_counts,
        top_players=top_players,
        top_table=top_table,
        my_rating=my_rating,
        mix=simple(Counter(i["tag"] for i in items), "Sessions"),
        dow=stacked(items, lambda i: DAYS[i["dt"].weekday()], DAYS),
        hour=stacked(items, lambda i: f"{i['dt'].hour:02d}:00", hour_order),
        court=stacked(items, lambda i: i["court_n"], courts_sorted),
        heat=heat,
        coach_all=coach_all,
        coach_private=one("Private Class"),
        coach_tp=one("Train and Play"),
        coach_other=coach_other,
        recent=recent,
        me=me_c,
        span=f"{first:%d %b %Y} – {last:%d %b %Y}",
    )


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Padel dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Manrope:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#f3f5f1;--card:#fff;--ink:#14213d;--mute:#5b6577;--line:#dfe4dc;--blue:#1b4fd8;--ball:#d4e157}
@media(prefers-color-scheme:dark){:root{--bg:#0e1626;--card:#16213a;--ink:#eaf0ff;--mute:#9aa7c2;--line:#26345a;--blue:#7ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Manrope,system-ui,sans-serif}
header{padding:40px 32px 24px;max-width:1280px;margin:auto}
h1{font:600 clamp(2rem,5vw,3.4rem)/1.05 Fraunces,Georgia,serif;margin:0}
header p{color:var(--mute);margin:8px 0 0}
main{max-width:1280px;margin:auto;padding:0 32px 64px}
h2{font:600 1.5rem Fraunces,Georgia,serif;margin:40px 0 14px;padding-left:12px;border-left:6px solid var(--ball)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k b{display:block;font:600 2rem/1.1 Fraunces,Georgia,serif;color:var(--blue)}.k span{font-weight:600}.k small{display:block;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}
.card.wide{grid-column:1/-1}.card h3{margin:0 0 10px;font-size:1rem}
.cv{height:300px;position:relative}
table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}th{color:var(--mute);font-weight:600}
.scroll{overflow-x:auto}.heat td{text-align:center;padding:5px 2px;border:2px solid var(--card);border-radius:4px;font-size:.75rem;min-width:30px}
@media(max-width:480px){.grid{grid-template-columns:1fr}main,header{padding-left:16px;padding-right:16px}}
</style></head><body>
<header><h1>Padel dashboard</h1><p id="sub"></p></header><main id="app"></main>
<script>
const D=__DATA__,app=document.getElementById('app'),esc=s=>String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
const pal=['#1b4fd8','#d4e157','#e8743b','#2aa198','#a855f7','#f472b6','#64748b'];
Chart.defaults.color=getComputedStyle(document.body).getPropertyValue('--mute');Chart.defaults.font.family='Manrope,system-ui,sans-serif';
Chart.defaults.borderColor='rgba(120,130,150,.18)';
document.getElementById('sub').textContent=`${D.me} · ${D.span}`;
function sec(t){const h=document.createElement('h2');h.textContent=t;const g=document.createElement('div');g.className='grid';app.append(h,g);return g}
function card(g,t,cls=''){const d=document.createElement('div');d.className='card '+cls;d.innerHTML=`<h3>${t}</h3>`;g.append(d);return d}
function chart(g,t,d,o={}){if(!d.labels.length)return;const c=card(g,t,o.cls),v=document.createElement('div');v.className='cv';
 const cv=document.createElement('canvas');v.append(cv);c.append(v);const ln=o.type==='line',pie=o.type==='doughnut';
 new Chart(cv,{type:o.type||'bar',data:{labels:d.labels,datasets:d.sets.map((s,i)=>({label:s.label,data:s.data,
  backgroundColor:pie?pal:ln?pal[i]+'33':pal[i%pal.length],borderColor:pie?'transparent':pal[i%pal.length],borderRadius:ln||pie?0:4,
  fill:ln,tension:.3,stepped:o.step,pointRadius:ln&&d.labels.length>30?0:3}))},
  options:{indexAxis:o.h?'y':'x',maintainAspectRatio:false,scales:pie?{}:{x:{stacked:!!o.stack,grid:{display:false},ticks:{maxTicksLimit:o.h?undefined:14}},y:{stacked:!!o.stack,beginAtZero:!ln||o.zero,ticks:{precision:o.dec?undefined:0}}},
  plugins:{legend:{display:d.sets.length>1||pie}}}})}
function table(g,t,cols,rows,cls=''){const c=card(g,t,cls);c.insertAdjacentHTML('beforeend',`<div class="scroll"><table><tr>${cols.map(x=>`<th>${x}</th>`).join('')}</tr>${rows.map(r=>`<tr>${r.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</table></div>`)}

const kg=document.createElement('div');kg.className='kpis';kg.innerHTML=D.kpis.map(k=>`<div class="k"><span>${esc(k[0])}</span><b>${esc(k[1])}</b><small>${esc(k[2])}</small></div>`).join('');app.append(kg);

let g=sec('Volume');
chart(g,'Hours per month',D.monthly,{stack:1,cls:'wide',dec:1});
chart(g,'Hours per year',D.yearly,{stack:1,dec:1});
chart(g,'Cumulative hours played',D.cumulative,{type:'line',dec:1});
chart(g,'Session mix',D.mix,{type:'doughnut'});

g=sec('Level journey');
chart(g,'Days taken to move up a level',{labels:D.ups.map(u=>u.label),sets:[{label:'Days',data:D.ups.map(u=>u.days)}]},{});
chart(g,'Matches and activities per level (before each level-up)',D.level_counts,{});
chart(g,'Hours played at each level band',D.by_level,{dec:1});
table(g,'Level timeline — days at level = days until the first session at the next band',['Level (band start)','First played','Days at level','Days since start','Sessions','Matches','Activities','Hours'],D.journey,'wide');

g=sec('Habits');
chart(g,'Sessions by start time',D.hour,{stack:1});
chart(g,'Sessions by day of week',D.dow,{stack:1});
chart(g,'Sessions by court',D.court,{stack:1});
{const c=card(g,'When you play (day × start hour)','wide'),H=D.heat,mx=Math.max(...H.flat()),hs=[...Array(24).keys()].filter(h=>H.some(r=>r[h]));
 const dn=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
 c.insertAdjacentHTML('beforeend',`<div class="scroll"><table class="heat"><tr><th></th>${hs.map(h=>`<th>${h}</th>`).join('')}</tr>${H.map((r,i)=>`<tr><th>${dn[i]}</th>${hs.map(h=>`<td style="background:rgba(27,79,216,${r[h]?.15+.85*r[h]/mx:0.05});color:${r[h]/mx>.5?'#fff':'inherit'}">${r[h]||''}</td>`).join('')}</tr>`).join('')}</table></div>`)}

g=sec('People');
chart(g,'Top 10 players you shared a match with',D.top_players,{h:1,stack:1});
table(g,'Top 10 detail',['Player','Matches','Teammate','Opponent','Avg rating','Last played'],D.top_table);

g=sec('Coaching');
chart(g,'Classes per coach',D.coach_all,{stack:1,cls:'wide'});
chart(g,'Private classes per coach',D.coach_private,{h:1});
chart(g,'Train and Play per coach',D.coach_tp,{h:1});
chart(g,'Other activities per coach',D.coach_other,{h:1,stack:1});

g=sec('Recent sessions');
table(g,'Last 15',['Date','Type','Name','Court','Level','Coach','Hours'],D.recent,'wide');
</script></body></html>"""


def render_dashboard(raw, me=None):
    """Scraper payload {"scraped_at", "items"} -> (self-contained dashboard HTML, meta dict)."""
    items, scraped = parse(raw)
    now = datetime.fromisoformat(scraped) if scraped else datetime.now()
    upcoming = [i for i in items if i["dt"] > now]
    items = [i for i in items if i["dt"] <= now]
    if not items:
        raise ValueError("We couldn't find any past sessions on your account yet.")
    names = Counter(clean(p["name"]) for i in items for p in (i.get("players") or []))
    me = clean(me) or (names.most_common(1)[0][0] if names else "Player")
    D = analyse(items, me, now.date())
    if upcoming:
        D["kpis"].append(["Upcoming", len(upcoming), "excluded from stats"])
    # "<" is escaped so scraped text can never close the <script> tag
    html = HTML.replace("__DATA__", json.dumps(D, default=str).replace("<", "\\u003c"))
    return html, dict(me=me, sessions=len(items), span=D["span"])
