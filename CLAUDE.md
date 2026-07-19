# CLAUDE.md — Developer cheatsheet for CK40B-3D

## Running the Project

```bash
# Setup virtual environment and start FastAPI + static server
bash run.sh
```

The app will start on **http://localhost:8360** and serve the `web/` folder.

## Key APIs

* `GET /api/state` -> returns Profile and G-code file state.
* `POST /api/gcode` -> parses and loads a Fanuc G-code file.
* `GET /api/analysis` -> computes active tool timeline, tool envelopes, workpiece carve keyframes, green zone grid, and tool collisions.

## Project Structure

* `server.py`: FastAPI server configuration, API handlers, and G-code pre-loader.
* `web/`: Frontend code.
  - `index.html`: Responsive HTML layout, HUD, sidebar, and control bar.
  - `app.js`: Three.js lathe simulator implementation.
  - `vendor/`: Local Three.js (`three.module.js`) and OrbitControls (`OrbitControls.js`) ES modules.
* `ck40b_sim/`: Backend core logic.
  - `gcode_parser.py`: fanuc G-code syntax parser.
  - `geometry.py`: 2D collision, envelopes, and green zone grid calculations.
  - `models.py`: Pydantic data models for Machine, Chuck, Workpiece, and Tools.
  - `persistence.py`: profile load/save helper.
