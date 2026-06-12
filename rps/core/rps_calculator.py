"""Stock and block RPS calculation.

Data flow:
  [static caches, refreshed on data sync]
    stock_pool          ← refresh_stock_pool()
    block_member_count  ← refresh_block_member_count()

  [daily cache, refreshed once per trading day]
    block_daily_pct     ← calc_block_daily_pct() / calc_block_daily_pct_history()

  [final outputs]
    rps_stock_daily     ← calc_stock_rps() / calc_stock_rps_history()
    rps_block_daily     ← calc_block_rps() / calc_block_rps_history()
"""
from __future__ import annotations

import duckdb

# ---------------------------------------------------------------------------
# Step 1a: block_daily_pct — expensive JOIN done once per day
# ---------------------------------------------------------------------------

_SQL_BLOCK_DAILY_PCT_SINGLE = """
INSERT OR REPLACE INTO block_daily_pct
SELECT
    bd.date                                                                  AS trade_date,
    bm.block_code,
    bi.block_name,
    bi.block_type,
    AVG(bd.change_pct)                                                       AS block_pct_1d,
    COUNT(bd.symbol)                                                         AS member_count,
    SUM(CASE WHEN bd.change_pct > 0 THEN 1 ELSE 0 END)                      AS rising_count,
    SUM(CASE
            WHEN bd.symbol LIKE '688%' OR bd.symbol LIKE '3%'
                THEN (CASE WHEN bd.change_pct >= 19.7 THEN 1 ELSE 0 END)
            ELSE (CASE WHEN bd.change_pct >= 9.7 THEN 1 ELSE 0 END)
        END)                                                                 AS limit_up_count
FROM raw_tdx_blocks_member bm
JOIN block_member_count    mc ON mc.block_code = bm.block_code
JOIN raw_tdx_blocks_info   bi ON bi.block_code = bm.block_code
JOIN stock_pool            sp ON sp.symbol = bm.stock_symbol
JOIN raw_basic_daily       bd ON bd.symbol = bm.stock_symbol AND bd.date = $target_date
GROUP BY bd.date, bm.block_code, bi.block_name, bi.block_type
"""

_SQL_BLOCK_DAILY_PCT_HISTORY = """
INSERT OR REPLACE INTO block_daily_pct
SELECT
    bd.date                                                                  AS trade_date,
    bm.block_code,
    bi.block_name,
    bi.block_type,
    AVG(bd.change_pct)                                                       AS block_pct_1d,
    COUNT(bd.symbol)                                                         AS member_count,
    SUM(CASE WHEN bd.change_pct > 0 THEN 1 ELSE 0 END)                      AS rising_count,
    SUM(CASE
            WHEN bd.symbol LIKE '688%' OR bd.symbol LIKE '3%'
                THEN (CASE WHEN bd.change_pct >= 19.7 THEN 1 ELSE 0 END)
            ELSE (CASE WHEN bd.change_pct >= 9.7 THEN 1 ELSE 0 END)
        END)                                                                 AS limit_up_count
FROM raw_tdx_blocks_member bm
JOIN block_member_count    mc ON mc.block_code = bm.block_code
JOIN raw_tdx_blocks_info   bi ON bi.block_code = bm.block_code
JOIN stock_pool            sp ON sp.symbol = bm.stock_symbol
JOIN raw_basic_daily       bd ON bd.symbol = bm.stock_symbol
WHERE bd.date BETWEEN $start_date AND $end_date
GROUP BY bd.date, bm.block_code, bi.block_name, bi.block_type
"""

# ---------------------------------------------------------------------------
# Step 1b: rps_block_daily — pure window functions on block_daily_pct
# ---------------------------------------------------------------------------

