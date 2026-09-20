import time
from types import SimpleNamespace
from unittest.mock import Mock

from pymavlink import mavutil

from src.modules.mavlink_controller import MavlinkController


def test_takeoff_temporary_rejection_returns_failure_without_timeout(monkeypatch):
    controller = MavlinkController()
    controller._connected = True
    controller._mavlink = SimpleNamespace(target_system=1, target_component=1)
    controller._selected_sysid = 1
    controller._system_table(1)
    controller._last_heartbeat = time.time()
    command = mavutil.mavlink.MAV_CMD_NAV_TAKEOFF

    def ack(cmd, result):
        return SimpleNamespace(
            get_type=lambda: 'COMMAND_ACK',
            to_dict=lambda: {'command': cmd, 'result': result, 'result_param2': 0},
        )

    unrelated = ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, mavutil.mavlink.MAV_RESULT_ACCEPTED)
    rejected = ack(command, mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED)
    receiver = Mock(side_effect=[unrelated, rejected, AssertionError('must stop on rejection')])
    monkeypatch.setattr(controller, '_recv_match', receiver)
    monkeypatch.setattr(controller, '_with_status_text', lambda text: text)
    assert controller._wait_command_ack(command, timeout=0.5) is False
    assert receiver.call_count == 2
    assert controller._telemetry['COMMAND_ACK']['command'] == command
    assert 'MAV_RESULT_TEMPORARILY_REJECTED' in controller._last_action_error
    assert 'Timed out' not in controller._last_action_error
