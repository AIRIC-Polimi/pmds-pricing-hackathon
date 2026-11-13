"""Small profiling harness for finding DME simulation bottlenecks.

Examples:
    uv run python benchmarks/benchmark_simulation.py --config config/default.yaml --turns 5
    uv run python benchmarks/benchmark_simulation.py \
        --config config/scaled_market.yaml --turns 3 --repeats 2 --top-functions 20
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import time
from dataclasses import replace
from pathlib import Path

from dme_simulator.agents.loader import (
    load_simulation_agents,
    resolve_agent_directories,
)
from dme_simulator.config import load_config
from dme_simulator.runner import run_simulation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark DME simulation phases")
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        default=None,
        help="YAML config to benchmark; repeat for multiple configs",
    )
    parser.add_argument(
        "--turns", type=int, help="turns per sample (default: config value)"
    )
    parser.add_argument("--repeats", type=int, default=1, help="samples per config")
    parser.add_argument("--seed", type=int, help="base seed; repeats increment it")
    parser.add_argument(
        "--agents-dir", type=Path, action="append",
        help="override the bundle search roots; repeat for multiple directories",
    )
    parser.add_argument(
        "--extra-agent", type=Path, action="append",
        help="append a bundle folder to the configured roster; repeat for more agents",
    )
    parser.add_argument(
        "--top-functions",
        type=int,
        default=0,
        metavar="N",
        help="show N cumulative cProfile entries for the first sample",
    )
    return parser


def _build_agents(
    config_path: Path, config, overrides: list[Path] | None,
    extra_agents: list[Path] | None = None,
):
    directories = resolve_agent_directories(config_path, config.agents.directory, overrides)
    return load_simulation_agents(directories, config.agents.include, extra_agents)


def _run_sample(
    config_path: Path, turns: int | None, seed: int, agents_dir: list[Path] | None,
    extra_agents: list[Path] | None = None,
):
    config = load_config(config_path)
    config = replace(
        config,
        output=replace(
            config.output,
            render_plots=False,
            progress_interval_turns=0,
            dashboard="none",
        ),
    )
    agents = _build_agents(config_path, config, agents_dir, extra_agents)
    started = time.perf_counter()
    simulator = run_simulation(
        config,
        agents,
        seed_override=seed,
        turns_override=turns,
        profile=True,
        quiet=True,
    )
    return simulator, time.perf_counter() - started


def _profile_sample(
    config_path: Path, turns: int | None, seed: int, agents_dir: list[Path] | None,
    extra_agents: list[Path] | None = None,
):
    profiler = cProfile.Profile()
    profiler.enable()
    _run_sample(config_path, turns, seed, agents_dir, extra_agents)
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats()
    return output.getvalue()


def _print_report(config_path: Path, samples) -> None:
    elapsed = [duration for _, duration in samples]
    simulator = samples[0][0]
    turns = max(1, len(simulator.history.turns))
    average = sum(elapsed) / len(elapsed)
    print(f"\n{config_path}")
    print(f"  wall time: {average:.3f}s/sample, {turns / average:.2f} turns/s")
    print("  average phase time per turn:")
    phase_values = simulator.history.diagnostics.phase_duration_seconds
    for phase, values in sorted(phase_values.items(), key=lambda item: -sum(item[1])):
        if values:
            print(f"    {phase:<20} {sum(values) / len(values) * 1000:9.3f} ms")

    decision_values = simulator.history.diagnostics.decision_duration_seconds
    decision_total = sum(sum(values) for values in decision_values.values())
    decision_calls = sum(len(values) for values in decision_values.values())
    if decision_calls:
        print(
            f"    {'agent decisions':<20} {decision_total / decision_calls * 1000:9.3f} ms/call"
        )


def main() -> None:
    args = build_parser().parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if args.turns is not None and args.turns < 1:
        raise SystemExit("--turns must be at least 1")
    if args.top_functions < 0:
        raise SystemExit("--top-functions cannot be negative")

    config_paths = [
        path.resolve() for path in (args.config or [Path("config/default.yaml")])
    ]
    for config_index, config_path in enumerate(config_paths):
        config = load_config(config_path)
        base_seed = config.seed if args.seed is None else args.seed
        samples = [
            _run_sample(
                config_path, args.turns, base_seed + repeat, args.agents_dir, args.extra_agent
            )
            for repeat in range(args.repeats)
        ]
        _print_report(config_path, samples)
        if args.top_functions and config_index == 0:
            print(f"\nTop {args.top_functions} cumulative functions:")
            profile = _profile_sample(
                config_path, args.turns, base_seed, args.agents_dir, args.extra_agent
            )
            print("\n".join(profile.splitlines()[: args.top_functions + 6]))


if __name__ == "__main__":
    main()
