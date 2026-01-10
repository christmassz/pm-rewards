"""
Gamma API client for fetching Polymarket markets and reward parameters.

Implements pagination, retries, and field extraction for market discovery.
"""

import json
import time
import requests
from typing import Iterator, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def parse_json_maybe(value: Any) -> Any:
    """Parse a value that might be JSON-encoded string or already parsed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def normalize_rewards_max_spread(value: Any) -> float:
    """
    Normalize rewardsMaxSpread to price units.
    If value > 1, interpret as cents and convert to price units via x/100.
    """
    if value is None:
        return 0.0

    float_val = float(value)
    if float_val > 1:
        return float_val / 100.0
    return float_val


def extract_market_fields(market_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract and normalize required fields from a raw Gamma market record.

    Returns a MarketRecord-compatible dict with all required fields.
    """
    # Parse outcomes and clobTokenIds (may be JSON strings)
    outcomes = parse_json_maybe(market_raw.get('outcomes', []))
    clob_token_ids = parse_json_maybe(market_raw.get('clobTokenIds', []))

    # Handle common field name variations and potential JSON encoding
    def get_field(key: str, default=None):
        value = market_raw.get(key, default)
        return parse_json_maybe(value)

    return {
        'id': market_raw.get('id'),
        'slug': market_raw.get('slug'),
        'conditionId': market_raw.get('conditionId'),
        'active': get_field('active', False),
        'closed': get_field('closed', False),
        'acceptingOrders': get_field('acceptingOrders', False),
        'enableOrderBook': get_field('enableOrderBook', False),
        'restricted': get_field('restricted', False),
        'rewardsMinSize': get_field('rewardsMinSize', 0),
        'rewardsMaxSpread': normalize_rewards_max_spread(get_field('rewardsMaxSpread', 0)),
        'outcomes': outcomes if isinstance(outcomes, list) else [],
        'clobTokenIds': clob_token_ids if isinstance(clob_token_ids, list) else [],
        'competitive': get_field('competitive', 0.0),
        'oneHourPriceChange': get_field('oneHourPriceChange', 0.0),
        'volume24hrClob': get_field('volume24hrClob', 0.0),
        'liquidityClob': get_field('liquidityClob', 0.0),
        'endDate': get_field('endDate'),
        'orderPriceMinTickSize': get_field('orderPriceMinTickSize'),
        'orderMinSize': get_field('orderMinSize'),
        'spread': get_field('spread', 0.0),
        'bestBid': get_field('bestBid'),
        'bestAsk': get_field('bestAsk'),
    }


def iter_markets(
    limit: int = 100,
    closed: bool = False,
    max_retries: int = 5,
    timeout_sec: int = 20,
    backoff_base_sec: float = 0.5,
    backoff_max_sec: float = 10.0
) -> Iterator[Dict[str, Any]]:
    """
    Paginate through Gamma markets API with retries and backoff.

    Args:
        limit: Number of markets to fetch per page
        closed: Whether to include closed markets
        max_retries: Maximum retry attempts for failed requests
        timeout_sec: Request timeout in seconds
        backoff_base_sec: Base backoff time for retries
        backoff_max_sec: Maximum backoff time

    Yields:
        Extracted market records (dicts) one by one
    """
    offset = 0
    session = requests.Session()

    while True:
        params = {
            'limit': limit,
            'offset': offset,
            'closed': 'true' if closed else 'false'
        }

        retry_count = 0
        while retry_count <= max_retries:
            try:
                logger.debug(f"Fetching markets: offset={offset}, limit={limit}")
                response = session.get(
                    f"{GAMMA_BASE_URL}/markets",
                    params=params,
                    timeout=timeout_sec
                )
                response.raise_for_status()

                data = response.json()
                markets = data if isinstance(data, list) else []

                if not markets:
                    logger.debug("Empty batch received, pagination complete")
                    return

                # Extract fields and yield each market
                for market_raw in markets:
                    try:
                        market_extracted = extract_market_fields(market_raw)
                        yield market_extracted
                    except Exception as e:
                        logger.warning(f"Failed to extract market {market_raw.get('id', 'unknown')}: {e}")
                        continue

                # Move to next page
                offset += len(markets)
                break  # Success, exit retry loop

            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"Failed to fetch markets after {max_retries} retries: {e}")
                    raise

                # Exponential backoff with jitter
                backoff_time = min(backoff_base_sec * (2 ** retry_count), backoff_max_sec)
                logger.warning(f"Request failed, retrying in {backoff_time:.1f}s (attempt {retry_count}/{max_retries}): {e}")
                time.sleep(backoff_time)

            except Exception as e:
                logger.error(f"Unexpected error fetching markets: {e}")
                raise