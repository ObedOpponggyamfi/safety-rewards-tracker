# Safety Rewards Tracker

**Reward Safety. Reduce Risk. Save Lives.**

A safety performance and rewards platform for mining, construction, factories,
logistics and contractor-heavy workplaces. Teams report safety observations,
hazards, near-misses and incidents; supervisors review and approve them; workers
earn safety points; rewards are requested, approved and released against budgets;
and departments compete in an **Adinkra League**.

Built as a **pure Python standard-library MVP** — no framework, no npm, no pip.
Server-rendered HTML/CSS with a JSON file store.

---

## Run

```bash
python app.py
```

Then open <http://localhost:8090>. That's it — no install step.

On Windows, double-click **`Open SafeReward App.bat`** from the project folder.
It starts the app with Python 3, opens the existing local app if the port is
already running, and keeps the server window visible while you use the app.

| Environment variable | Effect |
|---|---|
| `PORT` | Port to serve on (default `8090`). |
| `NO_BROWSER` | Set to any value to stop the browser auto-opening. |

On first run a demo dataset is seeded into `data/safetypays_data.json`. Delete that
file (or use **Admin Tools → Reset demo data**) to reseed a fresh demo.

---

## Demo login & roles

Pick any demo user on the sign-in screen — each carries a role:

- **Worker / Employee** — submit HID requests, view own points, request rewards
- **Department Safety Champion** — search department employees and convert worker HID requests into official HIDs
- **Supervisor** — verify department HIDs and assigned corrective actions
- **HSE Officer** — create and review operational HSE records
- **HSE Manager** — final HID approval/rejection, automatic point outcomes, manual point-adjustment approval, reports
- **Finance Approver** — approve, reject, hold, defer and release reward requests
- **Management** — company reporting and budget setup/approval
- **System Administrator** — users, roles, departments, champion assignment and settings

### Budget access control

> Budget visibility and editing are permission-based. Finance can view reward
> budgets, Management can set and approve budgets, and System Administrator does
> not automatically inherit Finance, HSE or budget approval permissions.

This is enforced both in the navigation and on every budget route.

---

## Key behaviours

- **Quarter is automatic from the month.** Pick a month anywhere (reports, budgets)
  and the quarter is derived live — Q1 = Jan–Mar, Q2 = Apr–Jun, Q3 = Jul–Sep,
  Q4 = Oct–Dec — with no separate quarter picker.
- **Weekly rewards use week-in-month, not week-in-year.** Periods read *Week 1 in
  June*, *Week 2 in June*, … (week = `(day − 1) // 7 + 1`).
- **Champion badges for the top three.** Every leaderboard shows 🏆 1st, 🥈 2nd,
  🥉 3rd, then `#4`, `#5`, …
- **Department reward limits are employee-based:**

  ```text
  Department Monthly Limit = Active Employees in Department × Budget Per Active Worker
  ```

  (Budget Per Active Worker defaults to GH₵75; Admins adjust employee counts in
  Admin Tools and limits recompute automatically.)
- **Incident → LTI reset.** Logging a Lost Time Injury resets the department's
  monthly safety points and records a `point_reset_event` for audit. Reset-aware
  totals power the monthly/quarterly/yearly leaderboards.
- **Official Safety Pays master data.** The demo now seeds the 11 official
  departments, a 33-company Contractor Master Register and a 700+ employee
  workforce structure.
- **Employee master tools.** Admin Tools support employee search, pagination,
  department/contractor/status filters, CSV or Excel-pasted imports, duplicate
  employee-ID validation and bulk department, supervisor and Safety Champion
  assignment.
- **Contractor rules.** Internal employees do not require a contractor company;
  contractor employees do. Invalid placeholder contractor names such as
  `Not Stated`, `N/A` and `ASANKO` are excluded from contractor options.

---

## Reward approval workflow

Workers request rewards using their **rewardable points** (spendable balance =
points earned − points already released). Each request runs a four-stage flow:

```text
Employee submits reward request
        ↓
System validates eligibility and reserves points
        ↓
Finance Approver approves / rejects / holds / defers
        ↓
Reward is released, or reserved points are restored on rejection
```

There is no Reward Administrator stage. Valid financial or physical reward
requests go directly to Finance after system validation. Non-financial rewards
configured as automatic can release immediately. Released rewards charge the
department, monthly, quarterly and yearly budgets; rejected Finance requests
restore reserved points.

## Monthly Reports Centre

`/reports` auto-generates a report for **every module** for the selected month
(the quarter is derived automatically). On-screen sections plus a full CSV export:

- Safety Observations (totals, status, by category)
- HID — Hazard reports (by severity)
- Near Misses (by severity)
- Incidents (by severity, LTI split)
- LTI (each Lost Time Injury and its point reset)
- Corrective Actions (opened / closed / open / overdue)
- Rewards (requests by workflow status, spend)
- Budget (monthly + auto-quarter usage)
- Departments (the Adinkra League snapshot)
- Contractors (points, members, reward spend)

---

## Modules

- Role-based dashboards
- Safety observation reporting
- Hazard / near-miss (HID) reporting
- Incident reporting with LTI reset trigger
- Supervisor review & approval queue
- Corrective action tracker (create, close, overdue flags)
- Safety points ledger
- Manual point-adjustment request and HSE Manager approval workflow
- Reward catalogue + worker request flow
- Reward approval workflow: submit → system validation → Finance → release, with rejection reasons
- Individual, contractor and department leaderboards (weekly/monthly/quarterly/yearly)
- Weekly Rewards (week-in-month)
- Adinkra Safety Identity & Adinkra League
- Monthly Reports Centre — auto-generated reports for every module
- Yearly, monthly and quarterly reward budget controls
- Department employee-based reward limits
- In-app notifications for role-specific workflow updates
- CSV exports that respect the active filters

