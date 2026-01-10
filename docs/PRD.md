### PRD.md — Polymarket Liquidity Rewards Auto-MM (Python, Single Wallet, Dynamic Top-3)

## 1) Objective
Build a Python service that earns Polymarket **Liquidity Rewards** by:
- dynamically selecting the **best 3** reward-eligible markets (stability-first)
- running a conservative, low-churn, two-sided market making strategy in those 3 markets
- using a **single wallet**
- defaulting to **paper mode** (no live orders unless explicitly enabled)

## 2) Non-goals
- multi-wallet strategies or “points farming” across identities
- any geo/KYC bypass logic
- UI scraping
- HFT/latency trading
- cross-venue hedging

## 3) Constraints
- Total capital: **1000 USDC**
- Active markets: **exactly 3**
- Prefer: **lower rewards, more stable**
- Selection must be dynamic (options change daily), but execution must avoid churn via hysteresis
- Use the term **append** (not “update”) in docs/log fields/comments
- Logs must be append-only JSONL files

## 4) External interfaces

### 4.1 Gamma API (market discovery and reward params)
Endpoint:
- `GET https://gamma-api.polymarket.com/markets`

Pagination:
- parameters: `limit`, `offset`, `closed`
- iterate `offset += len(batch)` until empty batch

Required fields (must parse even if encoded as JSON strings):
- identifiers: `id`, `slug`, `conditionId`
- status: `active`, `closed`, `acceptingOrders`, `enableOrderBook`, `restricted`
- rewards: `rewardsMinSize`, `rewardsMaxSpread`
- tokens: `outcomes`, `clobTokenIds`
- stability features: `competitive`, `oneHourPriceChange`, `spread`, `bestBid`, `bestAsk`
- liquidity/activity: `volume24hrClob`, `liquidityClob`
- time: `endDate`
- tick/mins: `orderPriceMinTickSize`, `orderMinSize`
- optional: `feesEnabled`, `holdingRewardsEnabled`

Normalization:
- `rewardsMaxSpread`: if value > 1, interpret as **cents**; normalize to price units via `x/100`

### 4.2 CLOB API (books, midpoints, orders)
Host:
- `https://clob.polymarket.com`

Library:
- `py-clob-client`

Read-only capabilities required:
- fetch order book for a token id
- fetch midpoint for a token id (optional; midpoint proxy will be computed from the book)

Live capabilities required (Phase Live):
- derive API creds from wallet
- place GTC orders
- cancel orders
- fetch open orders and fills (if supported by library; otherwise via REST endpoints the library wraps)

## 5) Data inputs and derived market spec

### 5.1 Parsed market record (internal)
For each Gamma market, build an internal `MarketRecord` with:
- `slug: str`
- `condition_id: str`
- `active, closed, accepting_orders, enable_order_book, restricted: bool`
- `rewards_min_size: int`
- `rewards_max_spread: float` (normalized to price units, e.g. 0.03)
- `outcomes: list[str]` (typically ["Yes","No"])
- `clob_token_ids: list[str]` (same order as outcomes)
- `competitive: float`
- `one_hour_price_change: float`
- `volume24h_clob: float`
- `liquidity_clob: float`
- `end_date: str` (ISO string)
- `order_price_min_tick_size: float | None`
- `order_min_size: float | None`

### 5.2 Outcome token mapping
Build a dict:
- `token_map = {"Yes": <token_id>, "No": <token_id>}`

If outcomes are not exactly “Yes/No”, the mapping must preserve the exact outcome strings.

## 6) Eligibility filter (selector)
A market is reward-eligible iff:
- `active == true`
- `closed == false`
- `acceptingOrders == true`
- `enableOrderBook == true`
- `rewardsMinSize > 0`
- `rewardsMaxSpread > 0`
- default excludes:
  - `restricted == true`
  - `endDate` within `end_date_buffer_days` (default 7 days)
  - `volume24hrClob < min_volume24h` (default 500)

## 7) Capital feasibility heuristic
Per market:
- `q = size_buffer * rewardsMinSize` (default `size_buffer=1.1`)
- `cap_est = 3.0 * q`

Global:
- `usable_cap = total_cap_usdc * usable_cap_frac` (default 0.85)
- `per_market_cap = usable_cap / 3`

