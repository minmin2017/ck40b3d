"""Data models for machine, chuck, workpiece, tools.

Coordinate frames (all in the X-Z plane; X = radius, +X toward centerline-down,
+Z toward the tailstock):

1. WORKPIECE frame — the one physical frame everything is drawn in. Origin at
   the spindle centerline (X=0) and the workpiece face (Z=0).

2. TABLE / SLIDE frame — where the tools are bolted and where the slide-table
   bounds live. Tool `mount_x/mount_z` and `SlideTable.x_min..z_max` are given
   here. The table is rigid: a tool's spot on the table never changes when you
   pick a different reference tool. At machine home the table's origin sits at
   `machine.slide_origin_x/z` in the workpiece frame, so:

       home position in workpiece = slide_origin + (table coordinate)

   `Profile.home_block(tool)` / `home_tip(tool)` are the single source of truth
   for this mapping — use them instead of re-deriving it.

3. G54 (program) frame — only the G-code cares about this. Its origin is the
   REFERENCE tool's tip (where the operator touched off). `tip_in_workpiece` and
   `machine_zero_workpiece` translate between G54 and the workpiece frame for the
   animation; they are the ONLY places the reference tool affects geometry.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Stroke(BaseModel):
    x_min: float = -50.0
    x_max: float = 200.0
    z_min: float = -300.0
    z_max: float = 50.0


class SlideTable(BaseModel):
    """Rectangular cross-slide that carries the tool holder blocks.

    Extent is given in the TABLE frame — the same frame as each tool's
    `mount_x/mount_z`. It is fixed to the physical table and does NOT depend on
    which tool is the reference tool. A candidate mounting offset is valid only
    if the holder block stays inside this rectangle. To place the table in the
    workpiece frame, add `machine.slide_origin_x/z` (see module docstring).
    """
    x_min: float = -10.0
    x_max: float = 220.0
    z_min: float = -120.0
    z_max: float = 120.0

    # T-slot visualization — two slots running along the full X span of the table
    show_slots: bool = True
    slot_z1: float = -30.0   # Z position of slot 1 in TABLE frame (mm)
    slot_z2: float = 30.0    # Z position of slot 2 in TABLE frame (mm)
    slot_width: float = 10.0  # width of each slot (mm)

    def slot_z(self, n: int | None) -> float | None:
        """Table-frame Z of slot `n` (1 or 2), or None for an invalid slot."""
        if n == 1:
            return self.slot_z1
        if n == 2:
            return self.slot_z2
        return None

    def normalize(self) -> bool:
        """Repair inverted bounds (min > max) by swapping. Returns True if a
        swap was needed. A degenerate rectangle (x_min > x_max) makes the
        green-zone grid empty and crashes the heatmap renderer, so callers
        should normalize after any edit and on load."""
        changed = False
        if self.x_min > self.x_max:
            self.x_min, self.x_max = self.x_max, self.x_min
            changed = True
        if self.z_min > self.z_max:
            self.z_min, self.z_max = self.z_max, self.z_min
            changed = True
        return changed


class Machine(BaseModel):
    name: str = "CK40B-#1"
    stroke: Stroke = Field(default_factory=Stroke)
    slide_table: SlideTable = Field(default_factory=SlideTable)
    # Slide table origin (slide frame 0,0 = x_min,z_min corner) position
    # in workpiece (G54) frame when the carriage is at machine home.
    # These are fixed machine properties — independent of which tool is the
    # reference tool. Changing the reference tool does NOT shift these values.
    slide_origin_x: float = 220.0   # X_radius (mm) in workpiece frame
    slide_origin_z: float = 70.0    # Z (mm) in workpiece frame


class Chuck(BaseModel):
    body_diameter: float = 160.0
    body_length_z: float = 40.0
    jaw_protrusion_z: float = 25.0
    jaw_outer_diameter: float = 180.0


class Workpiece(BaseModel):
    raw_diameter: float = 70.0
    raw_length: float = 50.0
    grip_length_in_chuck: float = 15.0
    z_face_position: float = 0.0

    @property
    def chuck_face_z(self) -> float:
        return self.z_face_position - self.raw_length


class Holder(BaseModel):
    """Rectangular block + shank in the holder's LOCAL frame.

    Local frame convention: tool tip at local origin (0,0), shank/block extends
    along local +u axis (u is the axis from tip into the block). The block
    occupies u in [shank_length, shank_length + block_length] and v in
    [-block_width/2, +block_width/2]. The shank is u in [0, shank_length],
    v in [-shank_diameter/2, +shank_diameter/2]. The whole thing is rotated by
    tool.orientation_deg and translated so the tip sits at the tool tip XZ.
    """
    block_width: float = 24.0      # extent perpendicular to shank axis
    block_length: float = 60.0     # extent along shank axis (block body only)
    shank_length: float = 30.0     # tip-to-block-front distance along shank
    shank_diameter: float = 12.0   # thickness of the shank (tool bar / boring bar)
    # Signed offset (mm) of the tip from the block's perpendicular CENTER.
    # 0 = tip on the block's centerline (current behavior).
    # +block_width/2 = tip at the block's top edge (e.g. OD turning insert
    # mounted at the upper corner). -block_width/2 = tip at the bottom edge.
    tip_v_offset: float = 0.0
    # Additional tip position offsets in MACHINE frame (X_radius, Z).
    # Applied on top of mount_x/mount_z when drawing the holder and checking
    # collisions. Use these to fine-tune where the cutting point appears
    # relative to the measured mount position.
    tip_dx: float = 0.0   # offset along X_radius axis (mm)
    tip_dz: float = 0.0   # offset along Z axis (mm)


class Tool(BaseModel):
    id: str = "T01"
    name: str = "OD Turning"
    type: Literal["turning_OD", "turning_ID", "boring", "drilling", "parting", "threading", "other"] = "turning_OD"
    # Tool TIP position in the TABLE frame — where the tip sits on the slide
    # table (measure with caliper/CAD). Same frame as SlideTable bounds; must be
    # consistent across all tools. mount_x is a radius (mm).
    #   home tip in workpiece = slide_origin + mount (+ holder tip offset)
    #   tip in G54 (program)  = mount - reference tool's mount
    mount_x: float = 0.0
    mount_z: float = 0.0
    # Direction the shank extends from the tip, measured in the (X_radius, Z) plane.
    # 0 deg = shank along +Z (tip points -Z toward chuck — boring/drill/tap).
    # 90 deg = shank along +X (tip points -X toward centerline — OD turning).
    # Tip points in -shank direction.
    orientation_deg: float = 90.0
    holder: Holder = Field(default_factory=Holder)
    active_in_program: bool = True
    color: str = "#1f77b4"
    # T-slot assignment: None = free positioning, 1 = slot 1, 2 = slot 2
    slot: int | None = None
    # Z distance from tool TIP (mount_z) to the T-slot bolt hole on this holder (mm).
    # When slot is set, mount_z is locked to: slot_z<n> - slot_attach_z
    slot_attach_z: float = 0.0


class ForbiddenZone(BaseModel):
    """Rectangular no-entry zone in workpiece (machine) coordinates.

    Both the candidate tool tip AND any active tool tip must not fall inside
    an enabled zone at the home position for the candidate's grid cell to be
    considered valid (green).
    """
    name: str = "Zone 1"
    x_min: float = 0.0
    x_max: float = 90.0
    z_min: float = -100.0
    z_max: float = -50.0
    enabled: bool = True


class ToolPosition(BaseModel):
    """A snapshot of one tool's mount placement — the fields that "Edit
    Position" and the T-slot system can change. Independent of holder
    geometry/type so a saved setup still applies if the holder was edited."""
    mount_x: float = 0.0
    mount_z: float = 0.0
    slot: int | None = None
    slot_attach_z: float = 0.0


class PositionSetup(BaseModel):
    """One named slot (1-4) of saved tool positions for the gang slide,
    keyed by tool id. Scoped to the profile it was saved from — tool ids
    are only meaningful within that profile's tool set."""
    saved_at: str | None = None  # ISO timestamp, for display in the picker
    positions: dict[str, ToolPosition] = Field(default_factory=dict)


