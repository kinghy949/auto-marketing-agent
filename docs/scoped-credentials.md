# Scoped Credentials 设计

> 对应任务 `P0-022`。落地代码见 `src/auto_marketing_agent/secrets/`。
>
> 相关架构条款:§3.5 Secrets Manager、§5.1 Sandbox Agent、§8 难点 9
> (凭证泄露面)、CLAUDE.md 已敲定约束"Secrets 必须 scoped"。

## 目标

把"哪个 agent 能读哪些 secret"显式化、可审计,**让凭证不再**以全量 token 的形式在
sandbox / tool 链路里流转。一行话:

> Sandbox Agent 和 Media Buyer Agent 拿到的凭证必须是最小权限子集,不能是 full-access
> token。

## 角色与数据流

```
 ┌────────────────────┐                    ┌──────────────────────┐
 │ 平台启动器 / 运维   │──(注入 env/Vault)──▶│  SecretsProvider     │
 └────────────────────┘                    │  (full root access)   │
                                           └──────────┬────────────┘
                                                      │ .scoped([...], view_id=X)
                                                      ▼
                                           ┌──────────────────────┐
                                           │  ScopedSecretView     │
                                           │  allowed_names={...}  │
                                           └──────────┬────────────┘
                                                      │ handoff / tool param
                                                      ▼
                              Sandbox Agent / Media Buyer Agent / Tool
                              (**仅见** ScopedSecretView,见不到 root)
```

三条核心规则:

1. **根 provider 只存在于编排层**。`Orchestrator` / 启动代码持有 `SecretsProvider`。
   业务 agent 的构造函数不接受 `SecretsProvider`,只接受 `ScopedSecretView`。
2. **Scope 视图一次性,不跨 handoff 复用**。每次 handoff 由 Orchestrator 重新派生
   view,`view_id` 绑定本次 handoff / task 的唯一标识(推荐
   `{source_agent}->{target_agent}:{handoff_id}`)。
3. **审计**:每次 `get()` 都产生一条 `AuditEvent`,包含 `accessor` / `secret_name` /
   `outcome` / `scoped_view_id`。Event Store(P1-020)就位后写入审计表。

## 最小权限清单(建议)

下表是 MVP 起步时各 agent 应当拿到的 secret 白名单的**建议基线**。真实白名单在
Orchestrator 派发 handoff 时按需裁剪(例:只给本 campaign 的 Meta 子账号 token)。

| Agent / Tool                | 允许的 secret 名(基线)                                        | 说明 |
|----------------------------|-----------------------------------------------------------------|------|
| Orchestrator                | —(直接持有 root provider)                                     | 负责派生所有下游 view |
| Audience                    | `cdp_readonly_token`                                            | 只读 CDP |
| Creative                    | `openai_api_key`, `dalle_api_key`, `asset_library_readonly`     | 不应拿平台投放 token |
| Guardrail                   | —(纯规则 + golden case,不接触外部)                            | 无网络访问 |
| Media Buyer (Meta)          | `meta_ads_{campaign_id}_token`                                  | 只给本 campaign 的子账号 |
| Media Buyer (Google)        | `google_ads_{campaign_id}_refresh_token`                        | 同上 |
| Experiment                  | `experiment_platform_api_key`                                   | 读写本 campaign 实验 |
| Attribution                 | `warehouse_readonly_{campaign_id}`, `mmm_sandbox_s3_readwrite`  | 读数仓 + 写 MMM 产物 |
| Sandbox: ffmpeg / PIL       | `asset_library_readonly`, `asset_library_writeonly_campaign_x`  | 只改本 campaign 命名空间 |
| Sandbox: MMM (PyMC / Robyn) | `warehouse_readonly_{campaign_id}`, `mmm_sandbox_s3_readwrite`  | 同 Attribution,隔离到 sandbox |

**反模式(禁止)**:

- `meta_ads_root_token` 下发到 Media Buyer agent —— 一个 campaign 能越权改其他
  campaign。
- OpenAI 生产 key 下发到 sandbox 内运行的代码 —— sandbox 内执行用户生成代码,token
  可能泄露。sandbox 内应使用专门的"sandbox-only" key,并设置 hard rate limit。
- 把 `ScopedSecretView` 的 `allowed_names` 作为可变集合传递 —— 实现已用
  `frozenset`,调用方不得绕过。

## 生命周期与持久化

- `ScopedSecretView` **不持久化**,也不应跨进程序列化。handoff payload 里只带
  `view_id`(纯字符串),目标 agent 启动时由自己所在 runtime 的 Orchestrator 重新
  构造 view。
- OAuth refresh token 的**轮换**由 provider 实现层负责(Vault / AWS Secrets
  Manager 后端都支持),业务 agent 拿到的永远是"新鲜"token;env 后端在 P0 阶段不
  支持轮换,需要外部脚本更新 `.env` 后重启进程(P1 接 Vault 后自动)。

## 后端替换路线

| 阶段 | 后端 | 备注 |
|------|------|------|
| P0 | `EnvSecretsProvider`(读 `os.environ` / 注入 mapping) | 仅本地 / 单租户 MVP |
| P1 | Vault(推荐)或 AWS Secrets Manager | 接 OAuth 轮换 |
| P2 | 同上 + Sandbox 专用只读身份(Kata / gVisor 内) | 凭证不落地 sandbox 文件系统 |

替换后端 = 实现 `SecretsProvider` 协议的新类 + 在启动器里切换。业务代码(所有拿
`ScopedSecretView` 的 agent)**无需修改**。

## 审计事件落地

当前 `NullAuditSink` 丢弃事件。P1-020 Event Store 就位后,新增一个
`EventStoreAuditSink`,把 `AuditEvent` 落库(append-only),合规回放时可按
`accessor` / `scoped_view_id` / `campaign_id` 聚合。

## 测试约束

`tests/secrets/test_env_provider.py` 已覆盖三条关键路径,修改实现时必须保持:

- 拒绝空 scope(避免 null view 退化)
- scope violation 与 not found 产生不同的审计 `outcome`
- `allowed_names` 为 `frozenset`,调用方不可就地扩权

新增后端类时,建议把这组测试抽成 provider-agnostic 的参数化 suite,后端必须通过同
一套契约。
