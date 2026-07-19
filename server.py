import os
import sys
import math
import json
import hashlib
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

# Single-entry cache for GET /api/analysis
_analysis_cache = {"key": None, "result": None}

def get_or_create_default_profile() -> Profile:
    p = load_profile(state_db["profile_name"])

    # First run only (empty profile): seed the PLAN.md default tool set.
    # Never touch tools on an existing profile — G-code loads auto-add tools
    # and the user tunes mounts/reference in Settings.
    if not p.tools:
        p.tools = [
            Tool(
                id="T01", name="OD Rough 90°", type="turning_OD",
                mount_x=120.0, mount_z=30.0, orientation_deg=90.0,
                holder=Holder(block_width=24.0, block_length=60.0, shank_length=30.0, shank_diameter=12.0, tip_v_offset=0.0)
            ),
            Tool(
                id="T02", name="Boring 0°", type="boring",
                mount_x=40.0, mount_z=-50.0, orientation_deg=0.0,
                holder=Holder(block_width=24.0, block_length=60.0, shank_length=40.0, shank_diameter=10.0, tip_v_offset=0.0)
            ),
            Tool(
                id="T03", name="Cutoff 90°", type="parting",
                mount_x=80.0, mount_z=20.0, orientation_deg=90.0,
                holder=Holder(block_width=24.0, block_length=50.0, shank_length=20.0, shank_diameter=12.0, tip_v_offset=0.0)
            ),
        ]
        p.reference_tool_id = "T01"
        save_profile(p)

    # Keep the reference pointing at a tool that still exists.
    if not p.get_tool(p.reference_tool_id):
        p.reference_tool_id = p.tools[0].id
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
    
    # Compute cache key
    p_dict = p.model_dump()
    p_json = json.dumps(p_dict, sort_keys=True)
    gcode = state_db.get("gcode_text", "")
    candidate_id = state_db.get("candidate_tool_id", "")
    
    raw_key = f"{p_json}||{gcode}||{candidate_id}"
    cache_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    
    if _analysis_cache["key"] == cache_key:
        return _analysis_cache["result"]
        
    blocks = state_db["blocks"]
    frames = state_db["frames"]
    
    if not blocks:
        res = {
            "timeline": [],
            "envelopes": {},
            "carve_keyframes": [],
            "green_zone": None,
            "collisions": []
        }
        _analysis_cache["key"] = cache_key
        _analysis_cache["result"] = res
        return res
        
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
                grid_x=grid_x, grid_z=grid_z, sample_stride=1,
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
    
    # We check against chuck + jaws (fixtures) for all envelopes,
    # and against workpiece (carved) only for other tools' envelopes.
    fixtures = obstacles_polygon(p, include_workpiece=False)
    carved = obstacles_polygon(p, cutting_envelopes=cut_envs, include_workpiece=True)
    
    collisions = []
    for tool in p.tools:
        try:
            # 1. Check against fixtures (chuck/jaws) - all envelopes
            has_col_fix, hits_fix = check_collision_for_tool(tool, (0, 0), envs_np, fixtures, p, sample_stride=1)
            if has_col_fix:
                for active_tid, local_idx in hits_fix:
                    timeline_idx = indices_by_tid[active_tid][local_idx]
                    collisions.append({
                        "tool_id": tool.id,
                        "i": timeline_idx,
                        "msg": f"ชน {tool.name} กับหัวจับ/ฟิกซ์เจอร์ ระหว่างทำงานของ {active_tid}"
                    })
            else:
                # 2. Check against carved workpiece - only other tools
                other_envs = {tid: e for tid, e in envs_np.items() if tid != tool.id}
                if other_envs:
                    has_col_wp, hits_wp = check_collision_for_tool(tool, (0, 0), other_envs, carved, p, sample_stride=1)
                    if has_col_wp:
                        for active_tid, local_idx in hits_wp:
                            timeline_idx = indices_by_tid[active_tid][local_idx]
                            collisions.append({
                                "tool_id": tool.id,
                                "i": timeline_idx,
                                "msg": f"ชน {tool.name} กับชิ้นงาน ระหว่างทำงานของ {active_tid}"
                            })
        except Exception as e:
            print(f"Error checking collision for tool {tool.id}: {e}")
            
    res = {
        "timeline": timeline,
        "envelopes": envelopes_json,
        "carve_keyframes": carve_keyframes,
        "green_zone": green_zone_data,
        "collisions": collisions
    }
    _analysis_cache["key"] = cache_key
    _analysis_cache["result"] = res
    return res

