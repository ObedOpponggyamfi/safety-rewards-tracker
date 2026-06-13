"""Safety Rewards Tracker -- Python standard-library MVP.

Run:
    python app.py            # serves http://localhost:8090

No npm, no pip, no framework. Server-rendered HTML/CSS with JSON persistence.
"""

import csv
import io
import os
import secrets
import webbrowser
from collections import Counter
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Timer
from urllib.parse import parse_qs, urlencode, urlparse

import adinkra
import domain as D
import render as R

PORT = int(os.environ.get("PORT", "8090"))
SESSIONS = {}  # token -> user_id


# --------------------------------------------------------------------------
# Tiny request helpers
# --------------------------------------------------------------------------


def q1(qs, key, default=None):
    vals = qs.get(key)
    return vals[0] if vals else default


def qint(qs, key, default=None):
    v = q1(qs, key)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def redirect(path, msg=None):
    if msg:
        sep = "&" if "?" in path else "?"
        path = "%s%s%s" % (path, sep, urlencode({"m": msg}))
    return ("redirect", path)


def page(title, active, body):
    return ("page", title, active, body)


# --------------------------------------------------------------------------
# Page bodies
# --------------------------------------------------------------------------


def body_dashboard(user, qs):
    yr, mo = D.today().year, D.today().month
    q = D.quarter_of_month(mo)
    obs = len([o for o in D.DB["safety_observations"]])
    hid = len(D.DB["near_miss_hazard_reports"])
    inc = len(D.DB["incidents"])
    open_actions = len([a for a in D.DB["corrective_actions"] if a["status"] == "open"])
    pending_reviews = len([o for o in D.DB["safety_observations"] if o["status"] == "submitted"])
    pending_rewards = len([r for r in D.DB["reward_requests"] if r["status"] == "pending_admin"])

    cards = (
        R.stat_card("Observations", obs, "all time")
        + R.stat_card("Hazards / Near-misses", hid)
        + R.stat_card("Incidents", inc)
        + R.stat_card("Open corrective actions", open_actions)
    )
    top = '<div class="grid cols-4">%s</div>' % cards

    # Personal panel for workers / contractors.
    personal = ""
    if user["role"] == "worker":
        bal = D.user_balance(user["id"])
        mpts = D.user_points(user["id"], year=yr, month=mo)
        my_week = D.user_points(user["id"], year=yr, month=mo, week=D.week_in_month(D.today()))
        personal = R.section("My safety points",
            '<div class="grid cols-3">%s%s%s</div>' % (
                R.stat_card("Spendable balance", "%d pts" % bal),
                R.stat_card("Earned this month", "%d pts" % mpts, "%s %d" % (D.month_name(mo), yr)),
                R.stat_card("Earned this week", "%d pts" % my_week, D.week_label(D.today())),
            ))

    # Department snapshot.
    dept = D.department(user["dept_key"])
    dept_html = ""
    if dept:
        limit = D.dept_monthly_limit(dept)
        used = D.dept_budget_used(dept["key"], yr, mo)
        pts = D.dept_points(dept["key"], year=yr, month=mo)
        dept_html = R.section("My department",
            '<div class="card adinkra-card">%s<div class="adinkra-meta">'
            '<h3>%s</h3><div class="kpi-mini">%s</div><div class="meaning">%s</div><div class="motto">%s</div>'
            '<div class="hint" style="margin-top:8px">Points this month: <strong>%d</strong> · '
            'Active employees: <strong>%d</strong> · Monthly limit: <strong>%s</strong> · '
            'Used: <strong>%s</strong></div></div></div>' % (
                R.symbol_img(dept["commons_file"], 84), R.esc(dept["adinkra_name"]),
                R.esc(dept.get("department", "")), R.esc(dept["meaning"]), R.esc(dept["motto"]), pts,
                dept["active_employees"], D.fmt_money(limit), D.fmt_money(used)))

    # Action items by role.
    todo = []
    if user["role"] in D.REVIEW_ROLES and pending_reviews:
        todo.append('<a class="btn gold" href="/review">Review queue (%d)</a>' % pending_reviews)
    if user["role"] in D.REWARD_APPROVE_ROLES and pending_rewards:
        todo.append('<a class="btn gold" href="/rewards/approvals">Reward approvals (%d)</a>' % pending_rewards)
    if user["role"] in D.REWARD_RELEASE_ROLES:
        fin = len([r for r in D.DB["reward_requests"]
                   if r["status"] in ("pending_finance", "finance_approved")])
        if fin:
            todo.append('<a class="btn gold" href="/rewards/releases">Finance queue (%d)</a>' % fin)
    todo.append('<a class="btn" href="/report/observation">Report an observation</a>')
    todo.append('<a class="btn ghost" href="/league">Adinkra League</a>')
    todo_html = R.section("Quick actions", '<div class="pill-row">%s</div>' % "".join(todo))

    league_preview = league_table(year=yr, month=mo, limit=3)
    preview = R.section("Adinkra League · top departments this month", league_preview,
                        actions='<a class="btn sm ghost" href="/league">Full league</a>')

    return top + personal + todo_html + dept_html + preview


def body_observation_form(user, qs):
    depts = "".join('<option value="%s">%s</option>' % (d["key"], R.esc(d["adinkra_name"])) for d in D.DB["departments"])
    cats = ["Unsafe act", "Unsafe condition", "Good practice", "Housekeeping", "PPE"]
    cat_opts = "".join("<option>%s</option>" % c for c in cats)
    sel_dept = lambda k: ' selected' if k == user["dept_key"] else ''
    dept_opts = "".join('<option value="%s"%s>%s</option>' % (d["key"], sel_dept(d["key"]), R.esc("%s — %s" % (d["adinkra_name"], d.get("department", "")))) for d in D.DB["departments"])
    form = """<form method="post" action="/report/observation" class="card form-card">
      <div class="row-inline">
        <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
        <div class="field"><label>Category</label><select name="category">%s</select></div>
      </div>
      <div class="field"><label>Location</label><input name="location" placeholder="e.g. Process Plant" required></div>
      <div class="field"><label>What did you observe?</label><textarea name="description" required></textarea></div>
      <button class="btn gold" type="submit">Submit observation (+%d pts on approval)</button>
    </form>""" % (dept_opts, cat_opts, D.POINTS["observation"])
    recent = [o for o in D.DB["safety_observations"] if o["reporter_id"] == user["id"]][-6:][::-1]
    rows = [[D.fmt_date(o["ts"]), R.esc(o["category"]), R.esc(o["location"]), R.status_badge(o["status"])] for o in recent]
    return R.section("Report a safety observation", form) + \
        R.section("My recent observations", R.table(["Date", "Category", "Location", "Status"], rows, "No observations yet."))


def body_hid_form(user, qs):
    sel_dept = lambda k: ' selected' if k == user["dept_key"] else ''
    dept_opts = "".join('<option value="%s"%s>%s</option>' % (d["key"], sel_dept(d["key"]), R.esc("%s — %s" % (d["adinkra_name"], d.get("department", "")))) for d in D.DB["departments"])
    form = """<form method="post" action="/report/hid" class="card form-card">
      <div class="row-inline">
        <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
        <div class="field"><label>Type</label><select name="type"><option>Hazard</option><option>Near miss</option></select></div>
        <div class="field"><label>Severity</label><select name="severity"><option>Low</option><option>Medium</option><option>High</option></select></div>
      </div>
      <div class="field"><label>Location</label><input name="location" required></div>
      <div class="field"><label>Describe the hazard or near-miss</label><textarea name="description" required></textarea></div>
      <button class="btn gold" type="submit">Submit report (+%d pts on approval)</button>
    </form>""" % (dept_opts, D.POINTS["hid"])
    return R.section("Hazard / Near-miss report (HID)", form)