Hard constraint:
- discard market if `cap_est > per_market_cap`

## 8) Scoring function (stability-first)
Inputs:
- `max_spread = normalized rewardsMaxSpread`
- `one_hour = abs(oneHourPriceChange or 0)`
- `competitive = competitive or 0`
- `vol24 = volume24hrClob or 0`
- `liq = liquidityClob or 0`
- `cap_est` and `per_market_cap`

Discard if `cap_est > per_market_cap`.

Score:
```text
score =
  + 2.0*log1p(max_spread*100)
  + log1p(vol24)
  + 0.5*log1p(liq)
  - 4.0*one_hour
  - 1.5*competitive
  - 0.8*(cap_est/per_market_cap)


Selector output:

* choose the top 3 markets by score
* write `data/target_markets.json`
* append one JSON line to `logs/selector.jsonl` each selector run

## 9) Quoting policy (paper + live)

Per selected market:

* maintain 4 quotes:

  * YES BUY, YES SELL
  * NO BUY, NO SELL
* order size:

  * `size = rewardsMinSize * size_buffer` (default 1.1)
* midpoint proxy per token:

  * compute from CLOB order book using cutoff = `rewardsMinSize`
  * bid_cutoff_px = price level where cumulative bid size >= cutoff
  * ask_cutoff_px = price level where cumulative ask size >= cutoff
  * `mid = (bid_cutoff_px + ask_cutoff_px)/2`
* quote placement:

  * `half_spread = rewardsMaxSpread * half_spread_frac` (default 0.85)
  * `bid = mid - half_spread`
  * `ask = mid + half_spread`
* rounding:

  * tick size = `orderPriceMinTickSize` if present else:

    * 0.001 if mid < 0.1 else 0.01
  * bid rounds down to tick
  * ask rounds up to tick

Eligibility check:

* in-band if `abs(price - mid) <= rewardsMaxSpread` and `size >= rewardsMinSize`

Replace rules (churn control):

* cancel/replace only if:

  * order is out-of-band, OR
  * target price differs by >= `update_min_ticks` (default 2 ticks), OR
  * remaining size < rewardsMinSize

## 10) Rotation policy (hysteresis)

Parameters:

* selector interval: `selector_interval_sec` (default 900)
* rotation cooldown: `rotation_cooldown_sec` (default 43200)
* minimum tenure: `min_tenure_sec` (default 21600)
* replacement multiplier: `score_replace_multiplier` (default 1.25)

Rules:

* rotation only if cooldown elapsed
* candidate can replace incumbent only if:

  * candidate_score >= incumbent_score * score_replace_multiplier
  * incumbent tenure >= min_tenure_sec
* avoid rotation if live-mode safe shutdown/cancel-all cannot be guaranteed

## 11) Persistence (SQLite) and logs (append-only)

SQLite (`data/pm_mm.db`) tables (minimum):

* `runtime_state`: key, value (for `last_rotation_ts`, etc.)
* `active_markets`: slug, condition_id, entered_at, score_at_entry
* `open_orders`: order_id, condition_id, token_id, side, price, size, status, created_at, updated_at

Logs (append-only JSONL):

* `logs/selector.jsonl`: one JSON object appended per selector run
* `logs/maker.jsonl`: one JSON object appended per market loop heartbeat

## 12) CLI surface (must exist for verifiable deliverables)

Selector:

* `python -m src.selector --gamma-smoke --n 5`
* `python -m src.selector --list-eligible --limit 200`
* `python -m src.selector --select-top --write`
* `python -m src.selector --print-config` (after T9)

Maker (paper):

* `python -m src.maker --paper-one --slug <slug>`
* `python -m src.maker --paper-loop --slug <slug> --seconds 120`

Main orchestrator:

* `python -m src.main --paper --seconds 600`
* `python -m src.main --live --seconds 120` (places orders; only when explicitly enabled)

## 13) Granular Tasks (each task ends with Deliverables)

**Rule:** After completing any task below, the task is not complete until its Deliverables are satisfied. Deliverables are the unit of progress.

### T1 — Repo scaffold and dependencies

**Requirements**

* Create repo structure as defined in AGENT.md Step 0.
* Create `requirements.txt` with required packages.
* Create `config.yaml.example`.

**Deliverables**

* Artifacts:

  * `requirements.txt`
  * `config.yaml.example`
  * `src/__init__.py`
  * `data/.gitkeep`
  * `logs/.gitkeep`
* Verification commands:

  * `pip install -r requirements.txt`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T1`

