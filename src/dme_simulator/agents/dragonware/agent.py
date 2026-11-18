"""Plan material purchases and retail offers from the public market snapshot."""

from collections import Counter, defaultdict, deque
from math import exp, sqrt

import numpy as np

from dme_simulator.agents import (
    AssemblyOrder,
    BaseAgent,
    Bid,
    Decision,
    Listing,
    Observation,
)
from dme_simulator.config import GoodsConfig


# Starting demand curves for the published electronics catalog. Each segment
# contributes an expected customer count and a uniform valuation interval.
DEMAND_PRIORS = {
    "SMARTWATCH": ((5.6, 90, 180),),
    "PHONE": ((3.75, 150, 280), (3.75, 250, 420), (0.83, 400, 650)),
    "TABLET": ((1.88, 180, 320), (2.5, 300, 520)),
    "CONSOLE": ((1.25, 350, 600), (1.67, 600, 1000)),
    "LAPTOP": ((1.25, 450, 750), (2.5, 800, 1400)),
}
SINGLE_PRODUCT_PRIOR = ((20, 40, 70), (13, 94, 106), (7, 90, 160))
MAX_STOCK = 160


def _demand(price, segments):
    return sum(
        customers * min(1.0, max(0.0, (high - price) / (high - low)))
        for customers, low, high in segments
    )


def _prices(segments, reference, last, competitor):
    prices = {reference, last, competitor * 0.98}
    for _, low, high in segments:
        prices.update(low + (high - low) * step / 8 for step in range(9))
        prices.add(low * 0.88)
    if last:
        prices.update(last * ratio for ratio in (0.75, 0.9, 1.06, 1.15, 1.3))
    return sorted(price for price in prices if price > 0)


