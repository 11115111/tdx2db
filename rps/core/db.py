import duckdb
from pathlib import Path

_SQL_CREATE = (Path(__file__).parent.parent / "sql" / "01_create_tables.sql").read_text()


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    return con


def init_tables(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in _SQL_CREATE.split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)


def refresh_block_member_count(con: duckdb.DuckDBPyConnection) -> int:
    """Recount members per block from raw_tdx_blocks_member and upsert cache.

    Call this after block data is synced. Returns number of blocks updated.
    """
    con.execute("""
        INSERT OR REPLACE INTO block_member_count (block_code, member_count, updated_at)
        SELECT block_code, COUNT(*) AS member_count, current_timestamp
        FROM raw_tdx_blocks_member
        GROUP BY block_code
    """)
    row = con.execute("SELECT COUNT(*) FROM block_member_count").fetchone()
    return row[0] if row else 0
