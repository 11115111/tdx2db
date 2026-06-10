"""Stock and block RPS calculation."""
from __future__ import annotations

import duckdb

_STOCK_FILTER = """
    s.class = 'stock'
    AND COALESCE(n.name, '') NOT LIKE '%ST%'
    AND COALESCE(n.name, '') NOT LIKE '%退%'
    AND s.symbol NOT LIKE '8%'
    AND s.symbol NOT LIKE '4%'
    AND s.symbol NOT LIKE '9%'
"""

_BLOCK_FILTER = """
    sc.class = 'stock'
    AND COALESCE(sn.name, '') NOT LIKE '%ST%'
    AND COALESCE(sn.name, '') NOT LIKE '%退%'
    AND bm.stock_symbol NOT LIKE '8%'
    AND bm.stock_symbol NOT LIKE '4%'
    AND bm.stock_symbol NOT LIKE '9%'
"""

_STOCK_RPS_CTE = """
WITH returns AS (
    SELECT
        q.date,
        q.symbol,
        q.close                                                               AS close_qfq,
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
    JOIN raw_symbol_class s ON s.symbol = q.symbol
    LEFT JOIN raw_symbol_name n ON n.symbol = q.symbol
    WHERE {stock_filter}
    WINDOW w AS (PARTITION BY q.symbol ORDER BY q.date)
),
ranked AS (
    SELECT
        r.*,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_5d   NULLS FIRST) * 99 AS INTEGER) AS rps5,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_10d  NULLS FIRST) * 99 AS INTEGER) AS rps10,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_20d  NULLS FIRST) * 99 AS INTEGER) AS rps20,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_50d  NULLS FIRST) * 99 AS INTEGER) AS rps50,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_120d NULLS FIRST) * 99 AS INTEGER) AS rps120,
        CAST(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_250d NULLS FIRST) * 99 AS INTEGER) AS rps250
    FROM returns r
    WHERE r.pct_5d IS NOT NULL
      AND {date_filter}
)
INSERT OR REPLACE INTO rps_stock_daily
SELECT
    r.date        AS trade_date,
    r.symbol,
    n.name,
    r.rps5, r.rps10, r.rps20, r.rps50, r.rps120, r.rps250,
    r.pct_5d, r.pct_10d, r.pct_20d, r.pct_50d, r.pct_120d, r.pct_250d,
    r.close_qfq,
    r.hhv60_qfq, r.hhv150_qfq, r.hhv250_qfq,
    r.close_qfq / NULLIF(r.hhv150_qfq, 0) AS h_div_hhv150,
    r.close_qfq / NULLIF(r.hhv250_qfq, 0) AS h_div_hhv250,
    b.close       AS close_bfq,
    b.floatmv, b.totalmv, b.turnover, b.amount, b.change_pct
FROM ranked r
LEFT JOIN raw_symbol_name n ON n.symbol = r.symbol
LEFT JOIN v_stock_bfq b     ON b.symbol = r.symbol AND b.date = r.date
"""

_SQL_STOCK_RPS_SINGLE = _STOCK_RPS_CTE.format(
    stock_filter=_STOCK_FILTER,
    date_filter="r.date = $target_date",
)

_SQL_STOCK_RPS_HISTORY = _STOCK_RPS_CTE.format(
    stock_filter=_STOCK_FILTER,
    date_filter="r.date BETWEEN $start_date AND $end_date",
)

