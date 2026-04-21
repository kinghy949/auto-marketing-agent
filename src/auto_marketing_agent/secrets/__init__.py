"""Secrets Manager 抽象层。

对外暴露两类对象:

- `SecretsProvider` —— **根**凭证访问者,持有全集 token。平台编排层(Orchestrator、
  启动器)使用此接口。
- `ScopedSecretView` —— 最小权限子集视图,仅允许白名单内的 secret 名称。业务 agent
  (尤其是 Sandbox / Media Buyer)**只能**拿 scoped view,不得直接持有 provider。

P0 阶段内置 env 后端;Vault / AWS Secrets Manager 后端留给 P1+,接口已预留。详细
使用约定见 `docs/scoped-credentials.md`(P0-022)。
"""

from auto_marketing_agent.secrets.audit import AuditEvent, AuditSink, NullAuditSink
from auto_marketing_agent.secrets.env import EnvSecretsProvider
from auto_marketing_agent.secrets.errors import (
    SecretNotFoundError,
    SecretScopeViolationError,
)
from auto_marketing_agent.secrets.protocols import ScopedSecretView, SecretsProvider

__all__ = [
    "AuditEvent",
    "AuditSink",
    "EnvSecretsProvider",
    "NullAuditSink",
    "ScopedSecretView",
    "SecretNotFoundError",
    "SecretScopeViolationError",
    "SecretsProvider",
]
