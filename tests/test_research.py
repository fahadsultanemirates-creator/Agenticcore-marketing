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


# --- keyword territory ---

TERRITORY_REPLY = """### TERM
TERM: how much does an ai automation agency cost
CLUSTER: pricing
INTENT: commercial
STAGE: consideration
PRIORITY: 5
WHY: Every competitor answers it with "contact us".

### TERM
TERM: ai agent vs zapier
PRIORITY: 4
CLUSTER: comparisons
INTENT: informational
STAGE: awareness

### TERM
TERM: is an ai automation agency a scam
CLUSTER: trust
PRIORITY: 3
STAGE: awareness
"""


def test_territory_parses_regardless_of_field_order():
    """Models reorder fields freely; position must not carry meaning."""

    from agenticcore.research import parse_territory

    targets = {t.term: t for t in parse_territory(TERRITORY_REPLY, "ac", [])}

    zapier = targets["ai agent vs zapier"]
    assert zapier.priority == 4          # PRIORITY came before CLUSTER
    assert zapier.cluster == "comparisons"
    assert zapier.intent == "informational"


def test_territory_defaults_fill_missing_fields():
    from agenticcore.research import parse_territory

    t = parse_territory("TERM: something people search for\n", "ac", [])[0]

    assert (t.cluster, t.intent, t.stage, t.priority) == (
        "general", "informational", "awareness", 3)


def test_territory_rejects_out_of_vocabulary_values():
    from agenticcore.research import parse_territory

    t = parse_territory(
        "TERM: a real search term\nINTENT: vibes\nSTAGE: whenever\nPRIORITY: 99\n",
        "ac", [])[0]

    assert t.intent == "informational" and t.stage == "awareness"
    assert t.priority == 5  # clamped, not 99


def _territory(tmp_path):
    from agenticcore.research import TerritoryStore, parse_territory

    store = TerritoryStore(tmp_path / "t.db")
    store.add_all(parse_territory(TERRITORY_REPLY, "ac", ["https://s.test"]))
    return store


def test_next_gap_takes_the_highest_priority_uncovered_term(tmp_path):
    store = _territory(tmp_path)

    assert store.next_gap("ac").term == "how much does an ai automation agency cost"


def test_covering_a_term_moves_the_gap_on(tmp_path):
    store = _territory(tmp_path)
    first = store.next_gap("ac")

    store.record_coverage("draft-1", first.id, "ac")

    assert store.next_gap("ac").term == "ai agent vs zapier"


def test_a_fully_covered_map_deepens_instead_of_stopping(tmp_path):
    store = _territory(tmp_path)
    for i, t in enumerate(store.for_brand("ac")):
        store.record_coverage(f"draft-{i}", t.id, "ac")
    # Cover the top term a second time so it is no longer the least-covered.
    top = [t for t in store.for_brand("ac") if t.priority == 5][0]
    store.record_coverage("draft-extra", top.id, "ac")

    gap = store.next_gap("ac")

    assert gap is not None
    assert gap.times_covered == 1  # the least-covered, not the highest priority


def test_duplicate_terms_are_not_remapped(tmp_path):
    from agenticcore.research import parse_territory

    store = _territory(tmp_path)

    fresh = store.add_all(parse_territory(TERRITORY_REPLY, "ac", []))

    assert fresh == []
    assert len(store.for_brand("ac")) == 3


def test_coverage_report_groups_by_cluster_and_counts(tmp_path):
    store = _territory(tmp_path)
    store.record_coverage("d1", store.next_gap("ac").id, "ac")

    report = store.coverage_report("ac")

    assert "1/3 terms covered" in report
    assert "pricing" in report and "comparisons" in report and "trust" in report


