import math
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.modules.mavlink_controller import MavlinkController


def controller_at(global_alt=48.8, z=0.0, sysid=1):
    c = MavlinkController()
    c._selected_sysid = sysid
    c._mavlink = SimpleNamespace(target_system=sysid, target_component=1, mav=Mock())
    table = c._system_table(sysid)
    table['telemetry']['GLOBAL_POSITION_INT'] = {'alt': global_alt}
    table['position']['z'] = z
    table['last_global_position'] = time.time()
    table['last_local_position'] = time.time()
    return c


@pytest.mark.parametrize('global_alt,z,expected', [(48.8, 0, 51.8), (50.8, -2, 51.8), (-20, 0, -17), (0, 0, 3)])
def test_local_target_to_amsl(global_alt, z, expected):
    c = controller_at(global_alt, z)
    assert c._native_takeoff_altitude_amsl(3) == pytest.approx(expected)


@pytest.mark.parametrize('key', ['last_global_position', 'last_local_position'])
def test_stale_reference_is_not_used(key):
    c = controller_at()
    c._system_table(1)[key] = time.time() - 2
    assert c._native_takeoff_altitude_amsl(3) is None


@pytest.mark.parametrize('value', [None, math.nan, math.inf])
def test_missing_or_invalid_reference_is_not_used(value):
    c = controller_at(value)
    assert c._native_takeoff_altitude_amsl(3) is None


def test_uses_command_target_not_selected_vehicle():
    c = controller_at(sysid=2)
    c._selected_sysid = 1
    c._system_table(1)['telemetry']['GLOBAL_POSITION_INT'] = {'alt': 1000}
    assert c._native_takeoff_altitude_amsl(3) == pytest.approx(51.8)


def test_native_command_sends_amsl(monkeypatch):
    c = controller_at()
    monkeypatch.setattr(c, 'update_telemetry', lambda **kw: None)
    monkeypatch.setattr(c, '_current_mode', lambda: 'LOITER')
    monkeypatch.setattr(c, '_is_armed', lambda: True)
    monkeypatch.setattr(c, '_wait_command_ack', lambda *a, **kw: False)
    assert not c._takeoff_via_native(3)
    assert c._mavlink.mav.command_long_send.call_args.args[-1] == pytest.approx(51.8)


def test_missing_reference_sends_no_native_command(monkeypatch):
    c = controller_at(None)
    monkeypatch.setattr(c, 'update_telemetry', lambda **kw: None)
    assert not c._takeoff_via_native(3)
    c._mavlink.mav.command_long_send.assert_not_called()


def test_native_takeoff_does_not_hover_at_first_altitude_crossing(monkeypatch):
    c = controller_at()
    clock = {'now': 100.0, 'samples': 0}
    monkeypatch.setattr('src.modules.mavlink_controller.time.time', lambda: clock['now'])
    monkeypatch.setattr('src.modules.mavlink_controller.time.monotonic', lambda: clock['now'])

    def update(**kw):
        clock['now'] += 0.25
        clock['samples'] += 1
        table = c._system_table(1)
        table['last_global_position'] = clock['now']
        table['last_local_position'] = clock['now']
        table['position']['z'] = -2.6 if clock['samples'] < 6 else -3.0
        table['velocity']['vz'] = -0.5 if clock['samples'] < 6 else 0.0

    monkeypatch.setattr(c, 'update_telemetry', update)
    monkeypatch.setattr(c, '_current_mode', lambda: 'TAKEOFF' if clock['samples'] < 6 else 'LOITER')
    monkeypatch.setattr(c, '_is_armed', lambda: True)
    monkeypatch.setattr(c, '_link_is_stale', lambda: False)
    monkeypatch.setattr(c, '_wait_command_ack', lambda *a, **kw: True)
    hover = Mock(side_effect=AssertionError('must not interrupt native climb'))
    monkeypatch.setattr(c, 'hover', hover)
    assert c._takeoff_via_native(3)
    assert clock['samples'] >= 10
    hover.assert_not_called()
