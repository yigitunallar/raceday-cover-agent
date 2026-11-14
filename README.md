# Raceday Cover Agent

Built for **Agents in Context Toronto: a MongoDB Hackathon**, track 3, "Finish the Job".

Buying raceday cover for a horse is manual and laborious, so owners miss it. If the horse dies in that race, the money is gone, and there is no second chance.

This agent takes a standing instruction, "cover my runners", and finishes the job for every horse before it leaves the stable: **one charge, one policy, one insurer record, proven even if it crashes halfway.**

All data in this repo is invented. No real insurer, horse, owner, price or limit appears here.

**Live demo:** https://raceday-cover-agent.vercel.app · **API:** https://raceday-cover-agent.onrender.com

## Try to break it (2 minutes)

1. Open the live demo. Press **Load race meeting**, **Owners confirm fit to run**, then **Kill right after the next charge**, then **Start agent**.
2. The agent dies the moment its first charge goes through, before it hears back: the money has moved, no policy exists.
3. Press **Start agent** again. It reads back from every system before acting, and finishes the meeting.
4. Check the scoreboard: every system shows **0 duplicates**. One charge, one policy, one insurer record per horse.
5. Now break a system: choose **insurer → Rename fields → Break it** and run again. The model maps the new fields once; the next time, **Atlas Vector Search** finds the saved fix and it takes one step.

## Where MongoDB Atlas does the work

| Need | Atlas feature | Where |
|---|---|---|
| Resume an agent mid-task after a kill | LangGraph **MongoDB checkpointer** (task ID = thread ID) | `agent/graph.py` |
| Prove what took effect in the outside world | `actions` ledger, **unique index on the operation ID**, intent saved before every call | `ledger.py`, `db.py` |
| Learn from past failures | **Atlas Vector Search** with **automated embeddings** (voyage-4) over `cases` | `agent/schema_recovery.py` |
| Explain every decision | `timeline` events, per-horse investigation trail, referral packs | `agent/investigator.py`, `agent/decider.py` |
| Durable mock systems | each outside system's records live in Atlas, so a restart loses nothing | `mocks/app.py` |

## Where the model decides (and where code says no)

The core rule: **the model decides what to do next; code decides whether it is allowed and whether it worked.** Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| The model decides | Code decides |
|---|---|
| Which evidence to check for each horse, one step at a time | Which checks the insurer requires before a price is allowed |
| How to recover when a step can't be proven (read back, re-send same ID, refund, escalate) | Which of those moves are allowed right now |
| How to map a renamed field | Whether the mapping is valid; whether the action is verified |
| The words: price explanations, underwriter packs, the phone call | Every number, checked against the facts the model was given |

The one human moment, the owner's "is the horse fit to run?", is a **real phone call**: an ElevenLabs voice agent over a Twilio number, whose answer comes back through our API. An unclear answer counts as no answer, never yes.

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

## Design documents

- [Full demo architecture](docs/ARCHITECTURE.md)
- [Current Python quote-first LLD review draft](docs/superpowers/specs/2026-11-14-raceday-cover-agent-python-lld.md)
- [Hackathon and Track 3 context](design-plans/toronto-agents-in-context-track-3-context.md)
- [Original high-level design](docs/superpowers/specs/2026-11-13-insurance-claim-agent-design.md)
- [Earlier low-level draft](docs/superpowers/specs/2026-11-13-raceday-cover-agent-lld.md) (superseded by the current Python review draft)

The quote-first LLD is a planning proposal for review and has not been reconciled with the full workflow in the existing architecture and scaffold. Review that scope difference before changing implementation.
