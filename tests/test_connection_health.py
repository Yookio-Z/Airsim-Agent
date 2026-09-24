import copy
import math
import time
import struct
from types import SimpleNamespace

from pymavlink import mavutil

from src.agent import runtime as runtime_module
from src.agent.runtime import _build_connect_params, _connection_settings
from src.agent.tool_executor import ToolCollector, ToolRuntime
from src.modules.flight_controller import DroneStatus
from src.modules.mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from src.modules.mavlink_controller import MavlinkController
from src.tools.core import register_core_tools


class _StaleController:
    backend_name = "mavlink"
    is_connected = True

    def get_status(self):
        return self.get_cached_status()

    def get_cached_status(self):
        return DroneStatus(extra={"link_stale": True, "heartbeat_age_s": 12.5})


class _YawOnlyController:
    backend_name = "px4_ros2"

    def __init__(self):
        self.target = None

    def get_status(self, vehicle_name: str = ""):
        return DroneStatus(
            position_ned={"x": 1.0, "y": 2.0, "z": -3.0},
            attitude_rad={"roll": 0.0, "pitch": 0.0, "yaw": math.pi / 2.0},
            extra={},
        )

    def move_to_position(self, x, y, z, velocity=2.0, vehicle_name: str = ""):
        self.target = {"x": x, "y": y, "z": z, "velocity": velocity}
        return True


def _stale_runtime() -> ToolRuntime:
    runtime = ToolRuntime(backend_id="px4_mavlink")
    runtime.backend_profile = runtime.backend_registry.require("px4_mavlink")
    runtime.controller = _StaleController()
    runtime.collector = ToolCollector()
    runtime.collector.tools["drone_arm"] = lambda: '{"status":"ok"}'
    runtime.available = True
    return runtime


def test_status_snapshot_marks_stale_px4_heartbeat_offline():
    snapshot = _stale_runtime().status_snapshot()

    assert snapshot["connected"] is False
    assert snapshot["stale_connection"] is True
    assert snapshot["drone"]["heartbeat_age_s"] == 12.5


def test_core_relative_move_uses_attitude_yaw_when_heading_field_is_missing():
    controller = _YawOnlyController()
    collector = ToolCollector()
    register_core_tools(collector, controller, lambda data: data)

    result = collector.tools["drone_move_relative"](forward_m=3.0, right_m=0.0, up_m=0.0, velocity=1.5)

    assert result["status"] == "ok"
    assert result["heading_deg"] == 90.0
    assert math.isclose(controller.target["x"], 1.0, abs_tol=1e-6)
    assert math.isclose(controller.target["y"], 5.0, abs_tol=1e-6)
    assert math.isclose(controller.target["z"], -3.0, abs_tol=1e-6)


def test_control_tool_is_blocked_before_sending_on_stale_px4_link():
    result = _stale_runtime().execute("drone_arm", {}, allow_reconnect=False)

    assert result.ok is False
    assert result.data["connection_error"] == "stale MAVLink heartbeat"
    assert "last heartbeat 12.5s ago" in result.data["message"]


def test_mavlink_arm_reports_stale_heartbeat_reason():
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = object()
    controller._last_heartbeat = time.time() - 8.0

    assert controller.arm() is False
    assert "heartbeat is lost" in controller.last_error


def test_command_ack_rejection_keeps_mav_result_diagnostic():
    command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM

    class _Ack:
        def get_type(self):
            return "COMMAND_ACK"

        def to_dict(self):
            return {
                "command": command,
                "result": mavutil.mavlink.MAV_RESULT_DENIED,
                "result_param2": 0,
            }

    class _Link:
        def __init__(self):
            self.message = _Ack()

        def recv_match(self, **_kwargs):
            message, self.message = self.message, None
            return message

    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    controller._last_heartbeat = time.time()

    assert controller._wait_command_ack(command, timeout=0.1) is False
    assert "MAV_RESULT_DENIED" in controller.last_error


