---
active: true
iteration: 3
max_iterations: 20
completion_promise: "<promise>T1_COMPLETE</promise>"
started_at: "2026-01-10T23:43:04Z"
---

Read docs/AGENT.md and docs/PRD.md first.
Follow the Mandatory loop protocol in docs/AGENT.md.

Do exactly one Granular Task: T1 from docs/PRD.md.
Do not start T2.

When T1 is complete:
- run the T1 verification command(s)
- append a new entry to docs/PROGRESS.md including 'Deliverables completed: T1', commands run, and observed output
- ensure the T1 artifact files exist

Stop condition: only output exactly <promise>T1_COMPLETE</promise> when all T1 Deliverables are satisfied.
