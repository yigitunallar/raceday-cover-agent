# Raceday Cover Agent: Low-Level Design

**Status:** current review draft

**Updated:** 2026-11-14

**Implementation language:** Python

**First build:** one horse, starting-gate quote only; no payment or policy issuance in this slice.

This LLD expands the supplied architecture and meeting notes into a buildable workflow. It uses a real historical horse-racing data API where an external horse can be matched, a seeded upcoming race, mocked private health/owner/insurance data, and Atlas for durable task evidence, LangGraph checkpoints, and recovery cases.

## 1. Decisions for the first build

- Python is the main language. Use LangGraph’s Python `StateGraph`, MongoDB Atlas, and the Python MongoDB LangGraph checkpointer.
- One demo run evaluates one horse for one seeded upcoming race. Other fixtures exist for negative cases and validation.
- The first output is a **starting-gate quote**. No money moves, policy is issued, insurer is notified, or owner message is sent in this phase.
- There is no human approval or review pause. The fit-to-run declaration is pre-supplied in the task/mock record. Missing, false, or contradictory required inputs follow deterministic rules and stop or mark the quote incomplete.
- The LLM selects among approved read tools and explains evidence. Python code performs validation, eligibility, valuation, risk adjustments, pricing, persistence, and verification.
- All horse/owner/health/value/rate fixtures are synthetic. The real API may enrich historical form/pedigree; it never supplies the demo’s upcoming declared race.
- Use a matrix and named risk rules, not trained ML. Every matrix amount and factor is invented for demonstration and stored with a version.
- Policy issuer remains unresolved for the later binding phase. The supplied Claude note says our platform issues the policy and reports it to the insurer; earlier discussion said the insurer issues it after payment. This does not block the quote-only build.

## 2. What the agent does, in order

### 2.1 Start or resume

1. A caller explicitly submits a task containing `taskId`, `raceId`, `horseId`, `ownerId`, cover type, and the pre-supplied declaration reference/value.
2. If there is no requested task, the agent stays idle. An empty Atlas database is not permission to invent a task.
3. Use `taskId` as LangGraph `thread_id`. Load the latest graph checkpoint and the task/stage records from Atlas. The graph state stores IDs and progress markers; Atlas stores evidence and outputs.
4. If the task is absent, report it as missing rather than silently creating a different task. If Atlas is unavailable, do not claim that work is empty or complete.

### 2.2 Confirm the race and runner

1. Read the target race and runner from the seeded `races` fixture. This is the demo source of truth for the upcoming race, declared status, course, race type/class, scheduled start, and loading/departure deadline.
2. Confirm that the requested `horseId` appears in the runner list and that `raceStatus == "declared"`. “Entered” is provisional; it is not enough for an insurable runner.
3. Store the read time, fixture version, and source fields. Use one absolute timestamp (`off_dt` / canonical `offAt` in UTC) for time comparisons; display-only `off_time` must not drive eligibility.

### 2.3 Stage 1 — health and declaration checks

Read the synthetic owner/declaration and health service for:

- supplied fit-to-run answer, who supplied it, and when;
- available vet records, prior injuries/surgery, and declaration history;
- optional measurements such as weight, with measurement date and unit;
- source update time and whether the feed returned a record or no record.

Do not prompt an owner in the middle of this run. A missing fit-to-run answer is a missing required fact; an adverse or contradictory declaration follows the matrix’s `no_quote` rule. An absent medical record is **unknown**, not a clean bill of health.

### 2.4 Stage 2 — racing history and conditions

1. Use the external horse ID mapping to fetch historical form and pedigree from the race-data provider when available.
2. Calculate facts from runs in ordinary Python: recent start count, days since last run, finishes/non-finishes, win/place counts, and rating trend. Preserve each source run used in a derived fact.
3. Read course weather for the target race time and, when the historical API has the needed data, compare prior performance with similar going/weather. Weather is contextual in v1 unless an explicit mock rule maps a condition to a pricing or quote outcome.
4. If the API returns no matching horse, stale data, rate limits, or an unavailable endpoint, record that limitation. Use a fixture fallback only for supporting data and label it `isMock: true`; never claim a fixture is live API evidence.

### 2.5 Stage 3 — completeness, risk, value, and quote