def body_incident_form(user, qs):
    sel_dept = lambda k: ' selected' if k == user["dept_key"] else ''
    dept_opts = "".join('<option value="%s"%s>%s</option>' % (d["key"], sel_dept(d["key"]), R.esc("%s — %s" % (d["adinkra_name"], d.get("department", "")))) for d in D.DB["departments"])
    form = """<form method="post" action="/report/incident" class="card form-card">
      <div class="row-inline">
        <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
        <div class="field"><label>Severity</label><select name="severity"><option>Minor</option><option>Moderate</option><option>Serious</option><option>Lost Time Injury</option></select></div>
      </div>
      <div class="field"><label>Location</label><input name="location" required></div>
      <div class="field"><label>Describe the incident</label><textarea name="description" required></textarea></div>
      <label class="field"><input type="checkbox" name="lti" value="1" style="width:auto;margin-right:8px">This was a Lost Time Injury (triggers department point reset)</label>
      <button class="btn gold" type="submit">Report incident (+%d pts)</button>
      <p class="hint">Reporting incidents promptly is rewarded. A Lost Time Injury resets the department's monthly safety points and is logged for audit.</p>
    </form>""" % (dept_opts, D.POINTS["incident"])
    return R.section("Report an incident", form)


def body_review(user, qs):
    pending = [o for o in D.DB["safety_observations"] if o["status"] == "submitted"]
    pending.sort(key=lambda o: o["ts"])
    rows = []
    for o in pending:
        rep = D.user(o["reporter_id"])
        actions = ("""<form class="inline" method="post" action="/review">
            <input type="hidden" name="id" value="%d"><input type="hidden" name="action" value="approve">
            <button class="btn ok sm">Approve +%d</button></form>
          <form class="inline" method="post" action="/review">
            <input type="hidden" name="id" value="%d"><input type="hidden" name="action" value="reject">
            <button class="btn bad sm">Reject</button></form>"""
            % (o["id"], D.POINTS["observation"], o["id"]))
        rows.append([D.fmt_date(o["ts"]), R.esc(rep["name"] if rep else "?"),
                     R.dept_label_html(o["dept_key"]), R.esc(o["category"]),
                     R.esc(o["location"]), actions])
    return R.section("Supervisor review & approval queue",
                     R.table(["Date", "Reporter", "Department", "Category", "Location", "Decision"], rows, "Queue is clear."))


def body_actions(user, qs):
    create = ""
    if user["role"] in D.REVIEW_ROLES:
        dept_opts = "".join('<option value="%s">%s</option>' % (d["key"], R.esc("%s — %s" % (d["adinkra_name"], d.get("department", "")))) for d in D.DB["departments"])
        worker_opts = "".join('<option value="%d">%s</option>' % (u["id"], R.esc(u["name"])) for u in D.DB["users"] if u["role"] == "worker")
        create = R.section("Raise a corrective action", """<form method="post" action="/actions" class="card form-card">
          <input type="hidden" name="action" value="create">
          <div class="row-inline">
            <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
            <div class="field"><label>Owner</label><select name="owner_id">%s</select></div>
            <div class="field"><label>Due date</label><input type="date" name="due"></div>
          </div>
          <div class="field"><label>Action</label><textarea name="description" required></textarea></div>
          <button class="btn">Create action</button></form>""" % (dept_opts, worker_opts))

    open_actions = [a for a in D.DB["corrective_actions"] if a["status"] == "open"]
    open_actions.sort(key=lambda a: a.get("due") or "")
    rows = []
    today = D.today().isoformat()
    for a in open_actions:
        owner = D.user(a["owner_id"])
        overdue = a.get("due") and a["due"] < today
        due_cell = R.esc(D.fmt_date(a["due"])) if a.get("due") else "&mdash;"
        if overdue:
            due_cell = '<span class="badge badge-bad">%s</span>' % R.esc(D.fmt_date(a["due"]))
        close_btn = """<form class="inline" method="post" action="/actions">
            <input type="hidden" name="action" value="close"><input type="hidden" name="id" value="%d">
            <button class="btn ok sm">Close +%d</button></form>""" % (a["id"], D.POINTS["action_closed"])
        rows.append([R.dept_label_html(a["dept_key"]), R.esc(owner["name"] if owner else "?"),
                     R.esc(a["description"]), due_cell, close_btn])
    open_tbl = R.table(["Department", "Owner", "Action", "Due", ""], rows, "No open actions.")
    closed = [a for a in D.DB["corrective_actions"] if a["status"] == "closed"][-8:][::-1]
    crows = [[R.dept_label_html(a["dept_key"]), R.esc((D.user(a["owner_id"]) or {}).get("name", "?")),
              R.esc(a["description"]), D.fmt_date(a.get("closed_ts") or a["ts"])] for a in closed]
    closed_tbl = R.table(["Department", "Owner", "Action", "Closed"], crows, "No closed actions yet.")
    return create + R.section("Open corrective actions", open_tbl) + R.section("Recently closed", closed_tbl)


def body_points(user, qs):
    dept = q1(qs, "dept")
    entries = list(D.DB["safety_points"])
    if dept:
        entries = [p for p in entries if p["dept_key"] == dept]
    if user["role"] == "worker":
        entries = [p for p in entries if p["user_id"] == user["id"]]
    entries.sort(key=lambda p: p["ts"], reverse=True)
    rows = []
    for p in entries[:200]:
        u = D.user(p["user_id"])
        rows.append([D.fmt_date(p["ts"]), R.esc(u["name"] if u else "?"),
                     R.dept_label_html(p["dept_key"]), R.esc(p["reason"]),
                     '<strong>+%d</strong>' % p["points"]])
    dept_filter = ""
    if user["role"] != "worker":
        opts = '<option value="">All departments</option>' + "".join(
            '<option value="%s"%s>%s</option>' % (d["key"], " selected" if d["key"] == dept else "", R.esc(d["adinkra_name"]))
            for d in D.DB["departments"])
        dept_filter = """<form class="filter-bar" method="get">
          <div class="field"><label>Department</label><select name="dept" onchange="this.form.submit()">%s</select></div>
          <a class="btn ghost" href="/points.csv?%s">Export CSV</a></form>""" % (opts, urlencode({"dept": dept or ""}))
    return dept_filter + R.section("Safety points ledger",
        R.table(["Date", "Worker", "Department", "Reason", "Points"], rows, "No points recorded."))


def body_rewards(user, qs):
    bal = D.user_balance(user["id"])
    head = R.stat_card("My spendable balance", "%d pts" % bal) if user["role"] == "worker" else ""
    cards = ""
    for rw in D.DB["rewards"]:
        if not rw["active"]:
            continue
        can_afford = user["role"] == "worker" and bal >= rw["point_cost"]
        btn = ""
        if user["role"] == "worker":
            disabled = "" if can_afford else "disabled"
            btn = """<form method="post" action="/rewards" style="margin-top:10px">
                <input type="hidden" name="action" value="request"><input type="hidden" name="reward_id" value="%d">
                <button class="btn gold sm" %s>Request</button></form>""" % (rw["id"], disabled)
        cards += """<div class="card"><h3 style="margin:0 0 4px">%s</h3>
            <div class="hint">%s</div>
            <div style="margin-top:8px"><strong>%d pts</strong> <span class="kpi-mini">· value %s</span></div>%s</div>""" % (
            R.esc(rw["name"]), R.esc(rw["description"]), rw["point_cost"], D.fmt_money(rw["cash_value"]), btn)
    catalogue = R.section("Reward catalogue", '<div class="grid cols-3">%s</div>' % cards)

    mine = [r for r in D.DB["reward_requests"] if r["user_id"] == user["id"]]
    mine.sort(key=lambda r: r["ts"], reverse=True)
    mrows = [[D.fmt_date(r["ts"]), R.esc(D.reward(r["reward_id"])["name"]), "%d pts" % r["point_cost"],
              D.fmt_money(r["cash_value"]), R.status_badge(r["status"]), R.reward_trail(r) or "&mdash;"]
             for r in mine]
    my_section = ""
    if user["role"] == "worker":
        my_section = (R.reward_flow_diagram()
                      + R.section("My reward requests",
                                  R.table(["Date", "Reward", "Cost", "Value", "Status", "Progress"],
                                          mrows, "No requests yet.")))
    return (head and '<div class="grid cols-3" style="margin-bottom:18px">%s</div>' % head or "") + catalogue + my_section


