"""三线红榜单计算."""
from __future__ import annotations

from typing import Any

import pandas as pd
import duckdb


def _calc_streak_stats(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    cfg: dict[str, Any],
    version: str,
) -> pd.DataFrame:
    """Return per-symbol streak stats for the 60 days ending at target_date."""
    c = cfg[version]
    rps50_min = c["rps50_min"]
    rps120_min = c["rps120_min"]
    rps250_min = c["rps250_min"]
    hhv_ratio_min = c["hhv_ratio_min"]

    sql = f"""
    SELECT trade_date, symbol
    FROM rps_stock_daily
    WHERE trade_date <= '{target_date}'
      AND trade_date > '{target_date}'::DATE - INTERVAL '60' DAY
      AND rps50  >= {rps50_min}
      AND rps120 >= {rps120_min}
      AND rps250 >= {rps250_min}
      AND h_div_hhv150 >= {hhv_ratio_min}
    ORDER BY symbol, trade_date
    """
    df = con.execute(sql).df()
    if df.empty:
        return pd.DataFrame(columns=["symbol", "consecutive_days", "total_days_60d",
                                     "enter_pool_count_60d", "join_date"])

    # Map dates to integer index using sorted unique trade-dates so gaps (weekends/
    # holidays) don't falsely break streaks.
    trade_dates = sorted(df["trade_date"].unique())
    date_to_idx: dict = {d: i for i, d in enumerate(trade_dates)}
    df["date_idx"] = df["trade_date"].map(date_to_idx)

    rows = []
    for symbol, grp in df.groupby("symbol"):
        grp = grp.sort_values("date_idx").reset_index(drop=True)
        grp["gap"] = grp["date_idx"].diff().fillna(1)
        grp["run_id"] = (grp["gap"] != 1).cumsum()

        last_run_id = grp["run_id"].iloc[-1]
        last_date = grp["trade_date"].iloc[-1]

        # Only count consecutive days if the last entry IS the target date.
        if str(last_date)[:10] == target_date[:10]:
            consecutive = int((grp["run_id"] == last_run_id).sum())
            join_date = grp.loc[grp["run_id"] == last_run_id, "trade_date"].iloc[0]
        else:
            consecutive = 0
            join_date = None

        rows.append({
            "symbol": symbol,
            "consecutive_days": consecutive,
            "total_days_60d": len(grp),
            "enter_pool_count_60d": int(grp["run_id"].nunique()),
            "join_date": join_date,
        })

    return pd.DataFrame(rows)


def _last_exit_date(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    cfg: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    """Return {symbol: last_exit_date} for symbols currently in pool."""
    c = cfg[version]
    sql = f"""
    SELECT symbol, MAX(trade_date) AS last_in
    FROM rps_stock_daily
    WHERE trade_date < '{target_date}'
      AND rps50  >= {c['rps50_min']}
      AND rps120 >= {c['rps120_min']}
      AND rps250 >= {c['rps250_min']}
      AND h_div_hhv150 >= {c['hhv_ratio_min']}
    GROUP BY symbol
    """
    in_pool = con.execute(sql).df().set_index("symbol")["last_in"].to_dict()

    # Symbols whose last_in < yesterday have an exit gap → last_exit_date is
    # the trading day after last_in (we approximate as last_in + 1 calendar day;
    # exact value requires calendar join which adds complexity for marginal gain).
    yesterday_sql = f"""
    SELECT MAX(trade_date) AS prev_day
    FROM rps_stock_daily
    WHERE trade_date < '{target_date}'
    """
    row = con.execute(yesterday_sql).fetchone()
    prev_day = row[0] if row else None

    result: dict[str, Any] = {}
    for sym, last_in in in_pool.items():
        if prev_day and str(last_in) < str(prev_day):
            result[sym] = last_in
        else:
            result[sym] = None
    return result


def calc_sanxianhong(
    con: duckdb.DuckDBPyConnection,
    target_date: str,
    cfg: dict[str, Any],
    versions: list[str] | None = None,
) -> int:
    """
    Compute sanxianhong_daily for target_date and insert/replace rows.
    Returns total rows written.
    """
    if versions is None:
        versions = ["strict"]

    total = 0
    for version in versions:
        c = cfg[version]
        rps50_min = c["rps50_min"]
        rps120_min = c["rps120_min"]
        rps250_min = c["rps250_min"]
        hhv_ratio_min = c["hhv_ratio_min"]

        today_sql = f"""
        SELECT
            trade_date, symbol, name,
            rps50, rps120, rps250,
            h_div_hhv150,
            close_bfq, floatmv, change_pct, turnover
        FROM rps_stock_daily
        WHERE trade_date = '{target_date}'
          AND rps50  >= {rps50_min}
          AND rps120 >= {rps120_min}
          AND rps250 >= {rps250_min}
          AND h_div_hhv150 >= {hhv_ratio_min}
        """
        today_df = con.execute(today_sql).df()
        if today_df.empty:
            continue

        streak_df = _calc_streak_stats(con, target_date, cfg, version)
        exit_map = _last_exit_date(con, target_date, cfg, version)

        merged = today_df.merge(streak_df, on="symbol", how="left")
        merged["formula_version"] = version
        merged["last_exit_date"] = merged["symbol"].map(exit_map)

        merged["consecutive_days"] = merged["consecutive_days"].fillna(1).astype(int)
        merged["total_days_60d"] = merged["total_days_60d"].fillna(1).astype(int)
        merged["enter_pool_count_60d"] = merged["enter_pool_count_60d"].fillna(1).astype(int)

        con.register("_szh_batch", merged)
        con.execute(f"""
            INSERT OR REPLACE INTO sanxianhong_daily
            SELECT
                trade_date,
                symbol,
                name,
                rps50, rps120, rps250,
                h_div_hhv150,
                formula_version,
                join_date,
                consecutive_days,
                total_days_60d,
                enter_pool_count_60d,
                last_exit_date,
                close_bfq,
                floatmv,
                change_pct,
                turnover
            FROM _szh_batch
        """)
        con.unregister("_szh_batch")
        total += len(merged)

    return total
