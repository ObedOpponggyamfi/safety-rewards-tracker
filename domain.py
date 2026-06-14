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
# 8 RBAC roles. The Reward Administrator role is removed: HSE is the final
# authority for points, and reward requests go directly to Finance.
ROLE_LABELS = {
    "worker": "Worker / Employee",
    "champion": "Department Safety Champion",
    "supervisor": "Supervisor",
    "hse_officer": "HSE Officer",
    "hse_manager": "HSE Manager",
    "finance_manager": "Finance Approver",
    "management": "Management",
    "admin": "System Administrator",
}
ROLE_ORDER = ["worker", "champion", "supervisor", "hse_officer", "hse_manager",
              "finance_manager", "management", "admin"]
ROLE_GROUP_ORDER = {
    "Overview": 10,
    "Worker": 20,
    "Champion": 30,
    "Supervisor": 40,
    "HSE": 50,
    "Finance": 60,
    "Recognition": 70,
    "Management": 80,
    "System": 90,
}

# Demo login may pick any seeded account; in production this is off and a user
# can only act as roles explicitly assigned to them.
DEMO_MODE = True

# Permission catalogue (section 9 of the RBAC spec).
PERMISSIONS = [
    "hid_request.create", "hid_request.view_own", "hid.create_for_employee", "hid.verify",
    "hid.approve", "hid.reject", "points.process_automatic", "points.adjust_authorised",
    "points.adjust_request", "points.adjust_approve", "notification.view_own",
    "incident.create", "incident.edit", "incident.approve", "audit.create",
    "investigation.create", "investigation.approve", "action.assign", "action.update_assigned",
    "action.verify", "lti.reset", "reward.view_eligibility", "reward.request",
    "reward.auto_release", "reward.finance_approve", "reward.finance_reject", "reward.release",
    "reward.defer", "reward.budget_hold", "budget.view", "budget.create", "budget.approve",
    "report.view_department", "report.view_company", "hse.module", "user.manage", "role.manage",
]

_BASE = {"notification.view_own"}
_EARN = _BASE | {"reward.view_eligibility", "reward.request", "hid_request.create", "hid_request.view_own"}
_HSE_CREATE = {"incident.create", "incident.edit", "audit.create", "investigation.create",
               "action.assign", "action.update_assigned", "action.verify", "hid.verify",
               "report.view_department", "hse.module"}

# role -> set of permissions. Users may hold several roles; perms are the union.
ROLE_PERMISSIONS = {
    "worker": set(_EARN),
    "champion": _EARN | {"hid.create_for_employee", "report.view_department"},
    "supervisor": _EARN | {"hid.verify", "action.update_assigned", "report.view_department", "hse.module"},
    "hse_officer": _BASE | set(_HSE_CREATE) | {"points.adjust_request"},
    "hse_manager": _HSE_CREATE | {"hid.approve", "hid.reject", "incident.approve",
        "investigation.approve", "points.process_automatic", "points.adjust_authorised",
        "points.adjust_request", "points.adjust_approve", "lti.reset", "report.view_company",
        "notification.view_own"},
    "finance_manager": {"reward.finance_approve", "reward.finance_reject", "reward.release",
        "reward.defer", "reward.budget_hold", "budget.view", "notification.view_own"},
    "management": {"report.view_department", "report.view_company", "budget.view",
        "budget.create", "budget.approve", "hse.module", "notification.view_own"},
    "admin": {"user.manage", "role.manage", "notification.view_own"},
}

