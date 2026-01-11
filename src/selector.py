"""
Market selector for Polymarket Liquidity Rewards Auto-MM.

Handles market discovery, filtering, scoring and selection.
"""

import argparse
import json
import time
import logging
from typing import Dict, List, Any, Optional
import os
from datetime import datetime, timedelta

from . import gamma


def setup_logging():
    """Configure logging for the selector."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs('logs', exist_ok=True)


def append_jsonl(filepath: str, obj: Dict[str, Any]) -> None:
    """Append a JSON object as a single line to a JSONL file."""
    ensure_logs_dir()
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj) + '\n')


def get_default_config() -> Dict[str, Any]:
    """
    Get default configuration values.

    Later this will load from config.yaml, but for now we use hardcoded defaults
    matching the config.yaml.example structure.
    """
    return {
        'exclude_restricted': True,
        'end_date_buffer_days': 7,
        'min_volume24h': 500.0,
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

    args = parser.parse_args()

    if args.gamma_smoke:
        cmd_gamma_smoke(args)
    elif args.list_eligible:
        cmd_list_eligible(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()