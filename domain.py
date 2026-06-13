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
from collections import Counter
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
# Plan tiering (this build is the Free Version)
# --------------------------------------------------------------------------
PLAN = "Free"
FREE_LIMITS = {
    "company": 1, "site": 1, "locations": 5, "departments": 2,
    "contractors": 3, "employees": 50, "champions": 2,
    "records_per_month": 100, "history_days": 90,
}

# Advanced capabilities reserved for paid tiers (rendered as locked cards).
PRO_FEATURES = [
    "AI Hazard Fingerprint", "AI Duplicate HID Detection", "Geographic hotspot maps",
    "GPS & QR location capture", "Advanced AI Risk Forecasting", "Unlimited location history",
    "Contractor frequency rates", "Man-hours integration", "LTIFR & TRIFR calculations",
    "Detailed root cause analysis", "Investigation workflow", "Cost analytics",
    "Quarterly & yearly reports", "Automated corrective-action escalation",
    "Power BI integration", "Multi-site comparison",
]

# --------------------------------------------------------------------------
# HSE controlled vocabularies (single approved master value each)
# --------------------------------------------------------------------------
RISK_LEVELS = ["Low", "Medium", "High", "Critical"]
RISK_RANK = {lvl: i for i, lvl in enumerate(RISK_LEVELS)}

CONSEQUENCES = ["Insignificant", "Minor", "Moderate", "Major", "Catastrophic"]
CONSEQUENCE_SEVERITY = {c: i + 1 for i, c in enumerate(CONSEQUENCES)}  # 1..5

CAUSE_CATEGORIES = [
    "Procedural", "Behavioural", "Equipment Condition", "Training Deficiency",
    "Inadequate Supervision", "Communication Failure", "Coordination Failure",
    "Environmental Condition", "Planning Failure", "Management System Failure", "Other",
]

COST_RANGES = [
    "Below GHS 1,000", "GHS 1,000–5,000", "GHS 5,001–20,000",
    "GHS 20,001–50,000", "Above GHS 50,000",
]
DAMAGE_TYPES = [
    "Vehicle", "Fixed Plant", "Mobile Equipment", "Property / Structure",
    "Tooling", "Electrical", "Other",
]
REPAIR_STATUS = ["Reported", "Under Repair", "Repaired", "Written Off"]

DEFAULT_HOTSPOT_THRESHOLDS = {"watch": 3, "high": 6, "critical": 10}

# Report-type registry used by the hotspot + summary modules.
REPORT_TYPES = {
    "incident": ("Incidents", "incidents"),
    "hid": ("HIDs", "near_miss_hazard_reports"),       # type == Hazard
    "near_miss": ("Near Misses", "near_miss_hazard_reports"),  # type == Near miss
    "observation": ("Safety Observations", "safety_observations"),
    "damage": ("Property / Equipment Damage", "property_damage"),
}

# Work activities and named equipment (used for AI prediction grouping).
ACTIVITIES = [
    "Loading", "Hauling", "Drilling & Blasting", "Crushing", "Maintenance",
    "Welding & Hot Work", "Working at Height", "Confined Space Entry",
    "Electrical Work", "Manual Handling", "Vehicle Operation",
]
EQUIPMENT = [
    "Loader LD-04", "Haul Truck HT-12", "Excavator EX-03", "Conveyor CV-02",
    "Light Vehicle LV-07", "Generator GEN-02", "Crusher Liner", "Drill Rig DR-05",
]

# --------------------------------------------------------------------------
# Basic AI Safety Prediction (rule-based + statistical, fully explainable)
# --------------------------------------------------------------------------
AI_MIN_RECORDS = 10   # approved records needed before predicting
AI_MIN_DAYS = 30      # days of activity needed
AI_MIN_ENTITY = 3     # records needed for a single entity prediction
AI_DISCLAIMER = ("AI predictions support HSE decision-making and do not replace professional "
                 "judgement, inspections, investigations, or legal compliance requirements.")
AI_FREE_LABEL = "Basic AI Safety Prediction — Included"
AI_PRO_LABEL = "Advanced AI Risk Forecasting — Pro"

# Advanced capabilities reserved for paid tiers (shown as locked cards on /ai).
AI_PRO_FEATURES = [
    "Machine-learning model training on multi-year data", "Custom company-specific models",
    "Real-time telemetry prediction", "Weather & environmental integration",
    "Fatigue prediction", "Vehicle collision prediction",
    "Automated critical-control failure prediction", "Cross-site benchmarking",
    "Multi-site risk forecasting", "Power BI AI integration", "API access",
    "Unlimited prediction history", "WhatsApp / SMS / email prediction alerts",
    "Predictive maintenance integration", "Model accuracy monitoring & retraining",
    "Custom risk weights",
]