_SQL_BLOCK_RPS_SINGLE = """
WITH block_returns AS (
    SELECT
        p.*,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_5d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 9  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_10d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_15d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_20d
    FROM block_daily_pct p
    WHERE p.member_count <= $max_member_count
),
ranked AS (
    SELECT
        r.*,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_5d  NULLS FIRST) * 100) AS bkrps5,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_10d NULLS FIRST) * 100) AS bkrps10,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_15d NULLS FIRST) * 100) AS bkrps15,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100) AS bkrps20,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100) AS bkrps50
    FROM block_returns r
    WHERE r.trade_date = $target_date
)
INSERT OR REPLACE INTO rps_block_daily
SELECT
    r.trade_date, r.block_code, r.block_name, r.block_type,
    r.bkrps5, r.bkrps10, r.bkrps15, r.bkrps20, r.bkrps50,
    r.block_pct_1d, r.block_pct_5d, r.block_pct_10d, r.block_pct_20d,
    r.member_count, r.rising_count, r.limit_up_count
FROM ranked r
"""

_SQL_BLOCK_RPS_HISTORY = """
WITH block_returns AS (
    SELECT
        p.*,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_5d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 9  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_10d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_15d,
        (EXP(SUM(LN(GREATEST(1 + p.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY p.block_code ORDER BY p.trade_date
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_20d
    FROM block_daily_pct p
    WHERE p.member_count <= $max_member_count
),
ranked AS (
    SELECT
        r.*,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_5d  NULLS FIRST) * 100) AS bkrps5,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_10d NULLS FIRST) * 100) AS bkrps10,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_15d NULLS FIRST) * 100) AS bkrps15,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100) AS bkrps20,
        floor(PERCENT_RANK() OVER (PARTITION BY r.trade_date ORDER BY r.block_pct_20d NULLS FIRST) * 100) AS bkrps50
    FROM block_returns r
    WHERE r.trade_date BETWEEN $start_date AND $end_date
)
INSERT OR REPLACE INTO rps_block_daily
SELECT
    r.trade_date, r.block_code, r.block_name, r.block_type,
    r.bkrps5, r.bkrps10, r.bkrps15, r.bkrps20, r.bkrps50,
    r.block_pct_1d, r.block_pct_5d, r.block_pct_10d, r.block_pct_20d,
    r.member_count, r.rising_count, r.limit_up_count
FROM ranked r
"""

# ---------------------------------------------------------------------------
# Step 2: rps_stock_daily — uses stock_pool cache instead of repeated filter JOIN
# ---------------------------------------------------------------------------

