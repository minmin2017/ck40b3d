"""Geometry: slide envelope, obstacles, collision detection.

Coordinate system: (X_radius, Z) machine coords.
+X is "down" physically but in pure math we treat as +X.
The UI is responsible for flipping the visual axis.
"""
from __future__ import annotations
import math
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from .models import Profile, Tool
from .gcode_parser import MotionBlock, discretize


def per_tool_envelopes(blocks: list[MotionBlock], step: float = 0.5) -> dict[str, np.ndarray]:
    """All-motion envelope per tool. The first cut for each tool marks the end
    of the "approach" phase: rapids before that have a fictional start (we don't
    know machine home), so we keep only the end point of each pre-cut rapid.
    Once a feed/arc has happened for a tool, subsequent rapids are discretized
    normally."""
    by_tool: dict[str, list[tuple[float, float]]] = {}
    approaching: dict[str, bool] = {}

    def is_approaching(tid: str) -> bool:
        return approaching.get(tid, True)

    for b in blocks:
        if b.move_type == "tool_change":
            approaching[b.tool_id] = True
            continue
        if b.move_type == "other":
            continue
        if b.move_type in ("feed", "arc_cw", "arc_ccw"):
            approaching[b.tool_id] = False
            by_tool.setdefault(b.tool_id, []).extend(discretize(b, step))
        elif b.move_type == "rapid":
            if b.start == b.end:
                continue  # zero-length rapid (e.g. G28 with U0/W0) — fictional home
            if is_approaching(b.tool_id):
                by_tool.setdefault(b.tool_id, []).append(b.end)
            else:
                by_tool.setdefault(b.tool_id, []).extend(discretize(b, step))
    return {tid: np.array(pts, dtype=float) for tid, pts in by_tool.items() if pts}


def per_tool_cutting_envelopes(blocks: list[MotionBlock], step: float = 0.5) -> dict[str, np.ndarray]:
    """Like per_tool_envelopes but keeps only G1/G2/G3 (feed/arc) — moves that
    actually remove material. Used to compute the carved workpiece shape."""
    by_tool: dict[str, list[tuple[float, float]]] = {}
    for b in blocks:
        if b.move_type not in ("feed", "arc_cw", "arc_ccw"):
            continue
        by_tool.setdefault(b.tool_id, []).extend(discretize(b, step))
    return {tid: np.array(pts, dtype=float) for tid, pts in by_tool.items() if pts}


def drop_cutoff_front(remaining: np.ndarray, eps: float = 0.05) -> np.ndarray:
    """Material in FRONT of a through-center cut falls off the bar.

    When a facing/parting pass reaches (near) the spindle centerline at some Z,
    everything at larger Z is no longer attached — physically it drops as a
    disc. The min-radius-per-bin carve model cannot see that and used to keep
    a phantom full-diameter fin in front of the faced plane, which idle-tool
    collision checks then "hit". Bins are assumed ordered z_back → z_front.
    Mutates and returns `remaining`.
    """
    idx = np.where(remaining <= eps)[0]
    if idx.size:
        remaining[idx.max():] = 0.0
    return remaining


def carved_workpiece_polygon(
    profile: Profile,
    cutting_envelopes: dict[str, np.ndarray],
    z_step: float = 0.5,
) -> Polygon | MultiPolygon:
    """Workpiece outline after all cuts. For each Z bin, the remaining OD is
    the min X-radius any cutter reached (clipped to >=0). Z bins no cutter
    visited stay at raw_radius."""
    wp = profile.workpiece
    margin = profile.safety_margin
    raw_r = wp.raw_diameter / 2.0
    z_back = wp.z_face_position - wp.raw_length
    z_front = wp.z_face_position
    if not cutting_envelopes:
        return box(-raw_r - margin, z_back, raw_r + margin, z_front)
    pts_list = [env for env in cutting_envelopes.values() if env.size > 0]
    if not pts_list:
        return box(-raw_r - margin, z_back, raw_r + margin, z_front)
    all_pts = np.concatenate(pts_list, axis=0)
    z_bins = np.arange(z_back, z_front + z_step, z_step)
    n_bins = len(z_bins)
    remaining = np.full(n_bins, raw_r)
    z_idx = np.clip(
        ((all_pts[:, 1] - z_back) / z_step).astype(int), 0, n_bins - 1
    )
    # To prevent artificial uncut workpiece spikes caused by step size mismatches
    # between toolpath discretization and z_bins, we update a window of adjacent bins
    # (i - 1, i, i + 1) for each cutting sample point.
    for i, x in zip(z_idx, all_pts[:, 0]):
        for offset in (-1, 0, 1):
            idx = i + offset
            if 0 <= idx < n_bins:
                if x < remaining[idx]:
                    remaining[idx] = max(0.0, x)
    drop_cutoff_front(remaining)
    strips = []
    for i in range(n_bins):
        if remaining[i] <= 0.0:
            continue  # cut off / parted away — no material, margin or not
        r = remaining[i] + margin
        if r > 0:
            z_start = z_bins[i]
            z_end = min(z_start + z_step, z_front)
            if z_start < z_end:
                strips.append(box(-r, z_start, r, z_end))
    if not strips:
        return Polygon()
    return unary_union(strips)


