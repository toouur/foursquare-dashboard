-- Read-only. Find check-in ids that D1 stores more times than the data repo has.
--
-- checkins.id is deliberately NOT unique (seq is the primary key) so the CSV's
-- own duplicate rows survive the sync. That also means a re-insert silently
-- adds a row: the append-only path skips ids already present, but a bulk resync
-- or an interrupted batch can land the same row twice.
--
-- The 1 Aug drift (+4) was exactly this: 69375 distinct ids on both sides, but
-- 69391 rows in D1 against 69387 in the source. Nothing is missing and nothing
-- is stale — four rows are simply stored twice.

-- Ids stored more than once, worst first.
SELECT   id, COUNT(*) AS copies, MIN(date) AS ts, MIN(venue) AS venue
FROM     checkins
GROUP BY id
HAVING   COUNT(*) > 1
ORDER BY copies DESC, ts
LIMIT    40;

-- Totals, so the surplus can be read off directly.
SELECT COUNT(*) AS rows_total, COUNT(DISTINCT id) AS ids_distinct
FROM   checkins;