_STOCK_RPS_CTE = """
WITH returns AS (
    SELECT
        q.date,
        q.symbol,
        q.close                                                               AS close_qfq,
        q.high                                                                AS high_qfq,
        (q.close / NULLIF(LAG(q.close, 5)   OVER w, 0) - 1) * 100           AS pct_5d,
        (q.close / NULLIF(LAG(q.close, 10)  OVER w, 0) - 1) * 100           AS pct_10d,
        (q.close / NULLIF(LAG(q.close, 20)  OVER w, 0) - 1) * 100           AS pct_20d,
        (q.close / NULLIF(LAG(q.close, 50)  OVER w, 0) - 1) * 100           AS pct_50d,
        (q.close / NULLIF(LAG(q.close, 120) OVER w, 0) - 1) * 100           AS pct_120d,
        (q.close / NULLIF(LAG(q.close, 250) OVER w, 0) - 1) * 100           AS pct_250d,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 59  PRECEDING AND CURRENT ROW)                      AS hhv60_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 149 PRECEDING AND CURRENT ROW)                      AS hhv150_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)                      AS hhv250_qfq
    FROM v_stock_qfq q
    JOIN stock_pool sp ON sp.symbol = q.symbol
    WINDOW w AS (PARTITION BY q.symbol ORDER BY q.date)
),
ranked AS (
    SELECT
        r.*,
        -- rps5: all rows here have pct_5d non-NULL (filtered above), plain rank
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_5d) * 100
            )                                                        AS rps5,
        -- rps10..rps250: rank only within stocks that have valid data for that
        -- period; stocks with shorter history get NULL, not a deflated score.
        -- PARTITION BY (pct IS NOT NULL) splits NULL/non-NULL into separate
        -- buckets so NULL rows don't dilute the non-NULL ranking population.
        CASE WHEN r.pct_10d  IS NOT NULL
             THEN floor(PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_10d  IS NOT NULL)
                      ORDER BY r.pct_10d ) * 100) END               AS rps10,
        CASE WHEN r.pct_20d  IS NOT NULL
             THEN floor(PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_20d  IS NOT NULL)
                      ORDER BY r.pct_20d ) * 100) END               AS rps20,
        CASE WHEN r.pct_50d  IS NOT NULL
             THEN floor(PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_50d  IS NOT NULL)
                      ORDER BY r.pct_50d ) * 100) END               AS rps50,
        CASE WHEN r.pct_120d IS NOT NULL
             THEN floor(PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_120d IS NOT NULL)
                      ORDER BY r.pct_120d) * 100) END               AS rps120,
        CASE WHEN r.pct_250d IS NOT NULL
             THEN floor(PERCENT_RANK() OVER (
                      PARTITION BY r.date, (r.pct_250d IS NOT NULL)
                      ORDER BY r.pct_250d) * 100) END               AS rps250
    FROM returns r
    WHERE r.pct_5d IS NOT NULL
      AND {date_filter}
)
INSERT OR REPLACE INTO rps_stock_daily
SELECT
    r.date    AS trade_date,
    r.symbol,
    sp.name,
    r.rps5, r.rps10, r.rps20, r.rps50, r.rps120, r.rps250,
    r.pct_5d, r.pct_10d, r.pct_20d, r.pct_50d, r.pct_120d, r.pct_250d,
    r.close_qfq,
    r.hhv60_qfq, r.hhv150_qfq, r.hhv250_qfq,
    r.high_qfq / NULLIF(r.hhv150_qfq, 0) AS h_div_hhv150,
    r.high_qfq / NULLIF(r.hhv250_qfq, 0) AS h_div_hhv250,
    b.close   AS close_bfq,
    b.floatmv, b.totalmv, b.turnover, b.amount, b.change_pct
FROM ranked r
JOIN stock_pool sp ON sp.symbol = r.symbol
LEFT JOIN v_stock_bfq b ON b.symbol = r.symbol AND b.date = r.date
"""

_SQL_STOCK_RPS_SINGLE = _STOCK_RPS_CTE.format(date_filter="r.date = $target_date")
_SQL_STOCK_RPS_HISTORY = _STOCK_RPS_CTE.format(date_filter="r.date BETWEEN $start_date AND $end_date")

_DEFAULT_MAX_MEMBER = 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calc_block_daily_pct(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    """Compute and cache block equal-weight daily returns for one date.

    Must be called before calc_block_rps for the same date.
    """
    con.execute(_SQL_BLOCK_DAILY_PCT_SINGLE, {"target_date": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_daily_pct WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_block_daily_pct_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    """Bulk compute and cache block daily returns for a date range."""
    con.execute(_SQL_BLOCK_DAILY_PCT_HISTORY, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM block_daily_pct WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0


def calc_stock_rps(con: duckdb.DuckDBPyConnection, target_date: str) -> int:
    con.execute(_SQL_STOCK_RPS_SINGLE, {"target_date": target_date})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_stock_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_stock_rps_history(
    con: duckdb.DuckDBPyConnection, start_date: str, end_date: str
) -> int:
    con.execute(_SQL_STOCK_RPS_HISTORY, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_stock_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0


def calc_block_rps(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    max_member_count: int = _DEFAULT_MAX_MEMBER,
) -> int:
    con.execute(_SQL_BLOCK_RPS_SINGLE, {"target_date": target_date, "max_member_count": max_member_count})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_block_daily WHERE trade_date = $1", [target_date]
    ).fetchone()
    return row[0] if row else 0


def calc_block_rps_history(
    con: duckdb.DuckDBPyConnection,
    start_date: str,
    end_date: str,
    max_member_count: int = _DEFAULT_MAX_MEMBER,
) -> int:
    con.execute(_SQL_BLOCK_RPS_HISTORY, {
        "start_date": start_date,
        "end_date": end_date,
        "max_member_count": max_member_count,
    })
    row = con.execute(
        "SELECT COUNT(*) FROM rps_block_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0
