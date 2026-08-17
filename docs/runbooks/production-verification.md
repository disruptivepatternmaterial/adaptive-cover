# Production Verification Runbook

Post-deploy verification against the live Home Assistant host (BowmanMtn).
A release that changes coordinator/manual-override behavior is not "done"
until these checks pass. All queries were used to diagnose the 2026-07-07
frozen-tracking regression; re-run them as-is.

Recorder is PostgreSQL (container `postgres`, db `homeassistant`). Run via:

```bash
ssh BowmanMtn "docker exec postgres psql -U homeassistant -d homeassistant -tAc \"<SQL>\""
```

## Check 1 — No manual latches from availability blips

A manual latch whose timestamp coincides with an `unavailable -> available`
transition of the same cover is a false positive (the v0.3.10 bug).

Manual latch timestamps (UTC) live in HA storage:

```bash
ssh BowmanMtn 'for f in /home/ntableman/docker/ha/config/.storage/adaptive_cover.*.manual_state; do echo "== $f"; python3 -c "import json;print(json.dumps(json.load(open(\"$f\"))[\"data\"],indent=1))"; done'
```

Availability transitions for a cover:

```sql
SELECT to_char(to_timestamp(s.last_updated_ts) AT TIME ZONE 'America/Denver','MM-DD HH24:MI:SS') AS t, s.state
FROM states s JOIN states_meta m ON s.metadata_id=m.metadata_id
WHERE m.entity_id='cover.library_shades'
  AND s.last_updated_ts > extract(epoch from now()) - 86400*3
ORDER BY s.last_updated_ts;
```

PASS: zero `manual_control: true` entries within ±5 s of an
`unavailable`→available transition over a 3-day window.

## Check 2 — Incremental tracking restored

Physical cover follows the calculated position in multiple steps
(June 20 baseline for the library morning window: 20→29→38→48→58→77→100).

Calculated position:

```sql
SELECT to_char(to_timestamp(s.last_updated_ts) AT TIME ZONE 'America/Denver','MM-DD HH24:MI') AS t, s.state
FROM states s JOIN states_meta m ON s.metadata_id=m.metadata_id
WHERE m.entity_id='sensor.library_shades_cover_position'
  AND s.last_updated_ts > extract(epoch from now()) - 86400
ORDER BY s.last_updated_ts;
```

Physical position (swap entity_id per cover):

```sql
SELECT to_char(to_timestamp(s.last_updated_ts) AT TIME ZONE 'America/Denver','MM-DD HH24:MI') AS t,
  sa.shared_attrs::json->>'current_position' AS pos
FROM states s JOIN states_meta m ON s.metadata_id=m.metadata_id
LEFT JOIN state_attributes sa ON s.attributes_id=sa.attributes_id
WHERE m.entity_id='cover.library_shades'
  AND s.last_updated_ts > extract(epoch from now()) - 86400
ORDER BY s.last_updated_ts;
```

PASS: on a sunny day, the physical cover shows 5+ distinct intermediate
positions during its tracking window (subject to the configured
`delta_position` / `delta_time` gates).

## Check 3 — Manual latches only from real external moves

Latch events and their causes are now logged at INFO:

```bash
ssh BowmanMtn 'grep -E "Manual override (detected|cleared)|Adaptive drive .* skipped" /home/ntableman/docker/ha/config/home-assistant.log | tail -30'
```

Daily latch counts (compare against the pre-fix baseline of 10–47/day):

```sql
SELECT to_char(to_timestamp(s.last_updated_ts) AT TIME ZONE 'America/Denver','MM-DD') AS day, count(*)
FROM states s JOIN states_meta m ON s.metadata_id=m.metadata_id
WHERE m.entity_id LIKE 'binary_sensor.%shades_manual_override%' AND s.state='on'
  AND s.last_updated_ts > extract(epoch from now()) - 86400*7
GROUP BY 1 ORDER BY 1;
```

PASS: every latch in the log names a real external move (e.g. the nightly
bedtime automation), and each clears per its configured duration.

## Schedule

- Day 1 after deploy: run all three checks; on any FAIL, roll back via HACS
  (reinstall the previous release) and open an issue with the query output.
- Day 3: re-run all three; only then mark the release verified.
