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
- [6] T-slot system (commit 20028f6): PATCH รับ machine, ฟอร์ม slots ใน settings, แถบร่องบนโต๊ะ 3D, ย้ายร่องแล้ว tool ที่ปักตามทันที — ทดสอบ +10mm ผ่าน
- [7] Advanced green zones (commit f1ed23c): /api/green-zones mode=global|per_tool + cache แยก, ปุ่ม ZONE 4 สถานะ, checkbox เลือก tool — ยืนยันโซนรวม (3,748 cells) ซ้อนในโซนรายตัวทุกตัว
- [8] Auto-pack + position setups (commit รอบนี้): POST /api/layout/pack (GAP 5mm, green-align Mode A/B, sync slot_attach_z กัน snap ดีดกลับ), GET/POST /api/layout/setups 1-4 (ใช้ profile.position_setups ที่มีข้อมูลเก่าจาก 2D ติดมาด้วย), ปุ่มจัดเรียง/PACK + UI Setup 4 ช่องใน drawer — ทดสอบ pack: ระยะตรงสูตร (45/25mm), Setup load กลับตรง backup

## Known gaps
- (a) cold /api/analysis และ /api/green-zones ยังช้า (~32s / ~100s ต่อโหมด) — พิจารณา precompute background
- (b) pack+green-align คำนวณช่วงปลอดภัยจาก toolpath ชุดก่อน re-parse (พฤติกรรมเดียวกับแอป 2D) → หลัง pack ต้องดูรายการชนที่คำนวณใหม่เสมอ (เทสต์จริงได้ชน 10 จุดหลัง pack) — future: วน green-align ซ้ำบน frames ใหม่จนนิ่ง

## Not yet ported from 2D
- drag tools ในหน้าจอ/edit-size คลิกชิ้นส่วน/measure mode
- G-code edit mode (editor+sync+transforms)
- หลายโปรไฟล์, tool preset library, workpiece-limit analysis, no-go zones
