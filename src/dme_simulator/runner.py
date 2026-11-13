"""Simulation orchestration independent from the CLI."""

from __future__ import annotations

import hashlib
import logging
import random
import shlex
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

from .agents.base import (
    AssemblyOrder,
    BaseAgent,
    Bid,
    Decision,
    Listing,
    Observation,
)
from .config import EnvironmentConfig, GoodsConfig
from .replay import DashboardTimeline, RecordingWriter
from .simulation import DMESimulator
from .visualization import render_plots

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, DMESimulator], None]


@dataclass(slots=True)
class _AgentRandomState:
    python: tuple[Any, ...]
    numpy: tuple[Any, ...]


def run_simulation(
    config: EnvironmentConfig,
    agents: dict[str, BaseAgent],
    output_directory: str | Path | None = None,
    seed_override: int | None = None,
    turns_override: int | None = None,
    profile: bool = False,
    quiet: bool = False,
    progress_callback: ProgressCallback | None = None,
    dashboard_override: str | None = None,
    turn_delay_override: float | None = None,
    recording_path: str | Path | None = None,
) -> DMESimulator:
    """Run agents once per turn, optionally recording copied dashboard frames."""
    seed = config.seed if seed_override is None else seed_override
    turns = turns_override or config.turns

    simulator = DMESimulator(list(agents), config, profile=profile, seed=seed)
    goods_configs = {team_id: deepcopy(config.goods) for team_id in agents}
    agent_random_states = {
        team_id: _new_agent_random_state(seed, team_id) for team_id in agents
    }
    window_requested = not quiet and (
        dashboard_override or config.output.dashboard
    ) == "window"
    # Ordinary terminal and batch runs do not need to retain dashboard snapshots.
    timeline = (
        DashboardTimeline.from_simulator(simulator, turns, seed)
        if window_requested or recording_path is not None else None
    )
    dashboard = _build_dashboard(
        config, quiet, dashboard_override, turn_delay_override, timeline
    )
    recorder = None
    end_reason = "interrupted"

    def finish_recording() -> None:
        """Finalize the file once and announce its replay command for non-quiet runs."""
        # finish() also runs during cleanup; its return value prevents duplicate commands.
        if recorder is not None and recorder.finish(end_reason) and not quiet:
            command = shlex.join([
                "uv", "run", "dme-sim", "--replay", str(Path(recording_path).resolve()),
            ])
            print(f"Replay: {command}", flush=True)

    if dashboard is None and not quiet:
        print(f"DME simulation: {len(agents)} teams, {turns} turns, seed {seed}")
    try:
        if recording_path is not None:
            recorder = RecordingWriter(recording_path, timeline.metadata)
            recorder.write_frame(timeline.frames[0])
        if dashboard is not None:
            # The window can be closed while waiting at turn 0, before any agent acts.
            if dashboard.start(simulator, turns, seed) is False:
                end_reason = "viewer_cancelled"
        for turn in range(1, turns + 1):
            if end_reason == "viewer_cancelled":
                if not quiet:
                    print("Stopped by the viewer on turn 000.")
                break
            decisions = {}
            for team_id, agent in agents.items():
                if simulator.teams_state[team_id].bankrupt:
                    continue
                state = simulator.get_team_state(team_id)
                started = perf_counter()
                try:
                    with (
                        _agent_random_context(agent_random_states[team_id]),
                        _decision_timeout(config.agents.decide_timeout_ms),
                    ):
                        decision = agent.decide(
                            state=state, goods_config=goods_configs[team_id]
                        )
                    _validate_decision(decision, state, goods_configs[team_id])
                    decisions[team_id] = decision
                except DecisionTimeout:
                    if not quiet and dashboard is None:
                        logger.warning(
                            "Agent %s reached the decide timeout of %.2f ms on turn %d; "
                            "using an empty decision",
                            team_id,
                            config.agents.decide_timeout_ms,
                            turn,
                        )
                    decisions[team_id] = Decision()
                except Exception:
                    if not quiet and dashboard is None:
                        logger.exception(
                            "Agent %s failed or returned an invalid decision on turn %d; "
                            "using an empty decision",
                            team_id,
                            turn,
                        )
                    decisions[team_id] = Decision()
                simulator.record_decision_duration(team_id, perf_counter() - started)
            report = simulator.run_turn(decisions)
            if timeline is not None:
                frame = timeline.capture(simulator, report)
                if recorder is not None:
                    recorder.write_frame(frame)
            all_bankrupt = all(
                state.bankrupt for state in simulator.teams_state.values()
            )
            completed = turn == turns or all_bankrupt
            if completed:
                end_reason = "completed"
                if timeline is not None:
                    timeline.end_reason = end_reason
                # Make the file replayable before the finished window waits for input.
                finish_recording()
            if progress_callback is not None:
                progress_callback(turn, simulator)
            if dashboard is not None:
                if not dashboard.update(simulator, report):
                    if not completed:
                        end_reason = "viewer_cancelled"
                    if not quiet:
                        print(f"Stopped by the viewer on turn {turn:03d}.")
                    break
            elif not quiet:
                _print_progress(config, simulator, turn)
            if all_bankrupt:
                if not quiet and dashboard is None:
                    print(f"All teams bankrupt on turn {turn:03d}; stopping.")
                break
    finally:
        if timeline is not None:
            timeline.end_reason = end_reason
        try:
            finish_recording()
        finally:
            if dashboard is not None:
                dashboard.stop(simulator)
    if profile and not quiet:
        _print_profile(simulator)
    if config.output.render_plots and not quiet:
        target = Path(output_directory or config.output.directory)
        render_plots(simulator, target, config.output.prefix)
    return simulator


