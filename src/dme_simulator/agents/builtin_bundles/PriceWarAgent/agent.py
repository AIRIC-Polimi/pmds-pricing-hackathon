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


def _buildable(recipe, inventory: dict[str, int]) -> int:
    return min(
        inventory.get(material, 0) // amount
        for material, amount in recipe.inputs.items()
    )


class PriceWarAgent(BaseAgent):
    """Outbid on the cheap goods, then undercut everyone selling them."""

    margin = 0.45
    budget_share = 0.6
    undercut = 0.8
    target_units = 4

    def __init__(self):
        self._last_listed: dict[str, int] = {}
        self._last_listing_prices: dict[str, float] = {}
        self._sales_seen: dict[str, int] = {}

    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        prices = _bid_prices(state, goods_config, self.margin)
        market_prices = {
            transaction.good: transaction.price
            for transaction in state.sales_transactions
        }
        sales_totals: dict[str, int] = {}
        for transaction in state.sales_transactions:
            sales_totals[transaction.good] = (
                sales_totals.get(transaction.good, 0) + transaction.quantity
            )

        # Only chase recipes that still make money at the price it will bid.
        profitable = {
            name: recipe
            for name, recipe in goods_config.recipes.items()
            if (
                recipe.output_good not in market_prices
                or market_prices[recipe.output_good] * self.undercut
                > _recipe_cost(recipe, prices)
            )
        }
        if not profitable:
            profitable = dict(goods_config.recipes)

        wanted = {good: 0 for good in goods_config.raw_materials}
        for recipe in profitable.values():
            for material, amount in recipe.inputs.items():
                wanted[material] += amount * self.target_units

        # Never commit more than a share of capital across the whole auction.
        budget = state.capital * self.budget_share
        planned_cost = sum(
            prices[lot.good] * wanted.get(lot.good, 0) for lot in state.auction_lots
        )
        scale = min(1.0, budget / planned_cost) if planned_cost > budget else 1.0
        bids = [
            Bid(
                lot.good,
                max(
                    0,
                    int(
                        (wanted.get(lot.good, 0) - state.inventory.get(lot.good, 0))
                        * scale
                    ),
                ),
                prices[lot.good],
            )
            for lot in state.auction_lots
        ]

        # Build whatever the inventory allows, cheapest recipe first.
        inventory = dict(state.inventory)
        assembly = []
        ranked = sorted(
            profitable.items(), key=lambda item: _recipe_cost(item[1], prices)
        )
        for name, recipe in ranked:
            quantity = _buildable(recipe, inventory)
            if quantity <= 0:
                continue
            assembly.append(AssemblyOrder(name, quantity))
            for material, amount in recipe.inputs.items():
                inventory[material] -= amount * quantity
            inventory[recipe.output_good] = (
                inventory.get(recipe.output_good, 0) + quantity
            )

        # Price under the going rate; clearing stock matters more than margin.
        # The floor is what the materials cost: undercutting the last market
        # price without one walks the price to zero, since that average is
        # partly this agent's own sales from the turn before.
        costs = {
            recipe.output_good: _recipe_cost(recipe, prices)
            for recipe in goods_config.recipes.values()
        }
        listings = []
        for good in goods_config.finished_goods:
            available = inventory.get(good, 0)
            if available <= 0:
                continue
            market = market_prices.get(good)
            sold_since_last_decision = sales_totals.get(good, 0) - self._sales_seen.get(
                good, 0
            )
            sold_out = (
                self._last_listed.get(good, 0) > 0
                and sold_since_last_decision >= self._last_listed[good]
            )
            if sold_out:
                reference = self._last_listing_prices[good] * 1.05
            else:
                reference = market if market is not None else costs.get(good, 0.0) * 2.0
                reference *= self.undercut
            price = max(costs.get(good, 0.0) * 1.05, reference)
            listings.append(Listing(good, available, price))

        self._last_listed = {listing.good: listing.quantity for listing in listings}
        self._last_listing_prices = {listing.good: listing.price for listing in listings}
        self._sales_seen = sales_totals
        return Decision(bids=bids, assembly=assembly, listings=listings)
