"""Domain logic and JSON persistence for Safety Rewards Tracker.

Pure Python standard library. No external packages.

This module owns:
  * the role model and budget-access rules
  * date helpers (auto quarter-from-month, week-in-month labels)
  * the JSON data store (seed on first run, load, save)
  * scoring, leaderboards, department employee-based limits and budgets
"""

import json
import os
import random
from datetime import date, datetime, timedelta

import adinkra

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "safetypays_data.json")

DB = {}

# --------------------------------------------------------------------------
# Roles and access control
# --------------------------------------------------------------------------
# role key -> display label. Login screen lists them in this order.
ROLE_LABELS = {
    "worker": "Worker",
    "supervisor": "Supervisor",
    "hse_manager": "HSE Manager",
    "management": "Management",
    "finance_manager": "Finance Manager",
    "admin": "Admin",
    "contractor_admin": "Contractor Admin",
}
ROLE_ORDER = [
    "worker",
    "supervisor",
    "hse_manager",
    "management",
    "finance_manager",
    "admin",
    "contractor_admin",
]

# Only Managers, Management and the Finance Team may even see the budget
# modules; only the Admin can create, edit, approve or lock a budget.
BUDGET_VIEW_ROLES = {"hse_manager", "management", "finance_manager", "admin"}
BUDGET_EDIT_ROLES = {"admin"}

# Who can approve worker reports / corrective actions.
REVIEW_ROLES = {"supervisor", "hse_manager", "admin"}
# Who approves reward requests (stage 1) and who releases them (stage 2).
REWARD_APPROVE_ROLES = {"admin"}
REWARD_RELEASE_ROLES = {"finance_manager", "admin"}
REPORTS_ROLES = {"supervisor", "hse_manager", "management", "finance_manager", "admin"}


def role_label(role):
    return ROLE_LABELS.get(role, role.title())


def can_view_budget(role):
    return role in BUDGET_VIEW_ROLES


def can_edit_budget(role):
    return role in BUDGET_EDIT_ROLES


# --------------------------------------------------------------------------
# Reward / budget economics
# --------------------------------------------------------------------------
CURRENCY = "GH₵"  # Ghana cedi
# Each active employee earns the department this much monthly reward headroom.
BUDGET_PER_ACTIVE_WORKER = 75  # GH cedi / active worker / month

POINTS = {
    "observation": 10,
    "hid": 20,          # hazard / near-miss report
    "incident": 15,     # reporting an incident is rewarded
    "action_closed": 25,
}

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# --------------------------------------------------------------------------
# Date helpers
# --------------------------------------------------------------------------


def month_name(m):
    return MONTHS[m - 1]


def quarter_of_month(month):
    """Quarter is derived automatically from the selected month."""
    return (int(month) - 1) // 3 + 1


def quarter_label(q):
    return "Q%d" % q


def quarter_months(q):
    return [3 * q - 2, 3 * q - 1, 3 * q]


def week_in_month(d):
    """Week number *inside the month* (1-5), not the ISO week-in-year."""
    if isinstance(d, str):
        d = datetime.fromisoformat(d).date()
    return (d.day - 1) // 7 + 1


def week_label(d):
    """e.g. 'Week 2 in June' -- week in month, never week in year."""
    if isinstance(d, str):
        d = datetime.fromisoformat(d).date()
    return "Week %d in %s" % (week_in_month(d), month_name(d.month))


def today():
    return date.today()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(s):
    return datetime.fromisoformat(s)


def fmt_date(s):
    try:
        return parse_dt(s).strftime("%d %b %Y")
    except Exception:
        return s


def fmt_money(amount):
    return "%s%s" % (CURRENCY, "{:,.0f}".format(amount))


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------


def load():
    global DB
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            DB = json.load(fh)
    else:
        DB = seed()
        save()
    return DB


def save():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(DB, fh, indent=2)