def body_reward_approvals(user, qs):
    pending = [r for r in D.DB["reward_requests"] if r["status"] == "pending_admin"]
    pending.sort(key=lambda r: r["ts"])
    rows = []
    for r in pending:
        u = D.user(r["user_id"])
        bal = D.user_balance(r["user_id"])
        decide = """<form class="inline" method="post" action="/rewards/approvals">
            <input type="hidden" name="id" value="%d">
            <button class="btn ok sm" name="action" value="approve">Approve</button></form>
          <form class="inline" method="post" action="/rewards/approvals">
            <input type="hidden" name="id" value="%d">
            <input name="reason" placeholder="Reason (if rejecting)" style="width:160px;display:inline-block">
            <button class="btn bad sm" name="action" value="reject">Reject</button></form>""" % (r["id"], r["id"])
        rows.append([D.fmt_date(r["ts"]), R.esc(u["name"] if u else "?"), R.dept_label_html(r["dept_key"]),
                     R.esc(D.reward(r["reward_id"])["name"]), "%d pts" % r["point_cost"],
                     "%d pts" % bal, decide])
    note = '<p class="hint">Step 2 of the workflow. Approved requests move on to the Finance Manager.</p>'
    return R.reward_flow_diagram("pending_admin") + note + R.section("Reward approvals · Admin",
        R.table(["Date", "Worker", "Department", "Reward", "Cost", "Balance", "Decision"], rows, "Nothing awaiting admin approval."))


def body_reward_finance(user, qs):
    yr, mo = D.today().year, D.today().month

    # Step 3 -- Finance Manager approval of admin-approved requests.
    pend = [r for r in D.DB["reward_requests"] if r["status"] == "pending_finance"]
    pend.sort(key=lambda r: r["ts"])
    prows = []
    for r in pend:
        u = D.user(r["user_id"])
        decide = """<form class="inline" method="post" action="/rewards/releases">
            <input type="hidden" name="id" value="%d">
            <button class="btn ok sm" name="action" value="fin_approve">Approve</button></form>
          <form class="inline" method="post" action="/rewards/releases">
            <input type="hidden" name="id" value="%d">
            <input name="reason" placeholder="Reason (if rejecting)" style="width:160px;display:inline-block">
            <button class="btn bad sm" name="action" value="reject">Reject</button></form>""" % (r["id"], r["id"])
        prows.append([D.fmt_date(r["ts"]), R.esc(u["name"] if u else "?"), R.dept_label_html(r["dept_key"]),
                      R.esc(D.reward(r["reward_id"])["name"]), D.fmt_money(r["cash_value"]), decide])
    finance_tbl = R.section("Finance approval · step 3",
        R.table(["Submitted", "Worker", "Department", "Reward", "Value", "Decision"], prows,
                "Nothing awaiting finance approval."))

    # Step 4 -- release the finance-approved rewards (budget check shown).
    appr = [r for r in D.DB["reward_requests"] if r["status"] == "finance_approved"]
    appr.sort(key=lambda r: r["ts"])
    rrows = []
    for r in appr:
        u = D.user(r["user_id"])
        dept = D.department(r["dept_key"])
        limit = D.dept_monthly_limit(dept) if dept else 0
        used = D.dept_budget_used(r["dept_key"], yr, mo)
        flag = R.badge("Within limit", "ok") if (used + r["cash_value"]) <= limit else R.badge("Over dept limit", "bad")
        btn = """<form class="inline" method="post" action="/rewards/releases">
            <input type="hidden" name="id" value="%d">
            <button class="btn ok sm" name="action" value="release">Release %s</button></form>""" % (r["id"], D.fmt_money(r["cash_value"]))
        rrows.append([D.fmt_date(r["ts"]), R.esc(u["name"] if u else "?"), R.dept_label_html(r["dept_key"]),
                      R.esc(D.reward(r["reward_id"])["name"]), D.fmt_money(r["cash_value"]), flag, btn])
    release_tbl = R.section("Reward release · step 4",
        R.table(["Submitted", "Worker", "Department", "Reward", "Value", "Dept budget", "Release"], rrows,
                "No finance-approved requests awaiting release."))
    return R.reward_flow_diagram("finance_approved") + finance_tbl + release_tbl


# ---- Leaderboards / recognition ----


def _period_from_qs(qs):
    period = q1(qs, "period", "month")
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    wk = qint(qs, "week", D.week_in_month(D.today()))
    return period, yr, mo, wk


def _scope_kwargs(period, yr, mo, wk):
    if period == "week":
        return dict(year=yr, month=mo, week=wk)
    if period == "month":
        return dict(year=yr, month=mo)
    if period == "quarter":
        return dict(year=yr, quarter=D.quarter_of_month(mo))
    return dict(year=yr)


def period_label(period, yr, mo, wk):
    if period == "week":
        return "%s %d" % (D.week_label("%04d-%02d-%02d" % (yr, mo, min(28, (wk - 1) * 7 + 1))), yr)
    if period == "month":
        return "%s %d" % (D.month_name(mo), yr)
    if period == "quarter":
        return "%s %d" % (D.quarter_label(D.quarter_of_month(mo)), yr)
    return str(yr)


def league_table(year, month, limit=None):
    rows_data = D.department_leaderboard(year=year, month=month)
    if limit:
        rows_data = rows_data[:limit]
    rows = []
    for i, r in enumerate(rows_data, start=1):
        over = r["used"] > r["limit"]
        pct = 0 if r["limit"] == 0 else min(100, round(100 * r["used"] / r["limit"]))
        bar = '<div class="progress%s"><span style="width:%d%%"></span></div>' % (" over" if over else "", pct)
        symbol = R.dept_symbol_cell(r["dept_key"], 40)
        rows.append([R.champion(i), symbol, "<strong>%d</strong>" % r["points"],
                     "%d" % r["active_employees"], D.fmt_money(r["limit"]),
                     "%s%s" % (D.fmt_money(r["used"]), bar)])
    return R.table(["#", "Department", "Points", "Active staff", "Monthly limit", "Budget used"], rows, "No data.")


