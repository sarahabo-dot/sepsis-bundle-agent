# Sepsis Bundle Agent — Full Package

A component of the Shura ICU Clinical Decision Support System.
Governing principle: **AI proposes, physician decides.**

## What's in here

```
backend/    FastAPI + SQLAlchemy service — deterministic SOFA/bundle logic,
            JWT auth, dual sign-off for high-risk actions, append-only audit log
frontend/   React (Vite) app — logs in, talks to the backend, no scores
            computed client-side
docs/       PRD, TDD, Shadow-Mode Validation Plan, Egypt Regulatory Pathway Overview
docker-compose.yml   Runs backend + frontend together locally
```

## Run it (local, fastest path)

```bash
# 1. Backend
cd backend
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY and a SECRET_KEY
python seed_user.py    # creates login: sarah / changeme123
uvicorn api.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open the frontend URL Vite prints (typically http://localhost:5173), sign in, and use it.

## Or with Docker

```bash
cp backend/.env.example backend/.env   # fill in real values first
docker compose up --build
```

## Status of this package

This is a working, tested, authenticated, persistent full-stack system — not a static mockup. 25/25 backend unit tests pass, and the full login → SOFA calculation → bundle confirmation → audit trail flow has been verified end-to-end against a running server.

It is **not** cleared for use on real patients. Read `docs/Shadow_Mode_Validation_Plan.docx` before considering any real clinical use, and `docs/Egypt_Regulatory_Pathway_Overview.docx` for the regulatory landscape that would apply.

## What's genuinely production-grade vs. what still needs work

| Area | Status |
|---|---|
| Deterministic clinical logic (SOFA, bundle timer) | Solid — unit tested, no LLM involvement |
| Data validation (range/staleness/plausibility gates) | Solid — implements FMEA mitigations FM-01/02/03 |
| Authentication | Baseline JWT auth in place (FM-04 partial mitigation) — needs real user provisioning, password policy, and TLS before real deployment |
| Dual sign-off for high-risk actions | Enforced server-side (FM-05 mitigation) |
| Audit log | Persistent and append-only by convention — needs DB-level enforcement (grant restrictions) for real deployment |
| Hosting | Runs locally / via Docker — needs a real server, HTTPS, and a managed database for anything beyond your own machine |
| Regulatory / clinical governance | Not started — this is the long pole if real use is ever pursued; see the two governance documents |
