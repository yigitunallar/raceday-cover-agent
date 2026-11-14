# Submission

## Project description (for Cerebral Valley)

**Raceday Cover Agent**: an agent that finishes the job of insuring every declared racehorse before it leaves the stable, and can prove after any crash exactly what took effect.

Buying raceday cover is manual and laborious, so owners miss it, and if the horse dies in that race the money is gone. Our agent takes one standing instruction, "cover my runners", and for every runner it prices the risk, gets the owner's fit-to-run confirmation, charges, issues the policy and reports it to the insurer.

What makes it Track 3:
- **Crash-safe side effects.** Every write is saved in Atlas as an intent first, sent with an operation ID, then read back from the target system. After a kill, the agent reads back before acting, so it never charges twice. A judge can pull the plug mid-run: the scoreboard still shows one charge, one policy and one insurer record per horse, and zero duplicates.
- **Adapts when a system changes.** When a system renames its fields, the model proposes a mapping, code validates it, and the fix is saved as a case. Next time Atlas Vector Search finds the case and the fix takes one step.
- **The model investigates like an underwriter.** Before pricing, it chooses which read-only evidence to check, one step at a time: a 260-day layoff leads it to the health records, an owner's 450,000 leads it to the sales history. Code refuses to price until the insurer's required checks are done.
- **The model decides how to recover.** When a step can't be proven, it chooses the next move (read back again, re-send with the same ID, refund and escalate, escalate) from a menu that code limits.
- **The model reasons, code decides.** The model explains each price in plain words and writes the underwriter's referral summary. Code computes every number and checks that every number the model writes is real.
- **A voice for the one human moment.** The agent asks the owner "is the horse fit to run?" in a spoken call (ElevenLabs; simulated in the demo, a real phone call in production).
- **Underwriting with humans only where the rules require them.** Horses above the insurer's authority limit, or whose declaration contradicts their history, go to an underwriter with a one-page pack and no charge.

Built with MongoDB Atlas (sandbox M10: task state, action ledger, LangGraph checkpoints, Vector Search with automated embeddings over recovery cases), LangGraph, Claude, ElevenLabs, FastAPI, Render and Vercel. All data is invented.

- Live demo: https://raceday-cover-agent.vercel.app
- Code: https://github.com/yigitunallar/raceday-cover-agent

## One-minute video script

| Time | Screen | Say |
|---|---|---|
| 0:00–0:08 | Title, then the empty page | "Buying raceday cover is manual, so owners miss it. If the horse dies, the money's gone." |
| 0:08–0:18 | Load race meeting, confirm, Start agent | "Our agent takes one instruction, cover my runners, and finishes the job for every horse before it leaves the stable." |
| 0:18–0:26 | Expand Copper Lantern and Tin Whistle | "Same race, two prices. The model explains why; code sets every number." |
| 0:26–0:38 | A row turns Charged → press KILL → Start again | "Now we kill it right after it takes the money. It restarts, reads back from each system first, and carries on." |
| 0:38–0:46 | Expand that horse's ledger, then the duplicates tiles | "One charge, one policy, one insurer record. Zero duplicates." |
| 0:46–0:53 | Rename fields chaos, agent log shows case found | "When a system renames its fields, it learns the fix once. Next time Vector Search finds it in one step." |
| 0:53–1:00 | Scoreboard: Missed 0; Northern Ledger's referral pack | "Over the limit goes to an underwriter with a one-page pack. Every runner accounted for. Missed: zero." |
