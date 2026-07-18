"""Fanuc-style lathe G-code parser.

Output: list of MotionBlock with positions in (X_radius_mm, Z_mm) machine coords.
X in G-code is diameter -> internally converted to radius.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Literal, Iterator

MoveType = Literal["rapid", "feed", "arc_cw", "arc_ccw", "tool_change", "home", "other"]


@dataclass
class MotionBlock:
    line_no: int
    raw: str
    tool_id: str
    move_type: MoveType
    start: tuple[float, float]  # (X_radius, Z)
    end: tuple[float, float]
    arc_center: tuple[float, float] | None = None  # (X_radius, Z) absolute
    feedrate: float | None = None
    spindle_rpm: float | None = None
    comment: str = ""


# Match address letters followed by optional sign and number
WORD_RE = re.compile(r"([A-Z])\s*(-?\d+\.?\d*|\.\d+)")
COMMENT_RE = re.compile(r"\(([^)]*)\)")
LINE_NO_RE = re.compile(r"^N(\d+)")


def _strip_block(line: str) -> tuple[str, str]:
    """Return (code, comment) with comments removed from code."""
    comments = " ".join(COMMENT_RE.findall(line))
    code = COMMENT_RE.sub("", line)
    # Also remove ;-style
    if ";" in code:
        code, c2 = code.split(";", 1)
        comments = (comments + " " + c2).strip()
    return code.strip(), comments.strip()


def tokenize(line: str) -> dict[str, list[float]]:
    """Parse a single line into {letter: [values]}. Multiple G/M allowed."""
    code, _ = _strip_block(line)
    words: dict[str, list[float]] = {}
    for letter, num in WORD_RE.findall(code):
        words.setdefault(letter, []).append(float(num))
    return words


def parse_tool_code(t_value: float) -> str:
    """T0101 -> 'T01' (use leading 2 digits as tool id)."""
    n = int(round(t_value))
    tool_num = n // 100 if n >= 100 else n
    return f"T{tool_num:02d}"


@dataclass
class ModalState:
    motion: int = 0  # G0/1/2/3
    plane: int = 18  # G17/18/19
    units: int = 21  # G20/21
    feed_mode: int = 99  # G98/99
    spindle_mode: int = 97  # G96/97
    work_offset: int = 54
    feedrate: float | None = None
    spindle_rpm: float | None = None
    tool_id: str = "T01"
    x: float = 0.0  # current X (radius)
    z: float = 0.0  # current Z


def parse(text: str, initial_pos: tuple[float, float] = (0.0, 0.0),
          z_offset: float = 0.0) -> list[MotionBlock]:
    """Parse full G-code program into motion blocks.

    initial_pos: starting (X_radius, Z) in workpiece frame. Defaults to (0,0)
    for backward compat; pass profile.machine_zero_workpiece() for accurate
    rapid paths at program start and after G28.

    z_offset: workpiece-face Z in the workpiece frame (G54 convention: program
    Z=0 is the workpiece face). All ABSOLUTE program Z words are shifted by this
    so the toolpath follows the face when stickout/raw-length changes. Pass
    profile.workpiece.z_face_position. Incremental W/U moves are unaffected.
    """
    state = ModalState(x=initial_pos[0], z=initial_pos[1])
    blocks: list[MotionBlock] = []
    home_pos = initial_pos

    for raw_line_idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("%") or line.startswith("O") or line.startswith("("):
            continue

        code, comment = _strip_block(line)
        if not code:
            continue

        # Extract N line number if present
        m = LINE_NO_RE.match(code)
        line_no = int(m.group(1)) if m else raw_line_idx
        if m:
            code = code[m.end():].strip()

        words = tokenize(code)

        # Tool change
        if "T" in words:
            new_tool = parse_tool_code(words["T"][0])
            if new_tool != state.tool_id:
                state.tool_id = new_tool
                blocks.append(MotionBlock(
                    line_no=line_no, raw=raw_line, tool_id=new_tool,
                    move_type="tool_change",
                    start=(state.x, state.z), end=(state.x, state.z),
                    comment=comment,
                ))

        # Modal G-codes
        if "G" in words:
            for g in words["G"]:
                gi = int(round(g))
                if gi in (0, 1, 2, 3):
                    state.motion = gi
                elif gi in (17, 18, 19):
                    state.plane = gi
                elif gi in (20, 21):
                    state.units = gi
                elif gi in (96, 97):
                    state.spindle_mode = gi
                elif gi in (98, 99):
                    state.feed_mode = gi
                elif 54 <= gi <= 59:
                    state.work_offset = gi
                elif gi == 28:
                    # Home: rapid to machine zero. Block becomes a rapid from
                    # current pos to home_pos. We flag this by overriding the
                    # motion target below.
                    state.motion = 0  # G28 implies rapid
                    words["__G28__"] = [1.0]
                elif gi == 50:
                    # spindle clamp or coordinate set; ignore for motion
                    pass

        if "F" in words:
            state.feedrate = words["F"][0]
        if "S" in words:
            state.spindle_rpm = words["S"][0]

        is_g28 = "__G28__" in words
        # Determine target position
        has_motion = is_g28 or "X" in words or "Z" in words or "U" in words or "W" in words
        if not has_motion:
            continue

        # X in G-code is diameter -> radius
        new_x = state.x
        new_z = state.z
        if is_g28:
            # G28 retracts only the axes named on the line (Fanuc convention).
            # G28 U0 -> X home only; G28 W0 -> Z home only; G28 alone -> both.
            has_x_word = "X" in words or "U" in words
            has_z_word = "Z" in words or "W" in words
            if not has_x_word and not has_z_word:
                new_x, new_z = home_pos
            else:
                if has_x_word:
                    new_x = home_pos[0]
                if has_z_word:
                    new_z = home_pos[1]
        else:
            if "X" in words:
                new_x = words["X"][0] / 2.0
            if "U" in words:
                # incremental X (diameter)
                new_x = state.x + words["U"][0] / 2.0
            if "Z" in words:
                new_z = words["Z"][0] + z_offset   # absolute Z → face-relative
            if "W" in words:
                new_z = state.z + words["W"][0]     # incremental: no offset

        # Determine move type
        if state.motion == 0:
            mtype: MoveType = "rapid"
        elif state.motion == 1:
            mtype = "feed"
        elif state.motion == 2:
            mtype = "arc_cw"
        elif state.motion == 3:
            mtype = "arc_ccw"
        else:
            mtype = "other"

        arc_center = None
        if mtype in ("arc_cw", "arc_ccw"):
            # I, K are incremental from start (I is X diameter offset -> radius)
            i = words.get("I", [0.0])[0] / 2.0
            k = words.get("K", [0.0])[0]
            arc_center = (state.x + i, state.z + k)

        blocks.append(MotionBlock(
            line_no=line_no, raw=raw_line, tool_id=state.tool_id,
            move_type=mtype,
            start=(state.x, state.z), end=(new_x, new_z),
            arc_center=arc_center,
            feedrate=state.feedrate, spindle_rpm=state.spindle_rpm,
            comment=comment,
        ))

        state.x, state.z = new_x, new_z

    return blocks


def discretize(block: MotionBlock, step: float = 0.5) -> Iterator[tuple[float, float]]:
    """Yield sample (X_radius, Z) points along a motion block."""
    sx, sz = block.start
    ex, ez = block.end

    if block.move_type in ("rapid", "feed"):
        dx, dz = ex - sx, ez - sz
        dist = (dx * dx + dz * dz) ** 0.5
        n = max(2, int(dist / step) + 1)
        for i in range(n + 1):
            t = i / n
            yield (sx + t * dx, sz + t * dz)
    elif block.move_type in ("arc_cw", "arc_ccw") and block.arc_center:
        import math
        cx, cz = block.arc_center
        r1 = math.hypot(sx - cx, sz - cz)
        r2 = math.hypot(ex - cx, ez - cz)
        r = (r1 + r2) / 2.0
        a1 = math.atan2(sx - cx, sz - cz)
        a2 = math.atan2(ex - cx, ez - cz)
        cw = block.move_type == "arc_cw"
        if cw and a2 > a1:
            a2 -= 2 * math.pi
        elif (not cw) and a2 < a1:
            a2 += 2 * math.pi
        arc_len = abs(a2 - a1) * r
        n = max(4, int(arc_len / step) + 1)
        for i in range(n + 1):
            t = i / n
            a = a1 + (a2 - a1) * t
            yield (cx + r * math.sin(a), cz + r * math.cos(a))
    else:
        yield block.start
        yield block.end
