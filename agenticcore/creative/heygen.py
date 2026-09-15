"""HeyGen avatar video generation.

NOTE: HeyGen's docs site blocked automated verification when this file was
written, so this client is built from HeyGen's long-stable v1/v2 API shape
rather than a freshly fetched page. Confirm the endpoint paths and response
fields against your HeyGen dashboard/docs before relying on this in
production, and adjust if they've changed.

Expected flow:
  1. POST /v2/video/generate  -> {"data": {"video_id": "..."}}
  2. GET  /v1/video_status.get?video_id=...
       -> {"data": {"status": "completed"|"processing"|"failed", "video_url": "..."}}
"""

from __future__ import annotations

import os
import time

import requests

BASE_URL = "https://api.heygen.com"


class HeyGenVideoClient:
    def __init__(self, api_key: str | None = None, avatar_id: str | None = None,
                 voice_id: str | None = None):
        self.api_key = api_key or os.environ["HEYGEN_API_KEY"]
        self.avatar_id = avatar_id or os.environ["HEYGEN_AVATAR_ID"]
        self.voice_id = voice_id or os.environ["HEYGEN_VOICE_ID"]

    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def generate_video(self, script: str, poll_interval: float = 5.0, timeout: float = 600.0) -> str:
        video_id = self._submit(script)
        return self._await_completion(video_id, poll_interval=poll_interval, timeout=timeout)

    def _submit(self, script: str) -> str:
        payload = {
            "video_inputs": [
                {
                    "character": {"type": "avatar", "avatar_id": self.avatar_id, "avatar_style": "normal"},
                    "voice": {"type": "text", "input_text": script, "voice_id": self.voice_id},
                }
            ],
            "dimension": {"width": 1080, "height": 1920},
        }
        response = requests.post(f"{BASE_URL}/v2/video/generate", headers=self._headers(), json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["data"]["video_id"]

    def _await_completion(self, video_id: str, poll_interval: float, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = requests.get(
                f"{BASE_URL}/v1/video_status.get",
                headers=self._headers(),
                params={"video_id": video_id},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()["data"]
            if data["status"] == "completed":
                return data["video_url"]
            if data["status"] == "failed":
                raise RuntimeError(f"HeyGen video {video_id} failed: {data}")
            time.sleep(poll_interval)
        raise TimeoutError(f"HeyGen video {video_id} did not complete within {timeout}s")
