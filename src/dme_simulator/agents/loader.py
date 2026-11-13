"""Discover agent bundles, load their classes, and construct simulation rosters."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Collection
from os import walk
from pathlib import Path
from uuid import uuid4

from dme_simulator.config import AgentSpec

from .base import BaseAgent


def resolve_agent_directories(
    config_path: Path, configured_directory: str, overrides: list[Path] | None
) -> list[Path]:
    """Resolve CLI roots from cwd, or the YAML default from its project root."""
    if overrides:
        return [path.resolve() for path in overrides]
    directory = Path(configured_directory)
    if not directory.is_absolute():
        directory = config_path.parent.parent / directory
    return [directory.resolve()]


def discover_agent_bundles(directories: list[str | Path]) -> list[Path]:
    """
    Find bundle directories marked by agent.py without importing agent code.

    Search roots may themselves be bundles. A bundle's contents are not searched
    for further bundles; hidden subdirectories and __pycache__ are also skipped.
    Overlapping roots return each resolved bundle path only once.

    Args:
        directories (list): list of directories to search for agent bundles

    Returns:
        list of resolved bundle paths containing agent.py
    """
    bundles: set[Path] = set()
    for directory in directories:
        root = Path(directory).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"agent search directory does not exist: {root}")
        for current, subdirectories, _ in walk(root):
            path = Path(current)
            if (path / "agent.py").is_file():
                bundles.add(path)
                subdirectories.clear()
                continue
            subdirectories[:] = sorted(
                name for name in subdirectories
                if not name.startswith(".") and name != "__pycache__"
            )
    if not bundles:
        raise ValueError("no agent bundles containing agent.py found in search directories")
    return sorted(bundles)


def resolve_extra_agent_paths(paths: list[Path] | None) -> list[Path]:
    """Resolve explicit bundle paths, requiring agent.py in each supplied folder."""
    bundles = sorted({path.resolve() for path in paths or []})
    for bundle in bundles:
        if not (bundle / "agent.py").is_file():
            raise FileNotFoundError(f"extra agent bundle must contain agent.py: {bundle}")
    return bundles


def load_agent_bundle_class(directory: str | Path) -> type[BaseAgent]:
    """
    Load one bundle's agent class, supporting imports relative to its folder.

    Only concrete subclasses defined in agent.py count as entry points. The
    returned class must accept no constructor arguments; it is not instantiated.

    Args:
        directory (str | Path): path to a bundle directory containing agent.py

    Returns:
        type[BaseAgent]: the bundle's concrete BaseAgent subclass
    """
    path = Path(directory).resolve()
    entry_point = path / "agent.py"
    if not entry_point.is_file():
        raise FileNotFoundError(f"agent bundle entry point does not exist: {entry_point}")

    # Treat agent.py as the bundle's package entry point. A fresh namespace keeps
    # helper imports independent even when bundles use identical file/class names.
    module_name = f"_dme_agent_bundle_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name, entry_point, submodule_search_locations=[str(path)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load agent bundle: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    classes = {
        candidate
        for _, candidate in inspect.getmembers(module, inspect.isclass)
        if candidate is not BaseAgent
        and issubclass(candidate, BaseAgent)
        and not inspect.isabstract(candidate)
        and candidate.__module__ == module.__name__
    }
    if len(classes) != 1:
        names = ", ".join(sorted(candidate.__name__ for candidate in classes)) or "none"
        raise ValueError(
            f"{entry_point}: expected exactly one concrete BaseAgent subclass "
            f"defined in agent.py; found: {names}"
        )
    agent_class = next(iter(classes))
    try:
        inspect.signature(agent_class).bind()
    except TypeError as error:
        raise TypeError(
            f"{entry_point}: {agent_class.__name__} must accept no constructor arguments"
        ) from error
    return agent_class


def discover_bundle_classes(
    directories: list[str | Path], *, selected_ids: Collection[str] | None = None
) -> dict[str, type[BaseAgent]]:
    """
    Return bundle IDs mapped to classes, without constructing any agents.

    Folder names are IDs, independently of Python class names. Validate all IDs
    before importing code so duplicate folder names cannot overwrite a bundle.
    When selected_ids is supplied, import only those bundles after checking that
    every requested ID exists. None imports all discovered bundles.

    Args:
        directories (list): list of directories to search for agent bundles
        selected_ids (Collection[str] | None): bundle IDs to load, or None for all

    Returns:
        dict: mapping of bundle IDs to their concrete BaseAgent subclasses
    """
    bundles_by_id: dict[str, Path] = {}
    for bundle in discover_agent_bundles(directories):
        agent_id = bundle.name
        if agent_id in bundles_by_id:
            raise ValueError(
                f"duplicate agent bundle ID {agent_id!r}: "
                f"{bundles_by_id[agent_id]} and {bundle}; "
                "use unique bundle folder names"
            )
        bundles_by_id[agent_id] = bundle
    if selected_ids is not None:
        selected = set(selected_ids)
        unknown = selected.difference(bundles_by_id)
        if unknown:
            missing = ", ".join(repr(agent_id) for agent_id in sorted(unknown))
            available = ", ".join(sorted(bundles_by_id))
            raise ValueError(f"unknown agent IDs: {missing}; available: {available}")
        bundles_by_id = {
            agent_id: bundle
            for agent_id, bundle in bundles_by_id.items()
            if agent_id in selected
        }
    return {
        agent_id: load_agent_bundle_class(bundle)
        for agent_id, bundle in bundles_by_id.items()
    }


def build_agents(
    classes: dict[str, type[BaseAgent]], include: list[AgentSpec]
) -> dict[str, BaseAgent]:
    """Construct all registry entries, or an explicit roster selected by agent ID.

    By default each registry ID is the exact team ID. Explicit roster entries
    use <team_prefix or agent_id>_<index> for their configured copies.

    Args:
        classes (dict): mapping of agent IDs to their concrete BaseAgent subclasses
        include (list): list of AgentSpec objects specifying which agents to include

    Returns:
        dict: mapping of team IDs to constructed BaseAgent instances
    """
    if not include:
        return {agent_id: agent_class() for agent_id, agent_class in classes.items()}
    agents: dict[str, BaseAgent] = {}
    for agent_spec in include:
        if agent_spec.agent_id not in classes:
            available = ", ".join(sorted(classes))
            raise ValueError(
                f"unknown agent ID {agent_spec.agent_id!r}; available: {available}"
            )
        if agent_spec.count < 1:
            raise ValueError(f"{agent_spec.agent_id}: count must be at least 1")
        prefix = agent_spec.team_prefix or agent_spec.agent_id
        for index in range(1, agent_spec.count + 1):
            team_id = f"{prefix}_{index}"
            if team_id in agents:
                raise ValueError(
                    f"duplicate team ID {team_id!r} generated by agents.include; "
                    "use unique team_prefix values"
                )
            agents[team_id] = classes[agent_spec.agent_id](**agent_spec.kwargs)
    return agents


def load_simulation_agents(
    directories: list[Path],
    include: list[AgentSpec],
    extra_agents: list[Path] | None = None,
) -> dict[str, BaseAgent]:
    """Build the selected roster and append one default instance per extra bundle.

    Extras use folder names as team IDs and are independent of include. Repeated
    paths add a bundle once; names conflicting with selected IDs or teams fail.
    """
    extra_paths = resolve_extra_agent_paths(extra_agents)
    classes = discover_bundle_classes(
        directories, selected_ids=[spec.agent_id for spec in include] or None
    )
    agents = build_agents(classes, include)
    for bundle in extra_paths:
        if bundle.name in classes or bundle.name in agents:
            raise ValueError(
                f"extra agent {bundle}: name {bundle.name!r} conflicts with the "
                "selected roster; remove the extra or rename its folder"
            )
    if extra_paths:
        extra_classes = discover_bundle_classes(extra_paths)
        agents.update(build_agents(extra_classes, []))
    return agents
