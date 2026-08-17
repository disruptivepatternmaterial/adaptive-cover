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
| Time window checks | Start/end checks must handle aware/naive datetimes safely and treat unparseable values as non-driving conditions rather than crashes. |
| Forecast fallback | Forecast caching uses longer TTL for successful fetches and shorter retry TTL after failures/unavailable entities. |
| Diagnostics safety | Diagnostics redact configured entity references and expose runtime summaries without raw entity identifiers. |

## Change policy

If a change intentionally alters one of these behaviors:

1. Update this file.
2. Add/adjust regression tests.
3. Record the change in `CHANGELOG.md` and relevant `README.md` sections.
