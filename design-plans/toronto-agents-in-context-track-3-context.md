# Toronto Agents in Context Hackathon: Track 3 Context

Source: the user-provided “Agents in Context: A MongoDB Hackathon Resource Guide,” dated November 14. This note captures the parts that should guide the raceday-cover-agent project. Keep the source guide as event context; the project’s design drafts remain the implementation-specific reference.

## Track 3: Finish the Job

Track 3 is about an agent completing a long, multi-step task across systems that can change or fail. MongoDB Atlas is expected to persist **verified task state** and Vector Search is expected to retrieve **relevant past experience**. The agent should survive crashes, distinguish an action that took effect from one that was only sent, resume without replaying its full history, and adapt when an external system changes its fields or workflow.

Our synthetic race-day cover workflow fits this track. The full product concept coordinates race/owner facts, payment, policy issuance/reporting, and owner notification while recovering from lost responses and worker restarts. The first Python build is intentionally narrower: it gathers and assesses one horse's data, then produces a quote; payment and policy actions are later stages.

## Apply the guide’s memory and state ideas

Keep these concepts separate in implementation and in the demo:

- **LangGraph checkpoint:** saves enough execution state to resume a thread. It says where the graph was; it does not prove that a payment or policy action happened.
- **Atlas task/action records:** the durable business state. Save an action intent before a side effect, then save target read-back evidence and the verifier outcome. On restart, reconcile unfinished actions against their target by stable operation ID before retrying.
- **Mock-system records:** the source of truth for effects owned by each system, such as a charge, issued policy, insurer report, or notification delivery. A request response, checkpoint, timeline event, or LLM explanation is not proof of the effect.
- **Vector Search recovery cases:** searchable prior experience for errors such as accepted-write timeouts or field renames. Retrieved notes may suggest safe checks; deterministic guards and target-system verification still decide what the agent can do and whether it succeeded.
- **Validated field adaptation:** a retrieved note may help find a known alias after schema drift. Validate any mapping against current schema and allowed fields before using it; do not let a semantic result authorize an arbitrary write.

For the quote-only first build, the low-level design uses `tasks`, `stage_evidence`, `quotes`, `recovery_cases`, and checkpointer-managed collections. The later payment/policy phase adds an `actions` ledger and separate mock-system records. The active task is loaded by exact task ID, not by vector search. Vector Search supports recovery; it is not the task database or evidence ledger.

## Event requirements and timing

- Finalist projects must create their project and cluster through the **MongoDB Atlas Hackathon Sandbox** link sent by email. Do not assume a personal Atlas project satisfies this requirement.
- Work must be original. Team size is at most four.
- Submit through the Cerebral Valley Platform: public GitHub repository, a one-minute demo video showing code and functionality built today, and a concise project description. Add every team member and verify video and audio playback.
- Demos are live and in person in Toronto.
- Guide schedule for November 14: doors at 9:00 AM; hacking begins at 10:45 AM; submissions close at 5:00 PM; first-round judging starts at 5:15 PM; top-six presentations start at 7:00 PM; winners are announced at 8:15 PM.

The GitHub repository is public and the initial commit is pushed. The guide’s Atlas Sandbox requirement still needs to be satisfied in the event-provided sandbox.

## MongoDB resources highlighted by the guide

Recommended starting sequence:

1. Install MongoDB Agent Skills in the coding assistant.
2. Connect the MongoDB MCP Server or an available Atlas marketplace connector.
3. Refer to the Natural Language to MongoDB Queries prompting guidance while asking the assistant to inspect/query data.

Resources most relevant to our implementation:

- **State and recovery:** “Build an AI Agent with LangGraph and MongoDB Atlas” and “State & Persistence: The Problem of Agent Reliability.”
- **Vector memory:** MongoDB Vector Search, Automated Embeddings, and the Embedding and Reranking API. Keep stored and query embeddings compatible and record the chosen model/dimensions before creating an index.
- **Agent integration:** “Building an Agent with Memory and Function Calling,” plus the JavaScript LangChain/MongoDB memory example.
- **Build reference:** “Build AI Agents with MongoDB” and the GenAI Showcase.
- **Embeddings option:** the guide says participants receive Voyage AI credits and points to its API/client setup, quickstart, usage dashboard, and rate limits. This is an option to evaluate, not a provider decision already made for the project.

The pasted guide lists these resource names but does not include their URLs. No Atlas connection, MCP configuration, API key, or embedding provider was set up as part of writing this note.

## Design questions to carry forward

1. The high-level design describes the insurer as issuing/binding the policy after payment. The low-level design instead has the platform issue the policy and then report it to the insurer. Resolve this before implementing the policy workflow.
2. Confirm that the Atlas project and cluster are created inside the emailed Hackathon Sandbox.
3. Choose the embedding provider/model and dimensions before building recovery-case vectors or the Atlas Vector Search index.
4. Keep the demo focused on Track 3: show a real restart/reconciliation path and a relevant retrieved recovery case, while making clear that retrieval does not prove an external action occurred.