@app.patch("/api/profile")
def patch_profile(update: dict):
    p = get_or_create_default_profile()
    
    try:
        # 1. Update chuck
        if "chuck" in update and update["chuck"]:
            from ck40b_sim.models import Chuck
            p.chuck = Chuck.model_validate(update["chuck"])
            
        # 2. Update workpiece
        if "workpiece" in update and update["workpiece"]:
            from ck40b_sim.models import Workpiece
            p.workpiece = Workpiece.model_validate(update["workpiece"])
            
        # 3. Update tools
        if "tools" in update and update["tools"] is not None:
            from ck40b_sim.models import Tool
            new_tools = []
            for t_dict in update["tools"]:
                new_tools.append(Tool.model_validate(t_dict))
            p.tools = new_tools
            
        # 4. Update reference tool
        if "reference_tool_id" in update and update["reference_tool_id"]:
            p.reference_tool_id = update["reference_tool_id"]
            
        # 5. Update candidate tool
        if "candidate_tool_id" in update and update["candidate_tool_id"]:
            state_db["candidate_tool_id"] = update["candidate_tool_id"]
            
        # Update machine
        if "machine" in update and update["machine"]:
            from ck40b_sim.models import Machine
            p.machine = Machine.model_validate(update["machine"])
            
        p.snap_all_slotted()
        save_profile(p)
        
        # Reload G-code if available to refresh timeline/collisions with the new profile
        if state_db["gcode_text"]:
            load_gcode_program(state_db["gcode_text"], state_db["gcode_name"])
            
        return get_state()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid profile data: {e}")

# Single-entry cache for GET /api/green-zones
_zones_cache = {"key": None, "result": None}

def intersect_green_zones(all_zones, step):
    """Intersect several per-tool green-zone grids into one tip-world grid."""
    x_lo = min(az[3] + az[1][0]  for az in all_zones)
    x_hi = max(az[3] + az[1][-1] for az in all_zones)
    z_lo = min(az[4] + az[2][0]  for az in all_zones)
    z_hi = max(az[4] + az[2][-1] for az in all_zones)
    world_xs = np.arange(x_lo, x_hi + step * 0.5, step)  # vertical (X)
    world_zs = np.arange(z_lo, z_hi + step * 0.5, step)  # horizontal (Z)

    inter = np.ones((len(world_zs), len(world_xs)), dtype=np.int8)  # (nz, nx)
    for grid, xs, zs, ax, az in all_zones:
        grid = np.asarray(grid)
        ix = np.round((world_xs - ax - xs[0]) / step).astype(int)  # len nx
        iz = np.round((world_zs - az - zs[0]) / step).astype(int)  # len nz
        ix_ok = (ix >= 0) & (ix < grid.shape[1])
        iz_ok = (iz >= 0) & (iz < grid.shape[0])
        ixc = np.clip(ix, 0, grid.shape[1] - 1)
        izc = np.clip(iz, 0, grid.shape[0] - 1)
        sub = (grid[np.ix_(izc, ixc)] == 1)           # (nz, nx)
        sub &= iz_ok[:, None] & ix_ok[None, :]         # out-of-range = unsafe
        inter &= sub.astype(np.int8)
    return inter, world_xs, world_zs

def _tool_green_zone(p, tool, envs_np, cut_envs, step, check_tool_overlap):
    from math import ceil
    s = p.machine.slide_table
    _EXPAND = 20.0  # mm beyond slide-table edges for a wider safe-zone view
    n_low_x = int(ceil((tool.mount_x - s.x_min + _EXPAND) / step))
    n_hi_x = int(ceil((s.x_max - tool.mount_x + _EXPAND) / step))
    n_low_z = int(ceil((tool.mount_z - s.z_min + _EXPAND) / step))
    n_hi_z = int(ceil((s.z_max - tool.mount_z + _EXPAND) / step))
    grid_x = (-n_low_x * step, n_hi_x * step, step)
    grid_z = (-n_low_z * step, n_hi_z * step, step)
    grid, xs, zs = compute_green_zone_grid(
        p, envs_np, tool,
        cutting_envelopes=cut_envs,
        grid_x=grid_x, grid_z=grid_z,
        sample_stride=1,
        consider_table=p.green_zone_consider_table,
        check_tool_overlap=check_tool_overlap,
    )
    anchor_x, anchor_z = p.home_tip(tool)
    return grid, xs, zs, anchor_x, anchor_z

