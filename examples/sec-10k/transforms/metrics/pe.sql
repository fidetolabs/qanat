-- pe -- price on the filing date over diluted EPS for the year reported.
--
-- **One row per subject per filing date.** `date` is the day the ratio became
-- true, which is what makes this a series rather than an answer to one call.
--
-- It used to end `AND f.as_of <= :as_of` and collapse to one row per company.
-- That kind stored nothing, so a screen had to run the formula -- and a formula
-- with no date to bind ran over every row, which is how a screen could return a
-- figure that was not public yet. The conversion is mechanical: group by the
-- filing date as well as the subject, then carry the last reported value
-- forward, which is the step function `as_of <= :as_of` produced one date at a
-- time.
--
-- `close_as_printed`, not `close`: the EPS comes from a filing and the SEC never
-- rewrites one, so the price has to be on the same footing.
--
-- `arg_max` rather than `row_number() ... WHERE rn = 1`: that shape feeding an
-- ASOF JOIN trips a DuckDB optimizer assertion (TopNWindowElimination), and
-- this says the same thing -- the value from the longest-dated period this
-- filing reported.
WITH latest AS (
    SELECT f.subject,
           f.as_of,
           arg_max(p.close_as_printed / NULLIF(f.value, 0), f.period_end) AS value
    FROM normalized__facts f
    JOIN normalized__filing_prices p ON p.document = f.document
    WHERE f.concept = 'us-gaap:EarningsPerShareDiluted'
      AND f.dims = '{}' AND NOT f.is_instant
      AND date_diff('day', f.period_start, f.period_end) BETWEEN 350 AND 380
    GROUP BY f.subject, f.as_of
),
filing_days AS (SELECT DISTINCT subject, as_of FROM normalized__facts)
SELECT d.subject,
       CAST(d.as_of AS DATE) AS date,
       r.value               AS value
FROM filing_days d
ASOF JOIN latest r ON d.subject = r.subject AND d.as_of >= r.as_of
WHERE r.value IS NOT NULL
