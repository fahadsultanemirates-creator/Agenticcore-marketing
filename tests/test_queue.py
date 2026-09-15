from agenticcore.queue import DraftStore


def test_create_and_get_round_trips(tmp_path):
    store = DraftStore(tmp_path / "drafts.db")
    draft = store.create(brand_slug="acme", channel="facebook", caption="Hello world")

    fetched = store.get(draft.id)
    assert fetched is not None
    assert fetched.brand_slug == "acme"
    assert fetched.channel == "facebook"
    assert fetched.caption == "Hello world"
    assert fetched.status == "draft"


def test_update_changes_status_and_fields(tmp_path):
    store = DraftStore(tmp_path / "drafts.db")
    draft = store.create(brand_slug="acme", channel="instagram", caption="Post")

    store.update(draft.id, status="published", published_post_id="abc123")

    fetched = store.get(draft.id)
    assert fetched.status == "published"
    assert fetched.published_post_id == "abc123"


def test_list_by_status_filters_correctly(tmp_path):
    store = DraftStore(tmp_path / "drafts.db")
    pending = store.create(brand_slug="acme", channel="facebook", caption="A", status="pending_approval")
    store.create(brand_slug="acme", channel="instagram", caption="B", status="draft")

    results = store.list_by_status("pending_approval")

    assert [d.id for d in results] == [pending.id]


def test_get_missing_draft_returns_none(tmp_path):
    store = DraftStore(tmp_path / "drafts.db")
    assert store.get("does-not-exist") is None


def test_update_rejects_column_names_outside_the_schema(tmp_path):
    """Values are bound parameters; the column names are what get interpolated."""

    import pytest

    store = DraftStore(tmp_path / "drafts.db")
    draft = store.create(brand_slug="acme", channel="facebook", caption="A")

    with pytest.raises(ValueError, match="Not updatable"):
        store.update(draft.id, **{"status = 'published', caption": "pwned"})

    with pytest.raises(ValueError, match="Not updatable"):
        store.update(draft.id, id="a-different-id")

    reloaded = store.get(draft.id)
    assert reloaded.status == "draft"
    assert reloaded.caption == "A"


def test_update_still_accepts_every_real_column(tmp_path):
    from agenticcore.queue import UPDATABLE_COLUMNS

    store = DraftStore(tmp_path / "drafts.db")
    draft = store.create(brand_slug="acme", channel="facebook", caption="A")

    store.update(draft.id, **{column: "v" for column in UPDATABLE_COLUMNS})

    assert store.get(draft.id).status == "v"
