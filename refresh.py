"""
refresh.py
==========
Pulls all Issue work items from Azure DevOps and generates index.html.

Usage:
    python3 refresh.py
    git add index.html
    git commit -m "Refresh dashboard"
    git push

That's it. The dashboard at:
    https://gameball-cx-team.github.io/support-dashboard/
will update for everyone on the team.

Setup (first time only):
    cp .env.example .env
    # Paste your ADO Personal Access Token into .env
    # Get one at: https://dev.azure.com/gameballers/_usersSettings/tokens
    # Required scope: Work Items — Read only

Dependencies: Python standard library only. Nothing to install.
"""

import urllib.request
import urllib.parse
import json
import base64
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOAD CREDENTIALS & CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reads from two files:
#   .env        — your personal PAT (gitignored, never committed)
#   config.env  — shared org/project config (committed)

def load_env(path):
    """Load key=value pairs from an env file into os.environ."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env("config.env")
load_env(".env")

ADO_PAT     = os.environ.get("ADO_PAT", "")
ADO_ORG     = os.environ.get("ADO_ORG", "")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "")

if not all([ADO_PAT, ADO_ORG, ADO_PROJECT]):
    print("❌  Missing credentials. Make sure .env and config.env are set up.")
    print("    See README.md for setup instructions.")
    raise SystemExit(1)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLA_DAYS = 10   # Tickets open longer than this are flagged as breached
                # TO CHANGE: edit this number

# ADO fields to fetch for each ticket
# TO ADD A FIELD: add its API name here, then map it in transform_ticket()
ADO_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.CreatedDate",
    "System.CreatedBy",
    "System.AssignedTo",
    "Microsoft.VSTS.Common.ResolvedDate",
    "Microsoft.VSTS.Common.Severity",
    "Custom.product_area2",      # Blocker / product area (was Custom.ProductArea)
    "Custom.ClientID",           # Client ID (was Custom.ID)
    "Custom.Plan",               # Enterprise / Non-Enterprise
    "Custom.exceeded_sla",       # Boolean — ADO's own SLA breach flag
    "Custom.AutomatedSLA",       # SLA deadline date
    "Custom.Type",               # Mobile / Web / etc.
]

SEVERITY_MAP = {
    "1 - critical": "Critical",
    "2 - high":     "High",
    "3 - medium":   "Medium",
    "4 - low":      "Low",
}

STATUS_ORDER = [
    "New", "Active", "Evaluating", "Needs Clarification",
    "Pending Customer Feedback", "QA Review",
    "Scheduled Deployment", "Re-Open", "Resolved"
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADO API HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ado_headers():
    token = base64.b64encode(f":{ADO_PAT}".encode()).decode()
    return {
        "Content-Type":  "application/json",
        "Authorization": f"Basic {token}",
    }

def ado_get(url):
    req = urllib.request.Request(url, headers=ado_headers())
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def ado_post(url, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=ado_headers(), method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_client(title):
    """Client name is at the start of the title before the first separator."""
    for sep in [" - ", " | ", ": "]:
        if sep in (title or ""):
            return title.split(sep, 1)[0].strip()
    return "Unknown"

def display_name(val):
    """ADO person fields are either a plain string or {"displayName": "..."}."""
    if not val:
        return "Unassigned"
    if isinstance(val, dict):
        return val.get("displayName", "Unassigned")
    return str(val)

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def days_between(start_str, end_str=None):
    start = parse_date(start_str)
    end   = parse_date(end_str) if end_str else datetime.now(timezone.utc)
    if not start:
        return 0
    return max(0, (end - start).days)

def normalize_severity(raw):
    if not raw:
        return "Medium"
    return SEVERITY_MAP.get(str(raw).lower().strip(), str(raw))

def normalize_plan(raw):
    if not raw:
        return "Non-Enterprise"
    return "Enterprise" if "enterprise" in str(raw).lower() else "Non-Enterprise"

def ado_url(ticket_id):
    return f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_workitems/edit/{ticket_id}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: FETCH TICKETS FROM ADO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_tickets():
    print("▶  Fetching ticket IDs from Azure DevOps...")

    # WIQL query — fetches all Issue work items, newest first
    # TO FILTER: add conditions to the WHERE clause
    wiql_url = f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis/wit/wiql?api-version=7.0"
    result   = ado_post(wiql_url, {"query": """
        SELECT [System.Id]
        FROM WorkItems
        WHERE [System.WorkItemType] = 'Issue'
        ORDER BY [System.CreatedDate] DESC
    """})

    ids = [item["id"] for item in result.get("workItems", [])]
    print(f"   Found {len(ids)} issues")

    # Fetch full details in batches of 200 (ADO limit)
    print("▶  Fetching ticket details...")
    fields_param = ",".join(ADO_FIELDS)
    all_items    = []

    for i in range(0, len(ids), 200):
        batch     = ",".join(map(str, ids[i:i+200]))
        url       = (f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis/wit/workitems"
                     f"?ids={batch}&fields={fields_param}&api-version=7.0")
        all_items.extend(ado_get(url).get("value", []))
        print(f"   Fetched {min(i+200, len(ids))} / {len(ids)}")

    return all_items

def transform_ticket(raw):
    """Convert one raw ADO work item into a clean dashboard ticket dict."""
    f          = raw.get("fields", {})
    tid        = str(raw.get("id", ""))
    state      = f.get("System.State", "New")
    created    = f.get("System.CreatedDate", "")
    resolved   = f.get("Microsoft.VSTS.Common.ResolvedDate")
    d_open     = days_between(created, resolved if state == "Resolved" else None)

    # Use ADO's own SLA breach flag if available, otherwise compute from days open
    exceeded_sla = f.get("Custom.exceeded_sla") or (d_open > SLA_DAYS)

    return {
        "id":           tid,
        "adoUrl":       ado_url(tid),
        "client":       extract_client(f.get("System.Title", "")),
        "clientId":     str(f.get("Custom.ClientID", "") or ""),   # fixed: was Custom.ID
        "subject":      f.get("System.Title", ""),
        "status":       state,
        "priority":     normalize_severity(f.get("Microsoft.VSTS.Common.Severity")),
        "stage":        state,
        "blocker":      f.get("Custom.product_area2", "") or "",   # fixed: was Custom.ProductArea
        "owner":        display_name(f.get("System.AssignedTo")),
        "openedBy":     display_name(f.get("System.CreatedBy")),
        "daysOpen":     d_open,
        "slaPercent":   min(100, round((d_open / SLA_DAYS) * 100)),
        "exceededSla":  bool(exceeded_sla),
        "slaDeadline":  f.get("Custom.AutomatedSLA", "") or "",
        "ticketType":   f.get("Custom.Type", "") or "",
        "createdDate":  created,
        "resolvedDate": resolved or "",
        "plan":         normalize_plan(f.get("Custom.Plan")),
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2: GENERATE HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_html(tickets, exported_at):
    tickets_json = json.dumps(tickets, ensure_ascii=False)
    now_str      = exported_at.strftime("%d %b %Y, %H:%M UTC")
    count        = len(tickets)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gameball Support Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ─────────────────────────────── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0a0c10;--surface:#13161d;--border:#1e2330;--text:#e2e8f0;--muted:#64748b;
  --amber:#f59e0b;--green:#22c55e;--red:#ef4444;--blue:#3b82f6;--purple:#8b5cf6;
  --pink:#ec4899;--teal:#14b8a6;--indigo:#6366f1;
  --s-new:#6366f1;--s-active:#3b82f6;--s-eval:#f59e0b;--s-clarify:#8b5cf6;
  --s-pcf:#ec4899;--s-qa:#14b8a6;--s-deploy:#22c55e;--s-reopen:#ef4444;--s-resolved:#6b7280;
}}
html{{scroll-behavior:smooth}}
body{{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:14px;min-height:100vh}}
a{{color:inherit;text-decoration:none}}
button{{cursor:pointer;border:none;background:none;font:inherit;color:inherit}}
input{{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 12px;outline:none}}
input:focus{{border-color:var(--amber)}}
select{{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 10px;outline:none;cursor:pointer}}
select:focus{{border-color:var(--amber)}}

/* ── Top Bar ──────────────────────────────────── */
#topbar{{
  position:sticky;top:0;z-index:100;background:rgba(10,12,16,.95);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--border);
  padding:12px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap
}}
#topbar .title{{font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:var(--amber);white-space:nowrap}}
#topbar .subtitle{{font-size:12px;color:var(--muted);white-space:nowrap}}
#topbar .spacer{{flex:1}}
#globalSearch{{width:220px;font-size:13px}}
.qf-btn{{padding:5px 12px;border-radius:6px;border:1px solid var(--border);font-size:12px;font-weight:500;transition:all .15s}}
.qf-btn:hover,.qf-btn.active{{background:var(--amber);color:#000;border-color:var(--amber)}}

/* ── Tabs ─────────────────────────────────────── */
#tabs{{display:flex;gap:2px;padding:0 24px;border-bottom:1px solid var(--border);background:var(--surface)}}
.tab-btn{{padding:12px 20px;font-size:13px;font-weight:500;color:var(--muted);border-bottom:2px solid transparent;transition:all .15s}}
.tab-btn:hover{{color:var(--text)}}
.tab-btn.active{{color:var(--amber);border-bottom-color:var(--amber)}}
.tab-panel{{display:none;padding:24px}}
.tab-panel.active{{display:block}}

/* ── Layout helpers ───────────────────────────── */
.row{{display:grid;gap:16px;margin-bottom:16px}}
.row-2{{grid-template-columns:1fr 1fr}}
.row-3{{grid-template-columns:1fr 1fr 1fr}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}}
.card-title{{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}}

/* ── KPI Strip ────────────────────────────────── */
#kpiStrip{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;margin-bottom:16px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;border-top-width:2px}}
.kpi .val{{font-family:'IBM Plex Mono',monospace;font-size:28px;font-weight:600;line-height:1}}
.kpi .lbl{{font-size:11px;color:var(--muted);margin-top:4px}}

/* ── Status badges ────────────────────────────── */
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}}
.b-new{{background:#6366f120;color:var(--s-new)}}
.b-active{{background:#3b82f620;color:var(--s-active)}}
.b-eval{{background:#f59e0b20;color:var(--s-eval)}}
.b-clarify{{background:#8b5cf620;color:var(--s-clarify)}}
.b-pcf{{background:#ec489920;color:var(--s-pcf)}}
.b-qa{{background:#14b8a620;color:var(--s-qa)}}
.b-deploy{{background:#22c55e20;color:var(--s-deploy)}}
.b-reopen{{background:#ef444420;color:var(--s-reopen)}}
.b-resolved{{background:#6b728020;color:var(--s-resolved)}}
.b-critical{{background:#ef444420;color:var(--red)}}
.b-high{{background:#f59e0b20;color:var(--amber)}}
.b-medium{{background:#3b82f620;color:var(--blue)}}
.b-low{{background:#22c55e20;color:var(--green)}}
.b-enterprise{{background:#8b5cf620;color:var(--purple)}}
.b-nonenterprise{{background:#1e233080;color:var(--muted)}}

/* ── Tables ───────────────────────────────────── */
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 10px;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;border-bottom:1px solid var(--border);white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:middle}}
tr:hover td{{background:rgba(255,255,255,.02)}}
tr.danger td{{background:rgba(239,68,68,.04)}}
.ticket-link{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--amber);cursor:pointer}}
.ticket-link:hover{{text-decoration:underline}}
.days-danger{{color:var(--red);font-weight:600}}
.days-warn{{color:var(--amber);font-weight:500}}
.sla-bar{{width:60px;height:6px;background:var(--border);border-radius:3px;display:inline-block;vertical-align:middle;margin-left:6px;overflow:hidden}}
.sla-fill{{height:100%;border-radius:3px;transition:width .3s}}

/* ── Bar charts (CSS) ─────────────────────────── */
.bar-chart{{display:flex;flex-direction:column;gap:8px}}
.bar-row{{display:grid;grid-template-columns:120px 1fr 40px;align-items:center;gap:8px;font-size:12px}}
.bar-track{{background:var(--border);border-radius:3px;height:8px;overflow:hidden;cursor:pointer}}
.bar-fill{{height:100%;border-radius:3px;transition:width .4s}}

/* ── Age bands ────────────────────────────────── */
.age-bands{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}}
.age-card{{border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;transition:opacity .15s}}
.age-card:hover{{opacity:.8}}
.age-card .age-val{{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600}}
.age-card .age-lbl{{font-size:11px;color:var(--muted);margin-top:2px}}
.danger-banner{{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:6px;padding:8px 12px;text-align:center;font-size:12px;font-weight:600;color:var(--red);margin-bottom:12px}}

/* ── Stage journey ────────────────────────────── */
.journey{{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px}}
.j-box{{flex:1;min-width:60px;padding:6px 4px;border-radius:6px;text-align:center;font-size:10px;font-weight:500;border:1px solid var(--border)}}
.j-past{{background:#22c55e15;color:var(--green);border-color:#22c55e30}}
.j-current{{background:#f59e0b20;color:var(--amber);border-color:var(--amber)}}
.j-current-blocked{{background:#ef444420;color:var(--red);border-color:var(--red)}}
.j-future{{background:var(--border);color:var(--muted)}}
.j-box .j-hours{{font-size:9px;opacity:.7;margin-top:2px}}

/* ── Donut chart ──────────────────────────────── */
#donutWrap{{display:flex;align-items:center;gap:20px}}
#donutCanvas{{flex-shrink:0}}
#donutLegend{{flex:1;display:flex;flex-direction:column;gap:6px;font-size:12px}}
.leg-row{{display:flex;align-items:center;gap:8px;cursor:pointer;padding:3px 6px;border-radius:4px;transition:background .15s}}
.leg-row:hover{{background:rgba(255,255,255,.05)}}
.leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.leg-name{{flex:1}}
.leg-count{{font-family:'IBM Plex Mono',monospace;color:var(--muted)}}

/* ── Filters ──────────────────────────────────── */
.filter-row{{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.filter-row label{{font-size:12px;color:var(--muted)}}

/* ── Client cards ─────────────────────────────── */
.client-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
.client-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;cursor:pointer;transition:border-color .15s;border-left-width:3px}}
.client-card:hover{{border-color:var(--amber)}}
.client-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.client-name{{font-weight:600;font-size:14px}}
.client-id{{font-size:11px;color:var(--muted);margin-top:2px}}
.client-stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:10px;text-align:center}}
.client-stat{{font-size:11px;color:var(--muted)}}
.client-stat .sv{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:var(--text);display:block}}
.risk-bar-wrap{{margin-bottom:6px}}
.risk-bar-track{{height:6px;background:var(--border);border-radius:3px;overflow:hidden}}
.risk-bar-fill{{height:100%;border-radius:3px}}
.risk-note{{font-size:11px;color:var(--muted);margin-top:4px}}
.client-expand{{margin-top:14px;display:none;border-top:1px solid var(--border);padding-top:14px}}
.client-expand.open{{display:block}}

/* ── Heatmap ──────────────────────────────────── */
.heatmap-grid{{display:grid;grid-template-columns:60px repeat(7,1fr);gap:4px}}
.hm-label{{font-size:11px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;padding-right:8px}}
.hm-cell{{height:36px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:500;cursor:default;transition:opacity .15s}}
.hm-cell:hover{{opacity:.8}}
.hm-0{{background:#1e2330}}
.hm-1{{background:#92400e;color:#fde68a}}
.hm-2{{background:#b45309;color:#fde68a}}
.hm-3{{background:#d97706;color:#000}}
.hm-4{{background:#dc2626;color:#fff}}
.hm-5{{background:#ef4444;color:#fff}}

/* ── People panels ────────────────────────────── */
.people-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.perf-table{{width:100%;font-size:12px;border-collapse:collapse}}
.perf-table th{{text-align:left;padding:6px 8px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border)}}
.perf-table td{{padding:6px 8px;border-bottom:1px solid var(--border)}}
.perf-table tr.slow td{{background:rgba(239,68,68,.06)}}

/* ── Modal ────────────────────────────────────── */
#modal{{display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.7);align-items:center;justify-content:center}}
#modal.open{{display:flex}}
#modalBox{{background:var(--surface);border:1px solid var(--border);border-radius:12px;width:min(740px,95vw);max-height:90vh;overflow-y:auto;padding:24px}}
#modalBox .modal-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px}}
#modalBox .modal-id{{font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--amber)}}
#modalBox .modal-close{{font-size:20px;color:var(--muted);cursor:pointer;line-height:1}}
#modalBox .modal-close:hover{{color:var(--text)}}
.alert-banner{{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:6px;padding:10px 14px;margin-bottom:16px;color:var(--red);font-size:13px;font-weight:500}}
.meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}}
.meta-item .mi-label{{font-size:11px;color:var(--muted);margin-bottom:3px}}
.meta-item .mi-val{{font-size:13px}}
.big-days{{font-family:'IBM Plex Mono',monospace;font-size:32px;font-weight:600}}
.ado-link{{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border:1px solid var(--amber);border-radius:6px;color:var(--amber);font-size:12px;font-weight:500;margin-bottom:16px;transition:background .15s}}
.ado-link:hover{{background:rgba(245,158,11,.1)}}
.activity-log{{display:flex;flex-direction:column;gap:8px}}
.activity-entry{{padding:8px 12px;border-radius:6px;border:1px solid var(--border);font-size:12px}}
.activity-entry .ae-meta{{font-size:11px;color:var(--muted);margin-bottom:3px}}
</style>
</head>
<body>

<!-- TOP BAR -->
<div id="topbar">
  <div>
    <div class="title">Gameball Support Dashboard</div>
    <div class="subtitle">Last synced: {now_str} &nbsp;·&nbsp; {count} tickets</div>
  </div>
  <div class="spacer"></div>
  <input id="globalSearch" type="text" placeholder="Search tickets…">
  <button class="qf-btn active" data-qf="all">All</button>
  <button class="qf-btn" data-qf="active">Active</button>
  <button class="qf-btn" data-qf="new">New</button>
  <button class="qf-btn" data-qf="atrisk">At Risk</button>
</div>

<!-- TABS -->
<div id="tabs">
  <button class="tab-btn active" data-tab="overview">Overview</button>
  <button class="tab-btn" data-tab="alltickets">All Tickets</button>
  <button class="tab-btn" data-tab="clients">Client Lookup</button>
  <button class="tab-btn" data-tab="heatmap">Heatmap</button>
</div>

<!-- TAB: OVERVIEW -->
<div id="tab-overview" class="tab-panel active">
  <div id="kpiStrip"></div>
  <div class="row row-2">
    <div class="card">
      <div class="card-title">Tickets by Status</div>
      <div id="donutWrap">
        <canvas id="donutCanvas" width="160" height="160"></canvas>
        <div id="donutLegend"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Ticket Age Bands</div>
      <div class="danger-banner">⚠ &gt;10 DAYS OPEN = DANGER ZONE</div>
      <div class="age-bands" id="ageBands"></div>
      <div class="bar-chart" id="ageBandChart"></div>
    </div>
  </div>
  <div class="row row-2">
    <div class="card">
      <div class="card-title">Stage Distribution</div>
      <div class="bar-chart" id="stageChart"></div>
    </div>
    <div class="card">
      <div class="card-title">Product Area / Blocker</div>
      <div class="bar-chart" id="blockerChart"></div>
    </div>
  </div>
  <div class="people-row">
    <div class="card">
      <div class="card-title">Top Openers</div>
      <div class="bar-chart" id="openersChart"></div>
    </div>
    <div class="card">
      <div class="card-title">Resolver Performance</div>
      <div class="tbl-wrap"><table class="perf-table" id="resolverTable">
        <thead><tr><th>Name</th><th>Handled</th><th>Avg Days</th><th>SLA Breach%</th><th>Open</th></tr></thead>
        <tbody id="resolverBody"></tbody>
      </table></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Longest Open Tickets (Top 8)</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>ID</th><th>Client</th><th>Plan</th><th>Subject</th><th>Status</th><th>Blocker</th><th>Owner</th><th>Opened By</th><th>Days Open</th><th>SLA</th></tr></thead>
      <tbody id="longestBody"></tbody>
    </table></div>
  </div>
</div>

<!-- TAB: ALL TICKETS -->
<div id="tab-alltickets" class="tab-panel">
  <div class="filter-row">
    <label>Status</label>
    <select id="fStatus"><option value="">All</option></select>
    <label>Plan</label>
    <select id="fPlan"><option value="">All</option><option>Enterprise</option><option>Non-Enterprise</option></select>
    <label>Age</label>
    <select id="fAge"><option value="">All</option><option value="lt5">&lt;5d</option><option value="5to10">5–10d</option><option value="10to20">10–20d</option><option value="gt20">&gt;20d</option><option value="atrisk">At Risk (&gt;10d open)</option></select>
    <label>Priority</label>
    <select id="fPriority"><option value="">All</option><option>Critical</option><option>High</option><option>Medium</option><option>Low</option></select>
    <span id="allCount" style="margin-left:auto;font-size:12px;color:var(--muted)"></span>
  </div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>ID</th><th>Client</th><th>Client ID</th><th>Subject</th><th>Status</th><th>Priority</th><th>Stage</th><th>Assigned To</th><th>Opened By</th><th>Days Open</th><th>Plan</th></tr></thead>
    <tbody id="allBody"></tbody>
  </table></div>
</div>

<!-- TAB: CLIENT LOOKUP -->
<div id="tab-clients" class="tab-panel">
  <div class="filter-row">
    <input id="clientSearch" type="text" placeholder="Search by client name or ID…" style="width:240px">
    <select id="clientSort"><option value="risk">Frustration Risk</option><option value="total">Total Tickets</option><option value="name">Name A–Z</option></select>
    <button class="qf-btn active" data-cf="all">All</button>
    <button class="qf-btn" data-cf="enterprise">Enterprise</button>
    <button class="qf-btn" data-cf="nonenterprise">Non-Enterprise</button>
  </div>
  <div class="client-grid" id="clientGrid"></div>
</div>

<!-- TAB: HEATMAP -->
<div id="tab-heatmap" class="tab-panel">
  <div class="row row-2">
    <div class="card">
      <div class="card-title">Ticket Creation Heatmap (Last 5 Weeks)</div>
      <div class="heatmap-grid" id="heatmapGrid"></div>
      <div style="display:flex;gap:8px;margin-top:12px;font-size:11px;color:var(--muted);align-items:center">
        <span>Low</span>
        <div style="width:16px;height:10px;background:#92400e;border-radius:2px"></div>
        <div style="width:16px;height:10px;background:#d97706;border-radius:2px"></div>
        <div style="width:16px;height:10px;background:#dc2626;border-radius:2px"></div>
        <div style="width:16px;height:10px;background:#ef4444;border-radius:2px"></div>
        <span>High</span>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Current Workload by Assignee</div>
      <div class="bar-chart" id="workloadChart"></div>
    </div>
  </div>
</div>

<!-- MODAL -->
<div id="modal">
  <div id="modalBox">
    <div class="modal-header">
      <div>
        <div class="modal-id" id="modalId"></div>
        <div style="font-size:16px;font-weight:600;margin-top:4px" id="modalSubject"></div>
      </div>
      <button class="modal-close" id="modalClose">✕</button>
    </div>
    <div id="modalAlert" class="alert-banner" style="display:none">⚠ This ticket has been open for more than 10 days</div>
    <a id="modalAdoLink" href="#" target="_blank" class="ado-link">View in Azure DevOps →</a>
    <div class="meta-grid" id="modalMeta"></div>
    <div class="card-title">Status Journey</div>
    <div class="journey" id="modalJourney"></div>
    <div class="card-title" style="margin-top:16px">Activity</div>
    <div class="activity-log" id="modalActivity"></div>
  </div>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────────
const TICKETS = {tickets_json};
const STATUS_ORDER = {json.dumps(STATUS_ORDER)};
const STATUS_COLORS = {{
  "New":"#6366f1","Active":"#3b82f6","Evaluating":"#f59e0b",
  "Needs Clarification":"#8b5cf6","Pending Customer Feedback":"#ec4899",
  "QA Review":"#14b8a6","Scheduled Deployment":"#22c55e",
  "Re-Open":"#ef4444","Resolved":"#6b7280"
}};
const BADGE_CLASS = {{
  "New":"b-new","Active":"b-active","Evaluating":"b-eval",
  "Needs Clarification":"b-clarify","Pending Customer Feedback":"b-pcf",
  "QA Review":"b-qa","Scheduled Deployment":"b-deploy",
  "Re-Open":"b-reopen","Resolved":"b-resolved",
  "Critical":"b-critical","High":"b-high","Medium":"b-medium","Low":"b-low",
  "Enterprise":"b-enterprise","Non-Enterprise":"b-nonenterprise"
}};
// Statuses considered "closed" — excluded from At Risk and overdue counts
const CLOSED_STATUSES = new Set(["Resolved"]);
const isOpen = t => !CLOSED_STATUSES.has(t.status);

// ── Helpers ───────────────────────────────────────────────────────────────────
const badge = (txt, cls) => `<span class="badge ${{cls || BADGE_CLASS[txt] || ''}}">${{txt}}</span>`;
const esc   = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function daysColor(t) {{
  if (t.daysOpen > 10 && t.status !== 'Resolved') return 'days-danger';
  if (t.daysOpen > 5  && t.status !== 'Resolved') return 'days-warn';
  return '';
}}

function slaBar(pct) {{
  const color = pct >= 100 ? '#ef4444' : pct >= 70 ? '#f59e0b' : '#22c55e';
  return `<span class="sla-bar"><span class="sla-fill" style="width:${{Math.min(100,pct)}}%;background:${{color}}"></span></span>`;
}}

function planBadge(plan) {{
  return badge(plan, plan === 'Enterprise' ? 'b-enterprise' : 'b-nonenterprise');
}}

function parseDate(s) {{
  if (!s) return null;
  return new Date(s.slice(0,19).replace('T',' ') + ' UTC');
}}

function fmtDate(s) {{
  const d = parseDate(s);
  if (!d) return '—';
  return d.toLocaleDateString('en-GB',{{day:'2-digit',month:'short',year:'numeric'}});
}}

// ── KPI Strip ─────────────────────────────────────────────────────────────────
function buildKPIs() {{
  const unresolved = TICKETS.filter(t => isOpen(t));
  const now        = new Date();
  const week_ms    = 7 * 864e5;
  const kpis = [
    {{ label:'Active',            val: unresolved.length,                                              color:'var(--blue)' }},
    {{ label:'Avg Days Open',     val: unresolved.length ? Math.round(unresolved.reduce((a,t)=>a+t.daysOpen,0)/unresolved.length) : 0, color:'var(--amber)' }},
    {{ label:'New This Week',     val: TICKETS.filter(t => parseDate(t.createdDate) && (now - parseDate(t.createdDate)) < week_ms).length, color:'var(--green)' }},
    {{ label:'Resolved',          val: TICKETS.filter(t => t.status === 'Resolved').length,            color:'var(--green)' }},
    {{ label:'Open >10 Days',     val: unresolved.filter(t => t.daysOpen > 10).length,                 color:'var(--red)' }},
    {{ label:'Enterprise Open',   val: unresolved.filter(t => t.plan === 'Enterprise').length,         color:'var(--purple)' }},
    {{ label:'Non-Enterprise Open',val:unresolved.filter(t => t.plan !== 'Enterprise').length,         color:'var(--muted)' }},
  ];
  document.getElementById('kpiStrip').innerHTML = kpis.map(k =>
    `<div class="kpi" style="border-top-color:${{k.color}}">
      <div class="val">${{k.val}}</div>
      <div class="lbl">${{k.label}}</div>
    </div>`
  ).join('');
}}

// ── Donut Chart ───────────────────────────────────────────────────────────────
function buildDonut() {{
  const counts = {{}};
  TICKETS.forEach(t => {{ counts[t.status] = (counts[t.status]||0)+1; }});
  const entries = STATUS_ORDER.filter(s => counts[s]).map(s => ({{s, n:counts[s]}}));
  const total   = entries.reduce((a,e)=>a+e.n,0);
  const canvas  = document.getElementById('donutCanvas');
  const ctx     = canvas.getContext('2d');
  const cx=80, cy=80, r=70, inner=42;
  let angle = -Math.PI/2;
  ctx.clearRect(0,0,160,160);
  entries.forEach(e => {{
    const slice = (e.n/total)*2*Math.PI;
    ctx.beginPath(); ctx.moveTo(cx,cy);
    ctx.arc(cx,cy,r,angle,angle+slice);
    ctx.closePath();
    ctx.fillStyle = STATUS_COLORS[e.s]||'#444';
    ctx.fill();
    angle += slice;
  }});
  ctx.beginPath(); ctx.arc(cx,cy,inner,0,2*Math.PI);
  ctx.fillStyle='#13161d'; ctx.fill();
  ctx.fillStyle='#e2e8f0'; ctx.font='600 18px IBM Plex Mono';
  ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.fillText(total,cx,cy);

  const legend = document.getElementById('donutLegend');
  legend.innerHTML = entries.map(e => `
    <div class="leg-row" onclick="filterAllByStatus('${{e.s}}')">
      <div class="leg-dot" style="background:${{STATUS_COLORS[e.s]}}"></div>
      <span class="leg-name">${{e.s}}</span>
      <span class="leg-count">${{e.n}} (${{Math.round(e.n/total*100)}}%)</span>
    </div>`).join('');
}}

// ── Age Bands ─────────────────────────────────────────────────────────────────
function buildAgeBands() {{
  const unresolved = TICKETS.filter(t => isOpen(t));
  const bands = [
    {{ label:'< 5 days',  key:'lt5',   count:unresolved.filter(t=>t.daysOpen<5).length,              color:'var(--green)' }},
    {{ label:'5–10 days', key:'5to10', count:unresolved.filter(t=>t.daysOpen>=5&&t.daysOpen<10).length, color:'var(--blue)' }},
    {{ label:'10–20 days',key:'10to20',count:unresolved.filter(t=>t.daysOpen>=10&&t.daysOpen<20).length,color:'var(--amber)',note:'At Risk' }},
    {{ label:'> 20 days', key:'gt20',  count:unresolved.filter(t=>t.daysOpen>=20).length,             color:'var(--red)',  note:'DANGER' }},
  ];
  const total = unresolved.length || 1;
  document.getElementById('ageBands').innerHTML = bands.map(b => `
    <div class="age-card" style="border-color:${{b.color}}" onclick="filterAllByAge('${{b.key}}')">
      <div class="age-val" style="color:${{b.color}}">${{b.count}}</div>
      <div class="age-lbl">${{b.label}}${{b.note ? ' — '+b.note : ''}}</div>
    </div>`).join('');
  document.getElementById('ageBandChart').innerHTML = bands.map(b => {{
    const pct = Math.round(b.count/total*100);
    return `<div class="bar-row">
      <span>${{b.label}}</span>
      <div class="bar-track" onclick="filterAllByAge('${{b.key}}')">
        <div class="bar-fill" style="width:${{pct}}%;background:${{b.color}}"></div>
      </div>
      <span style="color:var(--muted)">${{pct}}%</span>
    </div>`;
  }}).join('');
}}

// ── Stage Chart ───────────────────────────────────────────────────────────────
function buildStageChart() {{
  const HOT = new Set(['Evaluating','Pending Customer Feedback']);
  const counts = {{}}; const totDays = {{}};
  TICKETS.filter(t=>t.status!=='Resolved').forEach(t => {{
    counts[t.status]  = (counts[t.status]||0)+1;
    totDays[t.status] = (totDays[t.status]||0)+t.daysOpen;
  }});
  const entries = STATUS_ORDER.filter(s=>counts[s])
    .map(s => ({{s, avg:Math.round(totDays[s]/counts[s])}}) )
    .sort((a,b)=>b.avg-a.avg);
  const max = entries[0]?.avg||1;
  document.getElementById('stageChart').innerHTML = entries.map(e => `
    <div class="bar-row">
      <span style="color:${{HOT.has(e.s)?'var(--red)':'inherit'}}">${{e.s}}</span>
      <div class="bar-track">
        <div class="bar-fill" style="width:${{Math.round(e.avg/max*100)}}%;background:${{HOT.has(e.s)?'var(--red)':STATUS_COLORS[e.s]}}"></div>
      </div>
      <span style="color:var(--muted)">${{e.avg}}d</span>
    </div>`).join('');
}}

// ── Blocker Chart ─────────────────────────────────────────────────────────────
function buildBlockerChart() {{
  const counts = {{}};
  TICKETS.forEach(t => {{
    const k = t.blocker || 'No Blocker';
    counts[k] = (counts[k]||0)+1;
  }});
  const entries = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const max = entries[0]?.[1]||1;
  document.getElementById('blockerChart').innerHTML = entries.map(([k,n]) => `
    <div class="bar-row">
      <span>${{esc(k)}}</span>
      <div class="bar-track" onclick="filterAllByBlocker('${{esc(k)}}')">
        <div class="bar-fill" style="width:${{Math.round(n/max*100)}}%;background:var(--blue)"></div>
      </div>
      <span style="color:var(--muted)">${{n}}</span>
    </div>`).join('');
}}

// ── People Analytics ──────────────────────────────────────────────────────────
function buildPeople() {{
  // Openers
  const openers = {{}};
  TICKETS.forEach(t => {{ openers[t.openedBy] = (openers[t.openedBy]||0)+1; }});
  const opEntries = Object.entries(openers).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const maxOp = opEntries[0]?.[1]||1;
  document.getElementById('openersChart').innerHTML = opEntries.map(([n,c]) => {{
    const pct = Math.round(c/TICKETS.length*100);
    const flag = pct > 20 ? ' ⚠' : '';
    return `<div class="bar-row">
      <span>${{esc(n)}}${{flag}}</span>
      <div class="bar-track">
        <div class="bar-fill" style="width:${{Math.round(c/maxOp*100)}}%;background:var(--blue)"></div>
      </div>
      <span style="color:var(--muted)">${{c}}</span>
    </div>`;
  }}).join('');

  // Resolvers
  const owners = {{}};
  TICKETS.forEach(t => {{
    if (!owners[t.owner]) owners[t.owner] = {{total:0,days:0,breach:0,open:0}};
    owners[t.owner].total++;
    owners[t.owner].days += t.daysOpen;
    if (t.slaPercent >= 100) owners[t.owner].breach++;
    if (t.status !== 'Resolved') owners[t.owner].open++;
  }});
  const rows = Object.entries(owners)
    .filter(([,v])=>v.total>=2)
    .map(([n,v])=>([n, v.total, Math.round(v.days/v.total), Math.round(v.breach/v.total*100), v.open]))
    .sort((a,b)=>b[2]-a[2]);
  document.getElementById('resolverBody').innerHTML = rows.map(([n,tot,avg,bp,open]) =>
    `<tr class="${{avg>10?'slow':''}}">
      <td>${{esc(n)}}</td><td>${{tot}}</td>
      <td style="color:${{avg>10?'var(--red)':avg>5?'var(--amber)':'inherit'}}">${{avg}}d</td>
      <td>${{bp}}%</td><td>${{open}}</td>
    </tr>`).join('');
}}

// ── Longest Open ──────────────────────────────────────────────────────────────
function buildLongest() {{
  const rows = TICKETS.filter(t=>t.status!=='Resolved')
    .sort((a,b)=>b.daysOpen-a.daysOpen).slice(0,8);
  document.getElementById('longestBody').innerHTML = rows.map(t => `
    <tr class="${{t.daysOpen>10?'danger':''}}">
      <td><span class="ticket-link" onclick="openModal('${{t.id}}')">${{t.id}}</span></td>
      <td>${{esc(t.client)}}</td>
      <td>${{planBadge(t.plan)}}</td>
      <td>${{esc(t.subject).slice(0,50)}}${{t.subject.length>50?'…':''}}</td>
      <td>${{badge(t.status)}}</td>
      <td>${{esc(t.blocker||'—')}}</td>
      <td>${{esc(t.owner)}}</td>
      <td>${{esc(t.openedBy)}}</td>
      <td class="${{daysColor(t)}}">${{t.daysOpen}}d${{t.daysOpen>10?' ⚠':''}}</td>
      <td>${{t.slaPercent}}%${{slaBar(t.slaPercent)}}</td>
    </tr>`).join('');
}}

// ── All Tickets Tab ───────────────────────────────────────────────────────────
function buildAllFilters() {{
  const sel = document.getElementById('fStatus');
  [...new Set(TICKETS.map(t=>t.status))].forEach(s => {{
    const o = document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o);
  }});
}}

function renderAll() {{
  const fSt  = document.getElementById('fStatus').value;
  const fPl  = document.getElementById('fPlan').value;
  const fAge = document.getElementById('fAge').value;
  const fPr  = document.getElementById('fPriority').value;
  const fSrch= document.getElementById('globalSearch').value.toLowerCase();

  const filtered = TICKETS.filter(t => {{
    if (fSt  && t.status   !== fSt)  return false;
    if (fPl  && t.plan     !== fPl)  return false;
    if (fPr  && t.priority !== fPr)  return false;
    if (fAge) {{
      if (fAge==='lt5'    && t.daysOpen>=5)                          return false;
      if (fAge==='5to10'  && (t.daysOpen<5||t.daysOpen>=10))         return false;
      if (fAge==='10to20' && (t.daysOpen<10||t.daysOpen>=20))        return false;
      if (fAge==='gt20'   && t.daysOpen<20)                          return false;
      // At Risk = open tickets only with daysOpen > 10
      if (fAge==='atrisk' && !(isOpen(t) && t.daysOpen>10))          return false;
    }}
    if (fSrch) {{
      const hay = [t.id,t.client,t.owner,t.openedBy,t.blocker,t.subject].join(' ').toLowerCase();
      if (!hay.includes(fSrch)) return false;
    }}
    return true;
  }});

  document.getElementById('allCount').textContent = `${{filtered.length}} tickets`;
  document.getElementById('allBody').innerHTML = filtered.map(t => `
    <tr class="${{t.daysOpen>10&&t.status!=='Resolved'?'danger':''}}">
      <td><span class="ticket-link" onclick="openModal('${{t.id}}')">${{t.id}}</span></td>
      <td>${{esc(t.client)}}</td>
      <td style="font-size:11px;color:var(--muted)">${{esc(t.clientId)}}</td>
      <td>${{esc(t.subject).slice(0,45)}}${{t.subject.length>45?'…':''}}</td>
      <td>${{badge(t.status)}}</td>
      <td>${{badge(t.priority)}}</td>
      <td style="font-size:12px;color:var(--muted)">${{esc(t.stage)}}</td>
      <td>${{esc(t.owner)}}</td>
      <td>${{esc(t.openedBy)}}</td>
      <td class="${{daysColor(t)}}">${{t.daysOpen}}d${{t.daysOpen>10&&t.status!=='Resolved'?' ⚠':''}}</td>
      <td>${{planBadge(t.plan)}}</td>
    </tr>`).join('');
}}

// ── Client Lookup ─────────────────────────────────────────────────────────────
function buildClients() {{
  const map = {{}};
  TICKETS.forEach(t => {{
    if (!map[t.client]) map[t.client] = {{name:t.client,id:t.clientId,tickets:[]}};
    map[t.client].tickets.push(t);
  }});

  window._clientMap = map;
  renderClients();
}}

function frustScore(tickets) {{
  const unres  = tickets.filter(t=>t.status!=='Resolved');
  const gt10   = tickets.filter(t=>t.daysOpen>10&&t.status!=='Resolved');
  const maxDays= Math.max(...tickets.map(t=>t.daysOpen),0);
  return Math.min(100, Math.round(unres.length*8 + gt10.length*10 + maxDays*0.7));
}}

function riskTier(score) {{
  if (score>=75) return {{label:'🚨 Critical', color:'var(--red)'}};
  if (score>=50) return {{label:'⚠ High',     color:'var(--amber)'}};
  if (score>=25) return {{label:'• Medium',   color:'var(--blue)'}};
  return              {{label:'✓ Low',        color:'var(--green)'}};
}}

function renderClients() {{
  const search  = document.getElementById('clientSearch').value.toLowerCase();
  const sortBy  = document.getElementById('clientSort').value;
  const cfFilter= document.querySelector('[data-cf].active')?.dataset.cf || 'all';
  const map     = window._clientMap;

  let clients = Object.values(map).filter(c => {{
    if (search && !c.name.toLowerCase().includes(search) && !c.id.toLowerCase().includes(search)) return false;
    if (cfFilter==='enterprise'    && !c.tickets.some(t=>t.plan==='Enterprise'))     return false;
    if (cfFilter==='nonenterprise' && !c.tickets.some(t=>t.plan==='Non-Enterprise')) return false;
    return true;
  }});

  clients = clients.map(c => ({{...c, score:frustScore(c.tickets)}}));
  if (sortBy==='risk')  clients.sort((a,b)=>b.score-a.score);
  if (sortBy==='total') clients.sort((a,b)=>b.tickets.length-a.tickets.length);
  if (sortBy==='name')  clients.sort((a,b)=>a.name.localeCompare(b.name));

  document.getElementById('clientGrid').innerHTML = clients.map(c => {{
    const open    = c.tickets.filter(t=>t.status!=='Resolved').length;
    const gt10    = c.tickets.filter(t=>t.daysOpen>10&&t.status!=='Resolved').length;
    const resolvd = c.tickets.filter(t=>t.status==='Resolved').length;
    const resPct  = Math.round(resolvd/c.tickets.length*100);
    const avgDays = Math.round(c.tickets.reduce((a,t)=>a+t.daysOpen,0)/c.tickets.length);
    const tier    = riskTier(c.score);
    const plan    = c.tickets.some(t=>t.plan==='Enterprise') ? 'Enterprise':'Non-Enterprise';
    const borderColor = tier.color;

    const ticketRows = c.tickets.map(t => `
      <tr onclick="openModal('${{t.id}}')" style="cursor:pointer">
        <td><span class="ticket-link">${{t.id}}</span></td>
        <td>${{esc(t.subject).slice(0,40)}}${{t.subject.length>40?'…':''}}</td>
        <td>${{badge(t.status)}}</td>
        <td>${{badge(t.priority)}}</td>
        <td>${{esc(t.owner)}}</td>
        <td class="${{daysColor(t)}}">${{t.daysOpen}}d</td>
      </tr>`).join('');

    return `<div class="client-card" style="border-left-color:${{borderColor}}" onclick="toggleClient(this)">
      <div class="client-header">
        <div>
          ${{planBadge(plan)}}
          <div class="client-name" style="margin-top:6px">${{esc(c.name)}}</div>
          <div class="client-id">ID: ${{esc(c.id)}}</div>
        </div>
        <span class="badge" style="background:${{tier.color}}20;color:${{tier.color}}">${{tier.label}}</span>
      </div>
      <div class="client-stats">
        <div class="client-stat"><span class="sv">${{c.tickets.length}}</span>Total</div>
        <div class="client-stat"><span class="sv">${{open}}</span>Open</div>
        <div class="client-stat"><span class="sv" style="color:${{gt10>0?'var(--red)':'inherit'}}">${{gt10}}</span>&gt;10d</div>
        <div class="client-stat"><span class="sv">${{resPct}}%</span>Resolved</div>
        <div class="client-stat"><span class="sv">${{avgDays}}d</span>Avg Open</div>
      </div>
      <div class="risk-bar-wrap">
        <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
          <span style="color:var(--muted)">Frustration Risk</span>
          <span style="color:${{tier.color}};font-weight:600">${{c.score}}/100</span>
        </div>
        <div class="risk-bar-track">
          <div class="risk-bar-fill" style="width:${{c.score}}%;background:${{tier.color}}"></div>
        </div>
      </div>
      <div class="client-expand">
        <div class="tbl-wrap"><table>
          <thead><tr><th>ID</th><th>Subject</th><th>Status</th><th>Priority</th><th>Assigned To</th><th>Days Open</th></tr></thead>
          <tbody>${{ticketRows}}</tbody>
        </table></div>
      </div>
    </div>`;
  }}).join('');
}}

function toggleClient(el) {{
  const expand = el.querySelector('.client-expand');
  if (expand) expand.classList.toggle('open');
}}

// ── Heatmap ───────────────────────────────────────────────────────────────────
function buildHeatmap() {{
  const now    = new Date();
  const days   = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const counts = {{}};

  TICKETS.forEach(t => {{
    const d = parseDate(t.createdDate);
    if (!d) return;
    const key = d.toISOString().slice(0,10);
    counts[key] = (counts[key]||0)+1;
  }});

  // Build 5-week grid starting from Monday 5 weeks ago
  const startDate = new Date(now);
  startDate.setDate(startDate.getDate() - startDate.getDay() - 28); // ~5 weeks back, align Monday

  const grid = document.getElementById('heatmapGrid');
  let html = '<div class="hm-label"></div>' + days.map(d=>`<div class="hm-label" style="justify-content:center">${{d}}</div>`).join('');

  for (let w=0; w<5; w++) {{
    html += `<div class="hm-label">W${{w+1}}</div>`;
    for (let d=0; d<7; d++) {{
      const date = new Date(startDate);
      date.setDate(startDate.getDate() + w*7 + d);
      const key  = date.toISOString().slice(0,10);
      const n    = counts[key]||0;
      const cls  = n===0?'hm-0':n<=3?'hm-1':n<=6?'hm-2':n<=10?'hm-3':n<=15?'hm-4':'hm-5';
      html += `<div class="hm-cell ${{cls}}" title="${{key}}: ${{n}} tickets">${{n||''}}</div>`;
    }}
  }}
  grid.innerHTML = html;

  // Workload chart
  const ownerOpen = {{}};
  TICKETS.filter(t=>t.status!=='Resolved').forEach(t => {{
    ownerOpen[t.owner] = (ownerOpen[t.owner]||0)+1;
  }});
  const wEntries = Object.entries(ownerOpen).sort((a,b)=>b[1]-a[1]);
  const wMax = wEntries[0]?.[1]||1;
  document.getElementById('workloadChart').innerHTML = wEntries.map(([n,c]) => `
    <div class="bar-row">
      <span>${{esc(n)}}</span>
      <div class="bar-track">
        <div class="bar-fill" style="width:${{Math.round(c/wMax*100)}}%;background:var(--blue)"></div>
      </div>
      <span style="color:var(--muted)">${{c}}</span>
    </div>`).join('');
}}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(id) {{
  const t = TICKETS.find(x=>x.id===id);
  if (!t) return;

  document.getElementById('modalId').textContent      = '#' + t.id;
  document.getElementById('modalSubject').textContent = t.subject;
  document.getElementById('modalAdoLink').href        = t.adoUrl;

  const alert = document.getElementById('modalAlert');
  alert.style.display = (t.daysOpen > 10 && t.status !== 'Resolved') ? 'block' : 'none';

  const dayColor = t.daysOpen > 10 ? 'var(--red)' : t.daysOpen > 5 ? 'var(--amber)' : 'var(--green)';
  document.getElementById('modalMeta').innerHTML = [
    ['Status',      badge(t.status)],
    ['Priority',    badge(t.priority)],
    ['Assigned To', esc(t.owner)],
    ['Days Open',   `<span class="big-days" style="color:${{dayColor}}">${{t.daysOpen}}</span>`],
    ['Stage',       esc(t.stage)],
    ['Blocker',     t.blocker ? `<span class="badge b-high">${{esc(t.blocker)}}</span>` : '—'],
    ['SLA',         t.slaPercent + '%' + slaBar(t.slaPercent)],
    ['Opened By',   esc(t.openedBy)],
    ['Plan',        planBadge(t.plan)],
    ['Client ID',   esc(t.clientId)||'—'],
  ].map(([l,v])=>`<div class="meta-item"><div class="mi-label">${{l}}</div><div class="mi-val">${{v}}</div></div>`).join('');

  const statusIdx = STATUS_ORDER.indexOf(t.status);
  document.getElementById('modalJourney').innerHTML = STATUS_ORDER.map((s,i) => {{
    let cls = 'j-future';
    if (i < statusIdx)  cls = 'j-past';
    if (i === statusIdx) cls = t.blocker ? 'j-current-blocked' : 'j-current';
    return `<div class="j-box ${{cls}}">${{s}}</div>`;
  }}).join('');

  document.getElementById('modalActivity').innerHTML = [
    `<div class="activity-entry">
      <div class="ae-meta">📋 ${{esc(t.openedBy)}} &nbsp;·&nbsp; ${{fmtDate(t.createdDate)}}</div>
      Ticket opened
    </div>`,
    `<div class="activity-entry">
      <div class="ae-meta">👤 Assigned to ${{esc(t.owner)}}</div>
      Current assignee
    </div>`,
    t.resolvedDate ? `<div class="activity-entry" style="border-color:var(--green)">
      <div class="ae-meta">✅ Resolved &nbsp;·&nbsp; ${{fmtDate(t.resolvedDate)}}</div>
      Ticket resolved after ${{t.daysOpen}} days
    </div>` : `<div class="activity-entry" style="border-color:${{t.daysOpen>10?'var(--red)':'var(--border)' }}">
      <div class="ae-meta">⏳ Currently: ${{esc(t.status)}}</div>
      Open for ${{t.daysOpen}} days
    </div>`,
  ].join('');

  document.getElementById('modal').classList.add('open');
}}

document.getElementById('modalClose').onclick = () => document.getElementById('modal').classList.remove('open');
document.getElementById('modal').onclick = e => {{ if(e.target===e.currentTarget) e.currentTarget.classList.remove('open'); }};
document.addEventListener('keydown', e => {{ if(e.key==='Escape') document.getElementById('modal').classList.remove('open'); }});

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.onclick = () => {{
    document.querySelectorAll('.tab-btn,.tab-panel').forEach(el=>el.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-'+btn.dataset.tab).classList.add('active');
  }};
}});

// ── Quick filters → All Tickets ───────────────────────────────────────────────
document.querySelectorAll('[data-qf]').forEach(btn => {{
  btn.onclick = () => {{
    document.querySelectorAll('[data-qf]').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    const qf = btn.dataset.qf;
    document.getElementById('fStatus').value = '';
    document.getElementById('fAge').value    = '';
    if (qf==='active') document.getElementById('fStatus').value = 'Active';
    if (qf==='new')    document.getElementById('fStatus').value = 'New';
    if (qf==='atrisk') document.getElementById('fAge').value    = 'atrisk';
    switchTab('alltickets');
    renderAll();
  }};
}});

function switchTab(name) {{
  document.querySelectorAll('.tab-btn,.tab-panel').forEach(el=>el.classList.remove('active'));
  document.querySelector(`[data-tab="${{name}}"]`).classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}}

function filterAllByStatus(status) {{
  document.getElementById('fStatus').value = status;
  switchTab('alltickets'); renderAll();
}}
function filterAllByAge(age) {{
  document.getElementById('fAge').value = age;
  switchTab('alltickets'); renderAll();
}}
function filterAllByBlocker(blocker) {{
  document.getElementById('globalSearch').value = blocker;
  switchTab('alltickets'); renderAll();
}}

// ── Global search ─────────────────────────────────────────────────────────────
document.getElementById('globalSearch').addEventListener('input', () => {{
  switchTab('alltickets'); renderAll();
}});

// ── All Tickets filters ───────────────────────────────────────────────────────
['fStatus','fPlan','fAge','fPriority'].forEach(id => {{
  document.getElementById(id).addEventListener('change', renderAll);
}});

// ── Client filters ────────────────────────────────────────────────────────────
document.getElementById('clientSearch').addEventListener('input', renderClients);
document.getElementById('clientSort').addEventListener('change', renderClients);
document.querySelectorAll('[data-cf]').forEach(btn => {{
  btn.onclick = () => {{
    document.querySelectorAll('[data-cf]').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    renderClients();
  }};
}});

// ── Init ──────────────────────────────────────────────────────────────────────
buildKPIs();
buildDonut();
buildAgeBands();
buildStageChart();
buildBlockerChart();
buildPeople();
buildLongest();
buildAllFilters();
renderAll();
buildClients();
buildHeatmap();
</script>
</body>
</html>"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3: WRITE index.html
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def write_html(html):
    out = Path(__file__).parent / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"   ✅  Wrote {out} ({round(len(html)/1024)}kb)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    raw_items   = fetch_tickets()
    tickets     = [transform_ticket(i) for i in raw_items]
    exported_at = datetime.now(timezone.utc)
    print(f"▶  Generating index.html for {len(tickets)} tickets...")
    html = generate_html(tickets, exported_at)
    write_html(html)
    print("✅  Done. Now run:")
    print('    git add index.html && git commit -m "Refresh dashboard" && git push')