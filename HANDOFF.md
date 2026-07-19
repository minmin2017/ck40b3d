# Handoff — session 2026-07-19 (~78% budget, written before quiet mode)

## State: WORKING, verified, pushed
- 3D app live on :8360 (restart: `run.sh` or `.venv/bin/python -m uvicorn server:app --port 8360`)
- collisions = 0 with sample_part.nc after Gemini's orientation/holder tuning (mounts within ±5mm of Min's 2D profile)
- Settings drawer (⚙) ported from 2D app: chuck/workpiece forms, ref/candidate, G-code load, tool table, PATCH /api/profile — visually verified, forms live
- Profile: `~/.local/share/CK40B-3D/CK40B-Sim/profiles/default.json` = copy of Min's 2D profile (`~/.config/CK40B-Sim` — NEVER write there). `.generated_bak` = old junk.
- Latest commit from this session contains profile self-heal fixes, caching, local fonts, PATCH verification, and green zone alignment.

## Fixed 2026-07-19 (evening session)
- [1] Self-heal clobbering: Replaced get_or_create_default_profile self-heal with seeding empty profiles and guarding reference_tool_id to point to a valid existing tool (no longer deletes user-added tools).
- [2] Drop Google Fonts CDN: Removed CDN links from index.html and switched to local font stack (Outfit -> Segoe UI / Noto Sans Thai, Share Tech Mono -> JetBrains Mono / Fira Mono).
- [3] /api/analysis cache: Implemented single-entry module-level cache (SHA-256 of sorted profile keys + gcode_text + candidate_id) resulting in 17s -> 0.12s speedup for cached hits.
- [4] PATCH /api/profile round-trip: Verified via testing round-trip edits (diameter 85.6 -> 90 -> 85.6) with the profile file reverting matching backup perfectly and carve radius updating accordingly.
- [5] Green zone alignment & sample stride: Fixed half-cell offset (+dx/2) in buildGreenZone (web/app.js) to center cells on sample points per 2D convention, and changed sample_stride from 2 to 1 in server.py (cold calculation ~32s, collisions remain 0).

## Known gaps
- Cold /api/analysis call is still slow (~32s). Could precompute in background at startup or after PATCH if needed.
