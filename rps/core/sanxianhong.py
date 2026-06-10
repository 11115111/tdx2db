"""三线红榜单计算.

History bulk:   calc_sanxianhong_history()  — single SQL pass, gap-and-islands
Incremental:    calc_sanxianhong()          — derives from yesterday's sanxianhong_daily
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import duckdb


# ---------------------------------------------------------------------------
# History bulk — single SQL pass for all dates in range
# ---------------------------------------------------------------------------

def _build_history_sql(version: str, cfg: dict, start_date: str, end_date: str) -> str:
    c = cfg[version]
    return f"""
WITH trading_days AS (
    SELECT DISTINCT trade_date,
           ROW_NUMBER() OVER (ORDER BY trade_date) AS td_idx
    FROM rps_stock_daily
),
qualified AS (
    SELECT
        r.trade_date, r.symbol, r.name,
        r.rps50, r.rps120, r.rps250, r.h_div_hhv150,
        r.close_bfq, r.floatmv, r.change_pct, r.turnover,
        t.td_idx
    FROM rps_stock_daily r
    JOIN trading_days t ON t.trade_date = r.trade_date
    WHERE r.rps50  >= {c['rps50_min']}
      AND r.rps120 >= {c['rps120_min']}
      AND r.rps250 >= {c['rps250_min']}
      AND r.h_div_hhv150 >= {c['hhv_ratio_min']}
),
-- Gap-and-islands: consecutive td_idx rows within each symbol form one run.
-- td_idx - ROW_NUMBER() is constant within a consecutive run.
grouped AS (
    SELECT *,
           td_idx - ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY td_idx) AS run_id
    FROM qualified
),
with_streak AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY symbol, run_id ORDER BY td_idx)    AS consecutive_days,
           FIRST_VALUE(trade_date) OVER (
               PARTITION BY symbol, run_id ORDER BY td_idx
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )                                                                   AS join_date,
           -- mark first day of each run for enter_pool_count window
           CASE WHEN ROW_NUMBER() OVER (PARTITION BY symbol, run_id ORDER BY td_idx) = 1
                THEN 1 ELSE 0 END                                             AS is_run_start
    FROM grouped
),
-- Sliding 60-trading-day window using td_idx RANGE.
-- RANGE BETWEEN 59 PRECEDING AND CURRENT ROW = current + 59 earlier = 60 days.
with_window AS (
    SELECT
        s.*,
        CAST(COUNT(*) OVER (
            PARTITION BY s.symbol ORDER BY s.td_idx
            RANGE BETWEEN 59 PRECEDING AND CURRENT ROW
        ) AS INTEGER)                                                          AS total_days_60d,
        CAST(SUM(s.is_run_start) OVER (
            PARTITION BY s.symbol ORDER BY s.td_idx
            RANGE BETWEEN 59 PRECEDING AND CURRENT ROW
        ) AS INTEGER)                                                          AS enter_pool_count_60d
    FROM with_streak s
)
INSERT OR REPLACE INTO sanxianhong_daily
SELECT
    trade_date, symbol, name,
    rps50, rps120, rps250, h_div_hhv150,
    '{version}'         AS formula_version,
    join_date,
    CAST(consecutive_days    AS INTEGER),
    total_days_60d,
    enter_pool_count_60d,
    NULL                AS last_exit_date,
    close_bfq, floatmv, change_pct, turnover