NAV_ITEMS = [
    {"group": "Overview", "label": "My Dashboard", "route": "/", "icon": "home",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 10},
    {"group": "Overview", "label": "Upgrade to Pro", "route": "/pro", "icon": "lock",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 90},
    {"group": "Overview", "label": "Notifications", "route": "/notifications", "icon": "bell",
     "required_permission": "notification.view_own", "allowed_roles": ROLE_ORDER, "display_order": 80},

    {"group": "Worker", "label": "Submit HID Request", "route": "/hid/request", "icon": "plus",
     "required_permission": "hid_request.create", "allowed_roles": ["worker", "champion"], "display_order": 10},
    {"group": "Worker", "label": "My HID Requests", "route": "/hid/requests", "icon": "list",
     "required_permission": "hid_request.view_own", "allowed_roles": ["worker", "champion"], "display_order": 20},
    {"group": "Worker", "label": "My Safety Points", "route": "/points", "icon": "star",
     "required_permission": "reward.view_eligibility", "allowed_roles": ["worker", "champion"], "display_order": 30},
    {"group": "Worker", "label": "Available Rewards", "route": "/rewards", "icon": "gift",
     "required_permission": "reward.view_eligibility", "allowed_roles": ["worker", "champion"], "display_order": 40},

    {"group": "Champion", "label": "Champion Dashboard", "route": "/champion", "icon": "shield",
     "required_permission": "hid.create_for_employee", "allowed_roles": ["champion"], "display_order": 10},
    {"group": "Champion", "label": "Employee Search", "route": "/champion/employees", "icon": "search",
     "required_permission": "hid.create_for_employee", "allowed_roles": ["champion"], "display_order": 15},
    {"group": "Champion", "label": "Pending Verification", "route": "/champion/hid-requests", "icon": "clipboard",
     "required_permission": "hid.create_for_employee", "allowed_roles": ["champion"], "display_order": 20},
    {"group": "Champion", "label": "Create HID for Employee", "route": "/report/hid", "icon": "edit",
     "required_permission": "hid.create_for_employee", "allowed_roles": ["champion"], "display_order": 30},

    {"group": "Supervisor", "label": "HID Verification Queue", "route": "/review", "icon": "check",
     "required_permission": "hid.verify", "allowed_roles": ["supervisor"], "display_order": 10},
    {"group": "Supervisor", "label": "Assigned Corrective Actions", "route": "/actions", "icon": "wrench",
     "required_permission": "action.update_assigned", "allowed_roles": ["supervisor"], "display_order": 20},

    {"group": "HSE", "label": "HID Review and Approval", "route": "/review", "icon": "check-circle",
     "required_permission": "hid.approve", "allowed_roles": ["hse_manager"], "display_order": 10},
    {"group": "HSE", "label": "Point Adjustments", "route": "/points/adjustments", "icon": "plus-minus",
     "required_permission": "points.adjust_request", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 15},
    {"group": "HSE", "label": "Safety Observations", "route": "/report/observation", "icon": "eye",
     "required_permission": "hse.module", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 20},
    {"group": "HSE", "label": "Near Miss / HID", "route": "/report/hid", "icon": "alert",
     "required_permission": "hse.module", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 30},
    {"group": "HSE", "label": "Incident Report", "route": "/report/incident", "icon": "alert-triangle",
     "required_permission": "incident.create", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 40},
    {"group": "HSE", "label": "Property Damage", "route": "/damage", "icon": "tool",
     "required_permission": "incident.create", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 50},
    {"group": "HSE", "label": "Location Hotspots", "route": "/hotspots", "icon": "map",
     "required_permission": "hse.module", "allowed_roles": ["supervisor", "hse_officer", "hse_manager", "management"], "display_order": 60},
    {"group": "HSE", "label": "High-Potential Events", "route": "/highpotential", "icon": "zap",
     "required_permission": "hse.module", "allowed_roles": ["hse_officer", "hse_manager", "management"], "display_order": 70},
    {"group": "HSE", "label": "AI Safety Prediction", "route": "/ai", "icon": "activity",
     "required_permission": "hse.module", "allowed_roles": ["supervisor", "hse_officer", "hse_manager", "management"], "display_order": 80},
    {"group": "HSE", "label": "Data Quality Review", "route": "/quality", "icon": "database",
     "required_permission": "hse.module", "allowed_roles": ["hse_officer", "hse_manager"], "display_order": 90},
    {"group": "HSE", "label": "Dept & Contractor Summary", "route": "/summary", "icon": "bar-chart",
     "required_permission": "report.view_department", "allowed_roles": ["champion", "supervisor", "hse_officer", "hse_manager", "management"], "display_order": 100},

    {"group": "Finance", "label": "Awaiting Finance Approval", "route": "/rewards/releases", "icon": "wallet",
     "required_permission": "reward.finance_approve", "allowed_roles": ["finance_manager"], "display_order": 10},
    {"group": "Finance", "label": "Reward Budget", "route": "/budgets", "icon": "banknote",
     "required_permission": "budget.view", "allowed_roles": ["finance_manager"], "display_order": 20},

    {"group": "Recognition", "label": "Leaderboards", "route": "/leaderboard", "icon": "trophy",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 10},
    {"group": "Recognition", "label": "Weekly Rewards", "route": "/weekly", "icon": "calendar",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 20},
    {"group": "Recognition", "label": "Adinkra Identity", "route": "/adinkra", "icon": "sparkles",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 30},
    {"group": "Recognition", "label": "Adinkra League", "route": "/league", "icon": "flag",
     "required_permission": None, "allowed_roles": ROLE_ORDER, "display_order": 40},

    {"group": "Management", "label": "Monthly Reports", "route": "/reports", "icon": "file-text",
     "required_permission": "report.view_department", "allowed_roles": ["champion", "supervisor", "hse_officer", "hse_manager", "management", "finance_manager"], "display_order": 10},
    {"group": "Management", "label": "Reward Budgets", "route": "/budgets", "icon": "banknote",
     "required_permission": "budget.view", "allowed_roles": ["hse_manager", "management"], "display_order": 20},

    {"group": "System", "label": "User Management", "route": "/admin", "icon": "users",
     "required_permission": "user.manage", "allowed_roles": ["admin"], "display_order": 10},
]

# Legacy role-set constants kept for a few inline checks; membership reflects the
# new model (no Reward Administrator; admin is config-only).
REVIEW_ROLES = {"supervisor", "hse_officer", "hse_manager"}
REWARD_RELEASE_ROLES = {"finance_manager"}
REPORTS_ROLES = {"champion", "supervisor", "hse_officer", "hse_manager", "management", "finance_manager"}
BUDGET_VIEW_ROLES = {"hse_manager", "management", "finance_manager"}
BUDGET_EDIT_ROLES = {"management"}


def role_label(role):
    return ROLE_LABELS.get(role, role.title())


def user_roles(user):
    if not user:
        return []
    return user.get("roles") or ([user["role"]] if user.get("role") else [])


def has_role(user, role):
    return role in user_roles(user)


def has_any_role(user, roles):
    return any(r in roles for r in user_roles(user))


def user_perms(user):
    perms = set()
    for r in user_roles(user):
        perms |= ROLE_PERMISSIONS.get(r, set())
    return perms


def has_perm(user, perm):
    return perm in user_perms(user)


def user_role_label(user):
    roles = user_roles(user)
    return " + ".join(role_label(r) for r in roles) if roles else ""


def can_access_department(user, dept_key):
    if has_perm(user, "report.view_company"):
        return True
    return bool(user and user.get("dept_key") == dept_key)


def can_view_budget(user):
    return has_perm(user, "budget.view")


def can_edit_budget(user):
    return has_perm(user, "budget.create")


def nav_for(user):
    roles = set(user_roles(user))
    groups = {}
    for item in NAV_ITEMS:
        allowed = set(item.get("allowed_roles") or ROLE_ORDER)
        if allowed and not (roles & allowed):
            continue
        perm = item.get("required_permission")
        if perm and not has_perm(user, perm):
            continue
        groups.setdefault(item["group"], []).append(item)
    ordered = []
    for group, items in groups.items():
        items.sort(key=lambda it: (it["display_order"], it["label"]))
        ordered.append((group, items))
    ordered.sort(key=lambda pair: (ROLE_GROUP_ORDER.get(pair[0], 999), pair[0]))
    return ordered


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
VIOLATION_PENALTY = 30  # points deducted when HSE confirms a violation

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# --------------------------------------------------------------------------
# Plan tiering (this build is the Free Version)
# --------------------------------------------------------------------------
PLAN = "Free"
FREE_LIMITS = {
    "company": 1, "site": 1, "locations": 5, "departments": 11,
    "contractors": 33, "employees": 700, "champions": 55, "champions_per_dept": 5,
    "records_per_month": 100, "history_days": 90,
}

INVALID_CONTRACTOR_NAMES = {"not stated", "n/a", "asanko"}

