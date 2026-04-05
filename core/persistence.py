"""
G4H-RMA Quant Engine V7.0 -- SQLite Persistence Module
======================================================
Production-grade SQLite persistence layer for trades, positions, PnL history,
and daily statistics. Thread-safe with connection pooling, automatic table
creation, and circuit-breaker tracking.

Tables:
    trades         -- immutable ledger of every executed trade
    positions      -- open / closed position lifecycle
    daily_stats    -- per-day aggregate PnL, wins, losses, circuit-breaker flags
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_TRADES_DDL = """\
CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pair              TEXT    NOT NULL,
    action            TEXT    NOT NULL,
    entry_z           REAL,
    entry_spread      REAL,
    entry_price_base  REAL,
    entry_price_quote REAL,
    qty_base          REAL,
    qty_quote         REAL,
    entry_time        TEXT,
    exit_time         TEXT,
    exit_z            REAL,
    exit_spread       REAL,
    pnl               REAL    DEFAULT 0.0,
    status            TEXT    NOT NULL DEFAULT 'OPEN',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_POSITIONS_DDL = """\
CREATE TABLE IF NOT EXISTS positions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pair              TEXT    NOT NULL,
    action            TEXT    NOT NULL,
    entry_z           REAL,
    entry_spread      REAL,
    entry_time        TEXT    NOT NULL DEFAULT (datetime('now')),
    quantity          REAL    NOT NULL,
    stop_loss_z       REAL,
    take_profit_z     REAL,
    current_pnl       REAL    DEFAULT 0.0,
    max_pnl           REAL    DEFAULT 0.0,
    min_pnl           REAL    DEFAULT 0.0,
    exit_z            REAL,
    exit_spread       REAL,
    status            TEXT    NOT NULL DEFAULT 'OPEN',
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_DAILY_STATS_DDL = """\
CREATE TABLE IF NOT EXISTS daily_stats (
    date                        TEXT    PRIMARY KEY,
    total_trades                INTEGER NOT NULL DEFAULT 0,
    total_pnl                   REAL    NOT NULL DEFAULT 0.0,
    max_drawdown                REAL    NOT NULL DEFAULT 0.0,
    wins                        INTEGER NOT NULL DEFAULT 0,
    losses                      INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_triggered   INTEGER NOT NULL DEFAULT 0
);
"""

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);",
    "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);",
    "CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);",
    "CREATE INDEX IF NOT EXISTS idx_positions_pair ON positions(pair);",
    "CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);",
]


# ---------------------------------------------------------------------------
# PersistenceManager
# ---------------------------------------------------------------------------

class PersistenceManager:
    """Singleton SQLite persistence manager.

    Usage::

        pm = PersistenceManager.get_instance()
        trade_id = pm.record_trade({...})
        pm.update_position(pos_id, pnl=12.5, current_z=1.8)

    Thread safety
    -------------
    Every public method acquires a ``threading.Lock`` so that concurrent
    callers from the API layer or agents cannot corrupt the database.
    A single writer-lock means serialisation of writes, which is the
    correct trade-off for SQLite (a file database that does not support
    true concurrent writes).
    """

    _instance: Optional["PersistenceManager"] = None
    _lock = threading.Lock()  # class-level lock for singleton creation

    def __init__(self, db_path: str = "data/engine.db") -> None:
        """Initialise the manager and ensure tables exist.

        Parameters
        ----------
        db_path:
            Path to the SQLite database file.  Relative paths are resolved
            against the project root (the directory containing this module).
        """
        if PersistenceManager._instance is not None:
            raise RuntimeError(
                "PersistenceManager is a singleton. Use get_instance() instead."
            )

        # Resolve relative paths against the project root
        if not os.path.isabs(db_path):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, db_path)

        self._db_path = db_path
        self._local = threading.local()  # per-thread connection cache
        self._write_lock = threading.Lock()  # serialises write operations

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._ensure_tables()
        logger.info("PersistenceManager initialised: db=%s", self._db_path)

    # -- Singleton access ---------------------------------------------------

    @classmethod
    def get_instance(cls, db_path: str = "data/engine.db") -> "PersistenceManager":
        """Return the singleton instance (create if necessary)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (primarily for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close_all()
                cls._instance = None

    # -- Connection management -----------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating one if needed."""
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.row_factory = sqlite3.Row  # dict-like access
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
            self._local.in_transaction = False
        return conn

    @contextmanager
    def _conn(self):
        """Context manager that yields a connection and commits/rolls back."""
        conn = self._get_connection()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def _write(self):
        """Context manager for write operations (serialised)."""
        with self._write_lock:
            with self._conn() as conn:
                yield conn
                conn.commit()

    def close_all(self) -> None:
        """Close all thread-local connections."""
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.connection = None

    # -- Table creation -----------------------------------------------------

    def _ensure_tables(self) -> None:
        """Create tables and indexes if they do not already exist."""
        conn = self._get_connection()
        try:
            conn.executescript(_TRADES_DDL)
            conn.executescript(_POSITIONS_DDL)
            conn.executescript(_DAILY_STATS_DDL)
            for idx in _INDEX_DDL:
                conn.execute(idx)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # -- Public API: Trades -------------------------------------------------

    def record_trade(self, trade_data: dict) -> int:
        """Insert a new trade record and return its id.

        Parameters
        ----------
        trade_data:
            Dictionary with keys matching the *trades* table columns
            (excluding ``id`` and ``created_at``).

        Returns
        -------
        int
            The auto-generated primary key of the new row.
        """
        allowed = {
            "pair", "action", "entry_z", "entry_spread",
            "entry_price_base", "entry_price_quote",
            "qty_base", "qty_quote", "entry_time", "exit_time",
            "exit_z", "exit_spread", "pnl", "status",
        }
        keys = [k for k in allowed if k in trade_data]
        placeholders = ", ".join(f":{k}" for k in keys)
        columns = ", ".join(keys)
        sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"

        with self._write() as conn:
            cursor = conn.execute(sql, {k: trade_data[k] for k in keys})
            trade_id = cursor.lastrowid
            logger.info("Trade recorded: id=%s pair=%s action=%s",
                        trade_id, trade_data.get("pair"), trade_data.get("action"))
            return trade_id

    def update_trade(self, trade_id: int, updates: dict) -> None:
        """Update mutable fields on an existing trade record."""
        allowed = {
            "exit_time", "exit_z", "exit_spread", "pnl", "status",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            logger.warning("update_trade called with no valid fields for id=%s", trade_id)
            return
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        sql = f"UPDATE trades SET {set_clause} WHERE id = :id"
        params = dict(fields, id=trade_id)

        with self._write() as conn:
            conn.execute(sql, params)

    # -- Public API: Positions ----------------------------------------------

    def open_position(self, position_data: dict) -> int:
        """Create a new OPEN position and return its id."""
        keys = [
            "pair", "action", "entry_z", "entry_spread", "entry_time",
            "quantity", "stop_loss_z", "take_profit_z",
            "current_pnl", "max_pnl", "min_pnl", "status",
        ]
        keys = [k for k in keys if k in position_data]
        placeholders = ", ".join(f":{k}" for k in keys)
        columns = ", ".join(keys)
        sql = f"INSERT INTO positions ({columns}) VALUES ({placeholders})"

        with self._write() as conn:
            cursor = conn.execute(sql, {k: position_data[k] for k in keys})
            pos_id = cursor.lastrowid
            logger.info("Position opened: id=%s pair=%s", pos_id, position_data.get("pair"))
            return pos_id

    def update_position(self, position_id: int, pnl: float, current_z: float) -> None:
        """Update the PnL watermark on an open position."""
        sql = """\
            UPDATE positions
            SET current_pnl   = :pnl,
                max_pnl       = MAX(max_pnl, :pnl),
                min_pnl       = MIN(min_pnl, :pnl),
                updated_at    = datetime('now')
            WHERE id = :id AND status = 'OPEN';
        """
        with self._write() as conn:
            conn.execute(sql, {"id": position_id, "pnl": pnl})

    def close_position(
        self,
        position_id: int,
        exit_z: float,
        exit_spread: float,
        pnl: float,
    ) -> None:
        """Mark a position as CLOSED and record exit details."""
        sql = """\
            UPDATE positions
            SET current_pnl   = :pnl,
                max_pnl       = MAX(max_pnl, :pnl),
                min_pnl       = MIN(min_pnl, :pnl),
                exit_z        = :exit_z,
                exit_spread   = :exit_spread,
                status        = 'CLOSED',
                updated_at    = datetime('now')
            WHERE id = :id AND status = 'OPEN';
        """
        with self._write() as conn:
            cursor = conn.execute(sql, {
                "id": position_id,
                "exit_z": exit_z,
                "exit_spread": exit_spread,
                "pnl": pnl,
            })
            if cursor.rowcount == 0:
                logger.warning(
                    "close_position: no OPEN position found with id=%s", position_id,
                )

    # -- Public API: Queries ------------------------------------------------

    def get_trade_history(self, limit: int = 100) -> list[dict]:
        """Return the most recent trades, newest first."""
        sql = "SELECT * FROM trades ORDER BY id DESC LIMIT :limit"
        with self._conn() as conn:
            rows = conn.execute(sql, {"limit": limit}).fetchall()
            return [dict(row) for row in rows]

    def get_open_positions(self) -> list[dict]:
        """Return all currently open positions."""
        sql = "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]

    def get_position(self, position_id: int) -> Optional[dict]:
        """Return a single position by id, or None."""
        sql = "SELECT * FROM positions WHERE id = :id"
        with self._conn() as conn:
            row = conn.execute(sql, {"id": position_id}).fetchone()
            return dict(row) if row else None

    # -- Public API: Daily Stats --------------------------------------------

    def get_daily_stats(self, date_str: str) -> dict:
        """Return stats for a given date (YYYY-MM-DD).

        If no row exists, returns a zeroed-out default dict.
        """
        sql = "SELECT * FROM daily_stats WHERE date = :date"
        with self._conn() as conn:
            row = conn.execute(sql, {"date": date_str}).fetchone()
            if row:
                return dict(row)
        return {
            "date": date_str,
            "total_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "wins": 0,
            "losses": 0,
            "circuit_breaker_triggered": 0,
        }

    def update_daily_stats(self, date_str: str, pnl: float, is_win: bool) -> None:
        """Incrementally update the daily stats row.

        Inserts a new row for the date if it does not exist.
        Updates ``total_trades``, ``total_pnl``, ``wins``/``losses``,
        and recalculates ``max_drawdown`` (absolute value of the worst
        cumulative drawdown during the day).
        """
        upsert_sql = """\
            INSERT INTO daily_stats (date, total_trades, total_pnl, max_drawdown, wins, losses, circuit_breaker_triggered)
            VALUES (:date, 1, :pnl, CASE WHEN :pnl < 0 THEN ABS(:pnl) ELSE 0 END, :win, :loss, 0)
            ON CONFLICT(date) DO UPDATE SET
                total_trades  = total_trades + 1,
                total_pnl     = total_pnl + :pnl,
                wins          = wins + :win,
                losses        = losses + :loss,
                max_drawdown  = MAX(max_drawdown, CASE WHEN (total_pnl + :pnl) < 0 THEN ABS(total_pnl + :pnl) ELSE max_drawdown END)
        """
        win = 1 if is_win else 0
        loss = 1 - win

        with self._write() as conn:
            conn.execute(upsert_sql, {
                "date": date_str,
                "pnl": pnl,
                "win": win,
                "loss": loss,
            })

    def trigger_circuit_breaker(self, date_str: str) -> None:
        """Flag the circuit breaker for the given date."""
        sql = """\
            UPDATE daily_stats
            SET circuit_breaker_triggered = 1
            WHERE date = :date
        """
        with self._write() as conn:
            conn.execute(sql, {"date": date_str})

    def check_circuit_breaker(self) -> bool:
        """Return True if the circuit breaker is currently active (today)."""
        today = date.today().isoformat()
        stats = self.get_daily_stats(today)
        triggered = stats.get("circuit_breaker_triggered", 0)
        if triggered:
            logger.warning("Circuit breaker is TRIGGERED for %s", today)
        return bool(triggered)

    def reset_daily_counters(self) -> None:
        """Reset today's daily stats to zero (called at session start or midnight)."""
        today = date.today().isoformat()
        sql = """\
            INSERT INTO daily_stats (date, total_trades, total_pnl, max_drawdown, wins, losses, circuit_breaker_triggered)
            VALUES (:date, 0, 0.0, 0.0, 0, 0, 0)
            ON CONFLICT(date) DO UPDATE SET
                total_trades                = 0,
                total_pnl                   = 0.0,
                max_drawdown                = 0.0,
                wins                        = 0,
                losses                      = 0,
                circuit_breaker_triggered   = 0
        """
        with self._write() as conn:
            conn.execute(sql, {"date": today})
            logger.info("Daily counters reset for %s", today)

    # -- Public API: PnL Summary --------------------------------------------

    def get_pnl_summary(self) -> dict:
        """Compute aggregate PnL statistics across all trades.

        Returns a dict with keys:
            total_pnl, total_trades, wins, losses, win_rate,
            avg_pnl, max_pnl, min_pnl, avg_win, avg_loss,
            max_drawdown (approximate), last_7d_pnl
        """
        sql = """\
            SELECT
                COUNT(*)                          AS total_trades,
                COALESCE(SUM(pnl), 0.0)           AS total_pnl,
                COALESCE(AVG(pnl), 0.0)           AS avg_pnl,
                COALESCE(MAX(pnl), 0.0)           AS max_pnl,
                COALESCE(MIN(pnl), 0.0)           AS min_pnl,
                COUNT(CASE WHEN pnl > 0 THEN 1 END) AS wins,
                COUNT(CASE WHEN pnl <= 0 THEN 1 END) AS losses
            FROM trades
            WHERE status = 'CLOSED' AND pnl IS NOT NULL
        """
        with self._conn() as conn:
            row = conn.execute(sql).fetchone()
            if row is None:
                row = {"total_trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0,
                       "max_pnl": 0.0, "min_pnl": 0.0, "wins": 0, "losses": 0}

        stats = dict(row)
        total = stats["total_trades"]
        stats["win_rate"] = (stats["wins"] / total * 100.0) if total > 0 else 0.0
        stats["avg_win"] = (stats["total_pnl"] / stats["wins"]) if stats["wins"] > 0 else 0.0
        stats["avg_loss"] = (stats["total_pnl"] / stats["losses"]) if stats["losses"] > 0 else 0.0

        # Last 7 days PnL
        seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
        sql_7d = """\
            SELECT COALESCE(SUM(total_pnl), 0.0) AS pnl_7d
            FROM daily_stats
            WHERE date >= :cutoff
        """
        with self._conn() as conn:
            row_7d = conn.execute(sql_7d, {"cutoff": seven_days_ago}).fetchone()
            stats["last_7d_pnl"] = row_7d["pnl_7d"] if row_7d else 0.0

        # Approximate max drawdown from daily_stats
        sql_dd = "SELECT MIN(total_pnl) AS worst_cum FROM daily_stats"
        with self._conn() as conn:
            row_dd = conn.execute(sql_dd).fetchone()
            stats["max_drawdown"] = abs(row_dd["worst_cum"]) if row_dd and row_dd["worst_cum"] < 0 else 0.0

        return stats

    # -- Bulk / maintenance -------------------------------------------------

    def bulk_insert_trades(self, trades: list[dict]) -> list[int]:
        """Insert multiple trades in a single transaction. Returns list of ids."""
        ids: list[int] = []
        allowed = {
            "pair", "action", "entry_z", "entry_spread",
            "entry_price_base", "entry_price_quote",
            "qty_base", "qty_quote", "entry_time", "exit_time",
            "exit_z", "exit_spread", "pnl", "status",
        }
        with self._write_lock:
            with self._conn() as conn:
                for trade in trades:
                    keys = [k for k in allowed if k in trade]
                    placeholders = ", ".join(f":{k}" for k in keys)
                    columns = ", ".join(keys)
                    sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
                    cursor = conn.execute(sql, {k: trade[k] for k in keys})
                    ids.append(cursor.lastrowid)
                conn.commit()
        logger.info("Bulk inserted %d trades", len(ids))
        return ids

    def cleanup_old_data(self, days: int = 90) -> int:
        """Remove trades and daily_stats older than *days*.

        Returns the total number of rows deleted.
        """
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        deleted = 0
        with self._write_lock:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM trades WHERE entry_time < :cutoff", {"cutoff": cutoff})
                deleted += cur.rowcount
                cur = conn.execute("DELETE FROM daily_stats WHERE date < :cutoff", {"cutoff": cutoff})
                deleted += cur.rowcount
                conn.commit()
        logger.info("Cleaned up %d rows older than %s", deleted, cutoff)
        return deleted

    def __repr__(self) -> str:
        return f"PersistenceManager(db='{self._db_path}')"