def reset_demo():
    """Delete the runtime file and reseed (used by the Admin tools)."""
    global DB
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    DB = seed()
    save()
    return DB


_counters = {}


def next_id(collection):
    items = DB.get(collection, [])
    nums = [it.get("id", 0) for it in items]
    return (max(nums) + 1) if nums else 1


# --------------------------------------------------------------------------
# Seed data
# --------------------------------------------------------------------------


def seed():
    rng = random.Random(20260613)
    anchor = date.today()
    db = {
        "users": [],
        "departments": [],
        "companies": [],
        "safety_observations": [],
        "near_miss_hazard_reports": [],
        "incidents": [],
        "corrective_actions": [],
        "safety_points": [],
        "point_reset_events": [],
        "rewards": [],
        "reward_requests": [],
        "yearly_reward_budgets": [],
        "monthly_reward_budgets": [],
        "quarterly_reward_budgets": [],
    }

    # Departments straight from the Adinkra roster (no "Safety Team" labels).
    for idx, dept in enumerate(adinkra.DEPARTMENTS, start=1):
        active = max(1, dept["employee_count"] - rng.randint(0, 5))
        db["departments"].append({
            "id": idx,
            "key": dept["key"],
            "adinkra_name": dept["adinkra_name"],
            "meaning": dept["meaning"],
            "motto": dept["motto"],
            "commons_file": dept["commons_file"],
            "employee_count": dept["employee_count"],
            "active_employees": active,
        })

    # Contractor companies.
    db["companies"] = [
        {"id": 1, "name": "Tarkwa Drilling Ltd"},
        {"id": 2, "name": "Obuasi Haulage Co."},
    ]

    # ---- Users -------------------------------------------------------------
    uid = 1
    first = ["Kwame", "Ama", "Kofi", "Akua", "Yaw", "Abena", "Kojo", "Adwoa",
             "Kwabena", "Akosua", "Kwaku", "Afia", "Yaa", "Fiifi", "Esi",
             "Nana", "Kwadwo", "Maa", "Kweku", "Adjoa"]
    last = ["Mensah", "Owusu", "Boateng", "Asante", "Agyeman", "Darko",
            "Appiah", "Osei", "Annan", "Frimpong", "Tetteh", "Quartey",
            "Addo", "Bediako", "Gyamfi"]

    def make_name():
        return "%s %s" % (rng.choice(first), rng.choice(last))

    # Leadership / staff roles (not tied to a single department for scoring).
    staff = [
        ("HSE Manager", "hse_manager", "fihankra"),
        ("Management", "management", "adinkrahene"),
        ("Finance Manager", "finance_manager", "adinkrahene"),
        ("System Admin", "admin", "adinkrahene"),
    ]
    for title, role, dept_key in staff:
        db["users"].append({
            "id": uid, "name": make_name(), "role": role, "title": title,
            "dept_key": dept_key, "company_id": None, "is_contractor": False,
            "active": True,
        })
        uid += 1

    # Each department gets one supervisor + several worker-employees.
    for dept in db["departments"]:
        db["users"].append({
            "id": uid, "name": make_name(), "role": "supervisor",
            "title": "Supervisor", "dept_key": dept["key"], "company_id": None,
            "is_contractor": False, "active": True,
        })
        uid += 1
        for _ in range(rng.randint(4, 6)):
            db["users"].append({
                "id": uid, "name": make_name(), "role": "worker",
                "title": "Worker", "dept_key": dept["key"], "company_id": None,
                "is_contractor": False, "active": True,
            })
            uid += 1

    # Contractor admins + contractor workers (assigned to departments too).
    for comp in db["companies"]:
        db["users"].append({
            "id": uid, "name": make_name(), "role": "contractor_admin",
            "title": "Contractor Admin", "dept_key": rng.choice(db["departments"])["key"],
            "company_id": comp["id"], "is_contractor": True, "active": True,
        })
        uid += 1
        for _ in range(rng.randint(4, 6)):
            db["users"].append({
                "id": uid, "name": make_name(), "role": "worker",
                "title": "Contractor Worker",
                "dept_key": rng.choice(db["departments"])["key"],
                "company_id": comp["id"], "is_contractor": True, "active": True,
            })
            uid += 1

    workers = [u for u in db["users"] if u["role"] == "worker"]

    # ---- Rewards catalogue -------------------------------------------------
    db["rewards"] = [
        {"id": 1, "name": "Safety Boots Voucher", "description": "Voucher for a pair of certified safety boots.", "point_cost": 120, "cash_value": 180, "active": True},
        {"id": 2, "name": "Fuel Voucher", "description": "GH₵100 fuel voucher.", "point_cost": 80, "cash_value": 100, "active": True},
        {"id": 3, "name": "Airtime / Data Bundle", "description": "Monthly airtime and data bundle.", "point_cost": 40, "cash_value": 50, "active": True},
        {"id": 4, "name": "Branded Hard Hat", "description": "Premium branded hard hat.", "point_cost": 60, "cash_value": 75, "active": True},
        {"id": 5, "name": "Grocery Hamper", "description": "Family grocery hamper.", "point_cost": 150, "cash_value": 220, "active": True},
        {"id": 6, "name": "Safety Champion Plaque", "description": "Engraved recognition plaque.", "point_cost": 200, "cash_value": 250, "active": True},
    ]

    # ---- Activity + points ledger across the last ~6 weeks ----------------
    obs_cats = ["Unsafe act", "Unsafe condition", "Good practice", "Housekeeping", "PPE"]
    locations = ["Pit 4", "Process Plant", "Workshop", "Haul Road", "Stores", "Crusher", "Control Room"]
    pid = 1
    for w in workers:
        n_events = rng.randint(3, 11)
        for _ in range(n_events):
            days_ago = rng.randint(0, 40)
            d = anchor - timedelta(days=days_ago)
            kind = rng.choices(
                ["observation", "hid", "incident", "action_closed"],
                weights=[55, 25, 8, 12],
            )[0]
            ts = datetime(d.year, d.month, d.day, rng.randint(7, 17), rng.randint(0, 59)).isoformat(timespec="seconds")
            src_type, src_id = kind, None

            if kind == "observation":
                src_id = len(db["safety_observations"]) + 1
                db["safety_observations"].append({
                    "id": src_id, "ts": ts, "reporter_id": w["id"], "dept_key": w["dept_key"],
                    "location": rng.choice(locations), "category": rng.choice(obs_cats),
                    "description": "Observed during routine task.", "status": "approved",
                })
            elif kind == "hid":
                src_id = len(db["near_miss_hazard_reports"]) + 1
                db["near_miss_hazard_reports"].append({
                    "id": src_id, "ts": ts, "reporter_id": w["id"], "dept_key": w["dept_key"],
                    "type": rng.choice(["Hazard", "Near miss"]), "severity": rng.choice(["Low", "Medium", "High"]),
                    "location": rng.choice(locations), "description": "Potential hazard identified and reported.",
                    "status": "approved",
                })
            elif kind == "incident":
                src_id = len(db["incidents"]) + 1
                db["incidents"].append({
                    "id": src_id, "ts": ts, "reporter_id": w["id"], "dept_key": w["dept_key"],
                    "severity": rng.choice(["Minor", "Moderate"]), "lti": False,
                    "location": rng.choice(locations), "description": "Incident reported promptly.",
                    "status": "under_review", "lti_reset_applied": False,
                })
            else:  # action_closed
                src_id = len(db["corrective_actions"]) + 1
                db["corrective_actions"].append({
                    "id": src_id, "ts": ts, "source_type": "observation", "source_id": 0,
                    "dept_key": w["dept_key"], "owner_id": w["id"],
                    "description": "Corrective action completed and verified.",
                    "due": (d + timedelta(days=7)).isoformat(), "status": "closed", "closed_ts": ts,
                })

            db["safety_points"].append({
                "id": pid, "ts": ts, "user_id": w["id"], "dept_key": w["dept_key"],
                "points": POINTS[kind], "reason": kind.replace("_", " ").title(),
                "source_type": src_type, "source_id": src_id,
            })
            pid += 1

    # A couple of open observations awaiting supervisor review (queue demo).
    for _ in range(6):
        w = rng.choice(workers)
        d = anchor - timedelta(days=rng.randint(0, 6))
        ts = datetime(d.year, d.month, d.day, rng.randint(7, 17), rng.randint(0, 59)).isoformat(timespec="seconds")
        db["safety_observations"].append({
            "id": len(db["safety_observations"]) + 1, "ts": ts, "reporter_id": w["id"],
            "dept_key": w["dept_key"], "location": rng.choice(locations),
            "category": rng.choice(obs_cats), "description": "Submitted for review.",
            "status": "submitted",
        })

    # Some open corrective actions (tracker demo).
    for _ in range(7):
        w = rng.choice(workers)
        d = anchor - timedelta(days=rng.randint(1, 20))
        ts = datetime(d.year, d.month, d.day, 9, 0).isoformat(timespec="seconds")
        db["corrective_actions"].append({
            "id": len(db["corrective_actions"]) + 1, "source_type": "hid",
            "source_id": 0, "dept_key": w["dept_key"], "owner_id": w["id"],
            "description": "Install machine guard / repair guardrail.",
            "due": (anchor + timedelta(days=rng.randint(-3, 14))).isoformat(),
            "status": "open", "closed_ts": None,
        })

    # One Lost-Time Injury that triggers a department point reset (audit demo).
    lti_dept = db["departments"][2]["key"]  # Fihankra
    lti_worker = next(w for w in workers if w["dept_key"] == lti_dept)
    lti_day = anchor - timedelta(days=3)
    lti_ts = datetime(lti_day.year, lti_day.month, lti_day.day, 11, 30).isoformat(timespec="seconds")
    inc_id = len(db["incidents"]) + 1
    db["incidents"].append({
        "id": inc_id, "ts": lti_ts, "reporter_id": lti_worker["id"], "dept_key": lti_dept,
        "severity": "Lost Time Injury", "lti": True, "location": "Haul Road",
        "description": "Lost-time injury: operator sustained ankle injury.",
        "status": "under_review", "lti_reset_applied": True,
    })
    _apply_lti_reset(db, lti_dept, lti_ts, inc_id, lti_worker["id"])

    # ---- Reward requests in various states --------------------------------
    sample_workers = rng.sample(workers, 8)
    states = ["pending_admin", "pending_admin", "approved", "approved", "released", "released", "rejected", "pending_admin"]
    rqid = 1
    for w, st in zip(sample_workers, states):
        reward = rng.choice(db["rewards"])
        d = anchor - timedelta(days=rng.randint(0, 18))
        ts = datetime(d.year, d.month, d.day, 10, 0).isoformat(timespec="seconds")
        rq = {
            "id": rqid, "ts": ts, "user_id": w["id"], "dept_key": w["dept_key"],
            "reward_id": reward["id"], "point_cost": reward["point_cost"],
            "cash_value": reward["cash_value"], "status": st,
            "admin_id": None, "finance_id": None, "decided_ts": None,
        }
        if st in ("approved", "released", "rejected"):
            rq["admin_id"] = 4
            rq["decided_ts"] = ts
        if st == "released":
            rq["finance_id"] = 3
        db["reward_requests"].append(rq)
        rqid += 1

    # ---- Budgets ----------------------------------------------------------
    yr = anchor.year
    db["yearly_reward_budgets"].append({"id": 1, "year": yr, "amount": 240000, "locked": False})
    # Monthly budgets for the current and previous month.
    for m in {anchor.month, (anchor.month - 2) % 12 + 1}:
        db["monthly_reward_budgets"].append({
            "id": len(db["monthly_reward_budgets"]) + 1, "year": yr, "month": m,
            "amount": 20000, "locked": (m != anchor.month),
        })
    # Quarterly budget for the current quarter (quarter auto-derived from month).
    cq = quarter_of_month(anchor.month)
    db["quarterly_reward_budgets"].append({"id": 1, "year": yr, "quarter": cq, "amount": 60000, "locked": False})

    return db


