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

| Environment variable | Effect |
|---|---|
| `PORT` | Port to serve on (default `8090`). |
| `NO_BROWSER` | Set to any value to stop the browser auto-opening. |

On first run a demo dataset is seeded into `data/safetypays_data.json`. Delete that
file (or use **Admin Tools → Reset demo data**) to reseed a fresh demo.

---

## Demo login & roles

Pick any demo user on the sign-in screen — each carries a role:

- **Worker** — report observations/hazards/incidents, earn points, request rewards
- **Supervisor** — review & approve reports, raise/close corrective actions
- **HSE Manager** — review queue, reports centre, budget view
- **Management** — reports centre, budget view
- **Finance Manager** — approve & release rewards (steps 3–4), budget view
- **Admin** — approve rewards (step 2), **edit budgets**, manage department employee counts
- **Contractor Admin** — contractor-side access

### Budget access control

> Only **HSE Manager, Management, Finance Manager and Admin** can *see* the budget
> modules, and **only the Admin** can create, edit, approve or lock a budget.

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

---

## Reward approval workflow

Workers request rewards using their **rewardable points** (spendable balance =
points earned − points already released). Each request runs a four-stage flow:

```text
Employee submits reward request
        ↓
Admin approves            (step 2)
        ↓
Finance Manager approves  (step 3)
        ↓
Reward is released        (step 4)
```

Tracked at every stage: request status, admin approval, finance approval,
**rejection reason** (captured at the admin or finance stage), and reward-release
tracking (who released it and when). Either approver can reject with a reason; a
released reward's cash value is charged to the department, monthly, quarterly and
yearly budgets.

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
- Reward catalogue + worker request flow
- Reward approval workflow: submit → Admin → Finance → release, with rejection reasons
- Individual, contractor and department leaderboards (weekly/monthly/quarterly/yearly)
- Weekly Rewards (week-in-month)
- Adinkra Safety Identity & Adinkra League
- Monthly Reports Centre — auto-generated reports for every module
- Yearly, monthly and quarterly reward budget controls
- Department employee-based reward limits
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
`rewards`, `reward_requests`, `yearly_reward_budgets`, `monthly_reward_budgets`,
`quarterly_reward_budgets`.

---

## Future production upgrade

- Supabase Auth or enterprise SSO + role-based authorization middleware
- PostgreSQL / Supabase tables for the JSON collections
- Object storage for incident & report evidence
- Background jobs for overdue actions, budget warnings and report generation
- PDF report generation; API endpoints for Power BI, Excel and mobile offline sync
