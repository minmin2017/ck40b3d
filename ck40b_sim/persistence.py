"""Profile load/save to %APPDATA%/CK40B-Sim/profiles/.

Each profile is a project: full Tool layout (mount_x/mount_z, orientation,
holder geometry), chuck/workpiece, ref tool, last G-code path. Switching
profiles = switching to another project's setup.
"""
from __future__ import annotations
import json
import os
import re
import shutil
from pathlib import Path
from .models import Profile


def app_data_root() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "CK40B-Sim"
    p.mkdir(parents=True, exist_ok=True)
    return p


def app_data_dir() -> Path:
    p = app_data_root() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_path(name: str) -> Path:
    return app_data_dir() / f"{name}.json"


def list_profiles() -> list[str]:
    return sorted(p.stem for p in app_data_dir().glob("*.json"))


def load_profile(name: str = "default") -> Profile:
    path = profile_path(name)
    if not path.exists():
        prof = Profile(name=name)
        save_profile(prof)
        return prof
    prof = Profile.model_validate_json(path.read_text(encoding="utf-8"))
    # Self-heal inverted slide-table bounds saved by an earlier session — an
    # x_min > x_max rectangle makes the green-zone grid empty and crashes the
    # heatmap renderer (which then pops a modal error and freezes the UI).
    if prof.machine.slide_table.normalize():
        save_profile(prof)
    return prof


def save_profile(profile: Profile) -> None:
    path = profile_path(profile.name)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def delete_profile(name: str) -> None:
    p = profile_path(name)
    if p.exists():
        p.unlink()


def duplicate_profile(src: Profile, new_name: str) -> Profile:
    """Save the in-memory profile under a new name. Does not touch the source
    file. Use after the user picks a name in `Save As…`."""
    safe = sanitize_profile_name(new_name)
    dup = src.model_copy(deep=True)
    dup.name = safe
    save_profile(dup)
    return dup


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


def sanitize_profile_name(name: str) -> str:
    """Strip filesystem-hostile characters and trim whitespace. Used by
    Save As / Rename so a user-typed name turns into a safe filename
    without surprising them with a silent rejection."""
    s = _SAFE_NAME_RE.sub("_", name).strip() or "untitled"
    return s[:64]


# ----- tool preset library (shared across profiles) -------------------------

def _presets_path() -> Path:
    return app_data_root() / "tool_presets.json"


def load_tool_presets() -> list:
    """Return list[Tool] from the shared preset library."""
    from .models import Tool
    p = _presets_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Tool.model_validate(t) for t in data]
    except Exception:
        return []


def save_tool_presets(tools: list) -> None:
    """Persist list[Tool] to the shared preset library."""
    p = _presets_path()
    p.write_text(
        json.dumps([t.model_dump() for t in tools], indent=2),
        encoding="utf-8",
    )


# ----- last-used profile (so each app launch reopens the last project) -----

def _settings_path() -> Path:
    return app_data_root() / "settings.json"


def load_last_profile_name() -> str:
    p = _settings_path()
    if not p.exists():
        return "default"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        name = str(data.get("last_profile", "default")).strip()
        return name or "default"
    except Exception:
        return "default"


def save_last_profile_name(name: str) -> None:
    p = _settings_path()
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["last_profile"] = name
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
