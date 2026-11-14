# Raceday Cover Agent: Low-Level Design

**Status:** review draft  
**Date:** 2026-11-13  
**Purpose:** concrete build guide for a small TypeScript trial; this is design only, not implementation.

This design follows the partner planning note’s core model: LangGraph runs the agent, a guarded runtime controls side effects, Atlas stores reference data and the business ledger, and each external write is reconciled against its target. It incorporates the meeting decision that horse and race fixtures are synthetic and read-only to the agent, with no human approval pauses.

## 1. Decisions and assumptions

### Decided for this trial

- **Language/framework:** TypeScript on Node.js, LangGraph.js, MongoDB Node.js driver, Atlas.
- **No human-in-the-loop:** the graph has no approval or interrupt node. Missing/false owner declarations, ineligible horses, unsupported products, or over-limit values stop automatically with a reason.
- **Reference data is read-only:** the agent can read horses, races, owners, and pricing rules. It cannot edit these fixture collections.
- **One task covers one horse in one race.** The task ID is also the LangGraph `thread_id`.
- **The LLM may choose among supplied tools, but ordinary code enforces eligibility, deadlines, pricing arithmetic, write ordering, retries, and verification.**
- **No real money or customer data.** The payment processor, insurer, race feed, and owner notification are mocks.

### Assumptions to confirm during review

1. The newer partner note says the platform **charges payment, issues the policy, then reports it to the insurer**. Earlier discussion said the insurer issues the policy after payment. This draft uses the partner note sequence so each system has a clear role; the owner needs to choose which system is the policy issuer before implementation.
2. The owner’s fit-to-run confirmation is supplied with the initial task/race input or stored as a preloaded standing declaration. The agent does not ask for it mid-run.
3. A policy is not issued until payment is verified. If payment is verified but the policy cannot be issued after an authoritative absence and bounded retries, the proposed compensation is a verified refund. Confirm this before implementing that branch.
4. The race feed (or a separate mock loading feed) is the authoritative source for whether and when the horse was loaded.
5. If a policy is issued but cannot be reported to the insurer, the agent continues reconciling the report operation; it does not issue another policy or refund merely because the report reply was lost.
6. The fictional market, currencies, rates, limits, and timings are demo fixtures only, not real insurance guidance.

## 2. Trial scope

### Build first

- One explicit task at a time, one horse/race pair, deterministic mock data.
- Race and horse reads, declaration and eligibility checks, quote calculation, payment, platform policy issue, insurer report, and owner notification.
- Durable LangGraph checkpoints plus a separate Atlas action ledger.
- Restart reconciliation by operation ID; a chaos control can kill the worker after an accepted write.
- A small curated recovery-case set and Atlas Vector Search lookup for technical failures, especially field renames.
- The withdrawal rule (refund before loading; no refund after loading) once the policy cancellation contract is agreed.

### Leave out of the first working slice

- Live card processing, real insurer/racing integrations, real owner information, multiple concurrent workers, multi-horse triage, a rich dashboard, and automatic creation of new recovery notes from unreviewed LLM output.
- The checkpoint-only comparison is a follow-up experiment after the ledger-backed run works. It should not delay proving the core recovery behavior.

## 3. Services and ownership of facts

| Component | Owns / does | Agent access |
|---|---|---|
| Race feed mock | Race status, runners, declarations, race start time, loaded status | Read through mock API; fixture records are not directly writable by agent |
| Owner fixture/API | Owner, contact, country, ownership share, standing instruction, supplied declaration | Read; no customer data |
| Pricing mock | Versioned pricing matrix and quote result | Read/quote only |
| Payment mock | Charge/refund records and idempotency by operation ID | Write/read through guarded API |
| Platform policy mock | Creates the policy after verified payment | Write/read through guarded API |
| Insurer mock | Receives/reconciles policy report | Write/read through guarded API |
| Notification mock | Records owner delivery | Write/read through guarded API |
| Agent runtime | LangGraph graph, deterministic guards, adapters, verifiers | Read reference data; ledger/checkpoint writes only through application code |
| Atlas | Reference fixtures, task/action/timeline ledger, recovery cases, graph checkpoints | Collections split by purpose and database role |

