"""Reads back how a published post actually performed.

Verified against https://www.ayrshare.com/docs/apis/analytics/post as of
writing: POST https://api.ayrshare.com/api/analytics/post, Bearer auth,
body ``{"id": "<ayrshare post id>", "platforms": [...]}`` with ``platforms``
optional, and the same optional ``Profile-Key`` header the publish endpoint
takes to select one project's linked accounts.

The response is keyed by platform and each network returns its own shape —
YouTube reports average watch time, Facebook reports reaction breakdowns,
TikTok reports video views. Normalizing that into something comparable is
``agenticcore.performance``'s job, not this module's: here we keep the
payload as the API gave it to us.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

API_URL = "https://api.ayrshare.com/api/analytics/post"


class AyrshareAnalyticsError(RuntimeError):
    pass


class AyrshareAnalytics:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ["AYRSHARE_API_KEY"]

    def post_analytics(
        self,
        post_id: str,
        platforms: Optional[list[str]] = None,
        profile_key: Optional[str] = None,
    ) -> dict:
        """Return the analytics payload for one published post.

        ``post_id`` is the id Ayrshare returned when the post was published
        — what ``PostDraft.published_post_id`` holds.
        """

        headers = {"Authorization": f"Bearer {self.api_key}"}
        if profile_key:
            headers["Profile-Key"] = profile_key

        payload: dict = {"id": post_id}
        if platforms:
            payload["platforms"] = platforms

        response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        try:
            data = response.json()
        except ValueError as exc:  # pragma: no cover - malformed upstream response
            raise AyrshareAnalyticsError(
                f"Ayrshare returned non-JSON for post {post_id}: {response.text[:200]}"
            ) from exc

        if response.status_code >= 400 or data.get("status") == "error":
            raise AyrshareAnalyticsError(data)
        return data
