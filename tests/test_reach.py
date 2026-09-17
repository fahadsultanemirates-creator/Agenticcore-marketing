from agenticcore.agents.reach_critic import REVISE_BELOW, parse_verdict
from agenticcore.reach import (
    DISCOVERY_ALGORITHMIC,
    DISCOVERY_BROADCAST,
    DISCOVERY_GRAPH,
    REACH_RULES,
    brief_for_agent,
    rules_for,
    split_link,
)

POST_WITH_LINK = "Here is the teardown.\n\nRead more: https://example.dev/x\n\n#B2BSaaS"


def test_link_moves_to_first_comment_where_it_costs_reach():
    for platform in ("linkedin", "facebook", "twitter"):
        body, comment = split_link(POST_WITH_LINK, platform)
        assert "https://" not in body, platform
        assert comment == "https://example.dev/x", platform


def test_link_is_stripped_where_it_is_not_clickable():
    for platform in ("instagram", "tiktok"):
        body, comment = split_link(POST_WITH_LINK, platform)
        assert "https://" not in body, platform
        assert comment is None, platform  # nothing to salvage into a comment


def test_link_is_left_alone_where_it_costs_nothing():
    body, comment = split_link(POST_WITH_LINK, "telegram")

    assert body == POST_WITH_LINK
    assert comment is None


def test_stripping_a_link_does_not_leave_a_dangling_label():
    body, _ = split_link(POST_WITH_LINK, "linkedin")

    assert "Read more:" not in body
    assert not body.endswith(":")
    assert "\n\n\n" not in body
    assert "#B2BSaaS" in body  # the rest of the post survives


def test_a_post_with_no_link_is_unchanged():
    plain = "Just a post.\n\nNo links here."
    assert split_link(plain, "linkedin") == (plain, None)


def test_unknown_platform_defaults_to_protecting_reach():
    """A platform we haven't profiled shouldn't silently eat a link penalty."""

    body, comment = split_link(POST_WITH_LINK, "someNewNetwork")

    assert "https://" not in body
    assert comment == "https://example.dev/x"


def test_discovery_model_says_who_can_reach_from_a_standing_start():
    # The whole point: a new account can only earn reach on merit where the
    # platform shows content to non-followers.
    assert rules_for("tiktok").new_account_can_reach
    assert rules_for("instagram").new_account_can_reach
    assert not rules_for("linkedin").new_account_can_reach
    assert not rules_for("telegram").new_account_can_reach


def test_every_platform_has_a_coherent_profile():
    for platform, r in REACH_RULES.items():
        assert r.discovery in (DISCOVERY_ALGORITHMIC, DISCOVERY_GRAPH, DISCOVERY_BROADCAST)
        low, high = r.hashtag_range
        assert 0 <= low <= high, platform
        assert 0 < r.hook_chars <= r.body_target_chars, platform


def test_brief_tells_the_writer_the_rule_that_applies():
    linkedin = brief_for_agent("linkedin")
    telegram = brief_for_agent("telegram")

    assert "first comment" in linkedin.lower()
    assert "first hour" in linkedin.lower()       # graph platform
    assert "reach equals subscriber count" in telegram.lower()  # broadcast
    assert "first hour" not in telegram.lower()   # meaningless without a feed


def test_search_guidance_only_where_search_is_a_route():
    assert "search" in brief_for_agent("tiktok").lower()
    assert "compounds" in brief_for_agent("youtube").lower()
    assert "compounds" not in brief_for_agent("facebook").lower()


def test_verdict_parses_score_problems_and_rewrite():
    v = parse_verdict(
        "SCORE: 5\nPROBLEMS:\n- Hook is the brand name\nREVISED:\nThe real hook.\nSecond line."
    )

    assert v.score == 5
    assert "brand name" in v.problems
    assert v.revised == "The real hook.\nSecond line."
    assert v.should_revise


def test_a_strong_score_leaves_the_original_alone():
    v = parse_verdict("SCORE: 9\nPROBLEMS:\n- minor\nREVISED:\nSomething else.")

    assert not v.should_revise
    assert v.score >= REVISE_BELOW


def test_unparseable_reply_fails_open():
    """A confused parser must never be why a blank caption gets queued."""

    v = parse_verdict("Looks good to me!")

    assert v.score == 10
    assert not v.should_revise


def test_a_score_with_a_rewrite_but_no_problems_still_revises():
    v = parse_verdict("SCORE: 3\nREVISED:\nMuch better opening line.")

    assert v.should_revise
    assert v.revised == "Much better opening line."


def test_out_of_range_scores_are_clamped():
    assert parse_verdict("SCORE: 47\nREVISED:\nx").score == 10
    assert parse_verdict("SCORE: 0\nREVISED:\nx").score == 1
