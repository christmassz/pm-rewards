"""
Database utilities for Polymarket Liquidity Rewards Auto-MM.

Provides SQLite initialization and helper functions for restart-safe operation.
"""

import sqlite3
import os
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def ensure_data_dir() -> None:
    """Ensure data directory exists."""
    os.makedirs('data', exist_ok=True)


def init_database() -> str:
    """
    Initialize SQLite database with required tables.

    Creates the database file and all required tables if they don't exist.
    Safe to call multiple times - uses CREATE TABLE IF NOT EXISTS.

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
    """
    Get value from runtime_state table.

    Args:
        db_path: Path to SQLite database
        key: State key to retrieve

    Returns:
        State value or None if key doesn't exist
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute('SELECT value FROM runtime_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_state(db_path: str, key: str, value: str) -> None:
    """
    Set value in runtime_state table.

    Args:
        db_path: Path to SQLite database
        key: State key to set
        value: State value to set
    """
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
    """
    Get list of active markets from database.

    Args:
        db_path: Path to SQLite database

    Returns:
        List of active market records with condition_id, slug, entered_at, score_at_entry
    """
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
    """
    Insert or update active market record.

    Args:
        db_path: Path to SQLite database
        condition_id: Market condition ID (primary key)
        slug: Market slug
        entered_at: Unix timestamp when market was entered
        score_at_entry: Market score when it was selected
    """
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
    """
    Remove market from active_markets table.

    Args:
        db_path: Path to SQLite database
        condition_id: Market condition ID to remove
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('DELETE FROM active_markets WHERE condition_id = ?', (condition_id,))
        conn.commit()
    finally:
        conn.close()


def get_open_orders(db_path: str, condition_id: str = None) -> List[Dict[str, Any]]:
    """
    Get open orders, optionally filtered by condition_id.

    Args:
        db_path: Path to SQLite database
        condition_id: Optional condition ID to filter by

    Returns:
        List of open order records
    """
    conn = sqlite3.connect(db_path)
    try:
        if condition_id:
            cursor = conn.execute('''
                SELECT order_id, condition_id, token_id, side, price, size, status, created_at, updated_at
                FROM open_orders
                WHERE condition_id = ? AND status IN ('OPEN', 'PARTIAL')
            ''', (condition_id,))
        else:
            cursor = conn.execute('''
                SELECT order_id, condition_id, token_id, side, price, size, status, created_at, updated_at
                FROM open_orders
                WHERE status IN ('OPEN', 'PARTIAL')
            ''')

        rows = cursor.fetchall()
        return [
            {
                'order_id': row[0],
                'condition_id': row[1],
                'token_id': row[2],
                'side': row[3],
                'price': row[4],
                'size': row[5],
                'status': row[6],
                'created_at': row[7],
                'updated_at': row[8]
            }
            for row in rows
        ]
    finally:
        conn.close()


def upsert_open_order(
    db_path: str,
    order_id: str,
    condition_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    status: str,
    created_at: float,
    updated_at: float
) -> None:
    """
    Insert or update open order record.

    Args:
        db_path: Path to SQLite database
        order_id: Order ID (primary key)
        condition_id: Market condition ID
        token_id: Token ID for the order
        side: Order side (BUY/SELL)
        price: Order price
        size: Order size
        status: Order status (OPEN/CANCELED/FILLED/PARTIAL)
        created_at: Unix timestamp when order was created
        updated_at: Unix timestamp when order was last updated
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('''
            INSERT OR REPLACE INTO open_orders
            (order_id, condition_id, token_id, side, price, size, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order_id, condition_id, token_id, side, price, size, status, created_at, updated_at))
        conn.commit()
    finally:
        conn.close()


def validate_database_schema(db_path: str) -> bool:
    """
    Validate that database contains all required tables with correct schemas.

    Args:
        db_path: Path to SQLite database

    Returns:
        True if schema is valid, False otherwise
    """
    if not os.path.exists(db_path):
        return False

    conn = sqlite3.connect(db_path)
    try:
        # Check that all required tables exist
        cursor = conn.execute('''
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('runtime_state', 'active_markets', 'open_orders')
        ''')
        tables = {row[0] for row in cursor.fetchall()}
        required_tables = {'runtime_state', 'active_markets', 'open_orders'}

        if not required_tables.issubset(tables):
            missing = required_tables - tables
            logger.error(f"Database schema validation failed: missing tables {missing}")
            return False

        # Additional schema checks could go here
        # For now, just check table existence

        return True

    except sqlite3.Error as e:
        logger.error(f"Database schema validation error: {e}")
        return False
    finally:
        conn.close()