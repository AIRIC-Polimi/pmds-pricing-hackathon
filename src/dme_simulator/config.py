"""YAML-backed configuration dataclasses for every environment parameter."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from math import isfinite
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

import yaml

T = TypeVar("T")

#: How a run is displayed while it is in progress.
DASHBOARD_MODES = ("terminal", "window", "none")


@dataclass(frozen=True, slots=True)
class RecipeConfig:
    output_good: str
    inputs: dict[str, int]


@dataclass(frozen=True, slots=True)
class GoodsConfig:
    raw_materials: list[str]
    finished_goods: list[str]
    recipes: dict[str, RecipeConfig]


@dataclass(frozen=True, slots=True)
class SupplierAdaptationConfig:
    """How one supplier adapts to persistent stock pressure."""

    enabled: bool = True
    low_stock_consecutive_turns: int = 3
    high_stock_consecutive_turns: int = 5
    empty_stock_percentage: float = 0.05
    full_stock_percentage: float = 0.95
    increase_percentage: float = 0.10
    decrease_percentage: float = 0.10


@dataclass(frozen=True, slots=True)
class SupplierConfig:
    """The replenishment and base price of one auctioned raw material."""

    production_min: int
    production_max: int
    base_price: float = 0.0
    stock_capacity: int | None = None
    price_stock_factor: float = 0.2
    price_floor_multiplier: float = 0.5
    adaptation: SupplierAdaptationConfig = field(
        default_factory=SupplierAdaptationConfig
    )


@dataclass(frozen=True, slots=True)
class InitialStateConfig:
    starting_capital: float = 12_000.0
    initial_inventory: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProductionConfig:
    capacity_per_turn: int | None = None


@dataclass(frozen=True, slots=True)
class SegmentConfig:
    """One customer type: how common it is, what it wants, what it will pay."""

    share: float
    preferences: dict[str, float]
    willingness_to_pay: dict[str, tuple[float, float]]


@dataclass(frozen=True, slots=True)
class ConsumerMarketConfig:
    base_customers: int
    amplitude: int = 0
    period_divisor: float = 5.0
    price_sensitivity_beta: float = 0.05
    segments: dict[str, SegmentConfig] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_id: str
    count: int = 1
    team_prefix: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentsConfig:
    directory: str = "src/dme_simulator/agents"
    decide_timeout_ms: float = 10.0
    observe_all_transactions: bool = False
    include: list[AgentSpec] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OutputConfig:
    directory: str = "outputs"
    prefix: str = "market"
    render_plots: bool = True
    progress_interval_turns: int = 15
    dashboard: str = "terminal"
    turn_delay_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    turns: int
    seed: int
    initial_state: InitialStateConfig
    suppliers: dict[str, SupplierConfig]
    production: ProductionConfig
    consumer_market: ConsumerMarketConfig
    goods: GoodsConfig
    agents: AgentsConfig
    output: OutputConfig

    def validate(self) -> None:
        _require_int("turns", self.turns, minimum=1)
        _require_int("seed", self.seed)
        raw_materials, finished_goods = _validate_goods(self.goods)
        _validate_initial_state(self.initial_state, raw_materials | finished_goods)
        _validate_suppliers(self.suppliers, raw_materials)
        _validate_production(self.production)
        _validate_consumer_market(self.consumer_market, finished_goods)
        _validate_agents(self.agents)
        _validate_output(self.output)


def _validate_initial_state(config: InitialStateConfig, known_goods: set[str]) -> None:
    _require_number(
        "initial_state.starting_capital", config.starting_capital, minimum=0.0
    )
    if not isinstance(config.initial_inventory, dict):
        raise TypeError("initial_state.initial_inventory must be a mapping")
    for good, quantity in config.initial_inventory.items():
        if good not in known_goods:
            raise ValueError(
                f"initial_state.initial_inventory contains unknown good {good!r}"
            )
        _require_int(f"initial_state.initial_inventory.{good}", quantity, minimum=0)


def _validate_goods(config: GoodsConfig) -> tuple[set[str], set[str]]:
    raw_materials = _validate_good_names("goods.raw_materials", config.raw_materials)
    finished_goods = _validate_good_names("goods.finished_goods", config.finished_goods)
    if not raw_materials:
        raise ValueError("goods.raw_materials must not be empty")
    if not finished_goods:
        raise ValueError("goods.finished_goods must not be empty")
    overlap = raw_materials & finished_goods
    if overlap:
        raise ValueError(f"raw_materials and finished_goods overlap: {sorted(overlap)}")

    if not config.recipes:
        raise ValueError("goods.recipes must not be empty")
    recipe_outputs: set[str] = set()
    for name, recipe in config.recipes.items():
        _require_nonempty_string("recipe name", name)
        _require_nonempty_string(
            f"goods.recipes.{name}.output_good", recipe.output_good
        )
        if recipe.output_good not in finished_goods:
            raise ValueError(f"{name}: unknown output good {recipe.output_good!r}")
        recipe_outputs.add(recipe.output_good)
        if not recipe.inputs:
            raise ValueError(f"{name}: inputs must not be empty")
        invalid_inputs = set(recipe.inputs) - raw_materials
        if invalid_inputs:
            raise ValueError(
                f"{name}: invalid raw-material inputs {sorted(invalid_inputs)}"
            )
        for material, quantity in recipe.inputs.items():
            _require_int(f"{name}.inputs.{material}", quantity, minimum=1)
    missing_recipes = finished_goods - recipe_outputs
    if missing_recipes:
        raise ValueError(f"finished goods without recipes: {sorted(missing_recipes)}")
    return raw_materials, finished_goods


def _validate_suppliers(
    config: dict[str, SupplierConfig], raw_materials: set[str]
) -> None:
    lot_goods = set(config)
    if lot_goods != raw_materials:
        missing = sorted(raw_materials - lot_goods)
        extra = sorted(lot_goods - raw_materials)
        raise ValueError(f"suppliers mismatch; missing={missing}, extra={extra}")
    for good, supplier in config.items():
        if not isinstance(supplier, SupplierConfig):
            raise TypeError(f"suppliers.{good} must be a SupplierConfig")
        minimum = _require_int(
            f"suppliers.{good}.production_min", supplier.production_min, 0
        )
        maximum = _require_int(
            f"suppliers.{good}.production_max", supplier.production_max, 0
        )
        if maximum < minimum:
            raise ValueError(
                f"suppliers.{good}: production_max must not be below production_min"
            )
        _require_number(
            f"suppliers.{good}.base_price", supplier.base_price, 0.0
        )
        if supplier.stock_capacity is not None:
            _require_int(
                f"suppliers.{good}.stock_capacity",
                supplier.stock_capacity,
                minimum=1,
            )
        _require_number(
            f"suppliers.{good}.price_stock_factor",
            supplier.price_stock_factor,
            0.0,
        )
        _require_number(
            f"suppliers.{good}.price_floor_multiplier",
            supplier.price_floor_multiplier,
            minimum=0.0,
            exclusive=True,
        )
        adaptation = supplier.adaptation
        _require_bool(f"suppliers.{good}.adaptation.enabled", adaptation.enabled)
        _require_int(
            f"suppliers.{good}.adaptation.low_stock_consecutive_turns",
            adaptation.low_stock_consecutive_turns,
            minimum=1,
        )
        _require_int(
            f"suppliers.{good}.adaptation.high_stock_consecutive_turns",
            adaptation.high_stock_consecutive_turns,
            minimum=1,
        )
        _require_number(
            f"suppliers.{good}.adaptation.empty_stock_percentage",
            adaptation.empty_stock_percentage,
            minimum=0.0,
        )
        _require_number(
            f"suppliers.{good}.adaptation.full_stock_percentage",
            adaptation.full_stock_percentage,
            minimum=0.0,
        )
        if adaptation.full_stock_percentage < adaptation.empty_stock_percentage:
            raise ValueError(
                f"suppliers.{good}.adaptation.full_stock_percentage must not be "
                "below empty_stock_percentage"
            )
        _require_number(
            f"suppliers.{good}.adaptation.increase_percentage",
            adaptation.increase_percentage,
            minimum=0.0,
        )
        _require_number(
            f"suppliers.{good}.adaptation.decrease_percentage",
            adaptation.decrease_percentage,
            minimum=0.0,
        )


def _validate_production(config: ProductionConfig) -> None:
    if config.capacity_per_turn is not None:
        _require_int("production.capacity_per_turn", config.capacity_per_turn, 1)


def _validate_consumer_market(
    config: ConsumerMarketConfig, finished_goods: set[str]
) -> None:
    _require_int("consumer_market.base_customers", config.base_customers, minimum=0)
    _require_int("consumer_market.amplitude", config.amplitude, minimum=0)
    _require_number(
        "consumer_market.period_divisor",
        config.period_divisor,
        minimum=0.0,
        exclusive=True,
    )
    _require_number(
        "consumer_market.price_sensitivity_beta",
        config.price_sensitivity_beta,
        minimum=0.0,
    )
    if not config.segments:
        raise ValueError("consumer_market.segments must not be empty")

    total_share = 0.0
    covered_goods: set[str] = set()
    for name, segment in config.segments.items():
        _require_nonempty_string("segment name", name)
        total_share += _require_number(
            f"consumer_market.segments.{name}.share",
            segment.share,
            minimum=0.0,
            exclusive=True,
        )
        if not segment.preferences:
            raise ValueError(f"segment {name}: preferences must not be empty")
        unknown = set(segment.preferences) - finished_goods
        if unknown:
            raise ValueError(f"segment {name}: unknown goods {sorted(unknown)}")
        weight_total = 0.0
        for good, weight in segment.preferences.items():
            weight_total += _require_number(
                f"consumer_market.segments.{name}.preferences.{good}",
                weight,
                minimum=0.0,
            )
        if weight_total <= 0.0:
            raise ValueError(f"segment {name}: preference weights must sum above zero")
        covered_goods |= set(segment.preferences)

        missing_wtp = set(segment.preferences) - set(segment.willingness_to_pay)
        if missing_wtp:
            raise ValueError(
                f"segment {name}: missing willingness_to_pay for {sorted(missing_wtp)}"
            )
        for good, bounds in segment.willingness_to_pay.items():
            if good not in finished_goods:
                raise ValueError(
                    f"segment {name}: willingness_to_pay for unknown good {good!r}"
                )
            if not isinstance(bounds, tuple) or len(bounds) != 2:
                raise ValueError(
                    f"segment {name}: willingness_to_pay for {good} needs two values"
                )
            low = _require_number(
                f"consumer_market.segments.{name}.willingness_to_pay.{good}.minimum",
                bounds[0],
                minimum=0.0,
            )
            high = _require_number(
                f"consumer_market.segments.{name}.willingness_to_pay.{good}.maximum",
                bounds[1],
                minimum=0.0,
            )
            if high < low:
                raise ValueError(
                    f"segment {name}: willingness_to_pay for {good} is inverted"
                )
    if abs(total_share - 1.0) > 1e-6:
        raise ValueError("consumer_market segment shares must sum to 1")
    uncovered = finished_goods - covered_goods
    if uncovered:
        raise ValueError(f"finished goods no segment wants: {sorted(uncovered)}")


def _validate_agents(config: AgentsConfig) -> None:
    _require_nonempty_string("agents.directory", config.directory)
    _require_bool("agents.observe_all_transactions", config.observe_all_transactions)
    _require_number(
        "agents.decide_timeout_ms",
        config.decide_timeout_ms,
        minimum=0.0,
        exclusive=True,
    )
    if not isinstance(config.include, list):
        raise TypeError("agents.include must be a list")
    for index, spec in enumerate(config.include):
        if not isinstance(spec, AgentSpec):
            raise TypeError(f"agents.include[{index}] must be an AgentSpec")
        _require_nonempty_string(f"agents.include[{index}].agent_id", spec.agent_id)
        _require_int(f"agents.include[{index}].count", spec.count, minimum=1)
        if spec.team_prefix is not None:
            _require_nonempty_string(
                f"agents.include[{index}].team_prefix", spec.team_prefix
            )
        if not isinstance(spec.kwargs, dict) or not all(
            isinstance(key, str) for key in spec.kwargs
        ):
            raise TypeError(f"agents.include[{index}].kwargs must have string keys")


def _validate_output(config: OutputConfig) -> None:
    _require_nonempty_string("output.directory", config.directory)
    if not isinstance(config.prefix, str):
        raise TypeError("output.prefix must be a string")
    _require_bool("output.render_plots", config.render_plots)
    if config.dashboard not in DASHBOARD_MODES:
        raise ValueError(f"output.dashboard must be one of {sorted(DASHBOARD_MODES)}")
    _require_int(
        "output.progress_interval_turns", config.progress_interval_turns, minimum=0
    )
    _require_number("output.turn_delay_seconds", config.turn_delay_seconds, minimum=0.0)


def _validate_good_names(name: str, values: list[str]) -> set[str]:
    if not isinstance(values, list):
        raise TypeError(f"{name} must be a list")
    for value in values:
        _require_nonempty_string(name, value)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} contains duplicate entries")
    return set(values)


def _require_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")


def _require_int(name: str, value: object, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _require_number(
    name: str,
    value: object,
    minimum: float | None = None,
    exclusive: bool = False,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and (number <= minimum if exclusive else number < minimum):
        comparison = "greater than" if exclusive else "at least"
        raise ValueError(f"{name} must be {comparison} {minimum}")
    return number


def _require_nonempty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _convert(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if is_dataclass(annotation):
        return _from_mapping(annotation, value)
    if origin is list:
        return [_convert(args[0], item) for item in value]
    if origin is dict:
        return {key: _convert(args[1], item) for key, item in value.items()}
    if origin is tuple:
        return tuple(
            _convert(args[min(i, len(args) - 1)], item) for i, item in enumerate(value)
        )
    if origin is not None and type(None) in args:
        if value is None:
            return None
        return _convert(next(arg for arg in args if arg is not type(None)), value)
    return value


def _from_mapping(cls: type[T], data: dict[str, Any]) -> T:
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} must be configured as a mapping")
    hints = get_type_hints(cls)
    valid = {item.name for item in fields(cls)}
    unknown = set(data) - valid
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
    kwargs = {
        item.name: _convert(hints[item.name], data[item.name])
        for item in fields(cls)
        if item.name in data
    }
    return cls(**kwargs)


def load_config(path: str | Path) -> EnvironmentConfig:
    config_path = Path(path).resolve()
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise TypeError(f"{config_path} must contain a YAML mapping")
    config = _from_mapping(EnvironmentConfig, raw)
    config.validate()
    return config