OFFICIAL_DEPARTMENTS = [
    {"id": 1, "key": "mining", "department": "Mining", "employee_count": 70},
    {"id": 2, "key": "technical_services", "department": "Technical Services", "employee_count": 15},
    {"id": 3, "key": "processing", "department": "Processing", "employee_count": 230},
    {"id": 4, "key": "exploration", "department": "Exploration", "employee_count": 20},
    {"id": 5, "key": "environment_sustainability", "department": "Environment & Sustainability", "employee_count": 30},
    {"id": 6, "key": "ohs", "department": "Occupational Health & Safety", "employee_count": 18},
    {"id": 7, "key": "asset_protection", "department": "Asset Protection", "employee_count": 30},
    {"id": 8, "key": "finance_supply_chain", "department": "Finance and Supply Chain", "employee_count": 30},
    {"id": 9, "key": "organizational_capability", "department": "Organizational Capability", "employee_count": 20},
    {"id": 10, "key": "external_relations_social_responsibility", "department": "External Relations & Social Responsibility", "employee_count": 20},
    {"id": 11, "key": "site_management", "department": "Site Management", "employee_count": 10},
]

OLD_DEPARTMENT_KEY_MAP = {
    "adinkrahene": "mining",
    "nyansapo": "processing",
    "fihankra": "ohs",
    "akoben": "asset_protection",
    "eban": "site_management",
    "sankofa": "organizational_capability",
    "nkonsonkonson": "finance_supply_chain",
    "dwennimmen": "technical_services",
}

CONTRACTOR_INCIDENT_HISTORY = {
    "PW", "THONKET", "NAFHAS", "KPS", "Kanu Equipment", "Mobi Crane",
    "RABOTEC", "KASLIVE", "ROCKSURE", "G5",
}

CONTRACTOR_MASTER_SEED = [
    ("AEL", "AEL", "Blasting services", "mining", 8),
    ("DRA", "DRA", "Engineering projects", "technical_services", 12),
    ("AKL", "AKL", "Civil works", "mining", 5),
    ("Andy Best", "ANDY", "Transport services", "site_management", 7),
    ("Ariester", "ARIE", "Construction support", "site_management", 6),
    ("Bavad", "BAV", "Facilities services", "environment_sustainability", 5),
    ("BLOJ", "BLOJ", "Logistics support", "finance_supply_chain", 4),
    ("COFKANS", "COF", "Camp and catering services", "organizational_capability", 5),
    ("DMA", "DMA", "Exploration drilling", "exploration", 8),
    ("Erduk", "ERD", "Maintenance services", "processing", 6),
    ("Esjef", "ESJ", "Maintenance services", "processing", 5),
    ("G5", "G5", "Security services", "asset_protection", 12),
    ("GEOGRILL", "GEO", "Exploration drilling", "exploration", 8),
    ("Kanu Equipment", "KANU", "Equipment support", "mining", 14),
    ("Kapmoh", "KAP", "Civil works", "site_management", 5),
    ("KASLIVE", "KAS", "Transport services", "mining", 10),
    ("KPS", "KPS", "Processing support", "processing", 12),
    ("Mobi Crane", "MOBI", "Lifting operations", "technical_services", 9),
    ("MODU", "MODU", "Site services", "site_management", 6),
    ("MOORE ENGINEERING", "MOORE", "Engineering support", "technical_services", 8),
    ("NAFHAS", "NAF", "Transport services", "mining", 12),
    ("Oman GH", "OMGH", "Civil works", "site_management", 6),
    ("Omanbapa", "OMAN", "Civil works", "site_management", 6),
    ("PW", "PW", "Mining contractor", "mining", 18),
    ("PX EQUIPMENT", "PX", "Equipment support", "mining", 8),
    ("RABOTEC", "RAB", "Processing maintenance", "processing", 12),
    ("RIEEMY2K", "RIE", "Labour support", "organizational_capability", 7),
    ("ROCKSURE", "ROCK", "Mining contractor", "mining", 16),
    ("SAHARA", "SAH", "Fuel and lubricants", "finance_supply_chain", 6),
    ("SOLAR NITRO", "SOL", "Blasting services", "mining", 7),
    ("THONKET", "THO", "Haulage services", "mining", 12),
    ("WTS", "WTS", "Water treatment services", "environment_sustainability", 6),
    ("ZEN", "ZEN", "Supply services", "finance_supply_chain", 5),
]

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


def _norm_text(value):
    return str(value or "").strip().casefold()


def _contractor_name_is_valid(name):
    return _norm_text(name) not in INVALID_CONTRACTOR_NAMES


def official_department_rows():
    """Official Safety Pays department master with attached Adinkra identity."""
    symbols = adinkra.DEPARTMENTS
    rows = []
    for meta in OFFICIAL_DEPARTMENTS:
        symbol = symbols[(meta["id"] - 1) % len(symbols)]
        rows.append({
            "id": meta["id"],
            "key": meta["key"],
            "adinkra_name": symbol["adinkra_name"],
            "department": meta["department"],
            "meaning": symbol["meaning"],
            "motto": symbol["motto"],
            "commons_file": symbol["commons_file"],
            "employee_count": meta["employee_count"],
            "active_employees": meta["employee_count"],
            "official_headcount": meta["employee_count"],
        })
    return rows


def official_contractor_rows():
    rows = []
    for idx, (name, code, category, dept_key, workforce) in enumerate(CONTRACTOR_MASTER_SEED, start=1):
        has_history = name in CONTRACTOR_INCIDENT_HISTORY
        start_month = ((idx - 1) % 12) + 1
        rows.append({
            "id": idx,
            "name": name,
            "contractor_id": idx,
            "contractor_name": name,
            "contractor_code": code,
            "service_category": category,
            "responsible_department": dept_key,
            "contract_start_date": date(2024, start_month, 1).isoformat(),
            "contract_end_date": date(2027, start_month, 28).isoformat(),
            "active_workforce_count": workforce,
            "contract_owner": "%s Manager" % dept_master_name(dept_key),
            "status": "Active",
            "has_historical_incidents": has_history,
            "incident_count": (idx % 4 + 1) if has_history else 0,
            "notes": "Historical incidents on record." if has_history else "",
        })
    return rows


