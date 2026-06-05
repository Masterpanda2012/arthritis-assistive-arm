"""Activities of Daily Living (ADL) task library for arthritis assistive use.

Maps plain-language requests to object labels, YOLO synonyms, and safe
motion presets so users can say "get my pills" instead of robotics jargon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ADLTask:
    id: str
    label: str
    description: str
    object_labels: tuple[str, ...]
    voice_phrases: tuple[str, ...]
    intent: str = "pick_object"
    payload: dict[str, Any] = field(default_factory=dict)
    confirmation_message: str = ""
    panel_accent: str = "success"


ADL_TASKS: tuple[ADLTask, ...] = (
    ADLTask(
        id="medication",
        label="Medication",
        description="Retrieve a pill bottle or medication container from the table.",
        object_labels=("bottle", "cup", "vase"),
        voice_phrases=(
            "pills",
            "medication",
            "medicine",
            "my pills",
            "get my pills",
            "bring my medication",
            "get my medicine",
            "pill bottle",
        ),
        confirmation_message="I'll gently reach for your medication. Say yes to continue.",
        panel_accent="success",
    ),
    ADLTask(
        id="remote",
        label="TV Remote",
        description="Fetch a TV remote or similar handheld controller.",
        object_labels=("remote", "cell phone", "mouse"),
        voice_phrases=(
            "remote",
            "tv remote",
            "the remote",
            "get the remote",
            "bring the remote",
            "fetch the remote",
        ),
        confirmation_message="I'll reach for the remote. Say yes to continue.",
        panel_accent="info",
    ),
    ADLTask(
        id="water",
        label="Water / Drink",
        description="Bring a water bottle or cup within reach.",
        object_labels=("bottle", "cup", "wine glass"),
        voice_phrases=(
            "water",
            "drink",
            "bottle",
            "water bottle",
            "get my water",
            "bring my drink",
            "get the bottle",
            "i'm thirsty",
        ),
        confirmation_message="I'll reach for your drink. Say yes to continue.",
        panel_accent="primary",
    ),
    ADLTask(
        id="phone",
        label="Phone",
        description="Retrieve a cell phone from the workspace.",
        object_labels=("cell phone", "remote"),
        voice_phrases=(
            "phone",
            "cell phone",
            "my phone",
            "get my phone",
            "bring my phone",
        ),
        confirmation_message="I'll reach for your phone. Say yes to continue.",
        panel_accent="info",
    ),
    ADLTask(
        id="bring_to_me",
        label="Bring to Me",
        description="Return home after holding an object — preset safe retract.",
        object_labels=(),
        voice_phrases=(
            "bring to me",
            "bring it here",
            "come back",
            "return",
        ),
        intent="home",
        payload={},
        confirmation_message="",
        panel_accent="warning",
    ),
    ADLTask(
        id="help",
        label="I Need Help",
        description="Stop motion and return to a safe home pose.",
        object_labels=(),
        voice_phrases=("help", "i need help", "assist me"),
        intent="home",
        payload={},
        confirmation_message="",
        panel_accent="danger",
    ),
)

_TASK_BY_ID: dict[str, ADLTask] = {t.id: t for t in ADL_TASKS}


def list_adl_tasks() -> list[ADLTask]:
    return list(ADL_TASKS)


def get_adl_task(task_id: str) -> ADLTask | None:
    return _TASK_BY_ID.get(task_id)


def match_adl_phrase(text: str) -> ADLTask | None:
    """Return the best ADL task for normalized spoken/typed text."""
    norm = " ".join(text.strip().lower().split())
    if not norm:
        return None
    best: ADLTask | None = None
    best_len = 0
    for task in ADL_TASKS:
        for phrase in task.voice_phrases:
            if phrase == norm or phrase in norm:
                if len(phrase) > best_len:
                    best = task
                    best_len = len(phrase)
    return best


def adl_to_action_request(task: ADLTask, *, source: str) -> dict[str, Any]:
    """Shape consumed by ActionRequest construction."""
    payload = dict(task.payload)
    if task.intent == "pick_object" and task.object_labels:
        payload.setdefault("label", task.object_labels[0])
        payload["adl_id"] = task.id
    elif task.intent == "home":
        payload["adl_id"] = task.id
    requires_confirmation = task.intent in {"pick_object", "place_object"}
    return {
        "source": source,
        "intent": task.intent,
        "payload": payload,
        "requires_confirmation": requires_confirmation,
        "confirmation_message": task.confirmation_message,
    }


def yolo_label_matches_adl(detected_label: str, adl_id: str) -> bool:
    task = get_adl_task(adl_id)
    if task is None:
        return False
    detected = detected_label.strip().lower()
    return any(detected == lbl.lower() or lbl.lower() in detected for lbl in task.object_labels)


def adl_summary_for_llm() -> str:
    lines = []
    for task in ADL_TASKS:
        if task.intent == "pick_object":
            labels = ", ".join(task.object_labels)
            lines.append(f"- {task.label}: pick {labels} (phrases: {', '.join(task.voice_phrases[:4])})")
        else:
            lines.append(f"- {task.label}: {task.intent} (phrases: {', '.join(task.voice_phrases[:3])})")
    return "\n".join(lines)