@app.get("/api/green-zones")
def get_green_zones(mode: str, tools: Optional[str] = None):
    p = get_or_create_default_profile()
    
    # Compute cache key
    p_dict = p.model_dump()
    p_json = json.dumps(p_dict, sort_keys=True)
    gcode = state_db.get("gcode_text", "")
    
    # Resolve tools list
    if tools:
        tools_list = [t.strip() for t in tools.split(",") if t.strip()]
    else:
        tools_list = [t.id for t in p.tools]
        
    tools_str = ",".join(sorted(tools_list))
    raw_key = f"{p_json}||{gcode}||{mode}||{tools_str}"
    cache_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    
    if _zones_cache["key"] == cache_key:
        return _zones_cache["result"]
        
    blocks = state_db["blocks"]
    frames = state_db["frames"]
    
    if not blocks:
        if mode == "per_tool":
            res = {"mode": "per_tool", "zones": []}
        else:
            res = {
                "mode": "global",
                "x0": 0.0,
                "z0": 0.0,
                "dx": 2.0,
                "dz": 2.0,
                "nx": 0,
                "nz": 0,
                "mask": [],
                "n_tools": 0
            }
        _zones_cache["key"] = cache_key
        _zones_cache["result"] = res
        return res
        
    # Active tool path envelopes
    envs_by_tid = {}
    for f in frames:
        envs_by_tid.setdefault(f.active_tool_id, []).append((f.tip_x, f.tip_z))
    envs_np = {tid: np.asarray(pts, dtype=float) for tid, pts in envs_by_tid.items() if pts}
    
    # Cutting sweeps
    cut_envs = per_tool_cutting_envelopes(blocks, step=1.0)
    
    step = 2.0
    
    if mode == "global":
        included_tools = [p.get_tool(tid) for tid in tools_list if p.get_tool(tid) is not None]
        if not included_tools:
            res = {
                "mode": "global",
                "x0": 0.0,
                "z0": 0.0,
                "dx": 2.0,
                "dz": 2.0,
                "nx": 0,
                "nz": 0,
                "mask": [],
                "n_tools": 0
            }
        else:
            ref = p.get_tool(p.reference_tool_id)
            anchor_tool = ref if (ref is not None and ref.id in [t.id for t in included_tools]) else included_tools[0]
            cax, caz = p.home_tip(anchor_tool)
            
            all_zones = []
            for tool in included_tools:
                grid, xs, zs, ax, az = _tool_green_zone(p, tool, envs_np, cut_envs, step, check_tool_overlap=False)
                all_zones.append((grid, np.asarray(xs), np.asarray(zs), cax, caz))
                
            inter, world_xs, world_zs = intersect_green_zones(all_zones, step)
            res = {
                "mode": "global",
                "x0": float(world_xs[0]),
                "z0": float(world_zs[0]),
                "dx": float(step),
                "dz": float(step),
                "nx": int(len(world_xs)),
                "nz": int(len(world_zs)),
                "mask": inter.flatten().tolist(),
                "n_tools": int(len(all_zones))
            }
    elif mode == "per_tool":
        included_tools = [p.get_tool(tid) for tid in tools_list if p.get_tool(tid) is not None]
        res_zones = []
        for tool in included_tools:
            grid, xs, zs, ax, az = _tool_green_zone(p, tool, envs_np, cut_envs, step, check_tool_overlap=True)
            res_zones.append({
                "tool_id": tool.id,
                "x0": float(ax + xs[0]),
                "z0": float(az + zs[0]),
                "dx": float(step),
                "dz": float(step),
                "nx": int(len(xs)),
                "nz": int(len(zs)),
                "mask": grid.flatten().tolist()
            })
        res = {
            "mode": "per_tool",
            "zones": res_zones
        }
    else:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'global' or 'per_tool'")
        
    _zones_cache["key"] = cache_key
    _zones_cache["result"] = res
    return res

