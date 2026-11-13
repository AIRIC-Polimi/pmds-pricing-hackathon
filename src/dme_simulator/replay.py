"""Dashboard snapshots and portable recordings, independent of agent execution."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from functools import cache
from math import isfinite
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from .domain import TurnReport
from .simulation import DMESimulator

FORMAT_VERSION = 1
END_REASONS = {"completed", "viewer_cancelled", "interrupted"}


@dataclass(frozen=True, slots=True)
class DashboardMetadata:
    team_ids: list[str]
    raw_materials: list[str]
    finished_goods: list[str]
    seed: int
    planned_turns: int


@dataclass(frozen=True, slots=True)
class TeamSnapshot:
    capital: float
    inventory: dict[str, int]
    bankrupt: bool


@dataclass(slots=True)
class DashboardFrame:
    turn: int
    teams: dict[str, TeamSnapshot]
    report: TurnReport | None
    total_sold: dict[str, int] = field(init=False, default_factory=dict)


class DashboardTimeline:
    """Owned copies of display data; never restore these into a simulator."""

    def __init__(self, metadata: DashboardMetadata):
        """Create an empty timeline with fixed run metadata and no end status."""
        self.metadata = metadata
        self.frames: list[DashboardFrame] = []
        self.end_reason: str | None = None

    @classmethod
    def from_simulator(
        cls, simulator: DMESimulator, turns: int, seed: int
    ) -> DashboardTimeline:
        """Copy run metadata and capture turn 0 before any decisions resolve."""
        timeline = cls(DashboardMetadata(
            team_ids=list(simulator.team_ids),
            raw_materials=list(simulator.config.goods.raw_materials),
            finished_goods=list(simulator.config.goods.finished_goods),
            seed=seed,
            planned_turns=turns,
        ))
        timeline.capture(simulator, None)
        return timeline

    def capture(
        self, simulator: DMESimulator, report: TurnReport | None
    ) -> DashboardFrame:
        """Copy the current team states and report, append them, and return the frame."""
        # Later engine updates must not change inventories or reports already viewed.
        frame = DashboardFrame(
            turn=simulator.current_turn,
            teams={
                team: TeamSnapshot(state.capital, dict(state.inventory), state.bankrupt)
                for team, state in simulator.teams_state.items()
            },
            report=deepcopy(report),
        )
        self.append(frame)
        return frame

    def append(self, frame: DashboardFrame) -> None:
        """Validate turn order and team membership, then accumulate sales for the frame."""
        if frame.turn != len(self.frames) or frame.turn > self.metadata.planned_turns:
            raise ValueError("snapshot turns must be consecutive, starting at turn 0")
        if set(frame.teams) != set(self.metadata.team_ids):
            raise ValueError("snapshot teams do not match recording metadata")
        if (frame.turn == 0) != (frame.report is None):
            raise ValueError("only turn 0 must have no report")
        if frame.report is not None and frame.report.turn != frame.turn:
            raise ValueError("report turn does not match snapshot turn")
        # Derive totals once per frame so rewinding also rewinds cumulative market share.
        previous = self.frames[-1].total_sold if self.frames else {}
        frame.total_sold = {
            team: previous.get(team, 0) + (
                sum(frame.report.units_sold.get(team, {}).values()) if frame.report else 0
            )
            for team in self.metadata.team_ids
        }
        self.frames.append(frame)

    def capital_delta(self, turn: int, team: str) -> float:
        """Return the selected turn's capital change, or zero for initial turn 0."""
        previous = self.frames[max(0, turn - 1)].teams[team].capital
        return self.frames[turn].teams[team].capital - previous

    def capital_series(self, turn: int) -> dict[str, list[float]]:
        """Return each team's capital history from turn 0 through the selected turn."""
        return {
            team: [frame.teams[team].capital for frame in self.frames[:turn + 1]]
            for team in self.metadata.team_ids
        }


class RecordingWriter:
    """Write one self-contained JSONL recording without overwriting existing files."""

    def __init__(self, path: str | Path, metadata: DashboardMetadata):
        """Exclusively create a recording and immediately write its versioned metadata."""
        self._stream = Path(path).open("x", encoding="utf-8")
        try:
            self._write({"type": "metadata", "version": FORMAT_VERSION, **asdict(metadata)})
        except BaseException:
            self._stream.close()
            raise
        self._last_turn = -1

    def _write(self, record: dict) -> None:
        """Append one JSON record and flush it before the viewer can pause execution."""
        self._stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        self._stream.flush()

    def write_frame(self, frame: DashboardFrame) -> None:
        """Write a snapshot and report, omitting cumulative totals derived on load."""
        self._write({
            "type": "frame", "turn": frame.turn,
            "teams": {team: asdict(state) for team, state in frame.teams.items()},
            "report": asdict(frame.report) if frame.report is not None else None,
        })
        self._last_turn = frame.turn

    def finish(self, reason: str) -> bool:
        """Finalize once; return whether this call wrote the end record."""
        if not self._stream.closed:
            try:
                self._write({"type": "end", "reason": reason, "turn": self._last_turn})
            finally:
                self._stream.close()
            return True
        return False


@cache
def _hints(cls: type) -> dict:
    """Cache resolved dataclass annotations reused while decoding successive frames."""
    return get_type_hints(cls)


