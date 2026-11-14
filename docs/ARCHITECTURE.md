# Architecture

Every value, rate, limit, horse and owner in this project is invented.

## The pitch

Buying raceday cover is manual and laborious, so owners miss it. If the horse dies in that race, the money is gone, and there is no second chance.

Our agent takes a standing instruction, "cover my runners", and finishes the job for every horse before it leaves the stable: one charge, one policy, one insurer record, proven even if it crashes halfway.

## The pieces

- **Owner:** gives a standing instruction and one fit-to-run confirmation per race.
- **Agent:** a model in a loop. LangGraph runs it; MongoDB's checkpointer saves its state. Task ID = LangGraph thread ID.
- **Guarded tool runtime:** plain code between the model and the outside world. It checks rules, saves the intent, makes the call, reads the result back and records evidence. The model cannot bypass it.
- **Outside systems (mocks):** racing authority feed, health data provider (x-rays, vet records; read only), payment processor, insurer, notifications.
- **Our platform:** issues the policy and keeps past declarations.
- **Underwriter:** reviews referred cases only, using the pack the agent prepares.
- **Atlas:** the record of what was intended and what was proven, plus checkpoints and past cases.

## The core rule

**The model decides what to do next. Code decides whether it is allowed and whether it worked.**

## Horse risk profile and price

For every runner the agent builds a risk profile. The insurer's rules turn it into a price: a riskier horse costs more to insure.

Risk factors:

- **Health:** x-rays, vet records, past health issues, declaration history.
- **Recent form:** failed to finish, time since last run.
- **Workload:** too many races in a short period.
- **Breeding.**
- **Official rating** from the racing authority.
- **The race:** type and class.

The table gives the base (race type and class, times the owner's share); the risk factors adjust it. Code computes the price. The agent gathers the factors and explains the result in plain words, for example "priced higher: five runs in 30 days, failed to finish last time". The model never sets a price.

## Underwriting: two lanes

- **Value:** the sum insured is either under the insurer's authority limit or over it.
- **Health:** today's declaration is clean, adverse, or contradicts the horse's past declarations.

**Clean lane** (under the limit, clean answer, no contradiction): the owner confirms the horse is fit to run, then the agent pays, issues the policy and reports it to the insurer immediately, with proof that each step took effect.

**Referral lane** (over the limit, adverse answer, or contradiction): no charge. The underwriter gets a one-page pack: why it was referred, the horse, the value and how it was reached, the declaration history, and the time left before the horse leaves the stable. Underwriters answer within a day, so the agent refers the moment the declaration appears. Approved in time: the task continues in the clean lane. Declined or no answer: it stops with no charge and the owner is told.

The agent never accepts a risk outside the limit and never answers the declaration for the owner. History can only make it more cautious.

## What lives in Atlas

| Collection | One document is | Key contents |
|---|---|---|
| `tasks` | one horse in one race | horse, race, deadline (when the horse leaves the stable), status, lane, quote with reasons |
| `actions` | one attempt to change an outside system (the ledger) | task, step, system, operation ID (unique), intended change, state, evidence |
| `horse_profiles` | one horse's risk profile | health records, past declarations, runs, breeding, rating |
| `referrals` | one case sent to an underwriter | reason, pack, decision |
| `insurer_rules` | one version of the insurer's rules | sum insured table, rates, risk adjustments, tax, authority limit |
| `cases` | one solved problem | what failed, which system, what fixed it; embedded for Vector Search |
| `system_profiles` | what we believe about one system | field names and shapes, versioned |
| `checkpoints` | LangGraph's own | managed by the checkpointer |
| `mock_operations`, `mock_chaos` | the mock systems' state | kept in Atlas so a restart loses nothing |

## Every write follows the same five steps

1. **Guard:** prerequisites verified, inside the insurer's limit, horse has not left the stable.
2. **Intent:** save an `actions` record with the operation ID. If this save fails, stop.
3. **Call:** send the request with that operation ID.
4. **Read back:** ask the target system for that operation ID.
5. **Record:** save the outcome and evidence.

Outcomes: **verified**, **not found**, **mismatched**, **unable to determine**. Only "verified" lets the task move on.

## After a crash

1. Load the task and its actions from Atlas.
2. Skip everything verified.
3. For an action with an intent but no verified evidence, read back first: found → continue; not found → re-send with the same operation ID; still unknown → stop that task safely.

Nothing is re-sent blindly, and the model's history is not replayed.

## One horse, start to finish

1. Declaration appears; open a task with the deadline.
2. Build the risk profile and price it from the insurer's rules.
3. Over the limit → refer with the pack.
4. Ask the owner once, showing the price and its reasons: "is the horse fit to run?" No answer before the deadline → stop, no charge. Adverse or contradicting answer → refer.
5. Recheck the horse has not left the stable. Charge. Read back.
6. Issue the policy. Read back.
7. Report the policy to the insurer. Read back.
8. Notify the owner, once.
9. If cancelled: refund before the horse leaves, none after.

## What the model does

- **Risk profile:** gathers each horse's factors and explains the price.
- **Referral pack:** writes the one-page summary for the underwriter.
- **Unknown outcome:** chooses which evidence to gather and picks continue, re-send or refund.
- **Changed system:** a response arrives with renamed fields; it proposes the mapping, saves a new `system_profiles` version and retries only that step.
- **Triage:** the horse that leaves the stable soonest goes first.
- **Learning:** after solving a problem it writes a case; next time Vector Search finds it and the fix takes one step.

It can never mark an action verified, move money outside the rules, or answer the owner's declaration.

## Contract for every mock system

- Every write takes an operation ID and is safe to repeat with the same ID.
- Every system answers "does operation X exist, and with what result?"
- Chaos controls: lose the response, reject, rename fields. Killing the agent is done outside the mocks.
- Reset clears Atlas and all mock systems together.

## The demo

1. Two horses in the same race, two prices, each with its reasons.
2. One horse over the limit: no charge, the underwriter gets the pack.
3. A judge kills the agent after the charge: it resumes with one charge and one policy.
4. A response from the insurer is lost: the agent reads back and the insurer holds one record.
5. Cancellation before versus after the horse leaves the stable: one refund, or none.
6. A renamed field, seen twice: a slow fix the first time, one step the second.

Every declared runner ends in one accounted state: covered, referred, not confirmed, or not eligible. The closing line: "missed 0".

## Build order, and what to cut first

1. Clean lane for one horse, with the ledger and read-back. **Done**
2. Kill and resume: one charge, one policy. **Done**
3. Risk profile and price, with reasons. **Done (rules only; the model's explanation is next)**
4. Referral lane and the pack. **Done (raw facts; the model's summary is next)**
5. Full race meeting and the scoreboard. **Done**
6. Renamed field, and the second fix in one step.
7. Lost report to the insurer. **Done in the ledger; needs a demo beat**
8. Cancellation and refund.

## Where this goes next (pitch only)

- **Annual cover:** the agent builds the valuation case from breeding, earnings and recent sales, for an underwriter to approve.
- **Later:** policy documents and claims files.