# ── Packing and Layout Helpers ──────────────────────────────────────────────

def _tool_safe_z_interval(p, tool, envs_np, cut_envs, step):
    """Absolute mount_z range [lo, hi] where this tool's tip stays in its green zone."""
    grid, xs, zs, ax, az = _tool_green_zone(p, tool, envs_np, cut_envs, step, check_tool_overlap=True)
    grid = np.asarray(grid)
    if grid.size == 0:
        return None
    xs = np.asarray(xs)
    zs = np.asarray(zs)
    ix0 = int(np.argmin(np.abs(xs)))      # column at δx=0 (current mount_x)
    col = (grid[:, ix0] == 1)             # safe mask along z offsets
    if not col.any():
        return None
    iz0 = int(np.argmin(np.abs(zs)))      # row at δz=0 (current mount_z)
    
    # Collect contiguous green runs, then pick the one nearest iz0
    runs, start = [], None
    for i, v in enumerate(col):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(col) - 1))

    def _dist(run):
        lo, hi = run
        return 0 if lo <= iz0 <= hi else min(abs(iz0 - lo), abs(iz0 - hi))

    lo_i, hi_i = min(runs, key=_dist)
    return tool.mount_z + float(zs[lo_i]), tool.mount_z + float(zs[hi_i])

def _green_align_packed_tools(p, tools_sorted, envs_np, cut_envs, step):
    """After geometric packing, nudge tools into their green zones."""
    intervals, unsafe_ids = {}, []
    for t in tools_sorted:
        iv = _tool_safe_z_interval(p, t, envs_np, cut_envs, step)
        intervals[t.id] = iv
        if iv is None:
            unsafe_ids.append(t.id)

    # Mode A: feasible single Δ for all tools that have a zone
    lo_bound, hi_bound, have_any = -1e9, 1e9, False
    for t in tools_sorted:
        iv = intervals[t.id]
        if iv is None:
            continue
        have_any = True
        lo, hi = iv
        lo_bound = max(lo_bound, lo - t.mount_z)
        hi_bound = min(hi_bound, hi - t.mount_z)

    if have_any and lo_bound <= hi_bound:
        delta = min(max(0.0, lo_bound), hi_bound)
        if abs(delta) > 1e-6:
            for t in tools_sorted:
                t.mount_z = round(t.mount_z + delta, 2)
        return "A", unsafe_ids

    # Mode B: clamp each tool into its own safe interval individually
    for t in tools_sorted:
        iv = intervals[t.id]
        if iv is None:
            continue
        lo, hi = iv
        if t.mount_z < lo:
            t.mount_z = round(lo, 2)
        elif t.mount_z > hi:
            t.mount_z = round(hi, 2)
    return "B", unsafe_ids

def _auto_adjust_tools(p, tools):
    """Pack tools on the slide table with minimum clearance."""
    s = p.machine.slide_table
    GAP = 5.0
    
    def z_half_width(tool) -> float:
        ang = math.radians(tool.orientation_deg)
        bl = tool.holder.block_length
        bw = tool.holder.block_width
        return abs(bl / 2 * math.cos(ang)) + abs(bw / 2 * math.sin(ang))

    tools_sorted = sorted(tools, key=lambda t: t.mount_z)
    z_cursor = s.z_min
    for i, tool in enumerate(tools_sorted):
        hw = z_half_width(tool)
        new_z = z_cursor + hw
        new_z = max(s.z_min + hw, min(s.z_max - hw, new_z))
        tool.mount_z = round(new_z, 2)
        z_cursor = new_z + hw + GAP
    return tools_sorted

# ── Layout Packing and Position Setup API endpoints ───────────────────────

class PackRequest(BaseModel):
    tools: Optional[List[str]] = None
    green_align: bool = True

