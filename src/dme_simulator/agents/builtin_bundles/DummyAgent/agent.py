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


def _recipe_cost(recipe, unit_costs: dict[str, float]) -> float:
    """Value one unit of a recipe's output at the given material prices."""
    return sum(
        unit_costs.get(material, 0.0) * amount
        for material, amount in recipe.inputs.items()
    )


def _bid_prices(state: Observation, goods_config: GoodsConfig, margin: float):
    """Bid a small markup over each lot's reserve price."""
    return {lot.good: lot.reserve_price * (1.0 + margin) for lot in state.auction_lots}



class DummyAgent(BaseAgent):
    """Buy materials for one of everything and mark up 2x over cost."""

    margin = 0.3

    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        prices = _bid_prices(state, goods_config, self.margin)

        wanted = {good: 0 for good in goods_config.raw_materials}
        for recipe in goods_config.recipes.values():
            for material, amount in recipe.inputs.items():
                wanted[material] += amount
        bids = [
            Bid(
                lot.good,
                max(0, wanted.get(lot.good, 0) - state.inventory.get(lot.good, 0)),
                prices[lot.good],
            )
            for lot in state.auction_lots
        ]

        assembly = [AssemblyOrder(name, 1) for name in goods_config.recipes]

        listings = []
        for recipe in goods_config.recipes.values():
            available = state.inventory.get(recipe.output_good, 0)
            if available > 0:
                listings.append(
                    Listing(
                        recipe.output_good,
                        available,
                        2.0 * _recipe_cost(recipe, prices),
                    )
                )
        return Decision(bids=bids, assembly=assembly, listings=listings)

