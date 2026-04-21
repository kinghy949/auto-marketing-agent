"""环境变量后端实现。

MVP 用 env 起步是明确决策(见 `docs/tasks.md` P0-020)。生产 Vault / AWS Secrets
Manager 后端接入时,只需再加一个类实现 `SecretsProvider` 协议,业务代码不需要动。

### 命名约定

本后端从 mapping 直接按键名查:`provider.get("meta_ads_access_token")` 读取
`env["meta_ads_access_token"]`。**不做大小写转换 / 前缀注入**,保持无歧义,避免以后
迁到 Vault 时路径规则冲突。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from auto_marketing_agent.secrets.audit import AuditEvent, AuditSink, NullAuditSink
from auto_marketing_agent.secrets.errors import (
    SecretNotFoundError,
    SecretScopeViolationError,
)


class _EnvScopedView:
    """`EnvSecretsProvider.scoped()` 的返回类型。

    不对外暴露构造器,避免绕过 provider 自行构造。
    """

    def __init__(
        self,
        *,
        parent: EnvSecretsProvider,
        allowed: frozenset[str],
        view_id: str,
    ) -> None:
        self._parent = parent
        self.allowed_names = allowed
        self.view_id = view_id

    def get(self, name: str) -> str:
        if name not in self.allowed_names:
            self._parent._audit(
                AuditEvent(
                    accessor=self.view_id,
                    secret_name=name,
                    outcome="scope_violation",
                    scoped_view_id=self.view_id,
                )
            )
            raise SecretScopeViolationError(
                f"scoped view `{self.view_id}` 不允许访问 secret `{name}`"
            )
        return self._parent._read(
            name=name,
            accessor=self.view_id,
            scoped_view_id=self.view_id,
        )


class EnvSecretsProvider:
    """从 `Mapping[str, str]` 读取凭证的 provider。

    默认绑定 `os.environ`。测试 / 多租户场景可传入自定义 mapping 注入隔离。
    """

    def __init__(
        self,
        source: Mapping[str, str] | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._source: Mapping[str, str] = os.environ if source is None else source
        self._audit_sink: AuditSink = audit_sink or NullAuditSink()

    # --- SecretsProvider 协议实现 ---------------------------------------------

    def get(self, name: str, *, accessor: str) -> str:
        return self._read(name=name, accessor=accessor, scoped_view_id=None)

    def has(self, name: str) -> bool:
        return name in self._source

    def scoped(self, names: Iterable[str], *, view_id: str) -> _EnvScopedView:
        allowed = frozenset(names)
        if not allowed:
            raise ValueError("scoped view 至少需要一个 secret 名,空集合等同于 null view")
        return _EnvScopedView(parent=self, allowed=allowed, view_id=view_id)

    # --- 内部 ------------------------------------------------------------------

    def _read(
        self,
        *,
        name: str,
        accessor: str,
        scoped_view_id: str | None,
    ) -> str:
        try:
            value = self._source[name]
        except KeyError as exc:
            self._audit(
                AuditEvent(
                    accessor=accessor,
                    secret_name=name,
                    outcome="not_found",
                    scoped_view_id=scoped_view_id,
                )
            )
            raise SecretNotFoundError(f"secret `{name}` 未在环境中找到") from exc
        self._audit(
            AuditEvent(
                accessor=accessor,
                secret_name=name,
                outcome="granted",
                scoped_view_id=scoped_view_id,
            )
        )
        return value

    def _audit(self, event: AuditEvent) -> None:
        self._audit_sink.emit(event)