def test_qgc_usb_board_info_detects_px4_fmu_v6u(monkeypatch):
    port = SimpleNamespace(
        device="COM3",
        name="COM3",
        description="USB Serial Device (COM3)",
        hwid="USB VID:PID=1B8C:0036 SER=0",
        vid=7052,
        pid=54,
        manufacturer="Microsoft",
        product=None,
        serial_number="0",
    )

    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [port])

    candidates = discover_serial_mavlink_candidates()

    assert candidates
    assert candidates[0].url == "serial:COM3:115200"
    assert candidates[0].board_type == "Pixhawk"
    assert candidates[0].board_name == "PX4 FMU V6U"


def test_serial_baud_port_mixup_is_normalized():
    assert normalize_serial_baud("14550") == 115200

    backend, params = _build_connect_params({
        "type": "serial",
        "params": {"port": "COM3", "baud": "14550"},
    })

    assert backend == "px4_mavlink"
    assert params["url"] == "serial:COM3:115200"
    assert params["real_vehicle"] is True


def test_px4_backend_falls_back_to_a_compatible_default_preset():
    """A stale active id pointing at another backend must be replaced by a
    px4_mavlink-compatible preset.

    The shipped defaults are AirSim + PX4 SITL UDP + ROS2 (a serial preset is
    added by the operator when needed), so SITL UDP is the only compatible
    candidate here.
    """
    settings = {
        "backend": "px4_mavlink",
        "connections": {
            "active_connection_id": "default_px4_ros2",
        },
    }

    merged = _connection_settings(settings)

    assert merged["active_connection_id"] == "default_px4_sitl_udp"


def test_px4_backend_prefers_a_real_vehicle_link_over_the_sitl_default():
    """When both a SITL UDP endpoint and a real-vehicle link exist, the real one
    wins: the SITL default is 127.0.0.1, which on a real deployment either
    connects to nothing or to a local simulator instead of the aircraft.

    This used to be expressed as a hard-coded preset-id whitelist
    (default_px4_auto / default_px4_usb) that nothing ever created, so the
    preference never had any effect; an operator-added serial preset must win.
    """
    settings = {
        "backend": "px4_mavlink",
        "connections": {
            "active_connection_id": "default_px4_ros2",
            "connections": [
                {
                    "id": "default_px4_sitl_udp",
                    "name": "PX4 SITL UDP",
                    "type": "udp",
                    "params": {"host": "127.0.0.1", "portNumber": "14550"},
                },
                {
                    "id": "my_usb_link",
                    "name": "Pixhawk USB",
                    "type": "serial",
                    "params": {"port": "COM7", "baud": "115200"},
                },
            ],
        },
    }

    merged = _connection_settings(settings)

    assert merged["active_connection_id"] == "my_usb_link"


def test_px4_backend_keeps_an_explicitly_chosen_sitl_link():
    """The real-vehicle preference must not override an explicit choice."""
    settings = {
        "backend": "px4_mavlink",
        "connections": {
            "active_connection_id": "default_px4_sitl_udp",
            "connections": [
                {
                    "id": "default_px4_sitl_udp",
                    "name": "PX4 SITL UDP",
                    "type": "udp",
                    "params": {"host": "127.0.0.1", "portNumber": "14550"},
                },
                {
                    "id": "my_usb_link",
                    "name": "Pixhawk USB",
                    "type": "serial",
                    "params": {"port": "COM7", "baud": "115200"},
                },
            ],
        },
    }

    merged = _connection_settings(settings)

    assert merged["active_connection_id"] == "default_px4_sitl_udp"


def test_auto_link_builds_serial_first_with_udp_fallback(monkeypatch):
    port = SimpleNamespace(
        device="COM3",
        name="COM3",
        description="USB Serial Device (COM3)",
        hwid="USB VID:PID=1B8C:0036 SER=0",
        vid=7052,
        pid=54,
        manufacturer="Microsoft",
        product=None,
        serial_number="0",
    )
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [port])

    backend, params = _build_connect_params({
        "type": "auto",
        "params": {"host": "127.0.0.1", "portNumber": "14550", "remotePort": "18570"},
    })
    controller = MavlinkController()

    assert backend == "px4_mavlink"
    assert params["url"] == "auto:"
    assert params["fallback_url"] == "udp:127.0.0.1:14550"
    assert controller._candidate_urls(params["url"], fallback_url=params["fallback_url"])[0] == "serial:COM3:115200"