def _apply_lti_reset(db, dept_key, ts, incident_id, worker_id):
    """Record a point-reset event. Monthly buckets for the department are
    treated as reset from this timestamp (earlier points in the month no
    longer count toward the monthly/quarterly/yearly totals)."""
    d = parse_dt(ts).date()
    points_before = 0
    for p in db["safety_points"]:
        pd = parse_dt(p["ts"]).date()
        if p["dept_key"] == dept_key and pd.year == d.year and pd.month == d.month and p["ts"] <= ts:
            points_before += p["points"]
    db["point_reset_events"].append({
        "id": len(db["point_reset_events"]) + 1, "ts": ts, "dept_key": dept_key,
        "incident_id": incident_id, "reported_by": worker_id,
        "year": d.year, "month": d.month, "points_reset": points_before,
        "reason": "Lost Time Injury",
    })


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------


def user(uid):
    return next((u for u in DB["users"] if u["id"] == uid), None)


def department(key):
    return next((d for d in DB["departments"] if d["key"] == key), None)


def reward(rid):
    return next((r for r in DB["rewards"] if r["id"] == rid), None)


def company(cid):
    return next((c for c in DB["companies"] if c["id"] == cid), None)


def dept_name(key):
    d = department(key)
    return d["adinkra_name"] if d else key