Mock APIs are thin HTTP endpoints over collections in the same Atlas cluster for the trial. They imitate independent systems: each has its own records, operation lookup, response-loss control, and status. The agent must call their APIs rather than directly editing payment/policy/insurer/notification records. This makes “ask the system” concrete: call that system’s `GET by operationId` endpoint and inspect the returned record.

## 4. End-to-end execution

1. A caller explicitly submits a task with a task ID, horse ID, race ID, requested cover, insured-value request, and the already supplied fit-to-run declaration reference/value. No request means the agent stays idle; an empty Atlas database does not imply a task.
2. Validate the task input. Save a task record in Atlas before graph execution. Use its ID as LangGraph `thread_id`.
3. Load prior actions. For every action intent without verified evidence, query that action’s target by the existing operation ID before taking any next step. Do not replay LLM history as proof.
4. Read the horse, owner, and race from the race/owner APIs. Keep the raw source values, source system, source update time, and read time. Normalize them to the internal types and reject invalid data.
5. Check eligibility in fixed order: already insured at 100%; race already started/passed using UTC `off_dt`; region unsupported; race not `declared`; horse already loaded; owner declaration missing/false; then product/value outside the matrix or limit. First matching blocker stops the task and records why.
6. Request a quote from the pricing mock using the versioned matrix. Save its inputs, result, tax/currency, and matrix version to the task/timeline. Do not let the LLM calculate or alter premium values.
7. Immediately before charging, re-read declaration/race/loading/deadline facts that can change. Stop if the horse is no longer eligible or there is not enough time under the demo cutoff.
8. The guarded runtime writes a payment intent with a stable operation ID, calls the payment mock, and reads the payment back by that ID. Only a matching confirmed charge is verified.
9. After verified payment, re-check loading/deadline. Save a policy-issue intent, call the platform policy API, and verify the policy by operation ID. Its horse, race snapshot, insured share/value, premium, payment reference, declaration, and effective time must match.
10. Save an insurer-report intent, report the verified policy using a stable operation ID, and read the insurer’s record back. Only a matching insurer record counts as reported.
11. Save an owner-notification intent, notify once by operation ID, and verify delivery from the notification mock.
12. Mark the task complete only when the policy, charge, insurer record, and required notification all pass their verifier checks and cover was effective before loading.

### Eligibility wording for the demo

“Declared” means the horse is on the race’s confirmed runner list. “Entered” means an earlier, provisional stage and is not enough to buy cover. Only a declared, not-yet-loaded horse is eligible. This distinction is based on a fresh race-feed response, not the agent’s memory or a semantic search result.

## 5. TypeScript application shape

Keep one small application and mock HTTP service. Suggested modules:

```text
src/
  agent/graph.ts             LangGraph nodes, state, and routing
  agent/prompts.ts           Tool instructions; no business rules hidden here
  domain/schemas.ts          Runtime schemas and TypeScript types
  domain/eligibility.ts      Fixed eligibility and deadline rules
  domain/pricing.ts          Deterministic quote arithmetic
  runtime/actions.ts         Intent → call → read-back → evidence
  runtime/recovery.ts        Resume and reconcile unfinished actions
  runtime/adapters.ts        Field aliases and source normalization
  atlas/repositories.ts      Read/write access with collection boundaries
  atlas/vector-search.ts     Recovery-case retrieval
  mocks/                     Race, pricing, payment, policy, insurer, notify APIs
  seed/                      Deterministic synthetic fixture generator
```

Use TypeScript types for compile-time help and a runtime validator (proposed: Zod) at HTTP, seed, and database-read boundaries; TypeScript types alone do not validate JSON arriving at runtime. Keep the model-facing tools narrow and typed. The LLM cannot call MongoDB directly, choose arbitrary collections, or mark a ledger action verified.

### Main input and state (shape only)

```ts
type StartCoverTask = {
  taskId: string;
  horseId: string;
  raceId: string;
  ownerId: string;
  coverType: "raceday" | "paradeRingToPost";
  requestedSumInsured: number;
  ownershipShare: number; // 0 < share <= 1
  fitToRun: { answer: true | false; declaredBy: string; declaredAt: string };
};

type GraphState = {
  taskId: string;
  horseId: string;
  raceId: string;
  nextStep: string;
  lastVerifiedActionId?: string;
  blockedReason?: string;
};
```

Graph state stays small: stable IDs, current step, and routing result. Durable task facts and external evidence live in Atlas. Reload fresh race/loading data when needed rather than trusting stale checkpoint context.

