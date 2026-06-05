from __future__ import annotations

import asyncio
import json
import logging
import re

from config import RuntimeConfig
from ai.memory_store import MemoryStore
from ai.environment import EnvironmentMap
from ai.adl_tasks import adl_summary_for_llm, match_adl_phrase, adl_to_action_request
from ai.gesture_bindings import (
    GestureBindings,
    canonical_gesture,
    canonical_intent,
)
from models import ActionRequest

from ai.user_profile import UserProfile

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


LOGGER = logging.getLogger(__name__)


class LLMIntentAgent:
    def __init__(
        self,
        config: RuntimeConfig,
        memory_store: MemoryStore,
        environment: EnvironmentMap,
        gesture_bindings: GestureBindings | None = None,
        user_profile: UserProfile | None = None,
    ) -> None:
        self.config = config
        self.memory_store = memory_store
        self.environment = environment
        self.gesture_bindings = gesture_bindings
        self.user_profile = user_profile
        self.provider = config.llm_provider
        self.provider_config = config.llm_configs.get(self.provider)
        self.client = self._build_client()
        # User-taught phrase overrides load from the same memory DB.
        try:
            self._phrase_bindings: dict[str, tuple[str, dict]] = memory_store.load_phrase_bindings()
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Could not load learned phrase bindings: %s", exc)
            self._phrase_bindings = {}

    def learn_phrase(self, phrase: str, intent: str, payload: dict | None = None) -> None:
        phrase = " ".join(phrase.strip().lower().split())
        if not phrase:
            raise ValueError("empty phrase")
        resolved = canonical_intent(intent) if " " in intent else None
        if resolved is not None:
            intent, payload = resolved[0], resolved[1] if payload is None else payload
        payload = dict(payload or {})
        self._phrase_bindings[phrase] = (intent, payload)
        self.memory_store.save_phrase_binding(phrase, intent, payload)

    def clear_learned_phrases(self) -> None:
        self._phrase_bindings.clear()
        self.memory_store.clear_phrase_bindings()

    def _build_client(self):
        if not self.config.features.enable_ai or OpenAI is None:
            return None

        import os

        preferred = (self.config.llm_provider or "groq").strip()
        names = [preferred] + [k for k in self.config.llm_configs if k != preferred]

        for name in names:
            pc = self.config.llm_configs.get(name)
            if pc is None:
                continue
            if name == "ollama":
                opt_in = os.environ.get("ROBOT_ARM_TRY_OLLAMA", "").strip().lower() in {"1", "true", "yes", "on"}
                if preferred != "ollama" and not opt_in:
                    continue
            api_key = os.environ.get(pc.api_key_env, "").strip()
            if name == "ollama" and not api_key:
                api_key = "ollama"
            if not api_key:
                continue
            self.provider = name
            self.provider_config = pc
            LOGGER.info("LLM enabled via provider %s (model=%s).", name, pc.model)
            return OpenAI(api_key=api_key, base_url=pc.base_url)

        LOGGER.info(
            "LLM cloud APIs disabled (no GROQ_API_KEY / GOOGLE_API_KEY). "
            "Voice still uses local phrase matching; start Ollama and set ROBOT_ARM_LLM_PROVIDER=ollama for full NLP."
        )
        self.provider_config = None
        return None

    def status_summary(self) -> str:
        if not self.config.features.enable_ai:
            return "AI: off (gesture-only or disabled)"
        if self.client is not None and self.provider_config is not None:
            return f"AI: {self.provider} ({self.provider_config.model})"
        return "AI: heuristics only — add GROQ_API_KEY or run Ollama (ollama serve)"

    async def interpret_text(self, text: str, *, source: str = "voice") -> ActionRequest | None:
        # "Teach" / "forget" commands short-circuit the normal flow so
        # the learning hooks always win over any keyword heuristic.
        teach = self._parse_teach_command(text, source=source)
        if teach is not None:
            return teach

        # User-taught phrases win over both heuristics and the LLM so
        # the operator can permanently rename commands to their liking.
        learned = self._match_learned_phrase(text, source=source)
        if learned is not None:
            return learned

        adl = match_adl_phrase(text)
        if adl is not None:
            shaped = adl_to_action_request(adl, source=source)
            return ActionRequest(
                source=shaped["source"],
                intent=shaped["intent"],
                payload=shaped["payload"],
                requires_confirmation=shaped["requires_confirmation"],
            )

        heuristic = self._heuristic_action(text, source=source)
        if heuristic is not None:
            return heuristic

        if self.client is None or self.provider_config is None:
            LOGGER.info("No LLM client available for: %s", text)
            return None

        profile_line = ""
        if self.user_profile is not None:
            profile_line = (
                f"User has {self.user_profile.motor_level.value} arthritis/motor limitations. "
                f"Prefer plain, calm language. Default speed is gentle ({self.user_profile.default_speed_pct}%).\n"
            )

        prompt = (
            "Convert the assistive robot-arm request into compact JSON with keys "
            "intent, label, and pose_name. "
            "Use intents: pick_object, place_object, preset_pose, home, emergency_stop. "
            "If unsure, return intent unknown.\n"
            f"{profile_line}"
            "Common daily tasks:\n"
            f"{adl_summary_for_llm()}\n"
            f"Known frequent memory labels: {', '.join(self.memory_store.frequent_labels()) or 'none'}.\n"
            f"Currently visible physical objects on the map: {', '.join(self.environment.known_labels) or 'none'}.\n"
            f"{self.memory_store.recent_summary()}\n"
            f"Request: {text}"
        )

        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.provider_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You help people with arthritis and limited fine motor control operate "
                            "a gentle desktop assistive robot arm. Reply with JSON only. "
                            "Map everyday phrases (pills, remote, water) to pick_object with the best label."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            payload = json.loads(self._extract_json(content))
        except Exception as exc:
            LOGGER.warning("LLM parsing failed for %r (%s)", text, exc)
            return None

        intent = str(payload.get("intent", "unknown"))
        if intent == "unknown":
            return None
        if intent == "preset_pose":
            name = str(payload.get("pose_name", "inspect")).lower().strip()
            if name not in {"home", "inspect", "pickup_ready", "drop_ready"}:
                LOGGER.info("LLM returned preset_pose with unknown name '%s'; ignoring.", name)
                return None
            if name == "home":
                return ActionRequest(source=source, intent="home")
            return ActionRequest(source=source, intent="preset_pose", payload={"name": name})
        if intent == "pick_object":
            return ActionRequest(source=source, intent="pick_object", payload={"label": payload.get("label", "object")}, requires_confirmation=True)
        if intent == "place_object":
            return ActionRequest(source=source, intent="place_object", payload={"label": payload.get("label", "object")}, requires_confirmation=True)
        if intent == "home":
            return ActionRequest(source=source, intent="home")
        if intent == "emergency_stop":
            return ActionRequest(source=source, intent="emergency_stop")
        # Pass through simple movement intents if the LLM chose to emit them.
        if intent in {"open_claw", "close_claw", "lift_up", "lift_down",
                      "base_left", "base_right", "rotate_left", "rotate_right"}:
            return ActionRequest(source=source, intent=intent)
        return None

    # ------------------------------------------------------------------
    # Learning parsers
    # ------------------------------------------------------------------

    # Grammars we accept for gesture teaching:
    #   teach <gesture> as <action>
    #   teach <gesture> to <action>
    #   map <gesture> to <action>
    #   assign <gesture> to <action>
    #   bind <gesture> to <action>
    #   make <gesture> do <action>
    #   set <gesture> to <action>
    # ...and for phrase teaching:
    #   when i say <phrase> do <action>
    #   when i say <phrase>, <action>
    #   teach phrase <phrase> as <action>
    _GESTURE_TEACH_RE = re.compile(
        r"^\s*(?:please\s+)?"
        r"(?:teach|map|assign|bind|set|make)\s+"
        r"(?:(?:the|a|an)\s+)?"
        r"(?P<gesture>[a-z][a-z _-]*?)"
        r"\s+(?:gesture\s+)?"
        r"(?:to|as|do|equals?|means?)\s+"
        r"(?P<action>[a-z][a-z _-]+?)\s*$"
    )
    _PHRASE_TEACH_RE = re.compile(
        r"^\s*(?:when\s+i\s+say|if\s+i\s+say)\s+"
        r"['\"]?(?P<phrase>[^'\",]+?)['\"]?"
        r"[,\s]+(?:do|run|mean|means|make\s+it|make)\s+"
        r"(?P<action>[a-z][a-z _-]+?)\s*$"
    )
    _RESET_GESTURES_RE = re.compile(
        r"^\s*(?:reset|forget|clear|wipe)\s+"
        r"(?:all\s+)?(?:the\s+)?(?:learned\s+|custom\s+|user\s+)?"
        r"gesture(?:s)?(?:\s+(?:bindings|mappings|map))?\s*$"
    )

    def _parse_teach_command(self, text: str, *, source: str) -> ActionRequest | None:
        norm = " ".join(text.strip().lower().split())
        if not norm:
            return None

        if self._RESET_GESTURES_RE.match(norm):
            return ActionRequest(source=source, intent="reset_gestures")

        # Phrase teach takes priority because its prefix ("when i say")
        # never collides with a gesture name.
        m = self._PHRASE_TEACH_RE.match(norm)
        if m is not None:
            phrase = m.group("phrase").strip()
            resolved = canonical_intent(m.group("action"))
            if not phrase or resolved is None:
                return None
            target_intent, target_payload = resolved
            return ActionRequest(
                source=source,
                intent="teach_phrase",
                payload={
                    "phrase": phrase,
                    "target_intent": target_intent,
                    "target_payload": target_payload,
                },
            )

        m = self._GESTURE_TEACH_RE.match(norm)
        if m is not None:
            gesture = canonical_gesture(m.group("gesture"))
            resolved = canonical_intent(m.group("action"))
            if gesture is None or resolved is None:
                return None
            target_intent, target_payload = resolved
            return ActionRequest(
                source=source,
                intent="teach_gesture",
                payload={
                    "gesture": gesture,
                    "target_intent": target_intent,
                    "target_payload": target_payload,
                },
            )

        return None

    def _match_learned_phrase(self, text: str, *, source: str) -> ActionRequest | None:
        if not self._phrase_bindings:
            return None
        norm = " ".join(text.strip().lower().split())
        if not norm:
            return None
        hit = self._phrase_bindings.get(norm)
        if hit is None:
            # Also allow substring matches so a taught phrase "zap it"
            # still triggers inside "zap it now please".
            for phrase, binding in self._phrase_bindings.items():
                if phrase in norm:
                    hit = binding
                    break
        if hit is None:
            return None
        intent, payload = hit
        requires_confirm = intent in {"pick_object", "place_object"}
        return ActionRequest(
            source=source,
            intent=intent,
            payload=dict(payload),
            requires_confirmation=requires_confirm,
        )

    def _heuristic_action(self, text: str, *, source: str) -> ActionRequest | None:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        if not normalized:
            return None
        tokens = set(re.findall(r"[a-z]+", normalized))

        def has(*words: str) -> bool:
            return all(w in tokens for w in words)

        pick_match = re.search(
            r"\b(?:pick\s*up|grab|fetch|get|bring)\s+(?:me\s+)?(?:my\s+|the\s+|an?\s+)?([a-z][a-z0-9 _-]+)",
            normalized,
        )
        if pick_match:
            label = pick_match.group(1).strip().replace(" ", " ")
            # Map everyday words to vision labels.
            label_map = {
                "pills": "bottle",
                "pill": "bottle",
                "medicine": "bottle",
                "medication": "bottle",
                "remote": "remote",
                "phone": "cell phone",
                "water": "bottle",
                "drink": "bottle",
            }
            label = label_map.get(label, label)
            return ActionRequest(
                source=source,
                intent="pick_object",
                payload={"label": label},
                requires_confirmation=True,
            )

        place_match = re.search(
            r"\b(?:place|drop|put\s*(?:down|away)?)\s+(?:the\s+|an?\s+)?([a-z][a-z0-9_-]+)",
            normalized,
        )
        if place_match:
            return ActionRequest(
                source=source,
                intent="place_object",
                payload={"label": place_match.group(1)},
                requires_confirmation=True,
            )

        if re.search(r"\b(?:stop|e-?stop|emergency|halt|freeze|hold)\b", normalized):
            return ActionRequest(source=source, intent="emergency_stop")

        if re.search(r"\b(?:go\s*home|return\s*home|home\s*position|move\s*home|reset|center|centre)\b", normalized):
            return ActionRequest(source=source, intent="home")
        if normalized in {"home"}:
            return ActionRequest(source=source, intent="home")

        if has("open") and (has("claw") or has("gripper") or has("grip") or len(tokens) <= 3):
            return ActionRequest(source=source, intent="open_claw")
        if "release" in tokens:
            return ActionRequest(source=source, intent="open_claw")

        if has("close") and (has("claw") or has("gripper") or has("grip") or len(tokens) <= 3):
            return ActionRequest(source=source, intent="close_claw")
        if tokens & {"grab", "grip", "clamp", "squeeze"}:
            return ActionRequest(source=source, intent="close_claw")

        if has("lift", "up") or has("arm", "up") or tokens & {"raise", "higher"}:
            return ActionRequest(source=source, intent="lift_up")
        if has("lift", "down") or has("arm", "down") or tokens & {"lower", "down"}:
            return ActionRequest(source=source, intent="lift_down")
        if "lift" in tokens:
            return ActionRequest(source=source, intent="lift_up")
        if "up" in tokens:
            return ActionRequest(source=source, intent="lift_up")

        if has("base", "left") or has("turn", "left") or has("swing", "left") or has("move", "left"):
            return ActionRequest(source=source, intent="base_left")
        if has("base", "right") or has("turn", "right") or has("swing", "right") or has("move", "right"):
            return ActionRequest(source=source, intent="base_right")

        if has("rotate", "left") or has("twist", "left") or has("spin", "left"):
            return ActionRequest(source=source, intent="rotate_left")
        if has("rotate", "right") or has("twist", "right") or has("spin", "right"):
            return ActionRequest(source=source, intent="rotate_right")
        if "rotate" in tokens or "twist" in tokens or "spin" in tokens:
            return ActionRequest(source=source, intent="rotate_right")

        if "left" in tokens:
            return ActionRequest(source=source, intent="base_left")
        if "right" in tokens:
            return ActionRequest(source=source, intent="base_right")

        # Ambiguous single words - pick a sensible default so the robot
        # still responds instead of being silent. Users can always say
        # the opposite direction explicitly.
        if "base" in tokens or "swing" in tokens:
            return ActionRequest(source=source, intent="base_right")
        if "move" in tokens:
            return ActionRequest(source=source, intent="base_right")
        if "arm" in tokens:
            return ActionRequest(source=source, intent="lift_up")
        if "turn" in tokens:
            return ActionRequest(source=source, intent="base_right")
        if "claw" in tokens or "gripper" in tokens:
            return ActionRequest(source=source, intent="open_claw")

        if tokens & {"inspect", "look", "show"}:
            return ActionRequest(source=source, intent="preset_pose", payload={"name": "inspect"})

        if tokens & {"yes", "confirm", "ok", "okay", "affirmative", "yep"}:
            return ActionRequest(source=source, intent="confirm_yes")
        if tokens & {"no", "cancel", "nope", "negative", "nah"}:
            return ActionRequest(source=source, intent="confirm_no")

        if tokens & {"shutdown", "quit", "exit", "bye"}:
            return ActionRequest(source=source, intent="shutdown")

        return None

    def _extract_json(self, content: str) -> str:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return match.group(0)
        return content
