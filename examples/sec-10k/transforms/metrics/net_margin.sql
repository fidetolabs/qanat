-- net_margin -- net income over revenue, for the year reported.
--
-- One row per subject per filing date; see pe.sql for why the `:as_of`
-- parameter is gone.
WITH annual AS (
    SELECT subject, concept, value, as_of, period_end
    FROM normalized__facts
    WHERE dims = '{}' AND NOT is_instant
      AND date_diff('day', period_start, period_end) BETWEEN 350 AND 380
), ni AS (
    SELECT subject, as_of, value,
           row_number() OVER (PARTITION BY subject, as_of
                              ORDER BY period_end DESC) AS rn
    FROM annual WHERE concept = 'us-gaap:NetIncomeLoss'
), rev AS (
    -- Whichever revenue tag this filer uses. Preferring the contract tag when
    -- both are present keeps one company on one definition.
    SELECT subject, as_of, value,
           row_number() OVER (PARTITION BY subject, as_of
                              ORDER BY period_end DESC,
                              CASE concept
                                WHEN 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' THEN 0
                                ELSE 1 END) AS rn
    FROM annual
    WHERE concept IN ('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
                      'us-gaap:Revenues')
), reported AS (
    SELECT ni.subject, ni.as_of, ni.value / NULLIF(rev.value, 0) AS value
    FROM ni JOIN rev
      ON rev.subject = ni.subject AND rev.as_of = ni.as_of AND rev.rn = 1
    WHERE ni.rn = 1
),
filing_days AS (SELECT DISTINCT subject, as_of FROM normalized__facts)
SELECT d.subject,
       CAST(d.as_of AS DATE) AS date,
       r.value               AS value
FROM filing_days d
ASOF JOIN reported r ON d.subject = r.subject AND d.as_of >= r.as_of
WHERE r.value IS NOT NULL
