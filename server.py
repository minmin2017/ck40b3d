import os
import sys
import math
from pathlib import Path
from typing import Optional, Union, Dict, Any, List

# Force Linux persistence path to ~/.local/share/CK40B-3D
os.environ["APPDATA"] = os.path.expanduser("~/.local/share/CK40B-3D")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np

# Ensure current directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from ck40b_sim.persistence import load_profile, save_profile
from ck40b_sim.models import Profile, Tool, Holder
from ck40b_sim.gcode_parser import parse, MotionBlock
from ck40b_sim.sim import build_frames, SimFrame
from ck40b_sim.geometry import (
    per_tool_envelopes,
    per_tool_cutting_envelopes,
    carved_radius_profile,
    compute_green_zone_grid,
    obstacles_polygon,
    check_collision_for_tool
)

app = FastAPI(title="CK40B-3D Simulator Backend")

# Enable CORS for frontend flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global in-memory state
state_db = {
    "profile_name": "default",
    "candidate_tool_id": "T03",  # default candidate tool
    "gcode_name": "sample_part.nc",
    "gcode_text": "",
    "blocks": [],
    "frames": []
}

def get_or_create_default_profile() -> Profile:
    p = load_profile(state_db["profile_name"])
    
    # Check and self-heal default tools to match PLAN.md
    t1 = p.get_tool("T01")
    t2 = p.get_tool("T02")
    t3 = p.get_tool("T03")
    dirty = False
    
    if not t1:
        p.tools.append(Tool(
            id="T01", name="OD Rough 90°", type="turning_OD",
            mount_x=120.0, mount_z=30.0, orientation_deg=90.0,
            holder=Holder(block_width=24.0, block_length=60.0, shank_length=30.0, shank_diameter=12.0, tip_v_offset=0.0)
        ))
        dirty = True
    if not t2:
        p.tools.append(Tool(
            id="T02", name="Boring 0°", type="boring",
            mount_x=40.0, mount_z=-50.0, orientation_deg=0.0,
            holder=Holder(block_width=24.0, block_length=60.0, shank_length=40.0, shank_diameter=10.0, tip_v_offset=0.0)
        ))
        dirty = True
    if not t3:
        p.tools.append(Tool(
            id="T03", name="Cutoff 90°", type="parting",
            mount_x=80.0, mount_z=20.0, orientation_deg=90.0,
            holder=Holder(block_width=24.0, block_length=50.0, shank_length=20.0, shank_diameter=12.0, tip_v_offset=0.0)
        ))
        dirty = True
        
    # Remove any default unnamed tool
    original_len = len(p.tools)
    p.tools = [t for t in p.tools if t.id in ("T01", "T02", "T03")]
    if len(p.tools) != original_len:
        dirty = True
        
    if dirty:
        p.reference_tool_id = "T01"
        save_profile(p)
        
    return p

# Load G-code helper
def load_gcode_program(text: str, name: str = "program.nc"):
    p = get_or_create_default_profile()
    
    # Auto-add tools in G-code that aren't in profile
    temp_blocks = parse(text)
    detected_tids = {b.tool_id for b in temp_blocks if b.tool_id}
    existing_tids = {t.id for t in p.tools}
    new_tids = detected_tids - existing_tids
    
    if new_tids:
        for tid in sorted(new_tids):
            p.tools.append(Tool(id=tid, name=f"Auto {tid}", type="other"))
        save_profile(p)
    
    # Determine the first program tool for correct parser alignment
    first_tid = next((b.tool_id for b in temp_blocks if b.tool_id), None)
    first_tool = p.get_tool(first_tid) if first_tid else p.get_tool(p.reference_tool_id)
    
    initial_pos = p.home_tip(first_tool) if first_tool else p.machine_zero_workpiece()
    
    # Parse G-code with proper offsets
    blocks = parse(text, initial_pos=initial_pos, z_offset=p.workpiece.z_face_position)
    
    # Build frames at 0.5mm step initially
    frames = build_frames(blocks, step=0.5)
    
    # Cap timeline to ~4000 points
    if len(frames) > 4000:
        step = 0.5 * (len(frames) / 4000)
        frames = build_frames(blocks, step=step)
        
    state_db["gcode_text"] = text
    state_db["gcode_name"] = name
    state_db["blocks"] = blocks
    state_db["frames"] = frames

