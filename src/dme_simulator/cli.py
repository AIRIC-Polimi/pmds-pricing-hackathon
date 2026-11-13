"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import multiprocessing
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from .agents.base import BaseAgent
from .agents.loader import (
    discover_bundle_classes,
    load_simulation_agents,
    resolve_agent_directories,
    resolve_extra_agent_paths,
)
from .batch_runner import run_batch
from .config import DASHBOARD_MODES, load_config
from .dashboard import print_guide
from .replay import load_recording
from .runner import run_simulation
from .window import WindowDashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Dynamic Market Exchange simulation"
    )
    parser.add_argument(
        "--guide", action="store_true",
        help="print the dashboard guide and controls, then exit without running a simulation",
    )
    parser.add_argument(
        "--record", type=Path, nargs="?", const=True, metavar="PATH",
        help=(
            "record a single run to PATH, or to "
            "outputs/recordings/<timestamp>/simulation.jsonl when PATH is omitted"
        ),
    )
    parser.add_argument(
        "--replay", type=Path,
        help="open a recorded JSONL run in the window without loading agents",
    )
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        help="YAML environment configuration; repeat for multiple configs",
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        action="append",
        help=(
            "recursively search for agent.py bundles; repeat for multiple roots "
            "(replaces the directory configured in YAML)"
        ),
    )
    parser.add_argument(
        "--extra-agent",
        type=Path,
        action="append",
        help="append a bundle folder containing agent.py to the roster; repeat for more agents",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="override the dashboard output directory configured in YAML",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="print discovered bundle IDs and exit without constructing agents",
    )
    parser.add_argument(
        "--seed", type=int, help="An override for the seed in the config file"
    )
    parser.add_argument(
        "--turns", type=int, help="An override for the number of turns in the config"
    )
    parser.add_argument(
        "--ui",
        choices=DASHBOARD_MODES,
        help=(
            "how to display the run: 'window' opens a pygame dashboard, "
            "'terminal' draws in the console, 'none' prints plain progress "
            "(default: the scenario's output.dashboard)"
        ),
    )
    parser.add_argument(
        "--turn-delay",
        type=float,
        metavar="SECONDS",
        help=(
            "hold each turn on screen for this long so the run can be followed "
            "in real time (default: the scenario's output.turn_delay_seconds, "
            "or 0.6 for replay)"
        ),
    )
    parser.add_argument(
        "--simulations",
        type=int,
        # None distinguishes omission from an explicit flag when validating replay.
        default=None,
        help="number of seeded copies to run for each config (default: 1)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="maximum parallel worker processes (default: CPU count)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="print decision and simulation timing summaries",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.guide:
        print_guide()
        return
    if args.turns is not None and args.turns < 1:
        raise SystemExit("--turns must be at least 1")
    if args.simulations is not None and args.simulations < 1:
        raise SystemExit("--simulations must be at least 1")
    if args.max_workers is not None and args.max_workers < 1:
        raise SystemExit("--max-workers must be at least 1")
    if args.turn_delay is not None and (
        not isfinite(args.turn_delay) or args.turn_delay < 0
    ):
        raise SystemExit("--turn-delay must be finite and nonnegative")
    if args.replay is not None:
        conflicts = (
            args.config is not None or args.agents_dir is not None
            or args.output_dir is not None or args.list_agents
            or args.seed is not None or args.turns is not None
            or args.simulations is not None or args.max_workers is not None
            or args.profile or args.record is not None
            or args.ui not in (None, "window")
        )
        if conflicts:
            raise SystemExit("--replay accepts only --ui window and --turn-delay")

        try:
            timeline = load_recording(args.replay)
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        delay = 0.6 if args.turn_delay is None else args.turn_delay
        WindowDashboard(timeline, turn_delay=delay).replay()
        return
    # Restore normal run defaults only after checking replay's allowed options.
    args.simulations = args.simulations or 1
    args.max_workers = args.max_workers or multiprocessing.cpu_count() or 1
    config_paths = [
        path.resolve() for path in (args.config or [Path("config/default.yaml")])
    ]
    multiple_runs = len(config_paths) > 1 or args.simulations > 1
    if args.record is not None and (multiple_runs or args.list_agents):
        raise SystemExit("--record requires a single simulation, without --list-agents")
    if args.profile and multiple_runs:
        raise SystemExit("--profile can only be used with a single run")
    if args.list_agents:
        agent_ids: set[str] = set()
        for config_path in config_paths:
            config = load_config(config_path)
            agent_ids.update(
                _discover_agents(
                    config_path, config.agents.directory, args.agents_dir, args.extra_agent
                )
            )
        print("\n".join(sorted(agent_ids)))
        return

    if multiple_runs:
        output_directory = args.output_dir.resolve() if args.output_dir else None
        run_batch(
            config_paths,
            args.simulations,
            min(args.max_workers, multiprocessing.cpu_count() or 1),
            args.agents_dir,
            output_directory,
            args.seed,
            args.turns,
            extra_agents=args.extra_agent,
        )
        return

    config = load_config(config_paths[0])
    directories = resolve_agent_directories(
        config_paths[0], config.agents.directory, args.agents_dir
    )
    agents = load_simulation_agents(
        directories, config.agents.include, args.extra_agent
    )
    project_root = config_paths[0].parent.parent
    output_directory = args.output_dir
    if output_directory is None:
        output_directory = Path(config.output.directory)
        if not output_directory.is_absolute():
            output_directory = project_root / output_directory
    recording_path = args.record
    # argparse uses True for bare --record, a Path for an explicit target, and None otherwise.
    if recording_path is True:
        # UTC keeps names unambiguous; microseconds distinguish runs started in quick succession.
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S-%fZ")
        recording_directory = project_root / "outputs" / "recordings" / timestamp
        recording_directory.mkdir(parents=True)
        recording_path = recording_directory / "simulation.jsonl"
    run_simulation(
        config,
        agents,
        output_directory,
        seed_override=args.seed,
        turns_override=args.turns,
        profile=args.profile,
        dashboard_override=args.ui,
        turn_delay_override=args.turn_delay,
        recording_path=recording_path,
    )


def _discover_agents(
    config_path: Path,
    configured_directory: str,
    overrides: list[Path] | None,
    extra_agents: list[Path] | None = None,
) -> dict[str, type[BaseAgent]]:
    directories = resolve_agent_directories(config_path, configured_directory, overrides)
    directories.extend(resolve_extra_agent_paths(extra_agents))
    return discover_bundle_classes(directories)
