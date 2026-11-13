"""Shared dashboard definitions and display metrics from completed turns only."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import TurnReport
from .replay import DashboardTimeline

GUIDE_SECTIONS = (
    ("Cash and change", ("Capital is cash available to bid. It excludes the value of inventory. "
     "Change (delta) compares cash with the previous turn; turn 0 is the starting state. "
     "Money is rounded for display.")),
    ("Spend, revenue and cash flow", ("Spent is money paid for materials this turn. Revenue is "
     "money from finished-good sales this turn. Cash flow = revenue minus spent. This is not "
     "accounting profit: materials may be bought and their goods sold in different turns. "
     "The compact TUI's Out/In column shows spent/revenue.")),
    ("Bought, built, sold / listed", ("Bought counts raw-material units won at auction. Built counts "
     "finished units assembled. Sold / listed compares units sold with units actually offered this "
     "turn, including older stock. No listing means sell-through is unavailable, not 0%.")),
    ("Stock and unit share", ("Raw / goods (R/G in the compact TUI) are end-of-turn inventory counts. Stock carries over "
     "without a storage cost. Unit share is a team's fraction of all finished units sold since "
     "turn 1; it measures volume, not revenue or profitability.")),
    ("Auction", ("Won / offered shows material units allocated from the lot. In the window, team-colored bar "
     "segments show allocations; the remaining dark track is unsold supply. Paid shows the "
     "lowest to highest winning unit price. Winners pay their own bids, not a single clearing price.")),
    ("Sales and demand", ("Average price is sales revenue divided by units sold; a dash means no "
     "sale. In the window, the pale bar is served demand and the hatched red part is unserved demand. A customer "
     "may leave because stock was unavailable or every available price exceeded their valuation. "
     "The dashboard cannot distinguish these causes. Bar lengths share a scale within the panel.")),
    ("History charts", ("The window's Cash view shows available capital; cash flow shows revenue minus material "
     "spending each turn; sales shows finished units sold each turn. Team colors match the "
     "leaderboard and auction. Axes show dollars or units and turn numbers; the cash axis may "
     "start above zero to make changes visible.")),
    ("Reading a strategy", ("Cash down with stock up can mean investment, not failure. Unsold "
     "listed goods suggest checking price and competing supply. Lots of unserved customers "
     "suggest checking availability and affordability. These are clues, not automatic diagnoses.")),
    ("History and audience", ("Every panel follows the displayed turn, including cumulative share. "
     "Browsing history never reruns decisions. This is a spectator dashboard: aggregate market "
     "results here may exceed what an agent receives in its Observation.")),
)

WINDOW_CONTROLS = (
    "Windows start paused at turn 0. Restart or Home returns to turn 0 and pauses; "
    "Play then replays saved history before continuing live execution. "
    "Space pause/play; Left/P previous turn; Right/N next turn; Up/Down speed; "
    "C cycle chart; H or F1 guide; Esc close guide (otherwise quit); Q quit. "
    "Ctrl + plus/minus or Ctrl + wheel zooms; Ctrl + 0 resets to 100%. "
    "The header's minus/plus buttons also zoom. Zoom can enlarge the window and is limited "
    "by available display space; resizing smaller reduces zoom if needed. "
    "The footer buttons and chart tabs are clickable. Scroll over a panel to see more rows, "
    "or hover it and use Page Up/Page Down. In the guide, wheel or Page Up/Page Down scrolls. "
    "Closing the guide restores the previous playback state."
)

@dataclass(frozen=True, slots=True)
class TeamActivity:
    """Flow counts and cash movements for one team in one completed turn."""

    bought: int = 0
    built: int = 0
    sold: int = 0
    listed: int = 0
    spent: float = 0.0
    revenue: float = 0.0

    @property
    def cash_flow(self) -> float:
        """Return cash from sales less material spending, without valuing inventory."""
        return self.revenue - self.spent

    @property
    def sell_through(self) -> float | None:
        """Return the share of offered units sold, or None when nothing was listed."""
        return self.sold / self.listed if self.listed else None


def team_activity(report: TurnReport | None, team: str) -> TeamActivity:
    """Read one team's resolved activity, using zeros for the initial frame."""
    if report is None:
        return TeamActivity()
    return TeamActivity(
        bought=sum(report.units_won.get(team, {}).values()),
        built=sum(report.produced.get(team, {}).values()),
        sold=sum(report.units_sold.get(team, {}).values()),
        listed=sum(report.listed.get(team, {}).values()),
        spent=report.auction_spend.get(team, 0.0),
        revenue=report.revenue.get(team, 0.0),
    )


def history_series(timeline: DashboardTimeline, turn: int, metric: str) -> dict[str, list[float]]:
    """Build a chart using only snapshots through the displayed turn."""
    if metric == "Cash":
        return timeline.capital_series(turn)
    return {
        team: [
            activity.cash_flow if metric == "Cash flow" else activity.sold
            for frame in timeline.frames[:turn + 1]
            for activity in (team_activity(frame.report, team),)
        ]
        for team in timeline.metadata.team_ids
    }


def print_guide(console=None) -> None:
    """Print a standalone guide without loading a scenario or running any agents."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = console or Console()
    body = Text()
    for title, explanation in GUIDE_SECTIONS:
        body.append(title + "\n", style="bold")
        body.append(explanation + "\n\n")
    body.append("Window controls\n", style="bold")
    body.append(WINDOW_CONTROLS + "\n\n")
    body.append("Terminal mode\n", style="bold")
    body.append("The TUI refreshes after each turn and has no playback keys. Use --turn-delay "
                "to pace it, or record the run and open it with --replay for window navigation. "
                "Rerun --guide to read this legend. Short terminals show a compact table; "
                "full final standings are printed at the end.")
    console.print(Panel(body, title="Dynamic Market Exchange · Guide / legend"))
