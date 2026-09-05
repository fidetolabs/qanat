-- raw -> normalized. The API returns one column per currency; a table with one
-- row per currency per day is what every step after this one wants.
--
-- The API quotes "currency per 1 USD". A portfolio holds the currency, so the
-- price of that holding is the other way up: 1 / rate, in USD.
SELECT
    CAST(date AS DATE)   AS date,
    symbol,
    1.0 / rate           AS close,
    rate                 AS rate_per_usd
FROM (
    UNPIVOT raw__fx_wide
    ON COLUMNS(* EXCLUDE (date))
    INTO NAME symbol VALUE rate
)
WHERE rate IS NOT NULL AND rate > 0
