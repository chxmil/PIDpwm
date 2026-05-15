# DevLog - 2026-05-15 - Sorting Console UnicodeDecodeError fix

**Trigger:** Pi crash — Jinja2 `UnicodeDecodeError: 'utf-8' codec can't decode
byte 0x85 in position 3937` when serving `templates/index.html`.
**File:** `Arm_Control/templates/index.html` (rewritten, pure ASCII).

## Root cause
`index.html` contained literal non-ASCII characters (`...` ellipsis, `->`/`-`
glyphs, hourglass) that an editor re-saved as Windows-1252 (`0x85` = cp1252
ellipsis), so Flask/Jinja2 (UTF-8 loader) could not decode the template.

## Fix
Rewrote the file using **ASCII only** — all non-ASCII rendered via HTML
entities (`&hellip;`, `&rarr;`, `&mdash;`, `&#129520;`, `&#10003;`,
`&#9632;`, `&#9679;`) and ASCII text (`[ WAITING ]`, `-> bin`). Behaviour,
layout and the status/confirm logic are unchanged.

Verified: byte scan reports no byte > 0x7F (9282 bytes, pure ASCII) — the
template is now encoding-independent and cannot trigger this error again.

## Deployment note (not a code issue)
The traceback shows the Pi runs `/home/pi/robot_arm/app.py`, not
`Arm_Control/AppArm.py`. The new console needs the AppArm.py version that
exposes the `status` / `confirm` / `sort_mode` endpoints + `GATE_PROMPT` /
`LAST_SORT`. If the Pi's `app.py` is an older file, deploy the current
`AppArm.py` (as `app.py`) so the live-status and web-Confirm features work.
