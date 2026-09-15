import json

import pytest

from agenticcore.brands import BrandProfile, BrandRegistry


def make(**overrides):
    data = {"slug": "t", "name": "T", "audience": "A"}
    data.update(overrides)
    return BrandProfile.from_dict(data)


def test_page_media_falls_back_to_the_brand_default():
    brand = make(media="video", channels=[
        {"channel": "tiktok", "label": "TT"},
        {"channel": "linkedin", "label": "LI", "media": "image"},
    ])
    resolved = {t.channel: brand.media_for(t) for t in brand.channels}

    assert resolved == {"tiktok": "video", "linkedin": "image"}
    assert brand.required_media_types() == {"video", "image"}


def test_required_media_types_ignores_text_only_pages():
    brand = make(media="none", channels=[
        {"channel": "facebook", "label": "FB"},
        {"channel": "linkedin", "label": "LI", "media": "image"},
    ])

    assert brand.required_media_types() == {"image"}


def test_rejects_the_wrong_id_for_x():
    """Ayrshare's id is "twitter"; "x" would fail silently at publish time."""

    with pytest.raises(ValueError, match="twitter"):
        make(channels=[{"channel": "x", "label": "X"}])


def test_rejects_youtube_without_video():
    with pytest.raises(ValueError, match="requires a video"):
        make(media="image", channels=[{"channel": "youtube", "label": "YT"}])


def test_rejects_text_only_on_a_platform_that_needs_media():
    with pytest.raises(ValueError, match="text-only"):
        make(media="none", channels=[{"channel": "instagram", "label": "IG"}])


def test_registry_surfaces_a_bad_brand_file_by_name(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps(
        {"slug": "broken", "name": "B", "audience": "A",
         "channels": [{"channel": "youtube", "label": "YT"}]}
    ))

    with pytest.raises(ValueError, match="broken"):
        BrandRegistry(tmp_path)