### Graph nodes

`loadTaskAndLedger → reconcilePendingActions → loadAndNormalizeFacts → checkEligibility → quote → recheckBeforeCharge → chargeAndVerify → recheckBeforeIssue → issuePolicyAndVerify → reportToInsurerAndVerify → notifyOwnerAndVerify → complete`

Error routes go to `classifyFailure → [search recovery case if technical/schema failure] → reconcile or retry same operation ID → safe stop`. Eligibility failures route to `blocked`; exhausted/ambiguous technical failures route to `unresolved`. There is no human review node. Keep graph nodes aligned to business steps so a checkpoint can resume between steps; every external write still follows the ledger protocol regardless of checkpoint position.

## 6. Tool contracts and write safety

Read tools:

- `getRace(raceId)` / `getHorse(horseId, raceId)` / `getOwner(ownerId)` return source records, `sourceUpdatedAt`, and `readAt`.
- `getQuote(quoteInput)` returns `available`, price/tax/currency, matrix version, and a reason if unavailable.
- `getPaymentByOperationId(id)`, `getPolicyByOperationId(id)`, `getInsurerReportByOperationId(id)`, and `getNotificationByOperationId(id)` return `found | absent | unavailable` plus the authoritative record when found.

Write tools accept `operationId` on every side effect:

- `charge({ operationId, taskId, amount, currency })`
- `issuePolicy({ operationId, taskId, paymentOperationId, policyData })`
- `reportPolicy({ operationId, policyNumber, policyData })`
- `notifyOwner({ operationId, policyNumber, ownerId, messageType })`
- `cancelPolicy(...)` and `refund(...)` for the withdrawal/compensation branch.

All mock write endpoints enforce a unique operation ID and return the original result if called again with that ID. All must support read-back by operation ID. Unique constraints also protect payment, policy, insurer report, and notification records. A repeated call with the same key must never create a second record.

For every external write the guarded runtime performs, in order:

1. Check deterministic prerequisites and current deadline/status.
2. Persist an `actions` intent with target, action type, operation ID, input hash/safe summary, and `pending` status. If Atlas cannot save it, do not call the target.
3. Call the target with the same operation ID.
4. Read back by operation ID, even if the call returned success.
5. Compare the target record with expected values using deterministic code.
6. Persist `verified`, `not_found`, `mismatch`, or `unknown` plus evidence and timestamps. Only `verified` unlocks the next step.

`not_found` is retryable only when the target explicitly says its lookup is authoritative and complete. Retry the same operation ID, with a small bounded attempt count (proposal: 3 reads/calls, then `unresolved`). `unknown`, target outage, or mismatched data never authorizes a second ID or optimistic continuation. If status remains ambiguous, stop safely and expose the task status; do not introduce an unapproved human loop.

## 7. Atlas setup and collections

Create one Atlas project/cluster for the demo, one database named `raceday_demo`, and separate database users for the agent and mock services if time permits. Store the connection string in an uncommitted `.env`; never put it in the repo or prompt. The agent database role can read reference collections and read/write ledger/checkpoint collections. Only seed/admin code can write reference collections. Mock services own their target-system collections.

| Collection | Contents / access |
|---|---|
| `races` | Race feed fixture, including embedded runners; seed/admin writes, agent read-only |
| `owners` | Synthetic owner, standing instruction, ownership shares and supplied declaration; seed/admin writes, agent read-only |
| `pricing_matrix` | Versioned invented product rates, minimums, tax, limits and availability; seed/admin writes, agent read-only |
| `tasks` | One horse/race run, current outcome, matrix version, source snapshots/references; app read/write |
| `actions` | Intent and evidence for each external side effect; app read/write |
| `timeline` | Short ordered events for demo display/debugging; app append/read |
| `payments` | Mock charge/refund records; payment mock only writes; API reads by operation ID |
| `policies` | Platform-issued policy records; policy mock only writes; API reads by operation ID |
| `insurer_reports` | Insurer’s received policy records; insurer mock only writes; API reads by operation ID |
| `notifications` | Mock delivery results; notification mock only writes; API reads by operation ID |
| `recovery_cases` | Curated failure/fix notes plus embeddings and filter metadata; seed/admin writes, agent search-only |
| `system_profiles` | Versioned source field aliases and expected shapes; seed/admin or validated adapter update only |
| `checkpoints`, `checkpoint_writes` | LangGraph checkpointer-managed collections; runtime only |