@app.post("/api/layout/pack")
def layout_pack(req: PackRequest):
    p = get_or_create_default_profile()
    
    # 1. Resolve tools to pack
    if req.tools:
        tools_to_pack = [p.get_tool(tid) for tid in req.tools if p.get_tool(tid) is not None]
    else:
        tools_to_pack = p.tools
        
    if len(tools_to_pack) < 2:
        raise HTTPException(status_code=400, detail="Must select at least 2 tools to pack.")
        
    # 2. Perform geometric packing
    tools_sorted = _auto_adjust_tools(p, tools_to_pack)
    
    # 3. Apply green alignment if requested
    adjust_mode, unsafe_ids = None, []
    if req.green_align and state_db["blocks"]:
        frames = state_db["frames"]
        blocks = state_db["blocks"]
        
        # Envelopes
        envs_by_tid = {}
        for f in frames:
            envs_by_tid.setdefault(f.active_tool_id, []).append((f.tip_x, f.tip_z))
        envs_np = {tid: np.asarray(pts, dtype=float) for tid, pts in envs_by_tid.items() if pts}
        cut_envs = per_tool_cutting_envelopes(blocks, step=1.0)
        
        adjust_mode, unsafe_ids = _green_align_packed_tools(p, tools_sorted, envs_np, cut_envs, step=2.0)
        
    # 4. Update slot_attach_z for slotted tools so they align correctly
    st = p.machine.slide_table
    for t in p.tools:
        if t.slot is not None:
            sz = st.slot_z(t.slot)
            if sz is not None:
                t.slot_attach_z = round(sz - t.mount_z, 2)
                
    p.snap_all_slotted()
    save_profile(p)
    
    # Reload G-code to update state
    if state_db["gcode_text"]:
        load_gcode_program(state_db["gcode_text"], state_db["gcode_name"])
        
    # Calculate spacing deltas
    deltas = []
    for i in range(len(tools_sorted) - 1):
        t_a = tools_sorted[i]
        t_b = tools_sorted[i+1]
        deltas.append({
            "a": t_a.id,
            "b": t_b.id,
            "dz": float(round(t_b.mount_z - t_a.mount_z, 2)),
            "dx": float(round(t_b.mount_x - t_a.mount_x, 2))
        })
        
    return {
        "mode": adjust_mode,
        "unsafe_ids": unsafe_ids,
        "deltas": deltas,
        "tools": [{"id": t.id, "mount_x": float(t.mount_x), "mount_z": float(t.mount_z)} for t in p.tools]
    }

@app.get("/api/layout/setups")
def get_layout_setups():
    p = get_or_create_default_profile()
    res = {}
    for n in ["1", "2", "3", "4"]:
        setup = p.position_setups.get(n)
        if setup is not None and setup.saved_at:
            res[n] = {
                "saved_at": setup.saved_at,
                "n_tools": len(setup.positions)
            }
        else:
            res[n] = None
    return {"setups": res}

@app.post("/api/layout/setups/{n}/save")
def save_layout_setup(n: str):
    if n not in ["1", "2", "3", "4"]:
        raise HTTPException(status_code=400, detail="Invalid setup slot. Must be 1-4.")
    p = get_or_create_default_profile()
    from ck40b_sim.models import PositionSetup, ToolPosition
    import datetime
    
    pos_dict = {}
    for t in p.tools:
        pos_dict[t.id] = ToolPosition(
            mount_x=t.mount_x,
            mount_z=t.mount_z,
            slot=t.slot,
            slot_attach_z=t.slot_attach_z
        )
        
    saved_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    p.position_setups[n] = PositionSetup(
        saved_at=saved_time,
        positions=pos_dict
    )
    
    save_profile(p)
    return get_layout_setups()

@app.post("/api/layout/setups/{n}/load")
def load_layout_setup(n: str):
    if n not in ["1", "2", "3", "4"]:
        raise HTTPException(status_code=400, detail="Invalid setup slot. Must be 1-4.")
    p = get_or_create_default_profile()
    setup = p.position_setups.get(n)
    if not setup:
        raise HTTPException(status_code=400, detail=f"Setup slot {n} is empty.")
        
    applied = 0
    missing = []
    for t in p.tools:
        if t.id in setup.positions:
            pos = setup.positions[t.id]
            t.mount_x = pos.mount_x
            t.mount_z = pos.mount_z
            t.slot = pos.slot
            t.slot_attach_z = pos.slot_attach_z
            applied += 1
        else:
            missing.append(t.id)
            
    p.snap_all_slotted()
    save_profile(p)
    
    if state_db["gcode_text"]:
        load_gcode_program(state_db["gcode_text"], state_db["gcode_name"])
        
    return {
        "applied": applied,
        "missing": missing
    }

# Mount static web directory
app.mount("/", StaticFiles(directory="web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8360)
