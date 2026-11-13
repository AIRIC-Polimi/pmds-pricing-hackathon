"""Agent extension contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from dme_simulator.config import GoodsConfig


@dataclass(frozen=True, slots=True)
class AuctionLot:
    """One raw material published for sale in this turn's auction."""

    good: str
    quantity: int
    reserve_price: float


@dataclass(frozen=True, slots=True)
class AuctionResult:
    """One completed raw-material auction transaction."""

    good: str
    quantity: int
    price: float
    buyer: str


@dataclass(frozen=True, slots=True)
class SalesPrice:
    """One completed finished-good sales transaction."""

    good: str
    quantity: int
    price: float
    seller: str


@dataclass(frozen=True, slots=True)
class Bid:
    """A sealed bid for `quantity` units of one raw material."""

    good: str
    quantity: int
    price_per_unit: float


@dataclass(frozen=True, slots=True)
class AssemblyOrder:
    """A requested production quantity for one recipe."""

    recipe_name: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Listing:
    """An offer of `quantity` units of one finished good at `price` each."""

    good: str
    quantity: int
    price: float


@dataclass(frozen=True, slots=True)
class Decision:
    """A team's requested actions for one simulation turn."""

    bids: list[Bid] = field(default_factory=list)
    assembly: list[AssemblyOrder] = field(default_factory=list)
    listings: list[Listing] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Observation:
    """Read-only snapshot of the market information available to one team."""

    turn: int
    capital: float
    inventory: dict[str, int]
    bankrupt: bool
    auction_lots: list[AuctionLot]
    public_inventories: dict[str, dict[str, int]]
    public_capital: dict[str, float]
    auction_transactions: list[AuctionResult] = field(default_factory=list)
    sales_transactions: list[SalesPrice] = field(default_factory=list)


class BaseAgent(ABC):
    """Base class implemented by every dynamically loaded market agent."""

    @abstractmethod
    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        """Return this team's decisions for the next simulation turn."""