Add unique indexes on `tasks.taskId`, `actions.operationId`, and each mock’s `operationId`; add supporting lookups on task ID and target. Keep reference fixtures and target mock records separate: a race/owner fixture is read-only, while a policy/payment record is written only by its mock service.

### Atlas initialization checklist

1. Create Atlas cluster and database user; allow the developer machine’s IP for the trial.
2. Create database/collections and indexes above, then seed synthetic fixtures.
3. Validate each fixture before insert and read it back after seeding.
4. Configure `MongoDBSaver` using the same cluster but its dedicated checkpoint collections; call its documented `setup()` at startup.
5. Create the Vector Search index on `recovery_cases` after embeddings are seeded; wait for index status `READY` before demoing retrieval.
6. Start mock APIs and runtime with separate service roles/environment settings. Do not give the LLM a MongoDB URI.
7. Reset by restoring both Atlas fixture/mock collections and the graph checkpoint state to the same declared baseline. Never clear only the ledger while leaving payment/policy mocks intact.

## 8. Vector Search: what it does and how to set it up

Vector Search finds recovery notes with similar meaning (for example “write may have succeeded but response timed out”) even if the new error uses different wording. It is called after a technical or schema failure, not on every graph step, not to find the active task, and not to verify an external write. Active tasks are loaded by exact `taskId`; current race, payment, and policy facts come from their authoritative mock APIs.

Create a few reviewed `recovery_cases` first. Each record contains a short failure description, affected system, failure type, known schema/profile version, safe checks, bounded recovery steps, and an embedding made from the same text. On a matching failure, embed the error summary using the same embedding model and dimensions, then query Atlas Vector Search with a `system`/`failureType` pre-filter and a small result limit (for example, top 3). The result is advice only: deterministic allowlists and the target read-back still control the action.

Create a Vector Search index in Atlas UI for `recovery_cases` (name proposal: `recovery_cases_vector`) with:

- vector field `embedding`, dimension equal to the selected embedding model’s output, similarity `cosine`;
- filter fields `system`, `failureType`, and `profileVersion`.