FROM with_window
WHERE trade_date BETWEEN '{start_date}' AND '{end_date}'
"""


def calc_sanxianhong_history(
    con: duckdb.DuckDBPyConnection,
    start_date: str,
    end_date: str,
    cfg: dict[str, Any],
    versions: list[str] | None = None,
) -> int:
    """Single SQL pass for all dates in range. Use for --init-history."""
    if versions is None:
        versions = ["strict"]
    total = 0
    for version in versions:
        con.execute(_build_history_sql(version, cfg, start_date, end_date))
        row = con.execute(
            "SELECT COUNT(*) FROM sanxianhong_daily WHERE trade_date BETWEEN $1 AND $2",
            [start_date, end_date],
        ).fetchone()
        total += row[0] if row else 0
    return total


# ---------------------------------------------------------------------------
# Incremental daily — derives from yesterday's sanxianhong_daily
# ---------------------------------------------------------------------------

def _get_prev_trade_date(con: duckdb.DuckDBPyConnection, target_date: str) -> str | None:
    row = con.execute("""
        SELECT MAX(trade_date) FROM rps_stock_daily
        WHERE trade_date < $1
    """, [target_date]).fetchone()
    return str(row[0]) if row and row[0] else None


def _get_nth_prev_trade_date(con: duckdb.DuckDBPyConnection, target_date: str, n: int) -> str | None:
    """Return the trade date that is exactly n trading days before target_date."""
    row = con.execute("""
        SELECT trade_date FROM (
            SELECT trade_date,
                   ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
            FROM (SELECT DISTINCT trade_date FROM rps_stock_daily WHERE trade_date < $1)
        ) WHERE rn = $2
    """, [target_date, n]).fetchone()
    return str(row[0]) if row and row[0] else None


def calc_sanxianhong(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    cfg: dict[str, Any],
    versions: list[str] | None = None,
) -> int:
    """Incremental update for a single date.

    Reads yesterday's sanxianhong_daily for streak state instead of
    re-scanning 60 days of rps_stock_daily.
    """
    if versions is None:
        versions = ["strict"]

    prev_date = _get_prev_trade_date(con, target_date)
    # The row that "expires" out of the 60-day window today
    expire_date = _get_nth_prev_trade_date(con, target_date, 60)

    total = 0
    for version in versions:
        c = cfg[version]

        # Today's qualifying stocks
        today_df = con.execute(f"""
            SELECT trade_date, symbol, name,
                   rps50, rps120, rps250, h_div_hhv150,
                   close_bfq, floatmv, change_pct, turnover
            FROM rps_stock_daily
            WHERE trade_date = '{target_date}'
              AND rps50  >= {c['rps50_min']}
              AND rps120 >= {c['rps120_min']}
              AND rps250 >= {c['rps250_min']}
              AND h_div_hhv150 >= {c['hhv_ratio_min']}
        """).df()

        if today_df.empty:
            continue

        # Yesterday's sanxianhong rows for streak state
        if prev_date:
            prev_df = con.execute(f"""
                SELECT symbol, consecutive_days, total_days_60d,
                       enter_pool_count_60d, join_date
                FROM sanxianhong_daily
                WHERE trade_date = '{prev_date}' AND formula_version = '{version}'
            """).df().set_index("symbol")
        else:
            prev_df = pd.DataFrame(columns=["consecutive_days", "total_days_60d",
                                             "enter_pool_count_60d", "join_date"])
            prev_df.index.name = "symbol"

        # Stocks that expire from the 60-day window (were in pool on expire_date)
        if expire_date:
            expire_syms = set(con.execute(f"""
                SELECT symbol FROM sanxianhong_daily
                WHERE trade_date = '{expire_date}' AND formula_version = '{version}'
            """).df()["symbol"].tolist())
            # Stocks whose run started exactly on expire_date (their entry expires too)
            expire_run_start_syms = set(con.execute(f"""
                SELECT symbol FROM sanxianhong_daily
                WHERE trade_date = '{expire_date}'
                  AND formula_version = '{version}'
                  AND join_date = trade_date
            """).df()["symbol"].tolist())
        else:
            expire_syms = set()
            expire_run_start_syms = set()

        rows = []
        for _, row in today_df.iterrows():
            sym = row["symbol"]
            in_prev = sym in prev_df.index

            if in_prev:
                p = prev_df.loc[sym]
                consecutive_days    = int(p["consecutive_days"]) + 1
                join_date           = p["join_date"]
                total_days_60d      = int(p["total_days_60d"]) + 1 - (1 if sym in expire_syms else 0)
                enter_pool_count_60d = int(p["enter_pool_count_60d"]) - (1 if sym in expire_run_start_syms else 0)
            else:
                # New entry today
                consecutive_days    = 1
                join_date           = target_date
                prev_total          = int(prev_df.loc[sym, "total_days_60d"]) if sym in prev_df.index else 0
                total_days_60d      = prev_total + 1 - (1 if sym in expire_syms else 0)
                prev_enter          = int(prev_df.loc[sym, "enter_pool_count_60d"]) if sym in prev_df.index else 0
                enter_pool_count_60d = prev_enter + 1 - (1 if sym in expire_run_start_syms else 0)

            # Clamp to valid range
            total_days_60d       = max(total_days_60d, 1)
            enter_pool_count_60d = max(enter_pool_count_60d, 1)

            rows.append({
                "trade_date":          target_date,
                "symbol":              sym,
                "name":                row["name"],
                "rps50":               int(row["rps50"]),
                "rps120":              int(row["rps120"]),
                "rps250":              int(row["rps250"]),
                "h_div_hhv150":        float(row["h_div_hhv150"]),
                "formula_version":     version,
                "join_date":           join_date,
                "consecutive_days":    consecutive_days,
                "total_days_60d":      total_days_60d,
                "enter_pool_count_60d": enter_pool_count_60d,
                "last_exit_date":      None,
                "close_bfq":           row["close_bfq"],
                "floatmv":             row["floatmv"],
                "change_pct":          row["change_pct"],
                "turnover":            row["turnover"],
            })

        if rows:
            batch = pd.DataFrame(rows)
            con.register("_szh_batch", batch)
            con.execute("""
                INSERT OR REPLACE INTO sanxianhong_daily
                SELECT trade_date, symbol, name,
                       rps50, rps120, rps250, h_div_hhv150,
                       formula_version, join_date, consecutive_days,
                       total_days_60d, enter_pool_count_60d, last_exit_date,
                       close_bfq, floatmv, change_pct, turnover
                FROM _szh_batch
            """)
            con.unregister("_szh_batch")
            total += len(rows)

    return total