class Profile(BaseModel):
    name: str = "default"
    machine: Machine = Field(default_factory=Machine)
    chuck: Chuck = Field(default_factory=Chuck)
    workpiece: Workpiece = Field(default_factory=Workpiece)
    tools: list[Tool] = Field(default_factory=lambda: [Tool()])
    reference_tool_id: str = "T01"
    last_gcode_path: str | None = None
    safety_margin: float = 0.5
    green_zone_consider_table: bool = True
    forbidden_zones: list[ForbiddenZone] = Field(default_factory=list)
    # Saved gang-slide layouts, keyed "1".."4" — see PositionSetup.
    position_setups: dict[str, PositionSetup] = Field(default_factory=dict)

    def get_tool(self, tool_id: str) -> Tool | None:
        return next((t for t in self.tools if t.id == tool_id), None)

    # ---- T-slot constraint (single source of truth) ----------------------
    # A tool assigned to a slot has its bolt hole (mount_z + slot_attach_z)
    # pinned onto the slot's Z line, so mount_z is fully determined by the
    # slot. Z is never free for a slotted tool — only X slides. Every place
    # that can change mount_z, slot, slot_attach_z, or the slot positions must
    # call snap so the tool and the slot strip never drift apart.

    def snap_slotted_tool(self, tool: Tool) -> bool:
        """Pin a slotted tool's mount_z so its bolt hole sits on its slot.
        No-op (returns False) when slots are hidden or the tool is free."""
        st = self.machine.slide_table
        if not st.show_slots or tool.slot is None:
            return False
        sz = st.slot_z(tool.slot)
        if sz is None:
            return False
        new_z = sz - tool.slot_attach_z
        if new_z != tool.mount_z:
            tool.mount_z = new_z
            return True
        return False

    def snap_all_slotted(self) -> None:
        """Re-pin every slotted tool — call after loading a profile or after
        moving a slot in Settings."""
        for t in self.tools:
            self.snap_slotted_tool(t)

    # ---- TABLE → WORKPIECE mapping (single source of truth) --------------
    # Everything drawn at the machine-home pose goes through these. They never
    # touch the reference tool, so changing the reference tool cannot move the
    # table or the holders.

    @property
    def slide_origin(self) -> tuple[float, float]:
        """Position of the table-frame origin in the workpiece frame at home."""
        return (self.machine.slide_origin_x, self.machine.slide_origin_z)

    def home_block(self, tool: Tool) -> tuple[float, float]:
        """Tool's holder-block reference point (its tip BEFORE the holder
        tip offset) in the workpiece frame at machine home:

            home_block = slide_origin + mount
        """
        ox, oz = self.slide_origin
        return (ox + tool.mount_x, oz + tool.mount_z)

    def home_tip(self, tool: Tool) -> tuple[float, float]:
        """Tool's cutting TIP in the workpiece frame at machine home:

            home_tip = slide_origin + mount + holder tip offset
        """
        bx, bz = self.home_block(tool)
        return (bx + tool.holder.tip_dx, bz + tool.holder.tip_dz)

    # ---- G54 (program) frame — used only to anchor the G-code animation --

    @property
    def ref_mount(self) -> tuple[float, float]:
        ref = self.get_tool(self.reference_tool_id)
        if ref is None:
            return (0.0, 0.0)
        return (ref.mount_x, ref.mount_z)

    def tip_in_workpiece(self, tool: Tool) -> tuple[float, float]:
        """Tool tip in the G54 (program) frame: offset from the reference tip."""
        rx, rz = self.ref_mount
        return (tool.mount_x - rx, tool.mount_z - rz)

    def machine_zero_workpiece(self) -> tuple[float, float]:
        """Reference tool's block position in the workpiece frame at machine
        home — the anchor the G54 G-code animation rides on. This is exactly
        `home_block(reference tool)`; it is ref-dependent ON PURPOSE (G54 is
        defined by the reference tool), but it does not move the table because
        the table is drawn from `slide_origin`, not from this value."""
        ref = self.get_tool(self.reference_tool_id)
        if ref is None:
            return self.slide_origin
        return self.home_block(ref)
