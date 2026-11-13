"""Live terminal dashboard rendered while a simulation runs."""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .dashboard import team_activity
from .domain import TurnReport
from .simulation import DMESimulator


class LiveDashboard:
    """Refresh a leaderboard plus one panel per phase, once per turn."""

    def __init__(self, turn_delay: float = 0.0, console: Console | None = None) -> None:
        self.turn_delay = max(0.0, turn_delay)
        self.console = console or Console()
        self._live: Live | None = None
        self._turns = 0
        self._seed = 0
        self._previous_capital: dict[str, float] = {}
        self._total_sold: dict[str, int] = {}

    def start(self, simulator: DMESimulator, turns: int, seed: int) -> None:
        self._turns = turns
        self._seed = seed
        self._previous_capital = {
            team_id: state.capital for team_id, state in simulator.teams_state.items()
        }
        self._total_sold = {team_id: 0 for team_id in simulator.team_ids}
        self._live = Live(
            self._render(simulator, None),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()

    def update(self, simulator: DMESimulator, report: TurnReport) -> bool:
        """Render the turn, hold it for the pacing delay, and continue."""
        for team_id, sold in report.units_sold.items():
            self._total_sold[team_id] = self._total_sold.get(team_id, 0) + sum(
                sold.values()
            )
        if self._live is not None:
            self._live.update(self._render(simulator, report))
        self._previous_capital = {
            team_id: state.capital for team_id, state in simulator.teams_state.items()
        }
        if self.turn_delay > 0:
            time.sleep(self.turn_delay)
        return True

    def stop(self, simulator: DMESimulator) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._print_final(simulator)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def _render(self, simulator: DMESimulator, report: TurnReport | None) -> Group:
        header = Text.assemble(
            ("DME market  ", "bold"),
            (f"turn {simulator.current_turn}/{self._turns}", "cyan"),
            ("   seed ", "dim"),
            (str(self._seed), "dim"),
            ("   customers ", "dim"),
            (str(report.customers if report else 0), "dim"),
        )
        panels = Table.grid(expand=True)
        panels.add_column(ratio=1)
        panels.add_column(ratio=1)
        panels.add_row(self._auction_panel(report), self._sales_panel(report))
        return Group(
            header,
            self._leaderboard(simulator, report),
            panels,
            self._headline(report),
            Text(
                "Guide: Cash excludes stock; Δ cash = revenue minus spent (not profit).\n"
                "Out/In = spent/revenue; R/G = raw/goods. Share: all-time units. --guide",
                style="bright_white",
            ),
        )

    def _leaderboard(self, simulator: DMESimulator, report: TurnReport | None) -> Panel:
        finished = simulator.config.goods.finished_goods
        raw_materials = simulator.config.goods.raw_materials
        table = Table(box=None, expand=True, pad_edge=False)
        compact = self.console.width < 120
        table.add_column("Team", style="bold", min_width=13, no_wrap=True)
        headings = (
            ("Cash", "Δ cash", "Out/In", "Buy/Build", "Sold/List", "R/G", "Share")
            if compact else
            ("Cash", "Δ cash", "Spent", "Revenue", "Bought", "Built", "Sold/List", "Raw", "Goods", "Share")
        )
        for heading in headings:
            table.add_column(heading, justify="right", no_wrap=True)

        total_sold = max(1, sum(self._total_sold.values()))
        ranked = sorted(
            simulator.teams_state.items(),
            key=lambda item: item[1].capital,
            reverse=True,
        )
        # Keep the guide and phase summaries in view; final standings still list every team.
        limit = max(1, self.console.height - 18)
        for team_id, state in ranked[:limit]:
            delta = state.capital - self._previous_capital.get(team_id, state.capital)
            activity = team_activity(report, team_id)
            share = self._total_sold.get(team_id, 0) / total_sold
            raw = sum(state.inventory.get(good, 0) for good in raw_materials)
            goods = sum(state.inventory.get(good, 0) for good in finished)
            row = [
                Text(team_id, style="red" if state.bankrupt else "bold"),
                "BANKRUPT" if state.bankrupt else f"{state.capital:,.0f}",
                _delta(delta),
            ]
            if compact:
                row.extend((f"{activity.spent:,.0f}/{activity.revenue:,.0f}",
                            f"{activity.bought}/{activity.built}",
                            f"{activity.sold}/{activity.listed}", f"{raw}/{goods}"))
            else:
                row.extend((f"{activity.spent:,.0f}", f"{activity.revenue:,.0f}",
                            str(activity.bought), str(activity.built),
                            f"{activity.sold}/{activity.listed}", str(raw), str(goods)))
            table.add_row(*row, f"{share:.0%}")
        if len(ranked) > limit:
            table.caption = f"Showing {limit}/{len(ranked)} teams; full standings at the end."
        return Panel(table, title="Cash & activity · this turn", border_style="cyan")

    def _auction_panel(self, report: TurnReport | None) -> Panel:
        table = Table(box=None, expand=True, pad_edge=False)
        table.add_column("material", style="bold")
        table.add_column("won/offered", justify="right")
        table.add_column("paid", justify="right")
        show_winner = self.console.width >= 110
        if show_winner:
            table.add_column("most units won", justify="left", no_wrap=True)
        if report is not None:
            for good, offered in report.lots.items():
                won = {
                    team_id: units.get(good, 0)
                    for team_id, units in report.units_won.items()
                    if units.get(good, 0) > 0
                }
                sold = sum(won.values())
                top = max(won, key=won.get) if won else "-"
                low = report.clearing_price.get(good, 0.0)
                high = report.highest_bid.get(good, 0.0)
                price = f"{low:,.1f}" if low == high else f"{low:,.1f}-{high:,.1f}"
                row = [
                    good,
                    _fill(sold, offered),
                    price if sold else "-",
                ]
                if show_winner:
                    row.append(f"{top} ({won[top]})" if won else "-")
                table.add_row(*row)
        return Panel(table, title="Auction · won/offered", border_style="yellow")

    def _sales_panel(self, report: TurnReport | None) -> Panel:
        table = Table(box=None, expand=True, pad_edge=False)
        table.add_column("good", style="bold")
        table.add_column("sold", justify="right")
        table.add_column("avg $", justify="right", no_wrap=True)
        table.add_column("unserved", justify="right")
        if report is not None:
            for good, sold in report.sold_by_good.items():
                revenue = report.revenue_by_good.get(good, 0.0)
                missed = report.stockouts.get(good, 0)
                table.add_row(
                    good,
                    str(sold),
                    f"{revenue / sold:,.0f}" if sold else "-",
                    Text(str(missed), style="magenta" if missed else "dim"),
                )
        return Panel(table, title="Sales · this turn", border_style="green")

    def _headline(self, report: TurnReport | None) -> Text:
        if report is None:
            return Text("waiting for the first turn…", style="dim")
        sold = sum(report.sold_by_good.values())
        won = sum(
            sum(units.values()) for units in report.units_won.values()
        )
        return Text(
            f"Market: {sold}/{report.customers} customers served · "
            f"{won}/{report.units_offered} material units won. Unserved = no stock or price too high.",
            style="bright_white",
        )

    def _print_final(self, simulator: DMESimulator) -> None:
        table = Table(title="final standings", box=None)
        table.add_column("#", justify="right")
        table.add_column("team", style="bold")
        table.add_column("capital", justify="right")
        table.add_column("units sold", justify="right")
        ranked = sorted(
            simulator.teams_state.items(),
            key=lambda item: item[1].capital,
            reverse=True,
        )
        for rank, (team_id, state) in enumerate(ranked, start=1):
            table.add_row(
                str(rank),
                team_id,
                "BANKRUPT" if state.bankrupt else f"{state.capital:,.0f}",
                str(self._total_sold.get(team_id, 0)),
            )
        self.console.print(table)


def _delta(value: float) -> Text:
    if abs(value) < 0.5:
        return Text("=", style="dim")
    arrow = "▲" if value > 0 else "▼"
    return Text(f"{arrow}{abs(value):,.0f}", style="green" if value > 0 else "red")


def _fill(sold: int, offered: int) -> Text:
    style = "green" if offered and sold == offered else "yellow"
    return Text(f"{sold}/{offered}", style=style)
