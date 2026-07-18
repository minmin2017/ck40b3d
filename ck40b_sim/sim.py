"""Simulation frame builder: flatten G-code into per-sample positions.

Each frame = one sample of the *active* tool tip in workpiece (G54) frame,
plus which tool was active at that sample. The slide's translation at that
frame is implicit (= active_tip - active_tool.mount), so any other tool's
position = active_tip + (other.mount - active.mount).
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .gcode_parser import MotionBlock, discretize


@dataclass
class SimFrame:
    active_tool_id: str
    tip_x: float
    tip_z: float
    line_no: int
    is_cut: bool = False   # True for feed/arc (removes material); False for rapid
    move_type: str = ""    # original block move_type (rapid/feed/arc_cw/arc_ccw)


# Move types that remove material (turning cut). Rapids (G0) travel in air and
# must NOT carve — a G0 that enters the stock is a crash, handled as a collision.
_CUT_MOVES = ("feed", "arc_cw", "arc_ccw")


def build_frames(blocks: list[MotionBlock], step: float = 0.5) -> list[SimFrame]:
    frames: list[SimFrame] = []
    for b in blocks:
        if b.move_type in ("tool_change", "other"):
            continue
        if not b.tool_id:
            continue
        is_cut = b.move_type in _CUT_MOVES
        for x, z in discretize(b, step):
            frames.append(SimFrame(b.tool_id, x, z, b.line_no,
                                   is_cut=is_cut, move_type=b.move_type))
    return frames
