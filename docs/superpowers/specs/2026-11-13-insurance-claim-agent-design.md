# Racehorse Raceday Mortality Cover Agent: High-Level Design

**Status:** Draft for review  
**Scope:** Product/workflow design. **Current implementation default:** TypeScript with LangGraph.js and MongoDB Atlas; this is changeable if the team prefers Python. Schema, exact record formats, package versions, and implementation details remain open.

## Purpose

Demonstrate a tool-using agent that arranges a synthetic short-term mortality policy for a racehorse before a race-day transport deadline. The task crosses a racing authority, an insurer, a payment system, and an owner-notification system. The agent must survive interruption, determine what each system actually did, and finish without creating duplicate policies or charges.

The scenario is a proposed fit for Track 3, “Finish the Job.” All horses, owners, policies, premiums, races, and payments are synthetic. Nothing is connected to a real insurer, racing authority, transport service, or payment processor.

## Horse-insurance context and assumptions

For this demo, “raceday mortality cover” means a fictional cover product with a defined insured horse, insured value, event/travel scope, effective time, and cutoff before loading. Exact coverage terms are business rules that the team must define for this fictional product.

Do not present the demo’s loading or refund rules as universal insurance rules. Real equine policy terms vary. For example, a Markel mortality application says coverage is not bound until the insurer approves the completed application and receives the premium, and that horses currently in transit are not eligible under that product. That illustrates why the demo must define exactly what “bound” means and which event controls loading eligibility; it does not establish the rules for every race-day policy. [Markel mortality application](https://content.markel.com/api/public/content/mortality-application-MAML?v=5af053ac)

The proposed business rule from the domain contact is: withdrawal/cancellation before loading permits a refund; after loading it does not. Before implementation, clarify what “withdrawal” means operationally and confirm that this is the intended rule for the demo product. The loading time must come from a trusted mock event source, not from the LLM’s interpretation.

## Goals

- Complete one racehorse cover task before its declared loading deadline.
- Preserve task and action progress in Atlas so a stopped agent can resume.
- Verify binding, payment, cancellation/refund, and notification against the system that owns each result.
- Handle an accepted policy-issue request whose response is lost without creating a second policy or charge.
- Use Vector Search to find relevant prior recovery notes, without treating a note as proof or authority.
- Compare action-ledger recovery with a checkpoint-only baseline under the same controlled failures.
- Make the live demo understandable and repeatable.

## Actors and system boundaries

- **Owner or authorized representative:** supplies the cover request and any required declaration when starting the task; may submit a withdrawal request.
- **Demo operator:** starts/resets a run, triggers a failure, or restarts the agent. The operator does not approve actions during a run.
- **LLM agent:** chooses among a limited set of available tools based on the task state and results.
- **Agent runtime:** applies deterministic guards, executes tools, verifies results, and records evidence. The LLM cannot bypass it.
- **Racing authority mock:** provides horse identity/registration and the race declaration/rating data required by the agreed demo rules.
- **Loading/transport status source:** provides the authoritative loaded/not-loaded event and timestamp. The simplest demo may place this endpoint in the racing-authority mock; if that is not credible, treat it as a separate mock source.
- **Insurer mock:** prices the application, issues/reports policy status after verified payment, and handles cancellation according to the demo rules.
- **Payment mock:** charges or refunds the premium and supports lookup by a stable operation reference.
- **Owner-notification mock:** records that the owner received the confirmed policy details or a cancellation/refund update.
- **Atlas:** stores graph checkpoints and the separate business action ledger/timeline. The insurer, payment, racing-authority, and notification mocks remain the sources of truth for their own effects.
- **Vector Search:** retrieves reusable recovery notes; it cannot authorize a bind, decide loading status, or verify a policy.

## Proposed task flow

1. An owner/representative or demo operator explicitly starts a task. If no task is requested, the agent remains idle.
2. The system creates and saves the initial task in Atlas before any external write. The task is tied to one horse, owner, event, and loading deadline.
3. The agent loads the current task state and uses read tools to obtain horse/race facts and loading status from the racing authority source.
4. Deterministic rules check required facts and prerequisites. The LLM may help select the next allowed tool, but it does not decide whether a coverage rule passed.
5. Required owner declarations, such as fit-to-run, arrive as task inputs. The agent does not interrupt the run to request or approve them; if a required declaration is missing or false, it stops without binding.
6. The agent calls the insurer’s pricing tool with the verified horse/race facts and the supplied value/ownership information. The pricing matrix—not the LLM—returns the premium and any quote validity details. The result is saved in Atlas with enough source/version context to explain how it was calculated.
7. A deterministic eligibility/governance gate checks the proposed insured value or premium against configured limits. A request over the supported limit is declined or marked out of scope; it does not wait for human approval. Hard-prohibited requests are blocked.
8. Before charging, the runtime rechecks the authoritative loading status and deadline. It saves a payment intent and stable operation reference in Atlas, then calls the Stripe mock.
9. The payment verifier reads Stripe’s record. Only a confirmed charge proceeds; a timeout is reconciled by operation reference before any retry.
10. After payment is verified, the runtime rechecks loading status and deadline, saves a policy-issue intent and operation reference, then asks the insurer to issue/bind the policy.
11. The verifier reads the insurer’s policy record. It must match the horse, owner, insured value, event/scope, policy status, and effective time required by the demo rules. A response saying “request received” is not proof of issue.
12. The owner is notified and the policy document is sent only after policy issuance is verified. Each notification/document action gets its own saved intent and delivery check.
13. The task is complete only when payment, policy issuance, and required delivery are verified, with cover effective before the loading cutoff.

The current workflow assumption is **price → pay → issue policy**. If payment succeeds but policy issuance definitively cannot be completed, the agent must reconcile the insurer first and then follow a defined compensation rule, likely a verified refund. That compensation rule still needs confirmation.

### Additional task branches

- **Withdrawal:** read the trusted loading event before deciding. Before loading, save a cancellation intent, cancel and verify the policy, then save a refund intent, refund and verify the payment. After loading, do not refund under the proposed demo rule. Whether the policy remains active until its scheduled end or is otherwise changed must be defined in the fictional product contract.
- **Policy issue fails after payment:** first look up the policy by operation ID. If it exists, verify and continue. If it is authoritatively absent, retry with the same ID. If issue remains definitively impossible after the retry limit, apply the agreed compensation rule (proposed: refund the verified charge, with intent and read-back); if status is uncertain, keep reconciling and do not refund speculatively.
- **Field rename:** treat the mock system’s schema error as a recoverable failure. Look up its current schema and retrieve a curated mapping note; validate the mapping, retry with the original operation ID, and verify the resulting record. Vector Search suggests a mapping but cannot invent a permitted field or establish success.

## Prompt, tools, and information sources

The prompt tells the LLM how to behave; it is not the workflow engine, database, or security boundary. It should tell the agent to use only its supplied tools, not invent horse/health/race/payment/policy facts, treat source records as authoritative, and never claim an action succeeded without a verifier result. Deterministic code enforces the actual eligibility rules, value limit, deadline, and permitted transitions even if the LLM prompt is ignored.

The tools are separate operations, not one vague “buy insurance” action. Conceptually they include: read horse/race/declaration status; read owner and ownership inputs; calculate a quote; charge and look up payment; issue and look up a policy; send and look up a policy document/notification; cancel or refund for the withdrawal branch; and search recovery notes. Each tool calls the responsible mock system and returns a structured result to the agent.

The agent must distinguish where facts came from:

- **Task inputs:** owner/stable, requested horse and race, declared value/ownership, and the supplied fit-to-run declaration.
- **Racing source:** horse identity, entered/declared status, race, and loading status/time.
- **Pricing result:** premium returned by the insurer’s pricing matrix, not an LLM estimate.
- **Payment source:** Stripe’s charge/refund status and reference.
- **Policy source:** insurer’s issued policy and effective status.
- **Agent explanation:** useful for describing a decision, but never proof that an external fact or action is true.

For each fact used in a decision, keep its source and check time conceptually attached. On restart, load stable task inputs and prior verified results from Atlas, but refresh volatile facts such as declared status and loading time before a time-sensitive action. Do not use Vector Search to find the active task.

## LangGraph’s role

LangGraph is proposed as the agent/workflow framework. It models the run as nodes connected by explicit transitions, shares state between nodes, supports tool use and pause/resume, and can persist graph checkpoints. LangGraph’s JavaScript documentation describes nodes as functions over shared state and checkpointing at node boundaries. MongoDB also provides a JavaScript LangGraph checkpointer package. [LangGraph workflow guide](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph), [MongoDB checkpointer for LangGraph.js](https://reference.langchain.com/javascript/langchain-langgraph-checkpoint-mongodb)

At a high level, the graph could contain:

```text
load-or-create-task
        ↓
load-current-context → agent/tool choice → deterministic guard
        ↑                                  ↓
        └──── recover/reconcile ← execute one tool
                                           ↓
                                  read-back verifier
                                           ↓
                                  save result / route
                                  ↙       ↓        ↘
                               next   recovery stop   complete
```

The graph checkpoint and the business ledger serve different purposes:

- **LangGraph + MongoDB checkpointer:** remembers where graph execution paused and the framework state needed to resume the agent thread.
- **Business action ledger in Atlas:** records the intent, stable operation reference, target-system response/reference, read-back evidence, and verified/uncertain outcome for each external action.
- **Timeline:** explains the sequence of important decisions and external events; it is not the sole control state.

They can use the same Atlas cluster while remaining conceptually separate. A checkpoint proves what the graph had saved; it does not prove that an external insurer or payment system performed an action. Therefore, on restart, an unfinished external action is reconciled against that target system before retry. The target’s read-back and the deterministic verifier decide whether it is complete.

The LLM receives the current task and relevant prior results from Atlas at each decision. Its tools are limited to task reads, mock-system reads/writes, verification, and recovery-note search. For a write, the runtime checks prerequisites, saves intent, calls the target, reads it back, saves the outcome, then returns a structured result to the LLM. The model cannot write arbitrary Atlas data or declare its own action verified.

### Automated governance; no human approval loop

There is no human approval or review pause during task execution. Deterministic rules govern the allowed value, eligibility, fit-to-run declaration, loading cutoff, and permitted actions. If an input is missing, the horse is ineligible/already loaded, or the amount exceeds the configured limit, the agent records the reason and stops without making the bind or payment write. A required owner declaration is supplied with the initial request; it is not requested mid-run.

Technical failures are handled autonomously using the task/action records, target-system read-backs, safe retries, and relevant Vector Search notes. If the outcome is still unknowable after a fixed number of checks, the agent records an unresolved technical stop and makes no further side effects. That status is visible to the operator, but the system does not ask the operator to decide how to proceed.

## Recovery rules

- **Atlas empty, no start request:** stay idle; do not invent a task.
- **Explicit new task:** persist its initial state before any external action, then begin at the first allowed graph step.
- **Requested task ID missing:** stop and report it; do not silently create a replacement.
- **Atlas unavailable:** treat as an availability failure, not an empty database. Do not perform external writes.
- **Existing active task:** reload graph state and business state. Every action intent without verified evidence is read back from its target before any retry.
- **Existing completed task:** do not run it again.
- **Payment accepted, reply lost:** query by payment operation reference before any retry. If found, save evidence and continue to policy issuance; if authoritatively absent, retry with the same operation ID. If payment fails, do not issue a policy. If payment status remains uncertain after N checks, stop without charging again.
- **Policy issuance accepted, reply lost:** query the insurer by policy-issue operation reference. If a matching policy exists, save evidence and continue. If authoritatively absent, retry with the same operation ID. If the outcome remains uncertain after N checks, stop unresolved.
- **Any other external write:** use the same intent → target read-back → verify → record sequence. This includes voids, refunds, and owner notifications.
- **Field rename:** after a schema-related rejection, query the system’s current field/schema information and search curated recovery notes with Vector Search. Validate the suggested mapping against the allowed fields, retry with the same operation ID, then read back and verify. If the outcome remains uncertain after N checks, stop unresolved.
- **Withdrawal/cancellation:** use the authoritative loading event/time and the demo rule to select the cancellation/refund path. Do not let the LLM infer whether loading occurred.
- **Reset:** restore Atlas and all mocks together to a declared baseline. Clearing only Atlas could hide a policy or payment that already exists.

No system can promise exactly-once effects across unrelated services under every failure. This design aims for safe, verified recovery using idempotency, read-back, bounded retries, and a safe unresolved stop without human approval.

## Vector Search role

Vector Search is for reusable recovery experience, not for normal task progress. The agent searches it after a relevant error, such as an accepted-bind timeout, payment timeout, or known field rename. Initial notes should be curated and reviewed. A retrieved note can suggest a safe check or recovery path, but the normal guards still apply and target-system verification is still required.

Because semantic search needs vector representations for notes and queries, the project must choose an embedding approach during detailed design. The LLM’s working task state and action ledger should not be replaced by vector memory.

## Checkpoint-only comparison

Proposed experiment: compare two versions using the same graph, model, tool contracts, mocks, idempotency support, deadlines, and injected failures:

- **Baseline:** LangGraph checkpointing is the only durable workflow state; it resumes the graph but has no separate per-action ledger/reconciliation path.
- **Ledger version:** LangGraph checkpointing plus the business action ledger and explicit target-system read-back reconciliation.

Run the same seeded scenarios repeatedly. Compare completed-before-loading rate, duplicate policy count, duplicate charge count, correct recovery without human intervention, and unresolved/incorrect outcomes. Report actual observations rather than promising that one version must win. A checkpoint can resume a graph node, but external side effects still need a safe recovery strategy; the LangGraph docs describe checkpoints at node boundaries and recommend separating workflow steps for resilience and observability. [LangGraph workflow guide](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph)

## Major work to build

1. **Confirm domain contract:** define the cover period, required horse/race facts, fit-to-run input, policy-issue condition, premium timing, authoritative loading event, withdrawal meaning, and refund rule if payment succeeds but policy issuance ultimately fails.
2. **Define tools and rules:** specify allowed tools, deterministic prerequisites, value cap, hard blocks, read-back checks, retry limit N, and terminal status for unresolved technical failures.
3. **Define persistent concepts:** task context, graph checkpoint, action intent/outcome/evidence, event timeline, and reusable recovery note. Exact schema and record formats come later.
4. **Build mock systems:** racing authority, insurer, payments, owner notification, and a trusted loading-status event (inside the authority mock or separate).
5. **Build the LangGraph agent:** load or initialize a task, choose allowed tools, call the guarded runtime, route through verification/reconciliation, and stop at completion or an unresolved technical state.
6. **Build action-ledger recovery:** after loading a task, reconcile every intent without verified evidence against its target before retrying. Found means verify and continue; authoritatively absent means retry with the same operation ID; still ambiguous after N checks means stop safely.
7. **Build compensation and side-effect handling:** if payment succeeds but policy issuance definitively cannot be completed, apply the agreed compensation rule (proposed: refund). Save intents and verify outcomes for refunds, cancellations, and owner notifications too.
8. **Add Vector Search recovery notes:** prepare curated notes for accepted-write timeouts and the known field rename. Search them on relevant errors, validate suggestions against current system facts/rules, and record their use.
9. **Add chaos controls:** kill the graph worker two minutes before loading after the policy-issue request has been accepted; inject a payment timeout and field rename; exercise withdrawal/refund before and after loading; show an over-limit request stopping without a policy issue.
10. **Run the comparison and rehearse:** use identical seeds and failures for baseline and ledger versions; show one live recovery and a small, honest result summary.

## Success checks

- With no task/start request, the agent stays idle; a new task is recorded before its first external action.
- After the policy-issue request is accepted and the worker is killed, restart finds and verifies the one policy, records one charge, notifies the owner, and completes before the loading deadline.
- The agent does not duplicate a policy or charge after a lost response.
- A withdrawal before loading follows the configured refund rule; one after loading follows the no-refund rule, based on the trusted loading event.
- A request over the configured limit stops before any payment/policy-issue write, without requesting human approval.
- Every side effect, including void/refund and owner notification, has an intent saved before execution and a target-side result check afterward.
- A confirmed payment failure prevents policy issuance; an uncertain payment is reconciled before retrying or continuing.
- A missing operation is retried with the same operation ID; after N checks cannot establish the outcome, the agent stops safely without human review.
- A fit-to-run declaration is task input; if missing or false, the agent stops without binding.
- The policy record, payment record, notification, business ledger, and graph checkpoint agree on the final outcome.
- Vector Search retrieves a relevant note during a failure, but cannot bypass a rule or count as verification.
- The checkpoint-only and ledger versions are run under the same seeded conditions and their observed outcomes are reported.

## Constraints and non-goals

- One synthetic horse, one event, one cover workflow, and a few fixed failure cases for the first version.
- No live insurer, racing authority, transporter, owner data, or real payments.
- No claim that the demo’s policy terms apply to every insurer or jurisdiction.
- No arbitrary network/database tools for the LLM; no LLM-authored verification.
- No human approval loop; required owner declarations are supplied as initial task inputs.
- The demo interface supports the live recovery and is not the main product.
- Follow event rules: new work, public repository, Atlas Sandbox for finalist judging, and accessible demo video.

## Open for review

- What is the authoritative source for the actual loading time: racing authority, transporter, venue, or a demo-only event feed?
- What exactly does “withdrawal” mean, and is the pre-load refund/post-load no-refund rule the intended demo contract?
- What insurer response and read-back fields prove policy issuance and the effective time?
- What amount/value cap causes an automatic out-of-scope stop?
- Which policy violations are hard blocks, and should unsupported requests be declined or marked out of scope?
- What should the unresolved technical status be called, and what is N for recovery checks?
- What are the minimum horse, owner, race, value, and transport facts required for this fictional product?
- Is the checkpoint-only comparison required in the final demo, or should it be a development experiment with only its results shown?
- Does the owner provide fit-to-run as part of the initial task request, and what source is recorded?

This is a high-level proposal for review. The detailed design should settle these domain questions before defining the schema, formats, exact graph state, tool contracts, and field-level verification rules.