def test_cluster_performance_ranks_clusters_not_posts(tmp_path):
    """One good post is noise; a cluster that consistently wins is an instruction."""

    from agenticcore.performance import PostMetrics
    from agenticcore.research import cluster_performance

    store = _territory(tmp_path)
    targets = store.for_brand("ac")
    pricing = next(t for t in targets if t.cluster == "pricing")
    comparisons = next(t for t in targets if t.cluster == "comparisons")

    metrics = {
        "d1": PostMetrics("d1", "ac", "linkedin", impressions=1000, likes=100),
        "d2": PostMetrics("d2", "ac", "linkedin", impressions=1000, likes=10),
    }
    report = cluster_performance(
        targets, metrics, [("d1", pricing.id), ("d2", comparisons.id)]
    )

    assert report.index("pricing") < report.index("comparisons")  # ranked best first
    assert "10.0%" in report and "1.0%" in report


def test_cluster_performance_is_empty_before_any_results(tmp_path):
    from agenticcore.research import cluster_performance

    assert cluster_performance(_territory(tmp_path).for_brand("ac"), {}, []) == ""


# --- market signals ---

SIGNALS_REPLY = """### SIGNAL
HEADLINE: A major vendor shipped a no-code agent builder this week
KIND: event
WHAT: Launched Tuesday, overlaps what small agencies sell.
WHY: Buyers will ask why they should pay an agency at all.
ANGLE: Answer it before prospects raise it.
URGENCY: 5
FRESHNESS_HOURS: 72

### SIGNAL
HEADLINE: Rival agencies have started publishing real pricing pages
URGENCY: 2
KIND: competitor
FRESHNESS_HOURS: 240
WHY: Pricing transparency is becoming table stakes.
"""


def _signals(tmp_path):
    from agenticcore.research import SignalStore, parse_signals

    store = SignalStore(tmp_path / "s.db")
    store.add_all(parse_signals(SIGNALS_REPLY, "ac", ["https://s.test"]))
    return store


def test_signals_parse_in_any_field_order():
    from agenticcore.research import parse_signals

    by_kind = {s.kind: s for s in parse_signals(SIGNALS_REPLY, "ac", [])}

    assert by_kind["competitor"].urgency == 2        # URGENCY came before KIND
    assert by_kind["competitor"].freshness_hours == 240
    assert by_kind["event"].urgency == 5


def test_freshness_is_capped_at_two_weeks():
    """Anything longer-lived is evergreen and belongs in the territory map."""

    from agenticcore.research import parse_signals

    s = parse_signals("HEADLINE: something happened here\nFRESHNESS_HOURS: 99999\n", "a", [])[0]

    assert s.freshness_hours == 336


def test_live_signals_are_ordered_most_urgent_first(tmp_path):
    store = _signals(tmp_path)

    assert store.next_live("ac").urgency == 5


def test_a_signal_past_its_window_is_no_longer_live(tmp_path):
    import sqlite3

    store = _signals(tmp_path)
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("UPDATE market_signals SET detected_at = datetime('now','-5 days')")
    conn.commit()
    conn.close()

    live = store.live("ac")

    # The 72h event has expired; the 240h competitor signal is still inside its window.
    assert [s.kind for s in live] == ["competitor"]
    assert store.expire_stale("ac") == 1


def test_using_a_signal_takes_it_out_of_the_queue(tmp_path):
    store = _signals(tmp_path)
    first = store.next_live("ac")

    store.mark_used(first.id, draft_id="d1")

    assert store.next_live("ac").id != first.id


def test_duplicate_headlines_are_not_tracked_twice(tmp_path):
    from agenticcore.research import parse_signals

    store = _signals(tmp_path)

    assert store.add_all(parse_signals(SIGNALS_REPLY, "ac", [])) == []


def test_competitor_brief_forbids_inventing_their_numbers():
    """Search shows what a rival published, never how it performed."""

    from agenticcore.research import KIND_COMPETITOR, MarketSignal

    brief = MarketSignal(
        id="1", brand_slug="a", headline="Rival published a pricing page",
        kind=KIND_COMPETITOR,
    ).as_brief()

    assert "never claim engagement numbers" in brief
    assert "Do not name or attack them" in brief


def test_no_signals_found_is_respected(tmp_path):
    from agenticcore.research import OfflineResearchClient, SignalAgent

    agent = SignalAgent(OfflineResearchClient(text="NO SIGNALS FOUND"))

    found, _ = agent.find_events(brand())

    assert found == []


