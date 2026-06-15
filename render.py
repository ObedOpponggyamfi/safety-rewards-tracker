"""Server-rendered HTML for Safety Rewards Tracker (standard library only)."""

from html import escape

import adinkra
import domain as D


def esc(value):
    return escape(str(value if value is not None else ""))


# --------------------------------------------------------------------------
# Small components
# --------------------------------------------------------------------------


def symbol_img(commons_file, size=80, cls="adinkra-symbol"):
    url = adinkra.symbol_url(commons_file, width=max(64, size * 2))
    return ('<img class="%s" src="%s" alt="%s" loading="lazy" '
            'style="width:%dpx;height:%dpx;object-fit:contain" />'
            % (cls, esc(url), esc(commons_file), size, size))


def champion(rank):
    marker = D.champion_marker(rank)
    cls = "champ champ-%d" % rank if rank <= 3 else "rank-num"
    return '<span class="%s">%s</span>' % (cls, esc(marker))


def stat_card(label, value, sub=""):
    sub_html = '<div class="stat-sub">%s</div>' % esc(sub) if sub else ""
    return ('<div class="card stat"><div class="stat-label">%s</div>'
            '<div class="stat-value">%s</div>%s</div>'
            % (esc(label), esc(value), sub_html))


def badge(text, kind="muted"):
    return '<span class="badge badge-%s">%s</span>' % (kind, esc(text))


STATUS_BADGE = {
    "submitted": "warn", "approved": "ok", "rejected": "bad",
    "open": "warn", "closed": "ok", "under_review": "warn",
    "pending_finance": "warn", "finance_approved": "ok", "released": "ok",
    "finance_rejected": "bad", "budget_hold": "warn",
    "deferred_next_month": "warn", "deferred_next_quarter": "warn",
}

STATUS_LABEL = {
    "pending_finance": "Awaiting Finance Approval",
    "finance_approved": "Finance Approved",
    "finance_rejected": "Finance Rejected",
    "budget_hold": "Budget Hold",
    "deferred_next_month": "Deferred to Next Month",
    "deferred_next_quarter": "Deferred to Next Quarter",
    "released": "Released",
    "rejected": "Rejected",
}


def status_badge(status):
    label = STATUS_LABEL.get(status, status.replace("_", " ").title())
    return badge(label, STATUS_BADGE.get(status, "muted"))


# -- Department / Adinkra helpers: whenever an Adinkra appears, attach the dept --


def dept_label(dept_key):
    """Plain text: 'Akoben · HSE & Emergency Response'."""
    dep = D.dept_department(dept_key)
    return "%s · %s" % (D.dept_name(dept_key), dep) if dep else D.dept_name(dept_key)


def dept_label_html(dept_key):
    """Two-line cell: Adinkra name with its operational department beneath."""
    return '<strong>%s</strong><div class="kpi-mini">%s</div>' % (
        esc(D.dept_name(dept_key)), esc(D.dept_department(dept_key)))


def dept_symbol_cell(dept_key, size=40, extra=""):
    """Symbol + Adinkra name + attached department, used in league/leaderboards."""
    d = D.department(dept_key)
    if not d:
        return esc(dept_key)
    sub = esc(d.get("department", ""))
    if extra:
        sub += " · " + esc(extra)
    return ('<div class="symbol-cell">%s<div><strong>%s</strong>'
            '<div class="kpi-mini">%s</div></div></div>'
            % (symbol_img(d["commons_file"], size), esc(d["adinkra_name"]), sub))


# -- Reward approval workflow --
REWARD_FLOW = [
    ("pending_finance", "Employee submits"),
    ("finance_approved", "Finance approves"),
    ("released", "Reward released"),
]


def reward_flow_diagram(active=None):
    """Static 4-stage workflow diagram for the reward approval process."""
    order = {s: i for i, (s, _) in enumerate(REWARD_FLOW)}
    here = order.get(active, -1)
    cells = []
    for i, (status, label) in enumerate(REWARD_FLOW):
        state = "done" if i < here else ("now" if i == here else "todo")
        cells.append('<div class="flow-step %s"><span class="flow-dot">%d</span>%s</div>'
                     % (state, i + 1, esc(label)))
    return '<div class="flow">%s</div>' % '<span class="flow-arrow">&rarr;</span>'.join(cells)