def dept_master_name(key):
    meta = next((d for d in OFFICIAL_DEPARTMENTS if d["key"] == key), None)
    return meta["department"] if meta else key


def department_key_from_value(value, db=None, default=None):
    val = str(value or "").strip()
    if not val:
        return default
    low = val.casefold()
    if low in OLD_DEPARTMENT_KEY_MAP:
        return OLD_DEPARTMENT_KEY_MAP[low]
    rows = (db or DB).get("departments", []) if isinstance(db or DB, dict) else []
    official = {d["key"]: d for d in official_department_rows()}
    for key in official:
        if low == key.casefold():
            return key
    for d in list(rows) + list(official.values()):
        choices = [d.get("key"), d.get("department"), d.get("adinkra_name"), str(d.get("id", ""))]
        if any(low == str(choice or "").strip().casefold() for choice in choices):
            return d.get("key")
    return default


def valid_contractors(db=None):
    source = (db or DB).get("companies", []) if isinstance(db or DB, dict) else []
    rows = []
    for c in source:
        name = c.get("contractor_name") or c.get("name")
        if _contractor_name_is_valid(name):
            rows.append(c)
    return rows


def contractor_id_from_value(value, db=None, default=None):
    val = str(value or "").strip()
    if not val:
        return default
    low = val.casefold()
    for c in valid_contractors(db):
        choices = [
            c.get("id"), c.get("contractor_id"), c.get("contractor_name"),
            c.get("name"), c.get("contractor_code"),
        ]
        if any(low == str(choice or "").strip().casefold() for choice in choices):
            return c.get("id") or c.get("contractor_id")
    return default


def employee_by_employee_id(employee_id, db=None):
    low = _norm_text(employee_id)
    if not low:
        return None
    for u in (db or DB).get("users", []):
        if _norm_text(u.get("employee_id")) == low:
            return u
    return None


def employee_display_id(u):
    return u.get("employee_id") or ("#%s" % u.get("id", ""))


def next_employee_id(prefix="EMP", db=None):
    source = db or DB
    prefix = (prefix or "EMP").upper()
    seen = {_norm_text(u.get("employee_id")) for u in source.get("users", []) if u.get("employee_id")}
    n = 1
    for u in source.get("users", []):
        eid = str(u.get("employee_id") or "")
        if eid.upper().startswith(prefix):
            try:
                n = max(n, int(eid[len(prefix):]) + 1)
            except ValueError:
                continue
    return _unique_employee_id(prefix, seen, n)


def _unique_employee_id(prefix, seen, seed_number=1):
    n = max(1, int(seed_number or 1))
    while True:
        candidate = "%s%05d" % (prefix, n)
        low = candidate.casefold()
        if low not in seen:
            seen.add(low)
            return candidate
        n += 1


def _user_template(uid, name, role, title, dept_key, company_id=None,
                   is_contractor=False, active=True, roles=None, employee_id=None):
    employment_type = "Contractor" if is_contractor else "Internal"
    status = "Active" if active else "Inactive"
    return {
        "id": uid,
        "employee_id": employee_id or ("%s%05d" % ("CTR" if is_contractor else "EMP", uid)),
        "full_name": name,
        "name": name,
        "employment_type": employment_type,
        "department_id": dept_key,
        "dept_key": dept_key,
        "contractor_id": company_id if is_contractor else None,
        "company_id": company_id if is_contractor else None,
        "job_title": title,
        "title": title,
        "supervisor_id": None,
        "safety_champion_id": None,
        "shift": "Day",
        "site": "Asanko Gold Mine",
        "email": "",
        "phone": "",
        "status": status,
        "role": role,
        "roles": roles or [role],
        "is_contractor": is_contractor,
        "active": active,
        "is_champion": bool(roles and "champion" in roles) or role == "champion",
    }


def _assign_department_support(db):
    """Populate supervisor/champion links inside each department."""
    for dept in db.get("departments", []):
        dept_key = dept["key"]
        supervisor = next((u for u in db.get("users", [])
                           if u.get("dept_key") == dept_key and has_role(u, "supervisor")), None)
        champion = next((u for u in db.get("users", [])
                         if u.get("dept_key") == dept_key and has_role(u, "champion")), None)
        for u in db.get("users", []):
            if u.get("dept_key") != dept_key:
                continue
            if supervisor and u.get("id") != supervisor.get("id") and not u.get("supervisor_id"):
                u["supervisor_id"] = supervisor["id"]
            if champion and u.get("id") != champion.get("id") and not u.get("safety_champion_id"):
                u["safety_champion_id"] = champion["id"]


def _refresh_department_counts(db):
    for dept in db.get("departments", []):
        official = next((d for d in OFFICIAL_DEPARTMENTS if d["key"] == dept["key"]), None)
        if official:
            dept["employee_count"] = official["employee_count"]
            dept["official_headcount"] = official["employee_count"]
        active_internal = sum(
            1 for u in db.get("users", [])
            if u.get("dept_key") == dept["key"] and not u.get("is_contractor")
            and u.get("active", True)
        )
        dept["active_employees"] = max(active_internal, min(dept.get("employee_count", 0), active_internal))


def _migrate_department_keys(db):
    changed = False

    def mapped(value):
        new_key = department_key_from_value(value, db=db, default=value)
        return new_key or value

    collections = (
        "users", "safety_observations", "near_miss_hazard_reports", "incidents",
        "corrective_actions", "safety_points", "point_reset_events", "reward_requests",
        "property_damage", "worker_hid_requests", "department_access",
    )
    for coll in collections:
        for rec in db.get(coll, []):
            for field in ("dept_key", "department_id"):
                if field in rec:
                    new_key = mapped(rec.get(field))
                    if rec.get(field) != new_key:
                        rec[field] = new_key
                        changed = True
    return changed