def test_autopilot_version_decoder_matches_qgc_byte_layout():
    raw_version = (1 << 24) | (17 << 16) | (0 << 8) | 255
    controller = MavlinkController()

    decoded = controller._decode_autopilot_version({
        "flight_sw_version": raw_version,
        "flight_custom_version": [0x00, 0x11, 0x01, 1, 2, 3, 4, 5],
        "capabilities": mavutil.mavlink.MAV_PROTOCOL_CAPABILITY_MAVLINK2,
        "uid": 123,
        "vendor_id": 7052,
        "product_id": 54,
    })

    assert decoded["flight_version"]["text"] == "1.17.0"
    assert decoded["px4_custom_version"]["text"] == "1.17.0"
    assert decoded["git_hash"] == "0504030201011100"
    assert "MAVLINK2" in decoded["capability_flags"]


def test_param_value_cache_decodes_bytewise_int32():
    # 16_777_217 = 2**24 + 1 is the smallest int32 that float32 cannot represent
    # exactly, so a plain int(float_field) would come out as 16_777_216. Decoding
    # a value like 1 would pass either way and prove nothing.
    raw_int = 16_777_217

    class _ParamValue:
        def get_type(self):
            return "PARAM_VALUE"

        def get_srcSystem(self):
            return 1

        def get_srcComponent(self):
            return 1

        def to_dict(self):
            return {
                "param_id": "COM_ARM_WO_GPS\x00\x00",
                "param_value": struct.unpack("<f", struct.pack("<i", raw_int))[0],
                "param_type": mavutil.mavlink.MAV_PARAM_TYPE_INT32,
                "param_count": 1,
                "param_index": 0,
            }

    class _Link:
        def recv_match(self, **_kwargs):
            return None

    controller = MavlinkController()
    controller._connected = True
    # is_connected() requires a fresh heartbeat: the _connected flag alone is
    # treated as a stale belief and gets re-probed, which is what this test
    # used to trip over (the parameter snapshot then reported "disconnected").
    controller._last_heartbeat = time.time()
    controller._mavlink = _Link()
    controller._handle_message(_ParamValue())

    status = controller.get_parameter_status()
    params = controller.get_parameters(limit=10)

    assert status["ready"] is True
    assert status["received_count"] == 1
    assert params["parameters"][0]["name"] == "COM_ARM_WO_GPS"
    # exact int32 round-trip: no float32 truncation, null padding stripped
    assert params["parameters"][0]["value"] == raw_int
    assert params["parameters"][0]["type_name"] == "MAV_PARAM_TYPE_INT32"


def test_firmware_info_tool_is_registered_as_read_only():
    class _FirmwareController:
        backend_name = "mavlink"

        def get_firmware_info(self, force=False):
            return {
                "status": "ok",
                "flight_version": {"text": "1.17.0"},
                "force": force,
            }

    collector = ToolCollector()
    register_core_tools(collector, _FirmwareController(), lambda data: data)

    result = collector.tools["drone_get_firmware_info"](force=True)

    assert result["status"] == "ok"
    assert result["flight_version"]["text"] == "1.17.0"
    assert result["force"] is True


def test_parameters_tool_is_registered_as_read_only():
    class _ParameterController:
        backend_name = "mavlink"

        def get_parameters(self, refresh=False, query="", limit=50, offset=0, timeout=20.0):
            return {
                "status": "ready",
                "refresh": refresh,
                "query": query,
                "limit": limit,
                "offset": offset,
                "timeout": timeout,
                "received_count": 1,
                "expected_count": 1,
                "parameters": [{"name": "BAT_N_CELLS", "value": 4}],
            }

    collector = ToolCollector()
    register_core_tools(collector, _ParameterController(), lambda data: data)

    result = collector.tools["drone_get_parameters"](query="BAT", limit=10, refresh=True)

    assert "drone_get_parameters" in ToolRuntime.READ_ONLY_TOOLS
    assert result["status"] == "ready"
    assert result["query"] == "BAT"
    assert result["parameters"][0]["name"] == "BAT_N_CELLS"


