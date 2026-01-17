"""
Market selector for Polymarket Liquidity Rewards Auto-MM.

Handles market discovery, filtering, scoring and selection.
"""

import argparse
import json
import time
import logging
import math
from typing import Dict, List, Any, Optional
import os
from datetime import datetime, timedelta

from . import gamma
from . import config
from .logging_utils import append_jsonl
from . import clob_utils


def setup_logging():
    """Configure logging for the selector."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def get_default_config() -> Dict[str, Any]:
    """
    Load configuration from config.yaml and convert to dict format.

    Returns dict with all config values for use by selector functions.
    """
    cfg = config.load_config_or_default('config.yaml')

    # Convert Config dataclass to dict format expected by selector functions
    return {
        'exclude_restricted': cfg.exclude_restricted,
        'end_date_buffer_days': cfg.end_date_buffer_days,
        'min_volume24h': cfg.min_volume24h,
        'max_book_spread': cfg.max_book_spread,
        # Capital parameters
        'total_cap_usdc': cfg.total_cap_usdc,
        'usable_cap_frac': cfg.usable_cap_frac,
        'num_markets': cfg.num_markets,
        # Quote parameters
        'size_buffer': cfg.quote.size_buffer,
    }


def parse_outcome_token_map(market: Dict[str, Any]) -> Dict[str, str]:
    """
    Parse outcome token mapping using outcomes + clobTokenIds.

    Build a dict: {"Yes": <token_id>, "No": <token_id>}
    If outcomes are not exactly "Yes/No", preserve the exact outcome strings.

    Args:
        market: Market record from Gamma API (already extracted)

    Returns:
        Dict mapping outcome string to token ID
    """
    outcomes = market.get('outcomes', [])
    clob_token_ids = market.get('clobTokenIds', [])

    if not outcomes or not clob_token_ids:
        return {}

    if len(outcomes) != len(clob_token_ids):
        return {}

    return dict(zip(outcomes, clob_token_ids))


def compute_cap_feasibility(market: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, float]:
    """
    Compute capital feasibility metrics for a market.

    Returns dict with:
    - q: size_buffer * rewardsMinSize
    - cap_est: 3.0 * q
    - per_market_cap: usable_cap / num_markets
    - feasible: bool (cap_est <= per_market_cap)
    """
    size_buffer = cfg.get('size_buffer', 1.1)
    rewards_min_size = market.get('rewardsMinSize', 0)

    q = size_buffer * rewards_min_size
    cap_est = 3.0 * q

    total_cap = cfg.get('total_cap_usdc', 1000.0)
    usable_cap_frac = cfg.get('usable_cap_frac', 0.85)
    num_markets = cfg.get('num_markets', 3)

    usable_cap = total_cap * usable_cap_frac
    per_market_cap = usable_cap / num_markets

    return {
        'q': q,
        'cap_est': cap_est,
        'per_market_cap': per_market_cap,
        'feasible': cap_est <= per_market_cap
    }


def compute_market_score(market: Dict[str, Any], cap_feasibility: Dict[str, float]) -> float:
    """
    Compute stability-first market score from PRD section 8.

    Score formula:
    score =
      + 2.0*log1p(max_spread*100)
      + log1p(vol24)
      + 0.5*log1p(liq)
      - 4.0*one_hour
      - 1.5*competitive
      - 0.8*(cap_est/per_market_cap)
    """
    max_spread = market.get('rewardsMaxSpread', 0.0)
    one_hour = abs(market.get('oneHourPriceChange', 0.0))
    competitive = market.get('competitive', 0.0)
    vol24 = market.get('volume24hrClob', 0.0)
    liq = market.get('liquidityClob', 0.0)

    cap_est = cap_feasibility['cap_est']
    per_market_cap = cap_feasibility['per_market_cap']

    # Compute score components
    spread_term = 2.0 * math.log1p(max_spread * 100)
    vol_term = math.log1p(vol24)
    liq_term = 0.5 * math.log1p(liq)
    hour_term = -4.0 * one_hour
    comp_term = -1.5 * competitive
    cap_term = -0.8 * (cap_est / per_market_cap if per_market_cap > 0 else 0)

    score = spread_term + vol_term + liq_term + hour_term + comp_term + cap_term

    return score


def reward_eligible(market: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    """
    Check if a market is reward-eligible based on criteria from PRD section 6.

    A market is reward-eligible iff:
    - active == true
    - closed == false
    - acceptingOrders == true
    - enableOrderBook == true
    - rewardsMinSize > 0
    - rewardsMaxSpread > 0
    - default excludes:
      - restricted == true (if exclude_restricted config is true)
      - endDate within end_date_buffer_days (default 7 days)
      - volume24hrClob < min_volume24h (default 500)
    """
    # Required boolean flags
    if not market.get('active', False):
        return False
    if market.get('closed', True):  # Default to true (closed) if missing
        return False
    if not market.get('acceptingOrders', False):
        return False
    if not market.get('enableOrderBook', False):
        return False

    # Required rewards configuration
    if not (market.get('rewardsMinSize', 0) > 0):
        return False
    if not (market.get('rewardsMaxSpread', 0) > 0):
        return False

    # Exclude restricted markets if configured to do so
    if cfg.get('exclude_restricted', True) and market.get('restricted', False):
        return False

    # Exclude markets ending too soon
    end_date_str = market.get('endDate')
    if end_date_str:
        try:
            # Parse ISO date string
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            buffer_days = cfg.get('end_date_buffer_days', 7)
            cutoff_date = datetime.now().replace(tzinfo=end_date.tzinfo) + timedelta(days=buffer_days)
            if end_date < cutoff_date:
                return False
        except (ValueError, TypeError):
            # If we can't parse the date, be conservative and exclude
            return False

    # Exclude low-volume markets
    volume24h = market.get('volume24hrClob', 0)
    min_volume = cfg.get('min_volume24h', 500.0)
    if volume24h < min_volume:
        return False

    return True


def cmd_gamma_smoke(args) -> None:
    """
    Smoke test: fetch and display N markets from Gamma API.

    This verifies that the Gamma pagination client works correctly.
    """
    print(f"Fetching {args.n} markets from Gamma API...")

    markets_fetched = 0
    start_time = time.time()

    try:
        for market in gamma.iter_markets(limit=min(args.n, 100), closed=False):
            markets_fetched += 1

            # Print one-line summary
            slug = market.get('slug', 'unknown')
            rewards_min = market.get('rewardsMinSize', 0)
            rewards_spread = market.get('rewardsMaxSpread', 0)
            active = market.get('active', False)

            print(f"{markets_fetched:2d}. {slug} | rewards_min={rewards_min} | max_spread={rewards_spread:.3f} | active={active}")

            if markets_fetched >= args.n:
                break

    except Exception as e:
        print(f"ERROR: Failed to fetch markets: {e}")
        # Log error to JSONL
        error_record = {
            'ts': time.time(),
            'kind': 'gamma_smoke',
            'status': 'error',
            'markets_fetched': markets_fetched,
            'error': str(e)
        }
        append_jsonl('logs/selector.jsonl', error_record)
        return

    elapsed_time = time.time() - start_time

    print(f"\\nSmoke test complete: fetched {markets_fetched} markets in {elapsed_time:.2f}s")

    # Append success record to JSONL
    smoke_record = {
        'ts': time.time(),
        'kind': 'gamma_smoke',
        'status': 'success',
        'markets_fetched': markets_fetched,
        'elapsed_sec': elapsed_time,
        'requested_n': args.n
    }
    append_jsonl('logs/selector.jsonl', smoke_record)


def cmd_list_eligible(args) -> None:
    """
    List reward-eligible markets with token mapping.

    This verifies the reward eligibility filter and outcome token mapping functions.
    """
    print(f"Fetching up to {args.limit} markets and filtering for reward eligibility...")

    cfg = get_default_config()
    markets_fetched = 0
    eligible_markets = []
    start_time = time.time()

    try:
        for market in gamma.iter_markets(limit=100, closed=False):
            markets_fetched += 1

            if reward_eligible(market, cfg):
                # Parse outcome token mapping
                token_map = parse_outcome_token_map(market)

                eligible_market = {
                    'slug': market.get('slug'),
                    'rewardsMinSize': market.get('rewardsMinSize'),
                    'rewardsMaxSpread': market.get('rewardsMaxSpread'),
                    'volume24hrClob': market.get('volume24hrClob'),
                    'outcomes': market.get('outcomes', []),
                    'token_map': token_map
                }
                eligible_markets.append(eligible_market)

            # Stop when we reach the fetch limit
            if markets_fetched >= args.limit:
                break

    except Exception as e:
        print(f"ERROR: Failed to fetch markets: {e}")
        error_record = {
            'ts': time.time(),
            'kind': 'list_eligible',
            'status': 'error',
            'markets_fetched': markets_fetched,
            'eligible_count': len(eligible_markets),
            'error': str(e)
        }
        append_jsonl('logs/selector.jsonl', error_record)
        return

    elapsed_time = time.time() - start_time

    print(f"\\nEligibility filtering complete:")
    print(f"total_fetched: {markets_fetched}")
    print(f"total_eligible: {len(eligible_markets)}")
    print()

    # Print first 10 eligible markets with token mapping
    print("First 10 eligible markets with token mapping:")
    for i, market in enumerate(eligible_markets[:10]):
        token_map_str = ', '.join([f"{k}: {v}" for k, v in market['token_map'].items()])
        print(f"{i+1:2d}. {market['slug']} | "
              f"rewards_min={market['rewardsMinSize']} | "
              f"max_spread={market['rewardsMaxSpread']:.3f} | "
              f"vol24h={market['volume24hrClob']:.0f} | "
              f"tokens: {{{token_map_str}}}")

    # Append success record to JSONL
    eligible_record = {
        'ts': time.time(),
        'kind': 'list_eligible',
        'status': 'success',
        'markets_fetched': markets_fetched,
        'eligible_count': len(eligible_markets),
        'elapsed_sec': elapsed_time,
        'fetch_limit': args.limit,
        'config_used': cfg
    }
    append_jsonl('logs/selector.jsonl', eligible_record)


def cmd_select_top(args) -> None:
    """
    Select top N markets by scoring and optionally write to data/target_markets.json.

    This implements the full selector pipeline: eligibility filtering, cap feasibility,
    scoring, and selection of top N markets.
    """
    print(f"Running market selector: scoring and selecting top markets...")

    cfg = get_default_config()
    num_markets = cfg.get('num_markets', 3)
    markets_fetched = 0
    eligible_markets = []
    scored_markets = []
    start_time = time.time()

    try:
        # Step 1: Fetch and filter for reward eligibility
        print(f"Step 1: Fetching markets and filtering for reward eligibility...")
        for market in gamma.iter_markets(limit=100, closed=False):
            markets_fetched += 1

            if reward_eligible(market, cfg):
                eligible_markets.append(market)

            # Continue fetching until we have enough or hit practical limit
            if markets_fetched >= 1000:
                break

        print(f"  Found {len(eligible_markets)} eligible markets from {markets_fetched} total")

        # Step 2: Apply capital feasibility filter and scoring
        print(f"Step 2: Computing capital feasibility and scores...")
        cap_feasible_markets = []
        for market in eligible_markets:
            cap_feasibility = compute_cap_feasibility(market, cfg)

            if cap_feasibility['feasible']:
                token_map = parse_outcome_token_map(market)
                cap_feasible_markets.append((market, cap_feasibility, token_map))

        print(f"  Found {len(cap_feasible_markets)} cap-feasible markets")

        # Step 3: Filter for two-sided order books (CLOB preflight)
        print(f"Step 3: Checking for two-sided order books...")
        clob_client = clob_utils.create_readonly_clob_client()
        two_sided_rejected = 0
        max_spread = cfg.get('max_book_spread')

        for market, cap_feasibility, token_map in cap_feasible_markets:
            is_two_sided, reject_reason = clob_utils.check_market_two_sided(clob_client, token_map, max_spread=max_spread)

            if not is_two_sided:
                two_sided_rejected += 1
                print(f"  SKIP: {market.get('slug')} - {reject_reason}")
                continue

            score = compute_market_score(market, cap_feasibility)

            scored_market = {
                'slug': market.get('slug'),
                'conditionId': market.get('conditionId'),
                'rewardsMinSize': market.get('rewardsMinSize'),
                'rewardsMaxSpread': market.get('rewardsMaxSpread'),
                'outcome_token_map': token_map,
                'score': score,
                'cap_feasibility': cap_feasibility,
                'features': {
                    'oneHourPriceChange': market.get('oneHourPriceChange'),
                    'competitive': market.get('competitive'),
                    'volume24hrClob': market.get('volume24hrClob'),
                    'liquidityClob': market.get('liquidityClob'),
                    'endDate': market.get('endDate')
                }
            }
            scored_markets.append(scored_market)

        print(f"  Rejected {two_sided_rejected} markets with one-sided books")
        print(f"  Found {len(scored_markets)} markets with two-sided books")

        # Step 4: Sort by score (highest first) and select top N
        scored_markets.sort(key=lambda x: x['score'], reverse=True)
        top_markets = scored_markets[:num_markets]

        print(f"\\nTop {len(top_markets)} selected markets by score:")
        for i, market in enumerate(top_markets):
            print(f"{i+1}. {market['slug']} | score={market['score']:.3f}")

        # Step 5: Write to file if requested
        if args.write:
            print(f"\\nWriting to data/target_markets.json...")
            os.makedirs('data', exist_ok=True)

            # Build output according to schema from PRD section 15.1
            output_data = {
                'ts': time.time(),
                'total_fetched': markets_fetched,
                'total_eligible': len(eligible_markets),
                'per_market_cap': scored_markets[0]['cap_feasibility']['per_market_cap'] if scored_markets else 0,
                'topN': []
            }

            for market in top_markets:
                top_market = {
                    'slug': market['slug'],
                    'conditionId': market['conditionId'],
                    'rewardsMinSize': market['rewardsMinSize'],
                    'rewardsMaxSpread': market['rewardsMaxSpread'],
                    'outcome_token_map': market['outcome_token_map'],
                    'score': market['score'],
                    'features': market['features']
                }
                output_data['topN'].append(top_market)

            with open('data/target_markets.json', 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2)

            print(f"  Wrote {len(top_markets)} markets to data/target_markets.json")

    except Exception as e:
        print(f"ERROR: Failed to select markets: {e}")
        error_record = {
            'ts': time.time(),
            'kind': 'select_top_n',
            'status': 'error',
            'markets_fetched': markets_fetched,
            'eligible_count': len(eligible_markets),
            'scored_count': len(scored_markets),
            'error': str(e)
        }
        append_jsonl('logs/selector.jsonl', error_record)
        return

    elapsed_time = time.time() - start_time

    # Append success record to JSONL
    select_record = {
        'ts': time.time(),
        'kind': 'select_top_n',
        'status': 'success',
        'markets_fetched': markets_fetched,
        'eligible_count': len(eligible_markets),
        'cap_feasible_count': len(cap_feasible_markets),
        'two_sided_rejected': two_sided_rejected,
        'two_sided_passed': len(scored_markets),
        'selected_count': len(top_markets),
        'elapsed_sec': elapsed_time,
        'config_used': cfg,
        'top_slugs': [m['slug'] for m in top_markets],
        'top_scores': [m['score'] for m in top_markets]
    }
    append_jsonl('logs/selector.jsonl', select_record)


def cmd_print_config(args) -> None:
    """
    Print validated configuration values.

    Loads configuration and displays all values with secrets redacted.
    """
    print("Loading and validating configuration...")

    try:
        # Try to load config.yaml, fall back to defaults if not found
        cfg = config.load_config_or_default()

        print("✓ Configuration loaded successfully")
        print()

        # Display formatted config
        config_display = config.format_config_for_display(cfg, redact_secrets=True)
        print(config_display)

    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}")
        print()
        print("To create a config file:")
        print("  cp config.yaml.example config.yaml")
        print("  # Edit config.yaml as needed")


def main():
    """Main entry point for selector CLI."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Polymarket Liquidity Rewards Market Selector',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Gamma smoke test
    parser.add_argument(
        '--gamma-smoke',
        action='store_true',
        help='Test Gamma API pagination by fetching N markets'
    )
    parser.add_argument(
        '--n',
        type=int,
        default=5,
        help='Number of markets to fetch for smoke test (default: 5)'
    )

    # List eligible markets
    parser.add_argument(
        '--list-eligible',
        action='store_true',
        help='List reward-eligible markets with token mapping'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=200,
        help='Maximum number of markets to fetch for eligibility filtering (default: 200)'
    )

    # Select top markets
    parser.add_argument(
        '--select-top',
        action='store_true',
        help='Select top N markets by scoring and optionally write to data/target_markets.json'
    )
    parser.add_argument(
        '--write',
        action='store_true',
        help='Write results to data/target_markets.json (use with --select-top)'
    )

    # Print config
    parser.add_argument(
        '--print-config',
        action='store_true',
        help='Print validated configuration values (redacting secrets)'
    )

    args = parser.parse_args()

    if args.gamma_smoke:
        cmd_gamma_smoke(args)
    elif args.list_eligible:
        cmd_list_eligible(args)
    elif args.select_top:
        cmd_select_top(args)
    elif args.print_config:
        cmd_print_config(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()