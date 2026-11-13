"""Typed telemetry collected while a simulation runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

TeamSeries = dict[str, list[float]]
GoodSeries = dict[str, list[float]]
TeamGoodSeries = dict[str, dict[str, list[float]]]


@dataclass(slots=True)
class DiagnosticsHistory:
    customers: list[int]
    customers_by_segment: dict[str, list[int]]
    units_offered: list[int]
    units_auctioned: list[int]
    stockouts: GoodSeries
    decision_duration_seconds: dict[str, list[float]]
    phase_duration_seconds: dict[str, list[float]]


@dataclass(slots=True)
class SimulationHistory:
    turns: list[int]
    capital: TeamSeries
    revenue: TeamSeries
    auction_spend: TeamSeries
    raw_inventory: TeamSeries
    goods_inventory: TeamSeries
    units_produced: TeamSeries
    units_sold: TeamSeries
    clearing_price: GoodSeries
    lot_quantity: GoodSeries
    market_price: GoodSeries
    market_units: GoodSeries
    units_won: TeamGoodSeries
    listed_price: TeamGoodSeries
    sales_by_good: TeamGoodSeries
    diagnostics: DiagnosticsHistory

    @classmethod
    def create(
        cls,
        team_ids: Iterable[str],
        raw_materials: Iterable[str],
        finished_goods: Iterable[str],
        segments: Iterable[str],
    ) -> SimulationHistory:
        teams = list(team_ids)
        materials = list(raw_materials)
        goods = list(finished_goods)

        def team_series() -> TeamSeries:
            return {team_id: [] for team_id in teams}

        def good_series(names: list[str]) -> GoodSeries:
            return {name: [] for name in names}

        def team_good_series(names: list[str]) -> TeamGoodSeries:
            return {team_id: {name: [] for name in names} for team_id in teams}

        return cls(
            turns=[],
            capital=team_series(),
            revenue=team_series(),
            auction_spend=team_series(),
            raw_inventory=team_series(),
            goods_inventory=team_series(),
            units_produced=team_series(),
            units_sold=team_series(),
            clearing_price=good_series(materials),
            lot_quantity=good_series(materials),
            market_price=good_series(goods),
            market_units=good_series(goods),
            units_won=team_good_series(materials),
            listed_price=team_good_series(goods),
            sales_by_good=team_good_series(goods),
            diagnostics=DiagnosticsHistory(
                customers=[],
                customers_by_segment={name: [] for name in segments},
                units_offered=[],
                units_auctioned=[],
                stockouts=good_series(goods),
                decision_duration_seconds={team_id: [] for team_id in teams},
                phase_duration_seconds={},
            ),
        )
