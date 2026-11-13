"""Configuration-independent baseline strategies."""

from __future__ import annotations

from dme_simulator.agents.base import (
    AssemblyOrder,
    BaseAgent,
    Bid,
    Decision,
    Listing,
    Observation,
)
from dme_simulator.config import GoodsConfig




class PatientAgent(BaseAgent):
    """Take no actions; useful as a control baseline."""

    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        return Decision()


