"""Common interface every image/video generation backend implements."""

from __future__ import annotations

from typing import Protocol


class ImageGenerator(Protocol):
    def generate_image(self, prompt: str) -> str:
        """Return a URL (or local file path) to the generated image."""
        ...


class VideoGenerator(Protocol):
    def generate_video(self, script: str) -> str:
        """Return a URL (or local file path) to the generated video."""
        ...


class OfflineImageGenerator:
    """Dry-run stand-in: no network call, returns a placeholder reference."""

    def generate_image(self, prompt: str) -> str:
        return f"offline://image?prompt={prompt[:60]!r}"


class OfflineVideoGenerator:
    """Dry-run stand-in: no network call, returns a placeholder reference."""

    def generate_video(self, script: str) -> str:
        return f"offline://video?script={script[:60]!r}"