# Initial startup load
try:
    sample_path = Path(__file__).parent / "sample_part.nc"
    if sample_path.exists():
        load_gcode_program(sample_path.read_text(encoding="utf-8"), "sample_part.nc")
    else:
        # Fallback if sample_part.nc not found in current folder
        sample_path = Path("/home/minmin/Desktop/ck40b3d/sample_part.nc")
        if sample_path.exists():
            load_gcode_program(sample_path.read_text(encoding="utf-8"), "sample_part.nc")
except Exception as e:
    print(f"Error loading sample G-code on startup: {e}")

class GcodeRequest(BaseModel):
    path: Optional[str] = None
    text: Optional[str] = None

@app.get("/api/state")
def get_state():
    p = get_or_create_default_profile()
    
    # Sync candidate ID to first tool if invalid
    tids = [t.id for t in p.tools]
    if state_db["candidate_tool_id"] not in tids and tids:
        state_db["candidate_tool_id"] = tids[0]
        
    return {
        "machine": p.machine.model_dump(),
        "chuck": p.chuck.model_dump(),
        "workpiece": p.workpiece.model_dump(),
        "tools": [t.model_dump() for t in p.tools],
        "reference_tool_id": p.reference_tool_id,
        "candidate_tool_id": state_db["candidate_tool_id"],
        "gcode_name": state_db["gcode_name"]
    }

