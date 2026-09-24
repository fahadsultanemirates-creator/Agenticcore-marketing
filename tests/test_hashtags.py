"""Every text post ends with hashtags, whatever the model returned."""

from agenticcore.brands import BrandProfile
from agenticcore.hashtags import brand_tags, ensure_hashtags, found_in, to_tag


def a_brand(**kw):
    defaults = dict(
        slug="mmcore", name="M&MCore", description="", audience="", goal="",
        tone="", keywords=["social media marketing for real estate agents",
                           "real estate marketing packages",
                           "fixed price marketing agency"],
    )
    defaults.update(kw)
    return BrandProfile(**{k: v for k, v in defaults.items()
                           if k in BrandProfile.__dataclass_fields__})


class TestTagConversion:
    def test_a_long_keyword_becomes_a_readable_tag(self):
        assert to_tag("social media marketing for real estate agents") == "#SocialMediaMarketing"

    def test_stopwords_are_dropped_not_camel_cased(self):
        assert to_tag("how to start a business") == "#StartBusiness"

    def test_punctuation_never_reaches_the_tag(self):
        assert to_tag("M&MCore") == "#MMCore"
        assert to_tag("agenticcore.agency") == "#AgenticcoreAgency"

    def test_a_phrase_of_only_stopwords_still_yields_something(self):
        assert to_tag("the and of") == "#TheAndOf"

    def test_an_empty_phrase_yields_no_tag(self):
        assert to_tag("") == ""
        assert to_tag("!!!") == ""


class TestBrandFallback:
    def test_keywords_lead_and_the_brand_name_trails(self):
        tags = brand_tags(a_brand())
        assert tags[0] == "#SocialMediaMarketing"
        assert tags[-1] == "#MMCore"

    def test_keywords_collapsing_to_the_same_tag_are_deduped(self):
        tags = brand_tags(a_brand(keywords=["real estate marketing",
                                            "real estate marketing packages"]))
        assert tags.count("#RealEstateMarketing") == 1

    def test_a_brand_with_no_keywords_still_has_its_name(self):
        assert brand_tags(a_brand(keywords=[])) == ["#MMCore"]


class TestTheGuarantee:
    def test_a_bare_post_gets_topped_up_to_the_minimum(self):
        out = ensure_hashtags("The post.", "facebook", a_brand())

        assert len(found_in(out)) == 2  # facebook's floor
        assert out.startswith("The post.")

    def test_a_post_that_already_has_enough_is_left_alone(self):
        caption = "The post.\n\n#One #Two #Three"

        assert ensure_hashtags(caption, "facebook", a_brand()) == caption

    def test_a_partial_tag_line_is_extended_not_duplicated(self):
        out = ensure_hashtags("The post.\n\n#One", "facebook", a_brand())

        assert len(found_in(out)) == 2
        assert out.count("\n\n") == 1  # one tag block, not two

    def test_whatsapp_is_left_bare_because_tags_do_nothing_there(self):
        assert ensure_hashtags("The post.", "whatsapp", a_brand()) == "The post."

    def test_telegram_gets_tags_now_that_they_are_searchable_in_channel(self):
        assert found_in(ensure_hashtags("The post.", "telegram", a_brand()))

    def test_a_tag_the_post_already_carries_is_not_repeated(self):
        out = ensure_hashtags("The post.\n\n#SocialMediaMarketing",
                              "facebook", a_brand())

        assert out.count("#SocialMediaMarketing") == 1

    def test_case_differences_still_count_as_the_same_tag(self):
        out = ensure_hashtags("The post.\n\n#socialmediamarketing",
                              "facebook", a_brand())

        assert out.lower().count("#socialmediamarketing") == 1

    def test_consecutive_posts_do_not_end_with_the_same_tags(self):
        first = ensure_hashtags("Post one.", "twitter", a_brand(), index=0)
        second = ensure_hashtags("Post two.", "twitter", a_brand(), index=1)

        assert found_in(first) != found_in(second)

    def test_rotation_survives_a_brand_with_one_usable_tag(self):
        brand = a_brand(keywords=[])

        assert found_in(ensure_hashtags("P.", "twitter", brand, index=7)) == ["#MMCore"]
