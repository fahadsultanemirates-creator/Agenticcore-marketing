"""Ideogram image generation (v3 generate endpoint).

Verified against https://developer.ideogram.ai/api-reference as of writing:
POST https://api.ideogram.ai/v1/ideogram-v3/generate, multipart/form-data,
header "Api-Key". Response: {"data": [{"url": "...", ...}]}.
"""

from __future__ import annotations

import os

import requests

API_URL = "https://api.ideogram.ai/v1/ideogram-v3/generate"


class IdeogramImageClient:
    def __init__(self, api_key: str | None = None, style_type: str = "AUTO"):
        self.api_key = api_key or os.environ["IDEOGRAM_API_KEY"]
        self.style_type = style_type

    def generate_image(self, prompt: str, aspect_ratio: str = "1x1") -> str:
        response = requests.post(
            API_URL,
            headers={"Api-Key": self.api_key},
            data={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "style_type": self.style_type,
                "rendering_speed": "TURBO",
            },
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["url"]
