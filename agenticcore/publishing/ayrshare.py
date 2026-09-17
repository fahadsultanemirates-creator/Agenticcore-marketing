"""Publishes approved posts through Ayrshare's unified social API.

Verified against https://www.ayrshare.com/docs/apis/post/post as of writing:
POST https://api.ayrshare.com/api/post, Bearer auth, optional Profile-Key
header to target one of your linked-account groups (one project = one
Profile-Key). This is what lets you post to Meta/LinkedIn/X/etc. pages you
already own without going through each platform's own developer approval.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

API_URL = "https://api.ayrshare.com/api/post"
COMMENT_URL = "https://api.ayrshare.com/api/comments"


class AyrshareError(RuntimeError):
    pass


class AyrshareClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ["AYRSHARE_API_KEY"]

    def publish(
        self,
        caption: str,
        platforms: list[str],
        media_urls: Optional[list[str]] = None,
        profile_key: Optional[str] = None,
    ) -> dict:
        """Publish ``caption`` (+ optional media) to the given platforms.

        ``profile_key`` should be the target project's Ayrshare Profile-Key
        (``BrandProfile.ayrshare_profile_key``); omit it on single-profile
        plans where there is only one linked-account group on the API key.
        """

        headers = {"Authorization": f"Bearer {self.api_key}"}
        if profile_key:
            headers["Profile-Key"] = profile_key

        payload: dict = {"post": caption, "platforms": platforms}
        if media_urls:
            payload["mediaUrls"] = media_urls

        return self._post(API_URL, headers, payload)

    def comment(
        self,
        post_id: str,
        comment: str,
        platforms: list[str],
        profile_key: Optional[str] = None,
    ) -> dict:
        """Add a comment to a post we published.

        Verified against https://www.ayrshare.com/docs/apis/comments/overview
        as of writing: POST https://api.ayrshare.com/api/comments with
        ``{"id": <post id>, "platforms": [...], "comment": "..."}``.

        Used for the first-comment link: on LinkedIn, Facebook and X a link
        in the post body costs a large share of its reach, so the body ships
        clean and the link arrives here instead.
        """

        headers = {"Authorization": f"Bearer {self.api_key}"}
        if profile_key:
            headers["Profile-Key"] = profile_key
        return self._post(
            COMMENT_URL,
            headers,
            {"id": post_id, "platforms": platforms, "comment": comment},
        )

    @staticmethod
    def _post(url: str, headers: dict, payload: dict) -> dict:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        try:
            data = response.json()
        except ValueError as exc:  # pragma: no cover - malformed upstream response
            raise AyrshareError(
                f"Ayrshare returned non-JSON ({response.status_code}): "
                f"{response.text[:200]}"
            ) from exc
        if response.status_code >= 400 or data.get("status") == "error":
            raise AyrshareError(data)
        return data