def _ensure_master_data(db):
    changed = False
    if _migrate_department_keys(db):
        changed = True
    wanted_depts = official_department_rows()
    if db.get("departments") != wanted_depts:
        db["departments"] = wanted_depts
        changed = True
    wanted_companies = official_contractor_rows()
    current_names = {_norm_text(c.get("contractor_name") or c.get("name")) for c in db.get("companies", [])}
    current_names -= INVALID_CONTRACTOR_NAMES
    wanted_names = {_norm_text(c["contractor_name"]) for c in wanted_companies}
    if current_names != wanted_names or len(db.get("companies", [])) != len(wanted_companies):
        db["companies"] = wanted_companies
        changed = True
    else:
        by_name = {_norm_text(c.get("contractor_name") or c.get("name")): c for c in db.get("companies", [])}
        merged = []
        for row in wanted_companies:
            old = by_name.get(_norm_text(row["contractor_name"]), {})
            merged_row = dict(row)
            for field in ("contract_start_date", "contract_end_date", "active_workforce_count",
                          "contract_owner", "status", "incident_count", "notes"):
                if old.get(field) not in (None, ""):
                    merged_row[field] = old[field]
            merged.append(merged_row)
        if db.get("companies") != merged:
            db["companies"] = merged
            changed = True
    return changed


def _normalise_employee_fields(db):
    changed = False
    seen = set()
    official_default = db["departments"][0]["key"] if db.get("departments") else "mining"
    first_contractor = valid_contractors(db)[0]["id"] if valid_contractors(db) else None
    for u in db.get("users", []):
        dept_key = department_key_from_value(u.get("dept_key") or u.get("department_id"), db=db, default=official_default)
        if u.get("dept_key") != dept_key:
            u["dept_key"] = dept_key
            changed = True
        if u.get("department_id") != dept_key:
            u["department_id"] = dept_key
            changed = True
        is_contractor = bool(u.get("is_contractor") or u.get("employment_type") == "Contractor"
                             or u.get("company_id") or u.get("contractor_id"))
        contractor_id = contractor_id_from_value(u.get("contractor_id") or u.get("company_id"), db=db)
        if is_contractor and contractor_id is None:
            contractor_id = first_contractor
        if not is_contractor:
            contractor_id = None
        pairs = {
            "name": u.get("name") or u.get("full_name") or "Employee %s" % u.get("id", ""),
            "full_name": u.get("full_name") or u.get("name") or "Employee %s" % u.get("id", ""),
            "title": u.get("title") or u.get("job_title") or role_label(u.get("role", "worker")),
            "job_title": u.get("job_title") or u.get("title") or role_label(u.get("role", "worker")),
            "employment_type": "Contractor" if is_contractor else "Internal",
            "contractor_id": contractor_id,
            "company_id": contractor_id,
            "is_contractor": is_contractor,
            "active": bool(u.get("active", u.get("status", "Active") != "Inactive")),
            "status": "Active" if u.get("active", u.get("status", "Active") != "Inactive") else "Inactive",
            "shift": u.get("shift") or "Day",
            "site": u.get("site") or "Asanko Gold Mine",
            "email": u.get("email") or "",
            "phone": u.get("phone") or "",
            "supervisor_id": u.get("supervisor_id"),
            "safety_champion_id": u.get("safety_champion_id"),
        }
        for key, value in pairs.items():
            if u.get(key) != value:
                u[key] = value
                changed = True
        eid = str(u.get("employee_id") or "").strip()
        if not eid or eid.casefold() in seen:
            eid = _unique_employee_id("CTR" if is_contractor else "EMP", seen, u.get("id", 1))
            u["employee_id"] = eid
            changed = True
        else:
            seen.add(eid.casefold())
        if u.get("role") == "champion":
            roles = user_roles(u)
            if "worker" not in roles:
                roles.insert(0, "worker")
                u["roles"] = roles
                changed = True
    _assign_department_support(db)
    _refresh_department_counts(db)
    return changed


def _ensure_workforce_size(db):
    """Guarantee the company demo can carry a 700+ worker population."""
    changed = False
    rng = random.Random(20260614)
    first_names = ["Kwame", "Ama", "Kofi", "Akua", "Yaw", "Abena", "Kojo", "Adwoa",
                   "Kwabena", "Akosua", "Kwaku", "Afia", "Yaa", "Fiifi", "Esi",
                   "Nana", "Kwadwo", "Maa", "Kweku", "Adjoa"]
    last_names = ["Mensah", "Owusu", "Boateng", "Asante", "Agyeman", "Darko",
                  "Appiah", "Osei", "Annan", "Frimpong", "Tetteh", "Quartey",
                  "Addo", "Bediako", "Gyamfi"]
    job_titles = ["Operator", "Technician", "Safety Steward", "Artisan", "Field Assistant",
                  "Controller", "Maintainer", "Officer", "Assistant", "Coordinator"]
    shifts = ["Day", "Night", "A", "B"]
    seen = {_norm_text(u.get("employee_id")) for u in db.get("users", []) if u.get("employee_id")}
    next_uid = max([u.get("id", 0) for u in db.get("users", [])] or [0]) + 1

    def make_name(serial):
        return "%s %s" % (first_names[serial % len(first_names)], last_names[(serial * 3) % len(last_names)])

    def add_user(role, title, dept_key, company_id=None, is_contractor=False, roles=None):
        nonlocal next_uid, changed
        prefix = "CTR" if is_contractor else "EMP"
        employee_id = _unique_employee_id(prefix, seen, next_uid)
        u = _user_template(next_uid, make_name(next_uid), role, title, dept_key,
                           company_id=company_id, is_contractor=is_contractor,
                           roles=roles or [role], employee_id=employee_id)
        u["shift"] = rng.choice(shifts)
        u["email"] = "%s.%s@safetypays.demo" % (u["full_name"].split()[0].lower(), employee_id.lower())
        u["phone"] = "024%07d" % (1000000 + next_uid)
        db["users"].append(u)
        next_uid += 1
        changed = True
        return u

    for dept in db.get("departments", []):
        dept_key = dept["key"]
        if not any(u.get("dept_key") == dept_key and has_role(u, "champion") for u in db.get("users", [])):
            add_user("champion", "Department Safety Champion", dept_key, roles=["worker", "champion"])
        if not any(u.get("dept_key") == dept_key and has_role(u, "supervisor") for u in db.get("users", [])):
            add_user("supervisor", "Supervisor", dept_key, roles=["supervisor"])
        target = dept.get("official_headcount") or dept.get("employee_count", 0)
        internal_count = sum(1 for u in db.get("users", [])
                             if u.get("dept_key") == dept_key and not u.get("is_contractor"))
        while internal_count < target:
            add_user("worker", rng.choice(job_titles), dept_key, roles=["worker"])
            internal_count += 1

    for company in valid_contractors(db):
        target = int(company.get("active_workforce_count") or 0)
        dept_key = department_key_from_value(company.get("responsible_department"), db=db, default="mining")
        current = sum(1 for u in db.get("users", []) if u.get("company_id") == company["id"])
        while current < target:
            add_user("worker", "Contractor Worker", dept_key, company_id=company["id"],
                     is_contractor=True, roles=["worker"])
            current += 1

    _assign_department_support(db)
    _refresh_department_counts(db)
    return changed


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------


