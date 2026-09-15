"""xAI Grok image generation.

Verified against https://docs.x.ai/docs/guides/image-generations as of
writing: POST https://api.x.ai/v1/images/generations, Bearer auth,
OpenAI-compatible response shape {"data": [{"url": "..."}]}.
"""

from __future__ import annotations

import os

import requests

API_URL = "https://api.x.ai/v1/images/generations"


class GrokImageClient:
    def __init__(self, api_key: str | None = None, model: str = "grok-imagine-image-2.0"):
        self.api_key = api_key or os.environ["XAI_API_KEY"]
        self.model = model

    def generate_image(self, prompt: str) -> str:
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "prompt": prompt, "n": 1},
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["data"][0]["url"]
