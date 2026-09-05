-- Realised volatility over ${window} sessions, annualised.
WITH r AS (
    SELECT date, symbol,
           close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1 AS ret
    FROM normalized__prices
)
SELECT
    symbol,
    max(date)                                   AS as_of,
    stddev_samp(ret) * sqrt(252)                AS vol_annual,
    count(*)                                    AS observations
FROM (
    SELECT * FROM r
    QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY date DESC) <= ${window}
)
WHERE ret IS NOT NULL
GROUP BY symbol
