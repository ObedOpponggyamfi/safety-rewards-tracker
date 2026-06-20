"""Safety Rewards Tracker -- Python standard-library MVP.

Run:
    python app.py            # serves http://localhost:8090

No npm, no pip, no framework. Server-rendered HTML/CSS with JSON persistence.
"""

import csv
import io
import os
import secrets
import socket
import time
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
LOGIN_ATTEMPTS = {}  # login key -> failed attempt timestamps
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 10 * 60


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


ACCESS_DENIED = ("You do not have permission to access this module. "
                 "Contact your System Administrator if you believe this is incorrect.")


def has_any_perm(user, *perms):
    return any(D.has_perm(user, perm) for perm in perms)


def dept_workers(dept_key):
    return [u for u in D.DB["users"]
            if D.has_role(u, "worker") and u.get("dept_key") == dept_key and u.get("active", True)]


def request_status_badge(status):
    kind = {"Approved": "ok", "Rejected": "bad", "Closed": "muted"}.get(status, "warn")
    return R.badge(status, kind)


def adjustment_status_badge(status):
    kind = {"approved": "ok", "rejected": "bad", "pending": "warn"}.get(status, "muted")
    return R.badge(status.replace("_", " ").title(), kind)


def _login_key(employee_id, remote_addr):
    return "%s|%s" % ((employee_id or "").strip().casefold(), remote_addr or "")


def _login_attempts(key):
    now = time.time()
    attempts = [ts for ts in LOGIN_ATTEMPTS.get(key, []) if now - ts < LOGIN_WINDOW_SECONDS]
    if attempts:
        LOGIN_ATTEMPTS[key] = attempts
    else:
        LOGIN_ATTEMPTS.pop(key, None)
    return attempts


def _login_blocked(key):
    return len(_login_attempts(key)) >= MAX_LOGIN_ATTEMPTS


def _record_failed_login(key):
    LOGIN_ATTEMPTS[key] = _login_attempts(key) + [time.time()]


def _clear_login_attempts(key):
    LOGIN_ATTEMPTS.pop(key, None)


def _hse_dashboard_scope(user):
    if has_any_perm(user, "report.view_company"):
        return True, None, "Company HSE overview"
    if has_any_perm(user, "report.view_department", "hse.module"):
        return True, user.get("dept_key"), "%s HSE overview" % D.dept_name(user.get("dept_key"))
    return False, None, ""


def _scoped_dept(user, requested=None):
    requested = D.department_key_from_value(requested or "", default="") or ""
    if has_any_perm(user, "report.view_company"):
        return requested or None
    if has_any_perm(user, "report.view_department", "hse.module"):
        return user.get("dept_key")
    return requested or None


def _dept_opts_for_user(user, selected=None):
    if has_any_perm(user, "report.view_company"):
        return _dept_opts(selected)
    dept_key = user.get("dept_key")
    items = [(dept_key, D.dept_name(dept_key))] if D.department(dept_key) else []
    return _opts(items, dept_key, None)


def _open_actions_for_scope(dept_key=None):
    actions = [a for a in D.DB["corrective_actions"] if a["status"] == "open"]
    if dept_key:
        actions = [a for a in actions if a.get("dept_key") == dept_key]
    return actions


def _low_actual_high_potential_count(reports):
    return sum(1 for r in reports
               if r["rec"].get("actual_consequence") in ("Insignificant", "Minor", "Moderate")
               and r["rec"].get("potential_consequence") in ("Major", "Catastrophic"))


# --------------------------------------------------------------------------
# Page bodies
# --------------------------------------------------------------------------


