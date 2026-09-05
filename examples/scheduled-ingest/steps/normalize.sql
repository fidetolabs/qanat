-- raw -> normalized. Type it, dedupe it, conform it to one key set.
-- Tables are addressed as `stage__table`.
SELECT
    CAST(ts AS DATE)        AS date,
    symbol,
    CAST(open   AS DOUBLE)  AS open,
    CAST(high   AS DOUBLE)  AS high,
    CAST(low    AS DOUBLE)  AS low,
    CAST(close  AS DOUBLE)  AS close,
    CAST(volume AS BIGINT)  AS volume
FROM raw__daily_prices
QUALIFY row_number() OVER (PARTITION BY symbol, CAST(ts AS DATE) ORDER BY ts DESC) = 1
