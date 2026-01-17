"""
Configuration loader for Polymarket Liquidity Rewards Auto-MM.

Loads and validates config.yaml with typed config object and redacted printing.
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class QuoteConfig:
    """Quote-related configuration parameters."""
    size_buffer: float
    half_spread_frac: float
    update_min_ticks: int


@dataclass
class NetConfig:
    """Network and retry configuration parameters."""
    request_timeout_sec: int
    max_retries: int
    backoff_base_sec: float
    backoff_max_sec: float


@dataclass
class LiveConfig:
    """Live mode safety configuration parameters."""
    enabled_by_flag_only: bool
    max_markets_live: int
    cancel_on_exit: bool


@dataclass
class Config:
    """Complete configuration object with all parameters."""
    # Capital allocation
    total_cap_usdc: float
    usable_cap_frac: float
    num_markets: int

    # Market filtering
    exclude_restricted: bool
    end_date_buffer_days: int
    min_volume24h: float
    max_book_spread: float

    # Timing parameters
    selector_interval_sec: int
    poll_interval_sec: int
    rotation_cooldown_sec: int
    min_tenure_sec: int
    score_replace_multiplier: float
    loop_interval_sec: int

    # Nested configs
    quote: QuoteConfig
    net: NetConfig
    live: LiveConfig


def load_config(config_path: str = "config.yaml") -> Config:
    """
    Load and validate configuration from YAML file.

    Args:
        config_path: Path to config.yaml file

    Returns:
        Validated Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config validation fails
        yaml.YAMLError: If YAML parsing fails
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Failed to parse YAML config: {e}")

    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML dictionary")

    # Validate and extract required top-level keys
    required_keys = [
        'total_cap_usdc', 'usable_cap_frac', 'num_markets',
        'exclude_restricted', 'end_date_buffer_days', 'min_volume24h',
        'selector_interval_sec', 'poll_interval_sec', 'rotation_cooldown_sec',
        'min_tenure_sec', 'score_replace_multiplier', 'loop_interval_sec',
        'quote', 'net', 'live'
    ]

    missing_keys = [key for key in required_keys if key not in data]
    if missing_keys:
        raise ValueError(f"Missing required config keys: {missing_keys}")

    # Validate nested sections
    quote_data = data.get('quote', {})
    required_quote_keys = ['size_buffer', 'half_spread_frac', 'update_min_ticks']
    missing_quote = [key for key in required_quote_keys if key not in quote_data]
    if missing_quote:
        raise ValueError(f"Missing required quote config keys: {missing_quote}")

    net_data = data.get('net', {})
    required_net_keys = ['request_timeout_sec', 'max_retries', 'backoff_base_sec', 'backoff_max_sec']
    missing_net = [key for key in required_net_keys if key not in net_data]
    if missing_net:
        raise ValueError(f"Missing required net config keys: {missing_net}")

    live_data = data.get('live', {})
    required_live_keys = ['enabled_by_flag_only', 'max_markets_live', 'cancel_on_exit']
    missing_live = [key for key in required_live_keys if key not in live_data]
    if missing_live:
        raise ValueError(f"Missing required live config keys: {missing_live}")

    # Validate specific constraints
    if data['total_cap_usdc'] <= 0:
        raise ValueError("total_cap_usdc must be positive")

    if not (0 < data['usable_cap_frac'] <= 1):
        raise ValueError("usable_cap_frac must be between 0 and 1")

    if data['num_markets'] <= 0:
        raise ValueError("num_markets must be positive")

    if data['end_date_buffer_days'] < 0:
        raise ValueError("end_date_buffer_days must be non-negative")

    if data['min_volume24h'] < 0:
        raise ValueError("min_volume24h must be non-negative")

    if data.get('max_book_spread', 0.8) <= 0:
        raise ValueError("max_book_spread must be positive")

    if quote_data['size_buffer'] <= 0:
        raise ValueError("quote.size_buffer must be positive")

    if not (0 < quote_data['half_spread_frac'] <= 1):
        raise ValueError("quote.half_spread_frac must be between 0 and 1")

    if quote_data['update_min_ticks'] <= 0:
        raise ValueError("quote.update_min_ticks must be positive")

    if not live_data['enabled_by_flag_only']:
        raise ValueError("live.enabled_by_flag_only must be true")

    if live_data['max_markets_live'] <= 0:
        raise ValueError("live.max_markets_live must be positive")

    # Build typed config object
    try:
        config = Config(
            # Capital allocation
            total_cap_usdc=float(data['total_cap_usdc']),
            usable_cap_frac=float(data['usable_cap_frac']),
            num_markets=int(data['num_markets']),

            # Market filtering
            exclude_restricted=bool(data['exclude_restricted']),
            end_date_buffer_days=int(data['end_date_buffer_days']),
            min_volume24h=float(data['min_volume24h']),
            max_book_spread=float(data.get('max_book_spread', 0.8)),

            # Timing parameters
            selector_interval_sec=int(data['selector_interval_sec']),
            poll_interval_sec=int(data['poll_interval_sec']),
            rotation_cooldown_sec=int(data['rotation_cooldown_sec']),
            min_tenure_sec=int(data['min_tenure_sec']),
            score_replace_multiplier=float(data['score_replace_multiplier']),
            loop_interval_sec=int(data['loop_interval_sec']),

            # Nested configs
            quote=QuoteConfig(
                size_buffer=float(quote_data['size_buffer']),
                half_spread_frac=float(quote_data['half_spread_frac']),
                update_min_ticks=int(quote_data['update_min_ticks'])
            ),
            net=NetConfig(
                request_timeout_sec=int(net_data['request_timeout_sec']),
                max_retries=int(net_data['max_retries']),
                backoff_base_sec=float(net_data['backoff_base_sec']),
                backoff_max_sec=float(net_data['backoff_max_sec'])
            ),
            live=LiveConfig(
                enabled_by_flag_only=bool(live_data['enabled_by_flag_only']),
                max_markets_live=int(live_data['max_markets_live']),
                cancel_on_exit=bool(live_data['cancel_on_exit'])
            )
        )

        logger.info(f"Configuration loaded successfully from {config_path}")
        return config

    except (ValueError, TypeError) as e:
        raise ValueError(f"Config value validation failed: {e}")