# --------------------------------------------------------------------------
# Scoring (reset-aware)
# --------------------------------------------------------------------------


def _reset_cutoff(dept_key, year, month):
    """Latest LTI reset timestamp for a department in a given month, or None."""
    cutoffs = [e["ts"] for e in DB["point_reset_events"]
               if e["dept_key"] == dept_key and e["year"] == year and e["month"] == month]
    return max(cutoffs) if cutoffs else None


def _entry_counts(entry, year, month=None):
    d = parse_dt(entry["ts"]).date()
    if d.year != year:
        return False
    if month is not None and d.month != month:
        return False
    cutoff = _reset_cutoff(entry["dept_key"], d.year, d.month)
    if cutoff and entry["ts"] <= cutoff:
        return False
    return True


def user_points(uid, year=None, month=None, quarter=None, week=None):
    """Earned points for a user, optionally scoped to a period.

    Monthly/quarterly/yearly totals respect LTI resets; weekly totals are the
    raw points earned inside that week-in-month.
    """
    total = 0
    for p in DB["safety_points"]:
        if p["user_id"] != uid:
            continue
        d = parse_dt(p["ts"]).date()
        if year is not None and d.year != year:
            continue
        if quarter is not None and quarter_of_month(d.month) != quarter:
            continue
        if month is not None and d.month != month:
            continue
        if week is not None:
            if week_in_month(d) != week:
                continue
        elif not _entry_counts(p, d.year, d.month):
            continue
        total += p["points"]
    return total


