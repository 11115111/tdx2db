# RPS 项目开发文档

A股情绪周期 RPS 量化系统，基于 tdx2db 写入的 DuckDB 数据库。

## 项目结构

```
rps/
├── cli/run_daily.py          # 入口：每日更新 / 历史回填
├── config/thresholds.yaml    # 三线红阈值配置
├── core/
│   ├── db.py                 # 表初始化、stock_pool、block_member_count
│   ├── rps_calculator.py     # RPS 计算（股票 + 板块）
│   └── sanxianhong.py        # 三线红榜单计算
├── sql/
│   ├── 01_create_tables.sql  # 建表 DDL
│   ├── 02_views_limit_up.sql # 涨停/连板视图
│   └── 10_calc_rps_stock.sql # 参考 SQL（不直接执行）
├── tests/test_rps_calc.py    # 单元测试
└── ui/streamlit_app.py       # 可视化界面
```

## 依赖的上游表（由 tdx2db 写入，只读）

| 表/视图 | 说明 |
|---------|------|
| `v_stock_qfq` | 前复权行情视图（open/high/low/close 已调整） |
| `v_stock_bfq` | 不复权行情视图（用于展示现价） |
| `raw_kline_daily` | 原始日K线 |
| `raw_basic_daily` | 基本行情（turnover/floatmv/change_pct 等） |
| `raw_adjust_factor` | 复权因子 |
| `raw_symbol_class` | 股票分类（stock/etf 等） |
| `raw_symbol_name` | 股票名称（退市股不在此表） |
| `raw_tdx_blocks_info` | 板块信息（block_type/block_level） |
| `raw_tdx_blocks_member` | 板块成员 |

## 本项目维护的表

| 表 | 说明 | 刷新时机 |
|----|------|---------|
| `stock_pool` | 合格股票池 | 每次运行自动刷新 |
| `block_member_count` | 板块成员数缓存 | 每次运行自动刷新 |
| `block_daily_pct` | 板块每日涨跌幅缓存 | 每次运行 |
| `rps_stock_daily` | 个股每日 RPS | 每次运行 |
| `rps_block_daily` | 板块每日 RPS | 每次运行 |
| `sanxianhong_daily` | 三线红榜单 | 每次运行 |

## stock_pool 过滤规则

```python
# db.py refresh_stock_pool()
# INNER JOIN raw_symbol_name → 自动排除完全退市股（不在该表中）
# 保留：ST股、名称含"退"的退市整理股、北交所(8x)
# 排除：B股(9x)、三板(4x)
SELECT s.symbol, n.name
FROM raw_symbol_class s
JOIN raw_symbol_name n ON n.symbol = s.symbol   -- INNER JOIN 排除完全退市
WHERE s.class = 'stock'
  AND s.symbol NOT LIKE '4%'
  AND s.symbol NOT LIKE '9%'
```

三线红内部会再过滤 ST/退/退市（`name IS NOT NULL AND name NOT LIKE '%ST%' AND name NOT LIKE '%退%'`）。

## RPS 计算要点

### 收益率
```sql
pct_Nd = (close_t / LAG(close, N) OVER (PARTITION BY symbol ORDER BY date) - 1) * 100
```
- 用前复权收盘价（`v_stock_qfq`）
- LAG(N) = N 个交易日前收盘，数据只有交易日故天数计算正确

### 独立 per-period 排名（重要！）
上市不足 N 天的股票，pct_Nd = NULL，不能参与 N 周期排名。
用 `PARTITION BY (date, pct_Nd IS NOT NULL)` 把 NULL 和非 NULL 分到不同 bucket，避免短历史股票稀释分母：

```sql
CASE WHEN r.pct_50d IS NOT NULL
     THEN CAST(PERCENT_RANK() OVER (
              PARTITION BY r.date, (r.pct_50d IS NOT NULL)
              ORDER BY r.pct_50d) * 99 AS INTEGER) END AS rps50
```

### 近高比（h_div_hhv150 / h_div_hhv250）
```sql
-- 当日最高价 / 周期最高价（注意：用 high_qfq，不是 close）
high_qfq / NULLIF(hhv150_qfq, 0) AS h_div_hhv150
```
`hhv150_qfq = MAX(high) OVER (ROWS BETWEEN 149 PRECEDING AND CURRENT ROW)`

## 三线红计算要点

### gap-and-islands 模式（整个区间一条 SQL）

```sql
WITH trading_days AS (
    -- 必须先 DISTINCT 再 ROW_NUMBER，否则窗口在 DISTINCT 之前执行
    -- 产生数百万行笛卡尔爆炸，连续天数变成"几百年"
    SELECT trade_date,
           ROW_NUMBER() OVER (ORDER BY trade_date) AS td_idx
    FROM (SELECT DISTINCT trade_date FROM rps_stock_daily)
),
grouped AS (
    SELECT *,
           td_idx - ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY td_idx) AS run_id
    FROM qualified
    -- run_id 在连续交易日区间内为常数 → 识别每段连续在榜
)
```

### 每日计算 = 历史计算的子集
`calc_sanxianhong(date)` 直接调用 `calc_sanxianhong_history(date, date)`。
窗口函数扫全表，只 INSERT 指定日期的行。保证单日和回填结果完全一致。

### strict vs loose 版本
| 版本 | 条件 | 近高比列 |
|------|------|---------|
| strict | rps50 AND rps120 AND rps250 各自达标 | h_div_hhv150 |
| loose | rps50 OR rps120 OR rps250 任一达标 | h_div_hhv250 |

配置在 `config/thresholds.yaml`。

## 已修复的重要 Bug

1. **连续天数"几百年"**：`SELECT DISTINCT trade_date, ROW_NUMBER()` 窗口在 DISTINCT 前执行，产生爆炸。修复：子查询先 DISTINCT 再编号。

2. **total_days_60d > 60**：同上，td_idx 爆炸导致 RANGE 窗口跨越错误行。

3. **RPS 分数偏低**：短历史股票（pct IS NULL）被算进 PERCENT_RANK 分母，用 `PARTITION BY (IS NOT NULL)` 修复。

4. **退市股进入排名**：LEFT JOIN raw_symbol_name 导致完全退市股（无名称记录）进入 stock_pool。改为 INNER JOIN 修复。

5. **stock_pool 不清除旧数据**：INSERT OR REPLACE 不删除不再满足条件的股票（如新 ST 股）。改为 DELETE + INSERT 修复。

6. **h_div_hhv 用收盘价**：应用当日最高价，改为 `high_qfq / hhv_qfq`。

7. **loose 版本 KeyError**：loose 用 `rps_any_min`，strict 用 `rps50_min`，在 `_build_history_sql` 里按 version 分支处理。

## 运行命令

```bash
# 初始化历史（从根目录执行）
python -m rps.cli.run_daily --db /path/to/your.duckdb --init-history

# 每日更新
python -m rps.cli.run_daily --db /path/to/your.duckdb

# 补算指定区间
python -m rps.cli.run_daily --db /path/to/your.duckdb --init-history --start 2026-01-01 --end 2026-06-10

# 可视化
streamlit run rps/ui/streamlit_app.py -- --db /path/to/your.duckdb
```

## 注意事项

- DuckDB 不支持多进程并发写，JDBC 客户端连接时不能同时运行 Python 写入
- JDBC 驱动版本需与 Python duckdb 包版本一致，否则报元数据错误
- `rps_stock_daily` 有数据变更后需重跑 `--init-history` 刷新 `sanxianhong_daily`
- `v_stock_qfq` 前复权基准是最新因子，历史价格与通达信显示不同属正常，不影响收益率计算
