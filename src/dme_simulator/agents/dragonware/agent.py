from dme_simulator.agents import (
    AssemblyOrder,
    BaseAgent,
    Bid,
    Decision,
    Listing,
    Observation,
)
from dme_simulator.config import GoodsConfig


class DragonwareAgent(BaseAgent):
    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        return Decision(
            bids=[
                Bid(lot.good, 1, lot.reserve_price * 1.1)
                for lot in state.auction_lots
            ],
            assembly=[
                AssemblyOrder(name, 1)
                for name in goods_config.recipes
            ],
            listings=[
                Listing(good, 1000, 250.0)
                for good in goods_config.finished_goods
            ],
        )
