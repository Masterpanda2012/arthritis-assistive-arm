"""Grammar-constrained Vosk voice input.

The Vosk *small* model is inaccurate for open-domain speech, so we
constrain its language model to only the phrases we care about. Kaldi
treats the grammar as a closed vocabulary, which makes short commands
like "open claw" reliably decode instead of being mis-recognised as
random words. We also emit partial results to the log so the user sees
that the microphone is working as they speak.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable
import re
import sys

from config import RuntimeConfig
from inputs.lip_activity import LipActivity
from models import ActionRequest
from ai.adl_tasks import adl_to_action_request, match_adl_phrase

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None

try:
    from vosk import KaldiRecognizer, Model
except ImportError:  # pragma: no cover
    KaldiRecognizer = None
    Model = None


LOGGER = logging.getLogger(__name__)


def _default_input_device_label() -> str:
    if sd is None:
        return ""
    try:
        info = sd.query_devices(sd.default.device[0])
        return str(info.get("name", "")).strip()
    except Exception:
        return ""


# Wake words drop the recognizer into an open-vocabulary active-listen
# window so the user can say arbitrary teach/phrase commands. "robot"
# is the primary trigger because it acoustically stands apart from the
# rest of the command grammar; "computer" is offered as a backup for
# users who find the first one awkward.
WAKE_WORDS: tuple[str, ...] = ("robot", "computer")


# Vosk grammar = flat list of every word the recognizer is allowed to emit.
# "[unk]" is a magic token meaning "anything else" (kept out-of-vocab).
_GRAMMAR_WORDS = [
    # Core motion vocabulary.
    "open", "close", "claw", "grip", "gripper", "release", "grab",
    "lift", "raise", "lower", "higher", "up", "down", "arm",
    "base", "move", "turn", "rotate", "twist", "spin", "swing",
    "left", "right",
    "home", "reset", "center", "centre", "go", "back", "position",
    "stop", "halt", "emergency", "freeze", "hold", "moving",
    "yes", "confirm", "okay", "ok", "no", "cancel", "nope",
    # "pickup" is intentionally NOT in this grammar: when it was, Vosk
    # would acoustically collapse nearby out-of-vocab words (e.g. "cup")
    # onto it and trigger a pick_object confirmation. Users who want to
    # pick something up can still say the two-word form "pick up", which
    # decodes as "pick" + "up" and is caught by the multi-word rule
    # below. Typed input can still use "pickup" verbatim because the
    # typed text bypasses the grammar.
    "inspect", "show", "look", "pick", "place", "drop", "put",
    "quit", "exit", "shutdown", "shut", "bye",
    "the", "it", "please", "a", "an", "to", "hand",
    # Teach / learn vocabulary — so short rebinding phrases like
    # "teach fist as home" still decode in grammar mode, even before
    # the user has discovered the "robot" wake word. Adding these here
    # is cheap because Vosk treats the grammar as a finite word list.
    "teach", "learn", "map", "assign", "bind", "set", "make",
    "forget", "clear", "wipe", "when", "say", "do", "as", "means",
    "mean", "phrase", "gesture", "gestures", "mapping", "mappings",
    # Known gesture names so teach commands decode accurately.
    "fist", "pinch", "rock", "call", "me", "point", "one", "two",
    "three", "four", "tilt", "shaka", "horns", "pointer", "zoom",
    # Preset-pose names users can bind to.
    "pickup", "ready",
    # Wake words themselves have to live in the grammar so Vosk can
    # emit them as tokens in constrained mode.
    *WAKE_WORDS,
    "help",
    "pills", "medication", "medicine", "remote", "water", "drink", "bottle", "phone",
    "[unk]",
]

_GRAMMAR_JSON = json.dumps(sorted(set(_GRAMMAR_WORDS)))


# Ordered list of (set-of-keywords, intent). The first entry whose keywords
# are all present in the heard text wins; tight single keywords come last.
# Rule order matters. Longer (more specific) keyword tuples are listed
# before shorter ones so e.g. "emergency stop" wins over bare "stop",
# "lift up" beats "up", and "pick up" beats "up".
_INTENT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    # --- Multi-word rules (most specific) ---
    (("emergency", "stop"), "emergency_stop"),
    (("stop", "moving"), "emergency_stop"),
    (("open", "claw"), "open_claw"),
    (("open", "gripper"), "open_claw"),
    (("open", "grip"), "open_claw"),
    (("open", "hand"), "open_claw"),
    (("close", "claw"), "close_claw"),
    (("close", "gripper"), "close_claw"),
    (("close", "grip"), "close_claw"),
    (("close", "hand"), "close_claw"),
    (("lift", "up"), "lift_up"),
    (("move", "up"), "lift_up"),
    (("arm", "up"), "lift_up"),
    (("raise", "arm"), "lift_up"),
    (("lift", "down"), "lift_down"),
    (("move", "down"), "lift_down"),
    (("arm", "down"), "lift_down"),
    (("lower", "arm"), "lift_down"),
    (("pick", "up"), "pick_object"),
    (("put", "down"), "place_object"),
    (("base", "left"), "base_left"),
    (("turn", "left"), "base_left"),
    (("move", "left"), "base_left"),
    (("swing", "left"), "base_left"),
    (("base", "right"), "base_right"),
    (("turn", "right"), "base_right"),
    (("move", "right"), "base_right"),
    (("swing", "right"), "base_right"),
    (("rotate", "left"), "rotate_left"),
    (("twist", "left"), "rotate_left"),
    (("spin", "left"), "rotate_left"),
    (("rotate", "right"), "rotate_right"),
    (("twist", "right"), "rotate_right"),
    (("spin", "right"), "rotate_right"),
    (("go", "home"), "home"),
    (("go", "back"), "home"),
    (("home", "position"), "home"),
    (("shut", "down"), "shutdown"),
    # --- Single-word rules (fallbacks) ---
    # Emergency / halt
    (("halt",), "emergency_stop"),
    (("freeze",), "emergency_stop"),
    (("stop",), "emergency_stop"),
    # Claw
    (("release",), "open_claw"),
    (("open",), "open_claw"),
    (("grab",), "close_claw"),
    (("grip",), "close_claw"),
    (("close",), "close_claw"),
    # Lift
    (("raise",), "lift_up"),
    (("higher",), "lift_up"),
    (("lift",), "lift_up"),
    (("lower",), "lift_down"),
    # Pick / place (bare commands — the parser defers to the LLM if a
    # noun trails, so we can keep these permissive). We intentionally do
    # *not* have a bare single-word "pick" rule: when Vosk mis-decoded
    # short out-of-vocab words like "cup" as "pick", the arm kept
    # asking "pick up what?" unprompted. The multi-word ("pick", "up")
    # rule above still catches the real intent. "pickup" as a single
    # token only reaches this parser from typed input, which is safe.
    (("pickup",), "pick_object"),
    (("place",), "place_object"),
    (("drop",), "place_object"),
    # Preset
    (("inspect",), "preset_pose:inspect"),
    (("show",), "preset_pose:inspect"),
    (("look",), "preset_pose:inspect"),
    # Home
    (("home",), "home"),
    (("reset",), "home"),
    (("center",), "home"),
    (("centre",), "home"),
    (("back",), "home"),
    # Rotate
    (("rotate",), "rotate_right"),
    (("twist",), "rotate_right"),
    # Base
    (("left",), "base_left"),
    (("right",), "base_right"),
    # Directional (single-word fallbacks; lowest priority for up/down)
    (("up",), "lift_up"),
    (("down",), "lift_down"),
    # Confirmations
    (("yes",), "confirm_yes"),
    (("confirm",), "confirm_yes"),
    (("ok",), "confirm_yes"),
    (("okay",), "confirm_yes"),
    (("no",), "confirm_no"),
    (("nope",), "confirm_no"),
    (("cancel",), "confirm_no"),
    # Shutdown
    (("shutdown",), "shutdown"),
    (("quit",), "shutdown"),
    (("exit",), "shutdown"),
    (("help",), "home"),
    (("bye",), "shutdown"),
)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


_TOKEN_ALIASES = {
    "gripper": "claw",
    "grip": "claw",
    "hand": "claw",
    "centre": "center",
}


# Filler words that don't change the meaning of a command ("please open claw").
_FILLER = frozenset({"the", "a", "an", "please", "it"})


def parse_direct_command(text: str, *, source: str = "voice") -> ActionRequest | None:
    """Map an ASR hypothesis or typed line to an ``ActionRequest``."""

    adl = match_adl_phrase(text)
    if adl is not None:
        shaped = adl_to_action_request(adl, source=source)
        return ActionRequest(
            source=shaped["source"],
            intent=shaped["intent"],
            payload=shaped["payload"],
            requires_confirmation=shaped["requires_confirmation"],
        )

    tokens = [_TOKEN_ALIASES.get(token, token) for token in _tokenize(text)]
    if not tokens:
        return None
    token_set = set(tokens)
    content = token_set - _FILLER

    for keywords, intent in _INTENT_RULES:
        if not all(k in token_set for k in keywords):
            continue

        # Pick/place should only direct-match when the user said a bare
        # command (e.g. "pick up"). If there's an extra noun ("pick up
        # the cup"), defer to the LLM/heuristic so it can extract the
        # label instead of silently grabbing label='object'.
        if intent in {"pick_object", "place_object"}:
            extras = content - set(keywords) - {"pick", "up", "pickup", "place", "drop", "put", "down"}
            if extras:
                return ActionRequest(source=source, intent="spoken_text", payload={"text": " ".join(tokens)})
            return ActionRequest(
                source=source,
                intent=intent,
                payload={"label": "object"},
                requires_confirmation=True,
            )

        if intent.startswith("preset_pose:"):
            return ActionRequest(source=source, intent="preset_pose", payload={"name": intent.split(":", 1)[1]})
        return ActionRequest(source=source, intent=intent)

    return ActionRequest(source=source, intent="spoken_text", payload={"text": " ".join(tokens)})


async def voice_loop(
    config: RuntimeConfig,
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    *,
    voice_log: Any = None,
    lip_activity: LipActivity | None = None,
    enabled_fn: Callable[[], bool] | None = None,
) -> None:
    if Model is None or KaldiRecognizer is None or sd is None:
        detail = "Install vosk and sounddevice (pip install vosk sounddevice)."
        LOGGER.warning("Vosk voice stack is unavailable. %s", detail)
        if voice_log is not None:
            voice_log.set_mic("unavailable", error=detail)
        await _idle_voice_loop(stop_event, voice_log=voice_log)
        return

    if not config.vosk_model_path.exists():
        detail = (
            f"Speech model missing at {config.vosk_model_path}. "
            "Run ./setup.sh or download vosk-model-small-en-us-0.15."
        )
        LOGGER.warning("%s", detail)
        if voice_log is not None:
            voice_log.set_mic("unavailable", error=detail)
        await _idle_voice_loop(stop_event, voice_log=voice_log)
        return

    await _vosk_loop(
        config, action_queue, stop_event,
        voice_log=voice_log, lip_activity=lip_activity, enabled_fn=enabled_fn,
    )


async def _idle_voice_loop(
    stop_event: asyncio.Event,
    *,
    voice_log: Any = None,
) -> None:
    """Keep the voice task alive when hardware/model mic input is unavailable."""
    while not stop_event.is_set():
        await asyncio.sleep(0.5)


async def _text_command_loop(
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    *,
    voice_log: Any = None,
) -> None:
    if not sys.stdin.isatty():
        LOGGER.warning("Standard input is not interactive, so text voice fallback is disabled.")
        return

    loop = asyncio.get_running_loop()
    ready = asyncio.Event()

    def on_stdin_ready() -> None:
        ready.set()

    try:
        loop.add_reader(sys.stdin.fileno(), on_stdin_ready)
    except (NotImplementedError, ValueError):
        LOGGER.warning("Async stdin watching is unavailable, so text voice fallback is disabled.")
        return

    LOGGER.info("Voice fallback ready: type commands into the terminal and press Enter.")
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(ready.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

            ready.clear()
            raw = sys.stdin.readline()
            if raw == "":
                LOGGER.info("Text command input closed.")
                return

            command = parse_direct_command(raw, source="voice")
            if command is not None:
                LOGGER.info("Voice(text) command queued: %s %s", command.intent, command.payload or {})
                await action_queue.put(command)
    finally:
        loop.remove_reader(sys.stdin.fileno())


def _clean_tokens(text: str) -> str:
    """Drop Vosk's ``[unk]`` noise tokens from a decoded phrase."""
    return " ".join(
        w for w in text.split() if w and w.lower() not in {"[unk]", "<unk>"}
    ).strip()