---

### T2 — Gamma pagination client

**Requirements**

* Implement `src/gamma.py`:

  * `iter_markets(limit, closed)` with `offset` pagination
  * retries/backoff
  * `parse_json_maybe`
  * `normalize_rewards_max_spread`
  * `extract_market_fields`

**Deliverables**

* Artifacts:

  * `src/gamma.py`
* Verification commands:

  * `python -m src.selector --gamma-smoke --n 5`
* Expected minimum output:

  * prints 5 market summaries (one line each)
* Logs requirement:

  * append one JSON line to `logs/selector.jsonl` with `kind="gamma_smoke"`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T2`

---

### T3 — Reward eligibility filter + outcome token mapping

**Requirements**

* Implement `reward_eligible(m, cfg)`
* Implement `parse_outcome_token_map(m)` using `outcomes` + `clobTokenIds`

**Deliverables**

* Artifacts:

  * `src/selector.py` includes the filter + mapping functions
* Verification commands:

  * `python -m src.selector --list-eligible --limit 200`
* Expected minimum output:

  * prints `total_fetched` and `total_eligible`
  * prints first 10 eligible markets with token mapping
* Logs requirement:

  * append one JSON line to `logs/selector.jsonl` with `kind="list_eligible"`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T3`

---

### T4 — Selector scoring and top-3 write

**Requirements**

* Implement cap feasibility check.
* Implement scoring function (stability-first).
* Write `data/target_markets.json`.
* Append selector run result to `logs/selector.jsonl`.

**Deliverables**

* Artifacts:

  * `data/target_markets.json`
* Verification commands:

  * `python -m src.selector --select-top --write`
* Expected minimum output:

  * prints exactly 3 chosen slugs with scores
  * `data/target_markets.json` contains exactly 3 markets
* Logs requirement:

  * append one JSON line to `logs/selector.jsonl` with `kind="select_top_n"`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T4`

---

### T5 — CLOB read-only utilities + midpoint proxy

**Requirements**

* Implement `src/clob_utils.py`:

  * read-only client creation
  * order book fetch
  * size-cutoff midpoint proxy
  * tick rounding
* Implement maker paper one-shot that reads from `data/target_markets.json`.

**Deliverables**

* Artifacts:

  * `src/clob_utils.py`
  * `src/maker.py` supports `--paper-one`
* Verification commands:

  * `python -m src.maker --paper-one --slug <slug-from-target_markets>`
* Expected minimum output:

  * prints YES/NO mids and best bid/ask
* Logs requirement:

  * append one JSON line to `logs/maker.jsonl` with `kind="paper_one"`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T5`

---

### T6 — Maker paper loop (low churn decisions)

**Requirements**

* Maker supports `--paper-loop` for one market.
* Emits heartbeat lines each loop.
* Computes `replace_needed` under churn rules.
* Appends JSON heartbeat each loop.

**Deliverables**

* Artifacts:

  * `src/maker.py` supports `--paper-loop`
* Verification commands:

  * `python -m src.maker --paper-loop --slug <slug> --seconds 120`
* Expected minimum output:

  * multiple heartbeat lines printed
  * `logs/maker.jsonl` grows via appended lines
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T6`

---

### T7 — Orchestrator paper mode: exactly 3 workers + hysteresis

**Requirements**

* `src/main.py` runs selector on interval (in-process).
* Runs exactly 3 paper workers for the selected markets.
* Implements hysteresis (cooldown, tenure, replace multiplier).
* Persists runtime state to SQLite.

**Deliverables**

* Artifacts:

  * `src/main.py`
  * `data/pm_mm.db`
* Verification commands:

  * `python -m src.main --paper --seconds 600`
* Expected minimum output:

  * console heartbeats for exactly 3 markets
  * explicit “rotation blocked” reasons printed when applicable
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T7`

---

### T8 — Live mode (guarded)

**Requirements**