_BLOCK_RPS_CTE = """
WITH eligible_blocks AS (
    SELECT block_code
    FROM block_member_count
    WHERE member_count <= {max_member_count}
),
block_members AS (
    SELECT
        bm.block_code,
        bi.block_name,
        bi.block_type,
        bd.date,
        AVG(bd.change_pct)                                                   AS block_pct_1d,
        COUNT(bd.symbol)                                                     AS member_count,
        SUM(CASE WHEN bd.change_pct > 0 THEN 1 ELSE 0 END)                  AS rising_count,
        SUM(CASE
                WHEN bd.symbol LIKE '688%' OR bd.symbol LIKE '3%'
                    THEN (CASE WHEN bd.change_pct >= 19.7 THEN 1 ELSE 0 END)
                ELSE (CASE WHEN bd.change_pct >= 9.7 THEN 1 ELSE 0 END)
            END)                                                             AS limit_up_count
    FROM raw_tdx_blocks_member bm
    JOIN eligible_blocks       eb ON eb.block_code = bm.block_code
    JOIN raw_tdx_blocks_info   bi ON bi.block_code = bm.block_code
    JOIN raw_basic_daily       bd ON bd.symbol = bm.stock_symbol
    JOIN raw_symbol_class      sc ON sc.symbol = bm.stock_symbol
    LEFT JOIN raw_symbol_name  sn ON sn.symbol = bm.stock_symbol
    WHERE {block_filter}
    GROUP BY bm.block_code, bi.block_name, bi.block_type, bd.date
),
block_returns AS (
    SELECT
        bm.*,
        (EXP(SUM(LN(GREATEST(1 + bm.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY bm.block_code ORDER BY bm.date
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_5d,
        (EXP(SUM(LN(GREATEST(1 + bm.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY bm.block_code ORDER BY bm.date
                  ROWS BETWEEN 9  PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_10d,
        (EXP(SUM(LN(GREATEST(1 + bm.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY bm.block_code ORDER BY bm.date
                  ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_15d,
        (EXP(SUM(LN(GREATEST(1 + bm.block_pct_1d / 100, 0.01)))
            OVER (PARTITION BY bm.block_code ORDER BY bm.date
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) - 1) * 100    AS block_pct_20d
    FROM block_members bm
),
filtered AS (
    SELECT * FROM block_returns WHERE {date_filter}
),
ranked AS (
    SELECT
        f.*,
        CAST(PERCENT_RANK() OVER (PARTITION BY f.date ORDER BY f.block_pct_5d  NULLS FIRST) * 99 AS INTEGER) AS bkrps5,
        CAST(PERCENT_RANK() OVER (PARTITION BY f.date ORDER BY f.block_pct_10d NULLS FIRST) * 99 AS INTEGER) AS bkrps10,
        CAST(PERCENT_RANK() OVER (PARTITION BY f.date ORDER BY f.block_pct_15d NULLS FIRST) * 99 AS INTEGER) AS bkrps15,
        CAST(PERCENT_RANK() OVER (PARTITION BY f.date ORDER BY f.block_pct_20d NULLS FIRST) * 99 AS INTEGER) AS bkrps20,
        CAST(PERCENT_RANK() OVER (PARTITION BY f.date ORDER BY f.block_pct_20d NULLS FIRST) * 99 AS INTEGER) AS bkrps50
    FROM filtered f
)
INSERT OR REPLACE INTO rps_block_daily
SELECT
    r.date        AS trade_date,
    r.block_code,
    r.block_name,
    r.block_type,
    r.bkrps5, r.bkrps10, r.bkrps15, r.bkrps20, r.bkrps50,
    r.block_pct_1d, r.block_pct_5d, r.block_pct_10d, r.block_pct_20d,
    r.member_count, r.rising_count, r.limit_up_count
FROM ranked r
"""

_DEFAULT_MAX_MEMBER = 100


def _block_sql(date_filter: str, max_member_count: int) -> str:
    return _BLOCK_RPS_CTE.format(
        block_filter=_BLOCK_FILTER,
        date_filter=date_filter,
        max_member_count=max_member_count,
    )


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
    sql = _block_sql("date = $target_date", max_member_count)
    con.execute(sql, {"target_date": target_date})
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
    sql = _block_sql("date BETWEEN $start_date AND $end_date", max_member_count)
    con.execute(sql, {"start_date": start_date, "end_date": end_date})
    row = con.execute(
        "SELECT COUNT(*) FROM rps_block_daily WHERE trade_date BETWEEN $1 AND $2",
        [start_date, end_date],
    ).fetchone()
    return row[0] if row else 0
