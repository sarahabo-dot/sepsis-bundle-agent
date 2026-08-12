# Sepsis Bundle Agent — Backend

Deterministic backend for the Sepsis Bundle Agent, a component of the Shura
ICU clinical decision support system. Follows Shura's core governance
principle: **AI proposes, physician decides.**

## Module map

| Module | Responsibility | Calls an LLM? |
|---|---|---|
| `models.py` | Shared data structures | No |
| `sofa_calculator.py` | Deterministic SOFA scoring | No |
| `bundle_tracker.py` | Hour-1 bundle state machine, conditional item relevance, timers | No |
| `data_validation.py` | FMEA-driven mitigations: range, staleness, plausibility checks; dual-confirmation gating | No |
| `provenance_log.py` | Append-only audit trail | No |
| `api/main.py` | HTTP layer (FastAPI) | **Yes — only `/interpretation`** |

Every clinical number in this system is computed by plain Python. The LLM
(Claude, via `/interpretation`) only narrates numbers it is handed; it never
recalculates a score or decides bundle status.

## Run locally

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # fill in ANTHROPIC_API_KEY and SECRET_KEY
export $(cat .env | xargs)  # or use a tool like python-dotenv / direnv
python seed_user.py         # creates a first login: sarah / changeme123 (change immediately)
uvicorn api.main:app --reload --port 8000
```

Note: `passlib[bcrypt]` currently has a known compatibility issue with `bcrypt>=4.1`; `requirements.txt` pins `bcrypt==4.0.1` to avoid it. If you see a bcrypt version error, confirm that pin is installed.

## Run with Docker

```bash
docker compose up --build
```

This starts the backend on :8000 and the frontend dev server on :5173.

## Run tests

```bash
cd backend
python -m pytest tests/ -v
```

## FMEA traceability

| Failure Mode | Mitigation | Module |
|---|---|---|
| FM-01 Contaminated/swapped sample | Rate-of-change plausibility check flags implausible jumps as DRAFT | `data_validation.plausibility_check` |
| FM-02 Manual data entry error | Physiologic range check flags out-of-range values as DRAFT | `data_validation.range_check` |
| FM-03 Data transfer error | Freshness window; stale values excluded from live scoring | `data_validation.staleness_check` |
| FM-04 Hacking / unauthorized access | Out of scope for this prototype — see TDD "Security & Deployment" section | — |
| FM-05 No double-checking before high-risk action | Dual confirmation (`acknowledged_by` + `confirmed_by`) required for pressors, fluids, antibiotics | `data_validation.requires_dual_confirmation`, `api/main.bundle_confirm` |

## API surface

- `POST /auth/login` — form-encoded username/password, returns a JWT
- `POST /sofa/calculate` — deterministic SOFA score + completeness + Sepsis-3 delta check (auth required)
- `POST /bundle/start` — starts the Hour-1 recognition clock (auth required)
- `GET /bundle/status` — current bundle item states, overdue flags (auth required)
- `POST /bundle/confirm` — physician confirms a bundle item; high-risk items (antibiotics, fluids, pressors) require `confirmed_by` to be a *different* authenticated user than the one making the request (auth required)
- `POST /interpretation` — Claude narrates already-computed values; the only LLM call in the service (auth required)
- `GET /audit/events` — append-only audit log + high-risk attribution completeness KPI (auth required)
- `GET /health` — liveness check, no auth

All endpoints except `/auth/login` and `/health` require `Authorization: Bearer <token>`.

## Data persistence

Prototype default is SQLite (`sepsis_agent.db`, created on first run). Set `DATABASE_URL` to a Postgres connection string for anything beyond local development. The `AuditEventRecord` table is append-only *by convention* in this codebase — before any real deployment, enforce that at the database grant level (`REVOKE UPDATE, DELETE ON audit_events FROM app_role`).

## Still required before real patient use

This backend addresses FM-01, FM-02, FM-03, and FM-05 from the project FMEA, and provides a baseline mitigation for FM-04 (authentication). It does **not** by itself constitute a deployable clinical system. See the companion documents:

- `docs/Shadow_Mode_Validation_Plan.docx` — how to validate this system safely before it touches real patient care
- `docs/Egypt_Regulatory_Pathway_Overview.docx` — regulatory landscape overview (not legal advice)