CAUSE_RECOMMENDATION = {
    "Procedural": "Review the JSA / procedure",
    "Behavioural": "Coach the affected team",
    "Equipment Condition": "Inspect the equipment and verify critical controls",
    "Training Deficiency": "Deliver targeted training / a toolbox talk",
    "Inadequate Supervision": "Increase supervision",
    "Communication Failure": "Reinforce communication protocols",
    "Coordination Failure": "Review work coordination",
    "Environmental Condition": "Reassess environmental controls",
    "Planning Failure": "Review the job plan",
    "Management System Failure": "Audit the relevant management-system control",
    "Other": "Conduct a targeted inspection",
}

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
# Plan + HSE classification helpers
# --------------------------------------------------------------------------


def history_cutoff():
    """Earliest date the Free Version shows in detail (last 90 days)."""
    return today() - timedelta(days=FREE_LIMITS["history_days"])


def within_free_history(ts):
    """Free history = current month plus the previous 90 days."""
    d = parse_dt(ts).date()
    t = today()
    return (d.year == t.year and d.month == t.month) or d >= history_cutoff()


def severity_of(consequence):
    return CONSEQUENCE_SEVERITY.get(consequence, 0)


def risk_from_severity(sev):
    if sev >= 5:
        return "Critical"
    if sev == 4:
        return "High"
    if sev == 3:
        return "Medium"
    return "Low"


def highest_risk(levels):
    """Return the most severe risk level from an iterable, or '' if none."""
    best = ""
    for lv in levels:
        if lv and RISK_RANK.get(lv, -1) > RISK_RANK.get(best, -1):
            best = lv
    return best


def record_is_high_potential(rec):
    """A record is high-potential when the potential consequence is Major/
    Catastrophic, the risk level is Critical, or a reviewer flagged it."""
    if rec.get("is_high_potential"):
        return True
    if rec.get("potential_consequence") in ("Major", "Catastrophic"):
        return True
    if rec.get("risk_level") == "Critical":
        return True
    return False


def hotspot_thresholds():
    s = DB.get("settings", {})
    return s.get("hotspot_thresholds", dict(DEFAULT_HOTSPOT_THRESHOLDS))


