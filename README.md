# PM-Rewards: Polymarket Liquidity Rewards Auto Market Maker

A Python service that automatically earns Polymarket Liquidity Rewards by implementing a conservative, stability-first market-making strategy across the top 3 reward-eligible markets.

## 🎯 What It Does

**PM-Rewards** is an automated market maker that:
- **Dynamically selects** the best 3 reward-eligible markets from Polymarket using a stability-first scoring algorithm
- **Maintains two-sided quotes** (Yes/No Buy/Sell) in each market to earn liquidity rewards
- **Implements hysteresis** to minimize market churn and maintain stable operations
- **Defaults to paper mode** for safe testing, with optional live trading mode
- **Uses a single wallet** with 1000 USDC total capital allocation

### Key Philosophy: Stability Over Profits

The system prioritizes markets with:
- ✅ **Lower volatility** (penalizes high 1-hour price changes)
- ✅ **Better reward spreads** (higher `rewardsMaxSpread`)
- ✅ **Adequate volume** (minimum 24h volume requirements)
- ✅ **Lower competition** (penalizes high `competitive` scores)
- ✅ **Capital efficiency** (fits within allocated per-market budgets)

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone <repository-url>
cd PM-rewards

# Install dependencies
pip install -r requirements.txt

# Copy configuration template
cp config.yaml.example config.yaml
```

### 2. Configuration

Edit `config.yaml` to customize settings (see [Configuration](#-configuration) section for details):

```yaml
# Basic settings - modify as needed
total_cap_usdc: 1000.0        # Your total capital
usable_cap_frac: 0.85         # Use 85% of capital
min_volume24h: 500.0          # Minimum 24h volume filter
```

### 3. Test the System (Paper Mode)

```bash
# Test market selection
python -m src.selector --select-top --write

# Test single market maker
python -m src.maker --paper-one --slug $(jq -r '.topN[0].slug' data/target_markets.json)

# Run full orchestrator (paper mode - safe)
python -m src.main --paper --seconds 300
```

### 4. Live Trading (When Ready)

```bash
# Set your Polymarket private key
export PM_PRIVATE_KEY="your_polymarket_private_key_here"

# Start live trading (be careful!)
python -m src.main --live --seconds 3600
```

### 5. Monitor Operations

```bash
# Web dashboard (real-time monitoring)
python dashboard.py
# Open http://localhost:5000 in your browser

# View logs
tail -f logs/maker.jsonl logs/selector.jsonl
```

## 📋 CLI Commands

### Market Selector (`python -m src.selector`)

| Command | Description | Example |
|---------|-------------|---------|
| `--gamma-smoke --n N` | Test Gamma API by fetching N markets | `python -m src.selector --gamma-smoke --n 5` |
| `--list-eligible --limit L` | List reward-eligible markets | `python -m src.selector --list-eligible --limit 100` |
| `--select-top --write` | Select top 3 markets and save to file | `python -m src.selector --select-top --write` |
| `--print-config` | Display current configuration | `python -m src.selector --print-config` |

### Market Maker (`python -m src.maker`)

| Command | Description | Example |
|---------|-------------|---------|
| `--paper-one --slug SLUG` | Single market analysis | `python -m src.maker --paper-one --slug "market-slug"` |
| `--paper-loop --slug SLUG --seconds N` | Continuous paper trading | `python -m src.maker --paper-loop --slug "market-slug" --seconds 120` |

### Main Orchestrator (`python -m src.main`)

| Command | Description | Example |
|---------|-------------|---------|
| `--paper --seconds N` | Paper mode orchestrator | `python -m src.main --paper --seconds 600` |
| `--live --seconds N` | Live trading mode | `PM_PRIVATE_KEY=key python -m src.main --live --seconds 3600` |

## ⚙️ Configuration

The system is configured via `config.yaml`. Key sections:

### Capital Allocation
```yaml
total_cap_usdc: 1000.0        # Total available capital
usable_cap_frac: 0.85         # Fraction to actually use (15% buffer)
num_markets: 3                # Always trade exactly 3 markets
```

### Market Filtering
```yaml
exclude_restricted: true      # Skip restricted markets
end_date_buffer_days: 7       # Days before expiry to exclude
min_volume24h: 500.0          # Minimum 24h volume requirement
```

### Timing & Rotation
```yaml
selector_interval_sec: 900    # 15 min - how often to recheck market selection
rotation_cooldown_sec: 43200  # 12 hours - minimum time between market rotations
min_tenure_sec: 21600         # 6 hours - minimum time a market stays active
score_replace_multiplier: 1.25 # New market needs 1.25x score to replace current
```

### Quote Parameters
```yaml
quote:
  size_buffer: 1.1            # Order size = rewardsMinSize × 1.1
  half_spread_frac: 0.85      # Use 85% of allowed spread
  update_min_ticks: 2         # Minimum price change to trigger quote update
