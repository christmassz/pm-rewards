All paths in this repo are relative to repo root; docs live under docs/

### 0) What this project is

Build a Python service that:

* dynamically selects the best **3** Polymarket Liquidity Rewards markets
* runs a **stable / low-churn** market-making strategy in those 3 markets
* supports **paper mode by default**, live mode only behind an explicit flag
* uses **single wallet** only

### 1) Source of truth files (do not invent new process)

You are given exactly three docs:

* `AGENT.md` (this file): operating contract for the agent loop
* `PRD.md`: product requirements and acceptance tests (spec)
* `PROGRESS.md`: append-only log of work performed (journal)

Rules:

* Do not place status reports inside `AGENT.md`.
* Do not rewrite history in `PROGRESS.md`. Only **append** new entries.

### 2) Mandatory loop protocol (every iteration)

On every Ralph loop iteration, do the following in order:

1. Read `PRD.md` and identify the **next smallest deliverable** that is not complete.

   * Choose the smallest unit that can be verified by a command and/or file diff.

2. Implement that deliverable with minimal scope.

   * Prefer fewer files and fewer changes.
   * Do not start live trading until paper mode is stable and explicitly requested.

3. Run the verification command(s) for that deliverable.

   * If there is no verification command yet, create one now as part of the deliverable.

4. **Append** an entry to `PROGRESS.md` that includes:

   * ISO timestamp
   * goal
   * files changed
   * exact commands run
   * observed output (short)
   * next deliverable

5. If and only if the deliverable is complete and verified:

   * stage changes (`git add -A`)
   * commit with a message tied to the deliverable
   * (optional if repo allows) push


### 2.1) Deliverables rule (mandatory)

* The unit of work is a **Granular Task** in `PRD.md` (T1, T2, …).
* Each iteration must complete **exactly one** Granular Task unless blocked.
* A Granular Task is only complete when **all Deliverables** listed under that task in `PRD.md` are satisfied:

  * required artifacts exist
  * verification command(s) are run
  * expected minimum output is observed
  * required logs are **appended**
* In `PROGRESS.md`, every entry must include:

  * `Deliverables completed: T<id>`
  * the verification command(s) run for that task
  * the produced artifact paths

**Blocked rule**

* If a task is blocked, do not start a different task.
* Append a `PROGRESS.md` entry marked `Blocked` including:

  * blocker summary
  * commands tried
  * the smallest next action required to unblock

### 3) Guardrails (do not skip)

* Paper mode must remain supported permanently.
* No UI scraping.
* Use the Gamma markets endpoint for discovery and reward parameters.
* Use `py-clob-client` for order book reads and (later) order placement.
* All logs are append-only JSONL (one JSON object per line).
* Use the word **append** in comments/log fields/docs; do not use “update”.

### 4) Deliverable-first build order (enforced)

Build and verify in this exact order (do not jump ahead):

D1. Repo scaffolding + requirements install + config template exists

* Verify: `pip install -r requirements.txt`

D2. Gamma client can paginate markets and print N markets

* Verify: `python -m src.selector --gamma-smoke --n 5`

D3. Selector can filter reward-eligible markets and output top 3 to `data/target_markets.json`

* Verify: `python -m src.selector --select-top --write`

D4. CLOB read-only utilities can fetch order book and compute midpoint proxy for a slug in target_markets

* Verify: `python -m src.maker --paper-one --slug <slug>`

D5. Paper loop can run for 1 market for N seconds and append maker heartbeats to JSONL

* Verify: `python -m src.maker --paper-loop --slug <slug> --seconds 120`

D6. Orchestrator runs paper mode with exactly 3 markets and hysteresis (no rapid switching)

* Verify: `python -m src.main --paper --seconds 600`

D7. Live mode (explicit flag) for 1 market only, safe shutdown cancels all orders

* Verify: `python -m src.main --live --seconds 120` (only when explicitly authorized)

### 5) Completion promise (“DONE” condition)

Only output `DONE` if:

* All D1–D6 deliverables are complete and verified, AND
* `PROGRESS.md` contains an appended entry documenting each deliverable verification command and output.

6. If and only if the deliverable is complete and verified:

   6a) Stage all changes:
       git add -A

   6b) Confirm the staged diff is only related to the completed task:
       git diff --staged

   6c) Commit with a message tied to the task ID (use the exact task ID):
       git commit -m "T# <short description>"

 Commit rule:
- Never commit unless a single Granular Task (T#) deliverable is fully verified.
- One commit per task. Do not bundle multiple tasks into one commit.
- The commit message must start with the task ID (e.g., "T2 ...").