def body_leaderboard(user, qs):
    tab = q1(qs, "tab", "individual")
    period, yr, mo, wk = _period_from_qs(qs)
    sk = _scope_kwargs(period, yr, mo, wk)

    # filter bar
    def psel(p, label):
        return '<a class="%s" href="?%s">%s</a>' % (
            "active" if p == period else "",
            urlencode({"tab": tab, "period": p, "year": yr, "month": mo, "week": wk}), label)
    pills = '<div class="pill-row">%s%s%s%s</div>' % (
        psel("week", "Weekly"), psel("month", "Monthly"), psel("quarter", "Quarterly"), psel("year", "Yearly"))

    def tsel(t, label):
        return '<a class="%s" href="?%s">%s</a>' % (
            "active" if t == tab else "",
            urlencode({"tab": t, "period": period, "year": yr, "month": mo, "week": wk}), label)
    tabs = '<div class="pill-row">%s%s%s</div>' % (
        tsel("individual", "Individual"), tsel("contractor", "Contractor"), tsel("department", "Department"))

    controls = """<form class="filter-bar" method="get">
      <input type="hidden" name="tab" value="%s"><input type="hidden" name="period" value="%s">
      <div class="field"><label>Month</label>%s</div>
      <div class="field"><label>Week in month</label><select name="week">%s</select></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:90px"></div>
      <button class="btn">Apply</button>
      <a class="btn ghost" href="/leaderboard.csv?%s">Export CSV</a>
    </form>""" % (
        tab, period, R.month_select("month", mo, onchange=False),
        "".join('<option value="%d"%s>Week %d</option>' % (w, " selected" if w == wk else "", w) for w in range(1, 6)),
        yr, urlencode({"tab": tab, "period": period, "year": yr, "month": mo, "week": wk}))

    if tab == "department":
        body = league_table(year=yr, month=(mo if period in ("month", "week") else None))
    elif tab == "contractor":
        data = D.contractor_leaderboard(**sk)
        rows = [[R.champion(i), R.esc(r["name"]), "%d members" % r["members"], "<strong>%d</strong>" % r["points"]]
                for i, r in enumerate(data, start=1)]
        body = R.table(["#", "Contractor company", "Members", "Points"], rows, "No contractor points.")
    else:
        data = D.individual_leaderboard(**sk)
        rows = []
        for i, r in enumerate(data[:50], start=1):
            tag = R.badge("Contractor", "muted") if r["is_contractor"] else ""
            rows.append([R.champion(i), R.esc(r["name"]) + " " + tag, R.dept_label_html(r["dept_key"]),
                         "<strong>%d</strong>" % r["points"]])
        body = R.table(["#", "Worker", "Department", "Points"], rows, "No points in this period.")

    heading = "Leaderboard · %s" % period_label(period, yr, mo, wk)
    return tabs + pills + controls + R.section(heading, body)