```

### Live Mode Safety
```yaml
live:
  enabled_by_flag_only: true  # Must be true - prevents accidental live trading
  max_markets_live: 1         # Start with 1 market for initial testing
  cancel_on_exit: true        # Auto-cancel all orders on shutdown
```

## 🏗️ Architecture

### Directory Structure
```
PM-rewards/
├── src/                     # Main Python package
│   ├── main.py              # Orchestrator (3 worker threads)
│   ├── selector.py          # Market selection & scoring
│   ├── maker.py             # Market making logic
│   ├── gamma.py             # Polymarket Gamma API client
│   ├── clob_utils.py        # Order book utilities
│   ├── config.py            # Configuration management
│   ├── db.py                # SQLite database helpers
│   └── logging_utils.py     # Append-only JSONL logging
├── data/                    # Runtime data
│   ├── target_markets.json  # Selected markets (auto-generated)
│   └── pm_mm.db             # SQLite database
├── logs/                    # Append-only logs
│   ├── selector.jsonl       # Market selection logs
│   └── maker.jsonl          # Trading activity logs
├── config.yaml              # Your configuration
└── dashboard.py             # Web monitoring interface
```

### Market Selection Pipeline

1. **Fetch Markets** → Gamma API pagination with retries
2. **Filter Eligible** → Apply 8 eligibility criteria
3. **Check Feasibility** → Estimate capital requirements
4. **Score Markets** → Stability-first scoring formula
5. **Select Top 3** → Write to `data/target_markets.json`

### Market Making Loop (per market)

1. **Fetch Order Books** → Get current CLOB data for Yes/No tokens
2. **Compute Midpoints** → Size-weighted midpoint calculation
3. **Calculate Quotes** → Bid/ask around midpoint with spread
4. **Check Churn** → Determine if quotes need updating
5. **Update Orders** → Place/replace orders (live mode only)
6. **Log Heartbeat** → Append state to JSONL logs

### Orchestration

- **3 Worker Threads** → One per selected market
- **Periodic Selector** → Rerun market selection every 15 minutes
- **Hysteresis Logic** → Prevent frequent market rotations
- **Graceful Shutdown** → Cancel all orders on SIGINT/SIGTERM

## 📊 Market Selection Scoring

Markets are scored using this stability-first formula:

```
score = + 2.0 × log1p(max_spread × 100)        # Reward incentive
        + log1p(vol24)                          # Volume bonus
        + 0.5 × log1p(liq)                      # Liquidity bonus
        - 4.0 × one_hour                        # Volatility penalty
        - 1.5 × competitive                     # Competition penalty
        - 0.8 × (cap_est / per_market_cap)      # Efficiency penalty