def test_vehicle_setup_snapshot_exposes_qgc_style_diagnostics():
    class _Link:
        target_system = 1
        target_component = 1
        source_system = 255
        source_component = 190

        def recv_match(self, **_kwargs):
            return None

    class _Message:
        def __init__(self, msg_type, data):
            self._msg_type = msg_type
            self._data = data

        def get_type(self):
            return self._msg_type

        def get_srcSystem(self):
            return 1

        def get_srcComponent(self):
            return 1

        def to_dict(self):
            return dict(self._data)

    sensor_mask = (
        mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_GYRO
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_ACCEL
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_MAG
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_RC_RECEIVER
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS
        | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_BATTERY
    )
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    controller._last_heartbeat = time.time()
    controller._real_vehicle = True
    controller._active_connection_details = {"url": "serial:COM3:115200", "system_id": 1, "component_id": 1}
    controller._handle_message(_Message("HEARTBEAT", {
        "autopilot": 8,
        "type": 2,
        "base_mode": 129,
        "custom_mode": 4,
        "system_status": 4,
    }))
    controller._handle_message(_Message("SYS_STATUS", {
        "onboard_control_sensors_present": sensor_mask,
        "onboard_control_sensors_enabled": sensor_mask,
        "onboard_control_sensors_health": sensor_mask,
        "voltage_battery": 15300,
        "current_battery": 120,
        "battery_remaining": 78,
    }))
    controller._handle_message(_Message("ATTITUDE", {
        "roll": 0.1,
        "pitch": -0.05,
        "yaw": 1.2,
        "rollspeed": 0.2,
        "pitchspeed": -0.1,
        "yawspeed": 0.0,
    }))
    controller._handle_message(_Message("RC_CHANNELS", {"chan1_raw": 1500, "chan2_raw": 1600, "rssi": 180}))
    controller._handle_message(_Message("SERVO_OUTPUT_RAW", {"servo1_raw": 1000, "servo2_raw": 1100}))
    controller._handle_message(_Message("PARAM_VALUE", {
        "param_id": "MC_AIRMODE",
        "param_value": 2.0,
        "param_type": mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
        "param_count": 1,
        "param_index": 0,
    }))

    snapshot = controller.get_vehicle_setup_snapshot(history_limit=10)

    assert snapshot["connected"] is True
    assert snapshot["summary"]["sensors"]["gyro"] == "ready"
    assert snapshot["telemetry"]["sensor_health"]["items"]["accel"]["healthy"] is True
    assert snapshot["summary"]["radio"]["channels"] == 2
    assert snapshot["summary"]["actuators"]["outputs"] == 2
    assert snapshot["summary"]["power"]["voltage"] == 15.3
    assert snapshot["parameter_highlights"]["MC_AIRMODE"] == "2"
    assert snapshot["history"]["rate"][0]["roll"] > 0