Example index shape; replace `<EMBEDDING_DIMENSIONS>` with the chosen model’s actual dimension before creation:

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": <EMBEDDING_DIMENSIONS>, "similarity": "cosine" },
    { "type": "filter", "path": "system" },
    { "type": "filter", "path": "failureType" },
    { "type": "filter", "path": "profileVersion" }
  ]
}
```

The application query uses `$vectorSearch` with that index, the query embedding, allowed metadata filters, `numCandidates` modestly above `limit`, and `limit: 3`. Do not guess dimensions or mix embedding models: stored and query vectors must be produced by the same model. If the provider is unavailable, the index is not ready, or no cases match, continue with deterministic retry/reconciliation; Vector Search is helpful context, never a dependency for correctness.

For the demo, seed at least these reviewed cases: (1) payment accepted but reply lost; (2) policy/report accepted but reply lost; (3) `race_class` renamed to `class`, including the approved alias/profile update. If a field name changes, the adapter may use a known alias or a new field that passes schema/type validation. Do not let the LLM write arbitrary `system_profiles` paths or convert a semantic suggestion directly into a production mapping.

## 9. Mock data generation and validation

### Fixture contents

Generate all data locally from a fixed seed so every run has the same IDs, quotes, and expected results. Include:

- One race with status `declared`, one `entry`-only race, and one stale/changed race status; include `off_dt` as an absolute UTC timestamp and `off_time` as display-only text.
- A small group of horses (about 5–8) with stable unique `horse_id`s, varied ages/ratings, and deliberately repeated names to prove matching uses IDs.
- Synthetic owners with country, currency/tax mapping, ownership shares, standing instruction, fit-to-run answer, and loading time.
- Versioned pricing matrix with race types/classes or rating tiers, cover types, max insured values, premium rates, minimum premium, add-ons, tax rates, availability, and authority limit.
- One already insured 100% case; one unsupported region/race type; one over-age or otherwise unavailable product case; one shared-ownership case; and one eligible happy path.
- Two feed shapes: upcoming `{ off_time, race_class, distance_f }` and results `{ off, class, dist_f }`; some numbers as strings and one product spelling variant (`paradeRingToPost` / `paraderingtopost`).
- Recovery cases for accepted-write timeouts and schema drift.

### Normalize and validate before use

1. Keep the original source JSON for debugging; map known feed aliases into one internal canonical shape (`raceId`, `offAt`, `raceClass`, etc.).
2. Parse numeric strings explicitly; reject currency-formatted values where a numeric amount is required unless a named parser handles them. Never coerce invalid text to zero.
3. Validate IDs, enum values, required fields, `0 < ownershipShare <= 1`, positive insured values, supported currency, valid ISO timestamps, and array references.
4. Use `off_dt`/normalized UTC `offAt` for time decisions. `off_time` is display only. Reject missing timezone/invalid timestamp rather than guessing.
5. Check all runner `horse_id`s are unique within a race and joins use IDs, not names. Check owner/stable references and policy references resolve.
6. Recompute sample quotes independently from matrix fixtures and check minimum premium, tax, ownership share, add-ons, and rounding. Store currency minor units (integer cents/pence) in payment/policy records to avoid floating-point money errors.
7. After seed, read fixtures back and assert counts, IDs, expected eligible/ineligible cases, and expected quotes. Seed command should fail loudly if invalid; no partial silent seed.

Use Zod (proposed) as the runtime validator at seed, API input/output, and Atlas-read boundaries. Treat the raw feed schema and canonical internal schema as separate so changed source fields are visible rather than silently discarded.

## 10. Data records and state transitions

### Task

One document per horse/race request: `taskId`, `horseId`, `raceId`, `ownerId`, requested cover/value/share, declaration snapshot/reference, loading deadline, current status, pricing matrix version, source snapshots/references, created/updated times, and final reason. Statuses: `queued`, `running`, `blocked`, `unresolved`, `completed`, `withdrawn`.

### Action ledger

One document per intended external side effect: `actionId`, `taskId`, `step`, `targetSystem`, `operationId` (unique), intended payload hash/summary, status (`pending`, `verified`, `not_found`, `mismatch`, `unknown`), target reference, read-back evidence, attempt count, and timestamps. Do not store card credentials or sensitive personal data.

### Timeline

Append concise events such as task created, eligibility blocked, quote returned, intent saved, target accepted, read-back verified, recovery note used, and task completed. The timeline is for explanation; `tasks` and `actions` plus target records drive recovery.

### Policy snapshot

Unique `policyNumber` and `operationId`; coverage type/add-ons/ownership percentage; horse details keyed by `horseId`; race snapshot; sum insured, premium, tax, currency, matrix version; declaration answer/by/at; effective/expiry times; payment operation/reference. Record should show the facts used at issue time, not only live references that might change later.

## 11. Crash recovery and chaos controls

On restart, call the graph using the same `thread_id = taskId`; independently load the task and action ledger. For each non-verified action, query the target system:

| Read-back result | Next action |
|---|---|
| Matching record found | Save evidence and mark verified; continue from the next business step |
| Authoritative absence | Re-send the same payload with the same operation ID; then read back |
| Record found but values differ | Mark mismatch and stop safely; never create a new operation ID to hide it |
| Target unavailable or answer ambiguous | Retry read-back up to configured N, then mark unresolved; no blind write |

The graph checkpoint says where LangGraph was; the ledger says what the app intended and what the target proved. Neither alone proves the other. If Atlas is unavailable, do not perform a new external write. If no task ID was explicitly requested, stay idle. If task ID is requested but missing, report it rather than creating a replacement.

Chaos toggles for the mock services:

- `accept_then_drop_response` on charge, policy issue, insurer report, or notification;
- `delay_or_timeout` on the next read/write;
- `rename_field` for the feed/profile shape;
- `reject_write` before acceptance;
- process kill after target acceptance and before ledger verification/checkpoint.

Reset all mock target records and the app ledger/checkpoints together for a clean run. The field-rename demo should run first with a slow recovery (inspect new response, retrieve case, validate alias, retry/read back) and then again with the saved profile/case so the recovery is shorter.

## 12. Withdrawal and compensation branch

Before loading: read authoritative loading state; save cancellation intent; cancel/verify platform policy and insurer record as required; save refund intent; refund/verify payment; notify/verify owner. After loading: no refund under the agreed demo rule. The exact policy state after a post-load withdrawal and whether insurer cancellation is required remain domain questions. Payment-success/policy-issue-definitive-failure compensation uses the proposed same verified-refund protocol only after confirming policy is authoritatively absent; ambiguity never triggers refund.

## 13. Demo scenarios and success checks

1. **Happy path:** declared and eligible horse → one charge → one policy → one insurer record → one notification, all before loading.
2. **Kill after charge accepted:** restart reads payment by operation ID, records evidence, then creates exactly one policy; no second charge.
3. **Kill after policy issue accepted:** restart finds policy and continues to insurer report; no duplicate policy.
4. **Insurer report accepted, response dropped:** restart finds one insurer record and continues; no duplicate report.
5. **Field rename:** first run recovers with validated alias and Vector Search guidance; repeat uses stored validated profile/case and takes fewer recovery steps.
6. **Eligibility cases:** `entry` (not declared), already loaded, race started, unsupported region/type, age rule, already insured, false/missing declaration, over-limit. Each stops before payment/policy writes with a concrete reason.
7. **Withdrawal:** before loading refunds once; after loading refunds zero times.
8. **Unresolvable outage/mismatch:** after N checks, status is `unresolved`, no new operation ID is generated and no duplicate write occurs.

Track a small scoreboard: eligible tasks completed before loading, number of charges/policies/insurer records, duplicates, correct blocks, successful recovery after kill, and unresolved runs. Primary success means one verified charge and one verified policy/insurer record per eligible task, with no unauthorized side effects and recovery without human intervention.

## 14. Build order

1. Create TypeScript app, environment configuration, Atlas connection, schemas, and deterministic seed/validation.
2. Implement mock race/owner/pricing APIs and confirm eligibility/quote fixtures.
3. Implement payment/policy/insurer/notification mocks with operation-ID idempotency and read-back.
4. Implement task/action repositories and the guarded write protocol; verify its flow with one operation before adding an LLM.
5. Build the LangGraph nodes and MongoDB checkpointer; make task ID the thread ID.
6. Add recovery/reconciliation and kill-after-accept chaos hook.
7. Seed recovery cases and create the Atlas Vector Search index; add field-drift handling.
8. Add withdrawal, timeline view, and scoreboard only after the happy path and restart demo are reliable.

## 15. Open questions for review

1. **Policy issuer:** should our platform issue the policy then report it to the insurer, as the partner note says, or should the insurer issue it after payment, as earlier described?
2. **Payment/policy compensation:** if a charge is verified but policy issuance is definitively impossible, should the agent refund automatically? What does it do if refund is also uncertain?
3. **Fit-to-run input:** exactly which initial task record provides this declaration, and who/what system is recorded as its source?
4. **Loading authority:** what mock endpoint/source is the single source of truth for loaded status and timestamp?
5. **Eligibility threshold:** what invented age, region, race-type, and value-limit rules should the seeded demo use?
6. **Withdrawal meaning:** after loading, is the policy still active until race end, or cancelled without refund?
7. **Vector embedding provider:** which available model/service should create case and query vectors? Its vector dimensions must be known before creating the Atlas index.
8. **Retry limit and time budget:** is three read-back checks a good starting N, and how much buffer before loading is required to start a charge/issue action?
9. **Night-one scope:** should withdrawal be part of tonight’s trial, or should the first working proof focus on kill/resume, timeout, and field rename?

## References

- Partner architecture planning note and meeting notes supplied in this conversation (2026-11-13).
- [LangGraph.js persistence and checkpointing](https://github.com/langchain-ai/langgraphjs/blob/main/docs/docs/concepts/persistence.md)
- [MongoDBSaver reference for LangGraph.js](https://reference.langchain.com/javascript/langchain-langgraph-checkpoint-mongodb)
- [MongoDB Atlas Vector Search `$vectorSearch` documentation](https://www.mongodb.com/docs/atlas/atlas-search/operators-collectors/vectorsearch/)
- [MongoDB Vector Search index quick start](https://www.mongodb.com/docs/atlas/atlas-vector-search/tutorials/vector-search-quick-start/)
- [LangGraph.js MongoDBSaver security advisory](https://github.com/advisories/GHSA-98xf-r82g-9mhx)

**Implementation note:** before installing packages, check current compatible versions and advisories. As of this draft, the cited MongoDBSaver advisory lists versions through `1.3.0` as affected and `1.3.1` as patched; use a patched current release and validate string-only thread/checkpoint identifiers at the application boundary.