def reward_trail(r):
    """Compact per-request progress trail with timestamps + rejection reason."""
    parts = []
    if r.get("system_validation_status"):
        parts.append('<span class="trail ok">System validated</span>')
    if r.get("finance_ts"):
        parts.append('<span class="trail ok">Finance &check; %s</span>' % esc(D.fmt_date(r["finance_ts"])))
    if r.get("released_ts"):
        parts.append('<span class="trail ok">Released %s</span>' % esc(D.fmt_date(r["released_ts"])))
    if r.get("status") in ("finance_rejected", "rejected"):
        parts.append('<span class="trail bad">Rejected by Finance: %s</span>'
                     % esc(r.get("reject_reason") or "no reason given"))
    if r.get("status") == "budget_hold":
        parts.append('<span class="trail warn">Budget hold</span>')
    if r.get("status") in ("deferred_next_month", "deferred_next_quarter"):
        label = STATUS_LABEL.get(r["status"], r["status"].replace("_", " ").title())
        parts.append('<span class="trail warn">%s</span>' % esc(label))
    return '<div class="trail-row">%s</div>' % "".join(parts) if parts else ""


# -- Charts, risk/hotspot badges, Pro-locked cards (Free Version) --


def bar_chart(rows, unit=""):
    """Simple horizontal CSS bar chart. rows = [(label, value), ...]."""
    rows = [(lbl, v) for lbl, v in rows]
    if not rows:
        return '<div class="empty">No data for this period.</div>'
    mx = max((v for _, v in rows), default=0) or 1
    bars = ""
    for label, value in rows:
        pct = round(100 * value / mx)
        bars += ('<div class="bar-row"><span class="bar-label">%s</span>'
                 '<span class="bar-track"><span class="bar-fill" style="width:%d%%"></span></span>'
                 '<span class="bar-val">%s%s</span></div>'
                 % (esc(label), pct, esc(value), esc(unit)))
    return '<div class="bar-chart">%s</div>' % bars


HOTSPOT_BADGE = {"Normal": "ok", "Watch": "warn", "High Risk": "hot", "Critical": "bad"}
RISK_BADGE = {"Low": "muted", "Medium": "warn", "High": "hot", "Critical": "bad"}


def hotspot_badge(status):
    return badge(status, HOTSPOT_BADGE.get(status, "muted"))


def risk_badge(level):
    return badge(level, RISK_BADGE.get(level, "muted")) if level else '<span class="kpi-mini">—</span>'


def pro_badge():
    return '<span class="pro-badge">Available in Pro</span>'


def pro_card(title, desc=""):
    return ('<div class="card pro-locked"><div class="pro-top"><span class="pro-lock">🔒</span>%s</div>'
            '<h3>%s</h3><div class="hint">%s</div>'
            '<a class="btn gold sm" href="/pro">Upgrade</a></div>'
            % (pro_badge(), esc(title), esc(desc)))


def limit_banner(text):
    return ('<div class="limit-banner"><strong>Free plan limit.</strong> %s '
            '<a href="/pro">See Pro &rarr;</a></div>' % esc(text))


# -- AI Safety Prediction --
AI_LEVEL_BADGE = {"Low": "ok", "Moderate": "warn", "High": "hot", "Critical": "bad"}


def ai_level_badge(level):
    return badge(level, AI_LEVEL_BADGE.get(level, "muted"))


def confidence_badge(label):
    return badge("Confidence: %s" % label, {"High": "ok", "Medium": "warn", "Low": "bad"}.get(label, "muted"))


def ai_pred_card(pred, title=None):
    """Explainable AI prediction card — score, level, factors, recommendation."""
    name = title or pred["entity_name"]
    score = pred["risk_score"]
    over = " over" if pred["risk_level"] in ("High", "Critical") else ""
    bar = '<div class="progress%s"><span style="width:%d%%"></span></div>' % (over, min(100, score))
    return ('<div class="card ai-card"><div class="ai-head"><h3>%s</h3>%s</div>'
            '<div class="ai-score"><span class="ai-score-num">%d</span><span class="ai-score-max">/100</span></div>%s'
            '<div class="ai-factors">%s</div>'
            '<div class="ai-rec"><strong>Recommended:</strong> %s</div>'
            '<div class="ai-meta">%s · %s</div></div>'
            % (esc(name), ai_level_badge(pred["risk_level"]), score, bar,
               esc(pred["contributing_factors"]), esc(pred["recommended_action"]),
               esc(pred["prediction_period"]), confidence_badge(pred["confidence_label"])))


