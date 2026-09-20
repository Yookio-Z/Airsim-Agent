"""TCP MAVLink 链路测试：连接、识别飞控、下发指令、读取状态。

真机遥控器/机载转发对外提供的常是 TCP（例如 5760 或厂商自定义端口），
这条链路此前没有任何测试覆盖。这里用一个本机 TCP 假飞控（只回心跳和
COMMAND_ACK，不涉及任何真实硬件）验证四个环节：

1. ``tcp:host:port`` 能建立连接并收到心跳（而不是只在 UDP 下工作）；
2. 心跳里的 PX4/机型信息足以识别飞控并把模式解析出来；
3. 指令方向能通：COMMAND_LONG 发出、ACK 收下、状态翻转被观察到；
4. ``real_vehicle=True`` 能透传到连接详情里（真机审批门的输入）。
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from src.modules.mavlink_controller import MavlinkController

# PX4 AUTO.LOITER：main_mode=4(AUTO) sub_mode=3(LOITER)
CUSTOM_MODE_LOITER = (3 << 24) | (4 << 16)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakePx4TcpVehicle:
    """本机假飞控：持续发心跳，收到 arm 指令回 ACK 并翻转 armed 状态。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self.armed = False
        self.received_commands: list[int] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._link = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._link is not None:
            try:
                self._link.close()
            except Exception:
                pass

    def _serve(self) -> None:
        from pymavlink import mavutil

        # tcpin: 是 pymavlink 的服务端模式：绑定端口等客户端连进来。
        self._link = mavutil.mavlink_connection(
            f"tcpin:127.0.0.1:{self.port}", autoreconnect=False
        )
        deadline = time.time() + 8.0
        next_heartbeat = 0.0
        while not self._stop.is_set() and time.time() < deadline:
            now = time.time()
            if now >= next_heartbeat:
                try:
                    self._link.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_QUADROTOR,
                        mavutil.mavlink.MAV_AUTOPILOT_PX4,
                        mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0,
                        CUSTOM_MODE_LOITER,
                        0,
                    )
                except Exception:
                    pass
                next_heartbeat = now + 0.2
            try:
                msg = self._link.recv_match(blocking=False)
            except Exception:
                msg = None
            if msg is None:
                time.sleep(0.02)
                continue
            if msg.get_type() != "COMMAND_LONG":
                continue
            self.received_commands.append(int(msg.command))
            if int(msg.command) == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                self.armed = float(msg.param1) >= 0.5
                try:
                    self._link.mav.command_ack_send(
                        int(msg.command), mavutil.mavlink.MAV_RESULT_ACCEPTED
                    )
                except Exception:
                    pass
            # 翻转后的状态要立刻体现在心跳里，否则控制器等不到 armed 变化
            next_heartbeat = 0.0


@pytest.fixture()
def fake_vehicle():
    vehicle = FakePx4TcpVehicle(_free_port())
    vehicle.start()
    yield vehicle
    vehicle.stop()


def _controller(monkeypatch) -> MavlinkController:
    """构造控制器并跳过连接收尾的两个慢请求（与链路能力无关）。"""
    controller = MavlinkController(connection_string="")
    monkeypatch.setattr(controller, "get_firmware_info", lambda **kwargs: {})
    monkeypatch.setattr(controller, "download_parameters", lambda **kwargs: {})
    return controller


def test_tcp_link_connects_and_identifies_vehicle(fake_vehicle, monkeypatch) -> None:
    controller = _controller(monkeypatch)
    info = controller.connect(url=f"tcp:127.0.0.1:{fake_vehicle.port}")

    assert info.connected is True, info.details
    assert str(info.details.get("url") or "").startswith("tcp:127.0.0.1:")
    # 心跳识别出的身份与模式：不依赖 GPS、不依赖 UDP
    assert info.details.get("mode") == "LOITER"
    assert controller._is_px4() is True
    assert controller.is_connected is True
    controller.disconnect()


def test_tcp_link_marks_real_vehicle_flag(fake_vehicle, monkeypatch) -> None:
    """真机标记是审批门的输入，必须在连接详情里如实回传。"""
    controller = _controller(monkeypatch)
    info = controller.connect(url=f"tcp:127.0.0.1:{fake_vehicle.port}", real_vehicle=True)

    assert info.connected is True
    assert info.details.get("real_vehicle") is True
    assert controller.get_status().to_dict().get("real_vehicle") is True
    controller.disconnect()


def test_tcp_link_carries_commands_and_acks(fake_vehicle, monkeypatch) -> None:
    """指令方向：arm 要真的发出去，并且能从 ACK + 心跳确认状态翻转。"""
    controller = _controller(monkeypatch)
    from pymavlink import mavutil

    assert controller.connect(url=f"tcp:127.0.0.1:{fake_vehicle.port}").connected is True
    try:
        assert controller.arm() is True
        assert mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM in fake_vehicle.received_commands
        assert fake_vehicle.armed is True
        assert controller.is_flying_now() is False  # 假飞控没报 IN_AIR
    finally:
        controller.disconnect()