def load():
    global DB
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            DB = json.load(fh)
        if ensure_schema(DB):
            save()
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
    nums = [it.get("id", it.get("audit_id", 0)) for it in items]
    return (max(nums) + 1) if nums else 1


def record_audit(user, action, module, record_id=None, old_value=None, new_value=None,
                 ip_or_device_reference="local"):
    DB.setdefault("audit_logs", [])
    aid = next_id("audit_logs")
    DB["audit_logs"].append({
        "id": aid,
        "audit_id": aid,
        "user_id": user.get("id") if isinstance(user, dict) else user,
        "user_role": ",".join(user_roles(user)) if isinstance(user, dict) else "",
        "action": action,
        "module": module,
        "record_id": record_id,
        "old_value": old_value,
        "new_value": new_value,
        "timestamp": now_iso(),
        "ip_or_device_reference": ip_or_device_reference,
    })


def notify(user_id, title, message, link="", kind="info"):
    """Create an in-app notification for one user."""
    if not user_id:
        return None
    DB.setdefault("notifications", [])
    nid = next_id("notifications")
    note = {
        "id": nid,
        "user_id": user_id,
        "ts": now_iso(),
        "title": title,
        "message": message,
        "link": link,
        "kind": kind,
        "read": False,
    }
    DB["notifications"].append(note)
    return note


def notify_role(role, title, message, link="", kind="info", dept_key=None):
    sent = []
    for u in DB.get("users", []):
        if not has_role(u, role):
            continue
        if dept_key and u.get("dept_key") != dept_key:
            continue
        sent.append(notify(u["id"], title, message, link, kind))
    return sent


def ensure_schema(db):
    """Bring older JSON stores up to the current RBAC/workflow shape."""
    changed = False
    defaults = {
        "worker_hid_requests": [],
        "notifications": [],
        "point_adjustment_requests": [],
        "audit_logs": [],
        "roles": [],
        "permissions": [],
        "role_permissions": [],
        "user_roles": [],
        "department_access": [],
        "settings": {},
    }
    for key, value in defaults.items():
        if key not in db:
            db[key] = value.copy() if isinstance(value, list) else dict(value)
            changed = True
    if "hotspot_thresholds" not in db["settings"]:
        db["settings"]["hotspot_thresholds"] = dict(DEFAULT_HOTSPOT_THRESHOLDS)
        changed = True
    if "demo_mode" not in db["settings"]:
        db["settings"]["demo_mode"] = DEMO_MODE
        changed = True
    if _ensure_master_data(db):
        changed = True

    next_uid = (max([u.get("id", 0) for u in db.get("users", [])] or [0]) + 1)

    def add_user(role, title, dept_key=None):
        nonlocal next_uid, changed
        dept_key = dept_key or (db["departments"][0]["key"] if db.get("departments") else "")
        user_obj = _user_template(next_uid, "Demo %s" % role_label(role), role, title,
                                  dept_key, roles=[role])
        db["users"].append(user_obj)
        next_uid += 1
        changed = True
        return user_obj

    for u in db.get("users", []):
        old_role = u.get("role")
        if old_role == "contractor_admin":
            u["role"] = "worker"
            u["title"] = u.get("title") or "Contractor Worker"
            changed = True
        elif old_role not in ROLE_LABELS:
            u["role"] = "worker"
            changed = True
        roles = list(u.get("roles") or [u.get("role")])
        roles = [r for r in roles if r in ROLE_LABELS]
        if not roles:
            roles = [u.get("role", "worker")]
        if u.get("is_champion") and "champion" not in roles:
            roles.append("champion")
        if "champion" in roles:
            u["role"] = "champion"
            u["title"] = "Department Safety Champion"
        if u.get("role") not in roles:
            roles.insert(0, u["role"])
        if u.get("roles") != roles:
            u["roles"] = roles
            changed = True

    for role in ROLE_ORDER:
        if not any(role in user_roles(u) for u in db.get("users", [])):
            title = role_label(role)
            add_user(role, title)

    # Keep at least one champion per first two departments for demo coverage.
    for dept in db.get("departments", [])[:2]:
        champs = [u for u in db["users"]
                  if u.get("dept_key") == dept["key"] and "champion" in user_roles(u)]
        if not champs:
            worker = next((u for u in db["users"]
                           if u.get("dept_key") == dept["key"] and "worker" in user_roles(u)), None)
            if worker:
                roles = list(user_roles(worker))
                if "champion" not in roles:
                    roles.append("champion")
                worker["roles"] = roles
                worker["role"] = "champion"
                worker["title"] = "Department Safety Champion"
                worker["is_champion"] = True
                changed = True

    for rw in db.get("rewards", []):
        if "release_mode" not in rw:
            rw["release_mode"] = "automatic" if rw.get("name") == "Safety Champion Plaque" else "finance"
            changed = True
        if rw.get("release_mode") == "automatic" and rw.get("cash_value"):
            rw["cash_value"] = 0
            changed = True

    if _normalise_employee_fields(db):
        changed = True
    if _ensure_workforce_size(db):
        changed = True
    if _normalise_employee_fields(db):
        changed = True

    role_rows = [{"id": i + 1, "key": role, "label": role_label(role)} for i, role in enumerate(ROLE_ORDER)]
    if db.get("roles") != role_rows:
        db["roles"] = role_rows
        changed = True
    permission_rows = [{"id": i + 1, "key": perm} for i, perm in enumerate(PERMISSIONS)]
    if db.get("permissions") != permission_rows:
        db["permissions"] = permission_rows
        changed = True
    rp_rows = [{"role": role, "permission": perm}
               for role in ROLE_ORDER for perm in sorted(ROLE_PERMISSIONS.get(role, set()))]
    if db.get("role_permissions") != rp_rows:
        db["role_permissions"] = rp_rows
        changed = True
    ur_rows = [{"user_id": u["id"], "role": role}
               for u in db.get("users", []) for role in user_roles(u)]
    if db.get("user_roles") != ur_rows:
        db["user_roles"] = ur_rows
        changed = True
    da_rows = [{"user_id": u["id"], "dept_key": u.get("dept_key")}
               for u in db.get("users", []) if u.get("dept_key")]
    if db.get("department_access") != da_rows:
        db["department_access"] = da_rows
        changed = True

    if _migrate_reward_requests(db):
        changed = True
    if _seed_worker_hid_requests(db):
        changed = True
    if _seed_notifications(db):
        changed = True
    return changed


