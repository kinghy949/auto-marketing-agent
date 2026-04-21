"""Secrets 抽象接口。

用 `Protocol` 而非 `ABC` 便于各后端按鸭子类型接入,不强制继承。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class ScopedSecretView(Protocol):
    """最小权限子集视图。业务 agent 只能拿到此接口,不得持有完整 `SecretsProvider`。

    `allowed_names` 公开只读,便于调用方在日志 / trace 中记录自己被授予了哪些 secret。
    """

    allowed_names: frozenset[str]
    view_id: str

    def get(self, name: str) -> str:
        """读取一条 secret。名称不在 `allowed_names` 中必须抛 `SecretScopeViolationError`。"""
        ...


class SecretsProvider(Protocol):
    """凭证访问的根接口。"""

    def get(self, name: str, *, accessor: str) -> str:
        """直接读取一条 secret。`accessor` 用于审计日志。"""
        ...

    def has(self, name: str) -> bool:
        """仅判断 secret 是否存在,不读取值(不触发审计 'granted')。"""
        ...

    def scoped(self, names: Iterable[str], *, view_id: str) -> ScopedSecretView:
        """派生一个最小权限子集视图。

        `view_id` 必须由调用方指定(例:`orchestrator→media_buyer:{handoff_id}`),
        后续审计日志里的 `scoped_view_id` 字段会引用它。
        """
        ...
