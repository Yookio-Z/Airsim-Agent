"""Shared shells for the AgentRuntime / ToolRuntime test doubles.

Why this module exists
----------------------
Several test files used to hand-inject a runtime's attributes (one shell set 25
of them, all with `object.__new__`). Adding one required attribute to the runtime
then broke several files at once, each failing with an AttributeError raised from
deep inside the code under test — a message that says nothing about what changed.
The refactor that made this module possible is `AgentRuntime.__init__` being split
into `_init_runtime_state()` (pure fields, no injected objects, no side effects)
and `_wire_callbacks()` (wires methods onto injected dependencies), so building a
stub is now:

    runtime = object.__new__(AgentRuntime)
    runtime._init_runtime_state()
    # then only override what this particular test is about

`agent_runtime()` below does exactly that plus a set of inert defaults, and
`tool_runtime()` does the same for ToolRuntime.

Both raise AttributeError on an unknown override name on purpose: a typo in a
test must not silently leave a real dependency in place.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any

from src.agent.runtime import AgentRuntime
from src.agent.tool_executor import ToolCollector, ToolRuntime
from src.modules.safety_validator import FlightConstraint, SafetyValidator


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def status_controller(
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = -3.0,
    armed: bool = True,
    flying: bool = True,
    gps: dict[str, float] | None = None,
    connected: bool = True,
) -> SimpleNamespace:
    """Minimal connected flight-controller stub.

    `gps` is what the no-fly-zone and global-mission checks read; leave it None
    to model "no fix".
    """
    return SimpleNamespace(
        is_connected=connected,
        get_status=lambda vehicle_name="": SimpleNamespace(
            position_ned={"x": x, "y": y, "z": z},
            velocity_ned={"vx": 0.0, "vy": 0.0, "vz": 0.0},
            armed=armed,
            flying=flying,
            gps=gps,
            extra={},
        ),
    )


def tool_runtime(
    collector: ToolCollector | None = None,
    controller: Any = None,
    **overrides: Any,
) -> ToolRuntime:
    """ToolRuntime shell with a lock, a safety validator and inert defaults.

    Supersedes the three per-file copies that used to exist in
    test_tool_errors.py, test_formation_contract.py and
    test_safety_and_cancel_guards.py.
    """
    runtime = object.__new__(ToolRuntime)
    defaults: dict[str, Any] = {
        "backend_id": "fake",
        "collector": collector if collector is not None else ToolCollector(),
        "controller": controller,
        "camera_controller": None,
        "perception_axis": None,
        "vlm_provider": None,
        "backend_profile": None,
        "available": True,
        "init_error": "",
        "_lock": threading.RLock(),
        "_camera_lock": threading.RLock(),
        "_control_gate": threading.RLock(),
        "_real_vehicle": False,
        "_last_connect_params": {},
        "safety": SafetyValidator(
            FlightConstraint(
                max_altitude=50.0,
                min_altitude=0.5,
                max_velocity=8.0,
                max_distance_from_home=100.0,
            )
        ),
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(runtime, key, value)
    # Methods a stub must answer, unless the test overrode them explicitly.
    if "ensure_ready" not in overrides:
        runtime.ensure_ready = lambda: True  # type: ignore[method-assign]
    if "_camera_source_enabled" not in overrides:
        runtime._camera_source_enabled = lambda: False  # type: ignore[method-assign]
    return runtime


def agent_runtime(
    *,
    tools: Any | None = None,
    supervisor: Any | None = None,
    planner: Any | None = None,
    memory: Any | None = None,
    **overrides: Any,
) -> AgentRuntime:
    """AgentRuntime shell: every runtime field initialised, deps inert.

    The defaults are deliberately dumb (no flight control, no pause, nothing
    known) so a test states only the behaviour it cares about.
    """
    runtime = object.__new__(AgentRuntime)
    runtime._init_runtime_state()
    defaults: dict[str, Any] = {
        "tools": tools if tools is not None else tool_runtime(),
        "supervisor": supervisor
        if supervisor is not None
        else SimpleNamespace(
            should_pause=lambda: False,
            is_emergency_stopped=lambda: False,
            pause=_noop,
            resume=_noop,
            emergency_stop=_noop,
            reset_emergency=_noop,
        ),
        "planner": planner
        if planner is not None
        else SimpleNamespace(
            _is_close_range_visual_command=lambda command: False,
            last_reasoning="",
        ),
        "memory": memory
        if memory is not None
        else SimpleNamespace(
            snapshot=lambda: {},
            recall=lambda query: [],
            remember_tool_call=_noop,
            remember_mission=_noop,
        ),
        "task_runs": None,
        "skills": SimpleNamespace(guidance_cards=lambda *a, **k: [], execute=_noop),
        "rule_planner": None,
    }
    for key, value in {**defaults, **overrides}.items():
        if not hasattr(runtime, key) and key not in defaults:
            raise AttributeError(
                f"agent_runtime() got an unexpected override {key!r}; it is not a "
                "field of AgentRuntime and would have been silently ignored"
            )
        setattr(runtime, key, value)
    return runtime


def tool_result(
    tool: str,
    params: dict[str, Any] | None = None,
    *,
    ok: bool = True,
    data: dict[str, Any] | None = None,
    error_code: str = "",
    terminal: bool = True,
    task_id: str = "",
) -> Any:
    """ToolCallResult with the timestamps filled in."""
    import time

    from src.agent.tool_executor import ToolCallResult

    now = time.time()
    return ToolCallResult(
        tool,
        dict(params or {}),
        ok,
        dict(data or {"status": "ok"}),
        now,
        now,
        error_code=error_code,
        terminal=terminal,
        task_id=task_id,
    )


def json_tool(payload: dict[str, Any] | str = {"status": "ok"}):
    """A registered-tool callable returning a JSON string, like the real ones."""

    def _call(**_kwargs: Any) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    return _call
