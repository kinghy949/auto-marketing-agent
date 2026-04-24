"""PostgresHitlQueue 集成测试。

与 `tests/events/test_postgres_store.py` / `tests/dlq/test_postgres_queue.py`
同模式:`AMA_TEST_POSTGRES_DSN` 未设时整模块 skip。测试间用 uuid 前缀做
correlation_id / approval_id 命名空间,不清表。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

pytest.importorskip("psycopg", reason="postgres optional dep 未装,跳过集成测试")

from auto_marketing_agent.hitl import HitlAlreadyResolved, HitlNotFound
from auto_marketing_agent.hitl.postgres_queue import PostgresHitlQueue
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)


@pytest.fixture
def pg_hitl(pg_dsn: str) -> Iterator[PostgresHitlQueue]:
    queue = PostgresHitlQueue.from_dsn(pg_dsn)
    try:
        yield queue
    finally:
        queue.close()


@pytest.fixture
def ns() -> str:
    return f"pg-hitl-{uuid.uuid4()}"


def _variant(ns: str, variant_id: str | None = None) -> CreativeVariant:
    vid = variant_id or f"var:{ns}:main"
    return CreativeVariant(
        correlation_id=f"corr:{ns}",
        variant_id=vid,
        campaign_id=f"cmp:{ns}",
        target_segment_id=f"aud:{ns}:main",
        headline="测试标题",
        body="测试正文",
        call_to_action="立即购买",
        language="en-US",
        assets=[
            CreativeAsset(
                asset_id="asset:x",
                asset_type="text",
                text="slogan",
                rights=AssetRights(licensor="internal", license_id="lic-1"),
            )
        ],
        generated_by="creative-agent/gpt-4.1-mini",
    )


def _approval(
    ns: str,
    decision: str = "needs_hitl",
    *,
    approval_id: str | None = None,
    subject_id: str | None = None,
) -> ApprovalDecision:
    return ApprovalDecision(
        correlation_id=f"corr:{ns}",
        approval_id=approval_id or f"apv:{ns}:main",
        campaign_id=f"cmp:{ns}",
        subject_type="creative_variant",
        subject_id=subject_id or f"var:{ns}:main",
        decision=decision,
        rationale="hitl-stub",
        violations=["cn_ad_law:unapproved_medical_claims"] if decision != "approve" else [],
    )


def test_enqueue_and_get_roundtrip(pg_hitl: PostgresHitlQueue, ns: str) -> None:
    item = pg_hitl.enqueue(
        approval=_approval(ns),
        variant=_variant(ns),
        brand_guardrails=("禁用赌博", "禁用烟草"),
    )
    assert item.item_id == f"hitl:apv:{ns}:main"
    assert item.status == "pending"
    assert item.brand_guardrails == ("禁用赌博", "禁用烟草")
    assert item.correlation_id == f"corr:{ns}"

    fetched = pg_hitl.get(item.item_id)
    assert fetched is not None
    # pydantic 模型 JSONB roundtrip 后内容等价
    assert fetched.approval.approval_id == f"apv:{ns}:main"
    assert fetched.variant.variant_id == f"var:{ns}:main"
    assert fetched.brand_guardrails == ("禁用赌博", "禁用烟草")
    assert fetched.created_at.tzinfo is not None


def test_enqueue_is_idempotent_on_same_approval_id(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    first = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))
    second = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))

    assert first.item_id == second.item_id
    assert first.created_at == second.created_at  # ON CONFLICT 不覆盖 created_at


def test_enqueue_rejects_non_needs_hitl(pg_hitl: PostgresHitlQueue, ns: str) -> None:
    with pytest.raises(ValueError, match="needs_hitl"):
        pg_hitl.enqueue(
            approval=_approval(ns, decision="approve"), variant=_variant(ns)
        )
    with pytest.raises(ValueError, match="needs_hitl"):
        pg_hitl.enqueue(
            approval=_approval(ns, decision="reject"), variant=_variant(ns)
        )


def test_enqueue_rejects_subject_variant_mismatch(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    with pytest.raises(ValueError, match="subject_id"):
        pg_hitl.enqueue(
            approval=_approval(ns, subject_id=f"var:{ns}:other"),
            variant=_variant(ns, variant_id=f"var:{ns}:main"),
        )


def test_list_pending_scoped_to_namespace(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    a = pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:1", subject_id=f"var:{ns}:1"),
        variant=_variant(ns, variant_id=f"var:{ns}:1"),
    )
    b = pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:2", subject_id=f"var:{ns}:2"),
        variant=_variant(ns, variant_id=f"var:{ns}:2"),
    )

    pending = [p for p in pg_hitl.list_pending() if p.correlation_id == f"corr:{ns}"]
    assert [p.item_id for p in pending] == [a.item_id, b.item_id]


def test_list_pending_excludes_resolved(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    item = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))
    pg_hitl.resolve(item.item_id, status="approved", reviewer="alice")

    pending = [p for p in pg_hitl.list_pending() if p.correlation_id == f"corr:{ns}"]
    assert pending == []


def test_resolve_transitions_to_approved(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    item = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))
    resolved = pg_hitl.resolve(
        item.item_id,
        status="approved",
        reviewer="alice",
        note="可接受,语境无误导",
    )
    assert resolved.status == "approved"
    assert resolved.reviewer == "alice"
    assert resolved.reviewer_note == "可接受,语境无误导"
    assert resolved.decided_at is not None
    assert resolved.decided_at.tzinfo is not None


def test_resolve_supports_rejected_and_expired(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    i1 = pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:r", subject_id=f"var:{ns}:r"),
        variant=_variant(ns, variant_id=f"var:{ns}:r"),
    )
    i2 = pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:e", subject_id=f"var:{ns}:e"),
        variant=_variant(ns, variant_id=f"var:{ns}:e"),
    )

    r1 = pg_hitl.resolve(i1.item_id, status="rejected", reviewer="bob")
    r2 = pg_hitl.resolve(i2.item_id, status="expired", reviewer="cron")
    assert r1.status == "rejected"
    assert r2.status == "expired"


def test_resolve_unknown_raises(pg_hitl: PostgresHitlQueue) -> None:
    with pytest.raises(HitlNotFound):
        pg_hitl.resolve("hitl:does-not-exist", status="approved", reviewer="alice")


def test_resolve_already_resolved_raises(
    pg_hitl: PostgresHitlQueue, ns: str
) -> None:
    item = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))
    pg_hitl.resolve(item.item_id, status="approved", reviewer="alice")

    with pytest.raises(HitlAlreadyResolved) as exc:
        pg_hitl.resolve(item.item_id, status="rejected", reviewer="bob")
    assert exc.value.item_id == item.item_id
    assert exc.value.current_status == "approved"


def test_resolve_rejects_non_terminal_status(pg_hitl: PostgresHitlQueue) -> None:
    with pytest.raises(ValueError, match="终态"):
        pg_hitl.resolve("hitl:any", status="pending", reviewer="alice")


def test_resolve_requires_reviewer(pg_hitl: PostgresHitlQueue, ns: str) -> None:
    item = pg_hitl.enqueue(approval=_approval(ns), variant=_variant(ns))
    with pytest.raises(ValueError, match="reviewer"):
        pg_hitl.resolve(item.item_id, status="approved", reviewer="")


def test_len_counts_rows(pg_hitl: PostgresHitlQueue, ns: str) -> None:
    """全表 count(含终态),只验增量严格 +2。"""
    before = len(pg_hitl)
    pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:x", subject_id=f"var:{ns}:x"),
        variant=_variant(ns, variant_id=f"var:{ns}:x"),
    )
    pg_hitl.enqueue(
        approval=_approval(ns, approval_id=f"apv:{ns}:y", subject_id=f"var:{ns}:y"),
        variant=_variant(ns, variant_id=f"var:{ns}:y"),
    )
    assert len(pg_hitl) == before + 2
