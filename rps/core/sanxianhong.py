"""三线红榜单计算.

History bulk:   calc_sanxianhong_history()  — single SQL pass, gap-and-islands
Daily:          calc_sanxianhong()          — same windowed SQL for one date
"""
from __future__ import annotations

from typing import Any

import duckdb


# ---------------------------------------------------------------------------
# History bulk — single SQL pass for all dates in range
# ---------------------------------------------------------------------------

def _build_history_sql(version: str, cfg: dict, start_date: str, end_date: str) -> str:
    c = cfg[version]
    return f"""
WITH trading_days AS (
    -- DISTINCT must be applied BEFORE ROW_NUMBER, otherwise the window
    -- numbers every row of rps_stock_daily (millions) and DISTINCT keeps
    -- them all -> td_idx explodes and streaks become absurd.
    SELECT trade_date,
           ROW_NUMBER() OVER (ORDER BY trade_date) AS td_idx
    FROM (SELECT DISTINCT trade_date FROM rps_stock_daily)
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
# Daily single-date — reuses the windowed history SQL for one date.
#
# The CTEs in _build_history_sql compute streaks and the 60-day window over
# the *entire* rps_stock_daily; only the final INSERT filters by date. So a
# single date is computed exactly the same way as during a full backfill —
# correct across gaps and re-entries, with no fragile yesterday-delta logic.
# ---------------------------------------------------------------------------

def calc_sanxianhong(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    cfg: dict[str, Any],
    versions: list[str] | None = None,
) -> int:
    """Compute 三线红 for a single date via the windowed SQL.

    Windows are evaluated over all of rps_stock_daily, so consecutive_days,
    join_date, total_days_60d and enter_pool_count_60d are always correct
    regardless of prior gaps. Idempotent (INSERT OR REPLACE on the date).
    """
    return calc_sanxianhong_history(con, target_date, target_date, cfg, versions)