def _extract_wake_trailing(cleaned: str) -> tuple[bool, str]:
    """If ``cleaned`` starts with a wake word, return ``(True, remainder)``.

    ``remainder`` is everything after the wake word with surrounding
    whitespace trimmed; it will be empty when the user just said
    "robot" by itself, which is how we decide to open an active-listen
    window for the next utterance.
    """
    tokens = cleaned.split()
    if not tokens:
        return (False, "")
    if tokens[0].lower() in WAKE_WORDS:
        return (True, " ".join(tokens[1:]).strip())
    # Also allow the wake word to appear mid-phrase after a filler like
    # "hey". A strict prefix would miss "hey robot teach fist as home".
    for idx, token in enumerate(tokens[:3]):
        if token.lower() in WAKE_WORDS:
            return (True, " ".join(tokens[idx + 1 :]).strip())
    return (False, "")


async def _vosk_loop(
    config: RuntimeConfig,
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    *,
    voice_log: Any = None,
    lip_activity: LipActivity | None = None,
    enabled_fn: Callable[[], bool] | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_audio(indata, frames, time_info, status) -> None:  # pragma: no cover
        if status:
            LOGGER.debug("Audio stream status: %s", status)
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    try:
        model = Model(str(config.vosk_model_path))
        try:
            grammar_rec = KaldiRecognizer(model, 16000, _GRAMMAR_JSON)
            LOGGER.info("Voice grammar constrained to %d command words.", len(_GRAMMAR_WORDS))
        except TypeError:
            grammar_rec = KaldiRecognizer(model, 16000)
            LOGGER.info("Voice running with open vocabulary (grammar ctor not supported).")
        grammar_rec.SetWords(True)
        # Open-vocabulary recognizer is *lazy* — we pay the
        # initialisation cost only the first time the user invokes the
        # wake word. Most sessions never need it.
        open_rec: Any | None = None
        stream = sd.RawInputStream(
            samplerate=16000,
            blocksize=4000,
            dtype="int16",
            channels=1,
            callback=on_audio,
        )
    except Exception as exc:
        detail = (
            f"Could not open system microphone ({exc}). "
            "Grant Microphone access to Terminal/Python in System Settings → Privacy."
        )
        LOGGER.warning("%s", detail)
        if voice_log is not None:
            voice_log.set_mic("unavailable", error=detail)
        await _idle_voice_loop(stop_event, voice_log=voice_log)
        return

    device_label = _default_input_device_label()
    if voice_log is not None:
        voice_log.set_mic("listening", device=device_label)

    wake_list = "/".join(WAKE_WORDS)
    LOGGER.info(
        "Voice input ready — try: open / close / lift / lower / left / right / rotate / "
        "home / inspect / pick up / drop / stop / yes / no. "
        "Say '%s' to start active listening for teach/phrase commands.",
        WAKE_WORDS[0],
    )

    # Active-listen state.
    active_mode = False
    active_started_at = 0.0
    active_deadline = 0.0
    active_window_s = float(config.voice_active_listen_s)
    last_partial = ""

    def _enter_active() -> None:
        nonlocal active_mode, active_started_at, active_deadline, open_rec, last_partial
        if open_rec is None:
            open_rec = KaldiRecognizer(model, 16000)
            open_rec.SetWords(True)
            LOGGER.info("Open-vocabulary recognizer initialised for active listening.")
        try:
            # Flush any buffered audio so stale half-utterances don't
            # bleed into the new open-vocab session.
            open_rec.Reset()
        except AttributeError:
            pass
        active_mode = True
        active_started_at = time.monotonic()
        active_deadline = active_started_at + active_window_s
        last_partial = ""
        LOGGER.info("Voice: active listening ON (open vocabulary, %.1fs window).", active_window_s)
        if voice_log is not None:
            voice_log.set_heard("🎤 listening…", source="voice")
            voice_log.set_intent("", status="listening")

    def _exit_active(reason: str) -> None:
        nonlocal active_mode
        active_mode = False
        LOGGER.info("Voice: active listening OFF (%s).", reason)
        if voice_log is not None and voice_log.heard.startswith("🎤"):
            voice_log.set_heard("", source="voice")

    def _should_suppress(cleaned: str) -> bool:
        """Gate grammar-mode decodes on lip activity to cut ambient-audio
        misfires. We *only* suppress when a face is clearly visible but
        hasn't moved its mouth recently — if the user isn't on camera
        (e.g. across the room) we fall back to the old behaviour so we
        don't silently break their workflow."""
        if lip_activity is None:
            return False
        if not config.voice_require_lip_activity:
            return False
        if not lip_activity.has_face(window=2.5):
            return False
        if lip_activity.is_speaking(window=1.5):
            return False
        LOGGER.info("Voice: dropped %r — face visible but mouth wasn't moving.", cleaned)
        return True

    with stream:  # pragma: no branch
        while not stop_event.is_set():
            if enabled_fn is not None and not enabled_fn():
                if voice_log is not None and voice_log.mic_mode == "listening":
                    voice_log.set_mic("disabled", device=device_label)
                await asyncio.sleep(0.2)
                continue
            if voice_log is not None and voice_log.mic_mode == "disabled":
                voice_log.set_mic("listening", device=device_label)
            try:
                data = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # No audio — still tick the active-mode deadline.
                if active_mode and time.monotonic() > active_deadline:
                    _exit_active("timeout (silence)")
                continue

            # Keep the wake-window alive while the user is visibly still
            # speaking: lip activity extends the deadline but can't
            # lengthen a session beyond 2× the configured window.
            if active_mode and lip_activity is not None and lip_activity.is_speaking(0.6):
                hard_cap = active_started_at + 2.0 * active_window_s
                active_deadline = min(hard_cap, time.monotonic() + active_window_s * 0.6)

            if active_mode:
                assert open_rec is not None
                if open_rec.AcceptWaveform(data):
                    try:
                        result = json.loads(open_rec.Result())
                    except json.JSONDecodeError:
                        _exit_active("bad json")
                        continue
                    text = _clean_tokens(str(result.get("text", "")).strip())
                    if not text:
                        _exit_active("empty utterance")
                        continue
                    # Strip a leading wake word if the user re-said it.
                    _, trailing = _extract_wake_trailing(text)
                    final = trailing if trailing else text
                    if final.lower() in WAKE_WORDS:
                        # User only re-said the wake word — keep listening.
                        continue
                    LOGGER.info("Voice(active) heard: %s", final)
                    if voice_log is not None:
                        voice_log.set_heard(final, source="voice")
                        voice_log.set_intent("", status="interpreting…")
                    await action_queue.put(
                        ActionRequest(
                            source="voice", intent="spoken_text",
                            payload={"text": final},
                        )
                    )
                    _exit_active("utterance committed")
                else:
                    if time.monotonic() > active_deadline:
                        _exit_active("timeout")
                        continue
                    try:
                        partial = json.loads(open_rec.PartialResult()).get("partial", "")
                    except json.JSONDecodeError:
                        partial = ""
                    partial = partial.strip()
                    if partial and partial != last_partial:
                        last_partial = partial
                        if voice_log is not None:
                            voice_log.set_partial(f"🎤 {partial}")
                continue

            # --- Normal grammar-mode path --------------------------------
            if grammar_rec.AcceptWaveform(data):
                try:
                    result = json.loads(grammar_rec.Result())
                except json.JSONDecodeError:
                    continue
                text = str(result.get("text", "")).strip()
                if not text:
                    continue
                cleaned = _clean_tokens(text)
                if not cleaned:
                    LOGGER.debug("Voice skipped noise-only result: %s", text)
                    continue

                # Wake-word handling: if the user prefixed the command
                # with the wake word we treat the *whole* remainder as a
                # free-form spoken_text (so teach commands pass to the
                # LLM heuristic verbatim). If only the wake word was
                # heard, we flip into the open-vocab active window.
                has_wake, trailing = _extract_wake_trailing(cleaned)
                if has_wake:
                    if trailing:
                        LOGGER.info("Voice(wake) heard: %s → %r", cleaned, trailing)
                        if voice_log is not None:
                            voice_log.set_heard(trailing, source="voice")
                            voice_log.set_intent("", status="interpreting…")
                        await action_queue.put(
                            ActionRequest(
                                source="voice", intent="spoken_text",
                                payload={"text": trailing},
                            )
                        )
                    else:
                        _enter_active()
                    last_partial = ""
                    continue

                if _should_suppress(cleaned):
                    last_partial = ""
                    continue

                LOGGER.info("Voice heard: %s", cleaned)
                if voice_log is not None:
                    voice_log.set_heard(cleaned, source="voice")
                command = parse_direct_command(cleaned, source="voice")
                if command is None:
                    continue
                if command.intent == "spoken_text":
                    LOGGER.info("Voice unmatched phrase (LLM/heuristic): %s", cleaned)
                    if voice_log is not None:
                        voice_log.set_intent("", status="interpreting…")
                else:
                    LOGGER.info(
                        "Voice command: %s %s", command.intent, command.payload or {}
                    )
                    if voice_log is not None:
                        voice_log.set_intent(command.intent, command.payload, status="matched")
                await action_queue.put(command)
                last_partial = ""
            else:
                try:
                    partial = json.loads(grammar_rec.PartialResult()).get("partial", "")
                except json.JSONDecodeError:
                    partial = ""
                partial = partial.strip()
                if partial and partial != last_partial and partial != "[unk]":
                    LOGGER.info("Voice partial… %s", partial)
                    last_partial = partial
                    if voice_log is not None:
                        voice_log.set_partial(partial)
