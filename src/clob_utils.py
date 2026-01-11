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