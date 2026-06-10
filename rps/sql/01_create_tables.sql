-- 板块成员数缓存，随 raw_tdx_blocks_member 更新时刷新，供 RPS 查询过滤大板块
CREATE TABLE IF NOT EXISTS block_member_count (
    block_code   VARCHAR PRIMARY KEY,
    member_count INTEGER NOT NULL,
    updated_at   TIMESTAMP DEFAULT current_timestamp
);

-- 合格股票池缓存：排除 ST/退市/北交所/B股，随股票名称/分类数据更新时刷新
CREATE TABLE IF NOT EXISTS stock_pool (
    symbol VARCHAR PRIMARY KEY,
    name   VARCHAR
);

-- 板块每日涨跌幅缓存：每日 raw_basic_daily 写入后刷新一次
-- block RPS 的多周期复合收益率仅基于此表做窗口函数，无需再扫大表
CREATE TABLE IF NOT EXISTS block_daily_pct (
    trade_date    DATE    NOT NULL,
    block_code    VARCHAR NOT NULL,
    block_name    VARCHAR,
    block_type    VARCHAR,
    block_pct_1d  DOUBLE,
    member_count  INTEGER,
    rising_count  INTEGER,
    limit_up_count INTEGER,
    PRIMARY KEY (trade_date, block_code)
);

CREATE TABLE IF NOT EXISTS rps_stock_daily (
    trade_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    rps5 INTEGER, rps10 INTEGER, rps20 INTEGER, rps50 INTEGER, rps120 INTEGER, rps250 INTEGER,
    pct_5d DOUBLE, pct_10d DOUBLE, pct_20d DOUBLE, pct_50d DOUBLE, pct_120d DOUBLE, pct_250d DOUBLE,
    close_qfq DOUBLE,
    hhv60_qfq DOUBLE, hhv150_qfq DOUBLE, hhv250_qfq DOUBLE,
    h_div_hhv150 DOUBLE, h_div_hhv250 DOUBLE,
    close_bfq DOUBLE, floatmv DOUBLE, totalmv DOUBLE, turnover DOUBLE, amount DOUBLE, change_pct DOUBLE,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS rps_block_daily (
    trade_date DATE NOT NULL,
    block_code VARCHAR NOT NULL,
    block_name VARCHAR,
    block_type VARCHAR,
    bkrps5 INTEGER, bkrps10 INTEGER, bkrps15 INTEGER, bkrps20 INTEGER, bkrps50 INTEGER,
    block_pct_1d DOUBLE, block_pct_5d DOUBLE, block_pct_10d DOUBLE, block_pct_20d DOUBLE,
    member_count INTEGER, rising_count INTEGER, limit_up_count INTEGER,
    PRIMARY KEY (trade_date, block_code)
);

CREATE TABLE IF NOT EXISTS sanxianhong_daily (
    trade_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    name VARCHAR,
    rps50 INTEGER, rps120 INTEGER, rps250 INTEGER,
    h_div_hhv150 DOUBLE,
    formula_version VARCHAR,
    join_date DATE, consecutive_days INTEGER, total_days_60d INTEGER,
    enter_pool_count_60d INTEGER, last_exit_date DATE,
    close_bfq DOUBLE, floatmv DOUBLE, change_pct DOUBLE, turnover DOUBLE,
    PRIMARY KEY (trade_date, symbol, formula_version)
);
