-- Reference SQL for stock RPS calculation (not executed directly by Python)
-- Python embeds equivalent logic as strings in rps_calculator.py

-- Step 1: compute per-symbol N-day returns using LAG on qfq-adjusted close
WITH returns AS (
    SELECT
        q.date,
        q.symbol,
        q.close                                                              AS close_qfq,
        (q.close / NULLIF(LAG(q.close, 5)   OVER w, 0) - 1) * 100          AS pct_5d,
        (q.close / NULLIF(LAG(q.close, 10)  OVER w, 0) - 1) * 100          AS pct_10d,
        (q.close / NULLIF(LAG(q.close, 20)  OVER w, 0) - 1) * 100          AS pct_20d,
        (q.close / NULLIF(LAG(q.close, 50)  OVER w, 0) - 1) * 100          AS pct_50d,
        (q.close / NULLIF(LAG(q.close, 120) OVER w, 0) - 1) * 100          AS pct_120d,
        (q.close / NULLIF(LAG(q.close, 250) OVER w, 0) - 1) * 100          AS pct_250d,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 59  PRECEDING AND CURRENT ROW)                     AS hhv60_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 149 PRECEDING AND CURRENT ROW)                     AS hhv150_qfq,
        MAX(q.high) OVER (PARTITION BY q.symbol ORDER BY q.date
            ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)                     AS hhv250_qfq
    FROM v_stock_qfq q
    JOIN raw_symbol_class s ON s.symbol = q.symbol
    LEFT JOIN raw_symbol_name n ON n.symbol = q.symbol
    WHERE s.class = 'stock'
      AND COALESCE(n.name, '') NOT LIKE '%ST%'
      AND COALESCE(n.name, '') NOT LIKE '%退%'
      AND s.symbol NOT LIKE '8%'
      AND s.symbol NOT LIKE '4%'
      AND s.symbol NOT LIKE '9%'
    WINDOW w AS (PARTITION BY q.symbol ORDER BY q.date)
),
-- Step 2: rank returns cross-sectionally per date
ranked AS (
    SELECT
        r.*,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_5d   NULLS FIRST) * 100) AS rps5,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_10d  NULLS FIRST) * 100) AS rps10,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_20d  NULLS FIRST) * 100) AS rps20,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_50d  NULLS FIRST) * 100) AS rps50,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_120d NULLS FIRST) * 100) AS rps120,
        floor(PERCENT_RANK() OVER (PARTITION BY r.date ORDER BY r.pct_250d NULLS FIRST) * 100) AS rps250
    FROM returns r
    WHERE r.pct_5d IS NOT NULL
)
SELECT
    ranked.date        AS trade_date,
    ranked.symbol,
    n.name,
    ranked.rps5, ranked.rps10, ranked.rps20, ranked.rps50, ranked.rps120, ranked.rps250,
    ranked.pct_5d, ranked.pct_10d, ranked.pct_20d, ranked.pct_50d, ranked.pct_120d, ranked.pct_250d,
    ranked.close_qfq,
    ranked.hhv60_qfq, ranked.hhv150_qfq, ranked.hhv250_qfq,
    ranked.high_qfq / NULLIF(ranked.hhv150_qfq, 0) AS h_div_hhv150,
    ranked.high_qfq / NULLIF(ranked.hhv250_qfq, 0) AS h_div_hhv250,
    b.close            AS close_bfq,
    b.floatmv, b.totalmv, b.turnover, b.amount, b.change_pct
FROM ranked
LEFT JOIN raw_symbol_name n  ON n.symbol = ranked.symbol
LEFT JOIN v_stock_bfq b      ON b.symbol = ranked.symbol AND b.date = ranked.date
WHERE ranked.date = $target_date;
