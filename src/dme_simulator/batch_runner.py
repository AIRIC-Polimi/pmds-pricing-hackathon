"""Parallel orchestration and aggregation for batches of simulations."""

from __future__ import annotations

import copy
import io
import multiprocessing
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields, is_dataclass
from pathlib import Path
from queue import Empty
from typing import Any

import numpy as np
from tqdm import tqdm

from .agents.loader import (
    load_simulation_agents,
    resolve_agent_directories,
    resolve_extra_agent_paths,
)
from .config import load_config
from .history import SimulationHistory
from .runner import run_simulation
from .simulation import DMESimulator


def run_batch(
    config_paths: list[Path],
    simulations_per_config: int,
    max_workers: int,
    agents_directories: list[Path] | None,
    output_directory: Path | None,
    seed_override: int | None,
    turns_override: int | None,
    extra_agents: list[Path] | None = None,
) -> list[DMESimulator]:
    """Run config/seed jobs in worker processes and render grouped outputs."""
    jobs: list[dict[str, Any]] = []
    extra_paths = resolve_extra_agent_paths(extra_agents)
    configs = [(config_path, load_config(config_path)) for config_path in config_paths]
    # Interleave configs so the process pool does not spend all of its initial
    # worker slots on one config while other configs wait in the queue.
    for offset in range(simulations_per_config):
        for config_path, config in configs:
            jobs.append(
                {
                    "config_path": config_path,
                    "seed": (
                        seed_override if seed_override is not None else config.seed
                    )
                    + offset,
                    "turns": turns_override,
                    "agents_directories": agents_directories,
                    "extra_agents": extra_paths,
                    "job_id": len(jobs),
                }
            )

    worker_count = min(max_workers, multiprocessing.cpu_count(), len(jobs))
    with multiprocessing.Manager() as manager:
        progress_queue = manager.Queue()
        for job in jobs:
            job["progress_queue"] = progress_queue

        bars = {}
        results: dict[int, DMESimulator] = {}
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(_run_job, job): job for job in jobs}
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                _update_progress(progress_queue, bars, jobs)
                for future in done:
                    job = futures[future]
                    results[job["job_id"]] = future.result()
            _update_progress(progress_queue, bars, jobs)

        for bar in bars.values():
            bar.close()

    simulations = [results[index] for index in range(len(jobs))]
    _render_grouped_outputs(jobs, simulations, output_directory)
    return simulations


def _run_job(job: dict[str, Any]) -> DMESimulator:
    config_path = Path(job["config_path"])
    config = load_config(config_path)
    directories = resolve_agent_directories(
        config_path, config.agents.directory, job["agents_directories"]
    )
    agents = load_simulation_agents(
        directories, config.agents.include, job.get("extra_agents")
    )

    def report(turn: int, simulator: DMESimulator) -> None:
        leader = max(
            simulator.teams_state.values(),
            key=lambda state: state.capital if not state.bankrupt else float("-inf"),
        )
        leader_id = next(
            team_id
            for team_id, state in simulator.teams_state.items()
            if state is leader
        )
        job["progress_queue"].put(
            (job["job_id"], turn, leader_id, float(leader.capital))
        )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run_simulation(
            config,
            agents,
            seed_override=job["seed"],
            turns_override=job["turns"],
            quiet=True,
            progress_callback=report,
        )


def _update_progress(progress_queue, bars, jobs: list[dict[str, Any]]) -> None:
    job_by_id = {job["job_id"]: job for job in jobs}
    while True:
        try:
            job_id, turn, leader, capital = progress_queue.get_nowait()
        except Empty:
            return
        if job_id not in bars:
            job = job_by_id[job_id]
            config_name = Path(job["config_path"]).stem
            config = load_config(job["config_path"])
            total_turns = job["turns"] or config.turns
            bars[job_id] = tqdm(
                total=total_turns,
                desc=f"{config_name} seed={job['seed']} | {leader}: ${capital:,.0f}",
                unit="turn",
                position=job_id,
                leave=True,
            )
        bar = bars[job_id]
        bar.n = turn
        bar.set_description(
            f"{Path(job_by_id[job_id]['config_path']).stem} "
            f"seed={job_by_id[job_id]['seed']} | {leader}: ${capital:,.0f}"
        )
        bar.refresh()


def _render_grouped_outputs(
    jobs: list[dict[str, Any]],
    simulations: list[DMESimulator],
    output_directory: Path | None,
) -> None:
    groups: dict[str, list[DMESimulator]] = {}
    for job, simulation in zip(jobs, simulations):
        key = str(Path(job["config_path"]).resolve())
        groups.setdefault(key, []).append(simulation)

    from .visualization import render_plots

    for key, group in groups.items():
        config = group[0].config
        if not config.output.render_plots:
            continue
        target = output_directory or Path(config.output.directory)
        if not target.is_absolute():
            target = Path(key).parent.parent / target
        prefix = f"{Path(key).stem}_{config.output.prefix}"
        if len(group) == 1:
            render_plots(group[0], target, prefix)
            continue
        mean_history, error_history = _aggregate_histories(
            [simulation.history for simulation in group]
        )
        aggregate = copy.copy(group[0])
        aggregate.history = mean_history
        render_plots(aggregate, target, prefix, error_history=error_history)


def _aggregate_histories(
    histories: list[SimulationHistory],
) -> tuple[SimulationHistory, SimulationHistory]:
    """Aggregate on the longest timeline, padding shorter series with zeros."""
    if not histories:
        raise ValueError("at least one history is required")
    longest_turns = max((history.turns for history in histories), key=len)
    turn_count = len(longest_turns)

    def aggregate(values: list[Any], field_name: str = "") -> Any:
        first = values[0]
        if field_name == "turns":
            return copy.deepcopy(longest_turns)
        if is_dataclass(first):
            mean_values = {}
            error_values = {}
            for field in fields(first):
                field_values = [getattr(value, field.name) for value in values]
                if field.name == "turns":
                    mean_values[field.name] = copy.deepcopy(longest_turns)
                    error_values[field.name] = [0.0] * turn_count
                else:
                    mean_values[field.name], error_values[field.name] = aggregate(
                        field_values, field.name
                    )
            return type(first)(**mean_values), type(first)(**error_values)
        if isinstance(first, dict):
            mean_values = {}
            error_values = {}
            for key in first:
                mean_values[key], error_values[key] = aggregate(
                    [value[key] for value in values], field_name
                )
            return mean_values, error_values
        array = np.asarray(
            [
                list(value[:turn_count]) + [0.0] * max(0, turn_count - len(value))
                for value in values
            ],
            dtype=float,
        )
        mean = np.mean(array, axis=0).tolist()
        error = (
            (np.std(array, axis=0, ddof=1) / np.sqrt(len(values))).tolist()
            if len(values) > 1
            else np.zeros_like(array[0]).tolist()
        )
        return mean, error

    mean, error = aggregate(histories)
    return mean, error
