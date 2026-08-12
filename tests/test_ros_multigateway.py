"""px4_ros2 multi-gateway tests: endpoint resolution semantics and command
routing (each real vehicle runs its own gateway on a separate port)."""

from __future__ import annotations

from src.modules.ros_gateway_controller import RosGatewayController


class _FakeBridgeClient:
    """Records px4_* calls per endpoint."""

    def __init__(self, config) -> None:
        self.config = config
        self.calls: list[str] = []

    def health(self):
        return _Result(True, "ok", {})

    def px4_arm(self, armed: bool):
        self.calls.append(f"arm:{armed}")
        return _Result(True, "ok", {})

    def px4_hold(self, params):
        self.calls.append("hold")
        return _Result(True, "ok", {})

    def px4_set_mode(self, params):
        self.calls.append(f"set_mode:{params.get('mode')}")
        return _Result(True, "ok", {})

    def px4_status(self):
        self.calls.append("status")
        return _Result(True, "ok", {"position_ned": {"x": 1.0, "y": 2.0, "z": -3.0}})


class _Result:
    def __init__(self, ok: bool, message: str, data: dict) -> None:
        self.ok = ok
        self.message = message
        self.status = "ok" if ok else "error"
        self.data = data


def _multi_gateway(monkeypatch) -> RosGatewayController:
    monkeypatch.setattr(
        "src.modules.ros_gateway_controller.RosProviderBridgeClient",
        _FakeBridgeClient,
    )
    return RosGatewayController(
        base_url="",
        endpoints={"drone1": "http://127.0.0.1:8766", "drone2": "http://127.0.0.1:8767"},
    )


def test_list_vehicles_reports_each_gateway(monkeypatch) -> None:
    controller = _multi_gateway(monkeypatch)
    assert controller.list_vehicles() == ["px4_ros2_drone1", "px4_ros2_drone2"]


def test_default_targets_first_endpoint_only(monkeypatch) -> None:
    controller = _multi_gateway(monkeypatch)
    assert controller.arm("") is True
    drone1 = controller._endpoints["drone1"]
    drone2 = controller._endpoints["drone2"]
    assert drone1.calls == ["arm:True"]
    assert drone2.calls == []


def test_all_targets_every_endpoint(monkeypatch) -> None:
    controller = _multi_gateway(monkeypatch)
    assert controller.arm("all") is True
    assert controller._endpoints["drone1"].calls == ["arm:True"]
    assert controller._endpoints["drone2"].calls == ["arm:True"]


def test_named_target_resolves_single_endpoint(monkeypatch) -> None:
    controller = _multi_gateway(monkeypatch)
    assert controller.set_mode("OFFBOARD", vehicle_name="drone2") is True
    assert controller._endpoints["drone1"].calls == []
    assert controller._endpoints["drone2"].calls == ["set_mode:OFFBOARD"]


def test_get_status_reads_default_endpoint(monkeypatch) -> None:
    controller = _multi_gateway(monkeypatch)
    status = controller.get_status("")
    assert status.position_ned["x"] == 1.0
    assert controller._endpoints["drone1"].calls == ["status"]
    assert controller._endpoints["drone2"].calls == []


def test_single_endpoint_behaves_like_before(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.modules.ros_gateway_controller.RosProviderBridgeClient",
        _FakeBridgeClient,
    )
    controller = RosGatewayController(base_url="http://127.0.0.1:8766")
    assert controller.list_vehicles() == ["px4_ros2"]
    assert controller.arm("") is True
    assert controller.client.calls == ["arm:True"]