# --- website profiles ---

PROFILE_REPLY = """SUMMARY: A done-for-you agency run by AI agents.
SELLS: Websites, branding, marketing, bookkeeping.
PRICES: Packages from $150. Payment is 30% upfront, 70% on completion.
CTA: Get started free, leading to the signup page.
PROOF: none stated
TONE: Plain and direct. Repeats "one clear price per service".
TOPICS: what a $150 package includes | why we publish prices | what we refuse to build
"""


def test_profile_parses_its_fields_and_topics():
    from agenticcore.research import parse_profile

    p = parse_profile(PROFILE_REPLY, "acme", "https://acme.test")

    assert "$150" in p.prices
    assert p.topics == ["what a $150 package includes", "why we publish prices",
                        "what we refuse to build"]


def test_no_proof_reads_as_empty_so_the_warning_can_fire():
    """'none stated' must not look like a citable proof point."""

    from agenticcore.research import parse_profile

    p = parse_profile(PROFILE_REPLY, "acme", "https://acme.test")

    assert p.proof == ""
    assert "NONE" in p.as_brief()
    assert "never from credibility the business has not earned" in p.as_brief()


def test_not_published_is_kept_because_it_is_a_real_answer():
    from agenticcore.research import parse_profile

    p = parse_profile("SELLS: Things.\nPRICES: not published\n", "a", "https://a.test")

    assert p.prices == "not published"


def test_a_hand_written_profile_needs_no_fetch(tmp_path):
    """Works for a site that is not live yet, which a fetch cannot do at all."""

    from agenticcore.research import load_profile_file, write_profile_template

    path = write_profile_template("newsite", tmp_path)
    path.write_text(
        "# a comment that is ignored\n"
        "URL: https://notlive.test\n"
        "SELLS: Market data packages.\n"
        "PROOF: none stated\n"
        "TOPICS: one good angle here | another good angle here\n"
    )

    p = load_profile_file("newsite", tmp_path)

    assert p.url == "https://notlive.test"
    assert p.sells == "Market data packages."
    assert len(p.topics) == 2
    assert p.from_file


def test_the_brief_says_where_its_facts_came_from(tmp_path):
    """"The site says" and "the owner told us" are different kinds of fact."""

    from agenticcore.research import load_profile_file, parse_profile, write_profile_template

    fetched = parse_profile(PROFILE_REPLY, "acme", "https://acme.test")
    assert "read from the live site" in fetched.as_brief()

    write_profile_template("acme", tmp_path)
    supplied = load_profile_file("acme", tmp_path)
    assert "supplied by the owner" in supplied.as_brief()


def test_the_template_is_never_overwritten(tmp_path):
    from agenticcore.research import write_profile_template

    path = write_profile_template("acme", tmp_path)
    path.write_text("SELLS: my own careful notes\n")

    write_profile_template("acme", tmp_path)

    assert "my own careful notes" in path.read_text()


def test_a_missing_profile_file_is_not_an_error(tmp_path):
    from agenticcore.research import load_profile_file

    assert load_profile_file("nothing-here", tmp_path) is None


def test_store_round_trips_a_profile(tmp_path):
    from agenticcore.research import WebsiteStore, parse_profile

    store = WebsiteStore(tmp_path / "w.db")
    store.save(parse_profile(PROFILE_REPLY, "acme", "https://acme.test"))

    loaded = store.get("acme")

    assert loaded.sells.startswith("Websites")
    assert len(loaded.topics) == 3


def test_re_reading_replaces_rather_than_duplicates(tmp_path):
    from agenticcore.research import WebsiteStore, parse_profile

    store = WebsiteStore(tmp_path / "w.db")
    store.save(parse_profile("SELLS: old copy\n", "acme", "https://acme.test"))
    store.save(parse_profile("SELLS: new copy\n", "acme", "https://acme.test"))

    assert store.get("acme").sells == "new copy"