def obstacles_polygon(
    profile: Profile,
    cutting_envelopes: dict[str, np.ndarray] | None = None,
    include_workpiece: bool = True,
) -> Polygon | MultiPolygon:
    """Chuck body + jaws (+ workpiece if include_workpiece), expanded by
    safety_margin.

    include_workpiece=False is used by the green-zone check: the tool is
    expected to touch the workpiece (that's the cut), so it would otherwise
    produce false positives. Fixtures (chuck/jaws) are still forbidden.

    If cutting_envelopes is provided and workpiece is included, the workpiece
    is computed as the carved final profile (raw minus all G1/G2/G3 sweeps).
    """
    margin = profile.safety_margin
    chuck = profile.chuck
    wp = profile.workpiece

    chuck_face_z = wp.chuck_face_z
    chuck_body_z_back = chuck_face_z - chuck.body_length_z
    body_r = chuck.body_diameter / 2.0 + margin
    jaw_r = chuck.jaw_outer_diameter / 2.0 + margin
    jaw_z_front = chuck_face_z + chuck.jaw_protrusion_z

    parts = [
        box(-body_r, chuck_body_z_back, body_r, chuck_face_z),
        box(-jaw_r, chuck_face_z, jaw_r, jaw_z_front),
    ]
    if include_workpiece:
        if cutting_envelopes:
            parts.append(carved_workpiece_polygon(profile, cutting_envelopes))
        else:
            wp_r = wp.raw_diameter / 2.0 + margin
            wp_z_back = wp.z_face_position - wp.raw_length
            wp_z_front = wp.z_face_position
            parts.append(box(-wp_r, wp_z_back, wp_r, wp_z_front))
    return unary_union(parts)


def _local_axes(tool: Tool) -> tuple[tuple[float, float], tuple[float, float]]:
    """Returns ((shank_x, shank_z), (perp_x, perp_z)) unit vectors."""
    theta = math.radians(tool.orientation_deg)
    sz, sx = math.cos(theta), math.sin(theta)
    pz, px = -sx, sz
    return (sx, sz), (px, pz)


def _rect_corner_offsets(tool: Tool, u0: float, u1: float,
                         half_w: float, v_center: float = 0.0) -> np.ndarray:
    """Rectangle corners as (dx, dz) offsets from tip, after orientation.
    v_center shifts the rectangle perpendicular to shank axis."""
    (sx, sz), (px, pz) = _local_axes(tool)
    v_lo = v_center - half_w
    v_hi = v_center + half_w
    corners_uv = [(u0, v_lo), (u1, v_lo), (u1, v_hi), (u0, v_hi)]
    return np.array([(u * sx + v * px, u * sz + v * pz) for u, v in corners_uv])


def _holder_corner_offsets(tool: Tool) -> np.ndarray:
    """4 corners of the holder block. Block is shifted in -v by tip_v_offset
    so that the tip sits at the requested v-position relative to the block."""
    h = tool.holder
    return _rect_corner_offsets(tool, h.shank_length, h.shank_length + h.block_length,
                                h.block_width / 2.0, v_center=-h.tip_v_offset)


def _shank_corner_offsets(tool: Tool) -> np.ndarray:
    """4 corners of the shank rectangle (tip to block front). Shank stays
    aligned with the tip (v centered on 0)."""
    h = tool.holder
    return _rect_corner_offsets(tool, 0.0, h.shank_length, h.shank_diameter / 2.0,
                                v_center=0.0)


def tool_shank_polygon(tool: Tool, tip_xz: tuple[float, float]) -> Polygon:
    tx, tz = tip_xz
    return Polygon([(tx + dx, tz + dz) for dx, dz in _shank_corner_offsets(tool)])


