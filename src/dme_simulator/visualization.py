"""Matplotlib dashboards built from simulation telemetry.

Three figures, each answering one question about a finished run:

* ``overview``  - who won, and where did the money come from?
* ``auction``   - what did materials cost, and who got them?
* ``sales``     - what did each good sell for, and how much demand was missed?
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .history import SimulationHistory
from .simulation import DMESimulator


def render_plots(
    simulator: DMESimulator,
    output_directory: Path,
    prefix: str,
    error_history: SimulationHistory | None = None,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError(
            "plotting is enabled but Matplotlib is not installed; "
            "run `uv sync --extra plots` or set output.render_plots to false"
        ) from error

    output_directory.mkdir(parents=True, exist_ok=True)
    history = simulator.history
    turns = history.turns
    teams = simulator.team_ids
    materials = simulator.config.goods.raw_materials
    goods = simulator.config.goods.finished_goods
    diagnostics = history.diagnostics
    starting_capital = simulator.config.initial_state.starting_capital

    _render_overview(
        plt,
        output_directory / f"{prefix}_dashboard_overview.png",
        history,
        error_history,
        turns,
        teams,
        goods,
        diagnostics,
        starting_capital,
    )
    _render_auction(
        plt,
        output_directory / f"{prefix}_dashboard_auction.png",
        history,
        error_history,
        turns,
        teams,
        materials,
        diagnostics,
        simulator,
    )
    _render_sales(
        plt,
        output_directory / f"{prefix}_dashboard_sales.png",
        history,
        error_history,
        turns,
        teams,
        goods,
        diagnostics,
    )
    print(f"Dashboards saved to {output_directory.resolve()}")


def _render_overview(
    plt,
    path: Path,
    history,
    error_history,
    turns,
    teams,
    goods,
    diagnostics,
    starting_capital: float,
) -> None:
    """Who won, and what drove it."""
    figure = plt.figure(figsize=(15, 11))
    grid = figure.add_gridspec(3, 2, height_ratios=[1.4, 1, 1])

    # Capital is the scoreboard, so it gets the full width and the most space.
    capital_axis = figure.add_subplot(grid[0, :])
    for team in teams:
        _plot_series(
            capital_axis,
            turns,
            history.capital[team],
            team,
            _error(error_history, "capital", team),
            linewidth=2,
        )
    capital_axis.axhline(
        starting_capital, color="grey", linestyle="--", linewidth=1, alpha=0.7
    )
    capital_axis.annotate(
        "starting capital",
        xy=(0.995, starting_capital),
        xycoords=("axes fraction", "data"),
        ha="right",
        va="bottom",
        fontsize=8,
        color="grey",
    )
    _finish_axis(capital_axis, "Capital ($) — the scoreboard")

    profit_axis = figure.add_subplot(grid[1, 0])
    for team in teams:
        profit = np.asarray(history.capital[team]) - starting_capital
        _plot_series(profit_axis, turns, profit, team, None)
    profit_axis.axhline(0, color="grey", linewidth=1, alpha=0.7)
    _finish_axis(profit_axis, "Cumulative profit vs starting capital ($)")

    margin_axis = figure.add_subplot(grid[1, 1])
    for team in teams:
        margin = np.asarray(history.revenue[team]) - np.asarray(
            history.auction_spend[team]
        )
        _plot_series(margin_axis, turns, margin, team, None)
    margin_axis.axhline(0, color="grey", linewidth=1, alpha=0.7)
    _finish_axis(margin_axis, "Turn margin: revenue − auction spend ($)")

    share_axis = figure.add_subplot(grid[2, 0])
    sold = {team: np.cumsum(history.units_sold[team]) for team in teams}
    total = np.maximum(1, sum(sold.values()))
    share_axis.stackplot(
        turns,
        *[sold[team] * 100.0 / total for team in teams],
        labels=teams,
        alpha=0.85,
    )
    share_axis.set_ylim(0, 100)
    _finish_axis(share_axis, "Cumulative market share of units sold (%)")

    demand_axis = figure.add_subplot(grid[2, 1])
    served = sum(np.asarray(history.units_sold[team]) for team in teams)
    lost = sum(np.asarray(diagnostics.stockouts[good]) for good in goods)
    _plot_series(demand_axis, turns, served, "units sold", None, color="seagreen")
    _plot_series(
        demand_axis, turns, lost, "customers unserved", None, color="indianred"
    )
    _plot_series(
        demand_axis,
        turns,
        diagnostics.customers,
        "customers",
        None,
        color="grey",
        linestyle=":",
    )
    _finish_axis(demand_axis, "Demand captured vs demand missed")
    _save(plt, figure, path)


def _render_auction(
    plt,
    path: Path,
    history,
    error_history,
    turns,
    teams,
    materials,
    diagnostics,
    simulator,
) -> None:
    """What materials cost, and who ended up holding them."""
    figure, axes = plt.subplots(2, 2, figsize=(15, 9))

    price_axis = axes[0, 0]
    for material in materials:
        line = _plot_series(
            price_axis,
            turns,
            history.clearing_price[material],
            material,
            _error(error_history, "clearing_price", material),
        )
        # Show the configured base price alongside the dynamically calculated
        # reserve and the price the market actually paid.
        base_price = simulator.config.suppliers[material].base_price
        price_axis.axhline(
            base_price,
            color=line[0].get_color(),
            linestyle=":",
            linewidth=1,
            alpha=0.6,
        )
    _finish_axis(price_axis, "Lowest winning bid ($/unit), dotted = base price")

    supply_axis = axes[0, 1]
    for material in materials:
        _plot_series(
            supply_axis,
            turns,
            history.lot_quantity[material],
            material,
            _error(error_history, "lot_quantity", material),
        )
    _finish_axis(supply_axis, "Units offered per turn")

    fill_axis = axes[1, 0]
    offered = np.asarray(diagnostics.units_offered, dtype=float)
    auctioned = np.asarray(diagnostics.units_auctioned, dtype=float)
    fill_rate = np.divide(
        auctioned * 100.0, offered, out=np.zeros_like(auctioned), where=offered > 0
    )
    _plot_series(fill_axis, turns, fill_rate, "sold", None, color="darkgreen")
    fill_axis.set_ylim(0, 100)
    _finish_axis(fill_axis, "Auction fill rate (%) — unsold supply is opportunity")

    spend_axis = axes[1, 1]
    for team in teams:
        won = sum(
            np.asarray(history.units_won[team][material]) for material in materials
        )
        _plot_series(spend_axis, turns, won, team, None)
    _finish_axis(spend_axis, "Raw units won per turn")
    _save(plt, figure, path)


def _render_sales(
    plt, path: Path, history, error_history, turns, teams, goods, diagnostics
) -> None:
    """What each good sold for, and where demand went unmet."""
    rows = len(goods) + 1
    figure, axes = plt.subplots(rows, 2, figsize=(15, 3.1 * rows), squeeze=False)

    for row, good in enumerate(goods):
        volume_axis = axes[row][0]
        for team in teams:
            _plot_series(
                volume_axis,
                turns,
                history.sales_by_good[team][good],
                team,
                _error(error_history, "sales_by_good", team, good),
            )
        _finish_axis(volume_axis, f"{good}: units sold per team")

        # Listed price is what teams asked; market price is what buyers paid.
        # The market band is drawn first and softly, because a single seller
        # makes the two identical and the asked price must stay readable.
        price_axis = axes[row][1]
        _plot_series(
            price_axis,
            turns,
            _blank_zeros(history.market_price[good]),
            "market average",
            None,
            color="black",
            linewidth=4,
            alpha=0.25,
        )
        for team in teams:
            _plot_series(
                price_axis,
                turns,
                _blank_zeros(history.listed_price[team][good]),
                f"{team} listed",
                None,
                linestyle="--",
                linewidth=1.4,
            )
        _finish_axis(price_axis, f"{good}: listed price vs realized price ($)")

    lost_axis = axes[rows - 1][0]
    for good in goods:
        _plot_series(lost_axis, turns, diagnostics.stockouts[good], good, None)
    _finish_axis(lost_axis, "Customers who found no affordable listing")

    stock_axis = axes[rows - 1][1]
    for team in teams:
        _plot_series(
            stock_axis,
            turns,
            history.goods_inventory[team],
            team,
            _error(error_history, "goods_inventory", team),
        )
    _finish_axis(stock_axis, "Unsold finished goods held")
    _save(plt, figure, path)


def _blank_zeros(values):
    """Hide 'not listed' / 'no sale' zeros so they do not distort a price axis."""
    array = np.asarray(values, dtype=float)
    return np.where(array > 0, array, np.nan)


def _finish_axis(axis, title: str) -> None:
    axis.set_title(title, fontweight="bold")
    axis.set_xlabel("Turn")
    axis.grid(True, alpha=0.3)
    handles, _ = axis.get_legend_handles_labels()
    if handles:
        axis.legend(fontsize=8)


def _error(error_history: SimulationHistory | None, field_name: str, *keys):
    if error_history is None:
        return None
    values = getattr(error_history, field_name)
    for key in keys:
        values = values[key] if isinstance(values, dict) else getattr(values, key)
    return values


def _plot_series(axis, turns, values, label, errors=None, **kwargs):
    line = axis.plot(turns, values, label=label, **kwargs)
    if errors is not None:
        lower = np.asarray(values) - np.asarray(errors)
        upper = np.asarray(values) + np.asarray(errors)
        axis.fill_between(turns, lower, upper, alpha=0.15)
    return line


def _save(plt, figure, path: Path) -> None:
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)