def body_weekly(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label>%s</div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:90px"></div>
      <button class="btn">Apply</button>
    </form>""" % (R.month_select("month", mo, onchange=False), yr)

    blocks = ""
    for wk in range(1, 6):
        data = D.individual_leaderboard(year=yr, month=mo, week=wk)
        data = [d for d in data if d["points"] > 0]
        if not data:
            continue
        rows = []
        for i, r in enumerate(data[:5], start=1):
            rows.append([R.champion(i), R.esc(r["name"]), R.dept_label_html(r["dept_key"]),
                         "<strong>%d</strong>" % r["points"]])
        label = "Week %d in %s" % (wk, D.month_name(mo))
        blocks += R.section(label, R.table(["#", "Worker", "Department", "Points"], rows))
    if not blocks:
        blocks = '<div class="empty">No weekly points recorded for %s %d.</div>' % (D.month_name(mo), yr)
    intro = '<p class="hint">Weekly rewards use <strong>week-in-month</strong> labels (Week 1&ndash;5 inside %s), not week-in-year. The top three each week wear champion badges.</p>' % D.month_name(mo)
    return controls + intro + blocks


def body_adinkra(user, qs):
    cards = ""
    for d in D.DB["departments"]:
        cards += """<div class="card adinkra-card">%s
          <div class="adinkra-meta"><h3>%s</h3>
          <span class="who-role">%s</span>
          <div class="meaning" style="margin-top:6px">%s</div>
          <div class="motto">&ldquo;%s&rdquo;</div>
          <div class="hint" style="margin-top:6px">%d employees · <a href="%s" target="_blank" rel="noopener">symbol source</a></div>
          </div></div>""" % (
            R.symbol_img(d["commons_file"], 76), R.esc(d["adinkra_name"]), R.esc(d.get("department", "")),
            R.esc(d["meaning"]), R.esc(d["motto"]), d["employee_count"],
            R.esc(adinkra.file_page_url(d["commons_file"])))
    note = ('<p class="hint">Each department pairs a real operational unit with its Adinkra emblem, '
            'name, meaning and motto &mdash; the Adinkra is always shown with its department attached. '
            'Symbols are real files hosted on Wikimedia Commons.</p>')
    return note + R.section("Adinkra Safety Identity", '<div class="grid cols-2">%s</div>' % cards)


def body_league(user, qs):
    yr, mo = D.today().year, D.today().month
    intro = '<p class="hint">Departments ranked by safety points earned in %s %d. The top three fly champion badges. Each department\'s monthly reward limit is its active employees &times; %s.</p>' % (
        D.month_name(mo), yr, D.fmt_money(D.BUDGET_PER_ACTIVE_WORKER))
    return intro + R.section("Adinkra League · %s %d" % (D.month_name(mo), yr), league_table(year=yr, month=mo))


def _month_records(yr, mo):
    """Pull every module's records for a month -- shared by the page and CSV."""
    def closed_in(a):
        ts = a.get("closed_ts")
        if not ts:
            return False
        d = D.parse_dt(ts).date()
        return d.year == yr and d.month == mo
    today_iso = D.today().isoformat()
    inc = D.records_in("incidents", yr, month=mo)
    rq = D.records_in("reward_requests", yr, month=mo)
    comp_spend = {}
    for r in rq:
        if r["status"] != "released":
            continue
        u = D.user(r["user_id"])
        if u and u.get("company_id"):
            comp_spend[u["company_id"]] = comp_spend.get(u["company_id"], 0) + r["cash_value"]
    return {
        "obs": D.records_in("safety_observations", yr, month=mo),
        "haz": D.records_in("near_miss_hazard_reports", yr, month=mo, where=lambda x: x.get("type") == "Hazard"),
        "nm": D.records_in("near_miss_hazard_reports", yr, month=mo, where=lambda x: x.get("type") == "Near miss"),
        "inc": inc,
        "lti": [i for i in inc if i.get("lti")],
        "ca_opened": D.records_in("corrective_actions", yr, month=mo),
        "ca_closed": [a for a in D.DB["corrective_actions"] if closed_in(a)],
        "ca_open_now": [a for a in D.DB["corrective_actions"] if a["status"] == "open"],
        "ca_overdue": [a for a in D.DB["corrective_actions"]
                       if a["status"] == "open" and a.get("due") and a["due"] < today_iso],
        "rq": rq,
        "rq_released": [r for r in rq if r["status"] == "released"],
        "reward_spend": sum(r["cash_value"] for r in rq if r["status"] == "released"),
        "departments": D.department_leaderboard(year=yr, month=mo),
        "contractors": D.contractor_leaderboard(year=yr, month=mo),
        "comp_spend": comp_spend,
    }


def body_reports(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    q = D.quarter_of_month(mo)
    mlabel = "%s %d" % (D.month_name(mo), yr)
    rep = _month_records(yr, mo)

    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label>%s</div>
      <div class="field"><label>Quarter (auto from month)</label><div>%s</div></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:90px"></div>
      <button class="btn">Generate</button>
      <a class="btn ghost" href="/reports.csv?%s">Export full report (CSV)</a>
    </form>""" % (R.month_select("month", mo), R.quarter_box(mo), yr, urlencode({"year": yr, "month": mo}))
    intro = ('<p class="hint">Auto-generated monthly reports for every module &mdash; <strong>%s</strong>. '
             'The quarter (<strong>%s</strong>) is derived automatically from the selected month.</p>'
             % (mlabel, D.quarter_label(q)))

    def grid(*cards, cols=4):
        return '<div class="grid cols-%d">%s</div>' % (cols, "".join(cards))

    def breakdown(items, key, title):
        c = Counter((it.get(key) or "—") for it in items)
        rows = [[R.esc(k), "%d" % v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))]
        return R.table([title, "Count"], rows, "None recorded in %s." % mlabel)

    # ---- Summary -----------------------------------------------------------
    summary = R.section("Monthly summary · %s" % mlabel,
        grid(R.stat_card("Safety Observations", len(rep["obs"])),
             R.stat_card("HID (Hazards)", len(rep["haz"])),
             R.stat_card("Near Misses", len(rep["nm"])),
             R.stat_card("Incidents", len(rep["inc"])))
        + '<div style="height:14px"></div>'
        + grid(R.stat_card("Lost Time Injuries", len(rep["lti"])),
               R.stat_card("Actions closed", len(rep["ca_closed"])),
               R.stat_card("Reward requests", len(rep["rq"])),
               R.stat_card("Reward spend", D.fmt_money(rep["reward_spend"]))))

    # ---- Incidents / LTI / HID / Near Miss / Observations ------------------
    obs_status = Counter(o["status"] for o in rep["obs"])
    observations = R.section("Safety Observations",
        grid(R.stat_card("Total", len(rep["obs"])),
             R.stat_card("Approved", obs_status.get("approved", 0)),
             R.stat_card("Pending review", obs_status.get("submitted", 0)), cols=3)
        + breakdown(rep["obs"], "category", "By category"))

    hid = R.section("HID — Hazard Reports",
        grid(R.stat_card("Hazards reported", len(rep["haz"])), cols=3)
        + breakdown(rep["haz"], "severity", "By severity"))

    nearmiss = R.section("Near Misses",
        grid(R.stat_card("Near misses", len(rep["nm"])), cols=3)
        + breakdown(rep["nm"], "severity", "By severity"))

    incidents = R.section("Incidents",
        grid(R.stat_card("Total incidents", len(rep["inc"])),
             R.stat_card("Lost Time Injuries", len(rep["lti"])),
             R.stat_card("Non-LTI", len(rep["inc"]) - len(rep["lti"])), cols=3)
        + breakdown(rep["inc"], "severity", "By severity"))

    reset_by_inc = {e["incident_id"]: e for e in D.DB["point_reset_events"]}
    lti_rows = [[D.fmt_date(i["ts"]), R.dept_label_html(i["dept_key"]), R.esc(i.get("location", "")),
                 ("%d pts reset" % reset_by_inc[i["id"]]["points_reset"]) if i["id"] in reset_by_inc else "&mdash;"]
                for i in rep["lti"]]
    lti = R.section("Lost Time Injuries (LTI)",
        R.table(["Date", "Department", "Location", "Point reset"], lti_rows,
                "No LTIs recorded in %s — well done." % mlabel))

    # ---- Corrective Actions ------------------------------------------------
    actions = R.section("Corrective Actions",
        grid(R.stat_card("Opened", len(rep["ca_opened"])),
             R.stat_card("Closed", len(rep["ca_closed"])),
             R.stat_card("Still open", len(rep["ca_open_now"])),
             R.stat_card("Overdue", len(rep["ca_overdue"]))))

    # ---- Rewards -----------------------------------------------------------
    rstatus = Counter(r["status"] for r in rep["rq"])
    rewards = R.section("Rewards",
        grid(R.stat_card("Requests", len(rep["rq"])),
             R.stat_card("Released", rstatus.get("released", 0)),
             R.stat_card("Reward spend", D.fmt_money(rep["reward_spend"])), cols=3)
        + R.table(["Workflow status", "Count"],
                  [[R.status_badge(k), "%d" % v] for k, v in sorted(rstatus.items(), key=lambda kv: -kv[1])],
                  "No reward requests in %s." % mlabel))

    # ---- Budget ------------------------------------------------------------
    mb = next((b for b in D.DB["monthly_reward_budgets"] if b["year"] == yr and b["month"] == mo), None)
    qb = next((b for b in D.DB["quarterly_reward_budgets"] if b["year"] == yr and b["quarter"] == q), None)
    m_amt = mb["amount"] if mb else 0
    q_amt = qb["amount"] if qb else 0
    budget = R.section("Budget",
        grid(R.stat_card("Monthly budget", D.fmt_money(m_amt), mlabel),
             R.stat_card("Used", D.fmt_money(D.budget_used(yr, month=mo))),
             R.stat_card("Remaining", D.fmt_money(m_amt - D.budget_used(yr, month=mo))),
             R.stat_card("%s budget remaining" % D.quarter_label(q),
                         D.fmt_money(q_amt - D.budget_used(yr, quarter=q)))))

    # ---- Departments -------------------------------------------------------
    departments = R.section("Departments · %s" % mlabel, league_table(year=yr, month=mo))

    # ---- Contractors -------------------------------------------------------
    crows = []
    for i, c in enumerate(rep["contractors"], start=1):
        crows.append([R.champion(i), R.esc(c["name"]), "%d" % c["members"],
                      "<strong>%d</strong>" % c["points"],
                      D.fmt_money(rep["comp_spend"].get(c["company_id"], 0))])
    contractors = R.section("Contractors · %s" % mlabel,
        R.table(["#", "Contractor company", "Members", "Points", "Reward spend"], crows, "No contractor activity."))

    return (controls + intro + summary + observations + hid + nearmiss + incidents
            + lti + actions + rewards + budget + departments + contractors)


def body_budgets(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    q = D.quarter_of_month(mo)
    editable = D.can_edit_budget(user["role"])

    note = ('<p class="hint">Visible to HSE Manager, Management, Finance Manager and Admin. '
            + ("You are the Admin &mdash; you can create, edit and lock budgets."
               if editable else "Only the Admin can create, edit or lock a budget.") + "</p>")

    def budget_row(b, kind):
        used = (D.budget_used(b["year"], month=b.get("month")) if kind == "monthly"
                else D.budget_used(b["year"], quarter=b.get("quarter")) if kind == "quarterly"
                else D.budget_used(b["year"]))
        remaining = b["amount"] - used
        pct = 0 if b["amount"] == 0 else min(100, round(100 * used / b["amount"]))
        bar = '<div class="progress%s"><span style="width:%d%%"></span></div>' % (" over" if used > b["amount"] else "", pct)
        lock = R.badge("Locked", "muted") if b["locked"] else R.badge("Open", "ok")
        actions = "&mdash;"
        if editable:
            toggle = "unlock" if b["locked"] else "lock"
            actions = """<form class="inline" method="post" action="/budgets">
                <input type="hidden" name="action" value="%s"><input type="hidden" name="kind" value="%s">
                <input type="hidden" name="id" value="%d"><button class="btn sm ghost">%s</button></form>""" % (
                toggle, kind, b["id"], toggle.title())
        return [D.fmt_money(b["amount"]), D.fmt_money(used) + bar, D.fmt_money(remaining), lock, actions]

    yb = [b for b in D.DB["yearly_reward_budgets"] if b["year"] == yr]
    yrows = [[("%d" % b["year"])] + budget_row(b, "yearly") for b in yb]
    yearly = R.section("Yearly reward budget · %d" % yr,
        R.table(["Year", "Amount", "Used", "Remaining", "Status", ""], yrows, "No yearly budget set."))

    mb = sorted([b for b in D.DB["monthly_reward_budgets"] if b["year"] == yr], key=lambda b: b["month"])
    mrows = [[D.month_name(b["month"])] + budget_row(b, "monthly") for b in mb]
    monthly = R.section("Monthly reward budgets · %d" % yr,
        R.table(["Month", "Amount", "Used", "Remaining", "Status", ""], mrows, "No monthly budgets set."))

    qb = sorted([b for b in D.DB["quarterly_reward_budgets"] if b["year"] == yr], key=lambda b: b["quarter"])
    qrows = [[D.quarter_label(b["quarter"])] + budget_row(b, "quarterly") for b in qb]
    quarterly = R.section("Quarterly reward budgets · %d" % yr,
        R.table(["Quarter", "Amount", "Used", "Remaining", "Status", ""], qrows, "No quarterly budgets set."))

    # Department employee-based limits.
    drows = []
    for d in D.DB["departments"]:
        limit = D.dept_monthly_limit(d)
        used = D.dept_budget_used(d["key"], yr, mo)
        drows.append([R.dept_label_html(d["key"]), "%d / %d" % (d["active_employees"], d["employee_count"]),
                      D.fmt_money(D.BUDGET_PER_ACTIVE_WORKER), D.fmt_money(limit),
                      D.fmt_money(used), D.fmt_money(limit - used)])
    dept_tbl = R.section("Department reward limits (employee-based) · %s %d" % (D.month_name(mo), yr),
        R.table(["Department", "Active / Total", "Per worker", "Monthly limit", "Used", "Remaining"], drows))

    creator = ""
    if editable:
        creator = R.section("Create / update a budget", """<form method="post" action="/budgets" class="card form-card">
          <input type="hidden" name="action" value="create">
          <input type="hidden" name="quarter_auto" value="%d">
          <div class="row-inline">
            <div class="field"><label>Type</label><select name="kind"><option value="yearly">Yearly</option><option value="monthly" selected>Monthly</option><option value="quarterly">Quarterly</option></select></div>
            <div class="field"><label>Year</label><input name="year" value="%d"></div>
            <div class="field"><label>Month</label>%s</div>
          </div>
          <div class="field"><label>Quarter (auto-derived from the month)</label><div>%s</div></div>
          <div class="field"><label>Amount (%s)</label><input name="amount" type="number" min="0" step="100" required></div>
          <button class="btn gold">Save budget</button>
          <p class="hint">Quarterly budgets take the quarter automatically from the selected month.</p>
        </form>""" % (q, yr, R.month_select("month", mo), R.quarter_box(mo), D.CURRENCY))

    return note + yearly + monthly + quarterly + dept_tbl + creator


def body_admin(user, qs):
    dept_rows = ""
    for d in D.DB["departments"]:
        dept_rows += """<form class="inline" method="post" action="/admin" style="display:block;margin-bottom:8px">
            <input type="hidden" name="action" value="set_employees"><input type="hidden" name="dept_key" value="%s">
            <span style="display:inline-block;width:230px"><strong>%s</strong> <span class="kpi-mini">%s</span></span>
            Active <input name="active" type="number" min="0" value="%d" style="width:90px;display:inline-block">
            of <input name="total" type="number" min="0" value="%d" style="width:90px;display:inline-block">
            <button class="btn sm">Update limit</button>
            <span class="hint">limit = %s</span>
          </form>""" % (d["key"], R.esc(d["adinkra_name"]), R.esc(d.get("department", "")),
                        d["active_employees"], d["employee_count"],
                        D.fmt_money(D.dept_monthly_limit(d)))
    emp = R.section("Department employees &rarr; reward limits", '<div class="card">%s</div>' % dept_rows)
    reset = R.section("Demo data", """<div class="card">
        <p>Reseed the demo from scratch (clears the runtime JSON store).</p>
        <form method="post" action="/admin" data-confirm="Reset all demo data?">
          <input type="hidden" name="action" value="reset_demo">
          <button class="btn bad">Reset &amp; reseed demo data</button></form></div>""")
    return emp + reset


# --------------------------------------------------------------------------
# POST handlers
# --------------------------------------------------------------------------


def post_observation(user, form):
    o = {"id": D.next_id("safety_observations"), "ts": D.now_iso(),
         "reporter_id": user["id"], "dept_key": q1(form, "dept_key", user["dept_key"]),
         "location": q1(form, "location", ""), "category": q1(form, "category", "Observation"),
         "description": q1(form, "description", ""), "status": "submitted"}
    D.DB["safety_observations"].append(o)
    D.save()
    return redirect("/report/observation", "Observation submitted for review.")


def post_hid(user, form):
    h = {"id": D.next_id("near_miss_hazard_reports"), "ts": D.now_iso(),
         "reporter_id": user["id"], "dept_key": q1(form, "dept_key", user["dept_key"]),
         "type": q1(form, "type", "Hazard"), "severity": q1(form, "severity", "Low"),
         "location": q1(form, "location", ""), "description": q1(form, "description", ""),
         "status": "approved"}
    D.DB["near_miss_hazard_reports"].append(h)
    _award(user["id"], h["dept_key"], "hid", "near_miss_hazard_reports", h["id"])
    D.save()
    return redirect("/report/hid", "Hazard/near-miss logged. +%d points." % D.POINTS["hid"])


def post_incident(user, form):
    is_lti = q1(form, "lti") == "1"
    inc = {"id": D.next_id("incidents"), "ts": D.now_iso(), "reporter_id": user["id"],
           "dept_key": q1(form, "dept_key", user["dept_key"]),
           "severity": q1(form, "severity", "Minor"), "lti": is_lti,
           "location": q1(form, "location", ""), "description": q1(form, "description", ""),
           "status": "under_review", "lti_reset_applied": is_lti}
    D.DB["incidents"].append(inc)
    _award(user["id"], inc["dept_key"], "incident", "incidents", inc["id"])
    msg = "Incident reported. +%d points." % D.POINTS["incident"]
    if is_lti:
        D._apply_lti_reset(D.DB, inc["dept_key"], inc["ts"], inc["id"], user["id"])
        msg += " Lost Time Injury logged — department monthly points reset."
    D.save()
    return redirect("/report/incident", msg)


def _award(user_id, dept_key, kind, src_type, src_id):
    D.DB["safety_points"].append({
        "id": D.next_id("safety_points"), "ts": D.now_iso(), "user_id": user_id,
        "dept_key": dept_key, "points": D.POINTS[kind],
        "reason": kind.replace("_", " ").title(), "source_type": src_type, "source_id": src_id})


def post_review(user, form):
    if user["role"] not in D.REVIEW_ROLES:
        return redirect("/review", "Not permitted.")
    o = next((x for x in D.DB["safety_observations"] if x["id"] == qint(form, "id")), None)
    if not o:
        return redirect("/review", "Observation not found.")
    if q1(form, "action") == "approve":
        o["status"] = "approved"
        _award(o["reporter_id"], o["dept_key"], "observation", "safety_observations", o["id"])
        msg = "Approved. +%d points awarded." % D.POINTS["observation"]
    else:
        o["status"] = "rejected"
        msg = "Observation rejected."
    D.save()
    return redirect("/review", msg)


def post_actions(user, form):
    action = q1(form, "action")
    if action == "create":
        if user["role"] not in D.REVIEW_ROLES:
            return redirect("/actions", "Not permitted.")
        a = {"id": D.next_id("corrective_actions"), "ts": D.now_iso(), "source_type": "manual",
             "source_id": 0, "dept_key": q1(form, "dept_key"), "owner_id": qint(form, "owner_id"),
             "description": q1(form, "description", ""), "due": q1(form, "due") or None,
             "status": "open", "closed_ts": None}
        D.DB["corrective_actions"].append(a)
        D.save()
        return redirect("/actions", "Corrective action created.")
    if action == "close":
        a = next((x for x in D.DB["corrective_actions"] if x["id"] == qint(form, "id")), None)
        if a and a["status"] == "open":
            a["status"] = "closed"
            a["closed_ts"] = D.now_iso()
            _award(a["owner_id"], a["dept_key"], "action_closed", "corrective_actions", a["id"])
            D.save()
            return redirect("/actions", "Action closed. +%d points." % D.POINTS["action_closed"])
    return redirect("/actions")


def post_reward_request(user, form):
    if user["role"] != "worker":
        return redirect("/rewards", "Only workers request rewards.")
    rw = D.reward(qint(form, "reward_id"))
    if not rw:
        return redirect("/rewards", "Reward not found.")
    if D.user_balance(user["id"]) < rw["point_cost"]:
        return redirect("/rewards", "Not enough points for that reward.")
    D.DB["reward_requests"].append({
        "id": D.next_id("reward_requests"), "ts": D.now_iso(), "user_id": user["id"],
        "dept_key": user["dept_key"], "reward_id": rw["id"], "point_cost": rw["point_cost"],
        "cash_value": rw["cash_value"], "status": "pending_admin",
        "admin_id": None, "admin_ts": None, "finance_id": None, "finance_ts": None,
        "released_by": None, "released_ts": None,
        "reject_reason": None, "rejected_by": None, "reject_stage": None, "rejected_ts": None})
    D.save()
    return redirect("/rewards", "Reward requested. Awaiting admin approval.")


def _reject(r, user, stage, form):
    r["status"] = "rejected"
    r["rejected_by"] = user["id"]
    r["reject_stage"] = stage
    r["reject_reason"] = q1(form, "reason") or "No reason provided."
    r["rejected_ts"] = D.now_iso()


def post_reward_approval(user, form):
    """Step 2: Admin approves (-> Finance) or rejects with a reason."""
    if user["role"] not in D.REWARD_APPROVE_ROLES:
        return redirect("/rewards/approvals", "Not permitted.")
    r = next((x for x in D.DB["reward_requests"] if x["id"] == qint(form, "id")), None)
    if not r or r["status"] != "pending_admin":
        return redirect("/rewards/approvals", "Request not found.")
    if q1(form, "action") == "approve":
        r["status"] = "pending_finance"
        r["admin_id"] = user["id"]
        r["admin_ts"] = D.now_iso()
        msg = "Approved by Admin. Sent to the Finance Manager."
    else:
        r["admin_id"] = user["id"]
        r["admin_ts"] = D.now_iso()
        _reject(r, user, "admin", form)
        msg = "Request rejected."
    D.save()
    return redirect("/rewards/approvals", msg)


def post_reward_finance(user, form):
    """Steps 3 & 4: Finance approves/rejects, then releases the reward."""
    if user["role"] not in D.REWARD_RELEASE_ROLES:
        return redirect("/rewards/releases", "Not permitted.")
    r = next((x for x in D.DB["reward_requests"] if x["id"] == qint(form, "id")), None)
    if not r:
        return redirect("/rewards/releases", "Request not found.")
    action = q1(form, "action")
    if action == "fin_approve" and r["status"] == "pending_finance":
        r["status"] = "finance_approved"
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        msg = "Finance approved. Ready for release."
    elif action == "reject" and r["status"] == "pending_finance":
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        _reject(r, user, "finance", form)
        msg = "Request rejected by Finance."
    elif action == "release" and r["status"] == "finance_approved":
        r["status"] = "released"
        r["released_by"] = user["id"]
        r["released_ts"] = D.now_iso()
        msg = "Reward released. %s charged to the budget." % D.fmt_money(r["cash_value"])
    else:
        msg = "No change."
    D.save()
    return redirect("/rewards/releases", msg)


def post_budgets(user, form):
    if not D.can_edit_budget(user["role"]):
        return redirect("/budgets", "Only the Admin can edit budgets.")
    action = q1(form, "action")
    kind = q1(form, "kind")
    if action in ("lock", "unlock"):
        coll = {"yearly": "yearly_reward_budgets", "monthly": "monthly_reward_budgets",
                "quarterly": "quarterly_reward_budgets"}[kind]
        b = next((x for x in D.DB[coll] if x["id"] == qint(form, "id")), None)
        if b:
            b["locked"] = (action == "lock")
            D.save()
        return redirect("/budgets", "Budget %sed." % action)
    if action == "create":
        yr = qint(form, "year", D.today().year)
        amount = qint(form, "amount", 0)
        if kind == "yearly":
            b = next((x for x in D.DB["yearly_reward_budgets"] if x["year"] == yr), None)
            if b:
                b["amount"] = amount
            else:
                D.DB["yearly_reward_budgets"].append({"id": D.next_id("yearly_reward_budgets"), "year": yr, "amount": amount, "locked": False})
        elif kind == "monthly":
            mo = qint(form, "month", D.today().month)
            b = next((x for x in D.DB["monthly_reward_budgets"] if x["year"] == yr and x["month"] == mo), None)
            if b:
                b["amount"] = amount
            else:
                D.DB["monthly_reward_budgets"].append({"id": D.next_id("monthly_reward_budgets"), "year": yr, "month": mo, "amount": amount, "locked": False})
        else:  # quarterly -- quarter auto from selected month
            mo = qint(form, "month", D.today().month)
            qq = D.quarter_of_month(mo)
            b = next((x for x in D.DB["quarterly_reward_budgets"] if x["year"] == yr and x["quarter"] == qq), None)
            if b:
                b["amount"] = amount
            else:
                D.DB["quarterly_reward_budgets"].append({"id": D.next_id("quarterly_reward_budgets"), "year": yr, "quarter": qq, "amount": amount, "locked": False})
        D.save()
        return redirect("/budgets", "Budget saved.")
    return redirect("/budgets")


def post_admin(user, form):
    if user["role"] != "admin":
        return redirect("/admin", "Not permitted.")
    action = q1(form, "action")
    if action == "reset_demo":
        D.reset_demo()
        return redirect("/admin", "Demo data reset.")
    if action == "set_employees":
        d = D.department(q1(form, "dept_key"))
        if d:
            d["employee_count"] = max(0, qint(form, "total", d["employee_count"]))
            d["active_employees"] = max(0, min(qint(form, "active", d["active_employees"]), d["employee_count"]))
            D.save()
        return redirect("/admin", "Department limit updated.")
    return redirect("/admin")


# --------------------------------------------------------------------------
# CSV exports (respect active filters)
# --------------------------------------------------------------------------


def csv_points(user, qs):
    dept = q1(qs, "dept")
    entries = list(D.DB["safety_points"])
    if dept:
        entries = [p for p in entries if p["dept_key"] == dept]
    if user["role"] == "worker":
        entries = [p for p in entries if p["user_id"] == user["id"]]
    entries.sort(key=lambda p: p["ts"], reverse=True)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Worker", "Department", "Reason", "Points"])
    for p in entries:
        u = D.user(p["user_id"])
        w.writerow([p["ts"], u["name"] if u else "?", D.dept_name(p["dept_key"]), p["reason"], p["points"]])
    return "safety_points.csv", out.getvalue()


def csv_leaderboard(user, qs):
    tab = q1(qs, "tab", "individual")
    period, yr, mo, wk = _period_from_qs(qs)
    sk = _scope_kwargs(period, yr, mo, wk)
    out = io.StringIO()
    w = csv.writer(out)
    if tab == "department":
        w.writerow(["Rank", "Department", "Points", "ActiveEmployees", "MonthlyLimit", "Used", "Remaining"])
        for i, r in enumerate(D.department_leaderboard(year=yr, month=mo), start=1):
            w.writerow([i, r["adinkra_name"], r["points"], r["active_employees"], r["limit"], r["used"], r["remaining"]])
    elif tab == "contractor":
        w.writerow(["Rank", "Company", "Members", "Points"])
        for i, r in enumerate(D.contractor_leaderboard(**sk), start=1):
            w.writerow([i, r["name"], r["members"], r["points"]])
    else:
        w.writerow(["Rank", "Worker", "Department", "Contractor", "Points"])
        for i, r in enumerate(D.individual_leaderboard(**sk), start=1):
            w.writerow([i, r["name"], D.dept_name(r["dept_key"]), "yes" if r["is_contractor"] else "no", r["points"]])
    return "leaderboard_%s_%s.csv" % (tab, period), out.getvalue()


def csv_reports(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    q = D.quarter_of_month(mo)
    rep = _month_records(yr, mo)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Module", "Metric", "Value"])
    w.writerow(["Report", "Period", "%s %d" % (D.month_name(mo), yr)])
    w.writerow(["Report", "Quarter (auto from month)", D.quarter_label(q)])
    w.writerow(["Safety Observations", "Total", len(rep["obs"])])
    for k, v in Counter(o["status"] for o in rep["obs"]).items():
        w.writerow(["Safety Observations", "Status: %s" % k, v])
    w.writerow(["HID Hazards", "Total", len(rep["haz"])])
    w.writerow(["Near Miss", "Total", len(rep["nm"])])
    w.writerow(["Incidents", "Total", len(rep["inc"])])
    w.writerow(["Incidents", "Non-LTI", len(rep["inc"]) - len(rep["lti"])])
    w.writerow(["LTI", "Total", len(rep["lti"])])
    w.writerow(["Corrective Actions", "Opened", len(rep["ca_opened"])])
    w.writerow(["Corrective Actions", "Closed", len(rep["ca_closed"])])
    w.writerow(["Corrective Actions", "Open now", len(rep["ca_open_now"])])
    w.writerow(["Corrective Actions", "Overdue", len(rep["ca_overdue"])])
    w.writerow(["Rewards", "Requests", len(rep["rq"])])
    for k, v in Counter(r["status"] for r in rep["rq"]).items():
        w.writerow(["Rewards", "Status: %s" % k, v])
    w.writerow(["Rewards", "Spend", rep["reward_spend"]])
    w.writerow(["Budget", "Monthly used", D.budget_used(yr, month=mo)])
    w.writerow(["Budget", "Quarter used", D.budget_used(yr, quarter=q)])

    w.writerow([])
    w.writerow(["Department (Adinkra)", "Operational unit", "Points", "ActiveEmployees", "MonthlyLimit", "Used", "Remaining"])
    for d in rep["departments"]:
        w.writerow([d["adinkra_name"], d.get("department", ""), d["points"],
                    d["active_employees"], d["limit"], d["used"], d["remaining"]])

    w.writerow([])
    w.writerow(["Contractor", "Members", "Points", "RewardSpend"])
    for c in rep["contractors"]:
        w.writerow([c["name"], c["members"], c["points"], rep["comp_spend"].get(c["company_id"], 0)])
    return "monthly_report_%d_%02d.csv" % (yr, mo), out.getvalue()


# --------------------------------------------------------------------------
# Routing tables
# --------------------------------------------------------------------------
GET_ROUTES = {
    "/": ("Dashboard", body_dashboard),
    "/report/observation": ("Report Observation", body_observation_form),
    "/report/hid": ("Hazard / Near-miss", body_hid_form),
    "/report/incident": ("Report Incident", body_incident_form),
    "/review": ("Review Queue", body_review),
    "/actions": ("Corrective Actions", body_actions),
    "/points": ("Points Ledger", body_points),
    "/rewards": ("Reward Catalogue", body_rewards),
    "/rewards/approvals": ("Reward Approvals", body_reward_approvals),
    "/rewards/releases": ("Finance Approvals", body_reward_finance),
    "/leaderboard": ("Leaderboards", body_leaderboard),
    "/weekly": ("Weekly Rewards", body_weekly),
    "/adinkra": ("Adinkra Identity", body_adinkra),
    "/league": ("Adinkra League", body_league),
    "/reports": ("Report Centre", body_reports),
    "/budgets": ("Reward Budgets", body_budgets),
    "/admin": ("Admin Tools", body_admin),
}
POST_ROUTES = {
    "/report/observation": post_observation,
    "/report/hid": post_hid,
    "/report/incident": post_incident,
    "/review": post_review,
    "/actions": post_actions,
    "/rewards": post_reward_request,
    "/rewards/approvals": post_reward_approval,
    "/rewards/releases": post_reward_finance,
    "/budgets": post_budgets,
    "/admin": post_admin,
}
CSV_ROUTES = {
    "/points.csv": csv_points,
    "/leaderboard.csv": csv_leaderboard,
    "/reports.csv": csv_reports,
}
# Route -> required permission predicate (user -> bool). Absent = any logged-in user.
ROUTE_GUARDS = {
    "/review": lambda u: u["role"] in D.REVIEW_ROLES,
    "/rewards/approvals": lambda u: u["role"] in D.REWARD_APPROVE_ROLES,
    "/rewards/releases": lambda u: u["role"] in D.REWARD_RELEASE_ROLES,
    "/reports": lambda u: u["role"] in D.REPORTS_ROLES,
    "/budgets": lambda u: D.can_view_budget(u["role"]),
    "/admin": lambda u: u["role"] == "admin",
}


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "SafetyRewards/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet

    # -- session helpers --
    def current_user(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = c["sid"].value if "sid" in c else None
        uid = SESSIONS.get(token)
        return D.user(uid) if uid else None

    def send_html(self, html, status=200):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def send_csv(self, filename, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- GET --
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        user = self.current_user()

        if path == "/login":
            if user:
                return self.send_redirect("/")
            by_role = {}
            for u in D.DB["users"]:
                by_role.setdefault(u["role"], []).append(u)
            return self.send_html(R.login_page(by_role, q1(qs, "m", "")))

        if path == "/logout":
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            token = c["sid"].value if "sid" in c else None
            SESSIONS.pop(token, None)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "sid=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if not user:
            return self.send_redirect("/login")

        if path in CSV_ROUTES:
            if path == "/reports.csv" and user["role"] not in D.REPORTS_ROLES:
                return self.send_redirect("/")
            filename, text = CSV_ROUTES[path](user, qs)
            return self.send_csv(filename, text)

        if path in GET_ROUTES:
            guard = ROUTE_GUARDS.get(path)
            if guard and not guard(user):
                body = '<div class="empty">You do not have access to this module.</div>'
                return self.send_html(R.page("Not permitted", user, body, path))
            title, fn = GET_ROUTES[path]
            body = fn(user, qs)
            return self.send_html(R.page(title, user, body, path, q1(qs, "m", "")))

        self.send_html(R.page("Not found", user, '<div class="empty">Page not found.</div>', path), status=404)

    # -- POST --
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)

        if path == "/login":
            uid = qint(form, "user_id")
            if D.user(uid):
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = uid
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "sid=%s; Path=/; HttpOnly; SameSite=Lax" % token)
                self.end_headers()
                return
            return self.send_redirect("/login?m=Pick+a+user")

        user = self.current_user()
        if not user:
            return self.send_redirect("/login")

        handler = POST_ROUTES.get(path)
        if not handler:
            return self.send_redirect("/")
        guard = ROUTE_GUARDS.get(path)
        if guard and not guard(user):
            return self.send_redirect(path, )
        result = handler(user, form)
        if result and result[0] == "redirect":
            return self.send_redirect(result[1])
        return self.send_redirect(path)


def main():
    D.load()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://localhost:%d/" % PORT
    print("Safety Rewards Tracker running at %s" % url)
    print("Press Ctrl+C to stop.")
    if not os.environ.get("NO_BROWSER"):
        try:
            Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
