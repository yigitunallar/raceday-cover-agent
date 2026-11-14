# Raceday Cover Agent

Built for **Agents in Context Toronto: a MongoDB Hackathon**, track 3, "Finish the Job".

Buying raceday cover for a horse is manual and laborious, so owners miss it. If the horse dies in that race, the money is gone, and there is no second chance.

This agent takes a standing instruction, "cover my runners", and finishes the job for every horse before it leaves the stable: **one charge, one policy, one insurer record, proven even if it crashes halfway.**

All data in this repo is invented. No real insurer, horse, owner, price or limit appears here.

## How it works

- **The model decides what to do next. Code decides whether it is allowed and whether it worked.**
- Every change to an outside system goes through one guarded write: **guard → save intent → call with an operation ID → read back → record**. Only a verified read-back lets a task move on.
- After a crash the agent reads back first. Found: continue. Not found: re-send with the same operation ID. Nothing is sent twice.
- Each horse gets a **risk profile** (health, form, workload, breeding, official rating). The insurer's rules turn it into a price, with the reasons shown.
- Two lanes: **clean** (inside the limit, owner confirms fit to run, then pay, issue, report) and **referral** (over the limit, adverse answer, or a contradiction with past declarations: no charge, an underwriter gets a one-page pack).

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout

| Path | What it is |
|---|---|
| `src/raceday/ledger.py` | The guarded write and read-back. The heart of crash safety |
| `src/raceday/rules.py` | The insurer's rules: price, risk factors, limit, declaration history |
| `src/raceday/agent/graph.py` | One horse, start to finish, as a LangGraph graph with the MongoDB checkpointer |
| `src/raceday/worker.py` | The agent process. Kill it and restart it: nothing happens twice |
| `src/raceday/mocks/app.py` | Mock payment processor, policy platform, insurer and notifications, with chaos controls |
| `src/raceday/api/app.py` | API for the web page; also hosts the mocks and starts or kills the agent |
| `src/raceday/seed.py` | One invented race meeting with twelve runners |
| `web/` | The demo page: scoreboard, runners, kill button |
| `render.yaml` | Deploys the API (with the mocks) to Render |

## Run it locally

Needs Python 3.12 and a MongoDB Atlas connection string.

```bash
cp .env.example .env        # then fill in MONGODB_URI and ANTHROPIC_API_KEY
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/uvicorn raceday.api.app:app --app-dir src --port 8000
```

Open `web/index.html` in a browser, then: **Load race meeting → Owners confirm fit to run → Start agent**. Press **Kill agent** at any point, then **Start agent** again, and check that every system still shows 0 duplicates.

Tests:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests
```

## Deploy

- **API:** Render, from `render.yaml`. Set `MONGODB_URI` and `ANTHROPIC_API_KEY` in the Render dashboard.
- **Web page:** Vercel, from the `web/` folder. Point it at the API with `?api=https://<render-url>` or by editing `web/config.js`.

## Rules for contributors

- Never commit `.env` or any key.
- No real insurer names, prices, limits or policy wording. Every number is invented.
- No production data and no licensed racing-feed data.
