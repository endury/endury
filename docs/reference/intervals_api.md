# intervals.icu API facts

The verified facts `endury/intervals/` is built on: endpoints, field
semantics, the workout text DSL the serializer emits, and the `external_id`
scheme that keys every event we push. Embed no other assumptions. The code
is the source of truth for the exact field lists (`_ACTIVITY_FIELDS` in
`client.py`, the pydantic models in `models.py`).

## Transport

- Base: `https://intervals.icu/api/v1`. OpenAPI spec is public:
  `curl -A "Mozilla/5.0 (endury-dev)" https://intervals.icu/api/v1/docs` (243KB
  OpenAPI 3.0.1; fetch it if you need exact field lists — set a realistic
  User-Agent or Cloudflare may block the request).
- Auth: HTTP Basic, username literally `API_KEY`, password = the key. Athlete id `0`
  = the authenticated athlete. Env: `INTERVALS_API_KEY`, `INTERVALS_ATHLETE_ID`
  (default `"0"`), `INTERVALS_BASE_URL` (override for tests/mocks).
- Rate limits: 5,000/day, 2,500/15min, 10/sec — irrelevant at our volume, but honor
  `429` + `Retry-After` with a single retry.

## Reads

- `GET /athlete/{id}` → profile incl. `sportSettings[]`. Each SportSettings row:
  `types: [..activity type names..]`, `ftp`, `lthr`, `max_hr`, `threshold_pace`
  (**m/s**), `pace_units` (`SECS_100M|SECS_100Y|MINS_KM|MINS_MILE|...`),
  `power_zones`, `hr_zones`, `pace_zones`, zone names. Swim CSS = the Swim row's
  `threshold_pace` (convert m/s → sec/100m = 100/v). Run pace sec/km = 1000/v.