def _migrate_reward_requests(db):
    changed = False
    for r in db.get("reward_requests", []):
        st = r.get("status")
        if st == "pending_admin":
            r["status"] = "pending_finance"
            r["system_validation_status"] = "validated"
            changed = True
        elif st == "rejected":
            r["status"] = "finance_rejected"
            if not r.get("reject_stage"):
                r["reject_stage"] = "finance"
            changed = True
        r.setdefault("system_validation_status", "validated")
        r.setdefault("release_reference", None)
        r.setdefault("auto_release", False)
    return changed


def _seed_worker_hid_requests(db):
    if db.get("worker_hid_requests"):
        return False
    workers = [u for u in db.get("users", []) if "worker" in user_roles(u) and u.get("role") == "worker"]
    if not workers:
        return False
    for worker in workers[:3]:
        champ = next((u for u in db["users"]
                      if "champion" in user_roles(u) and u.get("dept_key") == worker.get("dept_key")), None)
        db["worker_hid_requests"].append({
            "id": len(db["worker_hid_requests"]) + 1,
            "request_id": len(db["worker_hid_requests"]) + 1,
            "employee_id": worker["id"],
            "department_id": worker.get("dept_key"),
            "champion_id": champ["id"] if champ else None,
            "location_id": "Process Plant",
            "hazard_summary": "Guarding concern near routine task area",
            "hazard_description": "Employee reported a hazard that needs champion review.",
            "photo_reference": "",
            "reported_date": today().isoformat(),
            "urgency": "Medium",
            "request_status": "Submitted",
            "converted_to_hid_id": None,
            "created_date": now_iso(),
        })
    return True


