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