- `GET /athlete/{id}/activities?oldest=&newest=` → huge objects (183 fields);
  always project with `fields=` (the live list is `_ACTIVITY_FIELDS` in
  `client.py` — the device-derived session summary: HR, NP, pace/GAP, IF,
  decoupling, zone times, `interval_summary`). `icu_training_load` is their
  TSS. `paired_event_id` links to a planned event; `compliance` is their
  planned-vs-actual %. `source` (`GARMIN_CONNECT`, `STRAVA`, `UPLOAD`, …)
  and `device_name` say where the file came from: the API terms require
  Garmin attribution wherever Garmin-sourced data is shown (`garminLabel` and
  `GARMIN_NOTE` in `web/`). An activity it does not pass on (`STRAVA` today,
  Garmin possibly next) arrives as a stub with no type or duration, which the
  sheet shows as recorded with its details not shared. `feel` (1-5, "How
  did you feel?", 1 = strong ... 5 = weak) and `icu_rpe` (1-10) are the
  athlete's own verdict; they arrive only when answered on the watch when
  saving (the FIT file), never when added later in Garmin Connect.
- `GET /activity/{id}?intervals=true` → the same object plus `icu_intervals[]`
  (per-rep) and `icu_groups[]` (repeats rolled up, with `count`); no `fields=`
  projection, ~25-30 KB raw — project client-side before the LLM sees it.
- `GET /athlete/{id}/wellness?oldest=&newest=` → daily rows keyed by ISO date:
  `ctl`, `atl` (combined across sports, not per-sport), `sleepSecs`, `sleepScore`, `hrv`, `restingHR`,
  `weight`, `fatigue`, `soreness`.
- `GET /athlete/{id}/events?oldest=&newest=&category=WORKOUT&resolve=true` reads
  back planned events with parsed/resolved steps (useful to verify what a
  push actually stored).
- `GET /activity/{id}?intervals=true` also carries zone-bound fields
  `icu_hr_zones` (bpm upper bounds), `icu_power_zones` (% of FTP upper
  bounds), `pace_zones` (% of threshold speed upper bounds), plus
  `use_gap_zone_times` (bool|null — athlete scores runs on GAP) and
  `pool_length` (metres, set only for a pool swim). Used by the activity
  trace projection (`endury/intervals/trace.py`), not requested elsewhere.

### Activity streams

- `GET /activity/{id}/streams.json?types=a,b,c` → JSON array of `{type,
  name, data, data2, valueType, valueTypeIsArray, allNull, anomalies,
  custom}`. `data` is one entry per recorded sample (~1 Hz of MOVING time;
  paused time has no samples), numbers or `null`. All streams of one
  activity have equal length. Payload size is ~25 KB per stream-hour.
- Valid type names confirmed: `time, watts, heartrate, velocity_smooth,
  altitude, ga_velocity` (gradient-adjusted speed, m/s — computed, works
  even though it is not listed in the activity's `stream_types`),
  `grade_smooth`, `fixed_heartrate`. Invalid (HTTP 422 `Invalid stream type
  [x]`): `gap`, `gap_velocity`. Asking for a valid type the activity lacks
  is not an error — that stream is simply absent from the response.

## Writes

Calendar events, two activity writes (pairing, the athlete's feel and effort)
and the thresholds the athlete saves:

- `PUT /activity/{id}` with a partial `Activity` body: `paired_event_id`, or
  `feel` (1 strong … 5 weak) / `icu_rpe` (1–10); fields not sent stay.

- `PUT /athlete/{id}/sport-settings/{rowId}?recalcHrZones=` with a partial
  `SportSettings` body (`ftp`, `lthr`, `threshold_pace` in m/s); fields not
  sent stay. `recalcHrZones` is required; we send `true` only with an
  `lthr`, so an FTP edit keeps the athlete's own HR zones. Past activities
  are not re-scored (that is `…/sport-settings/{rowId}/apply`, never
  called). One planner sport can span several rows (`Ride` and
  `VirtualRide` kept apart), so `endury/intervals/sport_settings.py`
  writes every row covering it, skipping load-only Hike/Walk rows.

- `POST /athlete/{id}/events/bulk?upsert=true&upsertOnUid=false&updatePlanApplied=false`
  with an array of events (the latter two are required params). Upsert matches
  our own `external_id` and only ever touches events created by our app/key.
- Responses send `null` (not `[]`) for empty `tags` / `push_errors`; the
  models coerce it.
- `PUT /athlete/{id}/events/bulk-delete` body `[{"external_id": "..."}, ...]`.
- Event fields we use: `category` (`WORKOUT` | `RACE_A` | `RACE_B` | `RACE_C`),
  `start_date_local` — **must be a full LocalDateTime, no zone**
  (`YYYY-MM-DDT00:00:00`); a bare `YYYY-MM-DD` is rejected with
  `422 Invalid start date` (use `endury.intervals.push.event_start`),
  `type` (`Run`|`Ride`|`Swim`), `name`, `description` (coaching prose + step
  DSL), `tags: ["endury"]`, `external_id`. Do not set `moving_time`/load —
  their parser computes from steps — except `icu_training_load` on a fitness
  test, whose RPE-only main effort the parser cannot price (unverified live
  whether a parsed workout keeps it).
- `Event.push_errors[] {service, message, date}` = onward device-sync failures.

## Workout text DSL (what the serializer emits, inside `description`)

Steps are lines starting `- `; free markdown before/between blocks is allowed and
ignored by the parser (coaching prose goes there). Repeat blocks: a line `4x` (or
`Main Set 4x`) followed by indented-looking `- ` step lines, with a **blank line
before and after the block**. **Nested repeats are not supported — flatten.**

- Time: `10m`, `30s`, `1h10m`, `1h2m30s`. **`m` = minutes. Meters = `mtr`.**
- Distance: `400mtr`, `2km`, `1mi` (run/swim steps).
- Power: `85%`, `85-95%` (% of FTP), absolute `200-240w`, `Z2` zones.
- HR: `70-80% LTHR`, `Z2 HR`, absolute `140-150bpm` is poorly supported →
  use % LTHR instead.
- Pace: `78-82% Pace` (% of threshold pace), absolute `4:30/km Pace`,
  swim `1:55/100m-2:05/100m Pace`.
- Cadence secondary: `10m 85% 90rpm`.
- Ramp: `10m ramp 50-75%`. Free ride: `20m freeride`.
- Step label: text before the duration: `- Warmup 15m 55-70% LTHR`.

Canonical emission policy: **the step's lead metric decides.** A step whose
primary target is heart rate goes out as `% LTHR` even when it also carries a pace
or power target, so the card and the watch show the same lead; every other step
takes the first target its sport's fixed order can render (running: pace, then HR,
then power; cycling: power, then HR; swimming: swim pace, then HR). An effort-led
(RPE first) running step takes its heart-rate band or nothing, never its pace: that
pace is the flat-ground equivalent of an effort the hill runs. **Percent-of-threshold
for power (FTP), run pace (threshold pace), and HR (LTHR) when the relevant threshold
is known; absolute otherwise (esp. swim /100m pace is fine absolute — but prefer
`% Pace` when CSS known).** Round percentages to whole numbers. Zone numbers from our TargetDef map to
percent ranges via the athlete's band-spec (`endury/training/zones.py`) — never emit
`Z<N>` (athletes customize their zone schemes; percent is scheme-independent).
The zone *number* a card shows is the athlete's own, read off the sport-settings
row's `hr_zones` / `power_zones` / `pace_zones` (`LoadMetricContext.athlete_zones`,
built in `endury/planning/load_planner_metrics.py`), so card, calendar and debrief
agree; a band that crosses the athlete's zones shows none, and Endury's bands stand
in where a row has none.

## external_id scheme

- Sessions: `coachie:{plan_id}:{slot_id}`
- Races: `coachie:{plan_id}:race:{priority}:{race_date.isoformat()}` — keyed
  by priority+date, not list index: a plan can carry several B/C races, and
  index-keying would remap a surviving race's remote event onto a dropped
  one's slot (see `push.py:race_external_id`)
- Race-table races the coach put on the calendar: `coachie:race:{race_id}`
  (`planning/race_calendar.py`). Only on a day with no other race event; a
  plan push that adds its own race event for that day deletes this one.

All Endury events also carry the tag `endury` (`EVENT_TAG`); nothing reads it back, so
events pushed before 2026-10-21 keep the former `coachie` tag until their next push.
The `coachie:` external ids stay, since they are how Endury finds its events. Rollback/delete = bulk-delete by the
plan's known external_ids (never delete by date-range).