* Live mode behind `--live`.
* Places and maintains 4 GTC orders per market.
* Safe shutdown cancels all open orders.

**Deliverables**

* Verification commands:

  * `PM_PRIVATE_KEY=... python -m src.main --live --seconds 120`
* Expected minimum output:

  * confirms orders placed
  * on Ctrl+C cancels open orders before exit
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T8`

---

### T9 — Config file and loader (single source of truth)

**Requirements**

* `config.yaml.example` includes all required keys and defaults.
* Implement `src/config.py` to load YAML, validate required keys, and provide a typed config object.
* Code must not hardcode constants that exist in config.

**Deliverables**

* Artifacts:

  * `src/config.py`
* Verification commands:

  * `python -m src.selector --print-config`
* Expected minimum output:

  * prints validated config values (redacting secrets)
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T9`

---

### T10 — Append-only log helper (JSONL contract)

**Requirements**

* Implement `src/logging_utils.py`:

  * `append_jsonl(path, obj)` appends exactly one JSON object per line
* All selector and maker logs must call `append_jsonl` (no ad-hoc writes).

**Deliverables**

* Artifacts:

  * `src/logging_utils.py`
* Verification commands:

  * `python -m src.selector --gamma-smoke --n 1`
  * `python -m src.maker --paper-one --slug <slug>`
* Expected minimum output:

  * both commands append one line each to their respective JSONL files
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T10`

---

### T11 — SQLite schema + db helper (restart-safe)

**Requirements**

* Implement `src/db.py` to:

  * initialize `data/pm_mm.db`
  * create tables if missing: `runtime_state`, `active_markets`, `open_orders`
  * expose helpers:

    * `get_state(key)`, `set_state(key, value)`
    * `upsert_active_market(condition_id, slug, entered_at, score_at_entry)`
* Orchestrator must persist `last_rotation_ts` and active markets.

**Deliverables**

* Artifacts:

  * `src/db.py`
  * `data/pm_mm.db` created by running a command
* Verification commands:

  * `python -m src.main --paper --seconds 5`
* Expected minimum output:

  * DB file exists and contains the required tables
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T11`

---

### T12 — Clean shutdown behavior (paper + live)

**Requirements**

* Implement SIGINT/SIGTERM handling:

  * paper mode: stop loops cleanly
  * live mode: attempt cancel-all before exit
* On shutdown, append a log line:

  * maker: `kind="shutdown"`
  * orchestrator (optional): `kind="shutdown"`

**Deliverables**

* Verification commands:

  * run `python -m src.maker --paper-loop --slug <slug> --seconds 9999` then Ctrl+C
* Expected minimum output:

  * process exits cleanly
  * one shutdown line appended to `logs/maker.jsonl`
* PROGRESS.md requirement:

  * append a PROGRESS.md entry referencing `T12`

---

## 14) Configuration schema (`config.yaml`)

**Rule:** all runtime behavior must be driven from `config.yaml` (copied from `config.yaml.example`). Code must load config once at startup and pass a config object around.

### 14.1 Top-level keys

* `total_cap_usdc: float` (default 1000)

* `usable_cap_frac: float` (default 0.85)

* `num_markets: int` (default 3)

* `exclude_restricted: bool` (default true)

* `end_date_buffer_days: int` (default 7)

* `min_volume24h: float` (default 500)

* `selector_interval_sec: int` (default 900)

* `poll_interval_sec: int` (default 5)

* `rotation_cooldown_sec: int` (default 43200)

* `min_tenure_sec: int` (default 21600)

* `score_replace_multiplier: float` (default 1.25)

### 14.2 Quote parameters

Under `quote:`:

* `size_buffer: float` (default 1.1)
* `half_spread_frac: float` (default 0.85)
* `update_min_ticks: int` (default 2)

### 14.3 Network/retry parameters

Under `net:`:

* `request_timeout_sec: int` (default 20)
* `max_retries: int` (default 5)
* `backoff_base_sec: float` (default 0.5)
* `backoff_max_sec: float` (default 10)

### 14.4 Live-mode safety

Under `live:`:

* `enabled_by_flag_only: bool` (must be true)
* `max_markets_live: int` (default 1 for first live tests; later can be 3)
* `cancel_on_exit: bool` (default true)

## 15) Output file schemas (explicit)

