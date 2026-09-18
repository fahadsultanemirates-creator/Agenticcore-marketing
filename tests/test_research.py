import pytest

from agenticcore.brands import BrandProfile
from agenticcore.research import (
    STATUS_OPEN,
    STATUS_USED,
    ContentOpportunity,
    DemandResearchAgent,
    OfflineResearchClient,
    OpportunityStore,
    parse_opportunities,
)
from agenticcore.research.client import ResearchResult

TWO_BLOCKS = """### OPPORTUNITY
QUERY: how much does an AI automation agency actually cost
EVIDENCE: Asked repeatedly on r/smallbusiness; every agency site says "contact us".
ANGLE: Publish real ranges and what moves you between tiers.
FORMAT: post

### OPPORTUNITY
QUERY: who owns the code when an AI agency finishes the project
EVIDENCE: Named as a top vetting question in several 2026 buyer guides.
ANGLE: State the ownership terms plainly, before being asked.
FORMAT: video
"""


def brand(**overrides):
    data = {
        "slug": "agenticcore", "name": "AgenticCore",
        "audience": "Founders at SMBs", "goal": "Book discovery calls",
        "channels": [{"channel": "linkedin", "label": "LI"}],
    }
    data.update(overrides)
    return BrandProfile.from_dict(data)


def test_parses_each_block_with_its_fields():
    ops = parse_opportunities(TWO_BLOCKS, "agenticcore", ["https://a.test"])

    assert len(ops) == 2
    assert ops[0].query.startswith("how much does an AI automation agency")
    assert "r/smallbusiness" in ops[0].evidence
    assert ops[1].suggested_format == "video"
    assert ops[0].sources == ["https://a.test"]


def test_a_block_missing_optional_fields_is_still_kept():
    """A real finding shouldn't be dropped because the model skipped a line."""

    ops = parse_opportunities("### OPPORTUNITY\nQUERY: what does an AI agent actually do\n", "b", [])

    assert len(ops) == 1
    assert ops[0].evidence == "" and ops[0].angle == ""


def test_prose_with_no_blocks_yields_nothing():
    assert parse_opportunities("I could not find anything useful.", "b", []) == []


def test_a_too_short_query_is_ignored():
    assert parse_opportunities("QUERY: ai\n", "b", []) == []


def test_no_opportunities_found_is_respected_not_parsed():
    """Finding nothing is a valid answer; inventing topics is the failure mode."""

    agent = DemandResearchAgent(OfflineResearchClient(text="NO OPPORTUNITIES FOUND"))

    ops, result = agent.find(brand())

    assert ops == []
    assert result.text == "NO OPPORTUNITIES FOUND"


def test_research_prompt_carries_the_brands_own_details():
    client = OfflineResearchClient(text=TWO_BLOCKS)
    agent = DemandResearchAgent(client)

    agent.find(brand(keywords=["ai automation agency"]))

    _, prompt = client.calls[0]
    assert "AgenticCore" in prompt
    assert "Founders at SMBs" in prompt
    assert "ai automation agency" in prompt


def test_already_covered_queries_are_excluded_from_the_next_search():
    client = OfflineResearchClient(text=TWO_BLOCKS)
    agent = DemandResearchAgent(client)

    agent.find(brand(), already_covered=["how much does an AI agency cost"])

    _, prompt = client.calls[0]
    assert "do NOT return these again" in prompt
    assert "how much does an AI agency cost" in prompt


def test_ungrounded_results_are_flagged():
    """A confident guess is worse than no answer when picking topics."""

    assert not ResearchResult(text="x", searches_run=0).is_grounded
    assert ResearchResult(text="x", searches_run=3).is_grounded


def test_store_round_trips_an_opportunity(tmp_path):
    store = OpportunityStore(tmp_path / "db.sqlite")
    ops = parse_opportunities(TWO_BLOCKS, "agenticcore", ["https://a.test"])

    store.add_all(ops)
    loaded = store.for_brand("agenticcore")

    assert len(loaded) == 2
    assert loaded[0].sources == ["https://a.test"]
    assert all(o.status == STATUS_OPEN for o in loaded)


def test_duplicate_queries_are_not_stored_twice(tmp_path):
    """Weekly research resurfaces evergreen questions; the queue must not fill up."""

    store = OpportunityStore(tmp_path / "db.sqlite")
    store.add_all(parse_opportunities(TWO_BLOCKS, "agenticcore", []))

    fresh = store.add_all(parse_opportunities(TWO_BLOCKS, "agenticcore", []))

    assert fresh == []
    assert len(store.for_brand("agenticcore")) == 2


def test_opportunities_are_consumed_in_order(tmp_path):
    store = OpportunityStore(tmp_path / "db.sqlite")
    store.add_all(parse_opportunities(TWO_BLOCKS, "agenticcore", []))

    first = store.next_open("agenticcore")
    store.mark_used(first.id, draft_id="draft-1")
    second = store.next_open("agenticcore")

    assert second.id != first.id
    assert store.for_brand("agenticcore", status=STATUS_USED)[0].used_by_draft == "draft-1"


def test_next_open_is_none_once_the_territory_is_spent(tmp_path):
    store = OpportunityStore(tmp_path / "db.sqlite")
    store.add_all(parse_opportunities(TWO_BLOCKS, "agenticcore", []))
    for o in store.for_brand("agenticcore"):
        store.mark_used(o.id)

    assert store.next_open("agenticcore") is None


def test_brands_do_not_see_each_others_opportunities(tmp_path):
    store = OpportunityStore(tmp_path / "db.sqlite")
    store.add_all(parse_opportunities(TWO_BLOCKS, "agenticcore", []))
    store.add_all(parse_opportunities(TWO_BLOCKS, "mmcore", []))

    assert len(store.for_brand("agenticcore")) == 2
    assert len(store.for_brand("mmcore")) == 2
    assert store.next_open("mmcore").brand_slug == "mmcore"


def test_as_brief_tells_the_writer_what_to_answer_and_not_to_embellish():
    op = ContentOpportunity(
        id="1", brand_slug="b", query="what does it cost",
        evidence="asked everywhere", angle="give real numbers",
        sources=["https://a.test"],
    )

    text = op.as_brief()

    assert "what does it cost" in text
    assert "give real numbers" in text
    assert "do not embellish" in text
