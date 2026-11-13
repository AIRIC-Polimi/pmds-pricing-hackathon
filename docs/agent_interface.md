# Agent interface

This reference explains the information passed to an agent and the actions
it can return. For the agent class and local bundle layout, see
[README section 4: Creating an agent](../README.md#4-creating-an-agent).
For official uploads and results, see
[Submit through the hackathon portal](../README.md#15-submit-through-the-hackathon-portal).

## 1. Inputs

### 1.1 Observation

`state: Observation` describes what is known at the start of this turn.

| Attribute: type | Meaning |
| --- | --- |
| `turn: int` | Number of the turn about to be played, starting at 1. |
| `capital: float` | Your team's money before this turn's auction. |
| `inventory: dict[str, int]` | Your units of each raw material and finished good, keyed by good name. |
| `bankrupt: bool` | Whether your team has been liquidated. The runner calls `decide` only for solvent teams. |
| `auction_lots: list[AuctionLot]` | Raw materials available for everyone to bid on **this turn**. |
| `public_inventories: dict[str, dict[str, int]]` | Each team ID mapped to that team's inventory, including your team. |
| `public_capital: dict[str, float]` | Each team ID mapped to that team's capital, including your team. |
| `auction_transactions: list[AuctionResult]` | Completed auction purchases from earlier turns that you are allowed to see. |
| `sales_transactions: list[SalesPrice]` | Completed sales from earlier turns that you are allowed to see. |

**Available auction lots.** Each element of `state.auction_lots` is an
`AuctionLot`:

| Attribute: type | Meaning |
| --- | --- |
| `good: str` | Raw-material name, such as `CHIP`. This is the name to use when bidding. |
| `quantity: int` | Units offered to all teams together; they are not reserved for you. |
| `reserve_price: float` | Minimum eligible bid **per unit** for this lot. |

Read every lot and compare it with what you already own:

```python
for lot in state.auction_lots:
    raw_material = lot.good
    units_offered = lot.quantity
    minimum_bid_per_unit = lot.reserve_price
    units_already_owned = state.inventory.get(raw_material, 0)
```

To inspect another team's public inventory and capital, use its team ID as the
key:

```python
for team_id, inventory in state.public_inventories.items():
    team_capital = state.public_capital[team_id]
    units_of_chip = inventory.get("CHIP", 0)
```

**Completed auction transactions.** Each element of
`state.auction_transactions` is an `AuctionResult`:

| Attribute: type | Meaning |
| --- | --- |
| `good: str` | Raw material purchased. |
| `quantity: int` | Units purchased in this transaction. |
| `price: float` | Price paid per unit. |
| `buyer: str` | Team ID of the buyer. |

```python
for auction in state.auction_transactions:
    purchased_good = auction.good
    units_purchased = auction.quantity
    purchase_price_per_unit = auction.price
    buyer_team_id = auction.buyer
```

**Completed sales transactions.** Each element of
`state.sales_transactions` is a `SalesPrice`:

| Attribute: type | Meaning |
| --- | --- |
| `good: str` | Finished good sold. |
| `quantity: int` | Units sold in this transaction. |
| `price: float` | Sale price per unit. |
| `seller: str` | Team ID of the seller. |

```python
for sale in state.sales_transactions:
    sold_good = sale.good
    units_sold = sale.quantity
    sale_price_per_unit = sale.price
    seller_team_id = sale.seller
```

These lists accumulate across earlier turns and include only transactions your
agent is allowed to see. They do not reveal current bids or listings.

### 1.2 GoodsConfig

`goods_config: GoodsConfig` describes the raw materials, finished goods, and
recipes available throughout the run.

| Attribute: type | Meaning |
| --- | --- |
| `raw_materials: list[str]` | Names of materials that can appear in auction lots and recipe inputs. |
| `finished_goods: list[str]` | Names of goods that can be produced and offered for sale. |
| `recipes: dict[str, RecipeConfig]` | Recipe name mapped to the inputs and output for that recipe. |

Each value in `goods_config.recipes` is a `RecipeConfig`:

| Attribute: type | Meaning |
| --- | --- |
| `output_good: str` | Finished good made by one unit of this recipe. |
| `inputs: dict[str, int]` | Raw-material name mapped to units consumed to make one finished unit. |

Access recipe names and the materials each recipe needs like this:

```python
for recipe_name, recipe in goods_config.recipes.items():
    finished_good = recipe.output_good
    for raw_material, units_per_finished_good in recipe.inputs.items():
        units_owned = state.inventory.get(raw_material, 0)
```

The recipe tells you how many units are needed; the lot tells you how many are
offered and at what reserve price. Other teams compete for the same units, so
an offered unit is not necessarily one you will receive.

## 2. Output: Decision

Return a `Decision` containing any combination of these actions. The
[starter agent](../README.md#13-set-up-your-agent-file) shows how to construct all three.

| Attribute: type | Meaning |
| --- | --- |
| `bids: list[Bid]` | Sealed `Bid(good, quantity, price_per_unit)` values for raw materials in this turn's auction lots. `price_per_unit` is your offered price for one unit. |
| `assembly: list[AssemblyOrder]` | `AssemblyOrder(recipe_name, quantity)` values for recipes in `goods_config.recipes`. Orders run in list order; `quantity` is the number of finished units requested. |
| `listings: list[Listing]` | `Listing(good, quantity, price)` values for finished goods in `goods_config.finished_goods`. `price` is the asking price per unit. |

Quantities must be non-negative integers; prices must be finite, non-negative
numbers. A bid may win fewer units than requested, and production and listings
are limited by the inventory actually available when their phases run. An
exception, invalid decision, or timeout produces an empty decision for that
turn. The default `agents.decide_timeout_ms` is 10 ms.
