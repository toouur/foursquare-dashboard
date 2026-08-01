-- Drop the second copy of four check-ins the July 2026 syncs inserted twice.
--
-- checkins has no UNIQUE on id (seq is the PK) so the CSV's own duplicate rows
-- survive — which also means INSERT OR IGNORE cannot reject a repeat. Two syncs
-- that both read "this id is not in D1 yet" before either committed will each
-- insert a row. update-dashboard runs hourly and recheck-enrich syncs D1 too,
-- so an overlap is the likely origin.
--
-- Verified against the data repo first: these four ids appear ONCE in
-- checkins.csv but TWICE in D1. The other twelve duplicated ids are legitimate
-- (duplicated in the source as well) and are deliberately left alone.
--
-- Keeps the lowest seq per id, i.e. the row that was inserted first.

SELECT id, COUNT(*) AS copies FROM checkins
WHERE  id IN ('6a5be7db967f10482d3415b8','6a5d52bf218af00835e2de40',
              '6a6479b859f475455a9def47','6a6479d57bc39c7398aebf6a')
GROUP BY id;

DELETE FROM checkins
WHERE  seq IN (
    SELECT MAX(seq) FROM checkins
    WHERE  id IN ('6a5be7db967f10482d3415b8','6a5d52bf218af00835e2de40',
                  '6a6479b859f475455a9def47','6a6479d57bc39c7398aebf6a')
    GROUP  BY id
    HAVING COUNT(*) > 1
);

-- Expect one row each now, and 69388 total against 69376 distinct ids
-- (the twelve legitimate source duplicates).
SELECT id, COUNT(*) AS copies FROM checkins
WHERE  id IN ('6a5be7db967f10482d3415b8','6a5d52bf218af00835e2de40',
              '6a6479b859f475455a9def47','6a6479d57bc39c7398aebf6a')
GROUP BY id;

SELECT COUNT(*) AS rows_total, COUNT(DISTINCT id) AS ids_distinct FROM checkins;