def _decode(annotation, value, label: str):
    """Validate the small typed recording schema before it reaches the renderer."""
    args = get_args(annotation)
    origin = get_origin(annotation)
    if type(None) in args:
        if value is None:
            return None
        return _decode(next(item for item in args if item is not type(None)), value, label)
    if is_dataclass(annotation):
        # Derived fields such as total_sold are recomputed when a frame is appended.
        expected = {item.name for item in fields(annotation) if item.init}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError(f"{label}: expected fields {sorted(expected)}")
        return annotation(**{
            name: _decode(_hints(annotation)[name], item, f"{label}.{name}")
            for name, item in value.items()
        })
    if origin is dict:
        if isinstance(value, dict) and all(isinstance(key, str) and key for key in value):
            return {
                key: _decode(args[1], item, f"{label}.{key}")
                for key, item in value.items()
            }
    elif origin is list:
        if isinstance(value, list):
            return [_decode(args[0], item, label) for item in value]
    elif annotation is float:
        if type(value) in (int, float) and isfinite(value):
            return float(value)
    elif annotation is int:
        # JSON booleans must not pass as counts, even though bool subclasses int.
        if type(value) is int:
            return value
    elif annotation is bool:
        if type(value) is bool:
            return value
    elif annotation is str and isinstance(value, str) and value:
        return value
    raise ValueError(f"{label}: invalid value for {annotation}")


def _validate_metadata(metadata: DashboardMetadata) -> None:
    """Check unique identifiers, disjoint goods categories, and a positive run duration."""
    for names in (metadata.team_ids, metadata.raw_materials, metadata.finished_goods):
        if not names or len(names) != len(set(names)):
            raise ValueError("metadata names must be nonempty and unique")
    if set(metadata.raw_materials) & set(metadata.finished_goods):
        raise ValueError("raw materials and finished goods must be distinct")
    if metadata.planned_turns < 1:
        raise ValueError("planned_turns must be positive")


def _validate_frame(frame: DashboardFrame, metadata: DashboardMetadata) -> None:
    """Check inventory quantities and report references against the recording metadata."""
    goods = set(metadata.raw_materials + metadata.finished_goods)
    for state in frame.teams.values():
        if set(state.inventory) != goods or any(q < 0 for q in state.inventory.values()):
            raise ValueError("snapshot inventory must contain nonnegative quantities of all goods")
    if frame.report is None:
        return
    report = frame.report
    materials = set(metadata.raw_materials)
    finished = set(metadata.finished_goods)
    team_fields = {
        "units_won": materials, "produced": finished, "listed": finished,
        "listed_price": finished, "units_sold": finished,
    }
    for name, allowed_goods in team_fields.items():
        values = getattr(report, name)
        if set(values) - set(metadata.team_ids):
            raise ValueError(f"report.{name}: unknown team")
        if any(set(items) - allowed_goods for items in values.values()):
            raise ValueError(f"report.{name}: unknown good")
    for name in ("auction_spend", "revenue"):
        if set(getattr(report, name)) - set(metadata.team_ids):
            raise ValueError(f"report.{name}: unknown team")
    for name, allowed in (
        ("lots", materials), ("clearing_price", materials), ("highest_bid", materials),
        ("stockouts", finished), ("sold_by_good", finished), ("revenue_by_good", finished),
    ):
        if set(getattr(report, name)) - allowed:
            raise ValueError(f"report.{name}: unknown good")

    # All report numbers are counts or prices, unlike a team's signed capital.
    def nonnegative(value):
        """Check every numeric leaf in a nested report mapping for nonnegative values."""
        if isinstance(value, dict):
            return all(nonnegative(item) for item in value.values())
        return value >= 0

    if not nonnegative(asdict(report)):
        raise ValueError("report counts and prices must be nonnegative")


def load_recording(path: str | Path) -> DashboardTimeline:
    """Load display data only; no configuration or agent code is needed."""
    timeline = None
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise TypeError("expected a JSON object")
                kind = record.pop("type", None)
                if timeline is None:
                    if kind != "metadata":
                        raise ValueError("first record must be metadata")
                    version = record.pop("version", None)
                    if type(version) is not int or version != FORMAT_VERSION:
                        raise ValueError(f"unsupported recording version {version!r}")
                    metadata = _decode(DashboardMetadata, record, "metadata")
                    _validate_metadata(metadata)
                    timeline = DashboardTimeline(metadata)
                elif timeline.end_reason is not None:
                    # The final status closes the recording; later frames are invalid.
                    raise ValueError("unexpected data after end record")
                elif kind == "frame":
                    frame = _decode(DashboardFrame, record, "frame")
                    _validate_frame(frame, timeline.metadata)
                    timeline.append(frame)
                elif kind == "end":
                    if (set(record) != {"reason", "turn"}
                            or record["reason"] not in END_REASONS
                            or type(record["turn"]) is not int
                            or record["turn"] != len(timeline.frames) - 1):
                        raise ValueError("invalid end record")
                    timeline.end_reason = record["reason"]
                else:
                    raise ValueError(f"unexpected record type {kind!r}")
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"Invalid recording at line {line_number}: {exc}") from exc
    if timeline is None or not timeline.frames or timeline.end_reason is None:
        raise ValueError("Incomplete recording: metadata, turn 0, and end record are required")
    return timeline
