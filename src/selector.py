"""
Market selector for Polymarket Liquidity Rewards Auto-MM.

Handles market discovery, filtering, scoring and selection.
"""

import argparse
import json
import time
import logging
from typing import Dict, List, Any
import os

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

    args = parser.parse_args()

    if args.gamma_smoke:
        cmd_gamma_smoke(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()