def _build_dashboard(
    config: EnvironmentConfig,
    quiet: bool,
    mode_override: str | None,
    turn_delay_override: float | None,
    timeline: DashboardTimeline | None = None,
):
    """Return the live dashboard to drive, or None for plain output.

    The dashboard owns the run's pacing: it holds each turn on screen for
    ``turn_delay_seconds`` so a viewer can follow along, and the window mode
    lets that be paused and re-timed from the keyboard.
    """
    mode = mode_override or config.output.dashboard
    delay = (
        config.output.turn_delay_seconds
        if turn_delay_override is None
        else turn_delay_override
    )
    if quiet or mode == "none":
        return None
    if mode == "window":
        from .window import WindowDashboard

        return WindowDashboard(timeline, turn_delay=delay)
    if not sys.stdout.isatty():
        return None
    from .live import LiveDashboard

    return LiveDashboard(turn_delay=delay)


def _print_progress(
    config: EnvironmentConfig, simulator: DMESimulator, turn: int
) -> None:
    interval = config.output.progress_interval_turns
    if not (turn == 1 or (interval > 0 and turn % interval == 0)):
        return
    print(f"Turn {turn:03d}")
    for team_id, state in simulator.teams_state.items():
        status = "BANKRUPT" if state.bankrupt else f"${state.capital:,.2f}"
        goods = sum(
            state.inventory.get(good, 0) for good in config.goods.finished_goods
        )
        print(f"  {team_id:<20} capital={status:<14} goods={goods}")


class DecisionTimeout(BaseException):
    """Interrupt an agent without being caught by normal exception handling."""


def _new_agent_random_state(seed: int, team_id: str) -> _AgentRandomState:
    seed_bytes = hashlib.blake2b(f"{seed}\0{team_id}".encode(), digest_size=8).digest()
    agent_seed = int.from_bytes(seed_bytes, "big")
    python = random.Random(agent_seed)
    numpy = np.random.RandomState(agent_seed % (2**32))
    return _AgentRandomState(python.getstate(), numpy.get_state())


@contextmanager
def _agent_random_context(state: _AgentRandomState) -> Iterator[None]:
    previous_python_state = random.getstate()
    previous_numpy_state = np.random.get_state()
    random.setstate(state.python)
    np.random.set_state(state.numpy)
    try:
        yield
    finally:
        state.python = random.getstate()
        state.numpy = np.random.get_state()
        random.setstate(previous_python_state)
        np.random.set_state(previous_numpy_state)


@contextmanager
def _decision_timeout(timeout_ms: float) -> Iterator[None]:
    """Interrupt a synchronous agent call after ``timeout_ms`` milliseconds."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    deadline = perf_counter() + timeout_ms / 1_000.0

    def raise_timeout(_signum: int, _frame: object) -> None:
        raise DecisionTimeout

    signal.signal(signal.SIGALRM, raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1_000.0)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    if perf_counter() >= deadline:
        raise DecisionTimeout


def _validate_decision(
    decision: Decision,
    state: Observation,
    goods_config: GoodsConfig,
) -> None:
    if not isinstance(decision, Decision):
        raise TypeError("decide must return a Decision")

    auction_goods = {lot.good for lot in state.auction_lots}
    _validate_list(decision.bids, Bid, "bids")
    for bid in decision.bids:
        if bid.good not in auction_goods:
            raise ValueError(f"bid for good {bid.good!r} which is not in this auction")
        if not _is_nonnegative_int(bid.quantity):
            raise ValueError("bid quantities must be non-negative integers")
        if not _is_nonnegative_number(bid.price_per_unit):
            raise ValueError("bid prices must be finite non-negative numbers")

    _validate_list(decision.assembly, AssemblyOrder, "assembly")
    for order in decision.assembly:
        if order.recipe_name not in goods_config.recipes:
            raise ValueError(f"unknown recipe {order.recipe_name!r}")
        if not _is_nonnegative_int(order.quantity):
            raise ValueError("assembly quantities must be non-negative integers")

    _validate_list(decision.listings, Listing, "listings")
    for listing in decision.listings:
        if listing.good not in goods_config.finished_goods:
            raise ValueError(f"unknown finished good {listing.good!r}")
        if not _is_nonnegative_int(listing.quantity):
            raise ValueError("listing quantities must be non-negative integers")
        if not _is_nonnegative_number(listing.price):
            raise ValueError("listing prices must be finite non-negative numbers")


def _validate_list(values: object, item_type: type, field_name: str) -> None:
    if not isinstance(values, list):
        raise TypeError(f"{field_name} must be a list")
    if not all(isinstance(value, item_type) for value in values):
        raise TypeError(f"{field_name} contains an invalid item")


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool) and value >= 0


def _is_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and isfinite(float(value))
        and value >= 0
    )


def _print_profile(simulator: DMESimulator) -> None:
    diagnostics = simulator.history.diagnostics
    print("Timing profile (total / average per turn, ms):")
    for team_id, values in diagnostics.decision_duration_seconds.items():
        if values:
            total = sum(values) * 1_000
            print(f"  decision {team_id:<16} {total:.2f} / {total / len(values):.2f}")
    for phase, values in diagnostics.phase_duration_seconds.items():
        if values:
            total = sum(values) * 1_000
            print(f"  phase {phase:<19} {total:.2f} / {total / len(values):.2f}")
