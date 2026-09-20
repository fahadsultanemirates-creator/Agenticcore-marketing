from agenticcore.topics import TopicLedger, topic_key


def test_a_topic_is_spent_once(tmp_path):
    ledger = TopicLedger(tmp_path / "t.db")

    assert ledger.mark("acme", "what a package includes") is True
    assert ledger.mark("acme", "what a package includes") is False
    assert len(ledger.covered("acme")) == 1


def test_near_identical_topics_count_as_one(tmp_path):
    ledger = TopicLedger(tmp_path / "t.db")
    ledger.mark("acme", "What A Package Includes")

    assert ledger.mark("acme", "what   a package includes") is False


def test_a_blank_topic_is_not_recorded(tmp_path):
    """An open topic spends nothing, or the first blank would block the rest."""

    ledger = TopicLedger(tmp_path / "t.db")

    assert ledger.mark("acme", "") is False
    assert ledger.mark("acme", "   ") is False
    assert ledger.covered("acme") == []


def test_sites_do_not_share_a_ledger(tmp_path):
    ledger = TopicLedger(tmp_path / "t.db")
    ledger.mark("acme", "a shared sounding topic")

    assert ledger.mark("mmcore", "a shared sounding topic") is True
    assert len(ledger.used_keys("acme")) == 1
    assert len(ledger.used_keys("mmcore")) == 1


def test_posts_and_videos_are_recorded_with_their_kind(tmp_path):
    ledger = TopicLedger(tmp_path / "t.db")
    ledger.mark("acme", "a topic for a post", kind="post")
    ledger.mark("acme", "a topic for a video", kind="video")

    kinds = {c.kind for c in ledger.covered("acme")}
    assert kinds == {"post", "video"}


def test_reset_frees_one_site_only(tmp_path):
    ledger = TopicLedger(tmp_path / "t.db")
    ledger.mark("acme", "topic one here")
    ledger.mark("mmcore", "topic two here")

    freed = ledger.reset("acme")

    assert freed == 1
    assert ledger.used_keys("acme") == set()
    assert len(ledger.used_keys("mmcore")) == 1


def test_a_single_topic_can_be_released(tmp_path):
    """For when a batch was discarded unused."""

    ledger = TopicLedger(tmp_path / "t.db")
    ledger.mark("acme", "topic one here")
    ledger.mark("acme", "topic two here")

    assert ledger.release("acme", "Topic One Here") is True
    assert len(ledger.used_keys("acme")) == 1
    assert ledger.release("acme", "never existed here") is False


def test_the_ledger_survives_a_restart(tmp_path):
    TopicLedger(tmp_path / "t.db").mark("acme", "a topic worth remembering")

    assert len(TopicLedger(tmp_path / "t.db").used_keys("acme")) == 1


def test_key_normalises_case_and_whitespace():
    assert topic_key("  Hello   World  ") == topic_key("hello world")