def ai_disclaimer():
    return '<div class="ai-disclaimer">%s</div>' % esc(D.AI_DISCLAIMER)


def table(headers, rows, empty="No records."):
    if not rows:
        return '<div class="empty">%s</div>' % esc(empty)
    head = "".join("<th>%s</th>" % h for h in headers)  # headers may contain markup
    body = ""
    for r in rows:
        body += "<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>"
    return ('<div class="table-wrap"><table><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (head, body))


def flash(msg):
    if not msg:
        return ""
    return '<div class="flash">%s</div>' % esc(msg)


def section(title, body, actions=""):
    act = '<div class="section-actions">%s</div>' % actions if actions else ""
    return ('<section class="block"><div class="block-head"><h2>%s</h2>%s</div>%s</section>'
            % (esc(title), act, body))


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------


def nav_for(user):
    """Return nav groups -> list of item dicts the user may see."""
    return D.nav_for(user)


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------


def page(title, user, body, active="/", msg=""):
    nav_html = ""
    for group_name, items in nav_for(user):
        links = ""
        for item in items:
            href = item["route"]
            label = item["label"]
            cls = "active" if href == active else ""
            links += '<a class="%s" href="%s">%s</a>' % (cls, href, esc(label))
        nav_html += '<div class="nav-group"><span class="nav-group-title">%s</span>%s</div>' % (esc(group_name), links)

    brand = adinkra.BRAND_SYMBOL
    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>%(title)s · Safety Rewards Tracker</title>
<style>%(css)s</style>
</head><body>
<div class="layout">
  <aside class="sidebar">
    <div class="brand">%(brand_img)s
      <div><strong>Safety Rewards</strong><span>Reward Safety. Save Lives.</span></div>
    </div>
    <nav>%(nav)s</nav>
  </aside>
  <main>
    <header class="topbar">
      <div class="crumb">%(title)s</div>
      <div class="who">
        <span class="who-name">%(uname)s</span>
        <span class="who-role">%(urole)s</span>
        <a class="logout" href="/logout">Sign out</a>
      </div>
    </header>
    <div class="content">
      %(flash)s
      %(body)s
    </div>
    <footer class="foot">Safety Rewards Tracker · Adinkra symbols courtesy of
      <a href="https://commons.wikimedia.org/wiki/Category:SVG_Adinkra_symbols" target="_blank" rel="noopener">Wikimedia Commons</a></footer>
  </main>
</div>
<script>%(js)s</script>
</body></html>""" % {
        "title": esc(title),
        "css": CSS,
        "js": JS,
        "brand_img": symbol_img(brand["commons_file"], size=34, cls="brand-symbol"),
        "nav": nav_html,
        "uname": esc(user["name"]),
        "urole": esc(D.user_role_label(user) or D.role_label(user["role"])),
        "flash": flash(msg),
        "body": body,
    }


def login_page(users_by_role, msg=""):
    brand = adinkra.BRAND_SYMBOL
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sign in · Safety Rewards Tracker</title>
<style>%(css)s</style></head>
<body class="login-body">
<div class="login-card">
  <div class="login-brand">%(brand)s<h1>Safety Rewards Tracker</h1>
  <p>Reward Safety. Reduce Risk. Save Lives.</p></div>
  %(flash)s
  <form method="post" action="/login">
    <label>Employee ID</label>
    <input name="employee_id" autocomplete="username" required>
    <label>Password</label>
    <input name="password" type="password" inputmode="numeric" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
  <p class="login-note">Use your Employee ID. The password is the last 4 digits of your phone number.
  Menus and routes are permission-gated for Worker, Champion, Supervisor, HSE,
  Finance, Management and System Administrator demos.</p>
</div>
</body></html>""" % {
        "css": CSS,
        "brand": symbol_img(brand["commons_file"], size=56, cls="brand-symbol"),
        "flash": flash(msg),
    }


# --------------------------------------------------------------------------
# Filter helpers (shared by leaderboards / reports / budgets)
# --------------------------------------------------------------------------


def month_select(name, value, with_quarter_hint=True, onchange=True):
    opts = ""
    for m in range(1, 13):
        sel = "selected" if m == value else ""
        opts += '<option value="%d" %s>%s</option>' % (m, sel, D.month_name(m))
    oc = ' data-quarter-source="1"' if onchange else ""
    return '<select name="%s"%s>%s</select>' % (name, oc, opts)


def quarter_box(month):
    q = D.quarter_of_month(month)
    months = ", ".join(D.month_name(m) for m in D.quarter_months(q))
    return ('<span class="quarter-box" id="quarterBox" data-q="%d">'
            '<strong>%s</strong> <span class="q-months">(%s)</span></span>'
            % (q, D.quarter_label(q), esc(months)))


def _sel(name, label, options, blank="—"):
    opts = ('<option value="">%s</option>' % esc(blank)) + "".join("<option>%s</option>" % esc(o) for o in options)
    return '<div class="field"><label>%s</label><select name="%s">%s</select></div>' % (esc(label), name, opts)


def hse_fields(include_cause=False, include_lost_days=False):
    """Risk + actual/potential consequence (+ optional cause) field group,
    shared by the report forms. Supports the data-quality override reason."""
    rows = ('<div class="row-inline">%s%s%s</div>'
            % (_sel("risk_level", "Risk level", D.RISK_LEVELS),
               _sel("actual_consequence", "Actual consequence", D.CONSEQUENCES),
               _sel("potential_consequence", "Potential consequence", D.CONSEQUENCES)))
    extra = ""
    if include_cause:
        extra += '<div class="row-inline">%s<div class="field"><label>Sub-location (optional)</label><input name="sub_location"></div></div>' % _sel("cause_category", "Cause category", D.CAUSE_CATEGORIES)
    else:
        extra += '<div class="field"><label>Sub-location (optional)</label><input name="sub_location"></div>'
    if include_lost_days:
        extra += '<div class="field"><label>Lost work days (required for an LTI)</label><input name="lost_days" type="number" min="0" value="0"></div>'
    extra += '<div class="field"><label>Reviewer override reason (only if a data-quality warning appears)</label><input name="override_reason" placeholder="optional"></div>'
    return rows + extra


# --------------------------------------------------------------------------
# CSS + JS
# --------------------------------------------------------------------------
CSS = """
:root{
  --bg:#f4f1ea; --panel:#ffffff; --ink:#1c1a17; --muted:#6b6457;
  --navy:#13303d; --navy-2:#1c4456; --gold:#d4a017; --gold-soft:#f3e2b3;
  --green:#1f9d55; --red:#c0392b; --warn:#c98a17; --line:#e6e0d4;
  --shadow:0 1px 3px rgba(20,17,15,.08),0 6px 24px rgba(20,17,15,.06);
}
*{box-sizing:border-box}
body{margin:0;font-family:"Segoe UI",system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--ink);font-size:14.5px;line-height:1.5}
a{color:var(--navy-2);text-decoration:none}
a:hover{text-decoration:underline}
.layout{display:flex;min-height:100vh}
.sidebar{width:248px;flex:0 0 248px;background:var(--navy);color:#e8eef0;position:sticky;top:0;height:100vh;overflow:auto;padding:18px 0}
.brand{display:flex;gap:10px;align-items:center;padding:0 18px 16px;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:10px}
.brand strong{display:block;font-size:15px}
.brand span{display:block;font-size:11px;color:#9fb6bf}
.brand-symbol{background:#fff;border-radius:8px;padding:3px}
.nav-group{padding:8px 10px 4px}
.nav-group-title{display:block;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#7f9aa4;padding:6px 8px 2px}
.nav-group a{display:block;color:#d7e2e6;padding:7px 10px;border-radius:7px;font-size:13.5px}
.nav-group a:hover{background:rgba(255,255,255,.07);text-decoration:none}
.nav-group a.active{background:var(--gold);color:#2a2305;font-weight:600}
main{flex:1;display:flex;flex-direction:column;min-width:0}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:14px 26px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.crumb{font-weight:600;font-size:16px}
.who{display:flex;align-items:center;gap:12px;font-size:13px}
.who-name{font-weight:600}
.who-role{background:var(--gold-soft);color:#6a5410;padding:2px 9px;border-radius:20px;font-size:12px}
.logout{color:var(--red)}
.content{padding:24px 26px;flex:1}
.foot{padding:14px 26px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}
h2{font-size:17px;margin:0}
.block{margin-bottom:26px}
.block-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:12px;flex-wrap:wrap}
.section-actions{display:flex;gap:8px;flex-wrap:wrap}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:var(--shadow)}
.grid{display:grid;gap:14px}
.grid.cols-4{grid-template-columns:repeat(4,1fr)}
.grid.cols-3{grid-template-columns:repeat(3,1fr)}
.grid.cols-2{grid-template-columns:repeat(2,1fr)}
@media(max-width:920px){.grid.cols-4,.grid.cols-3,.grid.cols-2{grid-template-columns:1fr 1fr}}
.stat .stat-label{color:var(--muted);font-size:12.5px}
.stat .stat-value{font-size:26px;font-weight:700;color:var(--navy)}
.stat .stat-sub{color:var(--muted);font-size:12px;margin-top:2px}
.table-wrap{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);font-size:13.5px;vertical-align:middle}
th{background:#faf7f0;color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;position:sticky;top:0}
tbody tr:hover{background:#fbfaf6}
.empty{padding:22px;color:var(--muted);background:var(--panel);border:1px dashed var(--line);border-radius:12px;text-align:center}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11.5px;font-weight:600}
.badge-ok{background:#dff3e6;color:#176c3c}
.badge-warn{background:#fbeccd;color:#8a5e08}
.badge-bad{background:#fadbd6;color:#962014}
.badge-muted{background:#ece8df;color:#5d564a}
.champ{font-size:18px}
.rank-num{color:var(--muted);font-weight:600}
.flash{background:var(--gold-soft);border:1px solid var(--gold);color:#6a5410;padding:10px 14px;border-radius:10px;margin-bottom:18px}
.btn,button{font:inherit;cursor:pointer;border:1px solid var(--navy);background:var(--navy);color:#fff;padding:8px 14px;border-radius:9px;font-size:13.5px}
.btn:hover,button:hover{background:var(--navy-2);text-decoration:none}
.btn.ghost{background:#fff;color:var(--navy)}
.btn.gold{background:var(--gold);border-color:var(--gold);color:#2a2305}
.btn.ok{background:var(--green);border-color:var(--green)}
.btn.bad{background:var(--red);border-color:var(--red)}
.btn.sm{padding:5px 10px;font-size:12.5px;border-radius:7px}
form.inline{display:inline}
.field{margin-bottom:14px}
.field label{display:block;font-size:13px;font-weight:600;margin-bottom:5px}
input,select,textarea{font:inherit;width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:9px;background:#fff}
textarea{min-height:84px;resize:vertical}
.form-card{max-width:620px}
.row-inline{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.row-inline .field{flex:1;min-width:150px;margin-bottom:0}
.filter-bar{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:18px;box-shadow:var(--shadow)}
.filter-bar .field{margin-bottom:0}
.quarter-box{display:inline-block;background:var(--gold-soft);color:#6a5410;padding:8px 12px;border-radius:9px;font-size:13.5px}
.q-months{color:#8a7a45;font-size:12px}
.adinkra-card{display:flex;gap:16px;align-items:center}
.adinkra-card .adinkra-symbol{background:#fff;border:1px solid var(--line);border-radius:12px;padding:8px}
.adinkra-meta h3{margin:0 0 2px;font-size:16px}
.adinkra-meta .meaning{color:var(--muted);font-size:13px}
.adinkra-meta .motto{color:var(--gold);font-style:italic;font-size:13px;margin-top:4px}
.progress{height:8px;background:#ece8df;border-radius:6px;overflow:hidden;margin-top:6px}
.progress > span{display:block;height:100%;background:var(--green)}
.progress.over > span{background:var(--red)}
.pill-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.pill-row a{background:#fff;border:1px solid var(--line);padding:7px 14px;border-radius:20px;font-size:13px}
.pill-row a.active{background:var(--navy);color:#fff}
.lock-tag{color:var(--muted);font-size:12px}
.hint{color:var(--muted);font-size:12.5px}
.kpi-mini{font-size:12px;color:var(--muted)}
.symbol-cell{display:flex;align-items:center;gap:10px}
.flow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:18px;box-shadow:var(--shadow)}
.flow-step{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.flow-step .flow-dot{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:#ece8df;color:#5d564a;font-size:12px;font-weight:700}
.flow-step.done{color:var(--ink)}
.flow-step.done .flow-dot{background:var(--green);color:#fff}
.flow-step.now{color:var(--navy);font-weight:600}
.flow-step.now .flow-dot{background:var(--gold);color:#2a2305}
.flow-arrow{color:var(--muted)}
.trail-row{display:flex;gap:6px;flex-wrap:wrap}
.trail{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:12px;background:#ece8df;color:#5d564a}
.trail.ok{background:#dff3e6;color:#176c3c}
.trail.bad{background:#fadbd6;color:#962014}
.trail.warn{background:#fbeccd;color:#8a5e08}
.badge-hot{background:#fbe2cf;color:#9a4d10}
.bar-chart{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.bar-row{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}
.bar-label{flex:0 0 38%;text-align:right;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{flex:1;background:#ece8df;border-radius:6px;height:14px;overflow:hidden}
.bar-fill{display:block;height:100%;background:linear-gradient(90deg,var(--navy-2),var(--gold))}
.bar-val{flex:0 0 60px;color:var(--muted);font-weight:600}
.pro-locked{position:relative;border-style:dashed;background:#fbfaf6}
.pro-locked h3{margin:6px 0 4px;font-size:15px;color:var(--navy)}
.pro-top{display:flex;justify-content:space-between;align-items:center}
.pro-lock{font-size:18px;opacity:.7}
.pro-badge{display:inline-block;background:var(--gold-soft);color:#6a5410;font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px}
.pro-locked .btn{margin-top:10px}
.limit-banner{background:#eef3f5;border:1px solid #cfe0e6;color:#1c4456;padding:10px 14px;border-radius:10px;margin-bottom:18px;font-size:13px}
.ai-card{display:flex;flex-direction:column;gap:8px}
.ai-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.ai-head h3{margin:0;font-size:15px}
.ai-score-num{font-size:26px;font-weight:700;color:var(--navy)}
.ai-score-max{color:var(--muted);font-size:13px}
.ai-factors{font-size:13px;line-height:1.5}
.ai-rec{font-size:13px;background:#f4f1ea;border-radius:8px;padding:8px 10px}
.ai-meta{font-size:12px;color:var(--muted);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ai-disclaimer{font-size:12px;color:var(--muted);background:#fbfaf6;border:1px dashed var(--line);border-radius:10px;padding:10px 14px;margin-bottom:18px}
.included-badge{display:inline-block;background:#dff3e6;color:#176c3c;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:20px}
@media print{
  .sidebar,.topbar,.foot,.btn,button,.filter-bar a,.section-actions,.pro-locked,.limit-banner{display:none!important}
  .layout,main,.content{display:block;padding:0}
  .card,.table-wrap,.bar-chart{box-shadow:none;border-color:#ccc;break-inside:avoid}
  body{background:#fff}
}
"""

JS = """
document.addEventListener('change', function(e){
  var el = e.target;
  if(el && el.matches('[data-quarter-source]')){
    var box = document.getElementById('quarterBox');
    if(box){
      var m = parseInt(el.value,10);
      var q = Math.floor((m-1)/3)+1;
      var names=['January','February','March','April','May','June','July','August','September','October','November','December'];
      var qm=[(q-1)*3, (q-1)*3+1, (q-1)*3+2].map(function(i){return names[i];}).join(', ');
      box.querySelector('strong').textContent = 'Q'+q;
      var qmEl = box.querySelector('.q-months');
      if(qmEl) qmEl.textContent = '('+qm+')';
      box.setAttribute('data-q', q);
      var hidden = document.querySelector('input[name=quarter_auto]');
      if(hidden) hidden.value = q;
    }
  }
});
document.addEventListener('submit', function(e){
  var f=e.target;
  if(f.matches('[data-confirm]') && !window.confirm(f.getAttribute('data-confirm'))){
    e.preventDefault();
  }
});
"""
