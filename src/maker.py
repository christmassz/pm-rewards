"""
Market maker for Polymarket Liquidity Rewards Auto-MM.

Implements paper trading and live trading functionality for selected markets.
"""

import argparse
import json
import time
import logging
import os
from typing import Dict, List, Any, Optional
import signal
import sys

from . import clob_utils
from .logging_utils import append_jsonl

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging for the maker."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def load_target_markets() -> List[Dict[str, Any]]:
    """
    Load target markets from data/target_markets.json.

    Returns:
        List of target market records
    """
    try:
        with open('data/target_markets.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('topN', [])
    except Exception as e:
        logger.error(f"Failed to load target markets: {e}")
        return []


def find_market_by_slug(slug: str, target_markets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Find a market record by slug in target markets.

    Args:
        slug: Market slug to find
        target_markets: List of target market records

    Returns:
        Market record or None if not found
    """
    for market in target_markets:
        if market.get('slug') == slug:
            return market
    return None


def get_default_config() -> Dict[str, Any]:
    """
    Get default configuration values for maker.

    Returns default values for paper loop operation.
    """
    return {
        'size_buffer': 1.1,
        'half_spread_frac': 0.85,
        'update_min_ticks': 2,
        'loop_interval_sec': 30.0,  # Time between loop iterations
    }


def compute_quote_prices(mid: float, half_spread: float, tick_size: float) -> Dict[str, float]:
    """
    Compute bid and ask prices from midpoint and half spread.

    From PRD section 9:
    - bid = mid - half_spread (rounds down to tick)
    - ask = mid + half_spread (rounds up to tick)

    Args:
        mid: Midpoint price
        half_spread: Half of the spread
        tick_size: Tick size for rounding

    Returns:
        Dict with 'bid' and 'ask' prices
    """
    raw_bid = mid - half_spread
    raw_ask = mid + half_spread

    bid = clob_utils.round_to_tick(raw_bid, tick_size, 'down')
    ask = clob_utils.round_to_tick(raw_ask, tick_size, 'up')

    return {'bid': bid, 'ask': ask}


def check_replace_needed(
    current_quotes: Dict[str, Dict[str, float]],
    target_quotes: Dict[str, Dict[str, float]],
    mid_prices: Dict[str, float],
    market_config: Dict[str, Any],
    tick_sizes: Dict[str, float]
) -> Dict[str, Dict[str, bool]]:
    """
    Check if quotes need replacing based on churn rules.

    From PRD section 9: Replace only if:
    - order is out-of-band, OR
    - target price differs by >= update_min_ticks, OR
    - remaining size < rewardsMinSize

    Args:
        current_quotes: Current quote prices {outcome: {side: price}}
        target_quotes: Target quote prices {outcome: {side: price}}
        mid_prices: Midpoint prices {outcome: price}
        market_config: Market configuration
        tick_sizes: Tick sizes {outcome: size}

    Returns:
        Replace needed flags {outcome: {side: bool}}
    """
    replace_needed = {}
    rewards_max_spread = market_config.get('rewardsMaxSpread', 0.035)
    update_min_ticks = market_config.get('update_min_ticks', 2)

    for outcome in ['Yes', 'No']:
        replace_needed[outcome] = {}

        if outcome not in current_quotes or outcome not in target_quotes:
            # No current quotes, need to place new ones
            replace_needed[outcome]['bid'] = True
            replace_needed[outcome]['ask'] = True
            continue

        if outcome not in mid_prices or mid_prices[outcome] is None:
            # No midpoint, cannot determine if in-band
            replace_needed[outcome]['bid'] = True
            replace_needed[outcome]['ask'] = True
            continue

        mid = mid_prices[outcome]
        tick_size = tick_sizes.get(outcome, 0.01)

        for side in ['bid', 'ask']:
            current_price = current_quotes[outcome].get(side)
            target_price = target_quotes[outcome].get(side)

            if current_price is None or target_price is None:
                replace_needed[outcome][side] = True
                continue

            # Check if current quote is out-of-band
            price_diff_from_mid = abs(current_price - mid)
            out_of_band = price_diff_from_mid > rewards_max_spread

            # Check if target price differs by >= update_min_ticks
            price_diff_ticks = abs(target_price - current_price) / tick_size
            price_diff_significant = price_diff_ticks >= update_min_ticks

            # For this simple implementation, assume size is always >= rewardsMinSize
            # In live mode, we would check actual remaining order size

            replace_needed[outcome][side] = out_of_band or price_diff_significant

    return replace_needed


def cmd_paper_loop(args) -> None:
    """
    Paper loop: continuously monitor and quote one market.

    This implements the paper trading loop with heartbeat logging and churn control.
    """
    print(f"Starting paper loop for slug: {args.slug}")
    print(f"Duration: {args.seconds} seconds")

    # Load target markets
    target_markets = load_target_markets()
    if not target_markets:
        print("ERROR: No target markets found in data/target_markets.json")
        return

    # Find the specified market
    market = find_market_by_slug(args.slug, target_markets)
    if not market:
        available_slugs = [m.get('slug', 'unknown') for m in target_markets]
        print(f"ERROR: Market slug '{args.slug}' not found in target markets")
        print(f"Available slugs: {', '.join(available_slugs)}")
        return

    # Extract market data
    outcome_token_map = market.get('outcome_token_map', {})
    rewards_min_size = market.get('rewardsMinSize', 0)
    rewards_max_spread = market.get('rewardsMaxSpread', 0.035)
    condition_id = market.get('conditionId')

    print(f"Market: {market.get('slug')}")
    print(f"Condition ID: {condition_id}")
    print(f"Rewards: min_size={rewards_min_size}, max_spread={rewards_max_spread}")
    print()

    # Create CLOB client and config
    client = clob_utils.create_readonly_clob_client()
    cfg = get_default_config()

    # Loop state
    loop_count = 0
    start_time = time.time()
    current_quotes = {'Yes': {}, 'No': {}}  # Simulated current quotes

    # Set up signal handler for graceful shutdown
    shutdown = [False]

    def signal_handler(signum, frame):
        print("\\nReceived interrupt signal, shutting down gracefully...")
        shutdown[0] = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while not shutdown[0]:
            loop_start = time.time()
            elapsed_total = loop_start - start_time

            # Check if we've exceeded the time limit
            if elapsed_total >= args.seconds:
                print(f"Time limit reached ({args.seconds}s), stopping loop")
                break

            loop_count += 1
            print(f"[{loop_count:3d}] Loop iteration at {elapsed_total:.1f}s")

            # Fetch order books and compute midpoints for each outcome
            mids = {}
            target_quotes = {}
            tick_sizes = {}

            for outcome, token_id in outcome_token_map.items():
                print(f"  Processing {outcome} token...")

                # Fetch order book
                order_book = clob_utils.fetch_order_book(client, token_id)
                if not order_book:
                    print(f"    ERROR: Could not fetch order book for {outcome}")
                    mids[outcome] = None
                    continue

                # Compute midpoint proxy
                midpoint = clob_utils.compute_midpoint_proxy(order_book, rewards_min_size)
                mids[outcome] = midpoint

                if midpoint is not None:
                    # Compute tick size
                    tick_size = clob_utils.get_tick_size(midpoint)
                    tick_sizes[outcome] = tick_size

                    # Compute target quotes
                    half_spread = rewards_max_spread * cfg['half_spread_frac']
                    quotes = compute_quote_prices(midpoint, half_spread, tick_size)
                    target_quotes[outcome] = quotes

                    print(f"    {outcome}: mid={midpoint:.4f}, bid={quotes['bid']:.4f}, ask={quotes['ask']:.4f}")
                else:
                    print(f"    {outcome}: mid=None (insufficient liquidity)")

            # Check what quotes need replacing
            market_config = {
                'rewardsMaxSpread': rewards_max_spread,
                'update_min_ticks': cfg['update_min_ticks']
            }

            replace_needed = check_replace_needed(
                current_quotes, target_quotes, mids, market_config, tick_sizes
            )

            # Count replacements needed
            total_replacements = sum(
                sum(sides.values()) for sides in replace_needed.values()
            )

            print(f"  Churn check: {total_replacements} quote replacements needed")

            # Simulate updating current quotes (in paper mode, just update our state)
            for outcome in target_quotes:
                if outcome not in current_quotes:
                    current_quotes[outcome] = {}
                for side, price in target_quotes[outcome].items():
                    if replace_needed.get(outcome, {}).get(side, False):
                        current_quotes[outcome][side] = price
                        print(f"    Updated {outcome} {side} to {price:.4f}")

            # Heartbeat logging
            heartbeat_record = {
                'ts': time.time(),
                'kind': 'paper_loop_heartbeat',
                'slug': args.slug,
                'condition_id': condition_id,
                'loop_count': loop_count,
                'elapsed_sec': elapsed_total,
                'mids': mids,
                'target_quotes': target_quotes,
                'replace_needed': replace_needed,
                'replacements_count': total_replacements
            }
            append_jsonl('logs/maker.jsonl', heartbeat_record)

            print(f"  Heartbeat logged (loop {loop_count})")

            # Sleep until next iteration
            loop_duration = time.time() - loop_start
            sleep_time = max(0, cfg['loop_interval_sec'] - loop_duration)

            if sleep_time > 0 and not shutdown[0]:
                print(f"  Sleeping {sleep_time:.1f}s until next iteration...")
                time.sleep(min(sleep_time, 1.0))  # Sleep in small chunks to check shutdown

        print(f"\\nPaper loop completed: {loop_count} iterations in {elapsed_total:.1f}s")

    except Exception as e:
        print(f"ERROR: Paper loop failed: {e}")
        error_record = {
            'ts': time.time(),
            'kind': 'paper_loop',
            'slug': args.slug,
            'status': 'error',
            'error': str(e),
            'loop_count': loop_count
        }
        append_jsonl('logs/maker.jsonl', error_record)
        return


def cmd_paper_one(args) -> None:
    """
    Paper one-shot: fetch order books and compute midpoints for a market.

    This verifies CLOB read-only utilities and midpoint proxy computation.
    """
    print(f"Running paper one-shot for slug: {args.slug}")

    # Load target markets
    target_markets = load_target_markets()
    if not target_markets:
        print("ERROR: No target markets found in data/target_markets.json")
        return

    # Find the specified market
    market = find_market_by_slug(args.slug, target_markets)
    if not market:
        available_slugs = [m.get('slug', 'unknown') for m in target_markets]
        print(f"ERROR: Market slug '{args.slug}' not found in target markets")
        print(f"Available slugs: {', '.join(available_slugs)}")
        return

    # Extract market data
    outcome_token_map = market.get('outcome_token_map', {})
    rewards_min_size = market.get('rewardsMinSize', 0)

    print(f"Market: {market.get('slug')}")
    print(f"Condition ID: {market.get('conditionId')}")
    print(f"Token mapping: {outcome_token_map}")
    print(f"Rewards min size: {rewards_min_size}")
    print()

    # Create CLOB client
    client = clob_utils.create_readonly_clob_client()

    # Results storage
    results = {
        'slug': args.slug,
        'mids': {},
        'best_bid_ask': {},
        'order_books': {}
    }

    start_time = time.time()

    try:
        # Process each outcome token
        for outcome, token_id in outcome_token_map.items():
            print(f"Processing {outcome} token: {token_id}")

            # Fetch order book
            order_book = clob_utils.fetch_order_book(client, token_id)
            if not order_book:
                print(f"  ERROR: Could not fetch order book for {outcome}")
                results['mids'][outcome] = None
                results['best_bid_ask'][outcome] = {'bid': None, 'ask': None}
                continue

            # Compute midpoint proxy
            midpoint = clob_utils.compute_midpoint_proxy(order_book, rewards_min_size)
            best_bid, best_ask = clob_utils.get_best_bid_ask(order_book)

            # Store results
            results['mids'][outcome] = midpoint
            results['best_bid_ask'][outcome] = {'bid': best_bid, 'ask': best_ask}
            results['order_books'][outcome] = {
                'bid_count': len(order_book.bids) if order_book else 0,
                'ask_count': len(order_book.asks) if order_book else 0
            }

            # Print results
            print(f"  {outcome} mid: {midpoint:.4f}" if midpoint else f"  {outcome} mid: None")
            print(f"  {outcome} best bid: {best_bid:.4f}" if best_bid else f"  {outcome} best bid: None")
            print(f"  {outcome} best ask: {best_ask:.4f}" if best_ask else f"  {outcome} best ask: None")
            print(f"  Order book: {results['order_books'][outcome]['bid_count']} bids, {results['order_books'][outcome]['ask_count']} asks")
            print()

    except Exception as e:
        print(f"ERROR: Failed to process market: {e}")
        error_record = {
            'ts': time.time(),
            'kind': 'paper_one',
            'slug': args.slug,
            'status': 'error',
            'error': str(e)
        }
        append_jsonl('logs/maker.jsonl', error_record)
        return

    elapsed_time = time.time() - start_time

    print(f"Paper one-shot complete in {elapsed_time:.2f}s")

    # Summary output as required by PRD
    print(f"\\nSUMMARY - YES/NO mids and best bid/ask:")
    for outcome in ['Yes', 'No']:  # Standard outcomes
        if outcome in results['mids']:
            mid = results['mids'][outcome]
            bid_ask = results['best_bid_ask'][outcome]
            mid_str = f"{mid:.4f}" if mid is not None else "None"
            bid_str = f"{bid_ask['bid']:.4f}" if bid_ask['bid'] is not None else "None"
            ask_str = f"{bid_ask['ask']:.4f}" if bid_ask['ask'] is not None else "None"
            print(f"{outcome}: mid={mid_str}, best_bid={bid_str}, best_ask={ask_str}")

    # Append success record to JSONL
    paper_one_record = {
        'ts': time.time(),
        'kind': 'paper_one',
        'slug': args.slug,
        'status': 'success',
        'mids': results['mids'],
        'best_bid_ask': results['best_bid_ask'],
        'elapsed_sec': elapsed_time,
        'rewards_min_size': rewards_min_size
    }
    append_jsonl('logs/maker.jsonl', paper_one_record)


def main():
    """Main entry point for maker CLI."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Polymarket Liquidity Rewards Market Maker',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Paper one-shot
    parser.add_argument(
        '--paper-one',
        action='store_true',
        help='Run paper one-shot for a specific market slug'
    )

    # Paper loop
    parser.add_argument(
        '--paper-loop',
        action='store_true',
        help='Run paper loop for a specific market slug'
    )

    parser.add_argument(
        '--slug',
        type=str,
        help='Market slug to process (required with --paper-one or --paper-loop)'
    )

    parser.add_argument(
        '--seconds',
        type=int,
        default=120,
        help='Duration for paper loop in seconds (default: 120)'
    )

    args = parser.parse_args()

    if args.paper_one:
        if not args.slug:
            print("ERROR: --slug is required with --paper-one")
            parser.print_help()
            return
        cmd_paper_one(args)
    elif args.paper_loop:
        if not args.slug:
            print("ERROR: --slug is required with --paper-loop")
            parser.print_help()
            return
        cmd_paper_loop(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()