def tool_holder_polygon(tool: Tool, tip_xz: tuple[float, float]) -> Polygon:
    """Holder block polygon with tip at tip_xz, rotated by tool.orientation_deg.

    Shank axis = unit vector pointing from tip INTO the block. orientation_deg
    is the angle of this axis measured CCW from +Z in the (Z, X) plane:
      0deg  -> shank along +Z (tip points -Z, e.g. boring toward chuck)
      90deg -> shank along +X (tip points -X, e.g. OD turning up to centerline)

    Block occupies along-shank distance [shank_length, shank_length + block_length]
    and perpendicular [-block_width/2, +block_width/2].
    """
    tx, tz = tip_xz
    offs = _holder_corner_offsets(tool)
    return Polygon([(tx + dx, tz + dz) for dx, dz in offs])


def holder_within_slide(tool: Tool, mount_xz: tuple[float, float], profile: Profile) -> bool:
    """Check the holder block fits inside the slide table extent (slide frame).

    mount_xz is the tool's tip position in the SLIDE frame.
    """
    poly = tool_holder_polygon(tool, mount_xz)
    minx, minz, maxx, maxz = poly.bounds[0], poly.bounds[1], poly.bounds[2], poly.bounds[3]
    s = profile.machine.slide_table
    return (minx >= s.x_min and maxx <= s.x_max
            and minz >= s.z_min and maxz <= s.z_max)


def check_collision_for_tool(
    tool: Tool,
    reference_offset: tuple[float, float],
    active_envelopes: dict[str, np.ndarray],
    obstacles: Polygon | MultiPolygon,
    profile: Profile,
    sample_stride: int = 1,
) -> tuple[bool, list[tuple[str, int]]]:
    """Check holder vs obstacles across all active tools' envelopes.

    this_tool_tip_pos = active_tool_tip_pos + (tool.offset - active_tool.offset)
    """
    hits: list[tuple[str, int]] = []
    if obstacles.is_empty:
        return False, hits

    # G-code env tracks the active tool's CUTTING TIP. The holder BODY (block +
    # shank) is offset BACK from the cutting tip by the active tool's tip_dx/dz.
    # Candidate's block position = env + (tool.mount - active.mount) - active.tip_off
    tool_mount = np.array([tool.mount_x, tool.mount_z])
    block_offs = _holder_corner_offsets(tool)
    shank_offs = _shank_corner_offsets(tool)

    for active_tid, env in active_envelopes.items():
        if env.size == 0:
            continue
        if active_tid == tool.id:
            # Same physical tool moving along its own path
            active_mount = tool_mount
            active_tip_off = np.array([tool.holder.tip_dx, tool.holder.tip_dz])
        else:
            active_tool = profile.get_tool(active_tid)
            if active_tool is None:
                active_mount = np.array([0.0, 0.0])
                active_tip_off = np.array([0.0, 0.0])
            else:
                active_mount = np.array([active_tool.mount_x, active_tool.mount_z])
                active_tip_off = np.array([active_tool.holder.tip_dx, active_tool.holder.tip_dz])

        # env points are cutting-tip positions; convert to block positions:
        delta = tool_mount - active_mount - active_tip_off
        this_tip_positions = env + delta

        for idx in range(0, len(this_tip_positions), sample_stride):
            tx, tz = this_tip_positions[idx]
            block = Polygon([(tx + dx, tz + dz) for dx, dz in block_offs])
            shank = Polygon([(tx + dx, tz + dz) for dx, dz in shank_offs])
            if block.intersects(obstacles) or shank.intersects(obstacles):
                hits.append((active_tid, idx))
                if len(hits) >= 5:
                    return True, hits
    return len(hits) > 0, hits


