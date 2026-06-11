"""Unit tests for RPS calculation using in-memory DuckDB with synthetic data."""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from rps.core.db import init_tables, refresh_block_member_count, refresh_stock_pool
from rps.core.rps_calculator import (
    calc_block_daily_pct,
    calc_stock_rps,
    calc_block_rps,
)
from rps.core.sanxianhong import calc_sanxianhong, calc_sanxianhong_history


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def con():
    """In-memory DuckDB with schema + synthetic data."""
    c = duckdb.connect(":memory:")
    init_tables(c)
    _seed_data(c)
    return c


def _seed_data(c: duckdb.DuckDBPyConnection) -> None:
    """Create minimal tables/views the calculator depends on."""
    # raw tables
    c.execute("""
        CREATE TABLE raw_symbol_class (symbol VARCHAR, class VARCHAR);
        CREATE TABLE raw_symbol_name  (symbol VARCHAR, name VARCHAR, class VARCHAR);
        CREATE TABLE raw_basic_daily  (
            date DATE, symbol VARCHAR, close DOUBLE, preclose DOUBLE,
            change_pct DOUBLE, amplitude DOUBLE, turnover DOUBLE,
            floatmv DOUBLE, totalmv DOUBLE
        );
        CREATE TABLE raw_adjust_factor (symbol VARCHAR, date DATE, hfq_factor DOUBLE);
        CREATE TABLE raw_tdx_blocks_info   (
            block_type VARCHAR, block_name VARCHAR, block_symbol VARCHAR,
            block_code VARCHAR, parent_code VARCHAR, block_level INTEGER
        );
        CREATE TABLE raw_tdx_blocks_member (stock_symbol VARCHAR, block_code VARCHAR);
        CREATE TABLE raw_kline_daily (
            symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, amount DOUBLE, volume BIGINT, date DATE
        );
    """)

    # 5 stocks × 300 trading days
    symbols = ["000001", "000002", "000003", "000004", "000005"]
    start = date(2023, 1, 3)
    rows_kline = []
    rows_basic = []
    rows_factor = []
    rows_class = [(s, "stock") for s in symbols]
    rows_name = [(s, f"测试股{s}", "stock") for s in symbols]

    for i, sym in enumerate(symbols):
        price = 10.0 + i * 2
        for d in range(300):
            dt = start + timedelta(days=d)
            # simple linear drift so each stock has different performance
            drift = 1 + (i - 2) * 0.0005
            price *= drift
            rows_kline.append((sym, price * 0.99, price * 1.01, price * 0.98, price, price * 1e6, 100000, dt))
            rows_basic.append((dt, sym, price, price / drift, (drift - 1) * 100, 2.0, 5.0, price * 1e8, price * 2e8))
            rows_factor.append((sym, dt, 1.0))

    c.executemany("INSERT INTO raw_symbol_class VALUES (?, ?)", rows_class)
    c.executemany("INSERT INTO raw_symbol_name  VALUES (?, ?, ?)", rows_name)
    c.executemany("INSERT INTO raw_kline_daily  VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_kline)
    c.executemany("INSERT INTO raw_basic_daily  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_basic)
    c.executemany("INSERT INTO raw_adjust_factor VALUES (?, ?, ?)", rows_factor)

    # block membership: 2 blocks
    c.execute("INSERT INTO raw_tdx_blocks_info VALUES ('概念','测试板块A','','BK001','',1)")
    c.execute("INSERT INTO raw_tdx_blocks_info VALUES ('概念','测试板块B','','BK002','',1)")
    for s in symbols[:3]:
        c.execute(f"INSERT INTO raw_tdx_blocks_member VALUES ('{s}', 'BK001')")
    for s in symbols[2:]:
        c.execute(f"INSERT INTO raw_tdx_blocks_member VALUES ('{s}', 'BK002')")

    # Populate static caches that queries depend on
    refresh_block_member_count(c)
    refresh_stock_pool(c)

    # Create the views that the calculator reads from
    c.execute("""
        CREATE VIEW v_stock_qfq AS
        SELECT
            k.symbol, k.date,
            k.open * f.hfq_factor AS open,
            k.high * f.hfq_factor AS high,
            k.low  * f.hfq_factor AS low,
            k.close* f.hfq_factor AS close,
            b.turnover, b.floatmv, b.totalmv, b.change_pct, b.amplitude,
            f.hfq_factor,
            f.hfq_factor AS qfq_factor,
            b.preclose
        FROM raw_kline_daily k
        JOIN raw_adjust_factor f ON f.symbol = k.symbol AND f.date = k.date
        JOIN raw_basic_daily   b ON b.symbol = k.symbol AND b.date = k.date
    """)
    c.execute("""
        CREATE VIEW v_stock_bfq AS
        SELECT
            k.symbol, k.date,
            k.open, k.high, k.low, k.close,
            b.preclose, b.turnover, b.floatmv, b.totalmv, b.change_pct,
            b.amplitude, 1.0 AS hfq_factor, 1.0 AS qfq_factor,
            k.amount
        FROM raw_kline_daily k
        JOIN raw_basic_daily b ON b.symbol = k.symbol AND b.date = k.date
    """)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_calc_stock_rps_returns_rows(con):
    target = "2023-06-01"
    n = calc_stock_rps(con, target)
    assert n > 0, "Should insert rows for a date with enough history"


def test_rps_range(con):
    target = "2023-06-01"
    calc_stock_rps(con, target)
    rows = con.execute(
        "SELECT rps50 FROM rps_stock_daily WHERE trade_date = ? AND rps50 IS NOT NULL",
        [target],
    ).fetchall()
    assert rows, "Expected rps50 values"
    for (v,) in rows:
        assert 0 <= v <= 99, f"rps50={v} out of range"


def test_rps_ordering(con):
    """The stock with highest 50-day return should have the highest rps50."""
    target = "2023-06-01"
    calc_stock_rps(con, target)
    rows = con.execute("""
        SELECT symbol, rps50, pct_50d
        FROM rps_stock_daily
        WHERE trade_date = ?
        ORDER BY rps50 DESC
    """, [target]).df()
    assert not rows.empty
    # Top RPS stock should have highest or near-highest pct_50d
    top_rps_sym = rows.iloc[0]["symbol"]
    top_pct_sym = rows.sort_values("pct_50d", ascending=False).iloc[0]["symbol"]
    assert top_rps_sym == top_pct_sym, "Highest RPS should match highest N-day return"


def test_no_future_leak(con):
    """Rows for date D must only use data <= D."""
    target = "2023-03-01"
    calc_stock_rps(con, target)
    rows = con.execute(
        "SELECT * FROM rps_stock_daily WHERE trade_date > ?", [target]
    ).fetchall()
    assert rows == [], "No rows should be written for dates after target"


def test_calc_block_rps(con):
    target = "2023-06-01"
    calc_block_daily_pct(con, target)
    n = calc_block_rps(con, target)
    assert n > 0


def test_block_rps_range(con):
    target = "2023-06-01"
    calc_block_daily_pct(con, target)
    calc_block_rps(con, target)
    rows = con.execute(
        "SELECT bkrps5 FROM rps_block_daily WHERE trade_date = ?", [target]
    ).fetchall()
    for (v,) in rows:
        assert 0 <= v <= 99


def test_sanxianhong_subset_of_rps(con):
    """Sanxianhong pool must be a subset of rps_stock_daily for the same date."""
    target = "2023-09-01"
    calc_stock_rps(con, target)
    cfg = {
        "strict": {
            "rps50_min": 50,   # relaxed so synthetic data can hit it
            "rps120_min": 50,
            "rps250_min": 50,
            "hhv_period": 150,
            "hhv_ratio_min": 0.5,
        }
    }
    calc_sanxianhong(con, target, cfg)
    szh = con.execute(
        "SELECT symbol FROM sanxianhong_daily WHERE trade_date = ?", [target]
    ).df()
    rps = con.execute(
        "SELECT symbol FROM rps_stock_daily WHERE trade_date = ?", [target]
    ).df()
    if not szh.empty:
        assert set(szh["symbol"]).issubset(set(rps["symbol"]))


def test_consecutive_days_increments(con):
    """consecutive_days should grow when a stock qualifies on two consecutive dates."""
    cfg = {
        "strict": {
            "rps50_min": 50,
            "rps120_min": 50,
            "rps250_min": 50,
            "hhv_period": 150,
            "hhv_ratio_min": 0.5,
        }
    }
    for d in ["2023-09-01", "2023-09-04"]:
        calc_stock_rps(con, d)
        calc_sanxianhong(con, d, cfg)

    rows = con.execute("""
        SELECT symbol, consecutive_days, trade_date
        FROM sanxianhong_daily
        ORDER BY symbol, trade_date
    """).df()

    for sym, grp in rows.groupby("symbol"):
        days = grp.sort_values("trade_date")["consecutive_days"].tolist()
        if len(days) >= 2 and days[-1] > 0:
            # Consecutive days on the second date should be >= first date's value
            assert days[-1] >= days[-2], (
                f"{sym}: consecutive_days should not decrease: {days}"
            )


def _seed_sanxianhong_pattern(c) -> tuple[list[str], dict]:
    """Seed rps_stock_daily for one symbol with a controlled qualify pattern.

    Qualifies on day indices: run1=0..4, run2=10..12, run3=70..75.
    Non-qualifying days are present too (so td_idx is dense, 1..80).
    """
    import datetime

    days = [(datetime.date(2024, 1, 1) + datetime.timedelta(days=i)).isoformat()
            for i in range(80)]
    qual_idx = set(range(0, 5)) | set(range(10, 13)) | set(range(70, 76))
    rows = []
    for i, d in enumerate(days):
        q = i in qual_idx
        rps = 99 if q else 10
        hh = 0.99 if q else 0.10
        rows.append((d, "A", "测试A", rps, rps, rps, hh, 10.0, 1e9, 1.0, 5.0))
    c.executemany("""
        INSERT INTO rps_stock_daily
          (trade_date, symbol, name, rps50, rps120, rps250, h_div_hhv150,
           close_bfq, floatmv, change_pct, turnover)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    cfg = {"strict": {"rps50_min": 90, "rps120_min": 90, "rps250_min": 90,
                      "hhv_ratio_min": 0.85}}
    return days, cfg


def _szh_rows(c):
    return c.execute("""
        SELECT trade_date::VARCHAR t, consecutive_days c, total_days_60d td,
               enter_pool_count_60d e, join_date::VARCHAR j,
               last_exit_date::VARCHAR le
        FROM sanxianhong_daily ORDER BY trade_date
    """).df()


def test_sanxianhong_streaks_and_windows():
    """Exact streak / 60-day window / entry-count values across gaps and re-entry."""
    c = duckdb.connect(":memory:")
    init_tables(c)
    days, cfg = _seed_sanxianhong_pattern(c)
    calc_sanxianhong_history(c, days[0], days[-1], cfg)
    res = _szh_rows(c)

    def row(i):
        return res[res["t"] == days[i]].iloc[0]

    def lexit(i):
        v = res[res["t"] == days[i]].iloc[0]["le"]
        return None if pd.isna(v) else v

    # run1 last day: 5 consecutive, window has only run1 (5 days, 1 entry)
    r = row(4)
    assert (r.c, r.td, r.e, r.j) == (5, 5, 1, days[0])
    assert lexit(4) is None  # first run ever -> no prior exit
    # run2 last day: 3 consecutive; window spans run1+run2 -> 8 days, 2 entries
    r = row(12)
    assert (r.c, r.td, r.e, r.j) == (3, 8, 2, days[10])
    assert lexit(12) == days[4]   # previous run ended on day 4
    # run3 last day: 6 consecutive; old runs have expired from the 60d window
    r = row(75)
    assert (r.c, r.td, r.e, r.j) == (6, 6, 1, days[70])
    assert lexit(75) == days[12]  # previous run ended on day 12


def test_sanxianhong_daily_matches_history():
    """Per-date daily compute must equal the full bulk history compute."""
    c = duckdb.connect(":memory:")
    init_tables(c)
    days, cfg = _seed_sanxianhong_pattern(c)

    calc_sanxianhong_history(c, days[0], days[-1], cfg)
    hist = _szh_rows(c)

    c.execute("DELETE FROM sanxianhong_daily")
    for d in days:
        calc_sanxianhong(c, d, cfg)
    daily = _szh_rows(c)

    assert hist.equals(daily)
