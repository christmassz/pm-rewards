"""
CLOB utilities for Polymarket order book reads and midpoint computation.

Provides read-only access to Polymarket CLOB API for fetching order books
and computing size-cutoff midpoint proxies.
"""

import math
import logging
from typing import Dict, List, Any, Optional, Tuple
from py_clob_client import ClobClient

logger = logging.getLogger(__name__)


def create_readonly_clob_client() -> ClobClient:
    """
    Create a read-only CLOB client for fetching order books.

    Returns a py-clob-client instance configured for read-only operations.
    """
    # Create client without credentials for read-only operations
    client = ClobClient("https://clob.polymarket.com")
    return client


def fetch_order_book(client: ClobClient, token_id: str):
    """
    Fetch order book for a specific token ID.

    Args:
        client: CLOB client instance
        token_id: Token ID to fetch book for

    Returns:
        OrderBookSummary object or None if fetch failed
    """
    try:
        # Fetch order book from CLOB API
        book = client.get_order_book(token_id)
        return book
    except Exception as e:
        logger.warning(f"Failed to fetch order book for token {token_id}: {e}")
        return None


def compute_midpoint_proxy(
    order_book,  # OrderBookSummary object
    cutoff_size: float
) -> Optional[float]:
    """
    Compute size-cutoff midpoint proxy from order book.

    From PRD section 9:
    - bid_cutoff_px = price level where cumulative bid size >= cutoff
    - ask_cutoff_px = price level where cumulative ask size >= cutoff
    - mid = (bid_cutoff_px + ask_cutoff_px)/2

    Args:
        order_book: OrderBookSummary object from py-clob-client
        cutoff_size: Size threshold (typically rewardsMinSize)

    Returns:
        Midpoint price or None if cannot compute
    """
    if not order_book:
        return None

    try:
        bids = order_book.bids  # List of order dictionaries
        asks = order_book.asks  # List of order dictionaries

        if not bids or not asks:
            return None

        # Find bid cutoff price (cumulative size >= cutoff)
        bid_cutoff_px = None
        bid_cumulative = 0.0

        for bid in bids:
            # Bids format: order object with price/size attributes
            price = float(bid.price)
            size = float(bid.size)
            bid_cumulative += size

            if bid_cumulative >= cutoff_size:
                bid_cutoff_px = price
                break

        # Find ask cutoff price (cumulative size >= cutoff)
        ask_cutoff_px = None
        ask_cumulative = 0.0

        for ask in asks:
            # Asks format: order object with price/size attributes
            price = float(ask.price)
            size = float(ask.size)
            ask_cumulative += size

            if ask_cumulative >= cutoff_size:
                ask_cutoff_px = price
                break

        if bid_cutoff_px is None or ask_cutoff_px is None:
            logger.debug(f"Could not find cutoff prices: bid={bid_cutoff_px}, ask={ask_cutoff_px}")
            return None

        # Compute midpoint
        midpoint = (bid_cutoff_px + ask_cutoff_px) / 2.0
        return midpoint

    except Exception as e:
        logger.warning(f"Failed to compute midpoint proxy: {e}")
        return None


def get_best_bid_ask(order_book) -> Tuple[Optional[float], Optional[float]]:
    """
    Get best bid and ask prices from order book.

    Args:
        order_book: OrderBookSummary object from py-clob-client

    Returns:
        (best_bid, best_ask) tuple, either may be None
    """
    if not order_book:
        return None, None

    try:
        bids = order_book.bids
        asks = order_book.asks

        best_bid = float(bids[0].price) if bids else None
        best_ask = float(asks[0].price) if asks else None

        return best_bid, best_ask

    except Exception as e:
        logger.warning(f"Failed to get best bid/ask: {e}")
        return None, None


def get_tick_size(midpoint: float, order_price_min_tick_size: Optional[float] = None) -> float:
    """
    Get tick size for price rounding.

    From PRD section 9:
    - tick size = orderPriceMinTickSize if present else:
    - 0.001 if mid < 0.1 else 0.01

    Args:
        midpoint: Current midpoint price
        order_price_min_tick_size: Explicit tick size from market config

    Returns:
        Tick size for rounding
    """
    if order_price_min_tick_size is not None:
        return order_price_min_tick_size

    return 0.001 if midpoint < 0.1 else 0.01


def round_to_tick(price: float, tick_size: float, direction: str) -> float:
    """
    Round price to tick boundary.

    From PRD section 9:
    - bid rounds down to tick
    - ask rounds up to tick

    Args:
        price: Price to round
        tick_size: Tick size for rounding
        direction: 'down' for bids, 'up' for asks

    Returns:
        Rounded price
    """
    if direction == 'down':
        return math.floor(price / tick_size) * tick_size
    elif direction == 'up':
        return math.ceil(price / tick_size) * tick_size
    else:
        return round(price / tick_size) * tick_size


def has_two_sided_book(order_book) -> bool:
    """
    Check if an order book has both bids and asks (two-sided).

    Args:
        order_book: OrderBookSummary object from py-clob-client

    Returns:
        True if book has at least one bid AND at least one ask
    """
    if not order_book:
        return False

    try:
        bids = order_book.bids
        asks = order_book.asks
        return bool(bids) and bool(asks)
    except Exception as e:
        logger.warning(f"Failed to check two-sided book: {e}")
        return False


def check_market_two_sided(
    client: ClobClient,
    token_map: Dict[str, str],
    max_spread: Optional[float] = None
) -> Tuple[bool, str]:
    """
    Check if a market has two-sided books for both YES and NO tokens,
    and optionally check that the spread is within a threshold.

    Args:
        client: CLOB client instance
        token_map: Dict mapping outcome names to token IDs (e.g., {"Yes": "123", "No": "456"})
        max_spread: Optional maximum allowed spread (best_ask - best_bid). If None, no spread check.

    Returns:
        (is_two_sided, reason) tuple where:
        - is_two_sided: True if both tokens have two-sided books and spreads are acceptable
        - reason: Description of why it failed (empty string if passed)
    """
    yes_token = token_map.get('Yes')
    no_token = token_map.get('No')

    if not yes_token or not no_token:
        return False, "missing token IDs"

    # Check YES token
    yes_book = fetch_order_book(client, yes_token)
    if not has_two_sided_book(yes_book):
        yes_bids = len(yes_book.bids) if yes_book and yes_book.bids else 0
        yes_asks = len(yes_book.asks) if yes_book and yes_book.asks else 0
        return False, f"YES token one-sided (bids={yes_bids}, asks={yes_asks})"

    # Check YES spread if max_spread is specified
    if max_spread is not None:
        yes_best_bid, yes_best_ask = get_best_bid_ask(yes_book)
        if yes_best_bid is not None and yes_best_ask is not None:
            yes_spread = yes_best_ask - yes_best_bid
            if yes_spread > max_spread:
                return False, f"YES spread too wide ({yes_best_bid} - {yes_best_ask})"

    # Check NO token
    no_book = fetch_order_book(client, no_token)
    if not has_two_sided_book(no_book):
        no_bids = len(no_book.bids) if no_book and no_book.bids else 0
        no_asks = len(no_book.asks) if no_book and no_book.asks else 0
        return False, f"NO token one-sided (bids={no_bids}, asks={no_asks})"

    # Check NO spread if max_spread is specified
    if max_spread is not None:
        no_best_bid, no_best_ask = get_best_bid_ask(no_book)
        if no_best_bid is not None and no_best_ask is not None:
            no_spread = no_best_ask - no_best_bid
            if no_spread > max_spread:
                return False, f"NO spread too wide ({no_best_bid} - {no_best_ask})"

    return True, ""