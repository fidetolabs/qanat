-- Sentiment, collapsed to one row per symbol.
SELECT
    symbol,
    max(ts)              AS as_of,
    avg(sentiment)       AS tone,
    count(*)             AS articles
FROM raw__news
GROUP BY symbol
