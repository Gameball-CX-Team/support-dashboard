# cx-support-dashboard

A self-contained dashboard that pulls Issue work items from the Gameball Azure DevOps board and renders a rich HTML status page — open tickets, overdue work, team performance, per-client breakdowns, and more.

No build step. No server. No external dependencies. One Python script, standard library only.

The latest dashboard is always available as `index.html` at the root of this repo, served via GitHub Pages at:
**https://gameball-cx-team.github.io/support-dashboard/**

---

## What you get

- **Overview** — 7 KPIs, status donut chart, ticket age bands with danger zone callout, stage distribution, product area/blocker breakdown, top openers, resolver performance table, longest open tickets
- **All Tickets** — full filterable/searchable table with status, priority, plan, age, and assignee filters
- **Client Lookup** — per-client cards with frustration risk scores, Enterprise/Non-Enterprise filter, expandable ticket tables
- **Heatmap** — 5-week creation heatmap and current workload by assignee
- Every ticket links directly back to the Azure DevOps work item

---

## Setup (first time only)

**1. Clone the repo:**
```bash
git clone https://github.com/Gameball-CX-Team/support-dashboard.git
cd support-dashboard
```

**2. Create your personal `.env`:**
```bash
cp .env.example .env
```

**3. Open `.env` and paste your Personal Access Token.**
Generate one at: https://dev.azure.com/gameballers/_usersSettings/tokens
Required scope: **Work Items — Read only**

**4. Fill in your ADO project name in `config.env`:**
```
ADO_PROJECT=YourProjectNameHere
```

That's it. Python 3 is all you need — no pip installs, no virtual environments.

---

## Refreshing the dashboard

```bash
python3 refresh.py
git add index.html
git commit -m "Refresh dashboard"
git push
```

This updates the live dashboard for everyone on the team.

---

## Files

```
support-dashboard/
├── refresh.py       # the whole thing — pulls ADO data and generates index.html
├── index.html       # latest generated dashboard (committed, served by GitHub Pages)
├── config.env       # shared config: ADO_ORG and ADO_PROJECT (committed)
├── .env             # your personal PAT (gitignored — never commit this)
├── .env.example     # PAT template for new team members
├── .gitignore
└── README.md
```

---

## Security

- `.env` is gitignored. Never commit it.
- The PAT only needs **Work Items: Read** — no write scopes required.
- If you accidentally expose a PAT, revoke it immediately at https://dev.azure.com/gameballers/_usersSettings/tokens

---

## Customisation

**Change the SLA threshold** (default 10 days):
Edit `SLA_DAYS = 10` near the top of `refresh.py`.

**Add a new ADO field:**
1. Add the field API name to `ADO_FIELDS` in `refresh.py`
2. Map it in the `transform_ticket()` function
3. Add it to the HTML template in `generate_html()`

**Change the sync schedule** (if you later add GitHub Actions):
The cron expression `0 6 * * *` = daily 8am Cairo (UTC+2). Use https://crontab.guru to adjust.