```

**Higher scores = better markets**. The system selects the top 3 scoring markets that pass all eligibility filters.

## 🛡️ Safety Features

### Paper Mode (Default)
- **No real orders** placed on Polymarket
- **Identical logging** to live mode for testing
- **Safe for experimentation** and strategy validation

### Live Mode Safeguards
- **Explicit opt-in** via `--live` flag + `PM_PRIVATE_KEY` environment variable
- **Configuration validation** - `live.enabled_by_flag_only` must be `true`
- **Gradual scaling** - start with `max_markets_live: 1`
- **Auto-cancellation** - all orders cancelled on shutdown (Ctrl+C)
- **Order tracking** - SQLite database tracks all live orders

### Error Handling
- **Retry logic** with exponential backoff for API failures
- **Graceful degradation** - continue with other markets if one fails
- **Comprehensive logging** - all errors logged to JSONL files
- **Process isolation** - worker thread failures don't crash main process

## 📈 Monitoring & Observability

### Web Dashboard
```bash
python dashboard.py
# Visit http://localhost:5000
```
- Real-time worker status
- Current market data
- Performance metrics
- Process controls

### Log Files (Append-only JSONL)

**Selector Logs** (`logs/selector.jsonl`):
```json
{"ts": 1768531078.901, "kind": "select_top_n", "status": "success", "selected_count": 3, "scores": [...]}
```

**Maker Logs** (`logs/maker.jsonl`):
```json
{"ts": 1768531110.456, "kind": "paper_loop_heartbeat", "slug": "...", "mids": {...}, "replace_needed": {...}}
```

### Database State

SQLite database (`data/pm_mm.db`) tracks:
- **Runtime state** - Last rotation time, mode, version
- **Active markets** - Currently trading markets with entry timestamps
- **Open orders** - Live mode order tracking for restart safety

## 🔧 Customization

### Adjusting Market Selection

**More Conservative** (prefer stability):
```yaml
min_volume24h: 1000.0         # Higher volume requirement
score_replace_multiplier: 1.5 # Harder to replace current markets
rotation_cooldown_sec: 86400  # 24-hour rotation cooldown
```

**More Aggressive** (prefer rewards):
```yaml
min_volume24h: 200.0          # Lower volume requirement
score_replace_multiplier: 1.1 # Easier to replace markets
rotation_cooldown_sec: 21600  # 6-hour rotation cooldown
```

### Adjusting Quote Behavior

**Tighter Spreads** (more competitive):
```yaml
quote:
  half_spread_frac: 0.7       # Use 70% of allowed spread
  update_min_ticks: 1         # Update on smaller price moves
```

**Wider Spreads** (more conservative):
```yaml
quote:
  half_spread_frac: 0.9       # Use 90% of allowed spread
  update_min_ticks: 3         # Update only on larger moves
```

### Capital Allocation

**Smaller Allocation**:
```yaml
total_cap_usdc: 500.0         # Use $500 total
usable_cap_frac: 0.9          # Use 90% (less buffer)
```

**Larger Allocation**:
```yaml
total_cap_usdc: 2000.0        # Use $2000 total
usable_cap_frac: 0.8          # Use 80% (more buffer)
```

## 🚨 Important Notes

### Before Going Live

1. **Test thoroughly** in paper mode
2. **Start small** - set `max_markets_live: 1`
3. **Monitor closely** - use dashboard and logs
4. **Verify quotes** - check orders appear correctly on Polymarket
5. **Test shutdown** - ensure orders cancel properly on Ctrl+C

### Operational Considerations

- **Capital requirements**: Each market needs ~$283 (1000 × 0.85 ÷ 3)
- **API rate limits**: Built-in retry logic handles Polymarket API limits
- **Network dependency**: Requires stable internet for API calls
- **24/7 operation**: Designed to run continuously (use `screen` or `tmux`)
- **Log growth**: JSONL files grow unbounded (implement log rotation if needed)


## 📚 Further Reading

- **[PRD.md](docs/PRD.md)** - Complete technical specification
- **[PROGRESS.md](docs/PROGRESS.md)** - Development history and completed tasks
- **[AGENT.md](docs/AGENT.md)** - Development workflow and agent instructions

## 🤝 Contributing

This project follows a deliverable-first development approach. See `docs/AGENT.md` for development workflow and `docs/PROGRESS.md` for change history.

---

**⚠️ Disclaimer**: This software is for educational purposes. Market making involves financial risk. Use at your own risk and ensure compliance with applicable regulations.