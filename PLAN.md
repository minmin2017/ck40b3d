# CK40B-3D — Plan (architect: Fable, 2026-07-18)

## Goal
3D version of the CK40B gang-tool clearance simulator. Same purpose as `~/Desktop/ck40b`
(collision check + green zone for tools on a gang slide table), but rendered as an
interactive 3D machine in the browser. Must be impressive AND extensible by Min.

## Stack (decided — do not change)
- **Backend**: Python 3 + FastAPI + uvicorn. Reuses the copied `ck40b_sim/` package
  (models, gcode_parser, geometry, persistence) — DO NOT rewrite the math, wrap it.
- **Frontend**: Three.js (vendored ES modules in `web/vendor/`, no CDN at runtime),
  OrbitControls, one page `web/index.html` + `web/app.js` (+ small modules if needed).
- Launch: `run.sh` → creates `.venv` if missing, installs `requirements.txt`
  (add fastapi, uvicorn), starts server on **http://localhost:8360**, serves `web/` statically.

## Machine → 3D world mapping
Internal machine coords: `(x_r, z)` = (radius from spindle centerline, Z along axis).
+X points toward operator (drawn "down" in 2D app), +Z away from chuck (right).
World mapping: `world = (z, -x_r, 0)` → spindle axis = world X axis, tools hang below.
Workpiece/chuck are solids of revolution around the spindle axis.

## Backend API (contract — frontend depends on these exact shapes)
- `GET /api/state` → current Profile as JSON (machine{slide table extents, stroke},
  chuck{body_diameter, body_length_z, jaw_*, face position derived}, workpiece{raw_diameter,
  raw_length, z_face_position}, tools[{id, name, type, mount_x, mount_z, orientation_deg,
  holder{block_width, block_length, shank_length, shank_width?}}], reference_tool_id,
  candidate_tool_id, gcode_name)
- `POST /api/gcode` body `{"path": "<abs path>"}` or `{"text": "<gcode>"}` → parse, store
  blocks in memory, return `{blocks: N, tools_seen: [...]}`
- `GET /api/analysis` → one bundle:
  - `timeline`: [{i, tool_id, x_r, z, rapid: bool}] — discretized (step 0.5mm) sample points
    of the ACTIVE tool tip in workpiece frame, in program order (use gcode_parser.discretize).
    Cap ~4000 points (increase step if over).
  - `envelopes`: {tool_id: [[x_r, z], ...]} from `per_tool_envelopes`
  - `carve_keyframes`: list of ~40 evenly spaced keyframes; each = {i: timeline index,
    profile: [[x_r, z], ...]} = exterior ring of `carved_workpiece_polygon` for blocks up to
    that point. Profile must be a clean open polyline from face to chuck side, x_r >= 0,
    suitable to revolve (frontend uses THREE.LatheGeometry).
  - `green_zone`: {x0, z0, dx, dz, nx, nz, mask: flat 0/1 list} from `compute_green_zone_grid`
    for the candidate tool (slide-table frame)
  - `collisions`: [{tool_id, i (timeline idx or block), msg}] from `check_collision_for_tool`
    for every tool
- Profile load/save: reuse `persistence.py` default profile if present, else build a sensible
  default (chuck Ø160×40, jaws, workpiece Ø70×50, 3 tools: OD rough 90°, boring 0°, cutoff 90°)
  and auto-load `sample_part.nc` on startup so the app opens with something to show.

## Frontend scene (the impressive part)
1. Dark studio background, hemisphere+directional lights, soft shadows, metal/rough PBR materials.
2. Chuck: gray cylinder body + 3 darker jaws (boxes on radial positions), spins with spindle.
3. Workpiece: `THREE.LatheGeometry` from current carve profile, brushed-steel material,
   **spins continuously** while program plays; geometry swaps as carve keyframes pass →
   visible material removal. Freshly-cut band can be slightly shinier.
4. Slide table: flat plate; each tool = holder block (box) + shank + small insert tip,
   positioned from mount_x/mount_z + orientation_deg. Active tool highlighted (emissive edge).
5. Playback: bottom bar — play/pause, speed (0.5/1/2/4x), timeline scrubber. The whole
   slide-table group moves so the ACTIVE tool tip follows `timeline`; passive tools ride along
   (this is the entire point of a gang lathe — passive tools sweeping near the chuck).
6. Green zone: translucent green extruded volume floating on the slide table (from mask grid,
   merged cells ok). Toggle button.
7. Collisions: when timeline index has a collision event → flash offending holder red +
   red ring marker + entry in a right-side report panel (click = jump timeline there).
8. HUD top-left: program name, current line/tool, X/Z DRO readout (machine coords, X as
   diameter to match G-code convention). Thai labels welcome, UI text bilingual ok.
9. Orbit/pan/zoom always live. Preset camera buttons: ¾ view, front (X-Z like the 2D app), top.

## Milestones
- M1: backend serves /api/state + /api/analysis for sample_part.nc (verify with curl + jq).
- M2: static scene renders (chuck, workpiece, table, tools) with orbit controls.
- M3: playback moves slide, spindle spins, carve keyframes swap workpiece geometry.
- M4: green zone volume + collision flash/report.
- M5: polish (lighting, DRO, camera presets), README.md + CLAUDE.md for the new repo.

## Rules
- Vendor three.module.js + OrbitControls.js locally (download once into web/vendor/).
- No build step — plain ES modules served statically.
- Keep `ck40b_sim/` untouched except: fix imports if `ui`-related imports break (ui/ was
  not copied), and it's fine to add small pure helper functions at the END of geometry.py.
- Port 8360. Do not touch port 4321 (powerNote) or 8090.
- Verify every milestone actually runs (curl / headless check) before moving on.
