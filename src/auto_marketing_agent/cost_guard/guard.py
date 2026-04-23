"""Cost Guard L1 + L2 + L3 实现。

职责:
- **L1**(`CostGuard`):每次 LLM 调用前按 (estimated_prompt_tokens,
  estimated_output_tokens) 做硬上限拦截,无状态,同步。
- **L2**(`DailyCostGuard`,P1-002):按 campaign_id + UTC 日窗聚合已放行 token,
  超 `max_tokens_per_campaign_per_day` 即拒绝。状态来源是 Event Store 的
  `cost_guard.authorized` 事件,不另立独立计数器 —— 事件是单一事实来源,重启 /
  多进程都能自恢复,代价只是每次拦截多扫一遍窗口内事件(P1 量级可接受;
  真上量后改 Postgres 索引聚合)。
- **L3**(`PlatformDailyCostGuard`,P1-003):同源聚合,但不按 campaign 过滤,
  把 Orchestrator 阶段(campaign_id=None)也算进去 —— L3 是平台级 daily cap,
  跨 campaign 共享,用来封顶"所有租户 / 所有 campaign 在同一 UTC 日内"的总花费,
  防止单个租户/活动意外消耗整个 API 配额。
- 拒绝统一抛 `CostGuardDenied`,带 `level` / `limit_kind`,便于 coordinator 据此
  决定:L1 允许重规划(压缩 brief);L2/L3 都不允许重规划(日预算是累计量,压
  prompt 救不回来,只能等次日或走 HITL 增额)。

`estimate_prompt_tokens` 启发式估算,避免对 `tiktoken` 强依赖。L2/L3 上线后仍沿用,
因为聚合的也是 L1 时刻的估算值,一致口径不会自我矛盾;真实计数要等 P2 把模型侧
usage 回填到事件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from auto_marketing_agent.events import EventStore


class CostGuardDenied(Exception):
    """Cost Guard 拒绝一次调用时抛出。

    `level` 区分 L1(单次 hard ceiling)/ L2(campaign 单日累计)/ L3(平台单日累计)。
    `limit_kind` 是具体触发项,方便 event 里分类归因。
    """

    def __init__(
        self,
        *,
        level: Literal["L1", "L2", "L3"],
        limit_kind: Literal[
            "prompt_tokens",
            "output_tokens",
            "total_tokens",
            "campaign_daily_tokens",
            "platform_daily_tokens",
        ],
        observed: int,
        limit: int,
        agent_name: str,
    ) -> None:
        self.level = level
        self.limit_kind = limit_kind
        self.observed = observed
        self.limit = limit
        self.agent_name = agent_name
        super().__init__(
            f"CostGuard[{level}] denied {agent_name}: {limit_kind} {observed} > {limit}"
        )


@dataclass(frozen=True, slots=True)
class CostGuardConfig:
    """L1 硬上限。

    默认值偏保守,业务侧按需在 `Settings` 里覆盖。三个字段互相独立:任意一项超限
    即拒绝,不做加权求和(加权逻辑见 L3 平台层 cap)。
    """

    max_prompt_tokens_per_call: int = 8_000
    max_output_tokens_per_call: int = 2_000
    max_total_tokens_per_call: int = 10_000


@dataclass(frozen=True, slots=True)
class CostGuardDecision:
    """Cost Guard 通过时返回的决策记录。

    用在后续 P1-021 Event Store 写入场景:coordinator 把这条 decision 一起落 event,
    重放时能复现"当时允许放行是基于哪条 L1 配额"。L2 不独立返回 decision —— 它
    的"通过"语义就是"没抛 denied",不需要在上游累计多余状态。
    """

    level: Literal["L1"]
    agent_name: str
    estimated_prompt_tokens: int
    estimated_output_tokens: int

    @property
    def estimated_total_tokens(self) -> int:
        return self.estimated_prompt_tokens + self.estimated_output_tokens


@dataclass(frozen=True, slots=True)
class DailyCostConfig:
    """L2 上限。默认 50k token / campaign / 日 —— 按 P1 默认 L1(~10k/call)推,
    允许一天约 5 次完整 Orchestrator→Audience→Creative 链路,留足 HITL 复核空间。
    业务侧通过 Settings 覆盖。
    """

    max_tokens_per_campaign_per_day: int = 50_000


@dataclass(frozen=True, slots=True)
class PlatformDailyCostConfig:
    """L3 平台层上限。默认 500k token / 日,相当于 10 个 campaign 跑满 L2 默认上限,
    再留 1-2 个活动的余量给运维应急。真实部署时必须按订阅的模型定价与预算重校 ——
    这里给的只是 P1 的安全兜底,不是推荐值。
    """

    max_tokens_per_day: int = 500_000


class CostGuard:
    """L1 拦截器。无状态 —— 每次调用只比较入参与 config。

    L2/L3 的累计状态由另外的 guard 对象承接,组合方式为 `CostGuardStack` 依次 authorize。
    这样每一层独立可测,也便于 P1-004 做"L1 放行但 L2 拒绝"的差异化日志。
    """

    def __init__(self, config: CostGuardConfig | None = None) -> None:
        self._config = config or CostGuardConfig()

    @property
    def config(self) -> CostGuardConfig:
        return self._config

    def authorize_call(
        self,
        *,
        agent_name: str,
        estimated_prompt_tokens: int,
        estimated_output_tokens: int,
    ) -> CostGuardDecision:
        """L1 检查。三个上限任一超限即抛 `CostGuardDenied`。

        `agent_name` 只用于日志 / event,不参与判定。入参必须非负,否则上游估算有 bug,
        直接 raise ValueError 让错误显式暴露,不默默放行。
        """
        if estimated_prompt_tokens < 0 or estimated_output_tokens < 0:
            raise ValueError(
                "estimated tokens must be non-negative: "
                f"prompt={estimated_prompt_tokens}, output={estimated_output_tokens}"
            )

        cfg = self._config
        if estimated_prompt_tokens > cfg.max_prompt_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="prompt_tokens",
                observed=estimated_prompt_tokens,
                limit=cfg.max_prompt_tokens_per_call,
                agent_name=agent_name,
            )
        if estimated_output_tokens > cfg.max_output_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="output_tokens",
                observed=estimated_output_tokens,
                limit=cfg.max_output_tokens_per_call,
                agent_name=agent_name,
            )
        total = estimated_prompt_tokens + estimated_output_tokens
        if total > cfg.max_total_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="total_tokens",
                observed=total,
                limit=cfg.max_total_tokens_per_call,
                agent_name=agent_name,
            )

        return CostGuardDecision(
            level="L1",
            agent_name=agent_name,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )


class DailyCostGuard:
    """L2 —— campaign 单日 token 累计拦截(P1-002)。

    状态只读 Event Store:聚合当前 UTC 日内、同 campaign_id 的
    `cost_guard.authorized` 事件,用 payload 里的 `estimated_prompt_tokens +
    estimated_output_tokens` 求和。调用前判断 `used + planned` 是否超过
    `max_tokens_per_campaign_per_day`,超限即抛 `CostGuardDenied(level=L2)`。

    设计取舍:
    - 用"已放行"而非"真实消耗"作为累计口径。P1 阶段没有回填真实 usage 的事件,
      用估算值做出的日预算本质也是估算值,口径一致不会漂。真实 usage 回填排
      P2 —— 到时候聚合器换源,外部接口不变。
    - `campaign_id=None` 不拦 —— Orchestrator 阶段还没 campaign_id,此时只有 L1
      管。把 "没有 campaign 的调用" 也纳入 L2 会把 correlation / campaign 两种
      scope 混在一起,出错时难排查。
    - 不缓存计数。P1 量级每次扫事件在内存里跑 O(events_in_day),Postgres 后端
      接入后改为 SQL `sum()`,cache 留给那时做。
    - 窗口起点用 "今天 UTC 00:00",而非滚动 24 小时。日预算的业务语义天然是
      "按日结算",滚动窗会让运维拿不到"到今天为止花了多少"的直觉读数。
    """

    def __init__(
        self,
        event_store: EventStore,
        config: DailyCostConfig | None = None,
    ) -> None:
        self._event_store = event_store
        self._config = config or DailyCostConfig()

    @property
    def config(self) -> DailyCostConfig:
        return self._config

    def authorize_call(
        self,
        *,
        agent_name: str,
        campaign_id: str | None,
        estimated_prompt_tokens: int,
        estimated_output_tokens: int,
    ) -> None:
        """campaign_id=None 直接放行;否则累加窗口内同 campaign 的已放行 token,
        `used + planned` 超 `max_tokens_per_campaign_per_day` 即 denied。
        """
        if campaign_id is None:
            return
        if estimated_prompt_tokens < 0 or estimated_output_tokens < 0:
            raise ValueError(
                "estimated tokens must be non-negative: "
                f"prompt={estimated_prompt_tokens}, output={estimated_output_tokens}"
            )

        now = datetime.now(timezone.utc)
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        past_events = self._event_store.list_by_time_range(
            start=window_start,
            event_type="cost_guard.authorized",
        )
        used = 0
        for evt in past_events:
            if evt.campaign_id != campaign_id:
                continue
            used += int(evt.payload.get("estimated_prompt_tokens", 0))
            used += int(evt.payload.get("estimated_output_tokens", 0))

        planned = estimated_prompt_tokens + estimated_output_tokens
        if used + planned > self._config.max_tokens_per_campaign_per_day:
            raise CostGuardDenied(
                level="L2",
                limit_kind="campaign_daily_tokens",
                observed=used + planned,
                limit=self._config.max_tokens_per_campaign_per_day,
                agent_name=agent_name,
            )


class PlatformDailyCostGuard:
    """L3 —— 平台单日 token 累计拦截(P1-003)。

    职责与 L2 一致,差别只在"过滤范围":L3 不按 campaign 分,把整个事件流里当日的
    `cost_guard.authorized` 都加起来,Orchestrator 阶段(campaign_id=None)也算进去 ——
    它也要烧 token,必须计入平台总耗。

    设计取舍:
    - 单独 class 而不是往 `DailyCostGuard` 塞 scope 参数。L2 和 L3 的业务语义不同
      (单 campaign 超额 vs. 平台超额),限额配置不同,错误归因不同;合并成一个类
      反而把"按 campaign 聚合"和"全局聚合"两套决策藏在参数后,后续加 tenant 级 cap
      会越塞越乱。
    - 复用 DailyCostGuard 的"读 Event Store、不维护独立计数"策略(参考 L2 docstring)。
    - 拦截顺序在 L2 之后 —— coordinator 先查 campaign 日预算、再查平台预算,这样
      事件日志能先暴露 L2 被打爆的 campaign,再暴露 L3 被打爆的租户,定位更快。
    """

    def __init__(
        self,
        event_store: EventStore,
        config: PlatformDailyCostConfig | None = None,
    ) -> None:
        self._event_store = event_store
        self._config = config or PlatformDailyCostConfig()

    @property
    def config(self) -> PlatformDailyCostConfig:
        return self._config

    def authorize_call(
        self,
        *,
        agent_name: str,
        estimated_prompt_tokens: int,
        estimated_output_tokens: int,
    ) -> None:
        """聚合窗口内所有 `cost_guard.authorized`(不分 campaign)token,
        `used + planned` 超 `max_tokens_per_day` 即 denied。
        """
        if estimated_prompt_tokens < 0 or estimated_output_tokens < 0:
            raise ValueError(
                "estimated tokens must be non-negative: "
                f"prompt={estimated_prompt_tokens}, output={estimated_output_tokens}"
            )

        now = datetime.now(timezone.utc)
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        past_events = self._event_store.list_by_time_range(
            start=window_start,
            event_type="cost_guard.authorized",
        )
        used = 0
        for evt in past_events:
            used += int(evt.payload.get("estimated_prompt_tokens", 0))
            used += int(evt.payload.get("estimated_output_tokens", 0))

        planned = estimated_prompt_tokens + estimated_output_tokens
        if used + planned > self._config.max_tokens_per_day:
            raise CostGuardDenied(
                level="L3",
                limit_kind="platform_daily_tokens",
                observed=used + planned,
                limit=self._config.max_tokens_per_day,
                agent_name=agent_name,
            )


def estimate_prompt_tokens(text: str) -> int:
    """启发式 token 估算。

    规则(故意粗略):每 4 个 ASCII 字符 ≈ 1 token,每个 CJK 字符 ≈ 1 token。这个估算
    在 L1 hard ceiling 场景足够用 —— 上限本身就是保守值,估算偏高会导致提前拒绝,不会
    放行过大的 prompt。真实 tokenizer(tiktoken / 模型侧计数)在 L2 cost 累计处引入,
    因为 L2 涉及钱的计算,粗估会累积漂移。
    """
    if not text:
        return 0

    ascii_chars = 0
    cjk_chars = 0
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            ascii_chars += 1
        elif 0x3000 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
            cjk_chars += 1
        else:
            # 其它(emoji / 拉丁扩展等)按 ASCII 规则估算
            ascii_chars += 1

    return max(1, (ascii_chars + 3) // 4 + cjk_chars)
