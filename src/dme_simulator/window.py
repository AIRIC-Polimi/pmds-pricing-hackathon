"""Live and recorded dashboard rendered in a pygame window.

The window is the interactive way to watch a run: it paces itself so a human
can follow each turn, and it can be paused, stepped and sped up from the
keyboard.  It shows the same telemetry as the terminal dashboard plus a
capital-over-time chart, which is what makes a run readable as a story.
"""

from __future__ import annotations

import math
import os
from time import perf_counter

from .dashboard import GUIDE_SECTIONS, WINDOW_CONTROLS, history_series, team_activity
from .domain import TurnReport
from .replay import DashboardFrame, DashboardTimeline
from .simulation import DMESimulator

BACKGROUND = (18, 18, 20)
PANEL = (27, 27, 30)
PANEL_EDGE = (48, 48, 52)
TEXT = (238, 240, 244)
MUTED = (177, 180, 188)
ACCENT = (94, 182, 255)
POSITIVE = (74, 222, 128)
NEGATIVE = (248, 113, 113)
WARNING = (250, 204, 21)
# Neutral fills for the per-good bars, kept clear of the team palette so a
# demand bar is never mistaken for a team's share.
DEMAND_MET = (148, 174, 199)
DEMAND_LOST = (146, 63, 76)

TEAM_COLORS = [
    (94, 182, 255),
    (250, 204, 21),
    (167, 139, 250),
    (74, 222, 128),
    (244, 114, 182),
    (251, 146, 60),
    (45, 212, 191),
    (232, 121, 249),
]

MINIMUM_DELAY = 0.02
MAXIMUM_DELAY = 8.0
ZOOM_LEVELS = (1.0, 1.25, 1.5, 1.75, 2.0)
GUIDE_LINE_HEIGHT = 23
FONT_STYLES = {
    "title": (24, True, False), "heading": (16, True, False),
    "body": (15, False, False), "small": (14, False, False),
    "number": (18, False, True),
}


