"""Telegram-based human approval gate.

Every generated post is sent to a Telegram chat with Approve/Reject buttons
before anything goes near a real page. Nothing publishes without a tap.

Setup:
  1. Message @BotFather on Telegram, run /newbot, copy the token it gives you.
  2. Add that bot to the chat (a DM with yourself, or a group) you want
     approvals to land in, and grab the chat id (e.g. via
     https://api.telegram.org/bot<token>/getUpdates after sending it a message).
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or a per-brand
     telegram_chat_id in that brand's JSON profile).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional

import requests


def _api_base(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"


class TelegramApprovalBot:
    def __init__(self, token: Optional[str] = None, default_chat_id: Optional[str] = None):
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.default_chat_id = default_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self._offset = 0

    def _call(self, method: str, **params) -> dict:
        response = requests.post(f"{_api_base(self.token)}/{method}", json=params, timeout=35)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error calling {method}: {data}")
        return data["result"]

    def send_for_approval(self, draft, caption: str, chat_id: Optional[str] = None) -> int:
        """Post ``draft`` (with its caption/media) for approval; returns the message id."""

        target_chat = chat_id or draft.telegram_chat_id or self.default_chat_id
        if not target_chat:
            raise ValueError("No Telegram chat id configured for this draft or as a default")

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve & post", "callback_data": f"approve:{draft.id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{draft.id}"},
                ]
            ]
        }

        media_url = draft.video_path or draft.image_path
        if media_url and media_url.startswith(("http://", "https://")):
            method = "sendVideo" if draft.video_path else "sendPhoto"
            field = "video" if draft.video_path else "photo"
            result = self._call(
                method,
                chat_id=target_chat,
                caption=caption,
                reply_markup=keyboard,
                **{field: media_url},
            )
        elif media_url:
            result = self._send_local_media(target_chat, media_url, caption, keyboard, is_video=bool(draft.video_path))
        else:
            result = self._call("sendMessage", chat_id=target_chat, text=caption, reply_markup=keyboard)

        return result["message_id"]

    def _send_local_media(self, chat_id: str, path: str, caption: str, keyboard: dict, is_video: bool) -> dict:
        import json

        method = "sendVideo" if is_video else "sendPhoto"
        field = "video" if is_video else "photo"
        with open(path, "rb") as handle:
            response = requests.post(
                f"{_api_base(self.token)}/{method}",
                data={"chat_id": chat_id, "caption": caption, "reply_markup": json.dumps(keyboard)},
                files={field: (Path(path).name, handle)},
                timeout=90,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error sending media: {data}")
        return data["result"]

    def poll_decisions(self, timeout: int = 30) -> Iterator[tuple[str, str]]:
        """Long-poll for Approve/Reject taps; yields (draft_id, "approve"|"reject")."""

        updates = self._call("getUpdates", offset=self._offset, timeout=timeout)
        for update in updates:
            self._offset = update["update_id"] + 1
            callback = update.get("callback_query")
            if not callback or "data" not in callback:
                continue
            action, _, draft_id = callback["data"].partition(":")
            if action not in ("approve", "reject"):
                continue
            self._call(
                "answerCallbackQuery",
                callback_query_id=callback["id"],
                text="Approved — publishing..." if action == "approve" else "Rejected",
            )
            yield draft_id, action