def _seed_notifications(db):
    if db.get("notifications"):
        return False
    for u in db.get("users", [])[:8]:
        db["notifications"].append({
            "id": len(db["notifications"]) + 1,
            "user_id": u["id"],
            "ts": now_iso(),
            "title": "Welcome to Safety Pays",
            "message": "Your sidebar is tailored to your assigned role permissions.",
            "link": "/",
            "kind": "info",
            "read": False,
        })
    return bool(db.get("notifications"))


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
        "worker_hid_requests": [],
        "notifications": [],
        "point_adjustment_requests": [],
        "audit_logs": [],
        "property_damage": [],
        "yearly_reward_budgets": [],
        "monthly_reward_budgets": [],
        "quarterly_reward_budgets": [],
        "roles": [],
        "permissions": [],
        "role_permissions": [],
        "user_roles": [],
        "department_access": [],
        "settings": {"hotspot_thresholds": dict(DEFAULT_HOTSPOT_THRESHOLDS), "demo_mode": DEMO_MODE},
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
        ("HSE Officer", "hse_officer", "fihankra"),
        ("HSE Manager", "hse_manager", "fihankra"),
        ("Management", "management", "adinkrahene"),
        ("Finance Approver", "finance_manager", "adinkrahene"),
        ("System Admin", "admin", "adinkrahene"),
    ]
    for title, role, dept_key in staff:
        db["users"].append({
            "id": uid, "name": make_name(), "role": role, "title": title,
            "dept_key": dept_key, "company_id": None, "is_contractor": False,
            "active": True, "roles": [role],
        })
        uid += 1

    # Each department gets one champion, one supervisor and several workers.
    for dept in db["departments"]:
        db["users"].append({
            "id": uid, "name": make_name(), "role": "champion",
            "title": "Department Safety Champion", "dept_key": dept["key"],
            "company_id": None, "is_contractor": False, "active": True,
            "is_champion": True, "roles": ["worker", "champion"],
        })
        uid += 1
        db["users"].append({
            "id": uid, "name": make_name(), "role": "supervisor",
            "title": "Supervisor", "dept_key": dept["key"], "company_id": None,
            "is_contractor": False, "active": True, "roles": ["supervisor"],
        })
        uid += 1
        for _ in range(rng.randint(4, 6)):
            db["users"].append({
                "id": uid, "name": make_name(), "role": "worker",
                "title": "Worker", "dept_key": dept["key"], "company_id": None,
                "is_contractor": False, "active": True, "roles": ["worker"],
            })
            uid += 1

    # Contractor workers are assigned to departments too.
    for comp in db["companies"]:
        db["users"].append({
            "id": uid, "name": make_name(), "role": "worker",
            "title": "Contractor Worker", "dept_key": rng.choice(db["departments"])["key"],
            "company_id": comp["id"], "is_contractor": True, "active": True, "roles": ["worker"],
        })
        uid += 1
        for _ in range(rng.randint(4, 6)):
            db["users"].append({
                "id": uid, "name": make_name(), "role": "worker",
                "title": "Contractor Worker",
                "dept_key": rng.choice(db["departments"])["key"],
                "company_id": comp["id"], "is_contractor": True, "active": True, "roles": ["worker"],
            })
            uid += 1

    workers = [u for u in db["users"] if "worker" in user_roles(u)]

    # ---- Rewards catalogue -------------------------------------------------
    db["rewards"] = [
        {"id": 1, "name": "Safety Boots Voucher", "description": "Voucher for a pair of certified safety boots.", "point_cost": 120, "cash_value": 180, "active": True, "release_mode": "finance"},
        {"id": 2, "name": "Fuel Voucher", "description": "GH₵100 fuel voucher.", "point_cost": 80, "cash_value": 100, "active": True},
        {"id": 3, "name": "Airtime / Data Bundle", "description": "Monthly airtime and data bundle.", "point_cost": 40, "cash_value": 50, "active": True, "release_mode": "finance"},
        {"id": 4, "name": "Branded Hard Hat", "description": "Premium branded hard hat.", "point_cost": 60, "cash_value": 75, "active": True, "release_mode": "finance"},
        {"id": 5, "name": "Grocery Hamper", "description": "Family grocery hamper.", "point_cost": 150, "cash_value": 220, "active": True, "release_mode": "finance"},
        {"id": 6, "name": "Safety Champion Plaque", "description": "Engraved recognition plaque.", "point_cost": 200, "cash_value": 0, "active": True, "release_mode": "automatic"},
        {"id": 7, "name": "Safety Recognition Badge", "description": "Non-financial recognition badge, released automatically.", "point_cost": 30, "cash_value": 0, "active": True, "release_mode": "automatic"},
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

    # ---- Department Safety Champions -------------------------------------
    # Champions are assigned deliberately per department by role, not by
    # random promotion, so department caps remain enforceable.

    # ---- Reward requests in the direct Finance workflow -------------------
    # employee request -> system validation/reservation -> finance -> release
    sample_workers = rng.sample(workers, 9)
    states = ["pending_finance", "pending_finance", "finance_approved", "released",
              "released", "finance_rejected", "budget_hold", "deferred_next_month",
              "deferred_next_quarter"]
    finance_uid = next(u["id"] for u in db["users"] if u["role"] == "finance_manager")
    rqid = 1
    for w, st in zip(sample_workers, states):
        reward = rng.choice(db["rewards"])
        d = anchor - timedelta(days=rng.randint(0, 18))
        base = datetime(d.year, d.month, d.day, 10, 0)
        ts = base.isoformat(timespec="seconds")
        fin_ts = base + timedelta(days=1)
        rel_ts = base + timedelta(days=2)
        rq = {
            "id": rqid, "ts": ts, "user_id": w["id"], "dept_key": w["dept_key"],
            "reward_id": reward["id"], "point_cost": reward["point_cost"],
            "cash_value": reward["cash_value"], "status": st,
            "system_validation_status": "validated",
            "admin_id": None, "admin_ts": None, "finance_id": None, "finance_ts": None,
            "released_by": None, "released_ts": None,
            "reject_reason": None, "rejected_by": None, "reject_stage": None, "rejected_ts": None,
            "release_reference": None, "auto_release": False,
        }
        if st in ("finance_approved", "released"):
            rq["finance_id"] = finance_uid
            rq["finance_ts"] = fin_ts.isoformat(timespec="seconds")
        if st == "released":
            rq["released_by"] = finance_uid
            rq["released_ts"] = rel_ts.isoformat(timespec="seconds")
            rq["release_reference"] = "FIN-%04d" % rqid
        if st == "finance_rejected":
            rq["finance_id"] = finance_uid
            rq["finance_ts"] = fin_ts.isoformat(timespec="seconds")
            rq["rejected_by"] = finance_uid
            rq["reject_stage"] = "finance"
            rq["reject_reason"] = "Budget or eligibility validation failed."
            rq["rejected_ts"] = fin_ts.isoformat(timespec="seconds")
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

    ensure_schema(db)
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
    norm_key = department_key_from_value(key, db=DB, default=key)
    return next((d for d in DB["departments"] if d["key"] == norm_key), None)


def reward(rid):
    return next((r for r in DB["rewards"] if r["id"] == rid), None)


def company(cid):
    wanted = str(cid or "").strip().casefold()
    for c in DB["companies"]:
        choices = [c.get("id"), c.get("contractor_id"), c.get("contractor_code"),
                   c.get("contractor_name"), c.get("name")]
        if any(wanted == str(choice or "").strip().casefold() for choice in choices):
            return c
    return None


def dept_name(key):
    d = department(key)
    return d.get("department") or d.get("adinkra_name") if d else key


def dept_department(key):
    """Adinkra identity attached to the operational department."""
    d = department(key)
    return ("Adinkra: %s" % d.get("adinkra_name")) if d and d.get("adinkra_name") else ""


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


RESERVED_REWARD_STATUSES = {"pending_finance", "finance_approved", "budget_hold",
                            "deferred_next_month", "deferred_next_quarter"}


def lifetime_points(uid):
    return sum(p["points"] for p in DB["safety_points"] if p["user_id"] == uid)


def released_points(uid):
    return sum(r["point_cost"] for r in DB["reward_requests"]
               if r["user_id"] == uid and r["status"] == "released")


def reserved_points(uid):
    return sum(r["point_cost"] for r in DB["reward_requests"]
               if r["user_id"] == uid and r["status"] in RESERVED_REWARD_STATUSES)


def rewardable_points(uid):
    return lifetime_points(uid) - released_points(uid)


def user_balance(uid):
    """Available points = rewardable points minus points reserved in pending requests."""
    return rewardable_points(uid) - reserved_points(uid)


def reward_budget_validation(dept_key, cash_value, year=None, month=None):
    """Return (ok, reason) for department, monthly, quarterly and yearly budget checks."""
    year = year or today().year
    month = month or today().month
    quarter = quarter_of_month(month)
    dept = department(dept_key)
    if dept and dept_budget_used(dept_key, year, month) + cash_value > dept_monthly_limit(dept):
        return False, "Department monthly reward limit would be exceeded."

    monthly = next((b for b in DB["monthly_reward_budgets"]
                    if b["year"] == year and b["month"] == month), None)
    if monthly and budget_used(year, month=month) + cash_value > monthly["amount"]:
        return False, "Monthly reward budget would be exceeded."

    quarterly = next((b for b in DB["quarterly_reward_budgets"]
                      if b["year"] == year and b["quarter"] == quarter), None)
    if quarterly and budget_used(year, quarter=quarter) + cash_value > quarterly["amount"]:
        return False, "Quarterly reward budget would be exceeded."

    yearly = next((b for b in DB["yearly_reward_budgets"] if b["year"] == year), None)
    if yearly and budget_used(year) + cash_value > yearly["amount"]:
        return False, "Annual reward budget would be exceeded."

    return True, "Validated."


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
        if not has_role(u, "worker"):
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
        if has_role(u, "worker") and u["is_contractor"] and u["company_id"] in agg:
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
