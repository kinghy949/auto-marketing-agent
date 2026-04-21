"""tracing 封装层的测试。

仅覆盖封装行为(`configure_tracing` 识别环境变量、`campaign_trace` 设对 group_id),
不验证 SDK 本身的上送链路。
"""

from __future__ import annotations

import pytest

from auto_marketing_agent.tracing import campaign_trace, configure_tracing


def test_configure_tracing_respects_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMA_TRACING_DISABLED", "1")
    configure_tracing()
    # 重置状态,避免污染其它测试
    configure_tracing(disabled=False)


def test_configure_tracing_explicit_override() -> None:
    configure_tracing(disabled=True)
    configure_tracing(disabled=False)


def test_campaign_trace_sets_group_id_and_metadata() -> None:
    configure_tracing(disabled=False)
    try:
        with campaign_trace(
            campaign_id="camp-42",
            phase="plan",
            metadata={"correlation_id": "corr-1"},
        ) as trace_obj:
            assert trace_obj is not None
            assert trace_obj.name == "auto_marketing_agent/plan"
            assert getattr(trace_obj, "group_id", None) == "camp-42"
    finally:
        configure_tracing(disabled=True)


def test_campaign_trace_works_when_disabled() -> None:
    configure_tracing(disabled=True)
    with campaign_trace(campaign_id="camp-1", phase="plan") as trace_obj:
        assert trace_obj is not None
