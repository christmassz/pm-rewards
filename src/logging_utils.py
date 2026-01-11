"""
Logging utilities for Polymarket Liquidity Rewards Auto-MM.

Provides append-only JSONL logging functionality ensuring consistent contract.
"""

import json
import os
import logging
from typing import Dict, Any, Union
import threading

logger = logging.getLogger(__name__)

# Thread lock to ensure atomic writes to log files
_write_lock = threading.Lock()


def ensure_logs_dir() -> None:
    """Ensure logs directory exists."""
    os.makedirs('logs', exist_ok=True)


def append_jsonl(filepath: str, obj: Dict[str, Any]) -> None:
    """
    Append exactly one JSON object per line to a JSONL file.

    This is the single source of truth for all JSONL logging in the application.
    All selector and maker logs must use this function.

    Args:
        filepath: Path to JSONL file (relative to current directory)
        obj: Dictionary object to append as JSON

    Raises:
        TypeError: If obj is not serializable to JSON
        OSError: If file write fails

    Example:
        append_jsonl('logs/selector.jsonl', {
            'ts': time.time(),
            'kind': 'gamma_smoke',
            'status': 'success',
            'markets_fetched': 5
        })
    """
    if not isinstance(obj, dict):
        raise TypeError(f"Object must be a dictionary, got {type(obj)}")

    # Ensure logs directory exists
    ensure_logs_dir()

    # Serialize to JSON (this will raise TypeError if not serializable)
    try:
        json_line = json.dumps(obj, separators=(',', ':'), sort_keys=True)
    except TypeError as e:
        raise TypeError(f"Object is not JSON serializable: {e}")

    # Thread-safe file append
    with _write_lock:
        try:
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(json_line + '\n')
            logger.debug(f"Appended JSONL entry to {filepath}: {obj.get('kind', 'unknown')}")
        except OSError as e:
            logger.error(f"Failed to write to {filepath}: {e}")
            raise


def read_jsonl(filepath: str) -> list[Dict[str, Any]]:
    """
    Read all JSON objects from a JSONL file.

    Args:
        filepath: Path to JSONL file

    Returns:
        List of dictionaries, one per line

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If any line is invalid JSON
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"JSONL file not found: {filepath}")

    objects = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:  # Skip empty lines
                    try:
                        obj = json.loads(line)
                        objects.append(obj)
                    except json.JSONDecodeError as e:
                        raise json.JSONDecodeError(f"Invalid JSON on line {line_num}: {e}")
    except OSError as e:
        raise OSError(f"Failed to read {filepath}: {e}")

    return objects


def count_jsonl_lines(filepath: str) -> int:
    """
    Count non-empty lines in a JSONL file.

    Args:
        filepath: Path to JSONL file

    Returns:
        Number of non-empty lines (i.e., JSON objects)
    """
    if not os.path.exists(filepath):
        return 0

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def get_latest_jsonl_entry(filepath: str) -> Dict[str, Any] | None:
    """
    Get the most recent JSON object from a JSONL file.

    Args:
        filepath: Path to JSONL file

    Returns:
        Dictionary of the last JSON object, or None if file is empty/missing
    """
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
            if lines:
                return json.loads(lines[-1])
    except (OSError, json.JSONDecodeError):
        pass

    return None


def filter_jsonl_by_kind(filepath: str, kind: str) -> list[Dict[str, Any]]:
    """
    Filter JSONL entries by 'kind' field.

    Args:
        filepath: Path to JSONL file
        kind: Kind value to filter by

    Returns:
        List of dictionaries matching the specified kind
    """
    try:
        objects = read_jsonl(filepath)
        return [obj for obj in objects if obj.get('kind') == kind]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def validate_jsonl_format(filepath: str) -> tuple[bool, str]:
    """
    Validate that a file follows proper JSONL format.

    Args:
        filepath: Path to JSONL file

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not os.path.exists(filepath):
        return True, "File does not exist (valid empty state)"

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:  # Skip empty lines
                    try:
                        obj = json.loads(line)
                        if not isinstance(obj, dict):
                            return False, f"Line {line_num}: JSON object is not a dictionary"
                    except json.JSONDecodeError as e:
                        return False, f"Line {line_num}: Invalid JSON - {e}"
    except OSError as e:
        return False, f"Failed to read file: {e}"

    return True, "Valid JSONL format"