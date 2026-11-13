"""Mutable runtime entities used by the simulation engine."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class TeamState:
    capital: float
    inventory: dict[str, int]
    bankrupt: bool = False


@dataclass(slots=True)
class TurnReport:
    """Per-turn telemetry shared by the history, the plots and the dashboard."""

    turn: int
    lots: dict[str, int] = field(default_factory=dict)
    units_offered: int = 0
    units_won: dict[str, dict[str, int]] = field(default_factory=dict)
    auction_spend: dict[str, float] = field(default_factory=dict)
    clearing_price: dict[str, float] = field(default_factory=dict)
    highest_bid: dict[str, float] = field(default_factory=dict)
    produced: dict[str, dict[str, int]] = field(default_factory=dict)
    listed: dict[str, dict[str, int]] = field(default_factory=dict)
    listed_price: dict[str, dict[str, float]] = field(default_factory=dict)
    units_sold: dict[str, dict[str, int]] = field(default_factory=dict)
    revenue: dict[str, float] = field(default_factory=dict)
    customers: int = 0
    customers_by_segment: dict[str, int] = field(default_factory=dict)
    stockouts: dict[str, int] = field(default_factory=dict)
    sold_by_good: dict[str, int] = field(default_factory=dict)
    revenue_by_good: dict[str, float] = field(default_factory=dict)