def body_dashboard_legacy(user, qs):
    return body_dashboard(user, qs)
    yr, mo = D.today().year, D.today().month
    q = D.quarter_of_month(mo)
    pending_reviews = len([o for o in D.DB["safety_observations"] if o["status"] == "submitted"])
    pending_rewards = len([r for r in D.DB["reward_requests"] if r["status"] == "pending_finance"])
    today_iso = D.today().isoformat()

    reps = D._norm_reports(free=True)
    n = lambda rt: sum(1 for r in reps if r["rtype"] == rt)
    open_actions = [a for a in D.DB["corrective_actions"] if a["status"] == "open"]
    overdue = [a for a in open_actions if a.get("due") and a["due"] < today_iso]
    hotspots = D.location_hotspots(free=True)
    causes = D.cause_category_counts(free=True)
    dq = D.data_quality(free=True)
    lahp = len(D.low_actual_high_potential(free=True))
    top_loc = ("%s (%d)" % (hotspots[0]["location"], hotspots[0]["total"])) if hotspots else "—"
    top_cause = causes.most_common(1)[0][0] if causes else "—"

    top = R.section("HSE overview · current month + 90 days",
        '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
        '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
        '<div class="grid cols-4">%s%s%s%s</div>' % (
            R.stat_card("Total Incidents", n("incident")),
            R.stat_card("Total HIDs", n("hid")),
            R.stat_card("Total Near Misses", n("near_miss")),
            R.stat_card("Total Observations", n("observation")),
            R.stat_card("High-Potential Events", sum(1 for r in reps if r["high_potential"])),
            R.stat_card("Property / Equipment Damage", n("damage")),
            R.stat_card("Open Corrective Actions", len(open_actions)),
            R.stat_card("Overdue Corrective Actions", len(overdue)),
            R.stat_card("Top Hotspot Location", top_loc),
            R.stat_card("Top Cause Category", top_cause),
            R.stat_card("Low Actual / High Potential", lahp),
            R.stat_card("Data Completeness", "%d%%" % dq["completeness"])))

    by_loc = Counter(r["location"] for r in reps)
    by_risk = Counter(r["risk_level"] or "Unspecified" for r in reps)
    by_dept = Counter(D.dept_name(r["dept_key"]) for r in reps)
    actual_dist = Counter(r["rec"].get("actual_consequence") or "—" for r in reps)
    potential_dist = Counter(r["rec"].get("potential_consequence") or "—" for r in reps)
    ordered = lambda c, order: [(k, c.get(k, 0)) for k in order if c.get(k, 0)]
    top += ('<div class="grid cols-2">%s%s</div>' % (
                R.section("Reports by location", R.bar_chart(by_loc.most_common(8))),
                R.section("Reports by risk level", R.bar_chart(ordered(by_risk, D.RISK_LEVELS) or by_risk.most_common())))
            + '<div class="grid cols-2">%s%s</div>' % (
                R.section("Reports by department", R.bar_chart(by_dept.most_common(8))),
                R.section("Top cause categories", R.bar_chart(causes.most_common(5))))
            + '<div class="grid cols-2">%s%s</div>' % (
                R.section("Actual consequences", R.bar_chart(ordered(actual_dist, D.CONSEQUENCES))),
                R.section("Potential consequences", R.bar_chart(ordered(potential_dist, D.CONSEQUENCES)))))

    # AI Safety Prediction band (compact; full detail on /ai)
    ai = D.ai_predict(free=True)
    if ai["ok"]:
        first = lambda l: l[0] if l else None

        def mini(title, pred):
            if not pred:
                return R.stat_card(title, "—", "insufficient data")
            return R.stat_card(title, "%s · %d" % (pred["risk_level"], pred["risk_score"]), pred["entity_name"])

        od = ai["overdue_actions"]
        n_hp = len(D.high_potential_events(free=True))
        ai_cards = ('<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
                    '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
                    '<div class="grid cols-2">%s%s</div>' % (
                        mini("Highest Risk Location", first(ai["locations"])),
                        mini("Highest Risk Department", first(ai["departments"])),
                        mini("Highest Risk Activity", first(ai["activities"])),
                        mini("Equipment Requiring Attention", first(ai["equipment"])),
                        mini("Contractor Risk Alert", first(ai["contractors"])),
                        R.stat_card("Corrective Action Overdue Risk", "%d flagged" % len(od), "overdue / due within 7 days"),
                        mini("Repeat Hazard Alert", first(ai["repeat_hazards"])),
                        R.stat_card("High-Potential Event Alert", "%d events" % n_hp, ai["period_label"]),
                        mini("Predicted Risk — %s" % ai["period_label"], ai["overall"]),
                        R.stat_card("Recommended Immediate Action", ai["top"]["risk_level"], ai["top"]["recommended_action"])))
        top += R.section("AI Safety Prediction · %s" % ai["period_label"],
                         ai_cards + '<div class="pill-row" style="margin-top:12px"><a class="btn gold" href="/ai">Open AI Safety Insights &rarr;</a></div>')

    # Personal panel for workers / contractors.
    personal = ""
    if D.has_role(user, "worker"):
        bal = D.user_balance(user["id"])
        reserved = D.reserved_points(user["id"])
        mpts = D.user_points(user["id"], year=yr, month=mo)
        personal = R.section("My safety points",
            '<div class="grid cols-3">%s%s%s</div>' % (
                R.stat_card("Spendable balance", "%d pts" % bal),
                R.stat_card("Reserved", "%d pts" % reserved, "pending reward requests"),
                R.stat_card("Earned this month", "%d pts" % mpts, "%s %d" % (D.month_name(mo), yr)),
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
    if has_any_perm(user, "hid.verify", "hid.approve", "points.process_automatic") and pending_reviews:
        todo.append('<a class="btn gold" href="/review">Review queue (%d)</a>' % pending_reviews)
    if D.has_perm(user, "reward.finance_approve"):
        fin = len([r for r in D.DB["reward_requests"]
                   if r["status"] in ("pending_finance", "finance_approved")])
        if fin:
            todo.append('<a class="btn gold" href="/rewards/releases">Finance queue (%d)</a>' % fin)
    if D.has_perm(user, "hid_request.create"):
        todo.append('<a class="btn" href="/hid/request">Submit HID request</a>')
    elif D.has_perm(user, "hse.module"):
        todo.append('<a class="btn" href="/report/observation">Report an observation</a>')
    todo.append('<a class="btn ghost" href="/league">Adinkra League</a>')
    todo_html = R.section("Quick actions", '<div class="pill-row">%s</div>' % "".join(todo))

    league_preview = league_table(year=yr, month=mo, limit=3)
    preview = R.section("Adinkra League · top departments this month", league_preview,
                        actions='<a class="btn sm ghost" href="/league">Full league</a>')

    return top + personal + todo_html + dept_html + preview


def body_dashboard(user, qs):
    yr, mo = D.today().year, D.today().month
    pending_reviews = len([o for o in D.DB["safety_observations"] if o["status"] == "submitted"])
    today_iso = D.today().isoformat()
    can_hse, scoped_dept, hse_title = _hse_dashboard_scope(user)

    top = ""
    if can_hse:
        reps = D._norm_reports(dept=scoped_dept, free=True)
        n = lambda rt: sum(1 for r in reps if r["rtype"] == rt)
        open_actions = _open_actions_for_scope(scoped_dept)
        overdue = [a for a in open_actions if a.get("due") and a["due"] < today_iso]
        hotspots = D.location_hotspots(dept=scoped_dept, free=True)
        causes = D.cause_category_counts(dept=scoped_dept, free=True)
        lahp = _low_actual_high_potential_count(reps)
        top_loc = ("%s (%d)" % (hotspots[0]["location"], hotspots[0]["total"])) if hotspots else "-"
        top_cause = causes.most_common(1)[0][0] if causes else "-"
        scope_card = (R.stat_card("Data Completeness", "%d%%" % D.data_quality(free=True)["completeness"])
                      if scoped_dept is None else R.stat_card("Records in Scope", len(reps), D.dept_name(scoped_dept)))

        top = R.section("%s - current history" % hse_title,
            '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
            '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
            '<div class="grid cols-4">%s%s%s%s</div>' % (
                R.stat_card("Total Incidents", n("incident")),
                R.stat_card("Total HIDs", n("hid")),
                R.stat_card("Total Near Misses", n("near_miss")),
                R.stat_card("Total Observations", n("observation")),
                R.stat_card("High-Potential Events", sum(1 for r in reps if r["high_potential"])),
                R.stat_card("Property / Equipment Damage", n("damage")),
                R.stat_card("Open Corrective Actions", len(open_actions)),
                R.stat_card("Overdue Corrective Actions", len(overdue)),
                R.stat_card("Top Hotspot Location", top_loc),
                R.stat_card("Top Cause Category", top_cause),
                R.stat_card("Low Actual / High Potential", lahp),
                scope_card))

        by_loc = Counter(r["location"] for r in reps)
        by_risk = Counter(r["risk_level"] or "Unspecified" for r in reps)
        by_dept = Counter(D.dept_name(r["dept_key"]) for r in reps)
        actual_dist = Counter(r["rec"].get("actual_consequence") or "-" for r in reps)
        potential_dist = Counter(r["rec"].get("potential_consequence") or "-" for r in reps)
        ordered = lambda c, order: [(k, c.get(k, 0)) for k in order if c.get(k, 0)]
        top += ('<div class="grid cols-2">%s%s</div>' % (
                    R.section("Reports by location", R.bar_chart(by_loc.most_common(8))),
                    R.section("Reports by risk level", R.bar_chart(ordered(by_risk, D.RISK_LEVELS) or by_risk.most_common())))
                + '<div class="grid cols-2">%s%s</div>' % (
                    R.section("Reports by department", R.bar_chart(by_dept.most_common(8))),
                    R.section("Top cause categories", R.bar_chart(causes.most_common(5))))
                + '<div class="grid cols-2">%s%s</div>' % (
                    R.section("Actual consequences", R.bar_chart(ordered(actual_dist, D.CONSEQUENCES))),
                    R.section("Potential consequences", R.bar_chart(ordered(potential_dist, D.CONSEQUENCES)))))

        if has_any_perm(user, "hse.module", "report.view_company"):
            ai = D.ai_predict(dept=scoped_dept, free=True)
            if ai["ok"]:
                first = lambda l: l[0] if l else None

                def mini(title, pred):
                    if not pred:
                        return R.stat_card(title, "-", "insufficient data")
                    return R.stat_card(title, "%s - %d" % (pred["risk_level"], pred["risk_score"]), pred["entity_name"])

                n_hp = len(D.high_potential_events(dept=scoped_dept, free=True))
                ai_cards = ('<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
                            '<div class="grid cols-4">%s%s%s%s</div><div style="height:14px"></div>'
                            '<div class="grid cols-2">%s%s</div>' % (
                                mini("Highest Risk Location", first(ai["locations"])),
                                mini("Highest Risk Department", first(ai["departments"])),
                                mini("Highest Risk Activity", first(ai["activities"])),
                                mini("Equipment Requiring Attention", first(ai["equipment"])),
                                mini("Contractor Risk Alert", first(ai["contractors"])),
                                R.stat_card("Corrective Action Overdue Risk", "%d flagged" % len(overdue), "scoped overdue actions"),
                                mini("Repeat Hazard Alert", first(ai["repeat_hazards"])),
                                R.stat_card("High-Potential Event Alert", "%d events" % n_hp, ai["period_label"]),
                                mini("Predicted Risk - %s" % ai["period_label"], ai["overall"]),
                                R.stat_card("Recommended Immediate Action", ai["top"]["risk_level"], ai["top"]["recommended_action"])))
                top += R.section("AI Safety Prediction - %s" % ai["period_label"],
                                 ai_cards + '<div class="pill-row" style="margin-top:12px"><a class="btn gold" href="/ai">Open AI Safety Insights &rarr;</a></div>')

    personal = ""
    if D.has_role(user, "worker"):
        bal = D.user_balance(user["id"])
        reserved = D.reserved_points(user["id"])
        mpts = D.user_points(user["id"], year=yr, month=mo)
        personal = R.section("My safety points",
            '<div class="grid cols-3">%s%s%s</div>' % (
                R.stat_card("Spendable balance", "%d pts" % bal),
                R.stat_card("Reserved", "%d pts" % reserved, "pending reward requests"),
                R.stat_card("Earned this month", "%d pts" % mpts, "%s %d" % (D.month_name(mo), yr)),
            ))

    dept = D.department(user["dept_key"])
    dept_html = ""
    if dept:
        limit = D.dept_monthly_limit(dept)
        used = D.dept_budget_used(dept["key"], yr, mo)
        pts = D.dept_points(dept["key"], year=yr, month=mo)
        dept_html = R.section("My department",
            '<div class="card adinkra-card">%s<div class="adinkra-meta">'
            '<h3>%s</h3><div class="kpi-mini">%s</div><div class="meaning">%s</div><div class="motto">%s</div>'
            '<div class="hint" style="margin-top:8px">Points this month: <strong>%d</strong> &middot; '
            'Active employees: <strong>%d</strong> &middot; Monthly limit: <strong>%s</strong> &middot; '
            'Used: <strong>%s</strong></div></div></div>' % (
                R.symbol_img(dept["commons_file"], 84), R.esc(dept["adinkra_name"]),
                R.esc(dept.get("department", "")), R.esc(dept["meaning"]), R.esc(dept["motto"]), pts,
                dept["active_employees"], D.fmt_money(limit), D.fmt_money(used)))

    todo = []
    if has_any_perm(user, "hid.verify", "hid.approve", "points.process_automatic") and pending_reviews:
        todo.append('<a class="btn gold" href="/review">Review queue (%d)</a>' % pending_reviews)
    if D.has_perm(user, "reward.finance_approve"):
        fin = len([r for r in D.DB["reward_requests"]
                   if r["status"] in ("pending_finance", "finance_approved")])
        if fin:
            todo.append('<a class="btn gold" href="/rewards/releases">Finance queue (%d)</a>' % fin)
    if D.has_perm(user, "hid_request.create"):
        todo.append('<a class="btn" href="/hid/request">Submit HID request</a>')
    elif D.has_perm(user, "hse.module"):
        todo.append('<a class="btn" href="/report/observation">Report an observation</a>')
    todo.append('<a class="btn ghost" href="/league">Adinkra League</a>')
    todo_html = R.section("Quick actions", '<div class="pill-row">%s</div>' % "".join(todo))

    league_preview = league_table(year=yr, month=mo, limit=3)
    preview = R.section("Adinkra League - top departments this month", league_preview,
                        actions='<a class="btn sm ghost" href="/league">Full league</a>')

    return top + personal + todo_html + dept_html + preview


def body_notifications(user, qs):
    status = q1(qs, "status", "all")
    notes = [n for n in D.DB.get("notifications", []) if n.get("user_id") == user["id"]]
    if status == "unread":
        notes = [n for n in notes if not n.get("read")]
    notes.sort(key=lambda n: n.get("ts", ""), reverse=True)
    unread = sum(1 for n in D.DB.get("notifications", [])
                 if n.get("user_id") == user["id"] and not n.get("read"))
    cards = '<div class="grid cols-3" style="margin-bottom:18px">%s%s%s</div>' % (
        R.stat_card("Unread", unread),
        R.stat_card("Total", len([n for n in D.DB.get("notifications", []) if n.get("user_id") == user["id"]])),
        R.stat_card("Showing", status.title()),
    )
    controls = """<form class="filter-bar" method="get">
        <a class="btn ghost%s" href="/notifications">All</a>
        <a class="btn ghost%s" href="/notifications?status=unread">Unread</a>
      </form>""" % (" active" if status == "all" else "", " active" if status == "unread" else "")
    rows = []
    for n in notes[:80]:
        link = '<a class="btn sm ghost" href="%s">Open</a>' % R.esc(n.get("link") or "/") if n.get("link") else "&mdash;"
        mark = "&mdash;" if n.get("read") else """<form class="inline" method="post" action="/notifications">
            <input type="hidden" name="action" value="read">
            <input type="hidden" name="id" value="%d">
            <button class="btn sm">Mark read</button></form>""" % n["id"]
        rows.append([D.fmt_date(n.get("ts", "")), R.esc(n.get("title", "")),
                     R.esc(n.get("message", "")), R.badge("Read" if n.get("read") else "Unread", "muted" if n.get("read") else "warn"),
                     link, mark])
    mark_all = ""
    if unread:
        mark_all = """<form method="post" action="/notifications">
            <input type="hidden" name="action" value="read_all">
            <button class="btn">Mark all read</button></form>"""
    return cards + controls + R.section("Notifications",
        R.table(["When", "Title", "Message", "Status", "Link", ""], rows, "No notifications yet."),
        actions=mark_all)


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
      %s
      <button class="btn gold" type="submit">Submit observation (+%d pts on approval)</button>
    </form>""" % (dept_opts, cat_opts, R.hse_fields(), D.POINTS["observation"])
    recent = [o for o in D.DB["safety_observations"] if o["reporter_id"] == user["id"]][-6:][::-1]
    rows = [[D.fmt_date(o["ts"]), R.esc(o["category"]), R.esc(o["location"]), R.status_badge(o["status"])] for o in recent]
    return R.section("Report a safety observation", form) + \
        R.section("My recent observations", R.table(["Date", "Category", "Location", "Status"], rows, "No observations yet."))


def body_hid_form(user, qs):
    sel_dept = lambda k: ' selected' if k == user["dept_key"] else ''
    dept_opts = "".join('<option value="%s"%s>%s</option>' % (
        d["key"], sel_dept(d["key"]),
        R.esc("%s - %s" % (d["adinkra_name"], d.get("department", ""))))
        for d in D.DB["departments"])
    employee_field = ""
    if D.has_perm(user, "hid.create_for_employee"):
        selected = qint(qs, "employee_id")
        opts = "".join('<option value="%d"%s>%s</option>' % (
            w["id"], " selected" if w["id"] == selected else "",
            R.esc("%s (%s)" % (w["name"], w.get("title", "Worker"))))
            for w in dept_workers(user["dept_key"]))
        employee_field = '<div class="field"><label>Employee</label><select name="submitted_for_user_id" required>%s</select></div>' % opts
    request_id = qint(qs, "request_id")
    request_hidden = '<input type="hidden" name="request_id" value="%d">' % request_id if request_id else ""
    form = """<form method="post" action="/report/hid" class="card form-card">
      %s
      <div class="row-inline">
        <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
        <div class="field"><label>Type</label><select name="type"><option>Hazard</option><option>Near miss</option></select></div>
        <div class="field"><label>Severity</label><select name="severity"><option>Low</option><option>Medium</option><option>High</option></select></div>
      </div>
      %s
      <div class="field"><label>Location</label><input name="location" required></div>
      <div class="field"><label>Describe the hazard or near-miss</label><textarea name="description" required></textarea></div>
      %s
      <button class="btn gold" type="submit">Submit official HID for review</button>
    </form>""" % (request_hidden, dept_opts, employee_field, R.hse_fields(include_cause=True))
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
      %s
      <label class="field"><input type="checkbox" name="lti" value="1" style="width:auto;margin-right:8px">This was a Lost Time Injury (triggers department point reset)</label>
      <button class="btn gold" type="submit">Report incident (+%d pts)</button>
      <p class="hint">Reporting incidents promptly is rewarded. A Lost Time Injury resets the department's monthly safety points and is logged for audit.</p>
    </form>""" % (dept_opts, R.hse_fields(include_cause=True, include_lost_days=True), D.POINTS["incident"])
    return R.section("Report an incident", form)


def body_hid_request_form(user, qs):
    mine = [r for r in D.DB["worker_hid_requests"] if r["employee_id"] == user["id"]]
    mine.sort(key=lambda r: r["created_date"], reverse=True)
    rows = [[D.fmt_date(r["created_date"]), R.esc(r["hazard_summary"]),
             R.esc(r.get("location_id") or ""), R.esc(r["urgency"]),
             request_status_badge(r["request_status"])] for r in mine[:10]]
    form = """<form method="post" action="/hid/request" class="card form-card">
      <div class="row-inline">
        <div class="field"><label>Location</label><input name="location_id" placeholder="e.g. Process Plant" required></div>
        <div class="field"><label>Urgency</label><select name="urgency"><option>Low</option><option selected>Medium</option><option>High</option><option>Critical</option></select></div>
      </div>
      <div class="field"><label>Hazard summary</label><input name="hazard_summary" required></div>
      <div class="field"><label>Hazard description</label><textarea name="hazard_description" required></textarea></div>
      <div class="field"><label>Photo reference</label><input name="photo_reference" placeholder="optional file name or link"></div>
      <button class="btn gold" type="submit">Submit HID request</button>
    </form>"""
    return R.section("Submit HID request", form) + R.section(
        "My HID requests",
        R.table(["Date", "Summary", "Location", "Urgency", "Status"], rows, "No HID requests yet."))


def body_my_hid_requests(user, qs):
    mine = [r for r in D.DB["worker_hid_requests"] if r["employee_id"] == user["id"]]
    mine.sort(key=lambda r: r["created_date"], reverse=True)
    rows = []
    for r in mine:
        champ = D.user(r.get("champion_id"))
        rows.append([D.fmt_date(r["created_date"]), R.esc(r["hazard_summary"]),
                     R.esc(r.get("location_id") or ""), R.esc(r["urgency"]),
                     R.esc(champ["name"] if champ else "Unassigned"),
                     request_status_badge(r["request_status"])])
    return R.section("My HID requests", R.table(
        ["Date", "Summary", "Location", "Urgency", "Champion", "Status"], rows,
        "No HID requests yet."))


def _champion_requests(user):
    return [r for r in D.DB["worker_hid_requests"]
            if r.get("department_id") == user.get("dept_key")]


def body_champion_dashboard(user, qs):
    reqs = _champion_requests(user)
    submitted = len([r for r in reqs if r["request_status"] in ("Submitted", "Assigned to Champion")])
    drafting = len([r for r in reqs if r["request_status"] == "Champion Drafting"])
    converted = len([r for r in reqs if r.get("converted_to_hid_id")])
    cards = '<div class="grid cols-3">%s%s%s</div>' % (
        R.stat_card("New employee requests", submitted),
        R.stat_card("Champion drafting", drafting),
        R.stat_card("Converted to official HID", converted),
    )
    recent = sorted(reqs, key=lambda r: r["created_date"], reverse=True)[:8]
    rows = []
    for r in recent:
        emp = D.user(r["employee_id"])
        rows.append([D.fmt_date(r["created_date"]), R.esc(emp["name"] if emp else "?"),
                     R.esc(r["hazard_summary"]), R.esc(r.get("location_id") or ""),
                     request_status_badge(r["request_status"])])
    return R.section("Champion dashboard", cards) + R.section(
        "Recent department HID requests",
        R.table(["Date", "Employee", "Summary", "Location", "Status"], rows, "No employee requests."))


def body_champion_employee_search(user, qs):
    q = (q1(qs, "q") or "").strip()
    contractor = q1(qs, "contractor") or ""
    status = q1(qs, "status") or "Active"
    page_no = qint(qs, "page", 1) or 1
    page_size = min(max(qint(qs, "page_size", 25) or 25, 10), 100)
    employees = [u for u in D.DB["users"]
                 if D.has_role(u, "worker") and u.get("dept_key") == user.get("dept_key")]
    if q:
        needle = q.lower()
        employees = [e for e in employees
                     if needle in e["name"].lower()
                     or needle in str(e.get("employee_id", "")).lower()
                     or needle == str(e["id"])
                     or needle in (e.get("title", "").lower())]
    if contractor:
        cid = D.contractor_id_from_value(contractor)
        employees = [e for e in employees if e.get("company_id") == cid]
    if status:
        employees = [e for e in employees if e.get("status", "Active") == status]
    employees.sort(key=lambda e: e["name"])
    contractor_ids = sorted({e.get("company_id") for e in employees if e.get("company_id")})
    contractor_items = [(cid, _contractor_label(cid)) for cid in contractor_ids]
    base_params = {"q": q, "contractor": contractor, "status": status, "page_size": page_size}
    page_no, pages, pager = _pagination(len(employees), page_no, page_size, base_params, path="/champion/employees")
    start = (page_no - 1) * page_size
    shown = employees[start:start + page_size]
    controls = """<form class="filter-bar" method="get">
        <div class="field"><label>Employee search</label><input name="q" value="%s" placeholder="name, employee id or title"></div>
        <div class="field"><label>Contractor</label><select name="contractor">%s</select></div>
        <div class="field"><label>Status</label><select name="status">%s</select></div>
        <div class="field"><label>Page size</label><select name="page_size">%s</select></div>
        <button class="btn">Search</button>
        <a class="btn ghost" href="/champion/employees">Reset</a>
      </form>""" % (
        R.esc(q1(qs, "q") or ""),
        _opts(contractor_items, contractor, "All contractors"),
        _opts([("Active", "Active"), ("Inactive", "Inactive")], status, "All"),
        _opts([(10, "10"), (25, "25"), (50, "50"), (100, "100")], page_size, ""),
    )
    rows = []
    for e in shown:
        open_requests = len([r for r in D.DB.get("worker_hid_requests", [])
                             if r.get("employee_id") == e["id"] and r.get("request_status") not in ("Approved", "Rejected", "Closed")])
        create = '<a class="btn sm gold" href="/report/hid?employee_id=%d">Create HID</a>' % e["id"]
        contractor_name = R.esc(_contractor_label(e.get("company_id"))) if e.get("is_contractor") else "-"
        rows.append([R.esc(D.employee_display_id(e)), R.esc(e["name"]), R.esc(e.get("title", "")),
                     R.dept_label_html(e.get("dept_key")), contractor_name, _status_badge(e.get("status", "Active")),
                     "%d pts" % D.lifetime_points(e["id"]), "%d" % open_requests, create])
    hint = '<p class="hint">Champions can search and select employees only inside their assigned department.</p>'
    return hint + controls + R.section("Employee Search",
        R.table(["Employee ID", "Name", "Title", "Department", "Contractor", "Status", "Lifetime points", "Open HID requests", ""],
                rows, "No employees found.") + pager)


def body_champion_hid_requests(user, qs):
    reqs = _champion_requests(user)
    reqs.sort(key=lambda r: r["created_date"])
    rows = []
    for r in reqs:
        emp = D.user(r["employee_id"])
        action = "&mdash;"
        if not r.get("converted_to_hid_id") and r["request_status"] not in ("Rejected", "Closed"):
            action = """<form class="inline" method="post" action="/champion/hid-requests">
                <input type="hidden" name="action" value="convert">
                <input type="hidden" name="id" value="%d">
                <button class="btn ok sm">Convert to official HID</button></form>""" % r["id"]
        rows.append([D.fmt_date(r["created_date"]), R.esc(emp["name"] if emp else "?"),
                     R.esc(r["hazard_summary"]), R.esc(r.get("location_id") or ""),
                     R.esc(r["urgency"]), request_status_badge(r["request_status"]), action])
    return R.section("Pending employee HID requests", R.table(
        ["Date", "Employee", "Summary", "Location", "Urgency", "Status", ""], rows,
        "No department HID requests."))


def body_review(user, qs):
    sections = []

    if D.has_perm(user, "hid.verify"):
        pending_hids = [h for h in D.DB["near_miss_hazard_reports"]
                        if h.get("supervisor_verification_status") in (None, "pending")
                        and h.get("status") == "submitted"
                        and D.can_access_department(user, h.get("dept_key"))]
        pending_hids.sort(key=lambda h: h["ts"])
        rows = []
        for h in pending_hids:
            employee = D.user(h.get("submitted_for_user_id") or h.get("reporter_id"))
            action = """<form class="inline" method="post" action="/review">
                <input type="hidden" name="rtype" value="hid">
                <input type="hidden" name="id" value="%d">
                <button class="btn ok sm" name="action" value="verify">Verify</button></form>""" % h["id"]
            rows.append([D.fmt_date(h["ts"]), R.esc(employee["name"] if employee else "?"),
                         R.dept_label_html(h["dept_key"]), R.esc(h.get("type", "Hazard")),
                         R.esc(h.get("location", "")), action])
        sections.append(R.section("HID verification queue",
            R.table(["Date", "Employee", "Department", "Type", "Location", "Supervisor action"],
                    rows, "No HIDs awaiting supervisor verification.")))

    if D.has_perm(user, "hid.approve"):
        hse_hids = [h for h in D.DB["near_miss_hazard_reports"]
                    if h.get("supervisor_verification_status") == "verified"
                    and h.get("hse_approval_status") in (None, "pending")
                    and D.can_access_department(user, h.get("dept_key"))]
        hse_hids.sort(key=lambda h: h["ts"])
        rows = []
        for h in hse_hids:
            employee = D.user(h.get("submitted_for_user_id") or h.get("reporter_id"))
            action = """<form class="inline" method="post" action="/review">
                <input type="hidden" name="rtype" value="hid">
                <input type="hidden" name="id" value="%d">
                <button class="btn ok sm" name="action" value="approve">Approve +%d</button></form>
              <form class="inline" method="post" action="/review">
                <input type="hidden" name="rtype" value="hid">
                <input type="hidden" name="id" value="%d">
                <input name="reason" placeholder="Reason" style="width:150px;display:inline-block">
                <button class="btn bad sm" name="action" value="reject">Reject</button></form>
              <form class="inline" method="post" action="/review" data-confirm="Confirm a violation and deduct %d points?">
                <input type="hidden" name="rtype" value="hid">
                <input type="hidden" name="id" value="%d">
                <button class="btn sm" name="action" value="violation">Violation &minus;%d</button></form>""" % (
                    h["id"], D.POINTS["hid"], h["id"], D.VIOLATION_PENALTY, h["id"], D.VIOLATION_PENALTY)
            rows.append([D.fmt_date(h["ts"]), R.esc(employee["name"] if employee else "?"),
                         R.dept_label_html(h["dept_key"]), R.esc(h.get("type", "Hazard")),
                         R.esc(h.get("severity", "")), R.esc(h.get("location", "")), action])
        sections.append(R.section("HSE HID approval",
            R.table(["Date", "Employee", "Department", "Type", "Severity", "Location", "HSE decision"],
                    rows, "No verified HIDs awaiting HSE approval.")))

    if D.has_perm(user, "points.process_automatic"):
        pending_obs = [o for o in D.DB["safety_observations"] if o["status"] == "submitted"]
        pending_obs.sort(key=lambda o: o["ts"])
        rows = []
        for o in pending_obs:
            if not D.can_access_department(user, o.get("dept_key")):
                continue
            rep = D.user(o["reporter_id"])
            actions = ("""<form class="inline" method="post" action="/review">
                <input type="hidden" name="rtype" value="observation">
                <input type="hidden" name="id" value="%d"><input type="hidden" name="action" value="approve">
                <button class="btn ok sm">Approve +%d</button></form>
              <form class="inline" method="post" action="/review">
                <input type="hidden" name="rtype" value="observation">
                <input type="hidden" name="id" value="%d"><input type="hidden" name="action" value="reject">
                <button class="btn bad sm">Reject</button></form>"""
                % (o["id"], D.POINTS["observation"], o["id"]))
            rows.append([D.fmt_date(o["ts"]), R.esc(rep["name"] if rep else "?"),
                         R.dept_label_html(o["dept_key"]), R.esc(o["category"]),
                         R.esc(o["location"]), actions])
        sections.append(R.section("Observation approval",
            R.table(["Date", "Reporter", "Department", "Category", "Location", "Decision"],
                    rows, "No observations awaiting HSE approval.")))

    return "".join(sections) if sections else R.section("Review queue", '<div class="empty">%s</div>' % ACCESS_DENIED)


def body_actions(user, qs):
    create = ""
    if D.has_perm(user, "action.assign"):
        dept_opts = "".join('<option value="%s">%s</option>' % (d["key"], R.esc("%s — %s" % (d["adinkra_name"], d.get("department", "")))) for d in D.DB["departments"])
        worker_opts = "".join('<option value="%d">%s</option>' % (u["id"], R.esc(u["name"])) for u in D.DB["users"] if D.has_role(u, "worker"))
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
    if not D.has_perm(user, "report.view_company"):
        open_actions = [a for a in open_actions if a.get("owner_id") == user["id"] or a.get("dept_key") == user.get("dept_key")]
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


def _adjustment_visible_users(user):
    users = [u for u in D.DB["users"] if D.has_role(u, "worker") and u.get("active", True)]
    if not D.has_perm(user, "report.view_company"):
        users = [u for u in users if u.get("dept_key") == user.get("dept_key")]
    users.sort(key=lambda u: u["name"])
    return users


def body_point_adjustments(user, qs):
    can_request = D.has_perm(user, "points.adjust_request")
    can_approve = D.has_perm(user, "points.adjust_approve")
    users = _adjustment_visible_users(user)
    form = ""
    if can_request:
        opts = "".join('<option value="%d">%s</option>' % (
            u["id"], R.esc("%s - %s" % (u["name"], D.dept_name(u.get("dept_key"))))) for u in users)
        form = R.section("Request manual point adjustment", """<form method="post" action="/points/adjustments" class="card form-card">
          <input type="hidden" name="action" value="request">
          <div class="row-inline">
            <div class="field"><label>Employee</label><select name="user_id" required>%s</select></div>
            <div class="field"><label>Points (+/-)</label><input name="points" type="number" required></div>
          </div>
          <div class="field"><label>Mandatory reason</label><textarea name="reason" required></textarea></div>
          <div class="field"><label>Supporting reference</label><input name="supporting_reference" placeholder="report id, investigation ref, email ref" required></div>
          <button class="btn gold">Submit for HSE Manager approval</button>
        </form>""" % opts)

    pending = [a for a in D.DB.get("point_adjustment_requests", []) if a.get("status") == "pending"]
    if not D.has_perm(user, "report.view_company"):
        pending = [a for a in pending if a.get("dept_key") == user.get("dept_key")]
    pending.sort(key=lambda a: a.get("ts", ""))
    prows = []
    for a in pending:
        target = D.user(a["user_id"])
        requester = D.user(a["requested_by"])
        action = "&mdash;"
        if can_approve and a.get("requested_by") != user["id"]:
            action = """<form class="inline" method="post" action="/points/adjustments">
                <input type="hidden" name="action" value="approve">
                <input type="hidden" name="id" value="%d">
                <button class="btn ok sm">Approve</button></form>
              <form class="inline" method="post" action="/points/adjustments">
                <input type="hidden" name="action" value="reject">
                <input type="hidden" name="id" value="%d">
                <input name="decision_reason" placeholder="Rejection reason" style="width:150px;display:inline-block">
                <button class="btn bad sm">Reject</button></form>""" % (a["id"], a["id"])
        elif can_approve:
            action = '<span class="hint">Self-approval blocked</span>'
        prows.append([D.fmt_date(a["ts"]), R.esc(target["name"] if target else "?"),
                      R.dept_label_html(a.get("dept_key")), "<strong>%+d</strong>" % a["points"],
                      R.esc(requester["name"] if requester else "?"), R.esc(a["reason"]),
                      R.esc(a["supporting_reference"]), action])
    approvals = ""
    if can_approve:
        approvals = R.section("Pending HSE Manager approval",
            R.table(["Requested", "Employee", "Department", "Points", "Requester", "Reason", "Reference", "Decision"],
                    prows, "No pending manual point adjustments."))

    mine = [a for a in D.DB.get("point_adjustment_requests", [])
            if a.get("requested_by") == user["id"] or (can_approve and D.can_access_department(user, a.get("dept_key")))]
    mine.sort(key=lambda a: a.get("ts", ""), reverse=True)
    rows = []
    for a in mine[:80]:
        target = D.user(a["user_id"])
        rows.append([D.fmt_date(a["ts"]), R.esc(target["name"] if target else "?"),
                     R.dept_label_html(a.get("dept_key")), "%+d" % a["points"],
                     adjustment_status_badge(a.get("status", "pending")),
                     R.esc(a.get("decision_reason") or a.get("reason", ""))])
    history = R.section("Adjustment history",
        R.table(["Date", "Employee", "Department", "Points", "Status", "Reason / decision"],
                rows, "No point adjustment requests yet."))
    return form + approvals + history


def body_points(user, qs):
    dept = q1(qs, "dept")
    entries = list(D.DB["safety_points"])
    if dept:
        entries = [p for p in entries if p["dept_key"] == dept]
    if D.has_role(user, "worker") and not has_any_perm(user, "report.view_department", "report.view_company"):
        entries = [p for p in entries if p["user_id"] == user["id"]]
    entries.sort(key=lambda p: p["ts"], reverse=True)
    rows = []
    for p in entries[:200]:
        u = D.user(p["user_id"])
        rows.append([D.fmt_date(p["ts"]), R.esc(u["name"] if u else "?"),
                     R.dept_label_html(p["dept_key"]), R.esc(p["reason"]),
                     '<strong>+%d</strong>' % p["points"]])
    dept_filter = ""
    if has_any_perm(user, "report.view_department", "report.view_company"):
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
    head = ""
    if D.has_perm(user, "reward.view_eligibility"):
        head = (R.stat_card("Available points", "%d pts" % bal)
                + R.stat_card("Reserved points", "%d pts" % D.reserved_points(user["id"]))
                + R.stat_card("Lifetime points", "%d pts" % D.lifetime_points(user["id"])))
    cards = ""
    for rw in D.DB["rewards"]:
        if not rw["active"]:
            continue
        can_afford = D.has_perm(user, "reward.request") and bal >= rw["point_cost"]
        btn = ""
        if D.has_perm(user, "reward.request"):
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
    if D.has_perm(user, "reward.view_eligibility"):
        my_section = (R.reward_flow_diagram()
                      + R.section("My reward requests",
                                  R.table(["Date", "Reward", "Cost", "Value", "Status", "Progress"],
                                          mrows, "No requests yet.")))
    return (head and '<div class="grid cols-3" style="margin-bottom:18px">%s</div>' % head or "") + catalogue + my_section


def body_reward_approvals(user, qs):
    return R.section("Reward approvals removed",
        '<div class="empty">Reward Administrator approval has been removed. Valid reward requests go directly to Finance.</div>')


def body_reward_finance(user, qs):
    yr, mo = D.today().year, D.today().month

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
            <button class="btn bad sm" name="action" value="reject">Reject</button></form>
          <form class="inline" method="post" action="/rewards/releases">
            <input type="hidden" name="id" value="%d">
            <button class="btn ghost sm" name="action" value="hold">Budget hold</button></form>
          <form class="inline" method="post" action="/rewards/releases">
            <input type="hidden" name="id" value="%d">
            <button class="btn ghost sm" name="action" value="defer_month">Defer month</button></form>""" % (
                r["id"], r["id"], r["id"], r["id"])
        prows.append([D.fmt_date(r["ts"]), R.esc(u["name"] if u else "?"), R.dept_label_html(r["dept_key"]),
                      R.esc(D.reward(r["reward_id"])["name"]), "%d pts" % r["point_cost"],
                      "%d pts" % D.rewardable_points(r["user_id"]), D.fmt_money(r["cash_value"]), decide])
    finance_tbl = R.section("Awaiting Finance approval",
        R.table(["Submitted", "Worker", "Department", "Reward", "Points", "Eligible points", "Value", "Decision"], prows,
                "Nothing awaiting finance approval."))

    appr = [r for r in D.DB["reward_requests"]
            if r["status"] in ("finance_approved", "budget_hold", "deferred_next_month", "deferred_next_quarter")]
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
            <input name="release_reference" placeholder="Reference" style="width:130px;display:inline-block">
            <button class="btn ok sm" name="action" value="release">Release %s</button></form>""" % (r["id"], D.fmt_money(r["cash_value"]))
        rrows.append([D.fmt_date(r["ts"]), R.esc(u["name"] if u else "?"), R.dept_label_html(r["dept_key"]),
                      R.esc(D.reward(r["reward_id"])["name"]), R.status_badge(r["status"]),
                      D.fmt_money(r["cash_value"]), flag, btn])
    release_tbl = R.section("Reward release",
        R.table(["Submitted", "Worker", "Department", "Reward", "Status", "Value", "Dept budget", "Release"], rrows,
                "No finance-approved requests awaiting release."))
    return R.reward_flow_diagram("finance_approved") + finance_tbl + release_tbl

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
    dept = q1(qs, "dept") or None
    loc = q1(qs, "location") or None
    rep = _month_records(yr, mo)
    hipo = D.high_potential_events(year=yr, month=mo, dept=dept, location=loc, free=False)
    dmg = D.damage_items(year=yr, month=mo, free=False)
    cc = D.cause_category_counts(year=yr, month=mo, dept=dept, location=loc, free=False)
    hs = D.location_hotspots(year=yr, month=mo, dept=dept, location=loc, free=False)[:5]
    lahp = D.low_actual_high_potential(year=yr, month=mo, free=False)

    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label>%s</div>
      <div class="field"><label>Quarter (auto from month)</label><div>%s</div></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:80px"></div>
      <div class="field"><label>Department</label><select name="dept">%s</select></div>
      <div class="field"><label>Location</label><select name="location">%s</select></div>
      <button class="btn">Generate</button>
      <button type="button" class="btn ghost" onclick="window.print()">Print view</button>
      <a class="btn ghost" href="/reports.csv?%s">Export CSV</a>
    </form>""" % (R.month_select("month", mo), R.quarter_box(mo), yr, _dept_opts(dept),
                  _opts([(l, l) for l in D.location_options()], loc, "All locations"),
                  urlencode({"year": yr, "month": mo, "dept": dept or "", "location": loc or ""}))
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
               R.stat_card("Reward spend", D.fmt_money(rep["reward_spend"])))
        + '<div style="height:14px"></div>'
        + grid(R.stat_card("High-Potential Events", len(hipo)),
               R.stat_card("Property / Equipment Damage", len(dmg)),
               R.stat_card("Open Corrective Actions", len(rep["ca_open_now"])),
               R.stat_card("Overdue Corrective Actions", len(rep["ca_overdue"]))))

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

    # ---- High-Potential, Damage, Hotspots, Cause, Actual-vs-Potential -----
    hipo_equip = sum(1 for e in hipo if e["rtype"] == "damage" or e["rec"].get("equipment_involved"))
    hipo_sec = R.section("High-Potential Events · %s" % mlabel,
        grid(R.stat_card("Total", len(hipo)),
             R.stat_card("Involving equipment", hipo_equip),
             R.stat_card("Low actual / high potential", len(lahp)), cols=3))

    equip_dmg = sum(1 for p in dmg if p.get("equipment_involved"))
    downtime = sum(p.get("downtime_hours", 0) for p in dmg)
    dmg_sec = R.section("Property / Equipment Damage · %s" % mlabel,
        grid(R.stat_card("Total damage cases", len(dmg)),
             R.stat_card("Equipment damage cases", equip_dmg),
             R.stat_card("Total downtime (hrs)", downtime),
             R.stat_card("Locations affected", len({p["location"] for p in dmg}))))

    hs_rows = [["#%d" % i, R.esc(s["location"]), "<strong>%d</strong>" % s["total"], s["incident"], s["hid"],
                s["near_miss"], R.risk_badge(s["highest_risk"]), R.hotspot_badge(s["status"])]
               for i, s in enumerate(hs, 1)]
    hotspot_sec = R.section("Top 5 hotspot locations · %s" % mlabel,
        R.table(["Rank", "Location", "Total", "Incidents", "HIDs", "Near Miss", "Highest Risk", "Status"], hs_rows, "No reports."))

    cause_sec = R.section("Top cause categories · %s" % mlabel, R.bar_chart(cc.most_common(5)))

    reps_avp = D._norm_reports(year=yr, month=mo, dept=dept, location=loc, free=False)
    actual_dist = Counter(r["rec"].get("actual_consequence") or "—" for r in reps_avp)
    pot_dist = Counter(r["rec"].get("potential_consequence") or "—" for r in reps_avp)
    avp_rows = [[R.esc(c), "%d" % actual_dist.get(c, 0), "%d" % pot_dist.get(c, 0)] for c in D.CONSEQUENCES]
    avp_sec = R.section("Actual vs potential consequence · %s" % mlabel,
        R.table(["Consequence", "Actual count", "Potential count"], avp_rows))

    pro = R.section("Advanced reporting", '<div class="grid cols-3">%s%s%s</div>' % (
        R.pro_card("Quarterly & yearly reports", "Roll-up reporting beyond the month."),
        R.pro_card("Excel & PDF export", "Formatted exports; CSV is included free."),
        R.pro_card("Scheduled reports", "Automated email delivery on a schedule.")))

    return (controls + intro + summary + observations + hid + nearmiss + incidents
            + lti + hipo_sec + dmg_sec + actions + rewards + budget
            + hotspot_sec + cause_sec + avp_sec + departments + contractors + pro)


def body_budgets(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    q = D.quarter_of_month(mo)
    editable = D.can_edit_budget(user)

    note = ('<p class="hint">Visible to authorised budget roles. '
            + ("You can create, edit and lock budgets."
               if editable else "Budget setup is read-only for this role.") + "</p>")

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


def _contractor_label(contractor_id):
    c = D.company(contractor_id)
    if not c:
        return ""
    return c.get("contractor_name") or c.get("name") or ""


def _employee_label(user_id):
    u = D.user(user_id)
    if not u:
        return ""
    return "%s - %s" % (D.employee_display_id(u), u.get("name", ""))


def _parse_employee_tokens(text):
    raw = (text or "").replace(";", ",").replace("\r", "\n")
    tokens = []
    for part in raw.replace(",", "\n").split("\n"):
        part = part.strip()
        if part:
            tokens.append(part)
    return tokens


def _employee_by_token(token):
    token = str(token or "").strip()
    if not token:
        return None
    emp = D.employee_by_employee_id(token)
    if emp:
        return emp
    try:
        return D.user(int(token.lstrip("#")))
    except ValueError:
        return None


def _status_badge(status):
    return R.badge(status or "Active", "ok" if status != "Inactive" else "muted")


def _employee_matches_filters(u, q="", dept="", contractor="", employment="", status=""):
    if q:
        hay = " ".join(str(x or "") for x in (
            u.get("employee_id"), u.get("name"), u.get("full_name"), u.get("title"),
            u.get("job_title"), u.get("email"), u.get("phone"), u.get("id"),
        )).lower()
        if q.lower() not in hay:
            return False
    if dept and u.get("dept_key") != dept:
        return False
    if contractor:
        cid = D.contractor_id_from_value(contractor)
        if u.get("company_id") != cid:
            return False
    if employment and u.get("employment_type") != employment:
        return False
    if status and u.get("status", "Active") != status:
        return False
    return True


def _pagination(total, page_no, page_size, base_params, path="/admin"):
    pages = max(1, (total + page_size - 1) // page_size)
    page_no = max(1, min(page_no, pages))
    params_prev = dict(base_params, page=page_no - 1)
    params_next = dict(base_params, page=page_no + 1)
    prev_link = '<a class="btn sm ghost" href="%s?%s">Previous</a>' % (path, urlencode(params_prev)) if page_no > 1 else ""
    next_link = '<a class="btn sm ghost" href="%s?%s">Next</a>' % (path, urlencode(params_next)) if page_no < pages else ""
    return page_no, pages, '<div class="row-inline" style="justify-content:flex-end">%s<span class="hint">Page %d of %d</span>%s</div>' % (
        prev_link, page_no, pages, next_link)


def _employee_csv_rows(text):
    text = (text or "").strip()
    if not text:
        return []
    sample = text[:2048]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for idx, row in enumerate(reader, start=2):
        clean = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
        clean["_line"] = idx
        rows.append(clean)
    return rows


def _employee_field(row, *names):
    for name in names:
        val = row.get(name)
        if val not in (None, ""):
            return val
    return ""


def body_admin_legacy(user, qs):
    can_roles = D.has_perm(user, "role.manage")
    dept_opts_plain = "".join('<option value="%s">%s</option>' % (d["key"], R.esc(d["adinkra_name"])) for d in D.DB["departments"])

    # 1. Create user --------------------------------------------------------
    role_opts = "".join('<option value="%s">%s</option>' % (r, R.esc(D.role_label(r))) for r in D.ROLE_ORDER)
    create = R.section("Create user", """<form method="post" action="/admin" class="card form-card">
        <input type="hidden" name="action" value="create_user">
        <div class="row-inline">
          <div class="field"><label>Full name</label><input name="name" required></div>
          <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
          <div class="field"><label>Initial role</label><select name="role">%s</select></div>
        </div>
        <button class="btn gold">Create user</button>
        <p class="hint">New users receive only the role you assign. Approval permissions (HID, incident,
        reward, finance, investigation) always require an explicit role.</p>
      </form>""" % (dept_opts_plain, role_opts)) if can_roles else ""

    # 2. User & role management (searchable) --------------------------------
    q = (q1(qs, "q") or "").strip().lower()
    rolef = q1(qs, "rolef") or ""
    users = D.DB["users"]
    if q:
        users = [u for u in users if q in u["name"].lower() or q == str(u["id"])]
    if rolef:
        users = [u for u in users if D.has_role(u, rolef)]
    if not q and not rolef:
        users = [u for u in users if D.user_roles(u) != ["worker"]]  # default: role-holders
    total_matches = len(users)
    shown = users[:12]
    search = """<form class="filter-bar" method="get">
        <div class="field"><label>Search user</label><input name="q" value="%s" placeholder="name or id"></div>
        <div class="field"><label>Filter role</label><select name="rolef">%s</select></div>
        <button class="btn">Search</button>
        <a class="btn ghost" href="/admin">Reset</a></form>""" % (
        R.esc(q1(qs, "q") or ""), _opts([(r, D.role_label(r)) for r in D.ROLE_ORDER], rolef, "All roles"))
    urows = []
    for u in shown:
        roles = D.user_roles(u)
        checks = ""
        for r in D.ROLE_ORDER:
            ck = " checked" if r in roles else ""
            checks += ('<label style="display:inline-block;margin:0 8px 4px 0;font-size:12px;font-weight:500">'
                       '<input type="checkbox" name="role_%s" value="1"%s style="width:auto;margin-right:3px">%s</label>'
                       % (r, ck, R.esc(D.role_label(r))))
        form = ("""<form method="post" action="/admin">
            <input type="hidden" name="action" value="set_roles"><input type="hidden" name="user_id" value="%d">
            %s<button class="btn sm">Save roles</button></form>""" % (u["id"], checks)) if can_roles else R.esc(", ".join(D.role_label(r) for r in roles))
        urows.append(["#%d" % u["id"], R.esc(u["name"]), R.esc(D.dept_name(u.get("dept_key"))), form])
    more = ('<p class="hint">Showing %d of %d matching users — use search to find others.</p>'
            % (len(shown), total_matches)) if total_matches > len(shown) else ""
    users_tbl = R.section("User & role management",
        search + R.table(["ID", "User", "Department", "Roles (tick to assign, then Save)"], urows,
                         "No matching users.") + more)

    # 3. Department Safety Champion assignment (max 5 per department) -------
    crows = []
    for d in D.DB["departments"]:
        champs = [u for u in D.DB["users"] if D.has_role(u, "champion") and u.get("dept_key") == d["key"]]
        cand = [u for u in D.DB["users"] if u.get("dept_key") == d["key"] and not D.has_role(u, "champion")]
        cand_opts = "".join('<option value="%d">%s</option>' % (u["id"], R.esc(u["name"])) for u in cand)
        champ_chips = ""
        for c in champs:
            champ_chips += ('<form class="inline" method="post" action="/admin"><input type="hidden" name="action" value="remove_champion">'
                            '<input type="hidden" name="user_id" value="%d"><span class="badge badge-ok">%s</span>'
                            '<button class="btn sm bad" title="Remove">&times;</button></form> ' % (c["id"], R.esc(c["name"])))
        add = ""
        if can_roles and len(champs) < D.FREE_LIMITS.get("champions_per_dept", 5):
            add = ('<form class="inline" method="post" action="/admin"><input type="hidden" name="action" value="assign_champion">'
                   '<input type="hidden" name="dept_key" value="%s"><select name="user_id">%s</select>'
                   '<button class="btn sm">Add champion</button></form>' % (d["key"], cand_opts))
        elif can_roles:
            add = '<span class="hint">Max 5 reached.</span>'
        crows.append([R.esc(d["adinkra_name"]), "%d / 5" % len(champs), (champ_chips or "&mdash;") + " " + add])
    champ_tbl = R.section("Department Safety Champion assignment",
        R.table(["Department", "Champions", "Manage (max 5 per department)"], crows))

    # 4. Department employees -> reward limits ------------------------------
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
                        d["active_employees"], d["employee_count"], D.fmt_money(D.dept_monthly_limit(d)))
    emp = R.section("Department employees → reward limits", '<div class="card">%s</div>' % dept_rows)

    # 5. Audit logs ---------------------------------------------------------
    logs = list(reversed(D.DB.get("audit_logs", [])))[:12]
    lrows = [[D.fmt_date(a.get("timestamp", "")), "#%s" % a.get("user_id"), R.esc(a.get("user_role", "")),
              R.esc(a.get("action", "")), R.esc(a.get("module", "")), R.esc(str(a.get("record_id", "")))]
             for a in logs]
    audit = R.section("Audit logs (most recent)",
        R.table(["When", "User", "Role(s)", "Action", "Module", "Record"], lrows, "No audit entries yet."))

    # 6. Demo data ----------------------------------------------------------
    reset = R.section("Demo data", """<div class="card">
        <p>Reseed the demo from scratch (clears the runtime JSON store).</p>
        <form method="post" action="/admin" data-confirm="Reset all demo data?">
          <input type="hidden" name="action" value="reset_demo">
          <button class="btn bad">Reset &amp; reseed demo data</button></form></div>""")

    return create + users_tbl + champ_tbl + emp + audit + reset


# --------------------------------------------------------------------------
# Included HSE modules
# --------------------------------------------------------------------------


def _rtype_label(rt):
    return D.REPORT_TYPES.get(rt, (rt.title(),))[0]


def _opts(items, cur, blank):
    o = '<option value="">%s</option>' % R.esc(blank)
    for val, label in items:
        o += '<option value="%s"%s>%s</option>' % (R.esc(val), " selected" if str(val) == str(cur) else "", R.esc(label))
    return o


def _dept_opts(selected=None):
    return _opts([(d["key"], "%s — %s" % (d["adinkra_name"], d.get("department", ""))) for d in D.DB["departments"]],
                 selected, "All departments")


def body_admin(user, qs):
    can_roles = D.has_perm(user, "role.manage")
    dept_items = [(d["key"], D.dept_name(d["key"])) for d in D.DB["departments"]]
    contractor_items = [(c["id"], c.get("contractor_name") or c.get("name")) for c in D.valid_contractors()]
    dept_opts_plain = "".join('<option value="%s">%s</option>' % (d["key"], R.esc(D.dept_name(d["key"]))) for d in D.DB["departments"])

    role_opts = "".join('<option value="%s">%s</option>' % (r, R.esc(D.role_label(r))) for r in D.ROLE_ORDER)
    create = R.section("Create employee", """<form method="post" action="/admin" class="card form-card">
        <input type="hidden" name="action" value="create_user">
        <div class="row-inline">
          <div class="field"><label>Full name</label><input name="name" required></div>
          <div class="field"><label>Employee ID</label><input name="employee_id" placeholder="auto if blank"></div>
          <div class="field"><label>Phone</label><input name="phone" placeholder="password uses last 4 digits"></div>
          <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
          <div class="field"><label>Employment type</label><select name="employment_type"><option>Internal</option><option>Contractor</option></select></div>
        </div>
        <div class="row-inline">
          <div class="field"><label>Contractor company</label><select name="contractor_id">%s</select></div>
          <div class="field"><label>Job title</label><input name="job_title" placeholder="Worker"></div>
          <div class="field"><label>Initial role</label><select name="role">%s</select></div>
        </div>
        <button class="btn gold">Create employee</button>
      </form>""" % (dept_opts_plain, _opts(contractor_items, "", "None / internal"), role_opts)) if can_roles else ""

    q = (q1(qs, "q") or "").strip()
    dept = D.department_key_from_value(q1(qs, "dept") or "", default="") or ""
    contractor = q1(qs, "contractor") or ""
    employment = q1(qs, "employment") or ""
    status = q1(qs, "status") or ""
    page_no = qint(qs, "page", 1) or 1
    page_size = min(max(qint(qs, "page_size", 25) or 25, 10), 100)
    employees = [u for u in D.DB["users"] if _employee_matches_filters(u, q, dept, contractor, employment, status)]
    employees.sort(key=lambda u: (D.dept_name(u.get("dept_key")), u.get("name", "")))
    total_employees = len(employees)
    base_params = {"q": q, "dept": dept, "contractor": contractor, "employment": employment,
                   "status": status, "page_size": page_size}
    page_no, pages, pager = _pagination(total_employees, page_no, page_size, base_params)
    start = (page_no - 1) * page_size
    shown_employees = employees[start:start + page_size]

    cards = '<div class="grid cols-4">%s%s%s%s</div>' % (
        R.stat_card("Workforce", len(D.DB["users"])),
        R.stat_card("Active", sum(1 for u in D.DB["users"] if u.get("active", True))),
        R.stat_card("Contractors", sum(1 for u in D.DB["users"] if u.get("is_contractor"))),
        R.stat_card("Departments", len(D.DB["departments"])),
    )
    filters = """<form class="filter-bar" method="get">
        <div class="field"><label>Employee search</label><input name="q" value="%s" placeholder="name, ID, title, email"></div>
        <div class="field"><label>Department</label><select name="dept">%s</select></div>
        <div class="field"><label>Contractor</label><select name="contractor">%s</select></div>
        <div class="field"><label>Employment</label><select name="employment">%s</select></div>
        <div class="field"><label>Status</label><select name="status">%s</select></div>
        <div class="field"><label>Page size</label><select name="page_size">%s</select></div>
        <button class="btn">Apply</button>
        <a class="btn ghost" href="/admin">Reset</a></form>""" % (
        R.esc(q), _opts(dept_items, dept, "All departments"),
        _opts(contractor_items, contractor, "All contractors"),
        _opts([("Internal", "Internal"), ("Contractor", "Contractor")], employment, "All"),
        _opts([("Active", "Active"), ("Inactive", "Inactive")], status, "All"),
        _opts([(10, "10"), (25, "25"), (50, "50"), (100, "100")], page_size, ""))
    erows = []
    for u in shown_employees:
        sup = _employee_label(u.get("supervisor_id")) or "-"
        champ = _employee_label(u.get("safety_champion_id")) or "-"
        contractor_name = R.esc(_contractor_label(u.get("company_id"))) if u.get("is_contractor") else "-"
        erows.append([
            R.esc(D.employee_display_id(u)),
            R.esc(u.get("name", "")),
            R.badge(u.get("employment_type", "Internal"), "muted"),
            R.dept_label_html(u.get("dept_key")),
            contractor_name,
            R.esc(u.get("job_title") or u.get("title", "")),
            R.esc(sup),
            R.esc(champ),
            _status_badge(u.get("status", "Active")),
        ])
    employee_table = R.table(
        ["Employee ID", "Name", "Type", "Department", "Contractor", "Job title", "Supervisor", "Safety Champion", "Status"],
        erows, "No employees match the current filters.")

    supervisors = [(u["id"], "%s - %s" % (D.employee_display_id(u), u["name"]))
                   for u in D.DB["users"] if D.has_role(u, "supervisor")]
    champions = [(u["id"], "%s - %s" % (D.employee_display_id(u), u["name"]))
                 for u in D.DB["users"] if D.has_role(u, "champion")]
    bulk = """<form method="post" action="/admin" class="card form-card">
        <input type="hidden" name="action" value="bulk_assign_employees">
        <div class="field"><label>Employee IDs</label><textarea name="employee_ids" placeholder="EMP00012, EMP00044 or one per line" required></textarea></div>
        <div class="row-inline">
          <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
          <div class="field"><label>Supervisor</label><select name="supervisor_id">%s</select></div>
          <div class="field"><label>Safety Champion</label><select name="safety_champion_id">%s</select></div>
          <div class="field"><label>Status</label><select name="status">%s</select></div>
        </div>
        <button class="btn">Apply bulk assignment</button>
      </form>""" % (
        _opts(dept_items, "", "No change"),
        _opts(supervisors, "", "No change"),
        _opts(champions, "", "No change"),
        _opts([("Active", "Active"), ("Inactive", "Inactive")], "", "No change"),
    )
    import_form = """<form method="post" action="/admin" class="card form-card">
        <input type="hidden" name="action" value="import_employees">
        <div class="field"><label>CSV or Excel rows</label><textarea name="csv_text" style="min-height:160px" placeholder="employee_id,full_name,employment_type,department_id,contractor_id,job_title,supervisor_id,safety_champion_id,shift,site,email,phone,status"></textarea></div>
        <button class="btn gold">Import employees</button>
      </form>"""
    employee_master = R.section("Employee Master",
        cards + filters + '<p class="hint">Showing %d of %d matching employees.</p>' % (len(shown_employees), total_employees)
        + employee_table + pager + '<div class="grid cols-2">%s%s</div>' % (bulk, import_form))

    contractor_rows = []
    for c in D.valid_contractors():
        history = R.badge("Yes (%s)" % c.get("incident_count", 0), "warn") if c.get("has_historical_incidents") else R.badge("No", "muted")
        contractor_rows.append([
            R.esc(c.get("contractor_id")),
            R.esc(c.get("contractor_name") or c.get("name")),
            R.esc(c.get("contractor_code", "")),
            R.esc(c.get("service_category", "")),
            R.esc(D.dept_name(c.get("responsible_department"))),
            R.esc("%s to %s" % (c.get("contract_start_date", ""), c.get("contract_end_date", ""))),
            R.esc(c.get("active_workforce_count", 0)),
            R.esc(c.get("contract_owner", "")),
            _status_badge(c.get("status", "Active")),
            history,
            R.esc(c.get("notes", "")),
        ])
    contractor_master = R.section("Contractor Master Register", R.table(
        ["ID", "Contractor", "Code", "Service", "Responsible department", "Contract dates",
         "Workforce", "Owner", "Status", "Historical incidents", "Notes"], contractor_rows))

    user_q = (q1(qs, "user_q") or "").strip().lower()
    rolef = q1(qs, "rolef") or ""
    users = D.DB["users"]
    if user_q:
        users = [u for u in users if user_q in u["name"].lower() or user_q == str(u["id"])
                 or user_q == str(u.get("employee_id", "")).lower()]
    if rolef:
        users = [u for u in users if D.has_role(u, rolef)]
    if not user_q and not rolef:
        users = [u for u in users if D.user_roles(u) != ["worker"]]
    total_matches = len(users)
    shown = users[:12]
    search = """<form class="filter-bar" method="get">
        <div class="field"><label>Search user</label><input name="user_q" value="%s" placeholder="name or id"></div>
        <div class="field"><label>Filter role</label><select name="rolef">%s</select></div>
        <button class="btn">Search</button>
        <a class="btn ghost" href="/admin">Reset</a></form>""" % (
        R.esc(q1(qs, "user_q") or ""), _opts([(r, D.role_label(r)) for r in D.ROLE_ORDER], rolef, "All roles"))
    urows = []
    for u in shown:
        roles = D.user_roles(u)
        checks = ""
        for r in D.ROLE_ORDER:
            ck = " checked" if r in roles else ""
            checks += ('<label style="display:inline-block;margin:0 8px 4px 0;font-size:12px;font-weight:500">'
                       '<input type="checkbox" name="role_%s" value="1"%s style="width:auto;margin-right:3px">%s</label>'
                       % (r, ck, R.esc(D.role_label(r))))
        form = ("""<form method="post" action="/admin">
            <input type="hidden" name="action" value="set_roles"><input type="hidden" name="user_id" value="%d">
            %s<button class="btn sm">Save roles</button></form>""" % (u["id"], checks)) if can_roles else R.esc(", ".join(D.role_label(r) for r in roles))
        urows.append([R.esc(D.employee_display_id(u)), R.esc(u["name"]), R.esc(D.dept_name(u.get("dept_key"))), form])
    more = ('<p class="hint">Showing %d of %d matching users - use search to find others.</p>'
            % (len(shown), total_matches)) if total_matches > len(shown) else ""
    users_tbl = R.section("User & role management",
        search + R.table(["Employee ID", "User", "Department", "Roles (tick to assign, then Save)"], urows,
                         "No matching users.") + more)

    crows = []
    for d in D.DB["departments"]:
        champs = [u for u in D.DB["users"] if D.has_role(u, "champion") and u.get("dept_key") == d["key"]]
        cand = [u for u in D.DB["users"] if u.get("dept_key") == d["key"] and not D.has_role(u, "champion")]
        cand_opts = "".join('<option value="%d">%s</option>' % (u["id"], R.esc("%s - %s" % (D.employee_display_id(u), u["name"]))) for u in cand)
        champ_chips = ""
        for c in champs:
            champ_chips += ('<form class="inline" method="post" action="/admin"><input type="hidden" name="action" value="remove_champion">'
                            '<input type="hidden" name="user_id" value="%d"><span class="badge badge-ok">%s</span>'
                            '<button class="btn sm bad" title="Remove">&times;</button></form> ' % (c["id"], R.esc(c["name"])))
        add = ""
        if can_roles and len(champs) < D.FREE_LIMITS.get("champions_per_dept", 5):
            add = ('<form class="inline" method="post" action="/admin"><input type="hidden" name="action" value="assign_champion">'
                   '<input type="hidden" name="dept_key" value="%s"><select name="user_id">%s</select>'
                   '<button class="btn sm">Add champion</button></form>' % (d["key"], cand_opts))
        elif can_roles:
            add = '<span class="hint">Max 5 reached.</span>'
        crows.append([R.esc(D.dept_name(d["key"])), "%d / 5" % len(champs), (champ_chips or "-") + " " + add])
    champ_tbl = R.section("Department Safety Champion assignment",
        R.table(["Department", "Champions", "Manage (max 5 per department)"], crows))

    dept_rows = ""
    for d in D.DB["departments"]:
        dept_rows += """<form class="inline" method="post" action="/admin" style="display:block;margin-bottom:8px">
            <input type="hidden" name="action" value="set_employees"><input type="hidden" name="dept_key" value="%s">
            <span style="display:inline-block;width:260px"><strong>%s</strong> <span class="kpi-mini">%s</span></span>
            Active <input name="active" type="number" min="0" value="%d" style="width:90px;display:inline-block">
            of <input name="total" type="number" min="0" value="%d" style="width:90px;display:inline-block">
            <button class="btn sm">Update limit</button>
            <span class="hint">limit = %s</span>
          </form>""" % (d["key"], R.esc(D.dept_name(d["key"])), R.esc(d.get("adinkra_name", "")),
                        d["active_employees"], d["employee_count"], D.fmt_money(D.dept_monthly_limit(d)))
    emp = R.section("Department employees -> reward limits", '<div class="card">%s</div>' % dept_rows)

    logs = list(reversed(D.DB.get("audit_logs", [])))[:12]
    lrows = [[D.fmt_date(a.get("timestamp", "")), "#%s" % a.get("user_id"), R.esc(a.get("user_role", "")),
              R.esc(a.get("action", "")), R.esc(a.get("module", "")), R.esc(str(a.get("record_id", "")))]
             for a in logs]
    audit = R.section("Audit logs (most recent)",
        R.table(["When", "User", "Role(s)", "Action", "Module", "Record"], lrows, "No audit entries yet."))

    reset = R.section("Demo data", """<div class="card">
        <p>Reseed the demo from scratch (clears the runtime JSON store).</p>
        <form method="post" action="/admin" data-confirm="Reset all demo data?">
          <input type="hidden" name="action" value="reset_demo">
          <button class="btn bad">Reset &amp; reseed demo data</button></form></div>""")

    return create + employee_master + contractor_master + users_tbl + champ_tbl + emp + audit + reset


def _dept_opts(selected=None):
    return _opts([(d["key"], D.dept_name(d["key"])) for d in D.DB["departments"]],
                 selected, "All departments")


def body_hotspots(user, qs):
    f = dict(year=qint(qs, "year", D.today().year), month=qint(qs, "month") or None,
             dept=_scoped_dept(user, q1(qs, "dept") or None), location=q1(qs, "location") or None,
             report_type=q1(qs, "report_type") or None, risk_level=q1(qs, "risk_level") or None)
    rows = D.location_hotspots(free=True, **f)
    cap = D.FREE_LIMITS["locations"]
    shown, hidden = rows[:cap], max(0, len(rows) - cap)

    mo = f["month"]
    mo_opts = '<option value="">Last 90 days</option>' + "".join(
        '<option value="%d"%s>%s</option>' % (m, " selected" if m == mo else "", D.month_name(m)) for m in range(1, 13))
    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label><select name="month">%s</select></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:80px"></div>
      <div class="field"><label>Department</label><select name="dept">%s</select></div>
      <div class="field"><label>Location</label><select name="location">%s</select></div>
      <div class="field"><label>Report type</label><select name="report_type">%s</select></div>
      <div class="field"><label>Risk level</label><select name="risk_level">%s</select></div>
      <button class="btn">Apply</button>
      <a class="btn ghost" href="/hotspots.csv?%s">Export CSV</a>
    </form>""" % (mo_opts, f["year"], _dept_opts_for_user(user, f["dept"]),
                  _opts([(l, l) for l in D.location_options()], f["location"], "All locations"),
                  _opts([(k, v[0]) for k, v in D.REPORT_TYPES.items()], f["report_type"], "All report types"),
                  _opts([(r, r) for r in D.RISK_LEVELS], f["risk_level"], "All risk levels"),
                  urlencode({k: (v if v else "") for k, v in f.items()}))

    trows = []
    for i, s in enumerate(shown, 1):
        trows.append(["#%d" % i, R.esc(s["location"]), "<strong>%d</strong>" % s["total"],
                      s["incident"], s["hid"], s["near_miss"], s["open_actions"], s["overdue_actions"],
                      R.risk_badge(s["highest_risk"]), R.hotspot_badge(s["status"])])
    table = R.table(["Rank", "Location", "Total Reports", "Incidents", "HIDs", "Near Misses",
                     "Open Actions", "Overdue Actions", "Highest Risk", "Hotspot Status"], trows,
                    "No reports in this range.")

    reps = D._norm_reports(free=True, **f)
    risk_counts = Counter(r["risk_level"] or "Unspecified" for r in reps)
    risk_rows = [(lvl, risk_counts.get(lvl, 0)) for lvl in D.RISK_LEVELS if risk_counts.get(lvl, 0)]
    charts = '<div class="grid cols-2">%s%s</div>' % (
        R.section("Reports by location", R.bar_chart([(s["location"], s["total"]) for s in shown])),
        R.section("Risk-level distribution", R.bar_chart(risk_rows)))
    charts2 = R.section("Open corrective actions by location",
                        R.bar_chart([(s["location"], s["open_actions"]) for s in shown if s["open_actions"]]))

    th = D.hotspot_thresholds()
    thresh = ""
    if D.has_role(user, "hse_manager"):
        thresh = R.section("Hotspot thresholds (HSE Manager)", """<form class="card form-card" method="post" action="/hotspots">
          <input type="hidden" name="action" value="thresholds">
          <div class="row-inline">
            <div class="field"><label>Watch &ge;</label><input name="watch" type="number" min="1" value="%d"></div>
            <div class="field"><label>High Risk &ge;</label><input name="high" type="number" min="1" value="%d"></div>
            <div class="field"><label>Critical &ge;</label><input name="critical" type="number" min="1" value="%d"></div>
          </div><button class="btn">Save thresholds</button>
          <p class="hint">0&ndash;%d Normal · %d&ndash;%d Watch · %d&ndash;%d High Risk · %d+ Critical.</p>
        </form>""" % (th["watch"], th["high"], th["critical"], th["watch"] - 1, th["watch"],
                      th["high"] - 1, th["high"], th["critical"] - 1, th["critical"]))

    banner = R.limit_banner("Showing the top %d hotspot locations; %d additional locations remain available." % (cap, hidden)) if hidden else ""
    pro = R.section("Advanced hotspot analytics", '<div class="grid cols-3">%s%s%s</div>' % (
        R.pro_card("Geographic hotspot maps", "GPS heatmaps, QR location capture & multi-site comparison."),
        R.pro_card("Location risk prediction", "AI predicts emerging hotspots before they escalate."),
        R.pro_card("Unlimited location history", "Full historical trend analysis beyond 90 days.")))
    intro = ('<p class="hint">Locations where incidents, HIDs, near misses, unsafe conditions and '
             'equipment damage are repeatedly reported. Default view: current month + previous 90 days.</p>')
    return intro + controls + banner + R.section("Top hotspot locations", table) + charts + charts2 + thresh + pro


def body_highpotential(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month") or None
    dept = _scoped_dept(user, q1(qs, "dept") or None)
    evts = D.high_potential_events(year=yr, month=mo, dept=dept, free=True)
    total = len(evts)
    open_e = sum(1 for e in evts if e["rec"].get("status") in ("open", "under_review", "submitted"))
    equip = sum(1 for e in evts if e["rtype"] == "damage" or e["rec"].get("equipment_involved"))
    by_loc = Counter(e["location"] for e in evts)
    by_dept = Counter(D.dept_name(e["dept_key"]) for e in evts)

    mo_opts = '<option value="">Last 90 days</option>' + "".join(
        '<option value="%d"%s>%s</option>' % (m, " selected" if m == mo else "", D.month_name(m)) for m in range(1, 13))
    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label><select name="month">%s</select></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:80px"></div>
      <div class="field"><label>Department</label><select name="dept">%s</select></div>
      <button class="btn">Apply</button></form>""" % (mo_opts, yr, _dept_opts_for_user(user, dept))

    cards = '<div class="grid cols-4">%s%s%s%s</div>' % (
        R.stat_card("High-Potential Events", total), R.stat_card("Open", open_e),
        R.stat_card("Involving equipment", equip), R.stat_card("Locations affected", len(by_loc)))
    trows = []
    for e in evts[:100]:
        rec = e["rec"]
        trows.append([D.fmt_date(e["ts"]), R.badge(_rtype_label(e["rtype"]), "muted"),
                      R.dept_label_html(e["dept_key"]), R.esc(e["location"]),
                      R.esc(rec.get("actual_consequence", "")), R.esc(rec.get("potential_consequence", "")),
                      R.risk_badge(e["risk_level"])])
    table = R.table(["Date", "Type", "Department", "Location", "Actual", "Potential", "Risk"], trows,
                    "No high-potential events in this range.")
    charts = '<div class="grid cols-2">%s%s</div>' % (
        R.section("High-potential by location", R.bar_chart(by_loc.most_common(8))),
        R.section("High-potential by department", R.bar_chart(by_dept.most_common(8))))
    pro = R.section("Advanced high-potential tools", '<div class="grid cols-3">%s%s%s</div>' % (
        R.pro_card("Investigation workflow", "Structured ICAM-style investigations."),
        R.pro_card("Failed critical control analysis", "Identify which controls failed."),
        R.pro_card("AI identification", "Auto-flag high-potential events from text.")))
    intro = ('<p class="hint">A record is high-potential when the potential consequence is Major or '
             'Catastrophic, the risk level is Critical, or an HSE reviewer flags it.</p>')
    return intro + controls + cards + charts + R.section("High-potential events", table) + pro


def body_damage(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month") or None
    items = D.damage_items(year=yr, month=mo, free=True)
    by_loc = Counter(p["location"] for p in items)
    equip = Counter(p["equipment_involved"] for p in items if p.get("equipment_involved"))
    downtime = sum(p.get("downtime_hours", 0) for p in items)
    cards = '<div class="grid cols-4">%s%s%s%s</div>' % (
        R.stat_card("Damage cases", len(items)), R.stat_card("Total downtime (hrs)", downtime),
        R.stat_card("Locations affected", len(by_loc)),
        R.stat_card("Distinct assets", len({p.get("asset_number") for p in items})))

    form = """<form method="post" action="/damage" class="card form-card">
      <div class="row-inline">
        <div class="field"><label>Department</label><select name="dept_key">%s</select></div>
        <div class="field"><label>Damage type</label><select name="damage_type">%s</select></div>
      </div>
      <div class="row-inline">
        <div class="field"><label>Equipment involved</label><input name="equipment_involved"></div>
        <div class="field"><label>Asset number</label><input name="asset_number"></div>
      </div>
      <div class="field"><label>Location</label><input name="location" required></div>
      <div class="row-inline">
        <div class="field"><label>Estimated cost range</label><select name="estimated_cost_range">%s</select></div>
        <div class="field"><label>Downtime (hrs)</label><input name="downtime_hours" type="number" min="0" value="0"></div>
        <div class="field"><label>Repair status</label><select name="repair_status">%s</select></div>
      </div>
      <div class="field"><label>Operational impact</label><input name="operational_impact" placeholder="e.g. Partial stoppage"></div>
      <div class="field"><label>Describe the damage</label><textarea name="description" required></textarea></div>
      %s
      <button class="btn gold" type="submit">Log damage event</button>
    </form>""" % ("".join('<option value="%s">%s — %s</option>' % (d["key"], R.esc(d["adinkra_name"]), R.esc(d.get("department", ""))) for d in D.DB["departments"]),
                  "".join("<option>%s</option>" % R.esc(t) for t in D.DAMAGE_TYPES),
                  "".join("<option>%s</option>" % R.esc(c) for c in D.COST_RANGES),
                  "".join("<option>%s</option>" % R.esc(s) for s in D.REPAIR_STATUS),
                  R.hse_fields())

    rows = [[D.fmt_date(p["ts"]), R.esc(p["damage_type"]), R.esc(p.get("equipment_involved", "")),
             R.esc(p.get("asset_number", "")), R.dept_label_html(p["dept_key"]), R.esc(p["location"]),
             R.esc(p.get("estimated_cost_range", "")), p.get("downtime_hours", 0),
             R.badge(p.get("repair_status", ""), "muted")] for p in items]
    table = R.table(["Date", "Type", "Equipment", "Asset", "Department", "Location", "Cost range", "Downtime", "Repair"], rows,
                    "No damage cases in this range.")
    charts = '<div class="grid cols-2">%s%s</div>' % (
        R.section("Damage by location", R.bar_chart(by_loc.most_common(8))),
        R.section("Most-involved equipment", R.bar_chart(equip.most_common(8))))
    pro = R.section("Advanced damage analytics", '<div class="grid cols-2">%s%s</div>' % (
        R.pro_card("Exact cost tracking", "Precise financial cost capture & analytics."),
        R.pro_card("Asset reliability trends", "Downtime and failure analytics per asset.")))
    return cards + R.section("Report property / equipment damage", form) + charts + R.section("Damage cases", table) + pro


def body_summary(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month") or None
    dept = _scoped_dept(user, q1(qs, "dept") or None)
    mo_opts = '<option value="">Last 90 days</option>' + "".join(
        '<option value="%d"%s>%s</option>' % (m, " selected" if m == mo else "", D.month_name(m)) for m in range(1, 13))
    controls = """<form class="filter-bar" method="get">
      <div class="field"><label>Month</label><select name="month">%s</select></div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:80px"></div>
      <div class="field"><label>Department (cause filter)</label><select name="dept">%s</select></div>
      <button class="btn">Apply</button>
      <a class="btn ghost" href="/summary.csv?%s">Export CSV</a></form>""" % (
        mo_opts, yr, _dept_opts_for_user(user, dept), urlencode({"year": yr, "month": mo or "", "dept": dept or ""}))

    ds = D.dept_summary(year=yr, month=mo, free=True)
    if dept:
        ds = [r for r in ds if r["dept_key"] == dept]
    cap_d = D.FREE_LIMITS["departments"]
    ds_shown, ds_hidden = ds[:cap_d], max(0, len(ds) - cap_d)
    drows = [[R.dept_label_html(r["dept_key"]), r["total"], r["incidents"], r["hids"], r["near_misses"],
              r["high_potential"], r["open_actions"], r["overdue_actions"], r["points"]] for r in ds_shown]
    dept_tbl = R.table(["Department", "Total", "Incidents", "HIDs", "Near Miss", "High-Pot.", "Open", "Overdue", "Points"], drows)
    dept_banner = R.limit_banner("Showing %d departments in this view; %d additional departments remain available." % (cap_d, ds_hidden)) if ds_hidden else ""

    cs = D.contractor_summary(year=yr, month=mo, free=True)
    if dept:
        dept_companies = {u.get("company_id") for u in D.DB["users"]
                          if u.get("dept_key") == dept and u.get("company_id")}
        cs = [r for r in cs if r.get("company_id") in dept_companies]
    cap_c = D.FREE_LIMITS["contractors"]
    cs_shown, cs_hidden = cs[:cap_c], max(0, len(cs) - cap_c)
    crows = [[R.esc(r["name"]), r["incidents"], r["hids"], r["near_misses"], r["high_potential"],
              r["damage"], r["open_actions"], r["overdue_actions"]] for r in cs_shown]
    con_tbl = R.table(["Contractor", "Incidents", "HIDs", "Near Miss", "High-Pot.", "Damage", "Open", "Overdue"], crows)
    con_banner = R.limit_banner("Showing %d contractors in this view; %d additional contractors remain available." % (cap_c, cs_hidden)) if cs_hidden else ""

    cc = D.cause_category_counts(year=yr, month=mo, dept=dept, free=True)
    cc_hi = D.cause_category_counts(year=yr, month=mo, dept=dept, free=True, high_only=True)
    causes = '<div class="grid cols-2">%s%s</div>' % (
        R.section("Top 5 cause categories", R.bar_chart(cc.most_common(5))),
        R.section("Cause categories — high-potential events", R.bar_chart(cc_hi.most_common(5))))
    pro = R.section("Advanced summary tools", '<div class="grid cols-3">%s%s%s</div>' % (
        R.pro_card("Contractor scorecards", "Monthly & quarterly contractor ranking."),
        R.pro_card("Frequency rates", "LTIFR / TRIFR with man-hours integration."),
        R.pro_card("Unlimited departments", "Full department and contractor views are included.")))
    return (controls + R.section("Department safety summary", dept_banner + dept_tbl)
            + R.section("Contractor safety summary", con_banner + con_tbl) + causes + pro)


def body_quality(user, qs):
    dq = D.data_quality(free=True)
    cards = '<div class="grid cols-4">%s%s%s%s</div>' % (
        R.stat_card("Data completeness", "%d%%" % dq["completeness"]),
        R.stat_card("Records missing info", dq["missing"]),
        R.stat_card("Classification warnings", dq["warnings"]),
        R.stat_card("Awaiting correction", dq["awaiting"]))
    rows = [[R.esc(s["kind"]), "#%s" % s["id"], R.esc(s["issue"])] for s in dq["samples"]]
    table = R.table(["Record", "ID", "Issue"], rows, "No outstanding data-quality issues.")
    rules = R.section("Validation rules", """<div class="card"><ul style="margin:0;padding-left:18px;line-height:1.9">
      <li>Required fields (location, description) must be completed before submission.</li>
      <li>A Near Miss should not record a serious actual injury — reclassify as an Incident.</li>
      <li>A Lost Time Injury must include lost work days.</li>
      <li>A closed record must include a closure date.</li>
      <li>Controlled vocabularies prevent duplicate department, location, cause and category values.</li>
      <li>Supervisors / HSE reviewers may override a warning by entering a reason.</li>
    </ul></div>""")
    pro = R.section("Advanced data quality", '<div class="grid cols-2">%s%s</div>' % (
        R.pro_card("AI contradiction detection", "Detects contradictory classifications automatically."),
        R.pro_card("Bulk correction tools", "Mass-fix and audit data issues across history.")))
    intro = '<p class="hint">Automatic validation surfaces missing fields and classification warnings so records stay clean.</p>'
    return intro + cards + R.section("Records needing attention", table) + rules + pro


def body_pro(user, qs):
    limit_rows = [
        ["Companies", "Included", "1 company demo"],
        ["Sites", "Included", "1 site demo"],
        ["Locations", "Included", "No app cap"],
        ["Departments", "Included", "All official departments"],
        ["Contractors", "Included", "Full contractor register"],
        ["Employees", "Included", "700+ workforce supported"],
        ["SafePay Champions", "Included", "Department champions supported"],
        ["Records / month", "Included", "No monthly record block"],
        ["History", "Included", "Full available history"],
        ["Export", "Included", "CSV exports and generated workbook artifacts"],
    ]
    limits = R.section("Included access", R.table(["Capability", "Status", "Scope"], limit_rows))
    cards = "".join(R.pro_card(name) for name in D.PRO_FEATURES)
    included = R.section("Included advanced capabilities", '<div class="grid cols-3">%s</div>' % cards)
    cta = ('<div class="card" style="text-align:center"><h3 style="margin:0 0 6px">All Safety Pays Features Included</h3>'
           '<p class="hint">Advanced analytics, full-history views, investigation workflow prompts, '
           'frequency-rate planning, map placeholders and enterprise controls are available in this build.</p></div>')
    intro = ('<p class="hint">You are on the <strong>%s</strong> plan. Existing data is never deleted or blocked by plan limits. '
             '<span class="included-badge">%s</span> &middot; <strong>%s</strong>.</p>'
             % (D.PLAN, R.esc(D.AI_FREE_LABEL), R.esc(D.AI_PRO_LABEL)))
    return intro + cta + limits + included


def body_ai(user, qs):
    period = q1(qs, "period", "month")
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month", D.today().month)
    week = qint(qs, "week")
    dept = _scoped_dept(user, q1(qs, "dept") or None)
    loc = q1(qs, "location") or None
    contractor = q1(qs, "contractor") or None
    dept_contractors = None
    if dept:
        dept_contractors = {u.get("company_id") for u in D.DB["users"]
                            if u.get("dept_key") == dept and u.get("company_id")}
        if contractor and D.contractor_id_from_value(contractor) not in dept_contractors:
            contractor = None
    equipment = q1(qs, "equipment") or None
    activity = q1(qs, "activity") or None
    res = D.ai_predict(year=yr, month=mo, period=period, week=week, dept=dept, location=loc,
                       contractor=contractor, equipment=equipment, activity=activity, free=True)

    qsd = {"period": period, "year": yr, "month": mo, "week": week or "", "dept": dept or "",
           "location": loc or "", "contractor": contractor or "", "equipment": equipment or "", "activity": activity or ""}
    intro = ('<p class="hint"><span class="included-badge">%s</span> Predictions are generated from approved '
             'safety records using transparent, rule-based scoring.</p>' % R.esc(D.AI_FREE_LABEL))
    pills = '<div class="pill-row">%s%s</div>' % (
        '<a class="%s" href="?%s">Weekly</a>' % ("active" if period == "week" else "", urlencode(dict(qsd, period="week"))),
        '<a class="%s" href="?%s">Monthly</a>' % ("active" if period == "month" else "", urlencode(dict(qsd, period="month"))))
    controls = """<form class="filter-bar" method="get">
      <input type="hidden" name="period" value="%s">
      <div class="field"><label>Month</label>%s</div>
      <div class="field"><label>Year</label><input name="year" value="%d" style="width:80px"></div>
      <div class="field"><label>Week</label><select name="week">%s</select></div>
      <div class="field"><label>Department</label><select name="dept">%s</select></div>
      <div class="field"><label>Location</label><select name="location">%s</select></div>
      <div class="field"><label>Contractor</label><select name="contractor">%s</select></div>
      <div class="field"><label>Equipment</label><select name="equipment">%s</select></div>
      <div class="field"><label>Activity</label><select name="activity">%s</select></div>
      <button class="btn">Apply</button>
      <a class="btn ghost" href="/ai.csv?%s">Export CSV</a>
    </form>""" % (period, R.month_select("month", mo, onchange=False), yr,
                  _opts([(w, "Week %d" % w) for w in range(1, 6)], week, "Auto"),
                  _dept_opts_for_user(user, dept), _opts([(l, l) for l in D.location_options()], loc, "All locations"),
                  _opts([(c["id"], c["name"]) for c in D.DB["companies"]
                         if dept_contractors is None or c["id"] in dept_contractors], contractor, "All contractors"),
                  _opts([(e, e) for e in D.EQUIPMENT], equipment, "All equipment"),
                  _opts([(a, a) for a in D.ACTIVITIES], activity, "All activities"), urlencode(qsd))
    disclaimer = R.ai_disclaimer()

    if not res["ok"]:
        msg = ('<div class="empty">%s<div class="hint" style="margin-top:8px">Currently %d approved record(s) in range — '
               'at least %d are required, with 30+ days of activity and 3+ records per entity.</div></div>'
               % (R.esc(res["message"]), res["have"], res["need"]))
        return intro + pills + controls + disclaimer + msg

    overall, top, pl = res["overall"], res["top"], res["period_label"]

    def syn(name, score, factors, rec, conf="Medium"):
        score = min(100, score)
        return {"entity_name": name, "risk_score": score, "risk_level": D.ai_risk_level(score),
                "contributing_factors": factors, "recommended_action": rec,
                "prediction_period": pl, "confidence_label": conf}

    od = res["overdue_actions"]
    n_over = sum(1 for a in od if a["risk"] == "Overdue")
    n_at = len(od) - n_over
    overdue_pred = syn("Corrective Action Overdue Risk", n_over * 15 + n_at * 8,
                       "%d corrective action(s) overdue and %d due within 7 days." % (n_over, n_at),
                       "Close overdue corrective actions and re-baseline upcoming due dates.")
    hp = D.high_potential_events(year=yr, month=(None if period == "week" else mo), dept=dept, location=loc, free=True)
    hipo_pred = syn("High-Potential Event Alert", len(hp) * 10,
                    "%d high-potential event(s) recorded in %s." % (len(hp), pl),
                    "Escalate and investigate high-potential events; verify critical controls.", "High")
    rec_pred = {"entity_name": top["entity_name"], "risk_score": top["risk_score"], "risk_level": top["risk_level"],
                "contributing_factors": top["contributing_factors"], "recommended_action": top["recommended_action"],
                "prediction_period": pl, "confidence_label": top["confidence_label"]}

    first = lambda lst: lst[0] if lst else None

    def card(title, pred):
        if not pred:
            return ('<div class="card"><div class="ai-head"><h3>%s</h3>%s</div>'
                    '<div class="hint" style="margin-top:8px">Not enough data for this prediction yet.</div></div>'
                    % (R.esc(title), R.badge("No data", "muted")))
        return R.ai_pred_card(pred, title=title)

    key_cards = '<div class="grid cols-2">%s</div>' % "".join([
        card("Highest Risk Location", first(res["locations"])),
        card("Highest Risk Department", first(res["departments"])),
        card("Highest Risk Activity", first(res["activities"])),
        card("Equipment Requiring Attention", first(res["equipment"])),
        card("Contractor Risk Alert", first(res["contractors"])),
        card("Corrective Action Overdue Risk", overdue_pred),
        card("Repeat Hazard Alert", first(res["repeat_hazards"])),
        card("High-Potential Event Alert", hipo_pred),
        card("Predicted Risk — %s" % pl, overall),
        card("Recommended Immediate Action", rec_pred)])
    key = R.section("AI prediction cards · %s" % pl, key_cards)

    def ptable(preds, label, cap):
        rows = [["#%d" % i, R.esc(p["entity_name"]), "<strong>%d</strong>" % p["risk_score"],
                 R.ai_level_badge(p["risk_level"]), R.esc(p["recommended_action"])]
                for i, p in enumerate(preds[:cap], 1)]
        return R.table(["#", label, "Score", "Risk", "Recommended action"], rows, "Not enough data for a prediction.")

    locs_tbl = R.section("Top predicted risk locations", ptable(res["locations"], "Location", D.FREE_LIMITS["locations"]))
    acts_tbl = R.section("Top risky activities", ptable(res["activities"], "Activity", 5))
    equip_tbl = R.section("Equipment requiring attention", ptable(res["equipment"], "Equipment", 5))
    dept_inc = [p for p in res["departments"] if p["stats"]["trend"]] or res["departments"]
    dept_tbl = R.section("Departments with increasing risk", ptable(dept_inc, "Department", D.FREE_LIMITS["departments"]))

    rep_rows = [[R.esc(p["entity_name"]), "<strong>%d</strong>" % p["risk_score"], R.ai_level_badge(p["risk_level"]),
                 R.esc(p["contributing_factors"])] for p in res["repeat_hazards"]]
    rep_tbl = R.section("Repeat hazards", R.table(["Entity", "Score", "Risk", "Why"], rep_rows, "No repeat hazards detected."))

    od_rows = [[D.fmt_date(a["due"]), R.dept_label_html(a["dept_key"]), R.esc(a.get("location", "")),
                R.esc(a["description"]), R.badge(a["risk"], "bad" if a["risk"] == "Overdue" else "hot")] for a in od[:15]]
    od_tbl = R.section("Corrective actions likely to lapse",
                       R.table(["Due", "Department", "Location", "Action", "Risk"], od_rows, "No overdue or at-risk actions."))

    recs = []
    for p in [overall] + res["locations"][:3] + res["equipment"][:3] + res["activities"][:3] + res["contractors"][:2] + [overdue_pred, hipo_pred]:
        if p and p["recommended_action"] not in recs:
            recs.append(p["recommended_action"])
    interventions = R.section("Recommended interventions",
        '<div class="card"><ul style="margin:0;padding-left:18px;line-height:1.9">%s</ul></div>'
        % "".join("<li>%s</li>" % R.esc(x) for x in recs[:10]))

    expl = [overall["contributing_factors"]] + [p["contributing_factors"] for p in res["locations"][:3]]
    panel = R.section("Prediction explanation panel",
        '<div class="card"><ul style="margin:0;padding-left:18px;line-height:1.7">%s</ul></div>'
        % "".join("<li>%s</li>" % R.esc(x) for x in expl))

    pro = R.section(D.AI_PRO_LABEL, '<div class="grid cols-3">%s</div>'
                    % "".join(R.pro_card(f) for f in D.AI_PRO_FEATURES))

    return (intro + pills + controls + disclaimer + key + locs_tbl + acts_tbl + equip_tbl
            + dept_tbl + rep_tbl + od_tbl + interventions + panel + pro)


def post_damage(user, form):
    if D.at_record_limit():
        return redirect("/damage", _limit_msg())
    p = {"id": D.next_id("property_damage"), "ts": D.now_iso(), "reporter_id": user["id"],
         "dept_key": q1(form, "dept_key", user["dept_key"]), "location": q1(form, "location", ""),
         "damage_type": q1(form, "damage_type", "Other"), "equipment_involved": q1(form, "equipment_involved", ""),
         "asset_number": q1(form, "asset_number", ""), "estimated_cost_range": q1(form, "estimated_cost_range", ""),
         "operational_impact": q1(form, "operational_impact", ""), "repair_status": q1(form, "repair_status", "Reported"),
         "description": q1(form, "description", ""), "status": "open"}
    try:
        p["downtime_hours"] = int(q1(form, "downtime_hours") or 0)
    except ValueError:
        p["downtime_hours"] = 0
    warnings = _hse_from_form(p, form, "damage")
    D.DB["property_damage"].append(p)
    D.save()
    msg = "Property / equipment damage logged."
    if warnings and not p.get("dq_override"):
        msg += " Data-quality warning(s): " + "; ".join(warnings)
    return redirect("/damage", msg)


def post_hotspots(user, form):
    if not D.has_role(user, "hse_manager"):
        return redirect("/hotspots", "Only the HSE Manager can adjust thresholds.")
    if q1(form, "action") == "thresholds":
        try:
            th = {"watch": int(q1(form, "watch") or 3), "high": int(q1(form, "high") or 6),
                  "critical": int(q1(form, "critical") or 10)}
            D.DB.setdefault("settings", {})["hotspot_thresholds"] = th
            D.save()
            return redirect("/hotspots", "Hotspot thresholds updated.")
        except ValueError:
            return redirect("/hotspots", "Thresholds must be numbers.")
    return redirect("/hotspots")


# --------------------------------------------------------------------------
# POST handlers
# --------------------------------------------------------------------------


def _hse_from_form(rec, form, kind):
    """Populate risk/consequence/cause fields on a new report, derive the
    high-potential flag, and return any data-quality warnings."""
    ac = q1(form, "actual_consequence", "") or ""
    pc = q1(form, "potential_consequence", "") or ""
    rec["risk_level"] = q1(form, "risk_level", "") or ""
    rec["actual_consequence"] = ac
    rec["potential_consequence"] = pc
    rec["actual_severity"] = D.severity_of(ac)
    rec["potential_severity"] = D.severity_of(pc)
    rec["actual_risk_rating"] = D.risk_from_severity(D.severity_of(ac)) if ac else ""
    rec["potential_risk_rating"] = D.risk_from_severity(D.severity_of(pc)) if pc else ""
    rec["sub_location"] = q1(form, "sub_location", "") or ""
    if kind in ("hid", "near_miss", "incident"):
        rec["cause_category"] = q1(form, "cause_category", "") or ""
    if kind == "incident":
        try:
            rec["lost_days"] = int(q1(form, "lost_days") or 0)
        except ValueError:
            rec["lost_days"] = 0
    rec["is_high_potential"] = D.record_is_high_potential(rec)
    rec["high_potential_reason"] = ("Potential %s consequence." % pc) if (rec["is_high_potential"] and pc) else ""
    rec["reviewed_by"] = rec["reviewed_by"] if rec.get("reviewed_by") else None
    rec["review_date"] = rec.get("review_date")
    check = dict(rec)
    check["lti"] = q1(form, "lti")
    warnings = D.validate_record(kind, check)
    override = (q1(form, "override_reason") or "").strip()
    rec["dq_warnings"] = warnings
    if override:
        rec["dq_override"] = True
        rec["dq_override_reason"] = override
    return warnings


def _limit_msg():
    return "All access is enabled; record limits are not enforced."


def post_notifications(user, form):
    action = q1(form, "action")
    if action == "read_all":
        for n in D.DB.get("notifications", []):
            if n.get("user_id") == user["id"]:
                n["read"] = True
        D.save()
        return redirect("/notifications", "All notifications marked read.")
    if action == "read":
        note = next((n for n in D.DB.get("notifications", [])
                     if n["id"] == qint(form, "id") and n.get("user_id") == user["id"]), None)
        if note:
            note["read"] = True
            D.save()
        return redirect("/notifications")
    return redirect("/notifications")


def post_point_adjustments(user, form):
    action = q1(form, "action")
    if action == "request":
        if not D.has_perm(user, "points.adjust_request"):
            return redirect("/points/adjustments", ACCESS_DENIED)
        target = D.user(qint(form, "user_id"))
        if not target or not D.has_role(target, "worker"):
            return redirect("/points/adjustments", "Employee not found.")
        if not D.can_access_department(user, target.get("dept_key")):
            return redirect("/points/adjustments", ACCESS_DENIED)
        points = qint(form, "points", 0)
        if points == 0:
            return redirect("/points/adjustments", "Point adjustment cannot be zero.")
        reason = (q1(form, "reason") or "").strip()
        supporting_reference = (q1(form, "supporting_reference") or "").strip()
        if not reason or not supporting_reference:
            return redirect("/points/adjustments", "Reason and supporting reference are required.")
        aid = D.next_id("point_adjustment_requests")
        req = {
            "id": aid, "ts": D.now_iso(), "requested_by": user["id"],
            "user_id": target["id"], "dept_key": target.get("dept_key"),
            "points": points, "reason": reason, "supporting_reference": supporting_reference,
            "status": "pending", "approved_by": None, "approved_ts": None,
            "decision_reason": None, "safety_point_id": None,
        }
        D.DB.setdefault("point_adjustment_requests", []).append(req)
        D.record_audit(user, "point_adjustment.request", "point_adjustment_requests", aid,
                       None, {"user_id": target["id"], "points": points})
        D.notify(target["id"], "Point adjustment requested",
                 "%+d points requested for review: %s" % (points, reason),
                 "/points", "points")
        D.notify_role("hse_manager", "Point adjustment awaiting approval",
                      "%s requested %+d points for %s." % (user["name"], points, target["name"]),
                      "/points/adjustments", "approval")
        D.save()
        return redirect("/points/adjustments", "Point adjustment submitted for HSE Manager approval.")

    req = next((a for a in D.DB.get("point_adjustment_requests", []) if a["id"] == qint(form, "id")), None)
    if not req:
        return redirect("/points/adjustments", "Adjustment request not found.")
    if not D.has_perm(user, "points.adjust_approve"):
        return redirect("/points/adjustments", ACCESS_DENIED)
    if req.get("requested_by") == user["id"]:
        return redirect("/points/adjustments", "Self-approval is not allowed.")
    if req.get("status") != "pending":
        return redirect("/points/adjustments", "That request has already been decided.")
    if action == "approve":
        target = D.user(req["user_id"])
        spid = D.next_id("safety_points")
        D.DB["safety_points"].append({
            "id": spid, "ts": D.now_iso(), "user_id": req["user_id"],
            "dept_key": req["dept_key"], "points": req["points"],
            "reason": "Manual adjustment: %s" % req["reason"],
            "source_type": "manual_adjustment", "source_id": req["id"],
        })
        req["status"] = "approved"
        req["approved_by"] = user["id"]
        req["approved_ts"] = D.now_iso()
        req["safety_point_id"] = spid
        D.record_audit(user, "point_adjustment.approve", "point_adjustment_requests", req["id"],
                       {"status": "pending"}, {"status": "approved", "safety_point_id": spid})
        D.notify(req["user_id"], "Point adjustment approved",
                 "%+d points were applied to your ledger." % req["points"],
                 "/points", "points")
        D.notify(req["requested_by"], "Point adjustment approved",
                 "Your request for %s was approved." % (target["name"] if target else "the employee"),
                 "/points/adjustments", "approval")
        D.save()
        return redirect("/points/adjustments", "Point adjustment approved and posted to the ledger.")
    if action == "reject":
        reason = (q1(form, "decision_reason") or "").strip() or "No reason provided."
        req["status"] = "rejected"
        req["approved_by"] = user["id"]
        req["approved_ts"] = D.now_iso()
        req["decision_reason"] = reason
        D.record_audit(user, "point_adjustment.reject", "point_adjustment_requests", req["id"],
                       {"status": "pending"}, {"status": "rejected", "reason": reason})
        D.notify(req["requested_by"], "Point adjustment rejected",
                 "Your manual point adjustment request was rejected: %s" % reason,
                 "/points/adjustments", "approval")
        D.save()
        return redirect("/points/adjustments", "Point adjustment rejected.")
    return redirect("/points/adjustments")


def post_observation(user, form):
    if D.at_record_limit():
        return redirect("/report/observation", _limit_msg())
    o = {"id": D.next_id("safety_observations"), "ts": D.now_iso(),
         "reporter_id": user["id"], "dept_key": q1(form, "dept_key", user["dept_key"]),
         "location": q1(form, "location", ""), "category": q1(form, "category", "Observation"),
         "description": q1(form, "description", ""), "status": "submitted"}
    warnings = _hse_from_form(o, form, "observation")
    D.DB["safety_observations"].append(o)
    D.save()
    msg = "Observation submitted for review."
    if warnings and not o.get("dq_override"):
        msg += " Data-quality warning(s): " + "; ".join(warnings)
    return redirect("/report/observation", msg)


def post_hid_request(user, form):
    if not D.has_perm(user, "hid_request.create"):
        return redirect("/hid/request", ACCESS_DENIED)
    champ = next((u for u in D.DB["users"]
                  if D.has_role(u, "champion") and u.get("dept_key") == user.get("dept_key")), None)
    rid = D.next_id("worker_hid_requests")
    D.DB["worker_hid_requests"].append({
        "id": rid,
        "request_id": rid,
        "employee_id": user["id"],
        "department_id": user["dept_key"],
        "champion_id": champ["id"] if champ else None,
        "location_id": q1(form, "location_id", ""),
        "hazard_summary": q1(form, "hazard_summary", ""),
        "hazard_description": q1(form, "hazard_description", ""),
        "photo_reference": q1(form, "photo_reference", ""),
        "reported_date": D.today().isoformat(),
        "urgency": q1(form, "urgency", "Medium"),
        "request_status": "Assigned to Champion" if champ else "Submitted",
        "converted_to_hid_id": None,
        "created_date": D.now_iso(),
    })
    D.record_audit(user, "hid_request.create", "worker_hid_requests", rid,
                   None, {"status": "Assigned to Champion" if champ else "Submitted"})
    if champ:
        D.notify(champ["id"], "New HID request assigned",
                 "%s submitted a HID request for champion review." % user["name"],
                 "/champion/hid-requests", "hid")
    D.notify(user["id"], "HID request submitted",
             "Your HID request has been sent to your Department Safety Champion.",
             "/hid/requests", "hid")
    D.save()
    return redirect("/hid/requests", "HID request submitted to your Department Safety Champion.")


def post_champion_hid_requests(user, form):
    if not D.has_perm(user, "hid.create_for_employee"):
        return redirect("/champion/hid-requests", ACCESS_DENIED)
    req = next((r for r in D.DB["worker_hid_requests"] if r["id"] == qint(form, "id")), None)
    if not req or req.get("department_id") != user.get("dept_key"):
        return redirect("/champion/hid-requests", "Request not found.")
    if req.get("converted_to_hid_id"):
        return redirect("/champion/hid-requests", "Request was already converted.")
    hid_id = D.next_id("near_miss_hazard_reports")
    h = {
        "id": hid_id,
        "ts": D.now_iso(),
        "reporter_id": user["id"],
        "dept_key": req["department_id"],
        "type": "Hazard",
        "severity": req.get("urgency") if req.get("urgency") in ("Low", "Medium", "High") else "High",
        "location": req.get("location_id") or "",
        "description": req.get("hazard_description") or req.get("hazard_summary") or "",
        "status": "submitted",
        "submitted_by_user_id": user["id"],
        "submitted_for_user_id": req["employee_id"],
        "submission_mode": "champion_converted",
        "champion_department_id": user.get("dept_key"),
        "employee_department_id": req["department_id"],
        "employee_confirmation_status": "pending",
        "supervisor_verification_status": "pending",
        "hse_approval_status": "pending",
        "source_request_id": req["id"],
    }
    D.DB["near_miss_hazard_reports"].append(h)
    req["request_status"] = "Submitted to HSE"
    req["converted_to_hid_id"] = hid_id
    req["champion_id"] = user["id"]
    D.record_audit(user, "hid.convert", "near_miss_hazard_reports", hid_id,
                   {"worker_hid_request": req["id"]}, {"status": "submitted"})
    D.notify(req["employee_id"], "HID request converted",
             "Your Department Safety Champion converted your request to an official HID.",
             "/hid/requests", "hid")
    D.notify_role("supervisor", "HID awaiting verification",
                  "%s submitted an official HID for verification." % user["name"],
                  "/review", "hid", dept_key=user.get("dept_key"))
    D.save()
    return redirect("/champion/hid-requests", "Employee HID request converted to an official HID.")


def post_hid(user, form):
    if not has_any_perm(user, "hid.create_for_employee", "hse.module"):
        return redirect("/report/hid", ACCESS_DENIED)
    if D.at_record_limit():
        return redirect("/report/hid", _limit_msg())
    submitted_for = qint(form, "submitted_for_user_id", user["id"])
    submitted_for_user = D.user(submitted_for)
    if D.has_perm(user, "hid.create_for_employee"):
        if not submitted_for_user or submitted_for_user.get("dept_key") != user.get("dept_key"):
            return redirect("/report/hid", "Champions can create HIDs only for employees in their department.")
    h = {"id": D.next_id("near_miss_hazard_reports"), "ts": D.now_iso(),
         "reporter_id": user["id"], "dept_key": q1(form, "dept_key", user["dept_key"]),
         "type": q1(form, "type", "Hazard"), "severity": q1(form, "severity", "Low"),
         "location": q1(form, "location", ""), "description": q1(form, "description", ""),
         "status": "submitted", "submitted_by_user_id": user["id"],
         "submitted_for_user_id": submitted_for, "submission_mode": "champion_created" if D.has_perm(user, "hid.create_for_employee") else "hse_created",
         "champion_department_id": user.get("dept_key") if D.has_perm(user, "hid.create_for_employee") else None,
         "employee_department_id": submitted_for_user.get("dept_key") if submitted_for_user else q1(form, "dept_key", user["dept_key"]),
         "employee_confirmation_status": "pending", "supervisor_verification_status": "pending",
         "hse_approval_status": "pending", "source_request_id": qint(form, "request_id")}
    kind = "hid" if h["type"] == "Hazard" else "near_miss"
    warnings = _hse_from_form(h, form, kind)
    D.DB["near_miss_hazard_reports"].append(h)
    req = next((r for r in D.DB["worker_hid_requests"] if r["id"] == h.get("source_request_id")), None)
    if req:
        req["request_status"] = "Submitted to HSE"
        req["converted_to_hid_id"] = h["id"]
    D.record_audit(user, "hid.create", "near_miss_hazard_reports", h["id"],
                   None, {"submitted_for_user_id": submitted_for, "status": "submitted"})
    D.notify(submitted_for, "Official HID submitted",
             "An official HID was submitted for you and is awaiting verification.",
             "/hid/requests", "hid")
    D.notify_role("supervisor", "HID awaiting verification",
                  "An official HID is awaiting supervisor verification.",
                  "/review", "hid", dept_key=h["dept_key"])
    D.save()
    msg = "Official HID submitted for supervisor verification."
    if warnings and not h.get("dq_override"):
        msg += " Data-quality warning(s): " + "; ".join(warnings)
    return redirect("/report/hid", msg)


def post_incident(user, form):
    if D.at_record_limit():
        return redirect("/report/incident", _limit_msg())
    is_lti = q1(form, "lti") == "1"
    inc = {"id": D.next_id("incidents"), "ts": D.now_iso(), "reporter_id": user["id"],
           "dept_key": q1(form, "dept_key", user["dept_key"]),
           "severity": q1(form, "severity", "Minor"), "lti": is_lti,
           "location": q1(form, "location", ""), "description": q1(form, "description", ""),
           "status": "under_review", "lti_reset_applied": is_lti}
    warnings = _hse_from_form(inc, form, "incident")
    D.DB["incidents"].append(inc)
    _award(user["id"], inc["dept_key"], "incident", "incidents", inc["id"])
    msg = "Incident reported. +%d points." % D.POINTS["incident"]
    if is_lti:
        D._apply_lti_reset(D.DB, inc["dept_key"], inc["ts"], inc["id"], user["id"])
        msg += " Lost Time Injury logged — department monthly points reset."
    if warnings and not inc.get("dq_override"):
        msg += " Data-quality warning(s): " + "; ".join(warnings)
    D.save()
    return redirect("/report/incident", msg)


def _award(user_id, dept_key, kind, src_type, src_id):
    D.DB["safety_points"].append({
        "id": D.next_id("safety_points"), "ts": D.now_iso(), "user_id": user_id,
        "dept_key": dept_key, "points": D.POINTS[kind],
        "reason": kind.replace("_", " ").title(), "source_type": src_type, "source_id": src_id})


def post_review(user, form):
    rtype = q1(form, "rtype", "observation")
    action = q1(form, "action")

    if rtype == "hid":
        h = next((x for x in D.DB["near_miss_hazard_reports"] if x["id"] == qint(form, "id")), None)
        if not h:
            return redirect("/review", "HID not found.")
        if not D.can_access_department(user, h.get("dept_key")):
            return redirect("/review", ACCESS_DENIED)
        if action == "verify":
            if not D.has_perm(user, "hid.verify"):
                return redirect("/review", ACCESS_DENIED)
            h["supervisor_verification_status"] = "verified"
            h["supervisor_verified_by"] = user["id"]
            h["supervisor_verified_ts"] = D.now_iso()
            h["status"] = "verified"
            D.record_audit(user, "hid.verify", "near_miss_hazard_reports", h["id"],
                           {"status": "submitted"}, {"status": "verified"})
            D.notify_role("hse_manager", "HID awaiting HSE approval",
                          "A supervisor verified an HID for final HSE decision.",
                          "/review", "hid")
            D.save()
            return redirect("/review", "HID verified and sent to HSE.")
        if action == "approve":
            if not D.has_perm(user, "hid.approve"):
                return redirect("/review", ACCESS_DENIED)
            h["hse_approval_status"] = "approved"
            h["hse_approved_by"] = user["id"]
            h["hse_approved_ts"] = D.now_iso()
            h["status"] = "approved"
            target_user_id = h.get("submitted_for_user_id") or h.get("reporter_id")
            if not any(p for p in D.DB["safety_points"]
                       if p.get("source_type") == "near_miss_hazard_reports" and p.get("source_id") == h["id"]):
                _award(target_user_id, h["dept_key"], "hid", "near_miss_hazard_reports", h["id"])
            req = next((r for r in D.DB["worker_hid_requests"] if r.get("converted_to_hid_id") == h["id"]), None)
            if req:
                req["request_status"] = "Approved"
            D.record_audit(user, "hid.approve", "near_miss_hazard_reports", h["id"],
                           {"hse_approval_status": "pending"}, {"hse_approval_status": "approved"})
            D.record_audit(user, "point_award", "safety_points", h["id"],
                           None, {"user_id": target_user_id, "points": D.POINTS["hid"]})
            D.notify(target_user_id, "HID approved",
                     "%d safety points were awarded automatically." % D.POINTS["hid"],
                     "/points", "points")
            D.save()
            return redirect("/review", "HID approved. %d points awarded automatically." % D.POINTS["hid"])
        if action == "reject":
            if not D.has_perm(user, "hid.reject"):
                return redirect("/review", ACCESS_DENIED)
            h["hse_approval_status"] = "rejected"
            h["hse_rejected_by"] = user["id"]
            h["hse_rejected_ts"] = D.now_iso()
            h["hse_rejection_reason"] = q1(form, "reason") or "No reason provided."
            h["status"] = "rejected"
            req = next((r for r in D.DB["worker_hid_requests"] if r.get("converted_to_hid_id") == h["id"]), None)
            if req:
                req["request_status"] = "Rejected"
            D.record_audit(user, "hid.reject", "near_miss_hazard_reports", h["id"],
                           {"hse_approval_status": "pending"}, {"reason": h["hse_rejection_reason"]})
            D.notify(h.get("submitted_for_user_id") or h.get("reporter_id"), "HID rejected",
                     "HSE rejected the HID: %s" % h["hse_rejection_reason"],
                     "/hid/requests", "hid")
            D.save()
            return redirect("/review", "HID rejected. No points awarded.")
        if action == "violation":
            if not D.has_perm(user, "points.process_automatic"):
                return redirect("/review", ACCESS_DENIED)
            target_user_id = h.get("submitted_for_user_id") or h.get("reporter_id")
            D.DB["safety_points"].append({
                "id": D.next_id("safety_points"), "ts": D.now_iso(), "user_id": target_user_id,
                "dept_key": h["dept_key"], "points": -D.VIOLATION_PENALTY,
                "reason": "Violation confirmed by HSE", "source_type": "violation", "source_id": h["id"]})
            h["violation_recorded"] = True
            D.record_audit(user, "point_deduction", "safety_points", h["id"],
                           None, {"user_id": target_user_id, "points": -D.VIOLATION_PENALTY})
            D.notify(target_user_id, "Safety points deducted",
                     "%d points were deducted after HSE confirmed a violation." % D.VIOLATION_PENALTY,
                     "/points", "points")
            D.save()
            return redirect("/review", "Violation confirmed. %d points deducted." % D.VIOLATION_PENALTY)
        return redirect("/review", "No change.")

    if not D.has_perm(user, "points.process_automatic"):
        return redirect("/review", ACCESS_DENIED)
    o = next((x for x in D.DB["safety_observations"] if x["id"] == qint(form, "id")), None)
    if not o:
        return redirect("/review", "Observation not found.")
    if not D.can_access_department(user, o.get("dept_key")):
        return redirect("/review", ACCESS_DENIED)
    if action == "approve":
        o["status"] = "approved"
        _award(o["reporter_id"], o["dept_key"], "observation", "safety_observations", o["id"])
        D.record_audit(user, "observation.approve", "safety_observations", o["id"],
                       {"status": "submitted"}, {"status": "approved"})
        msg = "Approved. +%d points awarded." % D.POINTS["observation"]
    else:
        o["status"] = "rejected"
        D.record_audit(user, "observation.reject", "safety_observations", o["id"],
                       {"status": "submitted"}, {"status": "rejected"})
        msg = "Observation rejected."
    D.save()
    return redirect("/review", msg)


def post_actions(user, form):
    action = q1(form, "action")
    if action == "create":
        if not D.has_perm(user, "action.assign"):
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
            if a.get("owner_id") != user["id"] and not D.has_perm(user, "action.verify"):
                return redirect("/actions", "Not permitted.")
            a["status"] = "closed"
            a["closed_ts"] = D.now_iso()
            _award(a["owner_id"], a["dept_key"], "action_closed", "corrective_actions", a["id"])
            D.save()
            return redirect("/actions", "Action closed. +%d points." % D.POINTS["action_closed"])
    return redirect("/actions")


def post_reward_request(user, form):
    if not D.has_perm(user, "reward.request"):
        return redirect("/rewards", "Only authorised workers request rewards.")
    rw = D.reward(qint(form, "reward_id"))
    if not rw:
        return redirect("/rewards", "Reward not found.")
    if not rw.get("active", True):
        return redirect("/rewards", "Reward is not active.")
    if D.user_balance(user["id"]) < rw["point_cost"]:
        return redirect("/rewards", "Not enough available points for that reward.")
    conflict = next((r for r in D.DB["reward_requests"]
                     if r["user_id"] == user["id"] and r["reward_id"] == rw["id"]
                     and r["status"] in D.RESERVED_REWARD_STATUSES), None)
    if conflict:
        return redirect("/rewards", "You already have a pending request for this reward.")
    ok, reason = D.reward_budget_validation(user["dept_key"], rw["cash_value"])
    if not ok:
        return redirect("/rewards", reason)
    auto = rw.get("release_mode") == "automatic"
    now = D.now_iso()
    request = {
        "id": D.next_id("reward_requests"), "ts": now, "user_id": user["id"],
        "dept_key": user["dept_key"], "reward_id": rw["id"], "point_cost": rw["point_cost"],
        "cash_value": rw["cash_value"], "status": "released" if auto else "pending_finance",
        "system_validation_status": "validated",
        "admin_id": None, "admin_ts": None, "finance_id": None, "finance_ts": None,
        "released_by": user["id"] if auto else None, "released_ts": now if auto else None,
        "reject_reason": None, "rejected_by": None, "reject_stage": None, "rejected_ts": None,
        "release_reference": "AUTO" if auto else None, "auto_release": auto,
    }
    D.DB["reward_requests"].append(request)
    D.record_audit(user, "reward.request", "reward_requests", request["id"],
                   None, {"status": request["status"], "reserved_points": 0 if auto else rw["point_cost"]})
    D.notify(user["id"], "Reward request submitted" if not auto else "Reward released",
             "Your reward request is %s." % ("awaiting Finance approval" if not auto else "released automatically"),
             "/rewards", "reward")
    if not auto:
        D.notify_role("finance_manager", "Reward awaiting Finance approval",
                      "%s requested %s." % (user["name"], rw["name"]),
                      "/rewards/releases", "reward")
    D.save()
    if auto:
        return redirect("/rewards", "Reward released automatically and points deducted.")
    return redirect("/rewards", "Reward requested. Points reserved and sent to Finance.")


def _reject(r, user, stage, form):
    r["status"] = "finance_rejected" if stage == "finance" else "rejected"
    r["rejected_by"] = user["id"]
    r["reject_stage"] = stage
    r["reject_reason"] = q1(form, "reason") or "No reason provided."
    r["rejected_ts"] = D.now_iso()


def post_reward_approval(user, form):
    return redirect("/rewards", "Reward Administrator approval has been removed. Requests go directly to Finance.")


def post_reward_finance(user, form):
    if not has_any_perm(user, "reward.finance_approve", "reward.finance_reject", "reward.release"):
        return redirect("/rewards/releases", "Not permitted.")
    r = next((x for x in D.DB["reward_requests"] if x["id"] == qint(form, "id")), None)
    if not r:
        return redirect("/rewards/releases", "Request not found.")
    action = q1(form, "action")
    if action == "fin_approve" and r["status"] in ("pending_finance", "budget_hold", "deferred_next_month", "deferred_next_quarter"):
        if not D.has_perm(user, "reward.finance_approve"):
            return redirect("/rewards/releases", "Not permitted.")
        r["status"] = "finance_approved"
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        msg = "Finance approved. Ready for release."
    elif action == "reject" and r["status"] in D.RESERVED_REWARD_STATUSES:
        if not D.has_perm(user, "reward.finance_reject"):
            return redirect("/rewards/releases", "Not permitted.")
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        _reject(r, user, "finance", form)
        msg = "Request rejected by Finance. Reserved points restored."
    elif action == "hold" and r["status"] == "pending_finance":
        if not D.has_perm(user, "reward.budget_hold"):
            return redirect("/rewards/releases", "Not permitted.")
        r["status"] = "budget_hold"
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        msg = "Request placed on budget hold."
    elif action == "defer_month" and r["status"] == "pending_finance":
        if not D.has_perm(user, "reward.defer"):
            return redirect("/rewards/releases", "Not permitted.")
        r["status"] = "deferred_next_month"
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        msg = "Request deferred to next month."
    elif action == "defer_quarter" and r["status"] == "pending_finance":
        if not D.has_perm(user, "reward.defer"):
            return redirect("/rewards/releases", "Not permitted.")
        r["status"] = "deferred_next_quarter"
        r["finance_id"] = user["id"]
        r["finance_ts"] = D.now_iso()
        msg = "Request deferred to next quarter."
    elif action == "release" and r["status"] in ("finance_approved", "budget_hold", "deferred_next_month", "deferred_next_quarter"):
        if not D.has_perm(user, "reward.release"):
            return redirect("/rewards/releases", "Not permitted.")
        r["status"] = "released"
        r["released_by"] = user["id"]
        r["released_ts"] = D.now_iso()
        r["release_reference"] = q1(form, "release_reference") or r.get("release_reference")
        msg = "Reward released. %s charged to the budget." % D.fmt_money(r["cash_value"])
    else:
        msg = "No change."
    D.record_audit(user, "reward.%s" % action, "reward_requests", r["id"],
                   None, {"status": r["status"]})
    D.save()
    return redirect("/rewards/releases", msg)


def post_budgets(user, form):
    if not D.can_edit_budget(user):
        return redirect("/budgets", "You do not have permission to edit budgets.")
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


def _champion_count(dept_key):
    return sum(1 for u in D.DB["users"] if D.has_role(u, "champion") and u.get("dept_key") == dept_key)


CHAMPIONS_PER_DEPT = 5


def _set_status_fields(u, status):
    status = "Inactive" if status == "Inactive" else "Active"
    u["status"] = status
    u["active"] = status == "Active"


def _apply_employee_profile(u, name, employment_type, dept_key, contractor_id, job_title,
                            employee_id=None, supervisor_id=None, safety_champion_id=None,
                            shift="", site="", email="", phone="", status="Active"):
    is_contractor = employment_type == "Contractor"
    u["employee_id"] = employee_id or u.get("employee_id")
    u["full_name"] = name
    u["name"] = name
    u["employment_type"] = employment_type
    u["department_id"] = dept_key
    u["dept_key"] = dept_key
    u["contractor_id"] = contractor_id if is_contractor else None
    u["company_id"] = contractor_id if is_contractor else None
    u["job_title"] = job_title
    u["title"] = job_title
    u["supervisor_id"] = supervisor_id
    u["safety_champion_id"] = safety_champion_id
    u["shift"] = shift or u.get("shift") or "Day"
    u["site"] = site or u.get("site") or "Asanko Gold Mine"
    u["email"] = email or u.get("email") or ""
    u["phone"] = phone or u.get("phone") or ""
    u["is_contractor"] = is_contractor
    _set_status_fields(u, status)


def _employee_import_payload(row):
    employee_id = _employee_field(row, "employee_id", "employee id", "id")
    name = _employee_field(row, "full_name", "full name", "name", "employee_name", "employee name")
    employment_type = (_employee_field(row, "employment_type", "employment type", "type") or "Internal").title()
    if employment_type not in ("Internal", "Contractor"):
        employment_type = "Contractor" if "contract" in employment_type.lower() else "Internal"
    dept_value = _employee_field(row, "department_id", "department", "department_key", "dept_key", "dept")
    dept_key = D.department_key_from_value(dept_value, default="")
    contractor_value = _employee_field(row, "contractor_id", "contractor", "contractor_company", "contractor_name", "company")
    contractor_id = D.contractor_id_from_value(contractor_value)
    job_title = _employee_field(row, "job_title", "job title", "title") or "Worker"
    supervisor = _employee_by_token(_employee_field(row, "supervisor_id", "supervisor", "supervisor employee id"))
    champion = _employee_by_token(_employee_field(row, "safety_champion_id", "safety champion", "champion"))
    return {
        "employee_id": employee_id,
        "name": name,
        "employment_type": employment_type,
        "dept_key": dept_key,
        "contractor_id": contractor_id,
        "job_title": job_title,
        "supervisor_id": supervisor["id"] if supervisor else None,
        "safety_champion_id": champion["id"] if champion else None,
        "shift": _employee_field(row, "shift"),
        "site": _employee_field(row, "site") or "Asanko Gold Mine",
        "email": _employee_field(row, "email"),
        "phone": _employee_field(row, "phone"),
        "status": _employee_field(row, "status") or "Active",
        "line": row.get("_line", "?"),
    }


def post_admin(user, form):
    if not D.has_perm(user, "user.manage"):
        return redirect("/admin", ACCESS_DENIED)
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

    if action == "import_employees":
        rows = _employee_csv_rows(q1(form, "csv_text") or "")
        if not rows:
            return redirect("/admin", "Paste CSV or Excel rows with a header first.")
        existing_ids = {str(u.get("employee_id", "")).strip().casefold()
                        for u in D.DB["users"] if u.get("employee_id")}
        file_ids = set()
        imported = 0
        skipped = []
        for row in rows:
            payload = _employee_import_payload(row)
            line = payload["line"]
            if not payload["name"]:
                skipped.append("line %s missing name" % line)
                continue
            if not payload["dept_key"]:
                skipped.append("line %s has an unknown department" % line)
                continue
            eid = (payload["employee_id"] or "").strip()
            if eid:
                low = eid.casefold()
                if low in existing_ids or low in file_ids:
                    skipped.append("line %s duplicate employee ID %s" % (line, eid))
                    continue
                file_ids.add(low)
            else:
                eid = D.next_employee_id("CTR" if payload["employment_type"] == "Contractor" else "EMP")
            if payload["employment_type"] == "Contractor" and not payload["contractor_id"]:
                skipped.append("line %s contractor employee needs a contractor company" % line)
                continue
            if payload["employment_type"] == "Internal":
                payload["contractor_id"] = None
            supervisor = D.user(payload["supervisor_id"])
            champion = D.user(payload["safety_champion_id"])
            if supervisor and supervisor.get("dept_key") != payload["dept_key"]:
                skipped.append("line %s supervisor is outside the department" % line)
                continue
            if champion and (not D.has_role(champion, "champion") or champion.get("dept_key") != payload["dept_key"]):
                skipped.append("line %s safety champion is outside the department" % line)
                continue
            uid = D.next_id("users")
            role = "worker"
            rec = {"id": uid, "role": role, "roles": [role], "is_champion": False}
            _apply_employee_profile(
                rec, payload["name"], payload["employment_type"], payload["dept_key"],
                payload["contractor_id"], payload["job_title"], employee_id=eid,
                supervisor_id=payload["supervisor_id"], safety_champion_id=payload["safety_champion_id"],
                shift=payload["shift"], site=payload["site"], email=payload["email"],
                phone=payload["phone"], status=payload["status"],
            )
            D.DB["users"].append(rec)
            existing_ids.add(eid.casefold())
            imported += 1
        D.ensure_schema(D.DB)
        D.record_audit(user, "employee.import", "users", None, None, {"imported": imported, "skipped": len(skipped)})
        D.save()
        msg = "Imported %d employee(s)." % imported
        if skipped:
            msg += " Skipped %d (%s)." % (len(skipped), "; ".join(skipped[:3]))
        return redirect("/admin", msg)

    if action == "bulk_assign_employees":
        targets = []
        missing = []
        for token in _parse_employee_tokens(q1(form, "employee_ids") or ""):
            emp = _employee_by_token(token)
            if emp:
                targets.append(emp)
            else:
                missing.append(token)
        if not targets:
            return redirect("/admin", "No matching employees found for bulk assignment.")
        new_dept = D.department_key_from_value(q1(form, "dept_key") or "", default="") or ""
        supervisor = D.user(qint(form, "supervisor_id"))
        champion = D.user(qint(form, "safety_champion_id"))
        new_status = q1(form, "status") or ""
        updated = 0
        skipped = []
        for emp in targets:
            dept_key = new_dept or emp.get("dept_key")
            if supervisor:
                if supervisor.get("dept_key") != dept_key:
                    skipped.append(D.employee_display_id(emp))
                    continue
            if champion:
                if not D.has_role(champion, "champion") or champion.get("dept_key") != dept_key:
                    skipped.append(D.employee_display_id(emp))
                    continue
            if new_dept:
                emp["dept_key"] = dept_key
                emp["department_id"] = dept_key
            if supervisor:
                emp["supervisor_id"] = supervisor["id"]
            if champion:
                emp["safety_champion_id"] = champion["id"]
            if new_status:
                _set_status_fields(emp, new_status)
            updated += 1
        D.ensure_schema(D.DB)
        D.record_audit(user, "employee.bulk_update", "users", None, None,
                       {"updated": updated, "missing": missing, "skipped": skipped})
        D.save()
        msg = "Updated %d employee(s)." % updated
        if missing or skipped:
            msg += " Review %d unmatched/mismatched ID(s)." % (len(missing) + len(skipped))
        return redirect("/admin", msg)

    # Role / champion management requires the role.manage permission.
    if action in ("create_user", "set_roles", "assign_champion", "remove_champion"):
        if not D.has_perm(user, "role.manage"):
            return redirect("/admin", ACCESS_DENIED)
        if action == "create_user":
            name = (q1(form, "name") or "").strip()
            if not name:
                return redirect("/admin", "A name is required.")
            role = q1(form, "role", "worker")
            if role not in D.ROLE_LABELS:
                role = "worker"
            employment_type = q1(form, "employment_type", "Internal")
            if employment_type not in ("Internal", "Contractor"):
                employment_type = "Internal"
            dept_key = D.department_key_from_value(q1(form, "dept_key") or "", default=D.DB["departments"][0]["key"])
            contractor_id = D.contractor_id_from_value(q1(form, "contractor_id") or "")
            if employment_type == "Contractor" and not contractor_id:
                return redirect("/admin", "Contractor employees must have a contractor company.")
            if employment_type == "Internal":
                contractor_id = None
            employee_id = (q1(form, "employee_id") or "").strip()
            if employee_id and D.employee_by_employee_id(employee_id):
                return redirect("/admin", "Duplicate employee ID: %s." % employee_id)
            if not employee_id:
                employee_id = D.next_employee_id("CTR" if employment_type == "Contractor" else "EMP")
            if role == "champion" and _champion_count(dept_key) >= CHAMPIONS_PER_DEPT:
                return redirect("/admin", "That department already has %d Department Safety Champions." % CHAMPIONS_PER_DEPT)
            uid = D.next_id("users")
            roles = ["worker", "champion"] if role == "champion" else [role]
            rec = {"id": uid, "role": role, "roles": roles, "is_champion": role == "champion"}
            _apply_employee_profile(
                rec, name, employment_type, dept_key, contractor_id,
                (q1(form, "job_title") or D.role_label(role)).strip(),
                employee_id=employee_id, phone=(q1(form, "phone") or "").strip(),
                status="Active",
            )
            D.DB["users"].append(rec)
            D.ensure_schema(D.DB)
            D.record_audit(user, "user.create", "users", uid, None, {"name": name, "roles": roles, "employee_id": employee_id})
            D.save()
            return redirect("/admin", "User created: %s (%s)." % (name, D.role_label(role)))

        target = D.user(qint(form, "user_id"))
        if not target:
            return redirect("/admin", "User not found.")
        if action == "set_roles":
            new_roles = [r for r in D.ROLE_ORDER if q1(form, "role_%s" % r) == "1"]
            if not new_roles:
                return redirect("/admin", "A user must keep at least one role.")
            if "champion" in new_roles and not D.has_role(target, "champion") and _champion_count(target.get("dept_key")) >= CHAMPIONS_PER_DEPT:
                return redirect("/admin", "That department already has %d Department Safety Champions." % CHAMPIONS_PER_DEPT)
            old = D.user_roles(target)
            target["roles"] = new_roles
            target["role"] = new_roles[0]
            target["is_champion"] = "champion" in new_roles
            D.record_audit(user, "role.update", "users", target["id"], {"roles": old}, {"roles": new_roles})
            D.save()
            return redirect("/admin", "Roles updated for %s." % target["name"])
        if action == "assign_champion":
            if _champion_count(target.get("dept_key")) >= CHAMPIONS_PER_DEPT:
                return redirect("/admin", "That department already has %d Department Safety Champions." % CHAMPIONS_PER_DEPT)
            if not D.has_role(target, "champion"):
                target["roles"] = D.user_roles(target) + ["champion"]
                target["is_champion"] = True
                D.record_audit(user, "champion.assign", "users", target["id"], None, {"dept": target.get("dept_key")})
                D.save()
            return redirect("/admin", "%s assigned as Department Safety Champion." % target["name"])
        if action == "remove_champion":
            target["roles"] = [r for r in D.user_roles(target) if r != "champion"] or ["worker"]
            target["role"] = target["roles"][0]
            target["is_champion"] = False
            D.record_audit(user, "champion.remove", "users", target["id"], None, {"dept": target.get("dept_key")})
            D.save()
            return redirect("/admin", "%s removed as Department Safety Champion." % target["name"])
    return redirect("/admin")


# --------------------------------------------------------------------------
# CSV exports (respect active filters)
# --------------------------------------------------------------------------


def csv_points(user, qs):
    dept = q1(qs, "dept")
    entries = list(D.DB["safety_points"])
    if dept:
        entries = [p for p in entries if p["dept_key"] == dept]
    if D.has_role(user, "worker") and not has_any_perm(user, "report.view_department", "report.view_company"):
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
    hipo = D.high_potential_events(year=yr, month=mo, free=False)
    dmg = D.damage_items(year=yr, month=mo, free=False)
    w.writerow(["High-Potential", "Total", len(hipo)])
    w.writerow(["High-Potential", "Low actual / high potential", len(D.low_actual_high_potential(year=yr, month=mo, free=False))])
    w.writerow(["Property/Equipment Damage", "Cases", len(dmg)])
    w.writerow(["Property/Equipment Damage", "Downtime hours", sum(p.get("downtime_hours", 0) for p in dmg)])
    for k, v in D.cause_category_counts(year=yr, month=mo, free=False).most_common():
        w.writerow(["Cause Category", k, v])
    reps_avp = D._norm_reports(year=yr, month=mo, free=False)
    a_dist = Counter(r["rec"].get("actual_consequence") or "—" for r in reps_avp)
    p_dist = Counter(r["rec"].get("potential_consequence") or "—" for r in reps_avp)
    for c in D.CONSEQUENCES:
        w.writerow(["Actual vs Potential", c, "actual=%d potential=%d" % (a_dist.get(c, 0), p_dist.get(c, 0))])

    w.writerow([])
    w.writerow(["Hotspot Rank", "Location", "TotalReports", "Incidents", "HIDs", "NearMisses", "HighestRisk", "Status"])
    for i, s in enumerate(D.location_hotspots(year=yr, month=mo, free=False)[:5], 1):
        w.writerow([i, s["location"], s["total"], s["incident"], s["hid"], s["near_miss"], s["highest_risk"], s["status"]])

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


def csv_hotspots(user, qs):
    f = dict(year=qint(qs, "year", D.today().year), month=qint(qs, "month") or None,
             dept=_scoped_dept(user, q1(qs, "dept") or None), location=q1(qs, "location") or None,
             report_type=q1(qs, "report_type") or None, risk_level=q1(qs, "risk_level") or None)
    rows = D.location_hotspots(free=True, **f)[:D.FREE_LIMITS["locations"]]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Rank", "Location", "TotalReports", "Incidents", "HIDs", "NearMisses",
                "OpenActions", "OverdueActions", "HighestRisk", "HotspotStatus"])
    for i, s in enumerate(rows, 1):
        w.writerow([i, s["location"], s["total"], s["incident"], s["hid"], s["near_miss"],
                    s["open_actions"], s["overdue_actions"], s["highest_risk"], s["status"]])
    return "hotspots.csv", out.getvalue()


def csv_summary(user, qs):
    yr = qint(qs, "year", D.today().year)
    mo = qint(qs, "month") or None
    dept = _scoped_dept(user, q1(qs, "dept") or None)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Section", "Name", "Total", "Incidents", "HIDs", "NearMisses", "HighPotential", "Open", "Overdue", "Extra"])
    dept_rows = D.dept_summary(year=yr, month=mo, free=True)
    if dept:
        dept_rows = [r for r in dept_rows if r["dept_key"] == dept]
    for r in dept_rows[:D.FREE_LIMITS["departments"]]:
        w.writerow(["Department", "%s - %s" % (r["adinkra_name"], r["department"]), r["total"], r["incidents"],
                    r["hids"], r["near_misses"], r["high_potential"], r["open_actions"], r["overdue_actions"], "points=%d" % r["points"]])
    contractor_rows = D.contractor_summary(year=yr, month=mo, free=True)
    if dept:
        dept_companies = {u.get("company_id") for u in D.DB["users"]
                          if u.get("dept_key") == dept and u.get("company_id")}
        contractor_rows = [r for r in contractor_rows if r.get("company_id") in dept_companies]
    for r in contractor_rows[:D.FREE_LIMITS["contractors"]]:
        w.writerow(["Contractor", r["name"], r["total"], r["incidents"], r["hids"], r["near_misses"],
                    r["high_potential"], r["open_actions"], r["overdue_actions"], "damage=%d" % r["damage"]])
    w.writerow([])
    w.writerow(["Cause Category", "Count"])
    for k, v in D.cause_category_counts(year=yr, month=mo, dept=dept, free=True).most_common():
        w.writerow([k, v])
    return "summary.csv", out.getvalue()


def csv_ai(user, qs):
    dept = _scoped_dept(user, q1(qs, "dept") or None)
    contractor = q1(qs, "contractor") or None
    if dept:
        dept_contractors = {u.get("company_id") for u in D.DB["users"]
                            if u.get("dept_key") == dept and u.get("company_id")}
        if contractor and D.contractor_id_from_value(contractor) not in dept_contractors:
            contractor = None
    res = D.ai_predict(year=qint(qs, "year", D.today().year), month=qint(qs, "month", D.today().month),
                       period=q1(qs, "period", "month"), week=qint(qs, "week"),
                       dept=dept, location=q1(qs, "location") or None,
                       contractor=contractor, equipment=q1(qs, "equipment") or None,
                       activity=q1(qs, "activity") or None, free=True)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["EntityType", "Entity", "RiskScore", "RiskLevel", "Confidence", "Recommended", "Period", "Factors"])
    if not res["ok"]:
        w.writerow(["info", res["message"], "", "", "", "", res["period_label"], ""])
        return "ai_predictions.csv", out.getvalue()
    rows = ([res["overall"]] + res["locations"] + res["departments"] + res["equipment"]
            + res["activities"] + res["contractors"] + res["causes"])
    for p in rows:
        w.writerow([p["entity_type"], p["entity_name"], p["risk_score"], p["risk_level"],
                    p["confidence_label"], p["recommended_action"], p["prediction_period"], p["contributing_factors"]])
    return "ai_predictions.csv", out.getvalue()


# --------------------------------------------------------------------------
# Routing tables
# --------------------------------------------------------------------------
GET_ROUTES = {
    "/": ("Dashboard", body_dashboard),
    "/notifications": ("Notifications", body_notifications),
    "/hid/request": ("Submit HID Request", body_hid_request_form),
    "/hid/requests": ("My HID Requests", body_my_hid_requests),
    "/champion": ("Champion Dashboard", body_champion_dashboard),
    "/champion/employees": ("Employee Search", body_champion_employee_search),
    "/champion/hid-requests": ("Pending Verification", body_champion_hid_requests),
    "/report/observation": ("Report Observation", body_observation_form),
    "/report/hid": ("Hazard / Near-miss", body_hid_form),
    "/report/incident": ("Report Incident", body_incident_form),
    "/review": ("Review Queue", body_review),
    "/actions": ("Corrective Actions", body_actions),
    "/points": ("Points Ledger", body_points),
    "/points/adjustments": ("Point Adjustments", body_point_adjustments),
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
    "/hotspots": ("Location Hotspots", body_hotspots),
    "/highpotential": ("High-Potential Events", body_highpotential),
    "/damage": ("Property / Equipment Damage", body_damage),
    "/summary": ("Dept & Contractor Summary", body_summary),
    "/quality": ("Data Quality", body_quality),
    "/pro": ("Included Features", body_pro),
    "/ai": ("AI Safety Insights", body_ai),
}
POST_ROUTES = {
    "/notifications": post_notifications,
    "/hid/request": post_hid_request,
    "/champion/hid-requests": post_champion_hid_requests,
    "/report/observation": post_observation,
    "/report/hid": post_hid,
    "/report/incident": post_incident,
    "/review": post_review,
    "/actions": post_actions,
    "/points/adjustments": post_point_adjustments,
    "/rewards": post_reward_request,
    "/rewards/approvals": post_reward_approval,
    "/rewards/releases": post_reward_finance,
    "/budgets": post_budgets,
    "/admin": post_admin,
    "/damage": post_damage,
    "/hotspots": post_hotspots,
}
CSV_ROUTES = {
    "/points.csv": csv_points,
    "/leaderboard.csv": csv_leaderboard,
    "/reports.csv": csv_reports,
    "/hotspots.csv": csv_hotspots,
    "/summary.csv": csv_summary,
    "/ai.csv": csv_ai,
}
# Route -> required permission predicate (user -> bool). Absent = any logged-in user.
ROUTE_GUARDS = {
    "/notifications": lambda u: D.has_perm(u, "notification.view_own"),
    "/hid/request": lambda u: D.has_perm(u, "hid_request.create"),
    "/hid/requests": lambda u: D.has_perm(u, "hid_request.view_own"),
    "/champion": lambda u: D.has_perm(u, "hid.create_for_employee"),
    "/champion/employees": lambda u: D.has_perm(u, "hid.create_for_employee"),
    "/champion/hid-requests": lambda u: D.has_perm(u, "hid.create_for_employee"),
    "/report/observation": lambda u: D.has_perm(u, "hse.module"),
    "/report/hid": lambda u: has_any_perm(u, "hid.create_for_employee", "hse.module"),
    "/report/incident": lambda u: D.has_perm(u, "incident.create"),
    "/damage": lambda u: D.has_perm(u, "incident.create"),
    "/review": lambda u: has_any_perm(u, "hid.verify", "hid.approve", "points.process_automatic"),
    "/actions": lambda u: has_any_perm(u, "action.assign", "action.update_assigned", "action.verify"),
    "/points": lambda u: has_any_perm(u, "reward.view_eligibility", "report.view_department", "report.view_company"),
    "/points/adjustments": lambda u: has_any_perm(u, "points.adjust_request", "points.adjust_approve"),
    "/rewards": lambda u: has_any_perm(u, "reward.view_eligibility", "reward.finance_approve"),
    "/rewards/approvals": lambda u: False,
    "/rewards/releases": lambda u: has_any_perm(u, "reward.finance_approve", "reward.finance_reject", "reward.release"),
    "/reports": lambda u: D.has_perm(u, "report.view_company"),
    "/budgets": lambda u: D.can_view_budget(u),
    "/admin": lambda u: has_any_perm(u, "user.manage", "role.manage"),
    "/hotspots": lambda u: has_any_perm(u, "hse.module", "report.view_company"),
    "/highpotential": lambda u: has_any_perm(u, "hse.module", "report.view_company"),
    "/summary": lambda u: has_any_perm(u, "report.view_department", "report.view_company"),
    "/quality": lambda u: D.has_perm(u, "hse.module"),
    "/ai": lambda u: has_any_perm(u, "hse.module", "report.view_company"),
}

CSV_ROUTE_GUARDS = {
    "/points.csv": lambda u: has_any_perm(u, "reward.view_eligibility", "report.view_department", "report.view_company"),
    "/leaderboard.csv": lambda u: has_any_perm(u, "reward.view_eligibility", "report.view_department", "report.view_company"),
    "/reports.csv": lambda u: D.has_perm(u, "report.view_company"),
    "/hotspots.csv": lambda u: has_any_perm(u, "hse.module", "report.view_company"),
    "/summary.csv": lambda u: has_any_perm(u, "report.view_department", "report.view_company"),
    "/ai.csv": lambda u: has_any_perm(u, "hse.module", "report.view_company"),
}


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "SafetyRewards/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive + Content-Length avoids RST-on-close truncation

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
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_forbidden(self, user, path):
        body = '<div class="empty">%s</div>' % R.esc(ACCESS_DENIED)
        return self.send_html(R.page("Access Denied", user, body, path), status=403)

    def send_csv(self, filename, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def finish(self):
        # Graceful shutdown (FIN after all buffered data) avoids the Windows
        # RST-on-close that truncates large responses for clients that send
        # "Connection: close".
        try:
            self.wfile.flush()
        except Exception:
            pass
        try:
            self.connection.shutdown(socket.SHUT_WR)
        except Exception:
            pass
        try:
            super().finish()
        except Exception:
            pass

    # -- GET --
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        user = self.current_user()

        if path == "/login":
            if user:
                return self.send_redirect("/")
            return self.send_html(R.login_page({}, q1(qs, "m", "")))

        if path == "/logout":
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            token = c["sid"].value if "sid" in c else None
            SESSIONS.pop(token, None)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "sid=; Path=/; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not user:
            return self.send_redirect("/login")

        if path in CSV_ROUTES:
            guard = CSV_ROUTE_GUARDS.get(path)
            if guard and not guard(user):
                return self.send_forbidden(user, path)
            filename, text = CSV_ROUTES[path](user, qs)
            return self.send_csv(filename, text)

        if path in GET_ROUTES:
            guard = ROUTE_GUARDS.get(path)
            if guard and not guard(user):
                return self.send_forbidden(user, path)
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
            employee_id = (q1(form, "employee_id") or "").strip()
            password = (q1(form, "password") or "").strip()
            login_key = _login_key(employee_id, self.client_address[0] if self.client_address else "")
            if _login_blocked(login_key):
                return self.send_redirect("/login?m=Too+many+failed+attempts.+Wait+10+minutes+and+try+again.")
            login_user = D.employee_by_employee_id(employee_id)
            if login_user and login_user.get("active", True) and D.valid_login_password(login_user, password):
                _clear_login_attempts(login_key)
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = login_user["id"]
                D.record_audit(login_user, "login", "auth", login_user["id"], None,
                               {"employee_id": login_user.get("employee_id")})
                D.save()
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "sid=%s; Path=/; HttpOnly; SameSite=Lax" % token)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            _record_failed_login(login_key)
            return self.send_redirect("/login?m=Invalid+Employee+ID+or+password")

        user = self.current_user()
        if not user:
            return self.send_redirect("/login")

        handler = POST_ROUTES.get(path)
        if not handler:
            return self.send_redirect("/")
        guard = ROUTE_GUARDS.get(path)
        if guard and not guard(user):
            return self.send_forbidden(user, path)
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