class DragonwareAgent(BaseAgent):
    def __init__(self):
        self._team = None
        self._sales_cursor = 0
        self._auction_cursor = 0
        self._listings = {}
        self._bids = {}
        self._samples = defaultdict(lambda: deque(maxlen=12))
        self._premium = {}
        self._initial_scale = None
        self._last_recipes = []
        self._last_inventory = {}
        self._capacity = None

    def _learn(self, state):
        # All own transactions are present even when rivals' trades are private.
        sales = state.sales_transactions[self._sales_cursor:]
        auctions = state.auction_transactions[self._auction_cursor:]
        self._sales_cursor = len(state.sales_transactions)
        self._auction_cursor = len(state.auction_transactions)

        rival_prices = {}
        rival_bids = {}
        sold = Counter()
        won = Counter()
        candidates = {
            team for team, stock in state.public_inventories.items()
            if stock == state.inventory and state.public_capital.get(team) == state.capital
        }
        if self._team is None and len(candidates) == 1:
            self._team = next(iter(candidates))
        if self._team is None and (
            any(sale.seller in candidates for sale in sales)
            or any(purchase.buyer in candidates for purchase in auctions)
        ):
            return rival_prices, rival_bids

        # If our snapshot is still ambiguous and none of its matching teams
        # traded, we can still learn that our bids received zero fills.
        for sale in sales:
            if sale.seller == self._team:
                sold[sale.good] += sale.quantity
            else:
                rival_prices[sale.good] = min(
                    rival_prices.get(sale.good, sale.price), sale.price
                )
        for purchase in auctions:
            if purchase.buyer == self._team:
                won[purchase.good] += purchase.quantity
            else:
                rival_bids[purchase.good] = max(
                    rival_bids.get(purchase.good, 0.0), purchase.price
                )

        for good, price in self._listings.items():
            remaining = state.inventory.get(good, 0)
            if remaining or sold[good]:
                self._samples[good].append((state.turn, price, sold[good], remaining == 0))

        for good, (quantity, lot_size, ratio) in self._bids.items():
            if won[good] < quantity:
                self._premium[good] = min(4.0, ratio * 1.16 + 0.08)
            elif quantity < lot_size:
                self._premium[good] = max(1.0, ratio - 0.015)
            else:
                self._premium[good] = max(1.07, ratio - 0.008)

        possible = Counter(self._last_inventory)
        possible.update(won)
        feasible = 0
        for recipe, quantity in self._last_recipes:
            made = min(quantity, *(possible[good] // units for good, units in recipe.inputs.items()))
            feasible += made
            for good, units in recipe.inputs.items():
                possible[good] -= made * units
        if self._last_recipes:
            actual = sum(
                state.inventory.get(good, 0) + sold[good] - self._last_inventory.get(good, 0)
                for good in self._listings
            )
            if 0 < actual < feasible:
                self._capacity = actual
        return rival_prices, rival_bids

    def _forecast(self, price, segments, scale, evidence, rival_stock, rival_price):
        prior = _demand(price, segments) * scale
        base = prior
        if rival_stock:
            # Some of the customers eligible for our offer will buy from rivals.
            share = 0.65 if not rival_price else 1 / (1 + exp(
                max(-20, min(20, (rival_price - price) * 0.01))
            ))
            base -= min(base * share, rival_stock * 0.65)

        total, weight = base * 1.8, 1.8
        for asked, adjustment, recency, censored in evidence:
            distance = abs(price - asked) / max(price, asked)
            sample_weight = recency * max(0.0, 1 - 4 * distance)
            estimate = max(0.0, prior + adjustment)
            if censored:
                estimate = max(base, estimate)
            total += sample_weight * estimate
            weight += sample_weight
        return total / weight

    def _retail_curve(self, good, segments, scale, cost, limit, turn, rival_stock, rival_price):
        """For each stock level, choose a price and estimate cash contribution."""
        last = self._listings.get(good, 0.0)
        candidates = _prices(segments, cost * 2.5, last, rival_price)
        evidence = [
            (
                asked,
                sales + (sqrt(sales) * 0.6 if censored else 0.0)
                - _demand(asked, segments) * scale,
                (1.3 if censored else 2.0) * 0.90 ** (turn - seen_turn),
                censored,
            )
            for seen_turn, asked, sales, censored in self._samples[good]
        ]
        demand = np.array([
            self._forecast(price, segments, scale, evidence, rival_stock, rival_price)
            for price in candidates
        ])[:, None]
        stock = np.arange(limit + 1)
        spread = np.sqrt(demand) * 1.25
        low = np.maximum(0.0, demand - spread)
        high = demand + spread
        sales = 0.25 * np.minimum(stock, low) + 0.5 * np.minimum(stock, demand)
        sales += 0.25 * np.minimum(stock, high)
        carry = cost * 0.15
        contribution = sales * (np.array(candidates)[:, None] - carry) + stock * carry
        best = contribution.argmax(axis=0)
        return contribution[best, stock].tolist(), [candidates[index] for index in best]

    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        if state.bankrupt:
            return Decision()
        if state.turn == 1:
            self.__init__()
        rival_prices, rival_bids = self._learn(state)
        lots = {lot.good: lot for lot in state.auction_lots}
        recipes = goods_config.recipes
        inventory = state.inventory
        raw = {good: inventory.get(good, 0) for good in goods_config.raw_materials}
        sizes = {
            name: min(
                (raw[good] + (lots[good].quantity if good in lots else 0)) // amount
                for good, amount in recipe.inputs.items()
            )
            for name, recipe in recipes.items()
        }
        if self._initial_scale is None:
            supply = max(sizes.values(), default=0)
            self._initial_scale = max(0.45, min(6.0, supply / 19))

        bid_prices = {}
        for good, lot in lots.items():
            ratio = self._premium.get(good, 1.12 if lot.quantity < 55 else 1.0)
            price = lot.reserve_price * ratio
            rival = rival_bids.get(good, 0.0)
            if rival and ratio > 1.15:
                price = max(price, min(lot.reserve_price * 4.0, rival * 1.01))
            bid_prices[good] = price

        recipe_costs = {
            name: sum(amount * bid_prices.get(good, 0.0) for good, amount in recipe.inputs.items())
            for name, recipe in recipes.items()
        }
        curves = {}
        quantities = {}
        for good in goods_config.finished_goods:
            owned = inventory.get(good, 0)
            sources = [name for name, recipe in recipes.items() if recipe.output_good == good]
            if not sources and not owned:
                continue
            cost = min((recipe_costs[name] for name in sources), default=0.0)
            segments = DEMAND_PRIORS.get(good)
            scale = self._initial_scale
            if segments is None and len(goods_config.finished_goods) == 1:
                segments, scale = SINGLE_PRODUCT_PRIOR, 1.0
            elif segments is None:
                reference = max(10.0, cost * 4.0)
                segments = ((6, reference * 0.6, reference * 1.6),)
            limit = min(MAX_STOCK, owned + sum(sizes[name] for name in sources))
            if not limit:
                continue
            opponents = max(0, sum(stock.get(good, 0) for stock in state.public_inventories.values()) - owned)
            curves[good] = self._retail_curve(
                good, segments, scale, cost, limit, state.turn, opponents, rival_prices.get(good, 0.0)
            )
            quantities[good] = min(owned, limit)

        budget = state.capital * 0.72
        purchases = Counter()
        steps = []
        # Allocate one finished unit at a time. The same order is used in
        # production, so earlier, more valuable units get first claim on a fill.
        for _ in range(min(MAX_STOCK, self._capacity or MAX_STOCK)):
            best = None
            best_score = 0.0
            for name, recipe in recipes.items():
                good = recipe.output_good
                if good not in curves:
                    continue
                values, _ = curves[good]
                count = quantities[good]
                if count + 1 >= len(values):
                    continue
                needed = {
                    material: max(0, units - raw[material])
                    for material, units in recipe.inputs.items()
                }
                if any(
                    amount and (material not in lots or purchases[material] + amount > lots[material].quantity)
                    for material, amount in needed.items()
                ):
                    continue
                spend = sum(amount * bid_prices[material] for material, amount in needed.items() if amount)
                if spend > budget:
                    continue
                margin = values[count + 1] - values[count] - spend
                # Even paid-for raw inputs have an alternative use in later turns.
                margin -= 0.45 * (recipe_costs[name] - spend)
                score = margin / sqrt(sum(recipe.inputs.values()))
                if score > best_score:
                    best = name, recipe, needed, spend
                    best_score = score
            if best is None:
                break
            name, recipe, needed, spend = best
            budget -= spend
            for material, units in recipe.inputs.items():
                purchases[material] += needed[material]
                raw[material] += needed[material] - units
            quantities[recipe.output_good] += 1
            if steps and steps[-1][0] == name:
                steps[-1] = (name, steps[-1][1] + 1)
            else:
                steps.append((name, 1))

        bids = [Bid(good, quantity, bid_prices[good]) for good, quantity in purchases.items() if quantity]
        listings = []
        for good, quantity in quantities.items():
            if quantity:
                # Include all existing stock even if it exceeds the planning grid.
                sell = max(inventory.get(good, 0), quantity)
                listings.append(Listing(good, sell, curves[good][1][quantity]))

        self._bids = {
            bid.good: (bid.quantity, lots[bid.good].quantity,
                       bid.price_per_unit / lots[bid.good].reserve_price
                       if lots[bid.good].reserve_price else 1.0)
            for bid in bids
        }
        self._listings = {listing.good: listing.price for listing in listings}
        self._last_recipes = [(recipes[name], quantity) for name, quantity in steps]
        self._last_inventory = dict(inventory)
        return Decision(bids, [AssemblyOrder(name, quantity) for name, quantity in steps], listings)
