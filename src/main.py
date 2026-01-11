"""
Main orchestrator for Polymarket Liquidity Rewards Auto-MM.

Implements paper and live trading orchestration with exactly 3 workers,
selector scheduling, and hysteresis-based market rotation.
"""

import argparse
import json
import time
import logging
import os
import sqlite3
import threading
import signal
import sys
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, Future

from . import selector
from . import maker
from .logging_utils import append_jsonl

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging for the orchestrator."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def ensure_data_dir():
    """Ensure data directory exists."""
    os.makedirs('data', exist_ok=True)


def init_database() -> str:
    """
    Initialize SQLite database with required tables.

    Returns:
        Path to database file
    """
    ensure_data_dir()
    db_path = 'data/pm_mm.db'

    conn = sqlite3.connect(db_path)
    try:
        # Create runtime_state table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        # Create active_markets table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS active_markets (
                condition_id TEXT PRIMARY KEY,
                slug TEXT NOT NULL,
                entered_at REAL NOT NULL,
                score_at_entry REAL NOT NULL
            )
        ''')

        # Create open_orders table (for future live mode)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS open_orders (
                order_id TEXT PRIMARY KEY,
                condition_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        ''')

        conn.commit()
        logger.info(f"Database initialized: {db_path}")
        return db_path

    finally:
        conn.close()


def get_state(db_path: str, key: str) -> Optional[str]:
    """Get value from runtime_state table."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute('SELECT value FROM runtime_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_state(db_path: str, key: str, value: str) -> None:
    """Set value in runtime_state table."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO runtime_state (key, value) VALUES (?, ?)',
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