### 15.1 `data/target_markets.json`

Must be valid JSON with keys:

* `ts: float` (unix time)
* `total_fetched: int`
* `total_eligible: int`
* `per_market_cap: float`
* `topN: list` length == `num_markets`

Each element in `topN` must include:

* `slug: str`
* `conditionId: str`
* `rewardsMinSize: int`
* `rewardsMaxSpread: float` (normalized)
* `outcome_token_map: dict[str,str]`
* `score: float`
* `features: dict` containing:

  * `oneHourPriceChange, competitive, volume24hrClob, liquidityClob, endDate`

### 15.2 `logs/selector.jsonl` (append-only)

Each line is one JSON object. Minimum keys:

* `ts: float`
* `kind: str` (one of: `gamma_smoke`, `list_eligible`, `select_top_n`, `selector_error`, `shutdown`)
* plus any relevant counters/outputs for that run

### 15.3 `logs/maker.jsonl` (append-only)

Each line is one JSON object. Minimum keys:

* `ts: float`
* `kind: str` (one of: `paper_one`, `paper_heartbeat`, `shutdown`; later `live_heartbeat`, `shutdown_cancel_attempt`)
* `slug: str`
* for heartbeats:

  * `mids: {token_or_outcome: float | null}`
  * `quotes: {token_or_outcome: {bid: float, ask: float, size: float}}`
  * `in_band: {token_or_outcome: {bid: bool, ask: bool}}`
  * `replace_needed: {token_or_outcome: {bid: bool, ask: bool}}`

## 16) SQLite schema (minimum contract)

**Rule:** DB must allow restart recovery and prevent duplicate order placement.

### 16.1 `runtime_state`

* `key TEXT PRIMARY KEY`
* `value TEXT NOT NULL`
  Examples:
* `last_rotation_ts`
* `mode`
* `version`

### 16.2 `active_markets`

* `condition_id TEXT PRIMARY KEY`
* `slug TEXT NOT NULL`
* `entered_at REAL NOT NULL`
* `score_at_entry REAL NOT NULL`

### 16.3 `open_orders` (live mode)

* `order_id TEXT PRIMARY KEY`
* `condition_id TEXT NOT NULL`
* `token_id TEXT NOT NULL`
* `side TEXT NOT NULL` (BUY/SELL)
* `price REAL NOT NULL`
* `size REAL NOT NULL`
* `status TEXT NOT NULL` (OPEN/CANCELED/FILLED/PARTIAL)
* `created_at REAL NOT NULL`
* `updated_at REAL NOT NULL`

## 17) Error handling and retry policy

**Rule:** loops must not crash the process due to transient errors.

### 17.1 Gamma requests

* timeouts + retry/backoff for HTTP errors
* on repeated failure: append a selector log line with `kind="selector_error"` and return without changing market set

### 17.2 CLOB reads

* if a token’s book cannot be fetched: set mid to null for that iteration
* if mid is null: mark quotes as disabled for that token for that iteration and do not place/replace

### 17.3 Live order actions

* if cancel/place fails: retry limited times
* if repeated failures exceed threshold: cancel everything possible and enter a paused state
* always append a log line describing the failure mode

## 18) Security / secrets

**Rule:** never store private keys in code, config, logs, or SQLite.

* Live mode requires `PM_PRIVATE_KEY` via environment variable
* Logs must not include raw signed payloads or secrets
* If printing wallet identifiers, print only shortened address prefix/suffix

## 19) Operational rules

* Paper mode is default forever.
* Live mode must require explicit `--live` flag and must refuse to run without it.
* On SIGINT/SIGTERM in live mode:

  * cancel all open orders for active markets
  * append a `maker.jsonl` line with `kind="shutdown_cancel_attempt"` summarizing how many cancels succeeded/failed
  * then exit

## 20) Open questions (track by appending to PROGRESS.md as discovered)

* What exact methods/endpoints are available via `py-clob-client` for:

  * listing open orders
  * reading fills/trades
* Whether reward eligibility can be inferred purely from Gamma fields for all markets (or if the rewards page has additional hidden logic)
* Whether any markets have outcomes not equal to “Yes/No” and how frequently (impact on token mapping)

```
::contentReference[oaicite:0]{index=0}
```