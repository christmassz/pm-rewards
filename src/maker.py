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

from . import clob_utils

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging for the maker."""
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
    parser.add_argument(
        '--slug',
        type=str,
        help='Market slug to process (required with --paper-one)'
    )

    args = parser.parse_args()

    if args.paper_one:
        if not args.slug:
            print("ERROR: --slug is required with --paper-one")
            parser.print_help()
            return
        cmd_paper_one(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()