"""`EnvSecretsProvider` 与 scoped view 的行为测试。

重点覆盖:
- 读取 / 不存在 / 作用域违规 三条路径都产生正确的审计事件
- scoped view 的白名单强制
- 空白名单拒绝构造(避免退化为 null view)
"""

from __future__ import annotations

from auto_marketing_agent.secrets import (
    AuditEvent,
    AuditSink,
    EnvSecretsProvider,
    SecretNotFoundError,
    SecretScopeViolationError,
)


class _RecordingAuditSink(AuditSink):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _build(
    source: dict[str, str] | None = None,
) -> tuple[EnvSecretsProvider, _RecordingAuditSink]:
    sink = _RecordingAuditSink()
    provider = EnvSecretsProvider(source or {}, audit_sink=sink)
    return provider, sink


# --- 直接访问 (full provider) -------------------------------------------------


def test_get_returns_value_and_audits_granted() -> None:
    provider, sink = _build({"meta_token": "abc"})
    assert provider.get("meta_token", accessor="orchestrator") == "abc"
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.accessor == "orchestrator"
    assert event.secret_name == "meta_token"
    assert event.outcome == "granted"
    assert event.scoped_view_id is None


def test_get_missing_raises_and_audits_not_found() -> None:
    provider, sink = _build()
    try:
        provider.get("missing", accessor="orchestrator")
    except SecretNotFoundError:
        pass
    else:
        raise AssertionError("期望抛 SecretNotFoundError")
    assert sink.events[-1].outcome == "not_found"


def test_has_does_not_emit_audit() -> None:
    provider, sink = _build({"a": "1"})
    assert provider.has("a") is True
    assert provider.has("b") is False
    assert sink.events == []


# --- scoped view ---------------------------------------------------------------


def test_scoped_view_allows_whitelisted_names() -> None:
    provider, sink = _build({"a": "1", "b": "2", "c": "3"})
    view = provider.scoped(["a", "b"], view_id="sandbox:handoff-1")
    assert view.get("a") == "1"
    assert view.get("b") == "2"
    assert all(e.outcome == "granted" for e in sink.events)
    assert all(e.scoped_view_id == "sandbox:handoff-1" for e in sink.events)


def test_scoped_view_blocks_non_whitelisted() -> None:
    provider, sink = _build({"a": "1", "c": "3"})
    view = provider.scoped(["a"], view_id="sandbox:handoff-1")
    try:
        view.get("c")
    except SecretScopeViolationError:
        pass
    else:
        raise AssertionError("期望抛 SecretScopeViolationError")
    assert sink.events[-1].outcome == "scope_violation"
    assert sink.events[-1].accessor == "sandbox:handoff-1"


def test_scoped_view_missing_secret_raises_not_found() -> None:
    provider, _ = _build({"a": "1"})
    view = provider.scoped(["a", "b"], view_id="sandbox:handoff-1")
    try:
        view.get("b")
    except SecretNotFoundError:
        pass
    else:
        raise AssertionError("期望抛 SecretNotFoundError")


def test_empty_scope_rejected() -> None:
    provider, _ = _build({"a": "1"})
    try:
        provider.scoped([], view_id="sandbox:1")
    except ValueError:
        pass
    else:
        raise AssertionError("空 scope 应被拒绝")


def test_allowed_names_is_immutable_frozenset() -> None:
    provider, _ = _build({"a": "1", "b": "2"})
    view = provider.scoped(["a", "b"], view_id="v1")
    assert view.allowed_names == frozenset({"a", "b"})
