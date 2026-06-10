"""Daily RPS pipeline entry point.

Usage:
    python -m rps.cli.run_daily --db /path/to/db.duckdb --date 2026-06-10
    python -m rps.cli.run_daily --db /path/to/db.duckdb --init-history
    python -m rps.cli.run_daily --db /path/to/db.duckdb --init-history --start 2020-01-01 --end 2024-12-31

Block member count cache must be populated before running RPS:
    python -m rps.cli.run_daily --db /path/to/db.duckdb --refresh-blocks
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rps.core.db import get_connection, init_tables, refresh_block_member_count
from rps.core.rps_calculator import (
    calc_stock_rps,
    calc_stock_rps_history,
    calc_block_rps,
    calc_block_rps_history,
)
from rps.core.sanxianhong import calc_sanxianhong

_DEFAULT_CFG = Path(__file__).parent.parent / "config" / "thresholds.yaml"


def _load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


@click.command()
@click.option("--db", required=True, help="Path to DuckDB file")
@click.option("--date", "target_date", default=None, help="Target date YYYY-MM-DD (default: latest in DB)")
@click.option("--init-history", is_flag=True, help="Compute full history instead of single date")
@click.option("--start", "start_date", default=None, help="History start date (used with --init-history)")
@click.option("--end", "end_date", default=None, help="History end date (used with --init-history)")
@click.option("--cfg", "cfg_path", default=str(_DEFAULT_CFG), help="Path to thresholds.yaml")
@click.option("--skip-sanxianhong", is_flag=True, help="Skip 三线红 step")
@click.option("--refresh-blocks", is_flag=True, help="Refresh block_member_count cache then exit")
def main(
    db: str,
    target_date: str | None,
    init_history: bool,
    start_date: str | None,
    end_date: str | None,
    cfg_path: str,
    skip_sanxianhong: bool,
    refresh_blocks: bool,
) -> None:
    cfg = _load_cfg(Path(cfg_path))
    szh_cfg = cfg["sanxianhong"]
    max_member = cfg.get("block_rps", {}).get("max_member_count", 100)

    con = get_connection(db)
    init_tables(con)

    if refresh_blocks:
        n = refresh_block_member_count(con)
        click.echo(f"[block_member_count] refreshed {n} blocks")
        con.close()
        return

    # Ensure cache exists before any block RPS query
    cache_empty = con.execute("SELECT COUNT(*) FROM block_member_count").fetchone()[0] == 0
    if cache_empty:
        click.echo("[block_member_count] cache empty, refreshing...")
        n = refresh_block_member_count(con)
        click.echo(f"  {n} blocks cached")

    if init_history:
        if not start_date:
            row = con.execute("SELECT MIN(date) FROM raw_kline_daily").fetchone()
            start_date = str(row[0]) if row and row[0] else "2010-01-01"
        if not end_date:
            row = con.execute("SELECT MAX(date) FROM raw_kline_daily").fetchone()
            end_date = str(row[0]) if row and row[0] else target_date

        click.echo(f"[stock RPS] history {start_date} → {end_date}")
        n = calc_stock_rps_history(con, start_date, end_date)
        click.echo(f"  {n} rows into rps_stock_daily")

        click.echo(f"[block RPS] history {start_date} → {end_date} (max_member={max_member})")
        n = calc_block_rps_history(con, start_date, end_date, max_member_count=max_member)
        click.echo(f"  {n} rows into rps_block_daily")

        if not skip_sanxianhong:
            dates_sql = f"""
            SELECT DISTINCT trade_date FROM rps_stock_daily
            WHERE trade_date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY trade_date
            """
            dates = [str(r[0]) for r in con.execute(dates_sql).fetchall()]
            click.echo(f"[三线红] computing for {len(dates)} dates")
            for d in dates:
                calc_sanxianhong(con, d, szh_cfg)
            click.echo("  done")
    else:
        if not target_date:
            row = con.execute("SELECT MAX(date) FROM raw_kline_daily").fetchone()
            target_date = str(row[0]) if row and row[0] else None
        if not target_date:
            click.echo("No target date and no data in DB", err=True)
            raise SystemExit(1)

        click.echo(f"[stock RPS] {target_date}")
        n = calc_stock_rps(con, target_date)
        click.echo(f"  {n} rows")

        click.echo(f"[block RPS] {target_date} (max_member={max_member})")
        n = calc_block_rps(con, target_date, max_member_count=max_member)
        click.echo(f"  {n} rows")

        if not skip_sanxianhong:
            click.echo(f"[三线红] {target_date}")
            n = calc_sanxianhong(con, target_date, szh_cfg)
            click.echo(f"  {n} rows")

    con.close()
    click.echo("done.")


if __name__ == "__main__":
    main()