@app.post("/api/gcode")
def post_gcode(req: GcodeRequest):
    if req.path:
        path = Path(req.path)
        if not path.exists():
            raise HTTPException(status_code=400, detail="File path does not exist")
        text = path.read_text(encoding="utf-8", errors="replace")
        name = path.name
    elif req.text is not None:
        text = req.text
        name = "untitled.nc"
    else:
        raise HTTPException(status_code=400, detail="Either 'path' or 'text' is required")
        
    try:
        load_gcode_program(text, name)
        p = get_or_create_default_profile()
        detected_tids = sorted(list({b.tool_id for b in state_db["blocks"] if b.tool_id}))
        return {
            "blocks": len(state_db["blocks"]),
            "tools_seen": detected_tids
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse G-code: {e}")

@app.get("/api/analysis")
def get_analysis():
    p = get_or_create_default_profile()
    blocks = state_db["blocks"]
    frames = state_db["frames"]
    
    if not blocks:
        return {
            "timeline": [],
            "envelopes": {},
            "carve_keyframes": [],
            "green_zone": None,
            "collisions": []
        }
        
    # 1. Timeline
    timeline = []
    for idx, f in enumerate(frames):
        timeline.append({
            "i": idx,
            "tool_id": f.active_tool_id,
            "x_r": float(f.tip_x),
            "z": float(f.tip_z),
            "rapid": f.move_type == "rapid",
            "n": int(f.line_no)
        })
        
    # 2. Envelopes
    envs = per_tool_envelopes(blocks, step=1.0)
    envelopes_json = {tid: env.tolist() for tid, env in envs.items() if env.size > 0}
    
    # 3. Carve Keyframes
    # Group cutting points from timeline sequentially to compute keyframe carved profiles
    cutting_points_accum: Dict[str, List[tuple]] = {}
    carve_keyframes = []
    
    T = len(frames)
    num_keyframes = min(40, T)
    if T > 0:
        keyframe_indices = [int(i * (T - 1) / (num_keyframes - 1)) for i in range(num_keyframes)]
    else:
        keyframe_indices = []
        
    keyframe_idx_set = set(keyframe_indices)
    
    # Pre-generate keyframes
    # For performance, we collect cutting points up to each index
    for idx, f in enumerate(frames):
        if f.is_cut:
            cutting_points_accum.setdefault(f.active_tool_id, []).append((f.tip_x, f.tip_z))
            
        if idx in keyframe_idx_set:
            # Reconstruct dict of numpy arrays
            cut_envs_at = {
                tid: np.array(pts, dtype=float)
                for tid, pts in cutting_points_accum.items()
                if pts
            }
            # Compute carved profile
            poly = carved_radius_profile(p, cut_envs_at, z_step=0.5)
            carve_keyframes.append({
                "i": idx,
                "profile": [[float(r), float(z)] for r, z in poly]
            })
            
    # 4. Green Zone Grid for the candidate tool (T-slot/slide frame)
    cand_id = state_db["candidate_tool_id"]
    cand = p.get_tool(cand_id)
    green_zone_data = None
    
    if cand:
        # Active tool path envelopes
        envs_by_tid = {}
        for f in frames:
            envs_by_tid.setdefault(f.active_tool_id, []).append((f.tip_x, f.tip_z))
        envs_np = {tid: np.asarray(pts, dtype=float) for tid, pts in envs_by_tid.items() if pts}
        
        # Cutting sweeps
        cut_envs = per_tool_cutting_envelopes(blocks, step=1.0)
        
        # Setup grid layout snapped to steps of 2.0
        step = 2.0
        s = p.machine.slide_table
        n_low_x = int(math.ceil((cand.mount_x - s.x_min) / step))
        n_hi_x = int(math.ceil((s.x_max - cand.mount_x) / step))
        n_low_z = int(math.ceil((cand.mount_z - s.z_min) / step))
        n_hi_z = int(math.ceil((s.z_max - cand.mount_z) / step))
        grid_x = (-n_low_x * step, n_hi_x * step, step)
        grid_z = (-n_low_z * step, n_hi_z * step, step)
        
        try:
            grid, xs, zs = compute_green_zone_grid(
                p, envs_np, cand, cutting_envelopes=cut_envs,
                grid_x=grid_x, grid_z=grid_z, sample_stride=2,
                consider_table=p.green_zone_consider_table,
                check_tool_overlap=True
            )
            green_zone_data = {
                "x0": float(xs[0]),
                "z0": float(zs[0]),
                "dx": float(step),
                "dz": float(step),
                "nx": int(len(xs)),
                "nz": int(len(zs)),
                "mask": grid.flatten().tolist()
            }
        except Exception as e:
            print(f"Error computing green zone: {e}")
            
    # 5. Collisions
    # Build active envelopes and active timeline map
    envs_by_tid = {}
    indices_by_tid = {}
    for i, f in enumerate(frames):
        envs_by_tid.setdefault(f.active_tool_id, []).append((f.tip_x, f.tip_z))
        indices_by_tid.setdefault(f.active_tool_id, []).append(i)
        
    envs_np = {tid: np.asarray(pts, dtype=float) for tid, pts in envs_by_tid.items() if pts}
    cut_envs = per_tool_cutting_envelopes(blocks, step=1.0)
    
    # We check against chuck + jaws + workpiece
    obstacles = obstacles_polygon(p, cutting_envelopes=cut_envs, include_workpiece=True)
    
    collisions = []
    for tool in p.tools:
        try:
            has_col, hits = check_collision_for_tool(tool, (0, 0), envs_np, obstacles, p, sample_stride=2)
            if has_col:
                for active_tid, local_idx in hits:
                    # Map local envelope index to global timeline index
                    timeline_idx = indices_by_tid[active_tid][local_idx]
                    collisions.append({
                        "tool_id": tool.id,
                        "i": timeline_idx,
                        "msg": f"ชน {tool.name} ระหว่างทำงานของ {active_tid}"
                    })
        except Exception as e:
            print(f"Error checking collision for tool {tool.id}: {e}")
            
    return {
        "timeline": timeline,
        "envelopes": envelopes_json,
        "carve_keyframes": carve_keyframes,
        "green_zone": green_zone_data,
        "collisions": collisions
    }

# Mount static web directory
app.mount("/", StaticFiles(directory="web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8360)