---

## Adinkra identity (no "Safety Team" labels)

Departments use **only** Adinkra names and symbols — there are no generic "Safety
Team" labels. Each Adinkra is the **emblem of a real operational department**, and
wherever a symbol or name appears in the app its department is attached to it.
Every symbol is a genuine file hosted on **Wikimedia Commons**, referenced through
the stable `Special:FilePath` endpoint, so the app always shows authentic artwork
while keeping source attribution clear (each card links to its Commons file page).

| Adinkra | Department | Meaning |
|---|---|---|
| **Akoben** | HSE & Emergency Response | War horn — vigilance, a call to action |
| **Eban** | Security | Fence — safety and security |
| **Fihankra** | Site Services & Facilities | Compound house — safety & solidarity |
| **Sankofa** | Training & Competency | Return and fetch it — learn from incidents |
| **Nkonsonkonson** | Logistics & Haulage | Chain links — we are linked |
| **Dwennimmen** | Maintenance & Engineering | Ram's horns — strength with humility |
| **Nyansapo** | Processing & Metallurgy | Wisdom knot — ingenuity |
| **Adinkrahene** | Mining Operations | Chief of symbols — leadership |

Brand mark: **Akoma Ntoaso** (linked hearts — understanding & agreement). All
symbol files were verified to resolve from Commons.

---

## Free-tier HSE modules (Safety Pays Free)

This build is the **Free Version** of Safety Pays. It gives basic HSE visibility;
advanced analytics, AI, unlimited history, automation and enterprise controls are
reserved for Pro / Enterprise and shown as locked **"Available in Pro"** cards with
an upgrade page at `/pro`.

- **Location Hotspots** (`/hotspots`) — reports by location/sub-location, top-5
  ranking, per-type counts, open/overdue actions, highest risk, and a hotspot
  status (Normal / Watch / High Risk / Critical) with **HSE-Admin-adjustable
  thresholds**. Filters: month, year, department, location, report type, risk level.
- **High-Potential Events** (`/highpotential`) — auto-flagged when the potential
  consequence is Major/Catastrophic, the risk is Critical, or a reviewer flags it.
- **Actual vs Potential consequence** on every report (Insignificant → Catastrophic),
  with a "Low actual / high potential" focus card.
- **Dept & Contractor Summary** (`/summary`) — basic counts (Free shows 11 departments
  / 33 contractors) plus cause-category charts.
- **Cause categories** — one controlled master value each (no duplicate variants).
- **Property / Equipment Damage** (`/damage`) — damage type, asset, cost range,
  downtime, repair status.
- **Data Quality** (`/quality`) — completeness %, missing-field and classification
  warnings, reviewer override-with-reason.
- **Dashboard & Monthly Reports Centre** — module cards, simple bar charts (by
  location / risk / department / cause / actual-vs-potential), CSV + print view.
- **AI Safety Insights** (`/ai`) — **Basic AI Safety Prediction (Included)**: a
  transparent, rule-based risk engine that scores locations, departments, equipment,
  activities, contractors and causes 0–100 (Low / Moderate / High / Critical) with a
  **fully explainable "because it recorded …"** rationale, recommended action and
  confidence on every prediction. Weekly/monthly with 8 filters, overdue-action and
  repeat-hazard alerts, a minimum-data guard, and an advisory disclaimer. Heavy ML,
  telemetry, multi-site forecasting and alerting are locked as **"Advanced AI Risk
  Forecasting — Pro"**.

**Free limits** (existing data is never deleted when a limit is reached — a clear
upgrade message is shown instead): 1 company · 1 site · 5 locations · 11 departments ·
33 contractors · 700 employees · 55 SafePay Champions (5 per department) ·
100 records per month · current month + 90 days of detailed history · CSV export only.

---

## Architecture

| File | Responsibility |
|---|---|
| `app.py` | HTTP server (`http.server`), routing, sessions/auth, forms, CSV exports |
| `domain.py` | Roles & budget rules, date helpers (auto-quarter, week-in-month), JSON store, scoring, leaderboards, limits |
| `render.py` | Server-rendered HTML, CSS, app shell, components |
| `adinkra.py` | Adinkra symbol data + Wikimedia Commons URL helpers |
| `data/` | Runtime JSON store (git-ignored, reseeds on delete) |

JSON collections already mirror a future relational schema: `users`,
`departments`, `companies`, `safety_observations`, `near_miss_hazard_reports`,
`incidents`, `corrective_actions`, `safety_points`, `point_reset_events`,
`rewards`, `reward_requests`, `worker_hid_requests`, `notifications`,
`point_adjustment_requests`, `roles`, `permissions`, `role_permissions`,
`user_roles`, `department_access`, `audit_logs`,
`yearly_reward_budgets`, `monthly_reward_budgets`, `quarterly_reward_budgets`.

---

## Future production upgrade

- Supabase Auth or enterprise SSO + role-based authorization middleware
- PostgreSQL / Supabase tables for the JSON collections
- Object storage for incident & report evidence
- Background jobs for overdue actions, budget warnings and report generation
- PDF report generation; API endpoints for Power BI, Excel and mobile offline sync
