"""SchemaEnvelope 基类行为测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from auto_marketing_agent.schemas.base import SchemaEnvelope


class _Probe(SchemaEnvelope):
    schema_name: Literal["__probe__"] = "__probe__"
    schema_version: Literal["v1"] = "v1"


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        _Probe(correlation_id="c1", unexpected="x")  # type: ignore[call-arg]


def test_frozen_after_construction() -> None:
    instance = _Probe(correlation_id="c1")
    with pytest.raises(ValidationError):
        instance.correlation_id = "c2"  # type: ignore[misc]


def test_created_at_defaults_to_utc_now() -> None:
    before = datetime.now(timezone.utc)
    instance = _Probe(correlation_id="c1")
    after = datetime.now(timezone.utc)
    assert before <= instance.created_at <= after
    assert instance.created_at.tzinfo is timezone.utc


def test_correlation_id_required() -> None:
    with pytest.raises(ValidationError):
        _Probe()  # type: ignore[call-arg]


def test_correlation_id_non_empty() -> None:
    with pytest.raises(ValidationError):
        _Probe(correlation_id="")
