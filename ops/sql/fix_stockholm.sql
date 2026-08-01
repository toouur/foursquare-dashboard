-- Repair the Adolf Fredriks kyrkogård check-in in D1.
--
-- detect_merges reported a phantom merge (kyrkogård -> kyrka): both check-ins
-- share unix second 1519338605 and the {ts: venue_id} map kept only the later
-- row, so the churchyard looked absent from the new snapshot. sync_to_d1 acted
-- on it — reassigning venue_id and copying the church's denormalized display
-- columns over the churchyard's own.
--
-- Values below come from checkins.csv, which was never wrong.
--   npx wrangler d1 execute swarmdata --file="fix_stockholm.sql" --remote

-- 1. Before — expect venue_id = 4adcdaeef964a520ae5a21e3 (the church): the damage.
SELECT id, venue_id, venue, category, city, country
FROM   checkins
WHERE  id = '5a8f446db1ec136ea90245a7';

-- 2. Put the check-in back on its own venue.
UPDATE checkins
SET    venue_id     = '4ddf95ef18388714bb6b061d',
       venue        = 'Adolf Fredriks kyrkogård',
       venue_url    = 'https://foursquare.com/v/4ddf95ef18388714bb6b061d',
       category     = 'Cemetery',
       city         = 'Stockholm',
       state        = 'Stockholm',
       country      = 'Sweden',
       lat          = 59.33786599725621,
       lng          = 18.06060954187381
WHERE  id = '5a8f446db1ec136ea90245a7';

-- 3. Re-create the venue row if an archive run pruned it as an orphan.
INSERT OR IGNORE INTO venues
       (id, name, category, lat, lng, city, country,
        checkin_count, first_checkin_at, last_checkin_at)
VALUES ('4ddf95ef18388714bb6b061d', 'Adolf Fredriks kyrkogård', 'Cemetery',
        59.33786599725621, 18.06060954187381, 'Stockholm', 'Sweden',
        1, 1519338605, 1519338605);

-- 4. Drop the phantom from the audit trail so it stops reading as real history.
DELETE FROM venue_changes
WHERE  venue_id  = '4ddf95ef18388714bb6b061d'
  AND  field     = 'venue_id'
  AND  new_value = '4adcdaeef964a520ae5a21e3';

-- 5. After — one check-in per venue, both at 1519338605.
SELECT venue_id, venue, date, id
FROM   checkins
WHERE  venue_id IN ('4ddf95ef18388714bb6b061d', '4adcdaeef964a520ae5a21e3')
ORDER  BY venue_id;

-- 6. Re-index search so the churchyard comes back in /api/search.
INSERT INTO venues_fts(venues_fts) VALUES('rebuild');