def user_balance(uid):
    """Spendable balance = earned points minus released redemptions."""
    earned = sum(p["points"] for p in DB["safety_points"] if p["user_id"] == uid)
    spent = sum(r["point_cost"] for r in DB["reward_requests"]
                if r["user_id"] == uid and r["status"] == "released")
    return earned - spent


def dept_points(dept_key, year=None, month=None, quarter=None):
    total = 0
    for p in DB["safety_points"]:
        if p["dept_key"] != dept_key:
            continue
        d = parse_dt(p["ts"]).date()
        if year is not None and d.year != year:
            continue
        if quarter is not None and quarter_of_month(d.month) != quarter:
            continue
        if month is not None and d.month != month:
            continue
        if not _entry_counts(p, d.year, d.month):
            continue
        total += p["points"]
    return total


# --------------------------------------------------------------------------
# Department employee-based limits & budget usage
# --------------------------------------------------------------------------


def dept_monthly_limit(dept):
    """Department Monthly Limit = Active Employees x Budget Per Active Worker."""
    return dept["active_employees"] * BUDGET_PER_ACTIVE_WORKER


def dept_budget_used(dept_key, year, month):
    """Cash value of reward requests released for a department this month."""
    used = 0
    for r in DB["reward_requests"]:
        if r["dept_key"] != dept_key or r["status"] != "released":
            continue
        d = parse_dt(r["ts"]).date()
        if d.year == year and d.month == month:
            used += r["cash_value"]
    return used


