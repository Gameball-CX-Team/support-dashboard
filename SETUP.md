# Setup Guide
# ===========
# Complete step-by-step setup from zero to a live dashboard.
# Estimated time: 30 minutes.
#
# After setup, refreshing the dashboard is just:
#   python3 refresh.py
#   git add index.html && git commit -m "Refresh dashboard" && git push
# ============================================================


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 1 — INSTALL PYTHON 3 (skip if already installed)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Check first:
1. Press Cmd + Space, type "Terminal", press Enter
2. Type: python3 --version
   → If you see "Python 3.x.x" skip to Part 2.
   → If you see "command not found", do this:

Install Homebrew then Python:
1. Paste into Terminal, press Enter:
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
2. Enter your Mac password when prompted (nothing appears as you type — normal)
3. Press Enter when asked to continue. Wait 3–5 min.
4. If it says "Run these commands to add Homebrew to your PATH", run them.
5. Then: brew install python
6. Verify: python3 --version → Python 3.x.x ✅

No other installs needed. refresh.py uses Python standard library only.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 2 — INSTALL GITHUB DESKTOP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Go to: https://desktop.github.com
2. Download and install it
3. Open GitHub Desktop, sign in with your GitHub account ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 3 — CREATE THE GITHUB REPOSITORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Go to: https://github.com/organizations/Gameball-CX-Team/repositories/new
2. Fill in:
   - Owner:            Gameball-CX-Team
   - Repository name:  support-dashboard
   - Visibility:       Private
   - Check:            Add a README file
3. Click "Create repository" ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 4 — CLONE THE REPO TO YOUR MAC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open GitHub Desktop
2. File → Clone Repository
3. Find "Gameball-CX-Team/support-dashboard" in the list
4. Local path: ~/Documents/support-dashboard
5. Click Clone ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 5 — ADD THE PROJECT FILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Copy these files into ~/Documents/support-dashboard:

   refresh.py      → root of repo
   config.env      → root of repo
   .env.example    → root of repo
   .gitignore      → root of repo
   README.md       → root of repo (replace the default one)

Result:
   support-dashboard/
   ├── refresh.py
   ├── config.env
   ├── .env.example
   ├── .gitignore
   └── README.md
✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 6 — FILL IN config.env
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open config.env in any text editor
2. Replace "YourProjectNameHere" with your actual ADO project name
3. ADO_ORG is already set to "gameballers"
4. Save ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 7 — SET UP YOUR PERSONAL .env
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your .env holds your personal ADO token. It is gitignored — never uploaded.

1. In Terminal:
   cd ~/Documents/support-dashboard
   cp .env.example .env

2. Get a token from ADO:
   - Go to: https://dev.azure.com/gameballers/_usersSettings/tokens
   - Click "+ New Token"
   - Name: support-dashboard-read
   - Expiration: 1 year (set a calendar reminder)
   - Scopes: Custom defined → Work Items → Read only
   - Click Create → COPY THE TOKEN (shown only once)

3. Open .env, replace the placeholder with your real token, save ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 8 — RUN IT FOR THE FIRST TIME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. In Terminal:
   cd ~/Documents/support-dashboard
   python3 refresh.py

2. Expected output:
   ▶  Fetching ticket IDs from Azure DevOps...
      Found 247 issues
   ▶  Fetching ticket details...
   ▶  Generating index.html...
      ✅  Wrote index.html
   ✅  Done.

3. Open index.html in your browser to preview locally ✅

TROUBLESHOOTING:
   "Missing credentials"  → Check .env and config.env
   HTTP 401               → Token is wrong — redo Part 7
   HTTP 404               → ADO_PROJECT name is wrong — fix config.env
   Empty dashboard        → Verify ADO project uses "Issue" work item type


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 9 — PUSH AND ENABLE GITHUB PAGES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Push:
1. Open GitHub Desktop
2. All files listed with checkboxes — make sure all are checked
3. Summary: "Initial setup"
4. Commit to main → Push origin ✅

Enable GitHub Pages:
1. Go to: https://github.com/Gameball-CX-Team/support-dashboard/settings/pages
2. Source: Deploy from a branch
3. Branch: main, Folder: / (root)
4. Save → wait 2 minutes

Live URL:
https://gameball-cx-team.github.io/support-dashboard/ ✅


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ONGOING WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Whenever you want a fresh dashboard (same as your manager's workflow):

   python3 refresh.py
   git add index.html
   git commit -m "Refresh dashboard"
   git push

Live URL updates for everyone on the team.