def load_config_or_default(config_path: str = "config.yaml") -> Config:
    """
    Load config with fallback to defaults if file doesn't exist.

    Args:
        config_path: Path to config.yaml file

    Returns:
        Loaded Config or default config if file doesn't exist
    """
    if os.path.exists(config_path):
        return load_config(config_path)
    else:
        logger.warning(f"Config file {config_path} not found, using defaults")
        return get_default_config()


def get_default_config() -> Config:
    """
    Get default configuration values.

    Returns:
        Config object with default values matching config.yaml.example
    """
    return Config(
        # Capital allocation
        total_cap_usdc=1000.0,
        usable_cap_frac=0.85,
        num_markets=3,

        # Market filtering
        exclude_restricted=True,
        end_date_buffer_days=7,
        min_volume24h=500.0,
        max_book_spread=0.8,

        # Timing parameters
        selector_interval_sec=900,   # 15 minutes
        poll_interval_sec=5,
        rotation_cooldown_sec=43200,  # 12 hours
        min_tenure_sec=21600,         # 6 hours
        score_replace_multiplier=1.25,
        loop_interval_sec=600,  # 10 minutes

        # Nested configs
        quote=QuoteConfig(
            size_buffer=1.1,
            half_spread_frac=0.85,
            update_min_ticks=2
        ),
        net=NetConfig(
            request_timeout_sec=20,
            max_retries=5,
            backoff_base_sec=0.5,
            backoff_max_sec=10.0
        ),
        live=LiveConfig(
            enabled_by_flag_only=True,
            max_markets_live=1,
            cancel_on_exit=True
        )
    )


def format_config_for_display(config: Config, redact_secrets: bool = True) -> str:
    """
    Format config for display with optional secret redaction.

    Args:
        config: Config object to display
        redact_secrets: Whether to redact sensitive values

    Returns:
        Formatted config string
    """
    lines = ["Configuration:"]
    lines.append("")

    # Capital allocation
    lines.append("Capital allocation:")
    lines.append(f"  total_cap_usdc: {config.total_cap_usdc}")
    lines.append(f"  usable_cap_frac: {config.usable_cap_frac}")
    lines.append(f"  num_markets: {config.num_markets}")
    lines.append("")

    # Market filtering
    lines.append("Market filtering:")
    lines.append(f"  exclude_restricted: {config.exclude_restricted}")
    lines.append(f"  end_date_buffer_days: {config.end_date_buffer_days}")
    lines.append(f"  min_volume24h: {config.min_volume24h}")
    lines.append(f"  max_book_spread: {config.max_book_spread}")
    lines.append("")

    # Timing parameters
    lines.append("Timing parameters:")
    lines.append(f"  selector_interval_sec: {config.selector_interval_sec}")
    lines.append(f"  poll_interval_sec: {config.poll_interval_sec}")
    lines.append(f"  rotation_cooldown_sec: {config.rotation_cooldown_sec}")
    lines.append(f"  min_tenure_sec: {config.min_tenure_sec}")
    lines.append(f"  score_replace_multiplier: {config.score_replace_multiplier}")
    lines.append("")

    # Quote parameters
    lines.append("Quote parameters:")
    lines.append(f"  size_buffer: {config.quote.size_buffer}")
    lines.append(f"  half_spread_frac: {config.quote.half_spread_frac}")
    lines.append(f"  update_min_ticks: {config.quote.update_min_ticks}")
    lines.append("")

    # Network parameters
    lines.append("Network parameters:")
    lines.append(f"  request_timeout_sec: {config.net.request_timeout_sec}")
    lines.append(f"  max_retries: {config.net.max_retries}")
    lines.append(f"  backoff_base_sec: {config.net.backoff_base_sec}")
    lines.append(f"  backoff_max_sec: {config.net.backoff_max_sec}")
    lines.append("")

    # Live mode parameters
    lines.append("Live mode parameters:")
    lines.append(f"  enabled_by_flag_only: {config.live.enabled_by_flag_only}")
    lines.append(f"  max_markets_live: {config.live.max_markets_live}")
    lines.append(f"  cancel_on_exit: {config.live.cancel_on_exit}")

    # Note about secrets (even though we don't have any in this config)
    if redact_secrets:
        lines.append("")
        lines.append("(secrets redacted)")

    return "\n".join(lines)