"""Secrets 相关异常。

单独拆出以便业务代码只捕获这两个具体异常,不误吞 `KeyError` / `PermissionError`。
"""

from __future__ import annotations


class SecretNotFoundError(LookupError):
    """请求的 secret 名在后端不存在。"""


class SecretScopeViolationError(PermissionError):
    """通过 `ScopedSecretView` 访问了不在白名单内的 secret 名。

    业务代码应当让此异常**直接抛出并进 DLQ / 告警**,而不是降级默默返回空串 —— 这是
    凭证泄露面(架构 §8 难点 9)的最后一道屏障。
    """
