from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

import factories as f
from equitydesk.domain.documents import SourceType
from equitydesk.domain.provenance import (
    FutureLeakageError,
    ProvenanceError,
    assert_no_future_documents,
    compute_content_hash,
    filter_documents_as_of,
    is_visible,
    split_as_of,
    verify_content,
)

AS_OF = f.dt(2024, 1, 31, 21)  # 2024-01-31 16:00 New York


def _docs() -> list:
    return [
        f.document(document_id="past", published_at=f.dt(2023, 11, 3)),
        f.document(document_id="exact", published_at=AS_OF),
        f.document(document_id="one_second_later", published_at=AS_OF + timedelta(seconds=1)),
        f.document(document_id="next_quarter", published_at=f.dt(2024, 5, 3)),
    ]


def test_future_documents_are_excluded_inclusive_boundary() -> None:
    view = filter_documents_as_of(_docs(), AS_OF)
    assert [d.document_id for d in view.visible] == ["past", "exact"]
    assert [d.document_id for d in view.excluded_future] == ["one_second_later", "next_quarter"]


def test_assert_no_future_raises_with_offending_ids() -> None:
    with pytest.raises(FutureLeakageError) as exc:
        assert_no_future_documents(_docs(), AS_OF)
    assert exc.value.offending == ("one_second_later", "next_quarter")
    assert_no_future_documents(_docs()[:2], AS_OF)


def test_late_retrieval_does_not_hide_historical_documents() -> None:
    d = f.document(published_at=f.dt(2023, 11, 3), retrieved_at=f.dt(2026, 10, 1))
    assert filter_documents_as_of([d], AS_OF).visible == (d,)


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ProvenanceError):
        is_visible(f.dt(2024, 1, 1), datetime(2024, 1, 1))  # noqa: DTZ001


def test_split_as_of_works_for_any_timestamped_item() -> None:
    facts = [f.fact(), f.fact(fact_id="restated", available_at=f.dt(2024, 8, 2))]
    view = split_as_of(facts, AS_OF, lambda x: x.available_at)
    assert [x.fact_id for x in view.visible] == ["fct_rev_fy23"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source_url": None, "official_id": None}, "source_url or official_id"),
        ({"content_hash": None}, "content_hash"),
        ({"retrieved_at": f.dt(2023, 1, 1)}, "retrieved_at"),
        ({"version": 2}, "supersedes"),
    ],
)
def test_primary_document_provenance_is_required(override: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        f.document(**override)


def test_secondary_sources_may_lack_hash_but_are_low_priority() -> None:
    d = f.document(
        source_type=SourceType.SECONDARY, content_hash=None, source_url=None, official_id=None
    )
    assert not d.is_primary
    assert d.priority > f.document().priority


def test_content_hash_verification() -> None:
    content = b"<html>10-K</html>"
    d = f.document(content_hash=compute_content_hash(content))
    verify_content(d, content)
    with pytest.raises(ProvenanceError, match="mismatch"):
        verify_content(d, content + b" tampered")
    assert compute_content_hash(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
