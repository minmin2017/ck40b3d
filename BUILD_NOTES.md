# BUILD_NOTES.md — CK40B-3D Build Summary

This document summarizes the development and verification details for the **CK40B-3D** interactive simulator.

## Completed Milestones

### 1. Milestone M1: Backend Server & APIs (Completed)
- Integrated `fastapi` and `uvicorn` into `requirements.txt`.
- Created `server.py` serving `/api/state`, `/api/gcode`, and `/api/analysis` matching the PLAN.md API contracts.
- Persisted profile path on Linux to `~/.local/share/CK40B-3D` and added self-healing code for default tools.
- Created `run.sh` to automate `.venv` setup and start the server on port 8360.
- **Verification**: Verified endpoints successfully via curl and python json validators (Exit code 0, all keys present: timeline, envelopes, carve_keyframes, green_zone, collisions).

### 2. Milestone M2: Frontend static scene rendering (Completed)
- Designed `web/index.html` with a modern dark theme layout, Outfitters typography, HUD DRO display, right sidebar warning panel, and playback/preset controls.
- Created `web/app.js` using local ES module imports from `./vendor/three.module.js` and `./vendor/OrbitControls.js`.
- Implemented machine coordinates to 3D world mapping: `world = (z, -x_r, 0)`.
- Rendered 3D chuck cylinder body and 3 jaws distributed at 120° angles.
- **Verification**: Executed `node --check web/app.js` successfully with exit code 0.

### 3. Milestone M3: Playback and dynamic material carving (Completed)
- Added playback loop that rotates chuck/workpiece, translates the slide table group so the active tool tip matches the timeline coordinate, and highlights active tool holders.
- Utilized `THREE.LatheGeometry` to dynamically revolve and swap workpiece geometries as carving keyframes pass, visually demonstrating material removal.

### 4. Milestone M4: Green zone and collisions visualization (Completed)
- Generated safe-mounting green zone grid boxes on the slide table using PBR transparent green materials.
- Displayed red collision torus rings at contact coordinates and highlighted colliding tool holders in glowing red.
- Populated the collision log panel (clicks dynamically jump playback scrubber directly to the collision timeline index).

### 5. Milestone M5: Final Polish (Completed)
- Implemented DRO updates, camera preset view buttons, and final documentation (README.md, CLAUDE.md, and BUILD_NOTES.md).

## How to Run

```bash
# Start the server and setup venv
bash run.sh
```
Open **http://localhost:8360** in the browser.

## Deviations from PLAN.md
- None. The API design and coordinates mapping were followed exactly.

## Verification Evidence
- **Python Syntax Check**: `python3 -m py_compile server.py` (Exit code 0)
- **JavaScript Syntax Check**: `node --check web/app.js` (Exit code 0)
- **GET /api/analysis verification**:
  - `dict_keys(['timeline', 'envelopes', 'carve_keyframes', 'green_zone', 'collisions'])`
  - Timeline length: 1786 frames
  - Keyframes count: 40 frames

## 2D Handoff Tasks Updates

### 6. Part 1: Collisions Tuning & Verification (Completed)
- **Problem**: Turning tool `T01` was colliding with the workpiece during `T01` operation and `T03` drilling/spotting operation due to incorrect tool orientation (0° instead of 90°) and zero tip offset.
- **Orientation Tuning**: Set turning tools `T01` and `T02` to `orientation_deg = 90.0` (aligned with `-X` towards spindle center). Left boring tool `T03` at `0.0`.
- **Holder Dimensions**: Set turning tool holder block to `20x50` and shank to `50` long (total length `100mm`).
- **Tip Offset**: Configured `tip_v_offset` of `T01` and `T02` to `10.0mm` to shift the tool block body to the right (`+Z` direction), keeping only the insert tip on the leading edge.
- **Mount Z Adjustments**:
  - Shifted `T01` Z mount position to `35.0` (shift of `+5.0mm` from `30.0` via `slot_attach_z = -5.0`).
  - Shifted `T02` Z mount position to `35.0` (shift of `+5.0mm` from `30.0` via `slot_attach_z = -5.0`).
  - Shifted `T03` Z mount position to `33.5` (shift of `-5.0mm` from `38.5` via `slot_attach_z = -3.5`).
- **Collision Checking Logic**: Updated `server.py` to match the 2D app's collision rule: do not check the active tool against the workpiece during its own operation (since it cuts the workpiece). Only check passive tools vs carved workpiece, and check all tools vs fixtures (chuck/jaws).
- **Result**: Collisions list is now `[]` (zero collisions).

### 7. Part 2: Ported Settings Features from 2D App (Completed)
- **PATCH /api/profile**: Added in `server.py` to allow saving Chuck, Workpiece, Tools list, and Ref/Cand selections. Validates inputs using Pydantic, snaps slotted tools, and triggers a full safety analysis recalculation.
- **⚙️ Settings Drawer**: Designed a sliding glass drawer on the left side of `index.html` to configure Chuck diameter/length, Workpiece diameter/length/grip, Reference/Candidate dropdowns, and custom G-code text/file uploads.
- **Interactive Tool Cards**: Added dynamic form fields to configure all properties (mount, orientation, slots, dimensions) for each tool, with full validation.
- **Visual Rebuild**: The page re-queries analysis and redraws the full 3D viewport (chuck, workpiece, tools, green zone) dynamically upon save or recalculation.