def test_vehicle_telemetry_snapshot_is_lightweight_live_subset():
    class _Link:
        target_system = 1
        target_component = 1
        source_system = 255
        source_component = 190

        def recv_match(self, **_kwargs):
            return None

    class _Message:
        def __init__(self, msg_type, data):
            self._msg_type = msg_type
            self._data = data

        def get_type(self):
            return self._msg_type

        def get_srcSystem(self):
            return 1

        def get_srcComponent(self):
            return 1

        def to_dict(self):
            return dict(self._data)

    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = _Link()
    controller._last_heartbeat = time.time()
    controller._active_connection_details = {"url": "serial:COM3:115200", "system_id": 1, "component_id": 1}
    controller._handle_message(_Message("HEARTBEAT", {
        "autopilot": 8,
        "type": 2,
        "base_mode": 129,
        "custom_mode": 4,
        "system_status": 4,
    }))
    controller._handle_message(_Message("ATTITUDE", {
        "roll": 0.1,
        "pitch": -0.05,
        "yaw": 1.2,
        "rollspeed": 0.2,
        "pitchspeed": -0.1,
        "yawspeed": 0.0,
    }))
    controller._handle_message(_Message("HIGHRES_IMU", {
        "xacc": 0.01,
        "yacc": 0.02,
        "zacc": 9.81,
        "xgyro": 0.1,
        "ygyro": 0.2,
        "zgyro": 0.3,
        "xmag": 0.001,
        "ymag": 0.002,
        "zmag": 0.003,
    }))
    controller._handle_message(_Message("BATTERY_STATUS", {
        "voltages": [4200, 4200, 4200, 4200, 65535],
        "current_battery": 123,
        "battery_remaining": 79,
    }))

    snapshot = controller.get_vehicle_telemetry_snapshot(history_limit=5)

    assert snapshot["connected"] is True
    assert snapshot["backend"] == "px4_mavlink"
    assert snapshot["telemetry"]["attitude"]["roll_deg"] > 0
    assert snapshot["telemetry"]["imu"]["unit"] == "m/s^2 / rad/s / gauss"
    assert snapshot["telemetry"]["battery"]["voltage"] == 16.8
    assert len(snapshot["history"]["imu"]) == 1
    assert "parameters" not in snapshot


def test_set_vehicle_parameter_sends_param_set_and_updates_cache():
    class _MavSender:
        def __init__(self, link):
            self.link = link

        def param_set_send(self, target_system, target_component, param_id, param_value, param_type):
            self.link.sent = {
                "target_system": target_system,
                "target_component": target_component,
                "param_id": param_id,
                "param_value": param_value,
                "param_type": param_type,
            }

    class _Link:
        target_system = 1
        target_component = 1
        source_system = 255
        source_component = 190

        def __init__(self):
            self.sent = None
            self.mav = _MavSender(self)
            self.messages = []

        def recv_match(self, **_kwargs):
            return self.messages.pop(0) if self.messages else None

    class _Message:
        def __init__(self, msg_type, data):
            self._msg_type = msg_type
            self._data = data

        def get_type(self):
            return self._msg_type

        def get_srcSystem(self):
            return 1

        def get_srcComponent(self):
            return 1

        def to_dict(self):
            return dict(self._data)

    link = _Link()
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = link
    controller._last_heartbeat = time.time()
    controller._handle_message(_Message("PARAM_VALUE", {
        "param_id": "MC_AIRMODE",
        "param_value": 0.0,
        "param_type": mavutil.mavlink.MAV_PARAM_TYPE_UINT8,
        "param_count": 1,
        "param_index": 0,
    }))
    link.messages.append(_Message("PARAM_VALUE", {
        "param_id": "MC_AIRMODE",
        "param_value": 2.0,
        "param_type": mavutil.mavlink.MAV_PARAM_TYPE_UINT8,
        "param_count": 1,
        "param_index": 0,
    }))

    result = controller.set_parameter("MC_AIRMODE", "2", timeout=1.0)

    assert result["status"] == "ok"
    assert link.sent["target_system"] == 1
    assert link.sent["target_component"] == 1
    assert link.sent["param_id"] == b"MC_AIRMODE"
    assert link.sent["param_value"] == 2.0
    assert result["parameter"]["value"] == 2
    assert controller.get_parameter_status()["received_count"] == 1


# ---------------------------------------------------------------------------
# 连接如实上报：监听模式（udpin:0.0.0.0）会接收任意来源的心跳，
# "已连接"不等于"连上了配置里那台设备"。
# ---------------------------------------------------------------------------


def _listening_controller(**details_overrides):
    controller = MavlinkController()
    details = {
        "url": "udpin:0.0.0.0:14550",
        "local_listen_url": "udpin:0.0.0.0:14550",
        "px4_remote_host": "192.168.137.217",
        "px4_remote_port": 18570,
        "px4_remote_endpoint": "192.168.137.217:18570",
    }
    details.update(details_overrides)
    controller._active_connection_details = details
    return controller


