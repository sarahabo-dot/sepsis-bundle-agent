# Sepsis Bundle Agent — Full Package

A component of the Shura ICU Clinical Decision Support System.
Governing principle: **AI proposes, physician decides.**

## What's in here

```
backend/    FastAPI + SQLAlchemy service — deterministic SOFA/bundle logic,
            JWT auth, dual sign-off for high-risk actions, append-only audit log,
            a genuine tool-calling agent (agent.py), and real Web Push alerts
frontend/   React (Vite) app — logs in, talks to the backend, shows the agent's
            tool trace live, and can enable push notifications on this device
docs/       PRD, TDD, Shadow-Mode Validation Plan, Egypt Regulatory Pathway Overview
docker-compose.yml   Runs backend + frontend together locally
```

## Run it (local, fastest path)

```bash
# 1. Backend
cd backend
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY, SECRET_KEY, VAPID_* keys
python seed_user.py    # creates login: sarah / changeme123
uvicorn api.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
cp .env.example .env
npm run dev
```

## What's new in this version

- **agent.py**: a real tool-calling agent. Claude decides for itself which
  read-only tools to call (current SOFA, domain trend, bundle status, draft
  flags, deterioration timeline) before submitting a structured assessment
  via a `submit_assessment` tool call — not free text.
- **push_notifications.py**: real Web Push. The agent's final step pages
  every subscribed physician/nurse device, even if the app is closed.
- **/triage/priority**: ranks all active patients by acuity, deterministically.
- Frontend: an "Agent Consult" panel showing the live tool-call trace, and an
  "Enable alerts" button that subscribes the current device to push.

## Still required before real patient use

Read `docs/Shadow_Mode_Validation_Plan.docx` and
`docs/Egypt_Regulatory_Pathway_Overview.docx` before considering any real
clinical use. This package is not cleared for use on real patients.