class WindowDashboard:
    """A pygame window that browses snapshots and paces a live run."""

    def __init__(
        self, timeline: DashboardTimeline, turn_delay: float = 0.6,
        size: tuple[int, int] = (1360, 820),
    ):
        self.timeline = timeline
        self._cursor = 0
        self.turn_delay = max(0.0, turn_delay)
        self._size = size
        self._screen = None
        self._clock = None
        self._fonts: dict[str, object] = {}
        self._render_fonts: dict[str, object] = {}
        self._zoom = 1.0
        self._zoom_buttons = {}
        self._turns = timeline.metadata.planned_turns
        self._seed = timeline.metadata.seed
        self._paused = True
        self._step_requested = False
        self._quit = False
        self._colors: dict[str, tuple[int, int, int]] = {}
        self._deadline = 0.0
        self._replaying = False
        self._chart_metric = "Cash"
        self._guide_open = False
        self._guide_was_paused = False
        self._guide_scroll = 0
        self._guide_max_scroll = 0
        self._guide_content_height = 0
        self._guide_close = None
        self._buttons: dict[int, object] = {}
        self._chart_buttons: dict[str, object] = {}
        self._panel_bounds: dict[str, object] = {}
        self._scroll = {"teams": 0, "auction": 0, "sales": 0}
        self._row_limits = {"teams": (0, 1), "auction": (0, 1), "sales": (0, 1)}
        # A headless driver has nobody to press a key, so never block on one.
        self._hold_on_finish = os.environ.get("SDL_VIDEODRIVER") != "dummy"
        self._finished = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, simulator: DMESimulator, turns: int, seed: int) -> bool:
        """Wait at turn 0 for playback or a step; return False if the viewer quits."""
        self._open()
        if self._hold_on_finish:
            self._wait()
        else:
            # Automated dummy-driver runs have no viewer to release the initial pause.
            self._paused = False
        return not self._quit

    def _open(self) -> None:
        """Initialize the pygame window, fonts, and team colors, then draw the selected frame."""
        import pygame

        pygame.init()
        pygame.display.set_caption("Dynamic Market Exchange")
        self._screen = pygame.display.set_mode(self._size, pygame.RESIZABLE)
        self._clock = pygame.time.Clock()
        self._fonts = {
            name: _font(pygame, size, bold=bold, mono=mono)
            for name, (size, bold, mono) in FONT_STYLES.items()
        }
        self._refresh_render_fonts(pygame)
        self._colors = {
            team_id: TEAM_COLORS[index % len(TEAM_COLORS)]
            for index, team_id in enumerate(self.timeline.metadata.team_ids)
        }
        self._draw()

    def update(self, simulator: DMESimulator, report: TurnReport) -> bool:
        """Show the turn, then hold the frame for the configured delay."""
        self._cursor = len(self.timeline.frames) - 1
        # The runner finalizes recordings before the final window hold.
        if self.timeline.end_reason is None:
            self._wait()
        else:
            self._draw()
        return not self._quit

    def stop(self, simulator: DMESimulator) -> None:
        import pygame

        if self._screen is None:
            return
        self._finished = True
        self._paused = True
        try:
            self._draw()
            if self._hold_on_finish and not self._quit:
                self._wait()
        finally:
            pygame.quit()
            self._screen = None

    def replay(self) -> None:
        """Browse an already loaded recording without creating a simulator."""
        self._replaying = True
        self._finished = True
        self._paused = True
        try:
            self._open()
            if self._hold_on_finish:
                self._wait()
        finally:
            import pygame

            pygame.quit()
            self._screen = None

    @property
    def _frame(self) -> DashboardFrame:
        """Return the snapshot selected by the viewing cursor."""
        return self.timeline.frames[self._cursor]

    @property
    def _report(self) -> TurnReport | None:
        """Return the selected turn's report, or None for the initial state."""
        return self._frame.report

    @property
    def _total_sold(self) -> dict[str, int]:
        """Return team sales accumulated only through the selected turn."""
        return self._frame.total_sold

    # ------------------------------------------------------------------
    # pacing and input
    # ------------------------------------------------------------------

    def _wait(self) -> None:
        """Render at ~60 fps until the turn's time is up or the viewer steps."""
        self._reset_deadline()
        while True:
            self._pump_events()
            self._draw()
            if self._quit:
                return
            if self._step_requested:
                self._step_requested = False
                return
            if not self._paused and perf_counter() >= self._deadline:
                self._advance()
            self._clock.tick(60)

    def _reset_deadline(self) -> None:
        """Allow a full turn delay after navigation, resuming playback, or changing speed."""
        self._deadline = perf_counter() + self.turn_delay

    def _advance(self) -> None:
        """Move to the next saved frame or request one new live turn at the frontier."""
        if self._cursor < len(self.timeline.frames) - 1:
            self._cursor += 1
        elif not self._finished:
            # Only this signal releases the waiting runner to execute another turn.
            self._step_requested = True
        if self._finished and self._cursor == len(self.timeline.frames) - 1:
            self._paused = True
        self._reset_deadline()

    def _pump_events(self) -> None:
        import pygame

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._quit = True
            elif event.type == pygame.VIDEORESIZE:
                self._size = (max(900, event.w), max(600, event.h))
                self._screen = pygame.display.set_mode(self._size, pygame.RESIZABLE)
                self._zoom = min(self._zoom, self._size[0] / 900, self._size[1] / 600)
                self._refresh_render_fonts(pygame)
            elif event.type == pygame.KEYDOWN:
                self._handle_key(pygame, event.key, getattr(event, "mod", 0))
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_click(pygame, self._logical_position(event.pos))
            elif event.type == pygame.MOUSEWHEEL and event.y:
                if pygame.key.get_mods() & pygame.KMOD_CTRL:
                    self._change_zoom(pygame, 1 if event.y > 0 else -1)
                else:
                    self._scroll_panel(self._logical_position(pygame.mouse.get_pos()), -event.y)

    def _handle_key(self, pygame, key: int, modifiers: int = 0) -> None:
        # Ctrl reserves plus/minus for zoom; the existing speed shortcuts still work.
        if modifiers & pygame.KMOD_CTRL:
            if key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                self._change_zoom(pygame, 1)
                return
            if key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self._change_zoom(pygame, -1)
                return
            if key in (pygame.K_0, pygame.K_KP0):
                self._set_zoom(pygame, 1.0)
                return
        if key in (pygame.K_h, pygame.K_F1) or (key == pygame.K_ESCAPE and self._guide_open):
            self._toggle_guide()
        elif key == pygame.K_q or key == pygame.K_ESCAPE:
            self._quit = True
        elif key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
            direction = -1 if key == pygame.K_PAGEUP else 1
            self._scroll_panel(self._logical_position(pygame.mouse.get_pos()), direction, page=True)
        elif self._guide_open:
            return
        elif key == pygame.K_c:
            metrics = ("Cash", "Cash flow", "Sales")
            self._chart_metric = metrics[(metrics.index(self._chart_metric) + 1) % len(metrics)]
        elif key == pygame.K_SPACE:
            at_end = self._finished and self._cursor == len(self.timeline.frames) - 1
            self._paused = True if at_end else not self._paused
            self._reset_deadline()
        elif key in (pygame.K_HOME, pygame.K_LEFT, pygame.K_p):
            self._paused = True
            # A rewind in the same event batch cancels a pending request for a live turn.
            self._step_requested = False
            self._cursor = 0 if key == pygame.K_HOME else max(0, self._cursor - 1)
            self._reset_deadline()
        elif key in (pygame.K_RIGHT, pygame.K_n):
            self._advance()
        elif key in (pygame.K_UP, pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.turn_delay = max(MINIMUM_DELAY, self.turn_delay / 1.5)
            self._reset_deadline()
        elif key in (pygame.K_DOWN, pygame.K_MINUS, pygame.K_KP_MINUS):
            self.turn_delay = min(MAXIMUM_DELAY, max(MINIMUM_DELAY, self.turn_delay) * 1.5)
            self._reset_deadline()

    def _logical_position(self, position):
        """Translate display pixels into layout coordinates for hit testing."""
        return tuple(value / self._zoom for value in position)

    def _refresh_render_fonts(self, pygame) -> None:
        """Rasterize fonts at the zoomed size so enlarged text stays sharp."""
        self._render_fonts = {
            name: _font(pygame, round(size * self._zoom), bold=bold, mono=mono)
            for name, (size, bold, mono) in FONT_STYLES.items()
        }

    def _change_zoom(self, pygame, direction: int) -> None:
        """Select the next zoom level without affecting playback or simulation state."""
        levels = [level for level in ZOOM_LEVELS if (level - self._zoom) * direction > 0.001]
        if levels:
            self._set_zoom(pygame, min(levels) if direction > 0 else max(levels))

    def _set_zoom(self, pygame, zoom: float) -> None:
        """Grow the window as needed, limiting zoom to space available on its display."""
        display = pygame.display.get_window_size()
        desktop = pygame.display.get_desktop_sizes()[0]
        # Reserve space for desktop panels and the window manager's title bar.
        available = (max(display[0], desktop[0] - 48), max(display[1], desktop[1] - 80))
        self._zoom = max(1.0, min(zoom, ZOOM_LEVELS[-1], available[0] / 900, available[1] / 600))
        self._size = (max(display[0], math.ceil(900 * self._zoom)),
                      max(display[1], math.ceil(600 * self._zoom)))
        if self._size != display:
            self._screen = pygame.display.set_mode(self._size, pygame.RESIZABLE)
        self._refresh_render_fonts(pygame)
        self._guide_scroll = 0
        # Rebuild hit targets immediately, even when several events arrive together.
        self._draw()

    def _scaled_rect(self, pygame, rectangle):
        """Scale rectangle edges together to avoid gaps from independent rounding."""
        rectangle = pygame.Rect(rectangle)
        left, top = round(rectangle.left * self._zoom), round(rectangle.top * self._zoom)
        return pygame.Rect(left, top, round(rectangle.right * self._zoom) - left,
                           round(rectangle.bottom * self._zoom) - top)

    def _paint_rect(self, pygame, color, rectangle, border_radius=0):
        """Draw a filled rectangle in display pixels from layout coordinates."""
        pygame.draw.rect(self._screen, color, self._scaled_rect(pygame, rectangle),
                         border_radius=round(border_radius * self._zoom))

    def _paint_line(self, pygame, color, start, end, width=1):
        """Draw a line with zoomed endpoints and stroke width."""
        pygame.draw.line(self._screen, color,
                         tuple(round(value * self._zoom) for value in start),
                         tuple(round(value * self._zoom) for value in end),
                         max(1, round(width * self._zoom)))

    def _paint_lines(self, pygame, color, closed, points, width=1):
        """Scale chart vertices without changing their underlying data."""
        pygame.draw.lines(self._screen, color, closed,
                          [tuple(round(value * self._zoom) for value in point) for point in points],
                          max(1, round(width * self._zoom)))

    def _paint_circle(self, pygame, color, center, radius):
        """Draw a chart endpoint at the same scale as its line."""
        pygame.draw.circle(self._screen, color,
                           tuple(round(value * self._zoom) for value in center),
                           max(1, round(radius * self._zoom)))

    def _toggle_guide(self) -> None:
        """Pause while reading help, then restore the previous playback state."""
        if self._guide_open:
            self._paused = self._guide_was_paused
        else:
            self._guide_was_paused = self._paused
            self._paused = True
            self._step_requested = False
            self._guide_scroll = 0
        self._guide_open = not self._guide_open
        self._reset_deadline()

    def _handle_click(self, pygame, position: tuple[int, int]) -> None:
        """Activate visible controls without sending clicks through the guide."""
        if self._guide_open:
            if self._guide_close is not None and self._guide_close.collidepoint(position):
                self._toggle_guide()
            return
        for direction, rectangle in self._zoom_buttons.items():
            if rectangle.collidepoint(position):
                self._change_zoom(pygame, direction)
                return
        for key, rectangle in self._buttons.items():
            if rectangle.collidepoint(position):
                self._handle_key(pygame, key)
                return
        for metric, rectangle in self._chart_buttons.items():
            if rectangle.collidepoint(position):
                self._chart_metric = metric
                return

    def _scroll_panel(self, position, direction: int, page: bool = False) -> None:
        """Scroll the guide or the hovered data panel, defaulting to the roster."""
        if self._guide_open:
            step = 40
            if page:
                # Use whole logical lines, leaving one visible line of context.
                visible_lines = self._guide_content_height // GUIDE_LINE_HEIGHT
                step = max(1, visible_lines - 1) * GUIDE_LINE_HEIGHT
            self._guide_scroll = max(0, min(self._guide_max_scroll, self._guide_scroll + direction * step))
            return
        target = next((name for name, rect in self._panel_bounds.items()
                       if rect.collidepoint(position)), "teams" if page else None)
        if target is None:
            return
        maximum, capacity = self._row_limits[target]
        self._scroll[target] = max(0, min(maximum, self._scroll[target] + direction * (capacity if page else 1)))

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        """Lay out the selected snapshot with room reserved for labels and controls."""
        import pygame

        if self._screen is None:
            return
        width, height = (int(value / self._zoom) for value in self._screen.get_size())
        self._screen.fill(BACKGROUND)
        self._name_targets = []
        header_height, footer_height, gap = 68, 74, 12
        self._draw_header(pygame, 0, 0, width, header_height)
        body_top = header_height + gap
        body_height = height - header_height - footer_height - 3 * gap
        # Two columns + one inner gap + equal outside gutters fill the window.
        left_width = int((width - 3 * gap) * 0.43)
        right_x = 2 * gap + left_width
        right_width = width - right_x - gap
        self._draw_leaderboard(pygame, gap, body_top, left_width, body_height)
        # Let history grow with the window while keeping room for both phase panels.
        chart_height = int(body_height * 0.38)
        self._draw_capital_chart(pygame, right_x, body_top, right_width, chart_height)
        lower_top = body_top + chart_height + gap
        lower_height = body_height - chart_height - gap
        half = (right_width - gap) // 2
        self._draw_auction(pygame, right_x, lower_top, half, lower_height)
        self._draw_sales(pygame, right_x + half + gap, lower_top,
                         right_width - half - gap, lower_height)
        self._draw_footer(pygame, 0, height - footer_height, width, footer_height)
        if self._guide_open:
            self._draw_guide(pygame, width, height)
        else:
            self._draw_tooltip(pygame, width, height)
        pygame.display.flip()

    def _draw_header(self, pygame, x: int, y: int, width: int, height: int) -> None:
        report = self._report
        self._text(pygame, "Dynamic Market Exchange", x + 16, y + 8, "title")
        self._text(pygame, f"turn {self._frame.turn}/{self._turns}", x + 16, y + 40, "small", ACCENT)
        facts = (f"latest {self.timeline.frames[-1].turn}   seed {self._seed}   "
                 f"customers {report.customers if report else 0}   {self.turn_delay:.2f}s / turn")
        self._fitted_text(pygame, facts, x + 190, y + 40, width - 390, "small", MUTED)
        mode = ("REPLAY" if self._replaying else "FINISHED" if self._finished else
                "HISTORY" if self._cursor < len(self.timeline.frames) - 1 else "LIVE")
        status = "PAUSED" if self._paused else "PLAYING"
        self._right_text(pygame, f"{mode} · {status}", width - 16, y + 14, "heading", MUTED)
        self._zoom_buttons = {}
        for direction, left, label in ((-1, width - 174, "−"), (1, width - 48, "+")):
            rectangle = pygame.Rect(left, y + 36, 30, 26)
            self._zoom_buttons[direction] = rectangle
            self._paint_rect(pygame, PANEL_EDGE, rectangle, border_radius=4)
            self._text(pygame, label, left + 8, y + 39, "body")
        self._text(pygame, f"Zoom {self._zoom:.0%}", width - 138, y + 40, "small", MUTED)
        progress = self._frame.turn / max(1, self._turns)
        self._paint_rect(pygame, PANEL_EDGE, (x, y + height - 4, width, 2))
        self._paint_rect(pygame, ACCENT, (x, y + height - 4, int(width * progress), 2))

    def _visible_rows(self, name, rows, x, y, width, height, row_height, top=54, bottom=24):
        """Clamp scrolling and keep row text away from panel headings and footers."""
        import pygame

        self._panel_bounds[name] = pygame.Rect(x, y, width, height)
        capacity = max(1, int((height - top - bottom) // row_height))
        maximum = max(0, len(rows) - capacity)
        self._row_limits[name] = (maximum, capacity)
        self._scroll[name] = min(maximum, self._scroll[name])
        offset = self._scroll[name]
        return list(enumerate(rows[offset:offset + capacity], offset))

    def _row_hint(self, name, count):
        """Describe hidden rows instead of silently dropping them."""
        maximum, capacity = self._row_limits[name]
        if not maximum:
            return ""
        offset = self._scroll[name]
        return f"{offset + 1}-{min(count, offset + capacity)} / {count} · scroll"

    def _draw_leaderboard(self, pygame, x: int, y: int, width: int, height: int) -> None:
        self._panel(pygame, x, y, width, height, "Cash & activity")
        self._fitted_text(pygame, "Flows: this turn · unit share: since turn 1", x + 14, y + 35, width - 28, "small", MUTED)
        ranked = sorted(self._frame.teams.items(), key=lambda item: item[1].capital, reverse=True)
        row_height = 100
        visible = self._visible_rows("teams", ranked, x, y, width, height, row_height, bottom=64)
        total = sum(self._total_sold.values())
        for row, (index, (team, state)) in enumerate(visible):
            row_y = y + 60 + row * row_height
            color = self._colors[team]
            activity = team_activity(self._report, team)
            cash = "BANKRUPT" if state.bankrupt else f"${state.capital:,.0f}"
            cash_width = self._fonts["number"].size(cash)[0]
            self._text(pygame, str(index + 1), x + 14, row_y, "small", MUTED)
            self._paint_rect(pygame, color, (x + 14, row_y + 23, 4, 61))
            self._fitted_text(pygame, team, x + 32, row_y, width - cash_width - 58, "heading", color)
            self._right_text(pygame, cash, x + width - 14, row_y, "number", TEXT)
            delta = self.timeline.capital_delta(self._cursor, team)
            share = f"{self._total_sold.get(team, 0) / total:.1%}" if total else "0%"
            share_label = f"Unit share {share}"
            delta_width = width - 58 - self._fonts["small"].size(share_label)[0]
            self._fitted_text(pygame, f"Δ cash {delta:+,.0f}",
                              x + 32, row_y + 21, delta_width, "small", POSITIVE if delta > 0 else NEGATIVE if delta < 0 else MUTED)
            self._right_text(pygame, share_label, x + width - 14, row_y + 21, "small", MUTED)
            self._fitted_text(pygame, f"Bought {activity.bought} · Built {activity.built} · Sold {activity.sold}/{activity.listed} listed",
                              x + 32, row_y + 40, width - 46, "small")
            self._fitted_text(pygame, f"Spent ${activity.spent:,.0f} · Revenue ${activity.revenue:,.0f}",
                              x + 32, row_y + 59, width - 46, "small")
            raw = sum(state.inventory.get(good, 0) for good in self.timeline.metadata.raw_materials)
            stock = sum(state.inventory.get(good, 0) for good in self.timeline.metadata.finished_goods)
            self._fitted_text(pygame, f"Stock: {raw} raw · {stock} goods", x + 32, row_y + 78, width - 46, "small", MUTED)
        share_y = y + 60 + len(visible) * row_height + 2
        self._draw_share_block(pygame, x, share_y, width, y + height - 8)
        hint = self._row_hint("teams", len(ranked))
        if hint:
            self._right_text(pygame, hint, x + width - 14, y + 12, "small", MUTED)

    def _draw_share_block(self, pygame, x, y, width, bottom) -> None:
        """Show cumulative unit shares and market context when space permits."""
        if bottom - y < 42:
            return
        self._text(pygame, "Unit share · team colors", x + 14, y, "small", MUTED)
        total = sum(self._total_sold.values())
        bar = pygame.Rect(x + 14, y + 24, width - 28, 8)
        self._paint_rect(pygame, PANEL_EDGE, bar)
        offset = bar.left
        cumulative = 0
        if total:
            for team in self.timeline.metadata.team_ids:
                cumulative += self._total_sold.get(team, 0)
                edge = bar.left + round(bar.width * cumulative / total)
                self._paint_rect(pygame, self._colors[team], (offset, bar.top, edge - offset, bar.height))
                offset = edge
        if self._report is None or bottom - y < 140:
            return
        report = self._report
        sold = sum(report.sold_by_good.values())
        won = sum(sum(units.values()) for units in report.units_won.values())
        for index, (label, value, whole) in enumerate((
            ("Customers served", sold, report.customers),
            ("Materials allocated", won, report.units_offered),
        )):
            row_y = y + 54 + index * 44
            self._text(pygame, label, x + 14, row_y, "small", MUTED)
            self._right_text(pygame, f"{value}/{whole}", x + width - 14, row_y, "small")
            self._paint_rect(pygame, PANEL_EDGE, (x + 14, row_y + 23, width - 28, 5))
            if whole:
                self._paint_rect(pygame, DEMAND_MET, (x + 14, row_y + 23, int((width - 28) * value / whole), 5))

    def _draw_capital_chart(self, pygame, x: int, y: int, width: int, height: int) -> None:
        """Plot one selected metric, with visible units, turn ticks and no future data."""
        self._panel(pygame, x, y, width, height, "History")
        self._chart_buttons = {}
        tab_x = x + 100
        for metric in ("Cash", "Cash flow", "Sales"):
            tab_width = self._fonts["body"].size(metric)[0] + 20
            rect = pygame.Rect(tab_x, y + 8, tab_width, 28)
            self._chart_buttons[metric] = rect
            self._text(pygame, metric, tab_x + 10, y + 12, "body", TEXT if metric == self._chart_metric else MUTED)
            if metric == self._chart_metric:
                self._paint_line(pygame, ACCENT, (tab_x + 10, y + 34), (rect.right - 10, y + 34))
            tab_x += tab_width + 4
        units = "units / turn" if self._chart_metric == "Sales" else "$ / turn" if self._chart_metric == "Cash flow" else "$ cash (stock excluded)"
        self._fitted_text(pygame, units + " · team colors", x + 14, y + 40, width - 28, "small", MUTED)
        capital = history_series(self.timeline, self._cursor, self._chart_metric)
        values = [value for series in capital.values() for value in series]
        minimum, maximum = min(values, default=0), max(values, default=0)
        if self._chart_metric == "Cash":
            padding = (maximum - minimum) * 0.15 or max(1, abs(maximum) * 0.05)
            low, high = minimum - padding, maximum + padding
        else:
            low, high = min(minimum, 0), max(maximum, 1)
            if self._chart_metric == "Sales":
                high = max(1, math.ceil(high))
        span = high - low
        plot = pygame.Rect(x + 76, y + 65, width - 94, max(25, height - 94))
        if self._chart_metric == "Sales":
            tick_values = sorted({0, high // 2, high})
        elif low < 0 < high and self._chart_metric == "Cash flow":
            tick_values = (low, 0, high)
        else:
            tick_values = (low, low + span / 2, high)
        for value in tick_values:
            fraction = (value - low) / span
            grid_y = plot.bottom - int(plot.height * fraction)
            self._paint_line(pygame, PANEL_EDGE, (plot.left, grid_y), (plot.right, grid_y))
            label = f"{value:,.0f}" if span >= 2 and span < 1000 else f"{value:.1f}" if span < 2 else _compact(value)
            self._right_text(pygame, label, plot.left - 10, grid_y - 8, "small", MUTED)
        if low < 0 < high:
            baseline = plot.bottom - int(plot.height * (0 - low) / span)
            self._paint_line(pygame, MUTED, (plot.left, baseline), (plot.right, baseline))
        for team, series in capital.items():
            points = [(plot.left + int(plot.width * turn / max(1, self._cursor)),
                       plot.bottom - int(plot.height * (value - low) / span))
                      for turn, value in enumerate(series)]
            if len(points) > 1:
                self._paint_lines(pygame, self._colors[team], False, points, 2)
            if points:
                self._paint_circle(pygame, self._colors[team], points[-1], 3)
        ticks = sorted({0, self._cursor // 2, self._cursor})
        for turn in ticks:
            tick_x = plot.left + int(plot.width * turn / max(1, self._cursor))
            text_width = self._fonts["small"].size(str(turn))[0]
            self._text(pygame, str(turn), min(plot.right - text_width, max(plot.left, tick_x - text_width // 2)), plot.bottom + 6, "small", MUTED)
        if self._cursor == 0:
            self._text(pygame, "turn", plot.right - 35, plot.bottom + 6, "small", MUTED)

    def _draw_auction(self, pygame, x: int, y: int, width: int, height: int) -> None:
        self._panel(pygame, x, y, width, height, "Auction · this turn")
        self._fitted_text(pygame, "Team colors; dark = unsold", x + 14, y + 35, width - 28, "small", MUTED)
        report = self._report
        if report is None:
            self._fitted_text(pygame, "No completed auction yet.", x + 14, y + 64, width - 28, "small", MUTED)
            return
        rows = list(report.lots.items())
        visible = self._visible_rows("auction", rows, x, y, width, height, 34, top=58)
        for row, (_, (good, offered)) in enumerate(visible):
            row_y = y + 58 + row * 34
            won = {team: units.get(good, 0) for team, units in report.units_won.items() if units.get(good, 0)}
            sold = sum(won.values())
            low, high = report.clearing_price.get(good, 0), report.highest_bid.get(good, 0)
            price = "—" if not sold else f"${low:,.1f}" if low == high else f"${low:,.1f}-{high:,.1f}"
            label = f"{sold}/{offered} · {price}"
            label_width = self._fonts["small"].size(label)[0]
            self._fitted_text(pygame, good, x + 14, row_y, width - label_width - 38, "small")
            self._right_text(pygame, label, x + width - 14, row_y, "small")
            bar = pygame.Rect(x + 14, row_y + 23, width - 28, 5)
            self._paint_rect(pygame, PANEL_EDGE, bar)
            offset, cumulative = bar.left, 0
            for team in self.timeline.metadata.team_ids:
                cumulative += won.get(team, 0)
                edge = bar.left + round(bar.width * cumulative / max(1, offered))
                self._paint_rect(pygame, self._colors[team], (offset, bar.top, edge - offset, bar.height))
                offset = edge
        hint = self._row_hint("auction", len(rows)) or "Won/offered · paid per unit"
        self._fitted_text(pygame, hint, x + 14, y + height - 21, width - 28, "small", MUTED)

    def _draw_sales(self, pygame, x: int, y: int, width: int, height: int) -> None:
        self._panel(pygame, x, y, width, height, "Sales · this turn")
        self._paint_rect(pygame, DEMAND_MET, (x + 14, y + 39, 10, 7))
        self._text(pygame, "served", x + 30, y + 34, "small", MUTED)
        self._hatch(pygame, pygame.Rect(x + 95, y + 39, 10, 7))
        self._text(pygame, "unserved", x + 111, y + 34, "small", MUTED)
        report = self._report
        if report is None:
            self._fitted_text(pygame, "No completed sales yet.", x + 14, y + 64, width - 28, "small", MUTED)
            return
        rows = list(report.sold_by_good.items())
        peak = max((sold + report.stockouts.get(good, 0) for good, sold in rows), default=0) or 1
        visible = self._visible_rows("sales", rows, x, y, width, height, 34, top=58, bottom=20)
        label_width = max((self._fonts["small"].size(f"{report.stockouts.get(good, 0)} unserved")[0]
                           for good, _ in rows), default=0)
        bar_width = max(20, width - 40 - label_width)
        for row, (_, (good, sold)) in enumerate(visible):
            row_y = y + 58 + row * 34
            missed = report.stockouts.get(good, 0)
            revenue = report.revenue_by_good.get(good, 0)
            label = f"{sold} @ ${revenue / sold:,.0f}" if sold else "0 sold · avg —"
            label_width = self._fonts["small"].size(label)[0]
            self._fitted_text(pygame, good, x + 14, row_y, width - label_width - 38, "small")
            self._right_text(pygame, label, x + width - 14, row_y, "small")
            missed_label = f"{missed} unserved"
            bar = pygame.Rect(x + 14, row_y + 23, bar_width, 5)
            self._paint_rect(pygame, PANEL_EDGE, bar)
            sold_width = round(bar.width * sold / peak)
            missed_width = round(bar.width * (sold + missed) / peak) - sold_width
            self._paint_rect(pygame, DEMAND_MET, (bar.left, bar.top, sold_width, bar.height))
            if missed_width:
                self._hatch(pygame, pygame.Rect(bar.left + sold_width, bar.top, missed_width, bar.height))
            self._right_text(pygame, missed_label, x + width - 14, row_y + 17, "small", MUTED)
        hint = self._row_hint("sales", len(rows)) or f"{sum(report.stockouts.values())}/{report.customers} customers unserved"
        self._fitted_text(pygame, hint, x + 14, y + height - 21, width - 28, "small", MUTED)

    def _draw_footer(self, pygame, x: int, y: int, width: int, height: int) -> None:
        self._fitted_text(pygame, self._headline(), x + 16, y + 5, width - 32, "small", MUTED)
        self._buttons = {}
        left = x + 16
        for key, label, button_width in (
            (pygame.K_HOME, "Restart", 92),
            (pygame.K_LEFT, "← Back", 82), (pygame.K_SPACE, "Play" if self._paused else "Pause", 76),
            (pygame.K_RIGHT, "Next →", 82), (pygame.K_h, "Guide [H]", 100),
        ):
            rectangle = pygame.Rect(left, y + 31, button_width, 32)
            self._buttons[key] = rectangle
            hovered = rectangle.collidepoint(self._logical_position(pygame.mouse.get_pos()))
            self._paint_rect(pygame, PANEL_EDGE if hovered else PANEL, rectangle, border_radius=4)
            self._text(pygame, label, left + 10, y + 38, "body")
            left += button_width + 8
        self._fitted_text(pygame, "Space play/pause · C chart · ↑/↓ speed · Q quit", left + 8, y + 40, width - left - 24, "small", MUTED)

    def _headline(self) -> str:
        """Summarize the selected turn without attributing unmet demand to one cause."""
        if self._report is None:
            return "Initial state · no auction, production or sales completed yet. H opens the guide."
        report = self._report
        sold = sum(report.sold_by_good.values())
        return (f"Turn {report.turn} · {sold}/{report.customers} customers served · "
                f"{sum(report.stockouts.values())} unserved (stock or affordability) · "
                "cash flow = revenue − spent")

    def _draw_guide(self, pygame, width, height) -> None:
        """Draw a scrollable metric legend while keeping live execution paused."""
        overlay = pygame.Surface(self._screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        self._screen.blit(overlay, (0, 0))
        rectangle = pygame.Rect(30, 24, width - 60, height - 48)
        self._paint_rect(pygame, PANEL, rectangle, border_radius=8)
        self._text(pygame, "Guide / legend", rectangle.left + 24, rectangle.top + 16, "title")
        self._guide_close = pygame.Rect(rectangle.right - 112, rectangle.top + 12, 92, 32)
        self._paint_rect(pygame, PANEL_EDGE, self._guide_close, border_radius=4)
        self._text(pygame, "Close [H]", self._guide_close.left + 8, self._guide_close.top + 7, "small")
        self._text(pygame, f"Reading turn {self._cursor} · playback paused while this guide is open", rectangle.left + 24, rectangle.top + 51, "body", MUTED)
        content = pygame.Rect(rectangle.left + 24, rectangle.top + 88, rectangle.width - 48, rectangle.height - 140)
        self._guide_content_height = content.height
        lines = []
        for title, explanation in (*GUIDE_SECTIONS, ("Window controls", WINDOW_CONTROLS)):
            lines.append((title, "heading", TEXT))
            lines.extend((line, "body", MUTED) for line in self._wrap_lines(explanation, content.width - 12, "body"))
            lines.append(("", "body", MUTED))
        self._guide_max_scroll = max(0, len(lines) * GUIDE_LINE_HEIGHT - content.height)
        self._guide_scroll = min(self._guide_scroll, self._guide_max_scroll)
        previous_clip = self._screen.get_clip()
        self._screen.set_clip(self._scaled_rect(pygame, content))
        for index, (line, font, color) in enumerate(lines):
            self._text(pygame, line, content.left, content.top + index * GUIDE_LINE_HEIGHT - self._guide_scroll, font, color)
        self._screen.set_clip(previous_clip)
        caption = "Wheel or PgUp/PgDn to scroll · H/Esc closes · previous playback state is restored"
        self._fitted_text(pygame, caption, rectangle.left + 24, rectangle.bottom - 33, rectangle.width - 48, "small", MUTED)
        if self._guide_max_scroll:
            fraction = self._guide_scroll / self._guide_max_scroll
            self._right_text(pygame, f"{fraction:.0%}", rectangle.right - 24, rectangle.top + 51, "small", MUTED)

    def _hatch(self, pygame, rectangle) -> None:
        """Use both color and pattern to identify unserved demand."""
        self._paint_rect(pygame, DEMAND_LOST, rectangle)
        previous_clip = self._screen.get_clip()
        self._screen.set_clip(self._scaled_rect(pygame, rectangle).clip(previous_clip))
        for offset in range(-rectangle.height, rectangle.width, 7):
            self._paint_line(pygame, (245, 170, 170),
                             (rectangle.left + offset, rectangle.bottom),
                             (rectangle.left + offset + rectangle.height, rectangle.top))
        self._screen.set_clip(previous_clip)

    def _wrap_lines(self, value: str, width: int, font: str) -> list[str]:
        """Wrap guide paragraphs using actual font measurements."""
        lines, current = [], ""
        for word in value.split():
            # Long team IDs have no spaces; break them before they exceed the tooltip.
            while self._fonts[font].size(word)[0] > width:
                if current:
                    lines.append(current)
                    current = ""
                cut = 1
                while cut < len(word) and self._fonts[font].size(word[:cut + 1])[0] <= width:
                    cut += 1
                lines.append(word[:cut])
                word = word[cut:]
            candidate = f"{current} {word}" if current else word
            if current and self._fonts[font].size(candidate)[0] > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _fitted_text(self, pygame, value, x, y, width, font="body", color=TEXT):
        """Keep a label in its column; expose its full text on hover if truncated."""
        original = value
        while value and self._fonts[font].size(value)[0] > max(0, width):
            value = value[:-2].rstrip("…") + "…" if len(value) > 2 else ""
        self._text(pygame, value, x, y, font, color)
        if value != original and width > 0:
            self._name_targets.append((pygame.Rect(x, y, width, self._fonts[font].get_height()), original))

    def _draw_tooltip(self, pygame, width, height):
        """Make shortened team names and labels readable without widening every panel."""
        position = self._logical_position(pygame.mouse.get_pos())
        for rectangle, value in self._name_targets:
            if rectangle.collidepoint(position):
                lines = self._wrap_lines(value, min(600, width - 48), "body")
                box_width = max(self._fonts["body"].size(line)[0] for line in lines) + 24
                box_height = len(lines) * 21 + 20
                left = min(position[0] + 12, width - box_width - 12)
                top = min(position[1] + 20, height - box_height - 12)
                self._paint_rect(pygame, PANEL_EDGE, (left, top, box_width, box_height), border_radius=4)
                for index, line in enumerate(lines):
                    self._text(pygame, line, left + 12, top + 10 + index * 21, "body")
                break

    def _panel(self, pygame, x: int, y: int, width: int, height: int, title: str) -> None:
        self._paint_rect(pygame, PANEL, (x, y, width, height), border_radius=6)
        self._text(pygame, title, x + 14, y + 12, "heading")

    def _text(self, pygame, value: str, x: int, y: int, font: str = "body", color=TEXT) -> None:
        self._screen.blit(self._render_fonts[font].render(value, True, color),
                          (round(x * self._zoom), round(y * self._zoom)))

    def _right_text(self, pygame, value: str, right, y, font: str = "body", color=TEXT) -> None:
        surface = self._render_fonts[font].render(value, True, color)
        self._screen.blit(surface, (round(right * self._zoom) - surface.get_width(), round(y * self._zoom)))


def _font(pygame, size: int, bold: bool = False, mono: bool = False):
    family = (
        "dejavusansmono,liberationmono,consolas,monospace"
        if mono
        else "dejavusans,liberationsans,segoeui,arial,sans"
    )
    return pygame.font.SysFont(family, size, bold=bold)


def _compact(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"