def test_listening_link_reports_real_heartbeat_source_not_configured_target():
    """配置的是 192.168.137.217，实际收到本机 WSL 的心跳时不能谎报配置地址。"""
    controller = _listening_controller()
    controller._mavlink = SimpleNamespace(
        udp_server=True,
        clients_last_alive={("192.168.137.1", 14550): time.time()},
    )
    controller._note_rx_peer()

    info = controller.get_connection_info()

    assert info["actual_peer_endpoint"] == "192.168.137.1:14550"
    assert info["peer_source_verified"] is True
    assert info["configured_peer_endpoint"] == "192.168.137.217:18570"
    assert info["peer_mismatch"] is True
    assert "192.168.137.217:18570" in info["peer_mismatch_note"]
    assert "192.168.137.1:14550" in info["peer_mismatch_note"]


def test_matching_source_is_not_flagged_as_mismatch():
    controller = _listening_controller()
    controller._peer_observations = {("192.168.137.217", 18570): time.time()}

    info = controller.get_connection_info()

    assert info["peer_mismatch"] is False
    assert info["peer_mismatch_note"] == ""
    assert info["actual_peer_endpoint"] == "192.168.137.217:18570"


def test_observed_source_on_same_host_but_other_port_is_a_mismatch():
    controller = _listening_controller()
    controller._peer_observations = {("192.168.137.217", 14550): time.time()}

    info = controller.get_connection_info()

    assert info["peer_mismatch"] is True


def test_unverified_send_target_is_not_reported_as_mismatch():
    """udpout 拿不到来源时，退回发送目标只是推断，不能据此报警。"""
    controller = _listening_controller()
    controller._mavlink = SimpleNamespace(
        udp_server=False,
        destination_addr=("10.0.0.5", 14550),
    )
    controller._note_rx_peer()

    info = controller.get_connection_info()

    assert info["actual_peer_endpoint"] == "10.0.0.5:14550"
    assert info["peer_source_verified"] is False
    assert info["peer_mismatch"] is False


def test_note_rx_peer_keeps_latest_udp_server_client():
    controller = MavlinkController()
    controller._mavlink = SimpleNamespace(
        udp_server=True,
        clients_last_alive={
            ("10.0.0.9", 18570): 100.0,
            ("192.168.137.1", 14550): 200.0,
        },
    )

    controller._note_rx_peer()

    assert controller._rx_peer == ("192.168.137.1", 14550)
    assert controller._peer_observations[("192.168.137.1", 14550)] == controller._rx_peer_ts


def test_configured_host_with_embedded_url_and_port_still_matches():
    controller = _listening_controller(
        px4_remote_host="udp:192.168.137.217:14550",
        px4_remote_port=0,
        px4_remote_endpoint="",
    )
    controller._peer_observations = {("192.168.137.217", 14550): time.time()}

    info = controller.get_connection_info()

    assert info["peer_mismatch"] is False


def test_disconnect_clears_observed_peer():
    controller = _listening_controller()
    controller._peer_observations = {("192.168.137.1", 14550): time.time()}
    controller._rx_peer = ("192.168.137.1", 14550)
    controller._rx_peer_ts = time.time()

    controller.disconnect()

    assert controller._peer_observations == {}
    assert controller._rx_peer is None
    assert controller.get_connection_info().get("actual_peer_endpoint") is None


# ---------------------------------------------------------------------------
# 断开链路：面板按真实端点识别出的活动链路未必等于 settings 里记的
# active_connection_id，所以断开需要一条不依赖该 id 的显式路径。
# ---------------------------------------------------------------------------

_WSL_LINK = {
    "id": "px4_wsl",
    "name": "PX4 WSL",
    "type": "udp",
    "params": {"host": "127.0.0.1", "portNumber": "14550", "remotePort": "18570"},
}


