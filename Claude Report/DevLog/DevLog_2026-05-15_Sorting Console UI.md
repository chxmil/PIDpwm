# DevLog — 2026-05-15 — Material Sorting Console UI

**Trigger:** user — make the templates/dashboard user-friendly for the
classification/sorting purpose.
**Files:** `Arm_Control/templates/index.html` (rewritten),
`Arm_Control/AppArm.py` (additive), `SORTING_MANUAL.md` + CLAUDE.md §2 synced.
`App.py`/`ModelInclude.py` unchanged; `dashboard.html` (ESP32 PID) untouched.

## Why
The old `index.html` was the "Random Pick & Place" teaching page (garbled
icons, home/1-3/4-6 random workflow) — irrelevant to material sorting and gave
no visibility into the grip/confirm cycle. Confirmation was joystick-only.

## Changes

### `index.html` → Material Sorting Console (full rewrite)
- **Live Status** card polling `/api {cmd:'status'}` at 1 Hz: arm state,
  sort mode, last sorted (`material → bin`), and a gate banner that **pulses
  amber while a gate is awaiting confirmation**.
- **CONFIRM** button (web equivalent of joystick btn 9, enabled only when a
  gate is waiting) and **EMERGENCY STOP**.
- **Teach Poses**: clearly labelled save buttons — START / PRE-GRIP / SAFE and
  HARD→Bin 4 / MEDIUM→Bin 5 / SOFT→Bin 6 / REJECT→Bin 7 (calls existing
  `save_pos`).
- Legacy random pick-place moved into a collapsed `<details>` (still works
  when not in sort mode).
- Dark theme consistent with `dashboard.html`; toast feedback; no external JS
  deps; mobile/tablet friendly.

### `AppArm.py` (additive only)
- New globals `GATE_PROMPT` (current waiting gate name) and `LAST_SORT`
  (`{material,bin,ts}` of last placed object).
- `sort_gate()` sets `GATE_PROMPT` on open, clears it on confirm/interrupt.
- `arm_place` records `LAST_SORT` on a successful drop.
- `status` now also returns `gate`, `awaiting`, `last_sort` (plus existing
  `state`/`confirmed`/`sort_mode`).
- **New `confirm` command**: web equivalent of joystick btn 9 — sets
  `CONFIRMATION_RECEIVED` **only while a gate is actually waiting**
  (`ALLOW_MANUAL_ADJUST and not CONFIRMATION_RECEIVED`), so a stray click
  can't pre-confirm a future gate. `wait_for_permission`/legacy untouched.

## Verification
`py_compile` clean on `Arm_Control/AppArm.py`. UI is static HTML/JS served by
the existing Flask route; no behavioural change to grip/PID/classify.
On-robot check still pending with the integration report steps 4–5.
