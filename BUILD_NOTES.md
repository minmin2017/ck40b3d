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
