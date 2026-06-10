"""Daily RPS pipeline entry point.

Cache refresh order (run once after each upstream data sync):
    --refresh-blocks   → block_member_count + stock_pool (after symbol/block data sync)
    --refresh-bfq      → block_daily_pct history (after full history backfill)

Normal daily run:
    python -m rps.cli.run_daily --db your.duckdb --date 2026-06-10

Full history init:
    python -m rps.cli.run_daily --db your.duckdb --init-history
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rps.core.db import get_connection, init_tables, refresh_block_member_count, refresh_stock_pool
from rps.core.rps_calculator import (
    calc_block_daily_pct,
    calc_block_daily_pct_history,
    calc_stock_rps,
    calc_stock_rps_history,
    calc_block_rps,
    calc_block_rps_history,
)
from rps.core.sanxianhong import calc_sanxianhong, calc_sanxianhong_history

_DEFAULT_CFG = Path(__file__).parent.parent / "config" / "thresholds.yaml"


def _load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _ensure_static_caches(con) -> None:
    """Auto-populate static caches on first run if empty."""
    if con.execute("SELECT COUNT(*) FROM block_member_count").fetchone()[0] == 0:
        click.echo("[cache] block_member_count empty, refreshing...")
        click.echo(f"  {refresh_block_member_count(con)} blocks cached")

    if con.execute("SELECT COUNT(*) FROM stock_pool").fetchone()[0] == 0:
        click.echo("[cache] stock_pool empty, refreshing...")
        click.echo(f"  {refresh_stock_pool(con)} symbols cached")


@click.command()
@click.option("--db", required=True, help="Path to DuckDB file")
@click.option("--date", "target_date", default=None, help="Target date YYYY-MM-DD (default: latest in DB)")
@click.option("--init-history", is_flag=True, help="Compute full history instead of single date")
@click.option("--start", "start_date", default=None, help="History start date (used with --init-history)")
@click.option("--end", "end_date", default=None, help="History end date (used with --init-history)")
@click.option("--cfg", "cfg_path", default=str(_DEFAULT_CFG), help="Path to thresholds.yaml")
@click.option("--skip-sanxianhong", is_flag=True, help="Skip 三线红 step")
@click.option("--refresh-blocks", is_flag=True, help="Refresh static caches (block_member_count + stock_pool) then exit")
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
        click.echo(f"[block_member_count] {refresh_block_member_count(con)} blocks")
        click.echo(f"[stock_pool]         {refresh_stock_pool(con)} symbols")
        con.close()
        return

    _ensure_static_caches(con)

    if init_history:
        if not start_date:
            row = con.execute("SELECT MIN(date) FROM raw_kline_daily").fetchone()
            start_date = str(row[0]) if row and row[0] else "2010-01-01"
        if not end_date:
            row = con.execute("SELECT MAX(date) FROM raw_kline_daily").fetchone()
            end_date = str(row[0]) if row and row[0] else target_date

        click.echo(f"[block_daily_pct] history {start_date} → {end_date}")
        n = calc_block_daily_pct_history(con, start_date, end_date)
        click.echo(f"  {n} rows")

        click.echo(f"[stock RPS] history {start_date} → {end_date}")
        n = calc_stock_rps_history(con, start_date, end_date)
        click.echo(f"  {n} rows into rps_stock_daily")

        click.echo(f"[block RPS] history {start_date} → {end_date} (max_member={max_member})")
        n = calc_block_rps_history(con, start_date, end_date, max_member_count=max_member)
        click.echo(f"  {n} rows into rps_block_daily")

        if not skip_sanxianhong:
            click.echo(f"[三线红] history {start_date} → {end_date} (single pass)")
            n = calc_sanxianhong_history(con, start_date, end_date, szh_cfg)
            click.echo(f"  {n} rows")
    else:
        if not target_date:
            row = con.execute("SELECT MAX(date) FROM raw_kline_daily").fetchone()
            target_date = str(row[0]) if row and row[0] else None
        if not target_date:
            click.echo("No target date and no data in DB", err=True)
            raise SystemExit(1)

        click.echo(f"[block_daily_pct] {target_date}")
        n = calc_block_daily_pct(con, target_date)
        click.echo(f"  {n} blocks")

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
