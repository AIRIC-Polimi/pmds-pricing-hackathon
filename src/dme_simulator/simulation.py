"""Core turn-by-turn DME simulation loop.

Every turn runs exactly three phases:

1. ``auction``    - published raw-material lots are sold to the highest sealed
   bids (pay-your-bid, partial fills, random round-robin on ties).
2. ``production`` - raw materials are converted into finished goods by recipe.
3. ``sales``      - teams list finished goods and spawned customers buy them.

There are no workforce, warehouse or storage costs: a team's only outflow is
what it pays at auction.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from math import ceil, log1p
from time import perf_counter

import numpy as np

from .agents.base import (
    AuctionLot,
    AuctionResult,
    Decision,
    Observation,
    SalesPrice,
)
from .config import EnvironmentConfig
from .domain import TeamState, TurnReport
from .history import SimulationHistory


class DMESimulator:
    def __init__(
        self,
        team_ids: list[str],
        config: EnvironmentConfig,
        profile: bool = False,
        seed: int | None = None,
    ):
        if not team_ids:
            raise ValueError("at least one team is required")
        self.config = config
        self.profile = profile
        self.team_ids = team_ids
        self.current_turn = 0
        random_seed = config.seed if seed is None else seed
        self.random = random.Random(random_seed)
        self.np_random = np.random.default_rng(random_seed)

        initial = config.initial_state
        all_goods = config.goods.raw_materials + config.goods.finished_goods
        self.teams_state = {
            team_id: TeamState(
                capital=initial.starting_capital,
                inventory={
                    good: initial.initial_inventory.get(good, 0) for good in all_goods
                },
            )
            for team_id in team_ids
        }

        self.segment_names = list(config.consumer_market.segments)
        self.history = self._new_history()
        self.supplier_stock = {good: 0 for good in config.goods.raw_materials}
        self.supplier_capacity = {
            good: supplier.stock_capacity or max(1, supplier.production_max * 5)
            for good, supplier in config.suppliers.items()
        }
        self.supply_multiplier = {good: 1.0 for good in config.goods.raw_materials}
        self.low_stock_turns = {good: 0 for good in config.goods.raw_materials}
        self.high_stock_turns = {good: 0 for good in config.goods.raw_materials}
        self.auction_transactions: list[AuctionResult] = []
        self.sales_transactions: list[SalesPrice] = []
        self.last_report: TurnReport | None = None
        self.auction_lots: list[AuctionLot] = self._publish_lots()

    def _new_history(self) -> SimulationHistory:
        return SimulationHistory.create(
            self.team_ids,
            self.config.goods.raw_materials,
            self.config.goods.finished_goods,
            self.segment_names,
        )

    def record_decision_duration(self, team_id: str, duration_seconds: float) -> None:
        if self.profile:
            self.history.diagnostics.decision_duration_seconds[team_id].append(
                duration_seconds
            )

    def record_phase_duration(self, phase: str, duration_seconds: float) -> None:
        if self.profile:
            self.history.diagnostics.phase_duration_seconds.setdefault(
                phase, []
            ).append(duration_seconds)

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def _publish_lots(self) -> list[AuctionLot]:
        """Replenish suppliers and publish their current stock for auction."""
        lots = []
        for good in self.config.goods.raw_materials:
            supplier = self.config.suppliers[good]
            multiplier = self.supply_multiplier[good]
            production_min = round(supplier.production_min * multiplier)
            production_max = round(supplier.production_max * multiplier)
            replenishment = int(
                self.np_random.integers(production_min, production_max + 1)
            )
            self.supplier_stock[good] = min(
                self.supplier_capacity[good],
                self.supplier_stock[good] + replenishment,
            )
            reference_supply = max(1, production_max)
            stock_ratio = self.supplier_stock[good] / reference_supply
            reserve_price = supplier.base_price * max(
                supplier.price_floor_multiplier,
                1.0
                - supplier.price_stock_factor * log1p(stock_ratio),
            )
            lots.append(
                AuctionLot(
                    good=good,
                    quantity=self.supplier_stock[good],
                    reserve_price=reserve_price,
                )
            )
        return lots

    def _adapt_supply(self) -> None:
        """Change replenishment and capacity after persistent stock pressure."""
        for good in self.config.goods.raw_materials:
            policy = self.config.suppliers[good].adaptation
            if not policy.enabled:
                continue
            capacity = self.supplier_capacity[good]
            stock = self.supplier_stock[good]
            fraction = stock / max(1, capacity)
            if fraction <= policy.empty_stock_percentage:
                self.low_stock_turns[good] += 1
                self.high_stock_turns[good] = 0
            elif fraction >= policy.full_stock_percentage:
                self.high_stock_turns[good] += 1
                self.low_stock_turns[good] = 0
            else:
                self.low_stock_turns[good] = 0
                self.high_stock_turns[good] = 0

            if self.low_stock_turns[good] >= policy.low_stock_consecutive_turns:
                self.supply_multiplier[good] *= 1.0 + policy.increase_percentage
                self.supplier_capacity[good] = ceil(
                    capacity * (1.0 + policy.increase_percentage)
                )
                self.low_stock_turns[good] = 0
            elif self.high_stock_turns[good] >= policy.high_stock_consecutive_turns:
                self.supply_multiplier[good] = max(
                    0.01,
                    self.supply_multiplier[good]
                    * (1.0 - policy.decrease_percentage),
                )
                self.supplier_capacity[good] = max(
                    1,
                    ceil(capacity * (1.0 - policy.decrease_percentage)),
                )
                self.high_stock_turns[good] = 0

    def get_team_state(self, team_id: str) -> Observation:
        state = self.teams_state[team_id]
        return Observation(
            turn=self.current_turn + 1,
            capital=state.capital,
            inventory=dict(state.inventory),
            bankrupt=state.bankrupt,
            auction_lots=list(self.auction_lots),
            public_inventories={
                other: dict(other_state.inventory)
                for other, other_state in self.teams_state.items()
            },
            public_capital={
                other: other_state.capital
                for other, other_state in self.teams_state.items()
            },
            auction_transactions=[
                transaction
                for transaction in self.auction_transactions
                if self.config.agents.observe_all_transactions
                or transaction.buyer == team_id
            ],
            sales_transactions=[
                transaction
                for transaction in self.sales_transactions
                if self.config.agents.observe_all_transactions
                or transaction.seller == team_id
            ],
        )

    # ------------------------------------------------------------------
    # turn loop
    # ------------------------------------------------------------------

    def run_turn(self, team_decisions: Mapping[str, Decision]) -> TurnReport:
        turn_started = perf_counter()
        unknown = set(team_decisions) - set(self.team_ids)
        if unknown:
            raise ValueError(f"decisions supplied for unknown teams: {sorted(unknown)}")
        self.current_turn += 1
        report = TurnReport(turn=self.current_turn)

        phase_started = perf_counter()
        self._clear_auction(team_decisions, report)
        self._adapt_supply()
        self.record_phase_duration("auction", perf_counter() - phase_started)

        phase_started = perf_counter()
        for team_id, decision in team_decisions.items():
            if self.teams_state[team_id].bankrupt:
                continue
            self._produce(team_id, decision, report)
        self.record_phase_duration("production", perf_counter() - phase_started)

        phase_started = perf_counter()
        self._clear_sales(team_decisions, report)
        self.record_phase_duration("sales", perf_counter() - phase_started)

        for team_id, state in self.teams_state.items():
            if not state.bankrupt and state.capital < 0:
                self._declare_bankruptcy(team_id)

        phase_started = perf_counter()
        self._record_history(report)
        self.record_phase_duration("history", perf_counter() - phase_started)

        self.last_report = report
        self.auction_lots = self._publish_lots()
        self.record_phase_duration("simulation_turn", perf_counter() - turn_started)
        return report

    # ------------------------------------------------------------------
    # phase 1: auction
    # ------------------------------------------------------------------

    def _clear_auction(
        self, team_decisions: Mapping[str, Decision], report: TurnReport
    ) -> None:
        """Sell each published lot to the highest bidders, pay-your-bid.

        A team's winnings are capped by the capital it still holds, so bidding
        for more than it can afford costs it the surplus units, never its
        solvency.  Bids at the same price share the remaining units through a
        round robin in random team order.
        """
        lots = {lot.good: lot for lot in self.auction_lots}
        report.lots = {lot.good: lot.quantity for lot in self.auction_lots}
        report.units_offered = sum(report.lots.values())
        report.units_won = {team_id: {} for team_id in self.team_ids}
        report.auction_spend = {team_id: 0.0 for team_id in self.team_ids}

        bids_by_good: dict[str, list[tuple[str, int, float]]] = {
            good: [] for good in lots
        }
        for team_id, decision in team_decisions.items():
            if self.teams_state[team_id].bankrupt:
                continue
            for bid in decision.bids:
                good, quantity, price = (
                    bid.good,
                    int(bid.quantity),
                    float(bid.price_per_unit),
                )
                lot = lots.get(good)
                if lot is None or quantity <= 0 or price < lot.reserve_price:
                    continue
                bids_by_good[good].append((team_id, quantity, price))

        remaining_capital = {
            team_id: state.capital for team_id, state in self.teams_state.items()
        }

        # Goods clear in a random order so that no material systematically gets
        # first claim on a team's bidding budget.
        good_order = list(lots)
        self.random.shuffle(good_order)
        for good in good_order:
            lot = lots[good]
            remaining_units = lot.quantity
            winning_prices: list[float] = []

            for price, demands in self._bids_by_price(bids_by_good[good]):
                if remaining_units <= 0:
                    break
                affordable = {}
                for team_id, quantity in demands.items():
                    budget_units = (
                        quantity
                        if price <= 0
                        else int((remaining_capital[team_id] + 1e-9) // price)
                    )
                    units = min(quantity, budget_units)
                    if units > 0:
                        affordable[team_id] = units
                if not affordable:
                    continue

                allocations = self._allocate(affordable, remaining_units)
                for team_id, units in allocations.items():
                    if units <= 0:
                        continue
                    cost = price * units
                    state = self.teams_state[team_id]
                    state.capital -= cost
                    state.inventory[good] += units
                    remaining_capital[team_id] -= cost
                    remaining_units -= units
                    winning_prices.append(price)
                    report.units_won[team_id][good] = (
                        report.units_won[team_id].get(good, 0) + units
                    )
                    report.auction_spend[team_id] += cost
                    self.auction_transactions.append(
                        AuctionResult(
                            good=good,
                            quantity=units,
                            price=price,
                            buyer=team_id,
                        )
                    )

            report.clearing_price[good] = min(winning_prices) if winning_prices else 0.0
            report.highest_bid[good] = max(winning_prices) if winning_prices else 0.0
            self.supplier_stock[good] -= lot.quantity - remaining_units

    @staticmethod
    def _bids_by_price(
        bids: Sequence[tuple[str, int, float]],
    ) -> list[tuple[float, dict[str, int]]]:
        """Group bids into descending price levels, merging repeats per team."""
        levels: dict[float, dict[str, int]] = {}
        for team_id, quantity, price in bids:
            level = levels.setdefault(price, {})
            level[team_id] = level.get(team_id, 0) + quantity
        return [(price, levels[price]) for price in sorted(levels, reverse=True)]

    def _allocate(
        self, demands: Mapping[str, int], available_units: int
    ) -> dict[str, int]:
        """Fill demands in full, or share the shortfall by random round robin."""
        if sum(demands.values()) <= available_units:
            return dict(demands)
        order = list(demands)
        self.random.shuffle(order)
        shuffled = {team_id: demands[team_id] for team_id in order}
        allocations = self._batched_round_robin_allocation(shuffled, available_units)
        # A round robin only hands out complete rounds; give the remainder away
        # one unit at a time, still in the shuffled order.
        leftover = available_units - sum(allocations.values())
        for team_id in order:
            if leftover <= 0:
                break
            if allocations[team_id] < shuffled[team_id]:
                allocations[team_id] += 1
                leftover -= 1
        return allocations

    @staticmethod
    def _batched_round_robin_allocation(
        demands: Mapping[str, int], available_units: int
    ) -> dict[str, int]:
        """Allocate only complete round-robin rounds without unit-by-unit loops."""
        allocations = {team_id: 0 for team_id in demands}
        remaining_demands = {
            team_id: max(0, int(quantity))
            for team_id, quantity in demands.items()
            if int(quantity) > 0
        }
        remaining_stock = max(0, int(available_units))

        while remaining_demands:
            active_count = len(remaining_demands)
            complete_rounds_available = remaining_stock // active_count
            if complete_rounds_available == 0:
                break
            rounds = min(min(remaining_demands.values()), complete_rounds_available)
            for team_id in tuple(remaining_demands):
                allocations[team_id] += rounds
                remaining_demands[team_id] -= rounds
                if remaining_demands[team_id] == 0:
                    del remaining_demands[team_id]
            remaining_stock -= rounds * active_count
        return allocations

    # ------------------------------------------------------------------
    # phase 2: production
    # ------------------------------------------------------------------

    def _produce(self, team_id: str, decision: Decision, report: TurnReport) -> None:
        recipes = self.config.goods.recipes
        state = self.teams_state[team_id]
        capacity = self.config.production.capacity_per_turn
        remaining_capacity = None if capacity is None else capacity
        produced = report.produced.setdefault(team_id, {})

        for order in decision.assembly:
            recipe_name = order.recipe_name
            requested_quantity = int(order.quantity)
            if requested_quantity <= 0 or recipe_name not in recipes:
                continue
            if remaining_capacity is not None and remaining_capacity <= 0:
                break

            recipe = recipes[recipe_name]
            max_by_materials = min(
                state.inventory.get(material, 0) // units
                for material, units in recipe.inputs.items()
            )
            quantity = min(requested_quantity, max_by_materials)
            if remaining_capacity is not None:
                quantity = min(quantity, remaining_capacity)
            if quantity <= 0:
                continue

            for material, units in recipe.inputs.items():
                state.inventory[material] -= units * quantity
            state.inventory[recipe.output_good] += quantity
            produced[recipe.output_good] = (
                produced.get(recipe.output_good, 0) + quantity
            )
            if remaining_capacity is not None:
                remaining_capacity -= quantity

    # ------------------------------------------------------------------
    # phase 3: sales
    # ------------------------------------------------------------------

    def _collect_listings(
        self, team_decisions: Mapping[str, Decision], report: TurnReport
    ) -> dict[str, list[tuple[str, float, int]]]:
        """Reserve listed stock per good as ``(team, price, quantity)`` offers.

        Listings are capped by what the team actually holds; a team may split a
        good across several prices, and each listing is honoured in order until
        its inventory runs out.
        """
        finished = set(self.config.goods.finished_goods)
        offers: dict[str, list[tuple[str, float, int]]] = {
            good: [] for good in self.config.goods.finished_goods
        }
        for team_id, decision in team_decisions.items():
            state = self.teams_state[team_id]
            if state.bankrupt:
                continue
            uncommitted = dict(state.inventory)
            listed = report.listed.setdefault(team_id, {})
            listed_value: dict[str, float] = {}
            for listing in decision.listings:
                good = listing.good
                quantity = int(listing.quantity)
                price = float(listing.price)
                if good not in finished or quantity <= 0 or price <= 0:
                    continue
                quantity = min(quantity, uncommitted.get(good, 0))
                if quantity <= 0:
                    continue
                uncommitted[good] -= quantity
                offers[good].append((team_id, price, quantity))
                listed[good] = listed.get(good, 0) + quantity
                listed_value[good] = listed_value.get(good, 0.0) + price * quantity
            prices = report.listed_price.setdefault(team_id, {})
            for good, value in listed_value.items():
                prices[good] = value / listed[good]
        return offers

    def _draw_customers(self, report: TurnReport) -> dict[str, list[float]]:
        """Spawn this turn's customers and return valuations grouped by good."""
        market = self.config.consumer_market
        count = max(
            0,
            int(
                market.base_customers
                + market.amplitude * np.sin(self.current_turn / market.period_divisor)
            ),
        )
        report.customers = count
        valuations: dict[str, list[float]] = {
            good: [] for good in self.config.goods.finished_goods
        }
        if count == 0:
            return valuations

        names = self.segment_names
        shares = np.asarray(
            [market.segments[name].share for name in names], dtype=float
        )
        shares = shares / shares.sum()
        segment_counts = self.np_random.multinomial(count, shares)

        for name, segment_count in zip(names, segment_counts):
            report.customers_by_segment[name] = int(segment_count)
            if segment_count == 0:
                continue
            segment = market.segments[name]
            goods = list(segment.preferences)
            weights = np.asarray(
                [segment.preferences[good] for good in goods], dtype=float
            )
            weights = weights / weights.sum()
            choices = self.np_random.multinomial(int(segment_count), weights)
            for good, wanted in zip(goods, choices):
                if wanted == 0:
                    continue
                low, high = segment.willingness_to_pay[good]
                valuations[good].extend(
                    self.np_random.uniform(low, high, size=int(wanted)).tolist()
                )
        return valuations

    def _clear_sales(
        self, team_decisions: Mapping[str, Decision], report: TurnReport
    ) -> None:
        """Match customers to listings, cheapest-acceptable weighted by price.

        Demand is grouped by good because listings and eligibility are
        independent across goods.  Within a good, a batch is no larger than the
        smallest listed quantity, so no offer can sell out before every customer
        in that batch has chosen.
        """
        beta = self.config.consumer_market.price_sensitivity_beta
        offers = self._collect_listings(team_decisions, report)
        valuations_by_good = self._draw_customers(report)
        report.units_sold = {team_id: {} for team_id in self.team_ids}
        report.revenue = {team_id: 0.0 for team_id in self.team_ids}

        for good, valuation_list in valuations_by_good.items():
            report.sold_by_good[good] = 0
            report.revenue_by_good[good] = 0.0
            report.stockouts[good] = 0
            if not valuation_list:
                continue
            good_offers = offers[good]
            if not good_offers:
                report.stockouts[good] = len(valuation_list)
                continue

            valuations = np.asarray(valuation_list, dtype=float)
            self.np_random.shuffle(valuations)
            sellers = [team_id for team_id, _, _ in good_offers]
            prices = np.asarray([price for _, price, _ in good_offers], dtype=float)
            quantities = np.asarray(
                [quantity for _, _, quantity in good_offers], dtype=int
            )

            next_customer = 0
            customer_count = valuations.size
            while next_customer < customer_count:
                available = quantities > 0
                if not available.any():
                    report.stockouts[good] += customer_count - next_customer
                    break

                batch_size = min(
                    customer_count - next_customer,
                    int(quantities[available].min()),
                )
                batch = valuations[next_customer : next_customer + batch_size]
                eligible = (batch[:, None] >= prices) & available[None, :]
                has_candidate = eligible.any(axis=1)
                report.stockouts[good] += int((~has_candidate).sum())

                if has_candidate.any():
                    utilities = np.where(
                        eligible[has_candidate], -beta * prices, -np.inf
                    )
                    weights = np.exp(utilities - utilities.max(axis=1, keepdims=True))
                    probabilities = weights / weights.sum(axis=1, keepdims=True)
                    choices = (
                        self.np_random.random((probabilities.shape[0], 1))
                        > probabilities.cumsum(axis=1)
                    ).sum(axis=1)

                    units_by_offer = np.bincount(choices, minlength=len(good_offers))
                    for offer_index, units in enumerate(units_by_offer):
                        if units == 0:
                            continue
                        team_id = sellers[offer_index]
                        price = float(prices[offer_index])
                        revenue = price * int(units)
                        state = self.teams_state[team_id]
                        state.capital += revenue
                        state.inventory[good] -= int(units)
                        quantities[offer_index] -= int(units)
                        report.units_sold[team_id][good] = report.units_sold[
                            team_id
                        ].get(good, 0) + int(units)
                        report.revenue[team_id] += revenue
                        report.sold_by_good[good] += int(units)
                        report.revenue_by_good[good] += revenue
                        self.sales_transactions.append(
                            SalesPrice(
                                good=good,
                                quantity=int(units),
                                price=price,
                                seller=team_id,
                            )
                        )

                next_customer += batch_size

    def _declare_bankruptcy(self, team_id: str) -> None:
        """Liquidate a team once its capital falls below zero."""
        state = self.teams_state[team_id]
        if state.bankrupt:
            return
        state.bankrupt = True
        state.inventory = {good: 0 for good in state.inventory}

    # ------------------------------------------------------------------
    # telemetry
    # ------------------------------------------------------------------

    def _record_history(self, report: TurnReport) -> None:
        history = self.history
        raw_materials = self.config.goods.raw_materials
        finished = self.config.goods.finished_goods

        history.turns.append(self.current_turn)
        for team_id, state in self.teams_state.items():
            history.capital[team_id].append(state.capital)
            history.revenue[team_id].append(report.revenue.get(team_id, 0.0))
            history.auction_spend[team_id].append(
                report.auction_spend.get(team_id, 0.0)
            )
            history.raw_inventory[team_id].append(
                sum(state.inventory.get(good, 0) for good in raw_materials)
            )
            history.goods_inventory[team_id].append(
                sum(state.inventory.get(good, 0) for good in finished)
            )
            history.units_produced[team_id].append(
                sum(report.produced.get(team_id, {}).values())
            )
            history.units_sold[team_id].append(
                sum(report.units_sold.get(team_id, {}).values())
            )
            for good in raw_materials:
                history.units_won[team_id][good].append(
                    report.units_won.get(team_id, {}).get(good, 0)
                )
            for good in finished:
                history.listed_price[team_id][good].append(
                    report.listed_price.get(team_id, {}).get(good, 0.0)
                )
                history.sales_by_good[team_id][good].append(
                    report.units_sold.get(team_id, {}).get(good, 0)
                )
        for good in raw_materials:
            history.clearing_price[good].append(report.clearing_price.get(good, 0.0))
            history.lot_quantity[good].append(report.lots.get(good, 0))
        for good in finished:
            units = report.sold_by_good.get(good, 0)
            history.market_units[good].append(units)
            history.market_price[good].append(
                report.revenue_by_good.get(good, 0.0) / units if units else 0.0
            )

        diagnostics = history.diagnostics
        diagnostics.customers.append(report.customers)
        for name in self.segment_names:
            diagnostics.customers_by_segment[name].append(
                report.customers_by_segment.get(name, 0)
            )
        diagnostics.units_offered.append(report.units_offered)
        diagnostics.units_auctioned.append(
            sum(sum(won.values()) for won in report.units_won.values())
        )
        for good in finished:
            diagnostics.stockouts[good].append(report.stockouts.get(good, 0))