def hotspot_status(total, th=None):
    th = th or hotspot_thresholds()
    if total >= th["critical"]:
        return "Critical"
    if total >= th["high"]:
        return "High Risk"
    if total >= th["watch"]:
        return "Watch"
    return "Normal"


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
        "property_damage": [],
        "yearly_reward_budgets": [],
        "monthly_reward_budgets": [],
        "quarterly_reward_budgets": [],
        "settings": {"hotspot_thresholds": dict(DEFAULT_HOTSPOT_THRESHOLDS)},
    }

    # Departments straight from the Adinkra roster (no "Safety Team" labels).
    for idx, dept in enumerate(adinkra.DEPARTMENTS, start=1):
        active = max(1, dept["employee_count"] - rng.randint(0, 5))
        db["departments"].append({
            "id": idx,
            "key": dept["key"],
            "adinkra_name": dept["adinkra_name"],
            "department": dept["department"],
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

    # ---- Enrich reports with HSE classification (risk + consequence) ------
    sub_locations = {
        "Pit 4": ["North Wall", "South Ramp", "Loading Bay"],
        "Process Plant": ["Mill 1", "Thickener", "Reagent Store"],
        "Workshop": ["Wash Bay", "Welding Bay"],
        "Haul Road": ["Switchback", "Intersection 3"],
        "Stores": ["Yard", "Chemical Store"],
        "Crusher": ["Primary", "Feed Conveyor"],
        "Control Room": [],
    }

    def consequence_pair(escalate_bias=0):
        actual = rng.choices(CONSEQUENCES, weights=[34, 30, 20, 10, 6])[0]
        pi = min(4, CONSEQUENCES.index(actual) + rng.choice([0, 0, 1, 1, 2, 3]) + escalate_bias)
        return actual, CONSEQUENCES[pi]

    def enrich(rec, escalate_bias=0, with_cause=False):
        subs = sub_locations.get(rec.get("location"), [])
        rec["sub_location"] = rng.choice(subs) if subs and rng.random() < 0.7 else ""
        actual, potential = consequence_pair(escalate_bias)
        rec["actual_consequence"] = actual
        rec["potential_consequence"] = potential
        rec["actual_severity"] = severity_of(actual)
        rec["potential_severity"] = severity_of(potential)
        rec["actual_risk_rating"] = risk_from_severity(severity_of(actual))
        rec["potential_risk_rating"] = risk_from_severity(severity_of(potential))
        rec["risk_level"] = risk_from_severity(severity_of(potential))
        if with_cause:
            rec["cause_category"] = rng.choice(CAUSE_CATEGORIES)
        rec["is_high_potential"] = record_is_high_potential(rec)
        rec["high_potential_reason"] = ("Potential %s consequence." % potential) if rec["is_high_potential"] else ""
        rec["reviewed_by"] = 1 if rec["is_high_potential"] else None
        rec["review_date"] = rec["ts"] if rec["is_high_potential"] else None
        rec["activity"] = rng.choice(ACTIVITIES)
        if not rec.get("equipment_involved") and rng.random() < 0.5:
            rec["equipment_involved"] = rng.choice(EQUIPMENT)

    for o in db["safety_observations"]:
        enrich(o)
    for h in db["near_miss_hazard_reports"]:
        enrich(h, escalate_bias=1, with_cause=True)
    for i in db["incidents"]:
        enrich(i, escalate_bias=1, with_cause=True)
    lti_inc = next(i for i in db["incidents"] if i["id"] == inc_id)
    lti_inc.update({"actual_consequence": "Major", "potential_consequence": "Catastrophic",
                    "actual_severity": severity_of("Major"), "potential_severity": severity_of("Catastrophic"),
                    "risk_level": "Critical", "is_high_potential": True, "reviewed_by": 1,
                    "review_date": lti_inc["ts"],
                    "high_potential_reason": "Lost-time injury with catastrophic potential."})

    # ---- Property / equipment damage events -------------------------------
    equip = EQUIPMENT
    for pd_id in range(1, 9):
        w = rng.choice(workers)
        d = anchor - timedelta(days=rng.randint(0, 60))
        ts = datetime(d.year, d.month, d.day, rng.randint(7, 17), 0).isoformat(timespec="seconds")
        loc = rng.choice(locations)
        actual, potential = consequence_pair(escalate_bias=1)
        db["property_damage"].append({
            "id": pd_id, "ts": ts, "reporter_id": w["id"], "dept_key": w["dept_key"],
            "location": loc, "sub_location": (rng.choice(sub_locations.get(loc) or [""]) or ""),
            "damage_type": rng.choice(DAMAGE_TYPES), "equipment_involved": rng.choice(equip),
            "activity": rng.choice(ACTIVITIES),
            "asset_number": "AST-%04d" % rng.randint(1000, 9999),
            "estimated_cost_range": rng.choice(COST_RANGES),
            "downtime_hours": rng.choice([0, 2, 4, 8, 12, 24, 48]),
            "operational_impact": rng.choice(["None", "Minor delay", "Partial stoppage", "Full stoppage"]),
            "repair_status": rng.choice(REPAIR_STATUS), "description": "Equipment / property damage reported.",
            "actual_consequence": actual, "potential_consequence": potential,
            "actual_severity": severity_of(actual), "potential_severity": severity_of(potential),
            "risk_level": risk_from_severity(severity_of(potential)),
            "is_high_potential": potential in ("Major", "Catastrophic"), "status": "open",
        })

    # Corrective actions get a location (for hotspot action counts) and the
    # LTI incident records lost work days (used by data-quality validation).
    for a in db["corrective_actions"]:
        a.setdefault("location", rng.choice(locations))
    lti_inc["lost_days"] = rng.randint(3, 30)

    # ---- SafePay Champions (Free plan allows up to 2) ---------------------
    champ_pool = [u for u in workers if not u["is_contractor"]]
    for u in rng.sample(champ_pool, min(FREE_LIMITS["champions"], len(champ_pool))):
        u["is_champion"] = True

    # ---- Reward requests across the 4-stage workflow ----------------------
    # submit -> admin approves -> finance approves -> released  (or rejected)
    sample_workers = rng.sample(workers, 9)
    states = ["pending_admin", "pending_admin", "pending_finance", "pending_finance",
              "finance_approved", "released", "released", "rejected", "pending_admin"]
    admin_uid = next(u["id"] for u in db["users"] if u["role"] == "admin")
    finance_uid = next(u["id"] for u in db["users"] if u["role"] == "finance_manager")
    rqid = 1
    for w, st in zip(sample_workers, states):
        reward = rng.choice(db["rewards"])
        d = anchor - timedelta(days=rng.randint(0, 18))
        base = datetime(d.year, d.month, d.day, 10, 0)
        ts = base.isoformat(timespec="seconds")
        admin_ts = base + timedelta(days=1)
        fin_ts = base + timedelta(days=2)
        rel_ts = base + timedelta(days=3)
        rq = {
            "id": rqid, "ts": ts, "user_id": w["id"], "dept_key": w["dept_key"],
            "reward_id": reward["id"], "point_cost": reward["point_cost"],
            "cash_value": reward["cash_value"], "status": st,
            "admin_id": None, "admin_ts": None, "finance_id": None, "finance_ts": None,
            "released_by": None, "released_ts": None,
            "reject_reason": None, "rejected_by": None, "reject_stage": None, "rejected_ts": None,
        }
        # admin approval recorded for everything past pending_admin (incl. rejected at finance)
        if st in ("pending_finance", "finance_approved", "released"):
            rq["admin_id"] = admin_uid
            rq["admin_ts"] = admin_ts.isoformat(timespec="seconds")
        if st in ("finance_approved", "released"):
            rq["finance_id"] = finance_uid
            rq["finance_ts"] = fin_ts.isoformat(timespec="seconds")
        if st == "released":
            rq["released_by"] = finance_uid
            rq["released_ts"] = rel_ts.isoformat(timespec="seconds")
        if st == "rejected":
            rq["admin_id"] = admin_uid
            rq["admin_ts"] = admin_ts.isoformat(timespec="seconds")
            rq["rejected_by"] = admin_uid
            rq["reject_stage"] = "admin"
            rq["reject_reason"] = "Insufficient supporting evidence for the request."
            rq["rejected_ts"] = admin_ts.isoformat(timespec="seconds")
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


def dept_department(key):
    """The real operational department a given Adinkra emblem represents."""
    d = department(key)
    return d.get("department", "") if d else ""


def records_in(coll, year, month=None, quarter=None, where=None):
    """All records in a collection within a period, with an optional predicate.

    Used by the Monthly Reports Centre to auto-generate per-module reports.
    """
    out = []
    for it in DB.get(coll, []):
        ts = it.get("ts")
        if not ts:
            continue
        d = parse_dt(ts).date()
        if d.year != year:
            continue
        if month is not None and d.month != month:
            continue
        if quarter is not None and quarter_of_month(d.month) != quarter:
            continue
        if where is not None and not where(it):
            continue
        out.append(it)
    return out


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
            "dept_key": d["key"], "adinkra_name": d["adinkra_name"],
            "department": d.get("department", ""), "meaning": d["meaning"],
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


# --------------------------------------------------------------------------
# HSE modules: hotspots, high-potential, summaries, cause, damage, quality
# --------------------------------------------------------------------------


def _norm_reports(year=None, month=None, dept=None, location=None, report_type=None,
                  risk_level=None, free=True):
    """Normalised stream of safety records across every reporting module."""
    res = []

    def consider(rec, rtype):
        ts = rec.get("ts")
        if not ts:
            return
        d = parse_dt(ts).date()
        if year and d.year != year:
            return
        if month and d.month != month:
            return
        if free and not within_free_history(ts):
            return
        if dept and rec.get("dept_key") != dept:
            return
        if location and (rec.get("location") or "—") != location:
            return
        if report_type and rtype != report_type:
            return
        if risk_level and rec.get("risk_level") != risk_level:
            return
        res.append({
            "rtype": rtype, "location": rec.get("location") or "—",
            "sub_location": rec.get("sub_location") or "", "dept_key": rec.get("dept_key"),
            "reporter_id": rec.get("reporter_id"), "risk_level": rec.get("risk_level") or "",
            "ts": ts, "high_potential": record_is_high_potential(rec),
            "cause_category": rec.get("cause_category") or "", "rec": rec,
        })

    for i in DB["incidents"]:
        consider(i, "incident")
    for h in DB["near_miss_hazard_reports"]:
        consider(h, "hid" if h.get("type") == "Hazard" else "near_miss")
    for o in DB["safety_observations"]:
        consider(o, "observation")
    for p in DB.get("property_damage", []):
        consider(p, "damage")
    return res


def location_options():
    locs = set()
    for coll in ("incidents", "near_miss_hazard_reports", "safety_observations", "property_damage"):
        for r in DB.get(coll, []):
            if r.get("location"):
                locs.add(r["location"])
    return sorted(locs)


def _open_overdue_actions_by_location():
    today_iso = today().isoformat()
    opened, overdue = Counter(), Counter()
    for a in DB["corrective_actions"]:
        if a["status"] != "open":
            continue
        loc = a.get("location") or "—"
        opened[loc] += 1
        if a.get("due") and a["due"] < today_iso:
            overdue[loc] += 1
    return opened, overdue


def location_hotspots(year=None, month=None, dept=None, location=None, report_type=None,
                      risk_level=None, free=True):
    reports = _norm_reports(year=year, month=month, dept=dept, location=location,
                            report_type=report_type, risk_level=risk_level, free=free)
    opened, overdue = _open_overdue_actions_by_location()
    locs = {}

    def slot(name):
        return locs.setdefault(name, {
            "location": name, "total": 0, "incident": 0, "hid": 0, "near_miss": 0,
            "observation": 0, "damage": 0, "unsafe": 0, "open_actions": 0,
            "overdue_actions": 0, "risks": [],
        })

    for r in reports:
        s = slot(r["location"])
        s["total"] += 1
        s[r["rtype"]] += 1
        if r["rtype"] == "observation" and r["rec"].get("category") == "Unsafe condition":
            s["unsafe"] += 1
        s["risks"].append(r["risk_level"])
    for name, n in opened.items():
        slot(name)["open_actions"] = n
    for name, n in overdue.items():
        slot(name)["overdue_actions"] = n

    rows = list(locs.values())
    for s in rows:
        s["highest_risk"] = highest_risk(s["risks"])
        s["status"] = hotspot_status(s["total"])
    rows.sort(key=lambda s: (-s["total"], s["location"]))
    return rows


def high_potential_events(year=None, month=None, dept=None, location=None, free=True):
    rows = [r for r in _norm_reports(year=year, month=month, dept=dept, location=location, free=free)
            if r["high_potential"]]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def low_actual_high_potential(year=None, month=None, free=True):
    """Records with low actual impact but Major/Catastrophic potential."""
    out = []
    for r in _norm_reports(year=year, month=month, free=free):
        rec = r["rec"]
        if (rec.get("actual_consequence") in ("Insignificant", "Minor", "Moderate")
                and rec.get("potential_consequence") in ("Major", "Catastrophic")):
            out.append(r)
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def dept_summary(year=None, month=None, free=True):
    today_iso = today().isoformat()
    yr = year or today().year
    mo = month or today().month
    rows = []
    for d in DB["departments"]:
        reps = _norm_reports(year=year, month=month, dept=d["key"], free=free)
        open_a = [a for a in DB["corrective_actions"] if a["dept_key"] == d["key"] and a["status"] == "open"]
        rows.append({
            "dept_key": d["key"], "adinkra_name": d["adinkra_name"], "department": d.get("department", ""),
            "total": len(reps),
            "incidents": sum(1 for r in reps if r["rtype"] == "incident"),
            "hids": sum(1 for r in reps if r["rtype"] == "hid"),
            "near_misses": sum(1 for r in reps if r["rtype"] == "near_miss"),
            "high_potential": sum(1 for r in reps if r["high_potential"]),
            "open_actions": len(open_a),
            "overdue_actions": sum(1 for a in open_a if a.get("due") and a["due"] < today_iso),
            "points": dept_points(d["key"], year=yr, month=mo),
        })
    rows.sort(key=lambda r: (-r["total"], r["adinkra_name"]))
    return rows


def contractor_summary(year=None, month=None, free=True):
    today_iso = today().isoformat()
    rows = []
    for c in DB["companies"]:
        members = {u["id"] for u in DB["users"] if u.get("company_id") == c["id"]}
        reps = [r for r in _norm_reports(year=year, month=month, free=free) if r["reporter_id"] in members]
        open_a = [a for a in DB["corrective_actions"] if a.get("owner_id") in members and a["status"] == "open"]
        rows.append({
            "company_id": c["id"], "name": c["name"], "total": len(reps),
            "incidents": sum(1 for r in reps if r["rtype"] == "incident"),
            "hids": sum(1 for r in reps if r["rtype"] == "hid"),
            "near_misses": sum(1 for r in reps if r["rtype"] == "near_miss"),
            "high_potential": sum(1 for r in reps if r["high_potential"]),
            "damage": sum(1 for r in reps if r["rtype"] == "damage"),
            "open_actions": len(open_a),
            "overdue_actions": sum(1 for a in open_a if a.get("due") and a["due"] < today_iso),
        })
    rows.sort(key=lambda r: (-r["total"], r["name"]))
    return rows


def cause_category_counts(year=None, month=None, dept=None, location=None, free=True, high_only=False):
    c = Counter()
    for r in _norm_reports(year=year, month=month, dept=dept, location=location, free=free):
        if r["rtype"] in ("incident", "hid", "near_miss") and r["cause_category"]:
            if high_only and not r["high_potential"]:
                continue
            c[r["cause_category"]] += 1
    return c


def damage_items(year=None, month=None, free=True):
    out = []
    for p in DB.get("property_damage", []):
        d = parse_dt(p["ts"]).date()
        if year and d.year != year:
            continue
        if month and d.month != month:
            continue
        if free and not within_free_history(p["ts"]):
            continue
        out.append(p)
    return out


def records_this_month():
    """Count of all detailed safety records created in the current month."""
    yr, mo = today().year, today().month
    n = 0
    for coll in ("incidents", "near_miss_hazard_reports", "safety_observations", "property_damage"):
        for r in DB.get(coll, []):
            d = parse_dt(r["ts"]).date()
            if d.year == yr and d.month == mo:
                n += 1
    return n


def at_record_limit():
    return records_this_month() >= FREE_LIMITS["records_per_month"]


def validate_record(kind, data):
    """Pre-submission data-quality warnings. Returns a list of warning strings.

    A Supervisor / HSE reviewer may override by supplying a reason.
    """
    warnings = []
    if not (data.get("location") or "").strip():
        warnings.append("Location is required.")
    if not (data.get("description") or "").strip():
        warnings.append("A description is required.")
    if kind == "hid" and data.get("type") == "Near miss":
        if data.get("actual_consequence") in ("Major", "Catastrophic"):
            warnings.append("A Near Miss should not record a serious actual injury — reclassify as an Incident.")
    if kind == "incident" and data.get("lti") == "1":
        try:
            if int(data.get("lost_days") or 0) <= 0:
                warnings.append("A Lost Time Injury must record the number of lost work days.")
        except ValueError:
            warnings.append("Lost work days must be a number.")
    return warnings


def data_quality(free=True):
    """Summary of data completeness and classification warnings across records."""
    total = 0
    missing = 0
    warnings = 0
    samples = []
    today_iso = today().isoformat()

    def scan(rec, kind, label):
        nonlocal total, missing, warnings
        total += 1
        miss = not (rec.get("location") and rec.get("description"))
        warn = False
        if kind == "near_miss" and rec.get("actual_consequence") in ("Major", "Catastrophic"):
            warn = True
        if kind == "incident" and rec.get("lti") and not rec.get("lost_days"):
            warn = True
        if miss:
            missing += 1
        if warn:
            warnings += 1
        if (miss or warn) and not rec.get("dq_override") and len(samples) < 12:
            samples.append({"kind": label, "id": rec.get("id"),
                            "issue": "Missing required field" if miss else "Classification warning"})

    for i in DB["incidents"]:
        scan(i, "incident", "Incident")
    for h in DB["near_miss_hazard_reports"]:
        scan(h, "hid" if h.get("type") == "Hazard" else "near_miss",
             "HID" if h.get("type") == "Hazard" else "Near Miss")
    for o in DB["safety_observations"]:
        scan(o, "observation", "Observation")
    for p in DB.get("property_damage", []):
        scan(p, "damage", "Damage")
    for a in DB["corrective_actions"]:
        total += 1
        if a["status"] == "closed" and not a.get("closed_ts"):
            warnings += 1
            if len(samples) < 12:
                samples.append({"kind": "Corrective Action", "id": a.get("id"),
                                "issue": "Closed without a closure date"})

    complete = total - missing
    pct = round(100 * complete / total) if total else 100
    return {"total": total, "missing": missing, "warnings": warnings,
            "awaiting": missing + warnings, "completeness": pct, "samples": samples}


# --------------------------------------------------------------------------
# AI Safety Prediction engine (Free: rule-based + statistical, explainable)
# --------------------------------------------------------------------------


def ai_risk_level(score):
    if score >= 75:
        return "Critical"
    if score >= 50:
        return "High"
    if score >= 25:
        return "Moderate"
    return "Low"


def _activity_span_days():
    """Days between the earliest and latest safety record (data maturity)."""
    dates = []
    for coll in ("incidents", "near_miss_hazard_reports", "safety_observations", "property_damage"):
        for r in DB.get(coll, []):
            if r.get("ts"):
                dates.append(parse_dt(r["ts"]).date())
    if not dates:
        return 0
    return (max(dates) - min(dates)).days


def _ai_confidence(n, window_days):
    return min(95, 35 + 7 * n + (15 if window_days >= AI_MIN_DAYS else 0))


def ai_confidence_label(c):
    return "High" if c >= 75 else ("Medium" if c >= 50 else "Low")


def _ai_score(recs, open_a, overdue_a, prev_count):
    inc = sum(1 for r in recs if r["rtype"] == "incident")
    hid = sum(1 for r in recs if r["rtype"] == "hid")
    nm = sum(1 for r in recs if r["rtype"] == "near_miss")
    dmg = sum(1 for r in recs if r["rtype"] == "damage")
    hipo = sum(1 for r in recs if r["high_potential"])
    pot_sev = max([severity_of(r["rec"].get("potential_consequence")) for r in recs] or [0])
    causes = Counter(r["cause_category"] for r in recs if r["cause_category"])
    repeat = sum(max(0, c - 1) for c in causes.values())
    trend = len(recs) > prev_count
    comp = {
        "Incidents": 9 * inc, "HIDs": 4 * hid, "Near misses": 3 * nm,
        "High-potential events": 12 * hipo, "Overdue actions": 7 * overdue_a,
        "Open actions": 2 * open_a, "Property/equipment damage": 4 * dmg,
        "Repeat hazards": 6 * repeat, "Potential severity": 5 * pot_sev,
        "Rising trend": 15 if trend else 0,
    }
    score = min(100, sum(comp.values()))
    stats = {"inc": inc, "hid": hid, "nm": nm, "dmg": dmg, "hipo": hipo, "pot_sev": pot_sev,
             "repeat": repeat, "open": open_a, "overdue": overdue_a, "trend": trend, "n": len(recs)}
    return score, comp, stats


def _ai_factors(name, level, stats, period_label):
    parts = []
    plural = lambda n, w, suff="s": "%d %s%s" % (n, w, suff if n != 1 else "")
    if stats["inc"]:
        parts.append(plural(stats["inc"], "incident"))
    if stats["hid"]:
        parts.append(plural(stats["hid"], "HID"))
    if stats["nm"]:
        parts.append("%d near miss%s" % (stats["nm"], "es" if stats["nm"] != 1 else ""))
    if stats["hipo"]:
        parts.append(plural(stats["hipo"], "high-potential event"))
    if stats["overdue"]:
        parts.append(plural(stats["overdue"], "overdue corrective action"))
    if stats["dmg"]:
        parts.append("%d property/equipment damage case%s" % (stats["dmg"], "s" if stats["dmg"] != 1 else ""))
    listing = ", ".join(parts) if parts else "limited recent activity"
    trend = " Activity is trending up versus the previous period." if stats["trend"] else ""
    return "%s is rated %s because it recorded %s during %s.%s" % (name, level, listing, period_label, trend)


def _ai_recommend(entity_type, name, recs, stats):
    if stats["overdue"]:
        return "Close the %d overdue corrective action(s) and verify critical controls." % stats["overdue"]
    if entity_type == "equipment":
        return "Inspect %s and verify critical controls." % name
    if entity_type == "activity":
        return "Review the JSA for %s and deliver a toolbox talk." % name
    if entity_type == "contractor":
        return "Review %s's safety performance and increase supervision." % name
    if entity_type == "cause":
        return CAUSE_RECOMMENDATION.get(name, "Conduct a targeted inspection") + "."
    causes = Counter(r["cause_category"] for r in recs if r["cause_category"])
    if stats["hipo"]:
        return "Escalate the high-potential event(s) at %s and conduct a targeted inspection." % name
    if causes:
        return "%s at %s." % (CAUSE_RECOMMENDATION.get(causes.most_common(1)[0][0], "Conduct a targeted inspection"), name)
    return "Conduct a targeted inspection at %s and increase supervision." % name


def _ai_prediction(entity_type, entity_id, name, recs, open_a, overdue_a, prev_count, period_label, window_days):
    score, comp, stats = _ai_score(recs, open_a, overdue_a, prev_count)
    level = ai_risk_level(score)
    conf = _ai_confidence(stats["n"], window_days)
    return {
        "prediction_id": "PRD-%s-%s" % (entity_type[:3].upper(), str(entity_id)),
        "prediction_type": "%s_risk" % entity_type, "entity_type": entity_type,
        "entity_id": entity_id, "entity_name": name, "risk_score": score, "risk_level": level,
        "contributing_factors": _ai_factors(name, level, stats, period_label),
        "components": comp, "stats": stats,
        "recommended_action": _ai_recommend(entity_type, name, recs, stats),
        "confidence_score": conf, "confidence_label": ai_confidence_label(conf),
        "prediction_period": period_label, "generated_date": now_iso(),
    }


def _entity_actions(entity_type, key, today_iso):
    if entity_type == "location":
        pool = [a for a in DB["corrective_actions"] if a.get("location") == key and a["status"] == "open"]
    elif entity_type == "department":
        pool = [a for a in DB["corrective_actions"] if a["dept_key"] == key and a["status"] == "open"]
    else:
        pool = []
    overdue = sum(1 for a in pool if a.get("due") and a["due"] < today_iso)
    return len(pool), overdue


def _ai_records(yr, mo, week, dept, location, contractor, equipment, activity, free):
    member_ids = None
    if contractor and str(contractor).isdigit():
        member_ids = {u["id"] for u in DB["users"] if u.get("company_id") == int(contractor)}
    out = []
    for r in _norm_reports(year=yr, month=mo, dept=dept, location=location, free=free):
        if r["rec"].get("status") == "rejected":
            continue
        if week and week_in_month(r["ts"]) != week:
            continue
        rec = r["rec"]
        if equipment and rec.get("equipment_involved") != equipment:
            continue
        if activity and rec.get("activity") != activity:
            continue
        if member_ids is not None and r["reporter_id"] not in member_ids:
            continue
        out.append(r)
    return out


def ai_overdue_risk():
    """Open corrective actions that are overdue or due within 7 days."""
    t = today()
    today_iso = t.isoformat()
    soon = (t + timedelta(days=7)).isoformat()
    out = []
    for a in DB["corrective_actions"]:
        if a["status"] != "open" or not a.get("due"):
            continue
        if a["due"] < today_iso:
            risk = "Overdue"
        elif a["due"] <= soon:
            risk = "At risk"
        else:
            continue
        out.append({"id": a["id"], "dept_key": a["dept_key"], "location": a.get("location", ""),
                    "owner_id": a.get("owner_id"), "due": a["due"], "description": a.get("description", ""),
                    "risk": risk})
    out.sort(key=lambda a: (0 if a["risk"] == "Overdue" else 1, a["due"]))
    return out


def ai_predict(year=None, month=None, period="month", week=None, dept=None, location=None,
               contractor=None, equipment=None, activity=None, free=True):
    """Generate explainable, rule-based risk predictions from approved records."""
    t = today()
    yr = year or t.year
    mo = month or t.month
    if period == "week":
        if not week:
            week = week_in_month(t) if (yr == t.year and mo == t.month) else 1
        period_label = "Week %d in %s %d" % (week, month_name(mo), yr)
        window_days = 7
    else:
        week = None
        period_label = "%s %d" % (month_name(mo), yr)
        window_days = 30

    recs = _ai_records(yr, mo, week, dept, location, contractor, equipment, activity, free)

    # Minimum-data guard (do not show a misleading prediction).
    if len(recs) < AI_MIN_RECORDS or _activity_span_days() < AI_MIN_DAYS:
        return {"ok": False, "period_label": period_label, "have": len(recs), "need": AI_MIN_RECORDS,
                "message": "More safety records are required before a reliable prediction can be generated."}

    if period == "week":
        prev = _ai_records(yr, mo, week - 1, dept, location, contractor, equipment, activity, free) if week and week > 1 else []
    else:
        pmo = mo - 1 if mo > 1 else 12
        pyr = yr if mo > 1 else yr - 1
        prev = _ai_records(pyr, pmo, None, dept, location, contractor, equipment, activity, free)

    today_iso = t.isoformat()

    def prev_counts(keyfn):
        c = Counter()
        for r in prev:
            k = keyfn(r)
            if k:
                c[k] += 1
        return c

    def build(entity_type, keyfn, namefn):
        groups = {}
        for r in recs:
            k = keyfn(r)
            if k:
                groups.setdefault(k, []).append(r)
        pc = prev_counts(keyfn)
        preds = []
        for k, rs in groups.items():
            if len(rs) < AI_MIN_ENTITY:
                continue
            open_a, overdue_a = _entity_actions(entity_type, k, today_iso)
            preds.append(_ai_prediction(entity_type, k, namefn(k), rs, open_a, overdue_a,
                                        pc.get(k, 0), period_label, window_days))
        preds.sort(key=lambda p: (-p["risk_score"], p["entity_name"]))
        return preds

    locations = build("location", lambda r: r["location"], lambda k: k)
    departments = build("department", lambda r: r["dept_key"], lambda k: dept_name(k))
    equipment_p = build("equipment", lambda r: r["rec"].get("equipment_involved"), lambda k: k)
    activities = build("activity", lambda r: r["rec"].get("activity"), lambda k: k)
    causes = build("cause", lambda r: r["cause_category"], lambda k: k)

    # Contractors (group by reporting user's company).
    cby, cprev = {}, Counter()
    for r in recs:
        u = user(r["reporter_id"])
        if u and u.get("company_id"):
            cby.setdefault(u["company_id"], []).append(r)
    for r in prev:
        u = user(r["reporter_id"])
        if u and u.get("company_id"):
            cprev[u["company_id"]] += 1
    contractors = []
    for cid, rs in cby.items():
        if len(rs) < AI_MIN_ENTITY:
            continue
        members = {u["id"] for u in DB["users"] if u.get("company_id") == cid}
        open_a = sum(1 for a in DB["corrective_actions"] if a.get("owner_id") in members and a["status"] == "open")
        overdue_a = sum(1 for a in DB["corrective_actions"] if a.get("owner_id") in members
                        and a["status"] == "open" and a.get("due") and a["due"] < today_iso)
        contractors.append(_ai_prediction("contractor", cid, (company(cid) or {}).get("name", str(cid)),
                                           rs, open_a, overdue_a, cprev.get(cid, 0), period_label, window_days))
    contractors.sort(key=lambda p: -p["risk_score"])

    all_open = sum(1 for a in DB["corrective_actions"] if a["status"] == "open")
    all_overdue = sum(1 for a in DB["corrective_actions"] if a["status"] == "open"
                      and a.get("due") and a["due"] < today_iso)
    overall = _ai_prediction("overall", "all", "Overall operations", recs, all_open, all_overdue,
                             len(prev), period_label, window_days)

    everything = locations + departments + equipment_p + activities + causes + contractors
    top = max(everything, key=lambda p: p["risk_score"]) if everything else overall
    repeat_hazards = [p for p in (locations + equipment_p) if p["stats"]["repeat"] >= 1][:5]

    return {
        "ok": True, "period_label": period_label, "period": period, "overall": overall,
        "locations": locations, "departments": departments, "equipment": equipment_p,
        "activities": activities, "causes": causes, "contractors": contractors,
        "overdue_actions": ai_overdue_risk(), "repeat_hazards": repeat_hazards,
        "top": top, "generated_date": now_iso(),
    }
