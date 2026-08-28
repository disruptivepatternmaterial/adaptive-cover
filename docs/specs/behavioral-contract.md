# Behavioral Contract — Adaptive Cover

This document defines runtime behavior that should remain stable across feature work.

## Core invariants

| Area | Contract |
|---|---|
| Manual override persistence | Manual-control state survives Home Assistant restart and is restored before first coordinator drive. |
| Startup gating | First automatic drive is deferred until expected switch restore is complete; empty switch set marks restored immediately. |
| Wait-for-target safety | Integration-commanded moves set wait state. `opening`/`closing` while a command is in flight (including after wait timeout) are not manual. `open`→`open` / `closed`→`closed` position ticks during wait are not manual. A travel→settled report off the commanded target latches manual and drops the stale target. Settling on-target (within settle tolerance) is not manual. Stale waits clear by timeout without discarding the commanded-target exemption. Toggles-off and a missing target clear wait and target together. Cover events before switch restore do not consume command-tracking. |
| Window latch behavior | If configured window/door sensors transition on->off, a timed refresh re-evaluates after hold duration; stale latch timers are canceled on reschedule/unload. |
| Climate brightness gate | Outside winter, lux-dim / irradiance-dim / not-sunny (cloud coverage or weather allow-list) uses default position, including in summer; summer force-close and transparent-blind close require sunny. |
| Cloud coverage thresholds | A numeric cloud reading is read in bands: at or above 90% it is not sunny regardless of the weather condition string; above 65% it is not sunny only if the condition string is also overcast-like, otherwise the weather allow-list decides; below 35% it is sunny. Between 35% and 65% the allow-list decides. With no allow-list configured, a numeric reading decides (sunny at or below 65%). A non-numeric reading is ignored and logged, never silently swallowed. |
| Solar date resolution | "Today" for the solar grid, sunrise, and sunset is resolved in Home Assistant's configured timezone, never the OS process timezone. The daily solar grid is computed at most once per local day, keyed on the local date it was built for rather than on its own result. |
| Polar day and night | On a day with no horizon crossing, sunrise/sunset resolve to None rather than raising, and the sunset gate falls back to the sun's actual elevation: night exactly when the sun is below the horizon. |
| Time window checks | Start/end checks must handle aware/naive datetimes safely and treat unparseable values as non-driving conditions rather than crashes. |
| Forecast fallback | Forecast caching uses longer TTL for successful fetches and shorter retry TTL after failures/unavailable entities. |
| Diagnostics safety | Diagnostics redact configured entity references and expose runtime summaries without raw entity identifiers. |

## Change policy

If a change intentionally alters one of these behaviors:

1. Update this file.
2. Add/adjust regression tests.
3. Record the change in `CHANGELOG.md` and relevant `README.md` sections.