1. Normalize raw provider shapes to the internal schema and validate types, IDs, timestamps, units, freshness, and cross-references.
2. Classify each input as required or supporting according to the mock insurer rules. Missing required identity/eligibility/value facts means `incomplete` and no quote. Missing supporting evidence allows a `provisional` quote with explicit warnings. Never fill missing values with zero or “healthy.”
3. Build a structured risk profile using fixed rules; do not ask the LLM to create risk facts or choose a premium.
4. Determine a supported full-horse value from synthetic valuation evidence and apply ownership share to derive covered value.
5. Evaluate the versioned underwriting matrix. Unsupported/over-limit cases stop automatically; there is no underwriter referral in this no-human first build.
6. Calculate the starting-gate quote, produce a plain-language explanation, save it with all inputs and rule versions, read it back, then mark the task complete.

## 3. LangGraph design

LangGraph is the stage manager and checkpointed workflow. The model is called inside an agent node with an allowlist of data-read tools. The graph’s deterministic nodes control the order and enforce business rules.

```text
START
  → load_or_resume_task
  → verify_seeded_race_and_runner
  → health_declaration_agent ↔ approved_read_tools
  → racing_conditions_agent ↔ approved_read_tools
  → normalize_and_validate
  → completeness_gate
      ├─ required input missing → save_incomplete → END
      ├─ source/schema failure → classify_failure → vector_recovery_lookup
      │      → validate_suggested_mapping → retry_read_once/bounded → continue or unresolved
      └─ sufficient evidence → build_risk_profile
  → value_rules
  → eligibility_and_authority_gate
      ├─ blocked/no automated quote → save_reason → END
      └─ allowed → quote_matrix
  → explain_quote
  → persist_quote
  → read_back_and_verify_quote
  → mark_complete
  → END
```

### Graph state

Keep only the information needed to route and resume:

```python
class AgentState(TypedDict):
    task_id: str
    race_id: str
    horse_id: str
    owner_id: str
    current_stage: str
    evidence_ids: list[str]
    risk_profile_id: str | None
    quote_id: str | None
    outcome: str | None  # quoted, provisional, incomplete, no_quote, unresolved
    warnings: list[str]
```

The current task facts and original source payloads live in Atlas, not in a giant prompt or only in the LLM conversation. On resume, reload by exact ID. Re-read volatile race/declaration/loading facts before quoting; reuse other successfully stored evidence unless its freshness rule requires a refresh.

### Tool boundaries

Read tools exposed to the agent:

- `get_seeded_race(race_id)`
- `get_race_api_form(source_horse_id)`
- `get_race_api_pedigree(source_horse_id)`
- `get_health_records(horse_id)`
- `get_owner_instruction(owner_id, horse_id, race_id)`
- `get_course_weather(course_id, forecast_for)`
- `search_recovery_cases(system, failure_type, error_summary)`

Only application code may save tasks, evidence, profiles, and quotes. The LLM receives no MongoDB URI and cannot choose arbitrary collections or mark its own result verified. Quote math is a normal Python function over validated inputs.

## 4. Data providers and API contracts

| Source | First-build choice | Data returned to the workflow |
|---|---|---|
| Upcoming race and declared runners | Seeded Atlas fixture, read-only to the agent | Race identity/status, runners, scheduled off time, course, region/type/class, loading deadline |
| Historical form/pedigree | Horse Racing API historical endpoints when an ID matches | Recent runs/results, ratings, pedigree; source ID and provider `data_as_of` |
| Course weather | Open-Meteo forecast API with labelled seeded fallback | Hourly weather around course coordinates/race time; units and retrieval time |
| Owner/declaration | Mock read-only API/fixture | Standing instruction, owner country, share, fit-to-run answer and provenance |
| Veterinary/health | Mock read-only API/fixture | Health records, measurements, declarations, freshness/completeness |
| Value/sale evidence | Mock fixture | Purchase/latest sale, currency, date, venue/lot, performance and pedigree evidence |
| Insurance quote rules | Mock versioned matrix | Base rate, risk modifiers, minimum premium, tax, required inputs, limits, decisions |