def get_active_markets(db_path: str) -> List[Dict[str, Any]]:
    """Get list of active markets from database."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute('''
            SELECT condition_id, slug, entered_at, score_at_entry
            FROM active_markets
        ''')
        rows = cursor.fetchall()
        return [
            {
                'condition_id': row[0],
                'slug': row[1],
                'entered_at': row[2],
                'score_at_entry': row[3]
            }
            for row in rows
        ]
    finally:
        conn.close()


def upsert_active_market(
    db_path: str,
    condition_id: str,
    slug: str,
    entered_at: float,
    score_at_entry: float
) -> None:
    """Insert or update active market record."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('''
            INSERT OR REPLACE INTO active_markets
            (condition_id, slug, entered_at, score_at_entry)
            VALUES (?, ?, ?, ?)
        ''', (condition_id, slug, entered_at, score_at_entry))
        conn.commit()
    finally:
        conn.close()


def remove_active_market(db_path: str, condition_id: str) -> None:
    """Remove market from active_markets table."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DELETE FROM active_markets WHERE condition_id = ?', (condition_id,))
        conn.commit()
    finally:
        conn.close()


def load_target_markets() -> List[Dict[str, Any]]:
    """Load target markets from data/target_markets.json."""
    try:
        with open('data/target_markets.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('topN', [])
    except Exception as e:
        logger.error(f"Failed to load target markets: {e}")
        return []


def get_default_config() -> Dict[str, Any]:
    """
    Get default configuration for orchestrator.

    From PRD section 14: configuration parameters for rotation and hysteresis.
    """
    return {
        'num_markets': 3,
        'selector_interval_sec': 3600,  # 1 hour (avoid during test)
        'rotation_cooldown_sec': 43200,  # 12 hours
        'min_tenure_sec': 21600,  # 6 hours
        'score_replace_multiplier': 1.25,
        'poll_interval_sec': 5,
        'worker_heartbeat_interval_sec': 10.0,  # Faster heartbeats for testing
    }


def check_rotation_eligible(
    db_path: str,
    config: Dict[str, Any]
) -> bool:
    """
    Check if market rotation is eligible based on cooldown.

    From PRD section 10: rotation only if cooldown elapsed.
    """
    last_rotation_str = get_state(db_path, 'last_rotation_ts')
    if not last_rotation_str:
        return True

    last_rotation_ts = float(last_rotation_str)
    elapsed = time.time() - last_rotation_ts

    return elapsed >= config['rotation_cooldown_sec']


def should_replace_market(
    incumbent: Dict[str, Any],
    candidate: Dict[str, Any],
    config: Dict[str, Any]
) -> bool:
    """
    Check if candidate should replace incumbent based on hysteresis rules.

    From PRD section 10:
    - candidate_score >= incumbent_score * score_replace_multiplier
    - incumbent tenure >= min_tenure_sec
    """
    # Check tenure requirement
    tenure = time.time() - incumbent['entered_at']
    if tenure < config['min_tenure_sec']:
        return False

    # Check score multiplier requirement
    required_score = incumbent['score_at_entry'] * config['score_replace_multiplier']
    return candidate['score'] >= required_score


def run_selector_update(db_path: str, config: Dict[str, Any]) -> bool:
    """
    Run selector to update target markets and check for rotations.

    Returns:
        True if markets were rotated, False otherwise
    """
    logger.info("Running selector update...")

    try:
        # Run selector to regenerate target_markets.json
        selector_args = argparse.Namespace(
            select_top=True,
            write=True,
            gamma_smoke=False,
            list_eligible=False,
            n=None,
            limit=None
        )

        # Call selector directly (reusing existing functionality)
        selector.cmd_select_top(selector_args)

        # Load newly generated target markets
        new_targets = load_target_markets()
        if not new_targets:
            logger.warning("No target markets found after selector run")
            return False

        # Get currently active markets
        current_active = get_active_markets(db_path)
        current_slugs = {market['slug'] for market in current_active}
        new_slugs = {market['slug'] for market in new_targets}

        logger.info(f"Current active markets: {list(current_slugs)}")
        logger.info(f"New target markets: {list(new_slugs)}")

        # Check if rotation is eligible
        if not check_rotation_eligible(db_path, config):
            logger.info("Rotation blocked: cooldown period not elapsed")
            return False

        # Determine if any rotations are needed
        rotations_needed = []

        for new_market in new_targets:
            new_slug = new_market['slug']

            if new_slug not in current_slugs:
                # This is a new market not currently active
                # See if it should replace any current market
                for current_market in current_active:
                    if should_replace_market(current_market, new_market, config):
                        rotations_needed.append({
                            'action': 'replace',
                            'out': current_market,
                            'in': new_market
                        })
                        break

        # Execute rotations
        if not rotations_needed:
            logger.info("No rotations needed based on hysteresis rules")
            return False

        logger.info(f"Executing {len(rotations_needed)} market rotations")

        now = time.time()
        for rotation in rotations_needed:
            out_market = rotation['out']
            in_market = rotation['in']

            # Remove old market
            remove_active_market(db_path, out_market['condition_id'])

            # Add new market
            upsert_active_market(
                db_path,
                in_market['conditionId'],
                in_market['slug'],
                now,
                in_market['score']
            )

            logger.info(f"Rotated: {out_market['slug']} -> {in_market['slug']}")

        # Update last rotation timestamp
        set_state(db_path, 'last_rotation_ts', str(now))

        return True

    except Exception as e:
        logger.error(f"Selector update failed: {e}")
        return False


def paper_worker(slug: str, stop_event: threading.Event, worker_config: Dict[str, Any]) -> None:
    """
    Paper trading worker for a single market.

    Runs continuously until stop_event is set.
    """
    logger.info(f"Starting paper worker for market: {slug}")

    try:
        while not stop_event.is_set():
            # Run one iteration of paper loop
            # We'll simulate the maker paper loop here without time limit
            try:
                # Load target markets to get market info
                target_markets = load_target_markets()
                market = None
                for m in target_markets:
                    if m.get('slug') == slug:
                        market = m
                        break

                if not market:
                    logger.warning(f"Market {slug} not found in target markets, worker stopping")
                    break

                # Simulate one paper loop iteration
                from . import clob_utils

                outcome_token_map = market.get('outcome_token_map', {})
                rewards_min_size = market.get('rewardsMinSize', 0)
                rewards_max_spread = market.get('rewardsMaxSpread', 0.035)
                condition_id = market.get('conditionId')

                client = clob_utils.create_readonly_clob_client()

                # Fetch order books and compute midpoints
                mids = {}
                for outcome, token_id in outcome_token_map.items():
                    order_book = clob_utils.fetch_order_book(client, token_id)
                    if order_book:
                        midpoint = clob_utils.compute_midpoint_proxy(order_book, rewards_min_size)
                        mids[outcome] = midpoint
                    else:
                        mids[outcome] = None

                # Log heartbeat
                heartbeat_record = {
                    'ts': time.time(),
                    'kind': 'orchestrator_worker_heartbeat',
                    'slug': slug,
                    'condition_id': condition_id,
                    'mids': mids,
                    'worker_type': 'paper'
                }
                append_jsonl('logs/maker.jsonl', heartbeat_record)

                # Print heartbeat to console
                print(f"[WORKER {slug}] Heartbeat: {mids}")

            except Exception as e:
                logger.warning(f"Paper worker {slug} iteration failed: {e}")

            # Sleep until next iteration (with early exit on stop)
            for _ in range(int(worker_config['worker_heartbeat_interval_sec'])):
                if stop_event.is_set():
                    break
                time.sleep(1.0)

        logger.info(f"Paper worker for {slug} stopping")

    except Exception as e:
        logger.error(f"Paper worker {slug} failed: {e}")


def cmd_paper(args) -> None:
    """
    Run paper mode orchestrator.

    Implements the full orchestration with exactly 3 workers and hysteresis.
    """
    print(f"Starting paper mode orchestrator for {args.seconds} seconds")

    # Initialize database
    db_path = init_database()
    config = get_default_config()

    # Set up signal handler for graceful shutdown
    shutdown = [False]
    worker_futures = []
    executor = None

    def signal_handler(signum, frame):
        print("\nReceived interrupt signal, shutting down gracefully...")
        shutdown[0] = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        start_time = time.time()
        last_selector_run = 0

        # For testing purposes, skip initial selector run if target_markets.json already exists
        target_markets = load_target_markets()
        if not target_markets:
            print("Running initial selector update...")
            run_selector_update(db_path, config)
            last_selector_run = time.time()
        else:
            print("Using existing target_markets.json")

        # Load initial target markets and set them as active if none exist
        target_markets = load_target_markets()
        active_markets = get_active_markets(db_path)

        if not active_markets and target_markets:
            print("No active markets found, initializing with top 3 from selector")
            now = time.time()
            for market in target_markets[:config['num_markets']]:
                upsert_active_market(
                    db_path,
                    market['conditionId'],
                    market['slug'],
                    now,
                    market['score']
                )

        # Start thread pool for workers
        executor = ThreadPoolExecutor(max_workers=config['num_markets'])
        stop_events = {}

        while not shutdown[0]:
            elapsed = time.time() - start_time

            # Check if we've exceeded time limit
            if elapsed >= args.seconds:
                print(f"Time limit reached ({args.seconds}s), stopping orchestrator")
                break

            # Check if it's time to run selector (skip if no active markets or during early initialization)
            if (time.time() - last_selector_run >= config['selector_interval_sec'] and
                active_markets and
                elapsed > 60):  # Wait at least 60s before first rotation check
                print("Running periodic selector update...")
                rotated = run_selector_update(db_path, config)
                last_selector_run = time.time()

                if rotated:
                    print("Markets rotated, restarting workers...")
                    # Stop all current workers
                    for stop_event in stop_events.values():
                        stop_event.set()

                    # Wait for workers to finish
                    for future in worker_futures:
                        future.result(timeout=10)

                    worker_futures.clear()
                    stop_events.clear()

            # Get current active markets
            active_markets = get_active_markets(db_path)

            # Ensure we have exactly 3 workers running
            current_workers = set(stop_events.keys())
            target_workers = {market['slug'] for market in active_markets[:config['num_markets']]}

            # Stop workers for markets no longer active
            for slug in current_workers - target_workers:
                print(f"Stopping worker for removed market: {slug}")
                stop_events[slug].set()
                del stop_events[slug]

            # Start workers for new markets
            for slug in target_workers - current_workers:
                print(f"Starting worker for new market: {slug}")
                stop_event = threading.Event()
                stop_events[slug] = stop_event
                future = executor.submit(paper_worker, slug, stop_event, config)
                worker_futures.append(future)

            print(f"[{elapsed:.1f}s] Active workers: {len(stop_events)} markets: {list(target_workers)}")

            # Sleep until next check
            time.sleep(config['poll_interval_sec'])

        print(f"\nOrchestrator completed: {elapsed:.1f}s")

    except Exception as e:
        print(f"ERROR: Orchestrator failed: {e}")
        return

    finally:
        # Clean shutdown
        print("Stopping all workers...")

        # Stop all workers
        for stop_event in stop_events.values():
            stop_event.set()

        # Wait for workers to finish
        if executor:
            executor.shutdown(wait=True)

        print("All workers stopped")


def live_worker(slug: str, stop_event: threading.Event, worker_config: Dict[str, Any], private_key: str) -> None:
    """
    Live trading worker for a single market.

    Places and maintains 4 GTC orders (Yes/No BUY/SELL) until stop_event is set.
    """
    logger.info(f"Starting live worker for market: {slug}")

    try:
        # Load target markets to get market info
        target_markets = load_target_markets()
        market = None
        for m in target_markets:
            if m.get('slug') == slug:
                market = m
                break

        if not market:
            logger.warning(f"Market {slug} not found in target markets, worker stopping")
            return

        # Extract market data
        outcome_token_map = market.get('outcome_token_map', {})
        rewards_min_size = market.get('rewardsMinSize', 0)
        rewards_max_spread = market.get('rewardsMaxSpread', 0.035)
        condition_id = market.get('conditionId')

        # Create live CLOB client with credentials
        from py_clob_client import ClobClient
        from py_clob_client.client import ClobClient

        try:
            # Initialize live client with private key
            client = ClobClient("https://clob.polymarket.com", key=private_key)
            logger.info(f"Live client initialized for market {slug}")
        except Exception as e:
            logger.error(f"Failed to initialize live client for {slug}: {e}")
            return

        # Track active orders
        active_orders = {}  # {outcome: {side: order_id}}

        while not stop_event.is_set():
            try:
                # Fetch order books and compute target quotes
                from . import clob_utils

                mids = {}
                target_quotes = {}

                for outcome, token_id in outcome_token_map.items():
                    order_book = clob_utils.fetch_order_book(client, token_id)
                    if not order_book:
                        mids[outcome] = None
                        continue

                    # Compute midpoint
                    midpoint = clob_utils.compute_midpoint_proxy(order_book, rewards_min_size)
                    mids[outcome] = midpoint

                    if midpoint is not None:
                        # Compute target quotes
                        half_spread = rewards_max_spread * worker_config.get('half_spread_frac', 0.85)
                        tick_size = clob_utils.get_tick_size(midpoint)

                        quotes = {
                            'bid': clob_utils.round_to_tick(midpoint - half_spread, tick_size, 'down'),
                            'ask': clob_utils.round_to_tick(midpoint + half_spread, tick_size, 'up'),
                            'size': rewards_min_size * worker_config.get('size_buffer', 1.1)
                        }
                        target_quotes[outcome] = quotes

                # Check existing orders and place/replace as needed
                for outcome, token_id in outcome_token_map.items():
                    if outcome not in target_quotes:
                        continue

                    target = target_quotes[outcome]

                    # Initialize order tracking for this outcome
                    if outcome not in active_orders:
                        active_orders[outcome] = {'bid': None, 'ask': None}

                    # Check each side (bid/ask)
                    for side in ['bid', 'ask']:
                        try:
                            # Determine order side and price
                            if side == 'bid':
                                order_side = 'BUY'
                                price = target['bid']
                            else:
                                order_side = 'SELL'
                                price = target['ask']

                            # Check if we need to place/replace order
                            current_order_id = active_orders[outcome][side]

                            # Cancel existing order if it exists
                            if current_order_id:
                                try:
                                    client.cancel_order(current_order_id)
                                    logger.info(f"Cancelled {outcome} {side} order: {current_order_id}")
                                except Exception as e:
                                    logger.warning(f"Failed to cancel order {current_order_id}: {e}")

                            # Place new order
                            order = client.create_order(
                                token_id=token_id,
                                price=price,
                                size=target['size'],
                                side=order_side,
                                order_type='GTC'
                            )

                            if order and 'id' in order:
                                active_orders[outcome][side] = order['id']
                                logger.info(f"Placed {outcome} {side} order: {order['id']} @ {price:.4f} size {target['size']}")
                                print(f"[LIVE {slug}] Placed {outcome} {side}: {order['id']} @ {price:.4f}")
                            else:
                                logger.warning(f"Failed to place {outcome} {side} order")
                                active_orders[outcome][side] = None

                        except Exception as e:
                            logger.error(f"Error managing {outcome} {side} order: {e}")
                            active_orders[outcome][side] = None

                # Log heartbeat
                heartbeat_record = {
                    'ts': time.time(),
                    'kind': 'live_worker_heartbeat',
                    'slug': slug,
                    'condition_id': condition_id,
                    'mids': mids,
                    'target_quotes': target_quotes,
                    'active_orders': active_orders,
                    'worker_type': 'live'
                }
                append_jsonl('logs/maker.jsonl', heartbeat_record)

                # Print heartbeat to console
                total_orders = sum(1 for outcome_orders in active_orders.values()
                                 for order_id in outcome_orders.values() if order_id)
                print(f"[LIVE {slug}] Heartbeat: {total_orders} active orders, mids: {mids}")

            except Exception as e:
                logger.warning(f"Live worker {slug} iteration failed: {e}")

            # Sleep until next iteration (with early exit on stop)
            for _ in range(int(worker_config.get('worker_heartbeat_interval_sec', 30.0))):
                if stop_event.is_set():
                    break
                time.sleep(1.0)

        # Cleanup: cancel all active orders on shutdown
        print(f"[LIVE {slug}] Shutting down, cancelling all orders...")
        cancelled_count = 0
        failed_count = 0

        for outcome in active_orders:
            for side in ['bid', 'ask']:
                order_id = active_orders[outcome].get(side)
                if order_id:
                    try:
                        client.cancel_order(order_id)
                        logger.info(f"Cancelled {outcome} {side} order on shutdown: {order_id}")
                        cancelled_count += 1
                    except Exception as e:
                        logger.error(f"Failed to cancel order {order_id} on shutdown: {e}")
                        failed_count += 1

        # Log shutdown attempt
        shutdown_record = {
            'ts': time.time(),
            'kind': 'shutdown_cancel_attempt',
            'slug': slug,
            'condition_id': condition_id,
            'orders_cancelled': cancelled_count,
            'orders_failed': failed_count,
            'worker_type': 'live'
        }
        append_jsonl('logs/maker.jsonl', shutdown_record)

        print(f"[LIVE {slug}] Shutdown complete: {cancelled_count} orders cancelled, {failed_count} failed")
        logger.info(f"Live worker for {slug} stopping: {cancelled_count} cancelled, {failed_count} failed")

    except Exception as e:
        logger.error(f"Live worker {slug} failed: {e}")


def cmd_live(args) -> None:
    """
    Run live mode orchestrator.

    Requires PM_PRIVATE_KEY environment variable.
    Places and maintains 4 GTC orders per market.
    """
    print(f"Starting live mode orchestrator for {args.seconds} seconds")

    # Check for private key
    private_key = os.getenv('PM_PRIVATE_KEY')
    if not private_key:
        print("ERROR: PM_PRIVATE_KEY environment variable is required for live mode")
        print("Usage: PM_PRIVATE_KEY=your_key python -m src.main --live --seconds 120")
        return

    # Initialize database
    db_path = init_database()
    config = get_default_config()
    config['worker_heartbeat_interval_sec'] = 30.0  # Slower for live mode

    # Set up signal handler for graceful shutdown
    shutdown = [False]
    worker_futures = []
    executor = None

    def signal_handler(signum, frame):
        print("\nReceived interrupt signal, shutting down gracefully...")
        print("Cancelling all open orders...")
        shutdown[0] = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        start_time = time.time()

        # Load target markets and set them as active if none exist
        target_markets = load_target_markets()
        if not target_markets:
            print("ERROR: No target markets found in data/target_markets.json")
            print("Run selector first: python -m src.selector --select-top --write")
            return

        active_markets = get_active_markets(db_path)

        if not active_markets and target_markets:
            print("No active markets found, initializing with top 3 from selector")
            now = time.time()
            for market in target_markets[:config['num_markets']]:
                upsert_active_market(
                    db_path,
                    market['conditionId'],
                    market['slug'],
                    now,
                    market['score']
                )

        # Start thread pool for workers
        max_live_markets = 1  # Start with 1 market for live mode safety
        executor = ThreadPoolExecutor(max_workers=max_live_markets)
        stop_events = {}

        while not shutdown[0]:
            elapsed = time.time() - start_time

            # Check if we've exceeded time limit
            if elapsed >= args.seconds:
                print(f"Time limit reached ({args.seconds}s), stopping orchestrator")
                break

            # Get current active markets (limit to 1 for safety)
            active_markets = get_active_markets(db_path)
            target_workers = {market['slug'] for market in active_markets[:max_live_markets]}

            # Ensure we have exactly the right number of workers
            current_workers = set(stop_events.keys())

            # Stop workers for markets no longer active
            for slug in current_workers - target_workers:
                print(f"Stopping live worker for removed market: {slug}")
                stop_events[slug].set()
                del stop_events[slug]

            # Start workers for new markets
            for slug in target_workers - current_workers:
                print(f"Starting live worker for new market: {slug}")
                stop_event = threading.Event()
                stop_events[slug] = stop_event
                future = executor.submit(live_worker, slug, stop_event, config, private_key)
                worker_futures.append(future)

            print(f"[{elapsed:.1f}s] Active live workers: {len(stop_events)} markets: {list(target_workers)}")

            # Sleep until next check
            time.sleep(config['poll_interval_sec'])

        print(f"\nLive orchestrator completed: {elapsed:.1f}s")

    except Exception as e:
        print(f"ERROR: Live orchestrator failed: {e}")
        return

    finally:
        # Clean shutdown
        print("Stopping all live workers and cancelling orders...")

        # Stop all workers
        for stop_event in stop_events.values():
            stop_event.set()

        # Wait for workers to finish (they will cancel orders)
        if executor:
            executor.shutdown(wait=True)

        print("All live workers stopped")


def main():
    """Main entry point for orchestrator CLI."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Polymarket Liquidity Rewards Auto-MM Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--paper',
        action='store_true',
        help='Run paper mode orchestrator (default mode)'
    )

    parser.add_argument(
        '--live',
        action='store_true',
        help='Run live mode orchestrator (requires PM_PRIVATE_KEY)'
    )

    parser.add_argument(
        '--seconds',
        type=int,
        default=600,
        help='Duration to run in seconds (default: 600)'
    )

    args = parser.parse_args()

    if args.live:
        cmd_live(args)
    elif args.paper:
        cmd_paper(args)
    else:
        # Default to paper mode
        args.paper = True
        cmd_paper(args)


if __name__ == '__main__':
    main()