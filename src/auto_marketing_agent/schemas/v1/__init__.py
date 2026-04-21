"""Schema Registry v1 模型集合。

此版本是 P0 → P1 阶段使用的稳定集合。向后不兼容的字段变更需切到 `v2/`,不要就地
修改已发布的 v1 定义。
"""

from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.attribution import AttributionReport, MetricValue
from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.buy_order import BuyOrder
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan
from auto_marketing_agent.schemas.v1.creative import AssetRights, CreativeAsset, CreativeVariant
from auto_marketing_agent.schemas.v1.experiment import ExperimentArm, ExperimentSpec, StopRule

__all__ = [
    "ApprovalDecision",
    "AssetRights",
    "AttributionReport",
    "AudienceSegment",
    "BuyOrder",
    "CampaignPlan",
    "CreativeAsset",
    "CreativeVariant",
    "ExperimentArm",
    "ExperimentSpec",
    "MetricValue",
    "StopRule",
]
