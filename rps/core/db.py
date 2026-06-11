import duckdb
from pathlib import Path

_SQL_CREATE = (Path(__file__).parent.parent / "sql" / "01_create_tables.sql").read_text(encoding="utf-8")


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path)


def init_tables(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in _SQL_CREATE.split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)


def refresh_block_member_count(con: duckdb.DuckDBPyConnection) -> int:
    """Recount members per block from raw_tdx_blocks_member and upsert cache.

    Call after block data is synced. Returns number of blocks updated.
    """
    con.execute("""
        INSERT OR REPLACE INTO block_member_count (block_code, member_count, updated_at)
        SELECT block_code, COUNT(*) AS member_count, current_timestamp
        FROM raw_tdx_blocks_member
        GROUP BY block_code
    """)
    row = con.execute("SELECT COUNT(*) FROM block_member_count").fetchone()
    return row[0] if row else 0


def refresh_stock_pool(con: duckdb.DuckDBPyConnection) -> int:
    """Rebuild eligible stock pool: excludes B-shares (9x) and 三板 (4x).

    ST, delisted, and BSE (8x) stocks are kept so RPS ranks them fairly.
    Sanxianhong applies its own ST/delisted filter at query time.
    Call after raw_symbol_name or raw_symbol_class is updated. Returns pool size.
    """
    con.execute("DELETE FROM stock_pool")
    con.execute("""
        INSERT INTO stock_pool (symbol, name)
        SELECT s.symbol, n.name
        FROM raw_symbol_class s
        JOIN raw_symbol_name n ON n.symbol = s.symbol
        WHERE s.class = 'stock'
          AND s.symbol NOT LIKE '4%'
          AND s.symbol NOT LIKE '9%'
    """)
    row = con.execute("SELECT COUNT(*) FROM stock_pool").fetchone()
    return row[0] if row else 0
