"""HITL 内存队列单元测试。

覆盖:
- 入队幂等(同一 approval_id 重复入队返回既有工单)
- 只接受 decision=needs_hitl;approve / reject 拒绝入队
- subject_id / variant_id 不一致时拒绝入队
- list_pending 按 created_at 升序
- resolve:pending → 终态,填充 reviewer / decided_at
- 重复 resolve 抛 HitlAlreadyResolved
- 未知 item_id resolve 抛 HitlNotFound
- reviewer 必填;status 必须是终态
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from auto_marketing_agent.hitl import (
    HitlAlreadyResolved,
    HitlNotFound,
    InMemoryHitlQueue,
)
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)

CORRELATION = "corr:hitl-test"
CAMPAIGN_ID = "cmp:hitl:001"


def _variant(variant_id: str = "var:hitl:main") -> CreativeVariant:
    return CreativeVariant(
        correlation_id=CORRELATION,
        variant_id=variant_id,
        campaign_id=CAMPAIGN_ID,
        target_segment_id="aud:hitl:main",
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
    decision: str = "needs_hitl",
    *,
    approval_id: str = "apv:var:hitl:main:abc123",
    subject_id: str = "var:hitl:main",
) -> ApprovalDecision:
    return ApprovalDecision(
        correlation_id=CORRELATION,
        approval_id=approval_id,
        campaign_id=CAMPAIGN_ID,
        subject_type="creative_variant",
        subject_id=subject_id,
        decision=decision,
        rationale="hitl-stub",
        violations=["cn_ad_law:unapproved_medical_claims"] if decision != "approve" else [],
    )


def test_enqueue_creates_pending_item_with_derived_id() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(
        approval=_approval(),
        variant=_variant(),
        brand_guardrails=("禁用赌博",),
    )

    assert item.item_id == "hitl:apv:var:hitl:main:abc123"
    assert item.status == "pending"
    assert isinstance(item.created_at, datetime)
    assert item.decided_at is None
    assert item.reviewer is None
    assert item.brand_guardrails == ("禁用赌博",)
    assert item.correlation_id == CORRELATION
    assert len(queue) == 1


def test_enqueue_is_idempotent_on_same_approval_id() -> None:
    queue = InMemoryHitlQueue()
    first = queue.enqueue(approval=_approval(), variant=_variant())
    second = queue.enqueue(approval=_approval(), variant=_variant())

    assert first.item_id == second.item_id
    assert first is second  # 返回同一份对象,created_at 不漂移
    assert len(queue) == 1


def test_enqueue_rejects_non_needs_hitl_decisions() -> None:
    queue = InMemoryHitlQueue()
    with pytest.raises(ValueError, match="needs_hitl"):
        queue.enqueue(approval=_approval(decision="approve"), variant=_variant())
    with pytest.raises(ValueError, match="needs_hitl"):
        queue.enqueue(approval=_approval(decision="reject"), variant=_variant())
    assert len(queue) == 0


def test_enqueue_rejects_subject_variant_mismatch() -> None:
    queue = InMemoryHitlQueue()
    with pytest.raises(ValueError, match="subject_id"):
        queue.enqueue(
            approval=_approval(subject_id="var:other"),
            variant=_variant(variant_id="var:hitl:main"),
        )


def test_list_pending_sorted_by_created_at() -> None:
    queue = InMemoryHitlQueue()
    queue.enqueue(
        approval=_approval(approval_id="apv:1", subject_id="var:1"),
        variant=_variant(variant_id="var:1"),
    )
    time.sleep(0.001)  # 保证 created_at 严格递增
    queue.enqueue(
        approval=_approval(approval_id="apv:2", subject_id="var:2"),
        variant=_variant(variant_id="var:2"),
    )

    pending = queue.list_pending()
    assert [i.approval.approval_id for i in pending] == ["apv:1", "apv:2"]


def test_list_pending_excludes_resolved_items() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    queue.resolve(item.item_id, status="approved", reviewer="alice")

    assert queue.list_pending() == []


def test_get_returns_item_or_none() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    assert queue.get(item.item_id) is item
    assert queue.get("hitl:does-not-exist") is None


def test_resolve_transitions_pending_to_approved() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    resolved = queue.resolve(
        item.item_id,
        status="approved",
        reviewer="alice",
        note="可接受,语境无误导",
    )

    assert resolved.status == "approved"
    assert resolved.reviewer == "alice"
    assert resolved.reviewer_note == "可接受,语境无误导"
    assert resolved.decided_at is not None
    # 队列里实际存的是 resolved 版本
    assert queue.get(item.item_id) is resolved


def test_resolve_supports_rejected_and_expired() -> None:
    queue = InMemoryHitlQueue()
    item1 = queue.enqueue(
        approval=_approval(approval_id="apv:r", subject_id="var:r"),
        variant=_variant(variant_id="var:r"),
    )
    item2 = queue.enqueue(
        approval=_approval(approval_id="apv:e", subject_id="var:e"),
        variant=_variant(variant_id="var:e"),
    )

    r1 = queue.resolve(item1.item_id, status="rejected", reviewer="bob")
    r2 = queue.resolve(item2.item_id, status="expired", reviewer="cron")

    assert r1.status == "rejected"
    assert r2.status == "expired"


def test_resolve_unknown_item_raises_not_found() -> None:
    queue = InMemoryHitlQueue()
    with pytest.raises(HitlNotFound):
        queue.resolve("hitl:nope", status="approved", reviewer="alice")


def test_resolve_already_resolved_item_raises() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    queue.resolve(item.item_id, status="approved", reviewer="alice")

    with pytest.raises(HitlAlreadyResolved) as exc:
        queue.resolve(item.item_id, status="rejected", reviewer="bob")
    assert exc.value.item_id == item.item_id
    assert exc.value.current_status == "approved"


def test_resolve_requires_reviewer() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    with pytest.raises(ValueError, match="reviewer"):
        queue.resolve(item.item_id, status="approved", reviewer="")


def test_resolve_rejects_non_terminal_status() -> None:
    queue = InMemoryHitlQueue()
    item = queue.enqueue(approval=_approval(), variant=_variant())
    with pytest.raises(ValueError, match="终态"):
        queue.resolve(item.item_id, status="pending", reviewer="alice")
