"""Built-in generic Topic presets for the Unified Web UI.

These presets are generic open-source templates (zero hardcoded personal/household terms).
If ``vs_runtime.manager.TOPIC_PRESETS`` is available, we prefer its definitions and enrich
them with UI metadata (description, icon hint, recommended camera count).
"""

from __future__ import annotations

from typing import Any

FALLBACK_PRESETS: list[dict[str, Any]] = [
    {
        "id": "package-watch",
        "name": "Package Watch",
        "description": (
            "Detect parcel deliveries at an entryway and alert if a package is picked up or left outside."
        ),
        "icon": "package",
        "recommended_cameras": 1,
        "cadence_sec": 60,
        "window_sec": 20,
        "speaker_enabled": False,
        "goal": (
            "Watch the entryway for delivered parcels. Send an info alert when a new package arrives "
            "and a warning alert if someone approaches and removes a package unexpectedly."
        ),
        "vision_rubric": (
            "# Vision rubric — Package Watch\n\n"
            "Inspect the entryway frame/clip for packages, delivery couriers, and visitors.\n"
            "Conditions to report:\n"
            "- `package_present`: one or more parcels/boxes are visible near the door.\n"
            "- `delivery_person`: a courier is placing or carrying a package.\n"
            "- `package_removed`: a previously visible package is being picked up or is gone.\n"
        ),
    },
    {
        "id": "door-left-open",
        "name": "Door / Gate Left Open",
        "description": (
            "Monitor a garage door, side gate, or entry door and alert when left open beyond a threshold."
        ),
        "icon": "door-open",
        "recommended_cameras": 1,
        "cadence_sec": 60,
        "window_sec": 15,
        "speaker_enabled": False,
        "goal": (
            "Monitor the door or gate. Alert with warning severity if the door stays open for more "
            "than 5 consecutive minutes without active people moving through it."
        ),
        "vision_rubric": (
            "# Vision rubric — Door / Gate Left Open\n\n"
            "Determine whether the monitored door or gate is open or closed and whether anyone is "
            "actively using it.\n"
            "Conditions to report:\n"
            "- `door_open`: the door or gate is visibly open or ajar.\n"
            "- `person_present`: a person is actively walking through or working near the opening.\n"
        ),
    },
    {
        "id": "pet-watch",
        "name": "Pet Watch",
        "description": (
            "Keep an eye on pets indoors or in a yard — spot off-limits furniture, accidents, or barking."
        ),
        "icon": "paw-print",
        "recommended_cameras": 1,
        "cadence_sec": 45,
        "window_sec": 15,
        "speaker_enabled": False,
        "goal": (
            "Watch for pets climbing onto restricted furniture or scratching at the door to go outside. "
            "Send a gentle info alert when a pet waits at the door, or a warning if they are on the counter."
        ),
        "vision_rubric": (
            "# Vision rubric — Pet Watch\n\n"
            "Observe pet location, posture, and audible barking/meowing.\n"
            "Conditions to report:\n"
            "- `pet_visible`: a pet is visible in frame.\n"
            "- `pet_restricted_area`: a pet is on restricted furniture or counters.\n"
            "- `pet_at_door`: a pet is waiting or scratching at an exterior door.\n"
        ),
    },
    {
        "id": "multi-cam-watch",
        "name": "Multi-Cam Watch",
        "description": "Correlate activity across 2+ cameras in synchronized wall-clock bundles for one decision.",
        "icon": "layout-grid",
        "recommended_cameras": 2,
        "cadence_sec": 60,
        "window_sec": 20,
        "speaker_enabled": False,
        "goal": (
            "Correlate movement across all subscribed cameras. Alert if a person or vehicle moves "
            "between zones outside expected hours, ignoring normal passing traffic on the street."
        ),
        "vision_rubric": (
            "# Vision rubric — Multi-Cam Watch\n\n"
            "Inspect synchronized media across all subscribed camera folders in the bundle.\n"
            "Note which camera_id observes each subject and whether movement crosses camera views.\n"
            "Conditions to report:\n"
            "- `person_detected`: person visible on property.\n"
            "- `vehicle_detected`: vehicle stopped on driveway or perimeter.\n"
            "- `cross_camera_movement`: subject appears across multiple camera views.\n"
        ),
    },
    {
        "id": "custom",
        "name": "Custom Topic",
        "description": "Start from a clean slate with your own monitoring goal, cameras, cadence, and models.",
        "icon": "sparkles",
        "recommended_cameras": 1,
        "cadence_sec": 60,
        "window_sec": 20,
        "speaker_enabled": False,
        "goal": "Describe what you want Vision Sentinel to watch for and when it should alert you.",
        "vision_rubric": "",
    },
]

_FALLBACK_BY_ID = {p["id"]: p for p in FALLBACK_PRESETS}


def list_presets(manager: Any = None) -> list[dict[str, Any]]:
    """Return UI-ready preset dicts, merging runtime presets if available."""
    if manager is not None and hasattr(manager, "list_presets"):
        try:
            raw = manager.list_presets()
            if isinstance(raw, list) and raw:
                merged: list[dict[str, Any]] = []
                for item in raw:
                    d = dict(item) if isinstance(item, dict) else item.model_dump()
                    pid = d.get("id") or d.get("name", "custom")
                    fb = _FALLBACK_BY_ID.get(pid, {})
                    merged.append({**fb, **d, "id": pid})
                return merged
        except Exception:
            pass
    return [dict(p) for p in FALLBACK_PRESETS]


def get_preset(preset_id: str, manager: Any = None) -> dict[str, Any] | None:
    for p in list_presets(manager):
        if p["id"] == preset_id:
            return p
    return None
