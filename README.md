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

Then open <http://localhost:8000>. That's it — no install step.

| Environment variable | Effect |
|---|---|
| `PORT` | Port to serve on (default `8000`). |
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
- **Finance Manager** — release approved rewards, budget view
- **Admin** — approve rewards, **edit budgets**, manage department employee counts
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

## Modules

- Role-based dashboards
- Safety observation reporting
- Hazard / near-miss (HID) reporting
- Incident reporting with LTI reset trigger
- Supervisor review & approval queue
- Corrective action tracker (create, close, overdue flags)
- Safety points ledger
- Reward catalogue + worker request flow
- Admin reward approval → Finance release (two-stage)
- Individual, contractor and department leaderboards (weekly/monthly/quarterly/yearly)
- Weekly Rewards (week-in-month)
- Adinkra Safety Identity & Adinkra League
- Monthly & quarterly report centre
- Yearly, monthly and quarterly reward budget controls
- Department employee-based reward limits
- CSV exports that respect the active filters

---

## Adinkra identity (no "Safety Team" labels)

Departments are represented **only** by real Adinkra symbols, names, meanings and
mottos. Every symbol is a genuine file hosted on **Wikimedia Commons**, referenced
through the stable `Special:FilePath` endpoint, so the app always shows authentic
artwork while keeping source attribution clear (each card links to its Commons file
page).

| Department | Meaning | Theme |
|---|---|---|
| **Akoben** | War horn — vigilance, a call to action | Alertness |
| **Eban** | Fence — safety and security | Protection |
| **Fihankra** | Compound house — safety & solidarity | Site safety |
| **Sankofa** | Return and fetch it | Learning from incidents |
| **Nkonsonkonson** | Chain links — we are linked | Teamwork |
| **Dwennimmen** | Ram's horns — strength with humility | Resilience |
| **Nyansapo** | Wisdom knot | Problem solving |
| **Adinkrahene** | Chief of symbols — leadership | Leadership |

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