def compute_green_zone_grid(
    profile: Profile,
    active_envelopes: dict[str, np.ndarray],
    candidate_tool: Tool,
    cutting_envelopes: dict[str, np.ndarray] | None = None,
    grid_x: tuple[float, float, float] = (-50.0, 50.0, 2.0),
    grid_z: tuple[float, float, float] = (-80.0, 80.0, 2.0),
    sample_stride: int = 4,
    consider_table: bool = True,
    check_tool_overlap: bool = True,
    progress_cb=None,
):
    """2D grid: 1 = safe (no collision; also holder fits in slide table when
    consider_table=True). 0 = hard collision (chuck/jaws/final workpiece).
    2 = danger/orange: the tip sweeps a forbidden zone, OR the holder clears
    the final carved shape but sweeps the raw bar — i.e. it collides or not
    depending on WHEN in the program the sweep happens (timing risk).

    Vectorized AABB implementation. The candidate's block and shank are
    tested as two separate axis-aligned bounding boxes (after rotation by
    orientation_deg) — exact for orient ∈ {0°, 90°, 180°, 270°} and
    conservative (slightly more red) for oblique angles.

    Performance: a 1 mm grid over a 250×240 mm slide took ~80 s with the
    previous shapely-per-cell-per-sample implementation. The numpy version
    runs in ~1-2 s by computing all AABB overlaps in one broadcast.

    progress_cb: optional callable(fraction: float) invoked after each
    processed chunk (fraction in [0, 1]) so a caller can show a progress
    bar/percentage — this function itself stays UI-agnostic (no Qt/etc.),
    the callback is the caller's hook to update a widget and pump the event
    loop if it wants to stay responsive during the computation.
    """
    margin = profile.safety_margin
    ch = profile.chuck
    wp = profile.workpiece
    body_r = ch.body_diameter / 2.0 + margin
    jaw_r = ch.jaw_outer_diameter / 2.0 + margin
    # Each obstacle as (x_min, z_min, x_max, z_max).
    # The raw bar IS an obstacle for a PARKED candidate: while another tool runs,
    # the shared slide drags the idle candidate around the workpiece frame, and
    # its holder must not sweep into the stock. (The candidate's OWN cutting path
    # is skipped below, so this does not flag legitimate cutting.) AABB is exact
    # for the cylindrical bar in the XZ section. Worst case = full raw bar.
    raw_r = wp.raw_diameter / 2.0
    wp_z_back = wp.z_face_position - wp.raw_length

    # Use carved workpiece polygon strips as AABBs when cutting envelopes are provided
    # so that parked tools are checked against the cut workpiece, not the uncut raw bar.
    carved_strips = False
    if cutting_envelopes:
        pts_list = [env for env in cutting_envelopes.values() if env.size > 0]
        if pts_list:
            all_pts = np.concatenate(pts_list, axis=0)
            z_step = 0.5
            z_bins = np.arange(wp_z_back, wp.z_face_position + z_step, z_step)
            n_bins = len(z_bins)
            remaining = np.full(n_bins, raw_r)
            z_idx = np.clip(
                ((all_pts[:, 1] - wp_z_back) / z_step).astype(int), 0, n_bins - 1
            )
            # Apply the windowed update to bridge gaps due to discretization mismatches
            for i, x in zip(z_idx, all_pts[:, 0]):
                for offset in (-1, 0, 1):
                    idx = i + offset
                    if 0 <= idx < n_bins:
                        if x < remaining[idx]:
                            remaining[idx] = max(0.0, x)
            drop_cutoff_front(remaining)
            wp_aabbs = []
            for i in range(n_bins):
                if remaining[i] <= 0.0:
                    continue  # parted/faced off — that material is gone
                r = remaining[i] + margin
                if r > 0:
                    z_start = z_bins[i]
                    z_end = min(z_start + z_step, wp.z_face_position)
                    if z_start < z_end:
                        wp_aabbs.append([-r, z_start, r, z_end])
            wp_aabbs = np.array(wp_aabbs, dtype=float).reshape(-1, 4)
            carved_strips = True
        else:
            wp_aabbs = np.array([[-raw_r, wp_z_back, raw_r, wp.z_face_position]], dtype=float)
    else:
        wp_aabbs = np.array([[-raw_r, wp_z_back, raw_r, wp.z_face_position]], dtype=float)

    obstacle_list = [
        [-body_r, wp.chuck_face_z - ch.body_length_z,
         body_r, wp.chuck_face_z],                       # chuck body
        [-jaw_r, wp.chuck_face_z,
         jaw_r, wp.chuck_face_z + ch.jaw_protrusion_z],  # jaws
    ]
    obstacle_aabbs = np.vstack([np.array(obstacle_list, dtype=float), wp_aabbs])

    # Timing-risk obstacle: the strips above describe the workpiece AFTER the
    # whole program has run, but during playback material is only removed when
    # the cut that removes it actually executes — early in the program the bar
    # is still at full raw diameter. A cell that clears the final shape yet
    # sweeps into the raw bar may or may not collide depending on WHEN the
    # sweep happens, so it is classed DANGER (orange) rather than safe. Green
    # alone then means "safe at every instant of the program". Exact per-frame
    # material would need time-stamped envelopes; the raw bar is the correct
    # conservative bound (material(t) ⊆ raw bar for all t).
    timing_aabb = None
    if carved_strips:
        timing_aabb = (-(raw_r + margin), wp_z_back,
                       raw_r + margin, wp.z_face_position)

    xs = np.arange(grid_x[0], grid_x[1] + 1e-9, grid_x[2])
    zs = np.arange(grid_z[0], grid_z[1] + 1e-9, grid_z[2])
    nx, nz = len(xs), len(zs)
    result = np.ones((nz, nx), dtype=np.int8)

    base_mx = candidate_tool.mount_x
    base_mz = candidate_tool.mount_z

    # Holder+shank AABB in the tool's local frame (already rotated).
    bo = _holder_corner_offsets(candidate_tool)
    so = _shank_corner_offsets(candidate_tool)
    all_offs = np.vstack([bo, so])
    h_dx_min, h_dz_min = all_offs.min(axis=0)
    h_dx_max, h_dz_max = all_offs.max(axis=0)
    # Per-part AABBs for obstacle tests: the combined box pads the narrow
    # shank out to the block's width (6 mm phantom per side on the default
    # holders), flagging touches that never happen. Testing block and shank
    # separately is exact for orient ∈ {0°, 90°, 180°, 270°}.
    part_aabbs = [
        (offs[:, 0].min(), offs[:, 1].min(), offs[:, 0].max(), offs[:, 1].max())
        for offs in (bo, so)
    ]

    # ----- Slide-fit prefilter (per cell, vectorized) -----
    # (nz, nx) grids of test mount positions.
    DX, DZ = np.meshgrid(xs, zs)
    test_mx = base_mx + DX
    test_mz = base_mz + DZ
    s = profile.machine.slide_table
    if consider_table:
        in_slide = ((test_mx + h_dx_min >= s.x_min)
                    & (test_mx + h_dx_max <= s.x_max)
                    & (test_mz + h_dz_min >= s.z_min)
                    & (test_mz + h_dz_max <= s.z_max))
        result[~in_slide] = 0
    else:
        in_slide = np.ones((nz, nx), dtype=bool)

    # ----- Tool-to-tool overlap (single-candidate reposition only) -----
    # Meaning here: "if I re-bolt ONLY this candidate to mount+offset, does its
    # holder hit another (stationary) tool?" — correct when repositioning one
    # tool. It is INTENTIONALLY skipped for the global-intersection path
    # (check_tool_overlap=False): there every tool would be remounted to the
    # same world point, which is not a real gang configuration and over-reddens
    # the zone. Tool-tool for the gang as a whole is a static layout check done
    # separately (MainWindow._static_tool_overlaps).
    if check_tool_overlap:
        cand_xmin = test_mx + h_dx_min
        cand_xmax = test_mx + h_dx_max
        cand_zmin = test_mz + h_dz_min
        cand_zmax = test_mz + h_dz_max
        for other in profile.tools:
            if other.id == candidate_tool.id:
                continue
            o_offs = np.vstack([_holder_corner_offsets(other),
                                _shank_corner_offsets(other)])
            o_dx_min, o_dz_min = o_offs.min(axis=0)
            o_dx_max, o_dz_max = o_offs.max(axis=0)
            o_xmin = other.mount_x + o_dx_min
            o_xmax = other.mount_x + o_dx_max
            o_zmin = other.mount_z + o_dz_min
            o_zmax = other.mount_z + o_dz_max
            overlap = ((cand_xmin < o_xmax) & (cand_xmax > o_xmin)
                       & (cand_zmin < o_zmax) & (cand_zmax > o_zmin))
            result[overlap] = 0

    # Flat arrays of the cells we still need to check.
    iz_idx, ix_idx = np.where(in_slide)
    if iz_idx.size == 0:
        if progress_cb is not None:
            progress_cb(1.0)
        return result, xs, zs
    cell_mx = test_mx[iz_idx, ix_idx]  # (n_cells,)
    cell_mz = test_mz[iz_idx, ix_idx]

    collide = np.zeros(cell_mx.shape, dtype=bool)
    # danger accumulates both timing-risk hits (raw-bar sweep, filled in the
    # main loop below) and forbidden-zone sweeps (separate pass further down).
    danger = np.zeros(cell_mx.shape, dtype=bool)

    # Process in chunks of cells to bound memory: (chunk × n_samples × ~32 B)
    CHUNK = 4096

    active_items = [
        (act_tid, env) for act_tid, env in active_envelopes.items()
        if env.size > 0 and act_tid != candidate_tool.id
    ]
    enabled_fz_preview = [fz for fz in getattr(profile, 'forbidden_zones', []) if fz.enabled]
    n_chunks = max(1, -(-cell_mx.size // CHUNK))  # ceil division
    total_units = max(1, n_chunks * len(active_items) * (2 if enabled_fz_preview else 1))
    done_units = 0

    def _tick():
        nonlocal done_units
        done_units += 1
        if progress_cb is not None:
            progress_cb(min(1.0, done_units / total_units))

    for act_tid, env in active_items:
        act = profile.get_tool(act_tid) or candidate_tool
        # env stores active tool cutting-tip positions. Candidate holder
        # block reference at sample i = env[i] + (test_mount - act.mount
        # - act.tip_off). Holder polygon AABB = block_ref + h_dx/h_dz min/max.
        env_s = env[::sample_stride] if sample_stride > 1 else env
        # env_s shape (S, 2)
        env_x = env_s[:, 0]
        env_z = env_s[:, 1]
        off_const_x = -act.mount_x - act.holder.tip_dx
        off_const_z = -act.mount_z - act.holder.tip_dz

        for start in range(0, cell_mx.size, CHUNK):
            _tick()
            end = min(start + CHUNK, cell_mx.size)
            # Skip cells already known to collide.
            sel = ~collide[start:end]
            if not sel.any():
                continue
            cmx = cell_mx[start:end][sel]   # (k,)
            cmz = cell_mz[start:end][sel]
            # block_ref per (cell, sample): shape (k, S)
            # offset per cell: cmx + off_const_x
            ox = (cmx + off_const_x)[:, None]   # (k, 1)
            oz = (cmz + off_const_z)[:, None]
            # Test block and shank AABBs separately at each (cell, sample)
            # against each obstacle AABB; any-overlap reduction across
            # obstacles, then across samples.
            cell_hit = np.zeros(cmx.size, dtype=bool)
            for p_dx_min, p_dz_min, p_dx_max, p_dz_max in part_aabbs:
                bxmin = env_x[None, :] + ox + p_dx_min
                bxmax = env_x[None, :] + ox + p_dx_max
                bzmin = env_z[None, :] + oz + p_dz_min
                bzmax = env_z[None, :] + oz + p_dz_max
                for ox_min, oz_min, ox_max, oz_max in obstacle_aabbs:
                    overlap = ((bxmin < ox_max) & (bxmax > ox_min)
                              & (bzmin < oz_max) & (bzmax > oz_min))
                    cell_hit |= overlap.any(axis=1)
                    if cell_hit.all():
                        break
                if cell_hit.all():
                    break
            # Write back into the full collide array. Build absolute indices
            # for the unprocessed cells that newly hit, since slice-then-mask
            # assignment on numpy arrays does not propagate.
            sub_idx = np.where(sel)[0]
            collide[start + sub_idx[cell_hit]] = True

            # Timing-risk sweep against the raw bar (see timing_aabb above).
            # Independent skip mask: hard-colliding cells may also be marked,
            # which is harmless — red wins when states are applied.
            if timing_aabb is not None:
                tsel = ~danger[start:end]
                if tsel.any():
                    tmx = cell_mx[start:end][tsel]
                    tmz = cell_mz[start:end][tsel]
                    tox = (tmx + off_const_x)[:, None]
                    toz = (tmz + off_const_z)[:, None]
                    t_hit = np.zeros(tmx.size, dtype=bool)
                    r_xmin, r_zmin, r_xmax, r_zmax = timing_aabb
                    for p_dx_min, p_dz_min, p_dx_max, p_dz_max in part_aabbs:
                        overlap = ((env_x[None, :] + tox + p_dx_min < r_xmax)
                                   & (env_x[None, :] + tox + p_dx_max > r_xmin)
                                   & (env_z[None, :] + toz + p_dz_min < r_zmax)
                                   & (env_z[None, :] + toz + p_dz_max > r_zmin))
                        t_hit |= overlap.any(axis=1)
                    t_idx = np.where(tsel)[0]
                    danger[start + t_idx[t_hit]] = True

    # ---- Danger Zone (forbidden-zone) sweep -----
    # The candidate is PARKED while OTHER tools run their programs; the shared
    # slide drags the candidate's tip around the workpiece frame. Mark any mount
    # offset whose tip sweeps into an enabled forbidden zone DURING another
    # tool's motion as DANGER (result == 2, drawn orange) — distinct from a hard
    # chuck/jaw collision (result == 0, red). A static home-pose check alone
    # misses this (the tip only reaches the zone mid-motion), which is why such
    # cells used to read green even though playback flags a collision there.
    enabled_fz = [fz for fz in getattr(profile, 'forbidden_zones', []) if fz.enabled]
    if enabled_fz:
        c_tip_dx = candidate_tool.holder.tip_dx
        c_tip_dz = candidate_tool.holder.tip_dz
        for act_tid, env in active_items:
            act = profile.get_tool(act_tid) or candidate_tool
            env_s = env[::sample_stride] if sample_stride > 1 else env
            env_x = env_s[:, 0]
            env_z = env_s[:, 1]
            # candidate tip = env + (test_mount - act.mount - act.tip_off) + cand.tip_off
            off_x = -act.mount_x - act.holder.tip_dx + c_tip_dx
            off_z = -act.mount_z - act.holder.tip_dz + c_tip_dz
            for start in range(0, cell_mx.size, CHUNK):
                _tick()
                end = min(start + CHUNK, cell_mx.size)
                sel = ~danger[start:end]
                if not sel.any():
                    continue
                cmx = cell_mx[start:end][sel]
                cmz = cell_mz[start:end][sel]
                tip_x = env_x[None, :] + (cmx + off_x)[:, None]   # (k, S)
                tip_z = env_z[None, :] + (cmz + off_z)[:, None]
                cell_in = np.zeros(cmx.size, dtype=bool)
                for fz in enabled_fz:
                    inside = ((tip_x >= fz.x_min) & (tip_x <= fz.x_max)
                              & (tip_z >= fz.z_min) & (tip_z <= fz.z_max))
                    cell_in |= inside.any(axis=1)
                sub_idx = np.where(sel)[0]
                danger[start + sub_idx[cell_in]] = True

    # Apply states: hard collision (red) wins; danger (orange) only upgrades
    # cells that are still green — never repaints tool-overlap red as orange.
    result[iz_idx[collide], ix_idx[collide]] = 0
    only_danger = danger & ~collide
    dz_i, dx_i = iz_idx[only_danger], ix_idx[only_danger]
    keep = result[dz_i, dx_i] == 1
    result[dz_i[keep], dx_i[keep]] = 2

    # Static home-pose forbidden check (covers the parked pose itself).
    if enabled_fz:
        slide_ox = profile.machine.slide_origin_x
        slide_oz = profile.machine.slide_origin_z
        tip_x_grid = test_mx + slide_ox + candidate_tool.holder.tip_dx
        tip_z_grid = test_mz + slide_oz + candidate_tool.holder.tip_dz
        for fz in enabled_fz:
            in_fz = ((tip_x_grid >= fz.x_min) & (tip_x_grid <= fz.x_max)
                     & (tip_z_grid >= fz.z_min) & (tip_z_grid <= fz.z_max))
            result[in_fz & (result == 1)] = 2

    if progress_cb is not None:
        progress_cb(1.0)
    return result, xs, zs


def compute_workpiece_limits(
    profile: Profile,
    active_envelopes: dict[str, np.ndarray],
    cutting_envelopes: dict[str, np.ndarray] | None = None,
    max_diameter: float = 500.0,
    max_extra_length: float = 300.0,
    tol: float = 0.5,
    sample_stride: int = 4,
) -> dict:
    """Binary-search for the largest workpiece (diameter / length) that all
    passive tool holders can survive without colliding with the raw workpiece,
    and derive the minimum dimensions from cutting paths.

    Chuck/jaw position is held fixed (independent of the test workpiece size).

    Returns dict with keys:
      max_diameter – largest raw_diameter (mm) before a holder hits the WP,
                     or None if the current size already collides
      max_length   – largest raw_length (mm) before a holder hits the WP,
                     or None if the current size already collides
      min_diameter – smallest raw_diameter (mm) for tools to still reach material
      min_length   – smallest raw_length (mm) for tools to complete all cuts
      current_ok   – True if current WP dimensions are already collision-free
    """
    from shapely.geometry import box as shp_box
    from shapely.ops import unary_union

    margin = profile.safety_margin
    ch = profile.chuck
    wp = profile.workpiece

    # Chuck/jaws fixed at original position — independent of test WP size.
    chuck_face_z = wp.chuck_face_z
    body_r = ch.body_diameter / 2.0 + margin
    jaw_r = ch.jaw_outer_diameter / 2.0 + margin
    chuck_obs = unary_union([
        shp_box(-body_r, chuck_face_z - ch.body_length_z, body_r, chuck_face_z),
        shp_box(-jaw_r, chuck_face_z, jaw_r, chuck_face_z + ch.jaw_protrusion_z),
    ])

    cur_d = wp.raw_diameter
    cur_l = wp.raw_length
    z_front = wp.z_face_position

    def _collides(test_d: float, test_l: float) -> bool:
        orig_d = wp.raw_diameter
        orig_l = wp.raw_length
        wp.raw_diameter = test_d
        wp.raw_length = test_l
        try:
            if cutting_envelopes:
                wp_poly = carved_workpiece_polygon(profile, cutting_envelopes)
            else:
                r = test_d / 2.0 + margin
                wp_poly = shp_box(-r, z_front - test_l, r, z_front)
            obs = chuck_obs.union(wp_poly)
        finally:
            wp.raw_diameter = orig_d
            wp.raw_length = orig_l

        for tool in profile.tools:
            if not tool.active_in_program:
                continue
            # Only flag a holder dragged AROUND the bar by OTHER tools' motions
            # (passive-holder clearance). A tool touching the bar along its OWN
            # cutting path is the cut itself, not a collision — testing it
            # against the full raw stock makes every active cutter a false
            # positive. Same convention as the green zone (which skips the
            # candidate's own path) and obstacles_polygon(include_workpiece=False).
            other_envs = {tid: env for tid, env in active_envelopes.items()
                          if tid != tool.id}
            if not other_envs:
                continue
            hit, _ = check_collision_for_tool(
                tool, (0.0, 0.0), other_envs, obs, profile,
                sample_stride=sample_stride)
            if hit:
                return True
        return False

    def _bisect_max(test_fn, lo: float, hi: float):
        # Precondition: lo (the current size) must be collision-free, so that the
        # search grows upward toward the first colliding size. If lo *already*
        # collides there is no valid "max" to find — return None instead of
        # silently collapsing back to lo (which used to read as "max = current").
        if test_fn(lo):
            return None
        while hi - lo > tol:
            mid = (lo + hi) / 2.0
            if test_fn(mid):
                hi = mid
            else:
                lo = mid
        return lo

    current_ok = not _collides(cur_d, cur_l)

    if current_ok:
        # Max diameter — keep length fixed at current.
        if _collides(max_diameter, cur_l):
            max_d = _bisect_max(lambda d: _collides(d, cur_l), cur_d, max_diameter)
        else:
            max_d = max_diameter

        # Max length — keep diameter fixed at current.
        hi_l = cur_l + max_extra_length
        if _collides(cur_d, hi_l):
            max_l = _bisect_max(lambda l: _collides(cur_d, l), cur_l, hi_l)
        else:
            max_l = hi_l
    else:
        # Current workpiece already collides with a holder → there is no size
        # window above it, so neither max is meaningful. Signal with None; the
        # caller already flags current_ok=False to the user.
        max_d = None
        max_l = None

    # ---- Min diameter and min length (derived from cutting paths) ----
    # Use cutting_envelopes when available (G1/G2/G3 only); fall back to all.
    cut_src = cutting_envelopes if cutting_envelopes else active_envelopes
    arrays_x = [env[:, 0] for env in cut_src.values() if env.size > 0]
    arrays_z = [env[:, 1] for env in cut_src.values() if env.size > 0]

    if arrays_x:
        # Tools must reach raw surface: raw_radius >= max cutting X encountered.
        min_d = float(np.concatenate(arrays_x).max()) * 2.0
    else:
        min_d = 0.0

    if arrays_z:
        # Workpiece must extend as far back as the deepest cut in Z.
        min_z_cut = float(np.concatenate(arrays_z).min())
        min_l = float(max(0.0, z_front - min_z_cut))
    else:
        min_l = 0.0

    return {
        "max_diameter": max_d,
        "max_length": max_l,
        "min_diameter": min_d,
        "min_length": min_l,
        "current_ok": current_ok,
    }
