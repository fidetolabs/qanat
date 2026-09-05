-- Total return over the lookback window, per currency, on the newest day we hold.
WITH ranked AS (
    SELECT symbol, date, close,
           row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS back
    FROM normalized__fx
)
SELECT
    now_.symbol,
    now_.date                              AS as_of,
    now_.close                             AS close,
    now_.close / then_.close - 1.0         AS momentum
FROM ranked now_
JOIN ranked then_
  ON then_.symbol = now_.symbol AND then_.back = ${lookback}
WHERE now_.back = 1
