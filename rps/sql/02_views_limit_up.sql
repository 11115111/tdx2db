-- 涨停派生视图 (基于 change_pct 近似，无分时数据)
-- 精确涨停判断需 preclose * 涨停比例 == close，这里用阈值近似

CREATE OR REPLACE VIEW v_limit_up_daily AS
SELECT
    b.symbol,
    b.date AS trade_date,
    n.name,
    b.change_pct,
    b.close,
    b.turnover,
    CASE
        WHEN b.symbol LIKE '688%' AND b.change_pct >= 19.5 THEN '科创涨停'
        WHEN b.symbol LIKE '3%'   AND b.change_pct >= 19.5 THEN '创业涨停'
        WHEN b.symbol NOT LIKE '688%'
         AND b.symbol NOT LIKE '3%'
         AND b.symbol NOT LIKE '8%'
         AND b.change_pct >= 9.7                            THEN '主板涨停'
        ELSE NULL
    END AS limit_status
FROM raw_basic_daily b
LEFT JOIN raw_symbol_name  n ON n.symbol = b.symbol
JOIN      raw_symbol_class c ON c.symbol = b.symbol AND c.class = 'stock'
WHERE c.symbol IS NOT NULL;

-- 连板派生视图
CREATE OR REPLACE VIEW v_lianban_daily AS
WITH lu_marked AS (
    SELECT
        symbol, trade_date,
        CASE WHEN limit_status IS NOT NULL THEN 1 ELSE 0 END AS is_lu
    FROM v_limit_up_daily
),
streak AS (
    SELECT
        symbol, trade_date, is_lu,
        SUM(CASE WHEN is_lu = 0 THEN 1 ELSE 0 END)
            OVER (PARTITION BY symbol ORDER BY trade_date) AS group_id
    FROM lu_marked
)
SELECT
    symbol,
    trade_date,
    is_lu,
    CASE WHEN is_lu = 1
         THEN ROW_NUMBER() OVER (PARTITION BY symbol, group_id ORDER BY trade_date)
         ELSE 0
    END AS lianban_count
FROM streak;