The public race API offers race, results, horse-form, and pedigree endpoints, but its free tier is historical-only; seed the upcoming race rather than relying on a live racecard. Provider access, keys, quotas, and terms must be checked before the demo. [API documentation](https://apihorseracing.com/documentation) · [Free-key limits](https://apihorseracing.com/documentation/free-key)

Weather is an optional supporting source with a fixture fallback. Open-Meteo returns time-indexed weather variables for coordinates. [Forecast API documentation](https://open-meteo.com/en/docs)

No general public feed for an individual horse’s private vet history was found for this plan. The Equine Data Standard describes structured exchange formats, not a universal public source of horse medical records. Mock health data and owner permission/provenance for any later integration. [Equine Data Standard](https://eqds.org/)

### Shared source-result envelope

Every adapter returns a consistent envelope so the graph can reason about source quality:

```python
class SourceResult(BaseModel):
    source_system: str
    source_record_id: str | None
    retrieved_at: datetime
    source_updated_at: datetime | None
    is_mock: bool
    status: Literal["found", "not_found", "unavailable", "stale"]
    raw_data: dict[str, Any] | None
    normalized_data: dict[str, Any] | None
    warnings: list[str]
```

Use Pydantic models at API and Atlas boundaries. Use `Decimal` or integer minor currency units for monetary calculations. Keep raw feed records separate from canonical models so schema changes are observable.

## 5. Mock data formats

All records are invented and seeded deterministically. Use stable IDs; names are display fields and never join keys.

### Race and runner

```json
{
  "race_id": "rac_demo_001",
  "date": "2026-11-15",
  "off_dt": "2026-11-15T15:10:00Z",
  "off_time": "3:10",
  "course": "Demo Downs",
  "region": "GB",
  "race_name": "Demo Handicap",
  "type": "Flat",
  "race_class": "Class 3",
  "is_abandoned": false,
  "race_status": "declared",
  "updatedAt": "2026-11-14T16:00:00Z",
  "course_location": {"latitude": 51.5, "longitude": -0.1},
  "runners": [
    {
      "horse_id": "hrs_demo_001",
      "source_horse_id": "provider-id-if-matched",
      "horse": "North Star",
      "age": "5",
      "sex": "Mare",
      "owner_id": "own_demo_001",
      "trainer_id": "trn_demo_001",
      "ofr": "92",
      "declared": true
    }
  ]
}
```

Intentional feed drift fixtures use upcoming names such as `off_time`, `race_class`, `distance_f`, and results names such as `off`, `class`, `dist_f`. The normalizer maps approved aliases to canonical names (`offAt`, `raceClass`, `distanceFurlongs`). It rejects unknown or type-incompatible fields rather than guessing.

### Owner, declaration, and health records

```json
{
  "owner_id": "own_demo_001",
  "country": "GB",
  "currency": "GBP",
  "standing_instruction": {"cover_declared_runners": true},
  "entries": [{
    "horse_id": "hrs_demo_001",
    "ownership_share": 0.5,
    "loading_at": "2026-11-15T13:00:00Z",
    "fit_to_run": {"answer": true, "declared_by": "synthetic_owner", "declared_at": "2026-11-14T15:00:00Z"}
  }]
}
```

```json
{
  "horse_id": "hrs_demo_001",
  "data_as_of": "2026-11-14T12:00:00Z",
  "weight": {"value": 500, "unit": "kg", "measured_at": "2026-11-12T10:00:00Z"},
  "health_records": [
    {"date": "2026-10-20", "kind": "vet_exam", "result": "cleared", "severity": "low", "source_id": "vet_demo_01"}
  ],
  "declarations": [
    {"date": "2026-10-20", "race_id": "rac_past_001", "answer": true, "given_by": "synthetic_owner"}
  ]
}
```

Fixtures should include: a clean eligible horse; optional health/weight gap; required declaration gap; adverse health fact; contradictory declaration; no matching historical API ID; already insured horse; entry-only runner; unsupported region/type; over-limit value; and duplicate horse names with different IDs.

### Form, conditions, and valuation evidence

Store race history as dated runs with `race_id`, `date`, `finished`, `position`, `rating`, `going`, and source reference. Derive `runs_last_30_days`, `days_since_last_run`, wins/places, and non-finish counts in Python. Store weather by `course_id`, coordinates, forecast/observation time, temperature, wind, precipitation, and units.

Store valuation evidence separately: horse ID, latest sale/purchase date and amount, currency, venue/lot, performance summary, pedigree band, evidence source, and confidence/completeness. Do not let the LLM estimate a sale value.

## 6. Mock underwriting matrix and calculation

### Matrix shape

Use a versioned document in `pricing_matrix`. Rates and limits are fictional demo values.

```json
{
  "matrix_version": "demo-2026-11-v1",
  "currency": "GBP",
  "effective_at": "2026-11-14T00:00:00Z",
  "automated_value_limit": 250000,
  "required_fields": ["horse_id", "race_status", "age", "sex", "race_type", "cover_type", "fit_to_run", "value_basis", "ownership_share"],
  "base_rates": [
    {"age_band": "4-6", "sex": "Mare", "race_type": "Flat", "cover_type": "starting_gate", "rate": "0.0080", "minimum_premium": "150.00"}
  ],
  "risk_rules": [
    {"factor": "recent_health_issue", "condition": "present", "premium_adjustment_pct": "0.20", "disposition": "provisional", "reason": "Recent health record requires review under demo rules."},
    {"factor": "high_workload", "condition": "runs_last_30_days >= 4", "premium_adjustment_pct": "0.10", "disposition": "allow", "reason": "High recent workload."},
    {"factor": "fit_to_run", "condition": "false_or_missing", "premium_adjustment_pct": "0", "disposition": "no_quote", "reason": "Required declaration is not positive."}
  ],
  "tax_by_owner_country": {"GB": "0.05"},
  "max_total_risk_adjustment_pct": "1.00",
  "optional_fields": ["weight", "weather_detail", "pedigree_detail"]
}
```

The implementation looks up exactly one base-rate row. Missing/ambiguous rows produce `no_quote`; there is no LLM interpolation. Each risk rule can allow, make the quote provisional, or block the quote. The sum of permitted percentage adjustments is capped by `max_total_risk_adjustment_pct` so stacked mock factors cannot grow without bound.

### Value and premium are different quantities

- **Full horse value:** use a recent purchase/auction amount when supplied and valid. If unavailable, use a synthetic comparables table keyed by performance and pedigree bands; mark that basis as provisional. Do not count vet bills as value. Health is not an automatic deduction from market value in this demo.
- **Covered value:** `full_horse_value × ownership_share`. Enforce `0 < ownership_share <= 1`.
- **Base premium:** `covered_value × matching_base_rate`.
- **Risk-adjusted premium:** `base_premium × (1 + sum(matching premium adjustments))`, with the matrix cap applied.
- **Pre-tax premium:** the larger of risk-adjusted premium and the minimum premium.
- **Quote total:** `round(pre_tax_premium × (1 + tax_rate), currency minor unit)`.

Illustration only: full value £120,000 × 50% ownership = £60,000 covered value. At a fictional 0.8% rate, base premium = £480. If the mock risk rules add 20% for a health factor and 10% for workload, adjusted premium = £480 × 1.30 = £624. With a fictional 5% tax, quote total = £655.20. The actual seeded rates, tax, thresholds, and factors must be visibly labelled as invented.

### Quote record

Persist the quote inputs, outcome, and reasons so it can be recomputed independently:

```json
{
  "quote_id": "task_demo_001:demo-2026-11-v1:starting_gate",
  "task_id": "task_demo_001",
  "status": "provisional",
  "cover_type": "starting_gate",
  "currency": "GBP",
  "full_horse_value": "120000.00",
  "valuation_basis": "latest_sale_and_form",
  "ownership_share": "0.50",
  "covered_value": "60000.00",
  "base_rate": "0.0080",
  "base_premium": "480.00",
  "risk_adjustments": [
    {"factor": "recent_health_issue", "adjustment_pct": "0.20", "reason": "Recent health record."},
    {"factor": "high_workload", "adjustment_pct": "0.10", "reason": "Four runs in the last 30 days."}
  ],
  "premium_before_tax": "624.00",
  "tax_rate": "0.05",
  "total": "655.20",
  "matrix_version": "demo-2026-11-v1",
  "evidence_ids": ["ev_health_001", "ev_form_001", "ev_value_001"],
  "warnings": ["Some supporting pedigree detail is unavailable."],
  "created_at": "2026-11-14T18:00:00Z"
}
```

Amounts are decimal strings in JSON and `Decimal` values in Python. The numbers are illustrative; each saved quote must contain the exact inputs and rules actually used.

### Required versus supporting data

- Required for a valid quote: matched horse/race identity, declared status, cover type, owner share, fit-to-run answer, age/sex/race type, supported value basis, and a matching rate row.
- Supporting: detailed weight (unless a particular rule requires it), some pedigree details, older race history, and weather. A gap can produce a provisional quote and a warning.
- If supporting health history is absent, the risk profile says `health_data_status: unknown`; it never says “clear.”
- No automated quote above the team’s proposed £250,000 authority limit. Since there is no human loop, the task ends with `outside_automated_authority` rather than referring it.

## 7. Atlas layout and initialization

Use one Atlas database, `raceday_demo`, with collections split by purpose. The agent’s data-access role is read-only on reference fixtures; application code writes task/evidence/quote records; mock services own their mock-system writes. Do not provide a MongoDB URI to the LLM.

### Python setup

Initial Python dependencies:

```text
langgraph
langgraph-checkpoint-mongodb
pymongo
pydantic
httpx
```

The LLM provider and embedding provider stay behind configurable adapters; choose their integrations when the team knows which API keys/credits it has. LangGraph’s MongoDB saver is initialized with a PyMongo client and a dedicated checkpoint database/collections, then passed to `compile`. Every invocation supplies the task ID as the `thread_id`:

```python
from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver

client = MongoClient(MONGODB_URI)
checkpointer = MongoDBSaver(client, db_name="raceday_checkpoints")
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": task_id}}
result = graph.invoke(task_input, config)
```

The checkpointer manages its own checkpoint collections and indexes. Business tasks, evidence, quotes, and recovery cases stay in the application’s separate `raceday_demo` database. The documented Python integration uses `langgraph-checkpoint-mongodb` and `MongoDBSaver`; exact compatible package versions should be checked when implementation begins. [MongoDB’s LangGraph integration](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/) · [MongoDBSaver implementation](https://github.com/langchain-ai/langchain-mongodb/blob/main/libs/langgraph-checkpoint-mongodb/langgraph/checkpoint/mongodb/saver.py)

| Collection | One document represents | Access |
|---|---|---|
| `races` | Race fixture with embedded runners | Seed/admin write; agent read-only |
| `owners` | Synthetic owner, standing instruction, shares, supplied declaration | Seed/admin write; agent read-only |
| `health_records` | Synthetic vet/health snapshot | Seed/admin write; agent read-only |
| `race_history` | Historical form snapshot/cache and external ID mapping | API adapter writes; agent reads through adapter |
| `weather_snapshots` | Conditions used for a task | Weather adapter writes; agent reads through adapter |
| `valuation_evidence` | Sale/purchase and performance/pedigree evidence | Seed/admin write; agent read-only |
| `pricing_matrix` | Versioned fake pricing/eligibility rules | Seed/admin write; quote engine read-only |
| `tasks` | One horse/race quote attempt and current status | Application read/write |
| `stage_evidence` | One source result or derived profile, with provenance | Application read/write |
| `quotes` | Calculated quote and full rule/input references | Application read/write; unique quote key |
| `timeline` | Ordered human-readable events | Application append/read |
| `recovery_cases` | Reviewed failure and safe recovery note plus embedding | Seed/admin write; agent search-only |
| `system_profiles` | Versioned, approved provider field aliases/shapes | Seed/admin or guarded adapter update |
| `checkpoints`, `checkpoint_writes` | LangGraph checkpoint data | Python checkpointer-managed |

Create indexes for unique task IDs, quote IDs, and provider horse IDs; query indexes on task ID and source ID. At startup configure `MongoDBSaver` from `langgraph.checkpoint.mongodb`, run its setup, and invoke the graph with `{"configurable": {"thread_id": task_id}}`. MongoDB documents this checkpointer and thread-based state persistence. [MongoDB LangGraph integration](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/)

Initialization order: create Atlas project/cluster and database role; create collections and unique indexes; load and validate fixtures; configure checkpointer; create the Vector Search index after recovery embeddings are inserted; wait for index status `READY`; then start API adapters and the graph. Keep URI/API keys in environment variables, not source, prompts, or checked-in fixtures.

## 8. Vector Search and failure recovery

### What is stored and searched

Seed a small reviewed set of `recovery_cases` such as:

1. Race API renamed `race_class` to `class`.
2. Results feed renamed `distance_f` to `dist_f`.
3. Historical form endpoint is rate-limited or returns stale data.
4. Weather API is unavailable and the approved fixture fallback is used.

Each recovery case contains `case_id`, `system`, `failure_type`, `profile_version`, short error description, safe checks, approved field mapping or fallback, postcondition, and `embedding`. Create an Atlas Vector Search index on `embedding` with dimensions matching the selected embedding model and filter fields `system`, `failure_type`, and `profile_version`. Use the same embedding model for seed and query vectors. The model provider is configurable; the exact dimensions must be set from that model at setup. [Atlas Vector Search documentation](https://www.mongodb.com/docs/atlas/atlas-search/operators-collectors/vectorsearch/)

Index shape (replace `<EMBEDDING_DIMENSIONS>` with the selected embedding model’s actual output size before creating the index):

```json
{
  "fields": [
    {"type": "vector", "path": "embedding", "numDimensions": <EMBEDDING_DIMENSIONS>, "similarity": "cosine"},
    {"type": "filter", "path": "system"},
    {"type": "filter", "path": "failure_type"},
    {"type": "filter", "path": "profile_version"}
  ]
}
```

The recovery tool embeds a compact error description and queries Atlas with failure metadata filters. For example:

```python
pipeline = [
    {
        "$vectorSearch": {
            "index": "recovery_cases_vector",
            "path": "embedding",
            "queryVector": query_embedding,
            "numCandidates": 30,
            "limit": 3,
            "filter": {
                "$and": [
                    {"system": {"$eq": "race_data_api"}},
                    {"failure_type": {"$eq": "schema_mismatch"}}
                ]
            }
        }
    },
    {
        "$project": {
            "system": 1,
            "failure_type": 1,
            "profile_version": 1,
            "safe_checks": 1,
            "approved_mapping": 1,
            "score": {"$meta": "vectorSearchScore"}
        }
    }
]
```

The returned documents are suggestions only; validate any mapping against `system_profiles` and Pydantic before retrying the source read.

### Recovery path

1. Adapter classifies a failure as timeout, rate limit, missing record, stale data, or schema mismatch.
2. Deterministic retry/backoff handles transient timeouts/rate limits first. Vector Search is used when a prior case may help, especially for schema mismatch.
3. Query Vector Search with the error summary and metadata filters; return at most three cases.
4. Treat results as suggestions. Check any proposed alias/fallback against an allowlisted `system_profiles` schema and Pydantic validation.
5. Retry the read with the validated mapping or approved fallback. Save new evidence with source and fallback provenance.
6. If required facts still cannot be established, mark `incomplete` or `unresolved`; do not invent facts or continue to pricing.

Vector Search does **not** find the active task, determine current race status, establish value, authorize a quote, or verify that a target system wrote something. Exact task lookup is by `taskId`; authoritative facts are re-read from their owning source. Vector retrieval can never override deterministic rules.

### Restart semantics for quote-only v1

On restart, load the checkpoint and task. For each completed stage, read its Atlas evidence reference and validate it; refresh volatile race/declaration status before quoting. Resume from the first unfinished stage. Save a quote with a deterministic key such as `{taskId}:{matrixVersion}:starting_gate`; if it already exists, read and verify it instead of creating a duplicate. If Atlas cannot save the quote, do not mark the task complete.

Payment/policy writes are out of this slice. When added later, they must use the full intent → same operation ID → target read-back → verification protocol from the partner architecture note.

## 9. Mock generation and validation plan

Create deterministic seed fixtures with a fixed seed so repeated runs yield the same horse IDs, data gaps, risk findings, and quotes. Include one happy-path demo horse plus fixture variants for each rejection/gap scenario. Keep reference fixture collections read-only to the agent.

Before insert and on adapter reads, validate:

- required IDs exist and are unique; joins use IDs, never names;
- enum values such as `race_status`, sex, race type, and cover type are allowed;
- timestamps have timezone/UTC meaning and are parseable;
- numeric strings are parsed explicitly; invalid text never becomes zero;
- ownership share is greater than zero and no greater than one;
- amounts/currencies are valid and monetary arithmetic uses decimal/minor units;
- race runner references, owner entries, source horse IDs, and valuation evidence resolve;
- `off_dt` is used for time logic; display `off_time` is not;
- sample quote outputs recompute exactly from the matrix version and inputs.

Use Pydantic for runtime validation. Seed command must fail loudly if input is invalid, verify inserted records by reading them back, and report fixture counts and expected test-case IDs.

## 10. Acceptance scenarios and success measures

1. Happy path: declared runner, required data complete, one reproducible starting-gate quote saved and verified.
2. Entry-only, abandoned, started, or loaded race: no quote and an explicit reason.
3. Missing required input or false/missing fit-to-run declaration: `incomplete`/`no_quote`, no invented value or premium.
4. Missing optional weight/pedigree/weather: provisional quote with a visible warning; unknown health is not clean health.
5. Health risk, high workload, and race-form fixture: matching named matrix adjustments are included once and shown in the explanation.
6. Over-limit or unsupported matrix row: stop with no automated quote and no human referral.
7. Duplicate horse names: correct result by stable ID.
8. API timeout, rate limit, stale data, no external horse match, and field rename: correct labelled fallback or safe incomplete/unresolved status; schema recovery uses a validated Vector Search suggestion.
9. Kill after evidence save but before checkpoint: restart reuses evidence and produces only one quote record.
10. Vector Search returns relevant failure notes under the correct metadata filters and cannot bypass validators or eligibility checks.

Success for the first slice means the quote is deterministic and explainable from saved evidence and a matrix version; data provenance and uncertainty are visible; restart does not duplicate quotes; and no unsupported case is silently treated as eligible.

## 11. Later binding phase (not built in v1)

After the quote workflow is accepted, a later phase can add a standing purchase authorization and maximum premium, final declaration/loading recheck, payment, policy issuance, insurer reporting, owner notification, cancellation/refund, and the kill-after-accepted-write demo. Each external write will require a saved intent, stable operation ID, target lookup by that ID, deterministic read-back verification, and evidence saved to Atlas. No human approval loop is added; unsupported or ambiguous cases stop safely.

Before that phase, decide whether our platform or the insurer issues the policy, the compensation rule if payment succeeds but policy issuance definitively fails, and the exact coverage effective start/end event. Starting-gate v1 quotes assume the covered interval is from the mock yard-departure event until the horse reaches the starting gate; confirm that product wording before binding.

## References

- Updated architecture/planning note and meeting notes supplied by the user (2026-11-13/14).
- [MongoDB LangGraph integration for Python](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/)
- [LangGraph Python checkpoint and thread concepts](https://reference.langchain.com/python/langgraph/checkpoints)
- [MongoDBSaver Python implementation and configuration](https://github.com/langchain-ai/langchain-mongodb/blob/main/libs/langgraph-checkpoint-mongodb/langgraph/checkpoint/mongodb/saver.py)
- [Horse Racing API documentation](https://apihorseracing.com/documentation) and [free-key limits](https://apihorseracing.com/documentation/free-key)
- [Open-Meteo forecast API](https://open-meteo.com/en/docs)
- [Equine Data Standard](https://eqds.org/)
- [MongoDB Atlas Vector Search](https://www.mongodb.com/docs/atlas/atlas-search/operators-collectors/vectorsearch/)
- [Markel horse mortality coverage and value explanation](https://www.markel.com/us/industries/horse-and-farm/horse-mortality)
- [USEA/Marshall+Sterling insured-value guidance](https://useventing-app-prod-c9atfja7fuawedcy.eastus-01.azurewebsites.net/news-media/news/how-to-determine-your-horses-insured-value)

**Risk/limitation:** all rule values and worked quote examples are fictional. This is a hackathon prototype design, not a real insurance rating or underwriting system. Verify provider access/terms and the embedding model/index dimensions before implementation.