def _runtime_with_stored_settings(monkeypatch, stored):
    monkeypatch.setattr(runtime_module, "_load_settings", lambda: copy.deepcopy(stored))
    monkeypatch.setattr(
        runtime_module,
        "_save_settings",
        lambda data: stored.clear() or stored.update(copy.deepcopy(data)),
    )

    class _Tools:
        def __init__(self):
            self.calls = []

        def execute(self, name, params, **_kwargs):
            self.calls.append(name)
            return SimpleNamespace(ok=True, to_dict=lambda: {"status": "ok", "tool": name})

    runtime = object.__new__(runtime_module.AgentRuntime)
    runtime.tools = _Tools()
    monkeypatch.setattr(runtime, "_append_event", lambda *args, **kwargs: None)
    return runtime


def test_deactivate_connection_disconnects_and_clears_active_id(monkeypatch):
    stored = {
        "backend": "px4_mavlink",
        "connections": {"active_connection_id": "px4_jetson", "connections": [_WSL_LINK]},
    }
    runtime = _runtime_with_stored_settings(monkeypatch, stored)

    result = runtime.deactivate_connection()

    assert result["ok"] is True
    assert result["action"] == "disconnect"
    assert runtime.tools.calls == ["drone_disconnect"]
    assert stored["connections"]["active_connection_id"] == ""


def test_activate_connection_disconnects_when_it_is_the_remembered_link(monkeypatch):
    stored = {
        "backend": "px4_mavlink",
        "connections": {"active_connection_id": "px4_wsl", "connections": [_WSL_LINK]},
    }
    runtime = _runtime_with_stored_settings(monkeypatch, stored)
    runtime.tools.status_snapshot = lambda: {"backend": "px4_mavlink", "connected": True, "stale_connection": False}

    result = runtime.activate_connection("px4_wsl")

    assert result["action"] == "disconnect"
    assert runtime.tools.calls == ["drone_disconnect"]


_JETSON_LINK = {
    "id": "px4_jetson",
    "name": "PX4 JETSON",
    "type": "udp",
    "params": {"host": "192.168.137.217", "portNumber": "14550", "remotePort": "18570"},
}


def test_activate_connection_switches_when_another_link_is_remembered(monkeypatch):
    stored = {
        "backend": "px4_mavlink",
        "connections": {
            "active_connection_id": "px4_jetson",
            "connections": [_JETSON_LINK, _WSL_LINK],
        },
    }
    runtime = _runtime_with_stored_settings(monkeypatch, stored)
    runtime.tools.status_snapshot = lambda: {"backend": "px4_mavlink", "connected": True, "stale_connection": False}
    switched = {}
    monkeypatch.setattr(
        runtime,
        "set_backend",
        lambda backend, connect_params=None, connection_id="": switched.update(
            {"backend": backend, "params": connect_params, "connection_id": connection_id}
        )
        or {"ok": True},
    )

    result = runtime.activate_connection("px4_wsl")

    assert result == {"ok": True}
    assert runtime.tools.calls == []
    assert switched["connection_id"] == "px4_wsl"
    assert switched["params"]["url"] == "udp:127.0.0.1:14550"
    assert stored["connections"]["active_connection_id"] == "px4_wsl"


# ---------------------------------------------------------------------------
# 监听口收不到心跳时要说清"端口被别的进程占了"：老实例没退出会悄悄收走
# 全部心跳，报出来却只是"no MAVLink heartbeat received"，看起来像 PX4 没起来。
# ---------------------------------------------------------------------------


def test_local_port_conflict_hint_detects_squatter():
    import socket

    squatter = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    squatter.bind(("127.0.0.1", 0))
    port = squatter.getsockname()[1]
    controller = MavlinkController()
    try:
        hint = controller._local_port_conflict_hint(f"udpin:0.0.0.0:{port}")
    finally:
        squatter.close()

    assert "已被其他进程占用" in hint
    assert str(port) in hint
    # 释放后不应再报占用
    assert controller._local_port_conflict_hint(f"udpin:0.0.0.0:{port}") == ""


def test_local_port_conflict_hint_ignores_non_listen_candidates():
    controller = MavlinkController()

    assert controller._local_port_conflict_hint("udpout:127.0.0.1:14550") == ""
    assert controller._local_port_conflict_hint("serial:COM3:115200") == ""
    assert controller._local_port_conflict_hint("udpin:0.0.0.0:not-a-port") == ""
