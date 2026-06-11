"""三线红榜单可视化 — Streamlit app.

Run from repo root:
    streamlit run rps/ui/streamlit_app.py -- --db /path/to/your.duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="三线红榜单",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# DB connection — cached per db path
# ---------------------------------------------------------------------------

@st.cache_resource
def get_con(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


def _db_path_from_args() -> str | None:
    """Read --db argument from streamlit's extra args (after --)."""
    args = sys.argv
    try:
        idx = args.index("--db")
        return args[idx + 1]
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_dates(_con_id: int, db_path: str) -> list[str]:
    con = get_con(db_path)
    rows = con.execute(
        "SELECT DISTINCT trade_date FROM sanxianhong_daily ORDER BY trade_date DESC LIMIT 120"
    ).fetchall()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=300)
def load_blocks(_con_id: int, db_path: str, trade_date: str, version: str) -> list[str]:
    """Return sorted list of blocks that contain stocks on this date."""
    con = get_con(db_path)
    try:
        rows = con.execute("""
            SELECT DISTINCT bi.block_name
            FROM sanxianhong_daily s
            JOIN raw_tdx_blocks_member bm ON bm.stock_symbol = s.symbol
            JOIN raw_tdx_blocks_info   bi ON bi.block_code   = bm.block_code
            WHERE s.trade_date = $1 AND s.formula_version = $2
            ORDER BY bi.block_name
        """, [trade_date, version]).fetchall()
        return ["全部"] + [r[0] for r in rows]
    except Exception:
        return ["全部"]


@st.cache_data(ttl=300)
def load_sanxianhong(
    _con_id: int,
    db_path: str,
    trade_date: str,
    version: str,
    block_filter: str | None,
) -> pd.DataFrame:
    con = get_con(db_path)

    block_join = ""
    block_where = ""
    if block_filter and block_filter != "全部":
        block_join = """
            JOIN raw_tdx_blocks_member bm ON bm.stock_symbol = s.symbol
            JOIN raw_tdx_blocks_info   bi ON bi.block_code   = bm.block_code
        """
        block_where = f"AND bi.block_name = '{block_filter.replace(chr(39), chr(39)+chr(39))}'"

    sql = f"""
        SELECT
            s.symbol                                    AS 代码,
            s.name                                      AS 名称,
            s.rps50                                     AS RPS50,
            s.rps120                                    AS RPS120,
            s.rps250                                    AS RPS250,
            ROUND(s.h_div_hhv150, 3)                    AS 近高比,
            s.consecutive_days                          AS 连续天数,
            s.total_days_60d                            AS "60日在榜",
            s.enter_pool_count_60d                      AS 上榜次数,
            s.join_date                                 AS 本轮入榜,
            s.last_exit_date                            AS 上次离场,
            ROUND(s.close_bfq, 2)                       AS 现价,
            ROUND(s.change_pct, 2)                      AS 涨跌幅,
            ROUND(s.turnover, 2)                        AS 换手率,
            ROUND(s.floatmv / 1e8, 1)                   AS 流通市值亿
        FROM sanxianhong_daily s
        {block_join}
        WHERE s.trade_date = $1
          AND s.formula_version = $2
          {block_where}
        ORDER BY s.consecutive_days DESC, s.rps50 DESC
    """
    try:
        df = con.execute(sql, [trade_date, version]).df()
    except Exception as e:
        st.error(f"查询失败: {e}")
        df = pd.DataFrame()

    if block_filter and block_filter != "全部" and not df.empty:
        # deduplicate if a stock belongs to the same block multiple times
        df = df.drop_duplicates(subset=["代码"])

    return df


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📈 三线红榜单")

    db_path = _db_path_from_args()

    # Sidebar — DB path input if not passed via --db
    with st.sidebar:
        st.header("数据源")
        if db_path:
            st.success(f"已连接: `{db_path}`")
        else:
            db_path = st.text_input("DuckDB 文件路径", placeholder="/path/to/your.duckdb")
        if not db_path:
            st.info("请输入数据库路径或通过 `-- --db /path/to/db` 启动")
            return

        st.divider()
        st.header("筛选")

    # Attempt DB connection
    try:
        con = get_con(db_path)
        con_id = id(con)
    except Exception as e:
        st.error(f"无法连接数据库: {e}")
        return

    # Available dates
    dates = load_dates(con_id, db_path)
    if not dates:
        st.warning("sanxianhong_daily 暂无数据，请先运行 `--init-history`")
        return

    with st.sidebar:
        selected_date = st.selectbox("日期", dates, index=0)
        version = st.selectbox("版本", ["strict", "loose"], index=0)
        blocks = load_blocks(con_id, db_path, selected_date, version)
        selected_block = st.selectbox("板块筛选", blocks, index=0)

        st.divider()
        sort_col = st.selectbox(
            "排序列",
            ["连续天数", "60日在榜", "上榜次数", "RPS50", "RPS120", "RPS250", "近高比", "流通市值亿", "涨跌幅", "换手率"],
            index=0,
        )
        sort_asc = st.checkbox("升序", value=False)

    # Load data
    df = load_sanxianhong(con_id, db_path, selected_date, version, selected_block)

    if df.empty:
        st.info(f"{selected_date} 暂无三线红数据")
        return

    # Sort
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=sort_asc)

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("在榜股票数", len(df))
    col2.metric("平均连续天数", f"{df['连续天数'].mean():.1f}")
    col3.metric("平均RPS50", f"{df['RPS50'].mean():.1f}")
    col4.metric("平均流通市值(亿)", f"{df['流通市值亿'].mean():.1f}" if df['流通市值亿'].notna().any() else "N/A")

    st.divider()

    # Main table
    st.dataframe(
        df.reset_index(drop=True),
        use_container_width=True,
        height=600,
        column_config={
            "代码":     st.column_config.TextColumn("代码", width="small"),
            "名称":     st.column_config.TextColumn("名称", width="medium"),
            "RPS50":    st.column_config.NumberColumn("RPS50",  format="%d"),
            "RPS120":   st.column_config.NumberColumn("RPS120", format="%d"),
            "RPS250":   st.column_config.NumberColumn("RPS250", format="%d"),
            "近高比":   st.column_config.NumberColumn("近高比", format="%.3f"),
            "连续天数": st.column_config.NumberColumn("连续天数", format="%d"),
            "60日在榜": st.column_config.NumberColumn("60日在榜", format="%d"),
            "上榜次数": st.column_config.NumberColumn("上榜次数", format="%d"),
            "本轮入榜": st.column_config.DateColumn("本轮入榜"),
            "上次离场": st.column_config.DateColumn("上次离场"),
            "现价":     st.column_config.NumberColumn("现价",   format="%.2f"),
            "涨跌幅":   st.column_config.NumberColumn("涨跌幅", format="%.2f%%"),
            "换手率":   st.column_config.NumberColumn("换手率", format="%.2f%%"),
            "流通市值亿": st.column_config.NumberColumn("流通市值(亿)", format="%.1f"),
        },
        hide_index=True,
    )

    # Download
    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        label="下载 CSV",
        data=csv,
        file_name=f"sanxianhong_{selected_date}.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
