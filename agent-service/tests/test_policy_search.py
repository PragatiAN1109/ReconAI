"""Tests for deterministic lexical policy search.

Most run against a small fixture corpus so assertions do not shift when the real
policy wording is edited. A few run against the shipped corpus, because the
point of the corpus is that real investigation queries find the right document.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.policy_search import PolicyEvidence, PolicySearch, tokenize

FIXTURE_DOCUMENTS = {
    "alpha.md": """---
document_id: POL-TEST-001
title: Alpha Policy
---

# Alpha Policy

## Settlement Fees

A processing fee is deducted from the settlement amount before funds arrive.

## Unrelated Matters

Stationery requisitions are handled quarterly.
""",
    "beta.md": """---
document_id: POL-TEST-002
title: Beta Policy
---

# Beta Policy

## Settlement Fees

A fee may apply.
""",
    "no-identifier.md": """# Not A Policy

This file has no front matter and cannot be cited.
""",
}


@pytest.fixture
def fixture_corpus(tmp_path: Path) -> Path:
    for name, content in FIXTURE_DOCUMENTS.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    # A non-Markdown file that must never be read.
    (tmp_path / "secrets.txt").write_text("apikey=do-not-read-me", encoding="utf-8")
    return tmp_path


@pytest.fixture
def search(fixture_corpus: Path) -> PolicySearch:
    return PolicySearch(fixture_corpus)


@pytest.fixture
def shipped_corpus() -> PolicySearch:
    return PolicySearch(Settings(_env_file=None).policy_corpus_path)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_the_corpus_loads_documents_with_identifiers(search: PolicySearch) -> None:
    assert search.document_ids == ["POL-TEST-001", "POL-TEST-002"]


def test_a_document_without_an_identifier_is_skipped(search: PolicySearch) -> None:
    """Nothing could cite it, so it is not usable evidence."""
    assert "no-identifier" not in " ".join(search.document_ids)
    assert all("Not A Policy" not in result.title for result in search.search("policy"))


def test_documents_are_split_into_citable_sections(search: PolicySearch) -> None:
    assert search.section_count == 3


def test_a_missing_corpus_directory_is_survivable(tmp_path: Path) -> None:
    empty = PolicySearch(tmp_path / "does-not-exist")

    assert empty.document_ids == []
    assert empty.search("fee") == []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_search_is_case_insensitive(search: PolicySearch) -> None:
    lower = search.search("settlement fee")
    upper = search.search("SETTLEMENT FEE")
    mixed = search.search("SeTtLeMeNt FeE")

    assert [r.document_id for r in lower] == [r.document_id for r in upper]
    assert [r.document_id for r in lower] == [r.document_id for r in mixed]


def test_punctuation_does_not_break_matching(search: PolicySearch) -> None:
    plain = search.search("settlement fees")
    punctuated = search.search("settlement, fees?  -- fees!")

    assert [r.document_id for r in plain] == [r.document_id for r in punctuated]


def test_identifiers_survive_tokenisation() -> None:
    """NORTHSTAR_PAYMENTS must stay one term, not fragment into noise."""
    assert "northstar_payments" in tokenize("NORTHSTAR_PAYMENTS settled")
    assert "amount_mismatch" in tokenize("an AMOUNT_MISMATCH was detected")


def test_stop_words_alone_match_nothing(search: PolicySearch) -> None:
    assert search.search("the and of") == []


def test_an_empty_query_matches_nothing(search: PolicySearch) -> None:
    assert search.search("") == []
    assert search.search("   ") == []


def test_a_query_matching_nothing_returns_an_empty_list(search: PolicySearch) -> None:
    assert search.search("cryptocurrency custody arrangements") == []


def test_results_are_limited(search: PolicySearch) -> None:
    assert len(search.search("fee", limit=1)) == 1


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


def test_every_result_can_be_cited(search: PolicySearch) -> None:
    results = search.search("processing fee deducted")

    assert results
    for result in results:
        assert isinstance(result, PolicyEvidence)
        assert result.document_id.startswith("POL-")
        assert result.title
        assert result.section
        assert result.excerpt


def test_the_result_names_the_section_it_came_from(search: PolicySearch) -> None:
    results = search.search("processing fee deducted")

    assert results[0].document_id == "POL-TEST-001"
    assert results[0].section == "Settlement Fees"


def test_excerpts_are_bounded(shipped_corpus: PolicySearch) -> None:
    for result in shipped_corpus.search("settlement fee reconciliation escalation", limit=5):
        assert len(result.excerpt) <= 405
        assert "\n" not in result.excerpt


def test_results_are_immutable(search: PolicySearch) -> None:
    result = search.search("fee")[0]

    with pytest.raises(Exception):
        result.score = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_query_always_returns_the_same_ordering(search: PolicySearch) -> None:
    runs = [
        [(r.document_id, r.section, r.score) for r in search.search("settlement fee")]
        for _ in range(5)
    ]

    assert all(run == runs[0] for run in runs)


def test_ordering_does_not_depend_on_filesystem_enumeration(fixture_corpus: Path) -> None:
    """Two independently loaded corpora must agree."""
    first = PolicySearch(fixture_corpus).search("settlement fee")
    second = PolicySearch(fixture_corpus).search("settlement fee")

    assert [(r.document_id, r.score) for r in first] == [(r.document_id, r.score) for r in second]


def test_equal_scores_are_broken_by_document_id(tmp_path: Path) -> None:
    """Two identical sections differing only by identifier must order stably."""
    for identifier, name in (("POL-ZZZ-001", "zzz.md"), ("POL-AAA-001", "aaa.md")):
        (tmp_path / name).write_text(
            f"---\ndocument_id: {identifier}\ntitle: T\n---\n\n## Fees\n\nA fee applies.\n",
            encoding="utf-8",
        )

    results = PolicySearch(tmp_path).search("fee applies")

    assert [r.document_id for r in results] == ["POL-AAA-001", "POL-ZZZ-001"]
    assert results[0].score == results[1].score


def test_a_section_covering_more_query_terms_ranks_higher(search: PolicySearch) -> None:
    results = search.search("processing fee deducted settlement funds")

    assert results[0].document_id == "POL-TEST-001"


# ---------------------------------------------------------------------------
# The shipped corpus answers real investigation questions
# ---------------------------------------------------------------------------


def test_a_fee_query_finds_the_fee_schedule(shipped_corpus: PolicySearch) -> None:
    results = shipped_corpus.search("NORTHSTAR_PAYMENTS cross-network settlement processing fee")

    assert results[0].document_id == "POL-FEE-001"
    assert "fee" in results[0].section.lower()


def test_a_settlement_query_finds_settlement_guidance(shipped_corpus: PolicySearch) -> None:
    results = shipped_corpus.search("expected settlement amount differs from settled amount")

    assert "POL-SETTLEMENT-001" in {result.document_id for result in results}


def test_a_duplicate_settlement_query_finds_duplicate_guidance(
    shipped_corpus: PolicySearch,
) -> None:
    results = shipped_corpus.search("duplicate settlement retry correction")

    assert results[0].document_id in {"POL-SETTLEMENT-001", "POL-EXCEPTION-001"}
    assert "duplicate" in results[0].section.lower()


def test_a_currency_query_finds_the_fx_policy(shipped_corpus: PolicySearch) -> None:
    results = shipped_corpus.search("currency mismatch conversion rate settlement currency")

    assert results[0].document_id == "POL-FX-001"


def test_a_missing_settlement_query_finds_relevant_guidance(
    shipped_corpus: PolicySearch,
) -> None:
    results = shipped_corpus.search("missing settlement window overdue not reported")

    assert {"POL-SETTLEMENT-001", "POL-RECON-001"} & {r.document_id for r in results}


def test_an_escalation_query_finds_the_exception_policy(shipped_corpus: PolicySearch) -> None:
    results = shipped_corpus.search("escalate insufficient evidence")

    assert results[0].document_id == "POL-EXCEPTION-001"


def test_the_shipped_corpus_has_the_five_expected_documents(
    shipped_corpus: PolicySearch,
) -> None:
    assert shipped_corpus.document_ids == [
        "POL-EXCEPTION-001",
        "POL-FEE-001",
        "POL-FX-001",
        "POL-RECON-001",
        "POL-SETTLEMENT-001",
    ]


# ---------------------------------------------------------------------------
# The filesystem boundary
# ---------------------------------------------------------------------------


def test_only_the_query_is_part_of_the_public_interface() -> None:
    """No path, glob or filename argument reaches a caller."""
    public = {
        name
        for name in dir(PolicySearch)
        if not name.startswith("_") and callable(getattr(PolicySearch, name))
    }

    assert public == {"search"}


def test_no_file_reading_or_directory_listing_is_exposed() -> None:
    forbidden = ("read", "open", "load", "file", "path", "glob", "list", "dir", "walk")
    public = {name.lower() for name in dir(PolicySearch) if not name.startswith("_")}

    assert not [name for name in public if any(word in name for word in forbidden)]


def test_non_markdown_files_in_the_corpus_are_never_read(search: PolicySearch) -> None:
    """A stray secrets.txt beside the policies must stay unread."""
    assert search.search("apikey") == []
    assert search.search("do-not-read-me") == []


def test_search_cannot_reach_outside_the_corpus_directory(tmp_path: Path) -> None:
    """A file one level up is not searchable, whatever the query says."""
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\ndocument_id: POL-OUTSIDE-001\ntitle: Outside\n---\n\n## Secret\n\nclassified material\n",
        encoding="utf-8",
    )
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "inside.md").write_text(
        "---\ndocument_id: POL-INSIDE-001\ntitle: Inside\n---\n\n## Fees\n\nA fee applies.\n",
        encoding="utf-8",
    )

    searcher = PolicySearch(corpus)

    assert searcher.document_ids == ["POL-INSIDE-001"]
    assert searcher.search("classified material") == []
    assert searcher.search("../outside.md") == []


def test_a_traversal_style_query_is_treated_as_ordinary_text(search: PolicySearch) -> None:
    """The query is search terms, never a path."""
    assert search.search("../../etc/passwd") == []
    assert search.search("/etc/passwd") == []