def budget_used(year, month=None, quarter=None):
    """Released reward cash across the org for a period."""
    used = 0
    for r in DB["reward_requests"]:
        if r["status"] != "released":
            continue
        d = parse_dt(r["ts"]).date()
        if d.year != year:
            continue
        if month is not None and d.month != month:
            continue
        if quarter is not None and quarter_of_month(d.month) != quarter:
            continue
        used += r["cash_value"]
    return used


# --------------------------------------------------------------------------
# Leaderboards
# --------------------------------------------------------------------------


def individual_leaderboard(year=None, month=None, quarter=None, week=None, contractors=None, dept_key=None):
    rows = []
    for u in DB["users"]:
        if u["role"] not in ("worker",):
            continue
        if contractors is True and not u["is_contractor"]:
            continue
        if contractors is False and u["is_contractor"]:
            continue
        if dept_key and u["dept_key"] != dept_key:
            continue
        pts = user_points(u["id"], year=year, month=month, quarter=quarter, week=week)
        rows.append({
            "user_id": u["id"], "name": u["name"], "dept_key": u["dept_key"],
            "company_id": u["company_id"], "is_contractor": u["is_contractor"],
            "points": pts,
        })
    rows.sort(key=lambda r: (-r["points"], r["name"]))
    return rows


def contractor_leaderboard(year=None, month=None, quarter=None, week=None):
    """Aggregate points by contractor company."""
    agg = {}
    for c in DB["companies"]:
        agg[c["id"]] = {"company_id": c["id"], "name": c["name"], "points": 0, "members": 0}
    for u in DB["users"]:
        if u["role"] == "worker" and u["is_contractor"] and u["company_id"] in agg:
            agg[u["company_id"]]["points"] += user_points(u["id"], year=year, month=month, quarter=quarter, week=week)
            agg[u["company_id"]]["members"] += 1
    rows = list(agg.values())
    rows.sort(key=lambda r: (-r["points"], r["name"]))
    return rows


def department_leaderboard(year=None, month=None, quarter=None):
    rows = []
    for d in DB["departments"]:
        pts = dept_points(d["key"], year=year, month=month, quarter=quarter)
        limit = dept_monthly_limit(d)
        used = dept_budget_used(d["key"], year or today().year, month or today().month)
        rows.append({
            "dept_key": d["key"], "adinkra_name": d["adinkra_name"], "meaning": d["meaning"],
            "motto": d["motto"], "commons_file": d["commons_file"],
            "points": pts, "active_employees": d["active_employees"],
            "employee_count": d["employee_count"], "limit": limit, "used": used,
            "remaining": limit - used,
        })
    rows.sort(key=lambda r: (-r["points"], r["adinkra_name"]))
    return rows


def champion_marker(rank):
    """Champion emoji for the top three places; plain number otherwise."""
    return {1: "\U0001F3C6", 2: "\U0001F948", 3: "\U0001F949"}.get(rank, "#%d" % rank)
