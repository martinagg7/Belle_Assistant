"""Conversation mode for Belle.

When active, Belle keeps listening without needing the wake word.

Activation reasons:
    -"perfil": the user wants to tell Belle something about themselves
    -"conversacion": Belle ended with a question and waits for the answer
    -"juego":trivial game running
    -"juego_mat":mental-maths game running
"""
from __future__ import annotations

import os
import logging

from groq import Groq
from config import Config
from tools.profile import update_profile

logger = logging.getLogger(__name__)


class ConversationMode:

    def __init__(self) -> None:
        self._active = False
        self._reason: str | None = None
        self._buffer: list[str] = []
        self._turns = 0
        self.MAX_TURNS = 5
        self._client = Groq(api_key=Config.GROQ_API_KEY)
        self._closing_prompt = self._load_closing_prompt()

    def _load_closing_prompt(self) -> str:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "conversation_closing_prompt.md")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def closes_conversation(self, text: str) -> bool:
        """Ask Groq whether the user's message closes the conversation."""
        try:
            response = self._client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": self._closing_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=5,
                stream=False,
            )
            decision = response.choices[0].message.content.strip().lower()
            logger.info("ConversationMode: closing=%s for '%s'", decision, text)
            return decision == "cierra"
        except Exception:
            logger.error("ConversationMode: error classifying closing", exc_info=True)
            return self.is_negation(text)  # fall back to the simple method

    def is_active(self) -> bool:
        return self._active

    def reason(self) -> str | None:
        return self._reason

    def is_negation(self, text: str) -> bool:
        t = text.strip().lower()
        negation_starts = ["no ", "no,", "nada", "ya está", "es todo", "no quiero", "no gracias", "para", "basta", "déjalo"]
        return t == "no" or any(t.startswith(p) for p in negation_starts)

    def activate(self, reason: str) -> None:
        self._active = True
        self._reason = reason
        self._buffer = []
        self._turns = 0
        logger.info("ConversationMode: activated — reason: %s", reason)

    def deactivate(self) -> None:
        self._active = False
        self._reason = None
        self._buffer = []
        self._turns = 0
        logger.info("ConversationMode: deactivated")

    def is_rest_answer(self, text: str) -> bool:
        # If they say yes to resting from the game
        rest_yes = ["sí", "si", "vale", "de acuerdo", "bueno", "venga", "sí quiero", "lo dejamos"]
        return text.strip().lower() in rest_yes

    def process_profile(self, text: str) -> tuple[str, bool]:
        self._turns += 1

        end_phrases = ["no", "no gracias", "nada", "no quiero", "ya está", "es todo", "ya estoy", "hasta aquí"]
        text_lower = text.lower().strip()
        finished = any(phrase in text_lower for phrase in end_phrases)

        if not finished and text_lower:
            self._buffer.append(text)

        if finished or self._turns >= self.MAX_TURNS:
            self._save_buffer()
            self.deactivate()
            return "Lo tendré en cuenta.", False

        return "¿Quieres contarme algo más?", True

    def process_game(self, text: str) -> tuple[str, bool]:
        """Delegate to play_game and check whether the game is still running.
        The GameEngine itself decides when to stop; we do not manage it here.
        """
        from tools.games import play_game, game_active

        result = play_game(text)

        if not game_active():
            self.deactivate()
            return result, False

        return result, True

    def _save_buffer(self) -> None:
        if not self._buffer:
            return
        full_text = " ".join(self._buffer)
        logger.info("ConversationMode: saving profile note")
        update_profile(full_text)
