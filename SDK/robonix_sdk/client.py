"""RobonixClient —— TCP 客户端，逐帧握手与 PLC 通信。"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterable

from . import protocol as proto

DEFAULT_PORT = 2000


class ProtocolError(Exception):
    """协议层错误（非法帧 / CRC 失败 / 应答错误）。"""


class RobonixClient:
    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._seq = 0
        self._buf = b""

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._buf = b""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "RobonixClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        # 先发 CMD_CLOSE 让 PLC 走状态 90 关闭流程，避免旧 socket
        # 留在 PLC 里导致下一次连接无法被 Accept。
        if self._sock is not None:
            try:
                self.close_session()
            except Exception:
                pass
            finally:
                self.close()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------
    # 底层收发
    # ------------------------------------------------------------------
    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    def _send(self, cmd: int, payload: bytes = b"") -> int:
        if self._sock is None:
            raise RuntimeError("未连接，请先调用 connect()")
        seq = self._next_seq()
        frame = proto.build_frame(cmd, seq, payload)
        self._sock.sendall(frame)
        return seq

    def _recv_frame(self) -> proto.Frame:
        if self._sock is None:
            raise RuntimeError("未连接")
        while True:
            frame, consumed = proto.parse_frame(self._buf)
            if frame is not None:
                self._buf = self._buf[consumed:]
                return frame
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise TimeoutError("等待 PLC 应答超时")
            if not chunk:
                raise ConnectionError("连接被对端关闭")
            self._buf += chunk

    def _request(self, cmd: int, payload: bytes = b"") -> proto.Frame:
        """发送一帧并等待对应 seq 的 ACK，返回 ACK 帧。"""
        seq = self._send(cmd, payload)
        while True:
            frame = self._recv_frame()
            if frame.cmd != proto.CMD_ACK:
                continue
            if frame.seq != seq:
                continue
            return frame

    def _expect_ack_ok(self, cmd: int, payload: bytes = b"", what: str = "命令") -> tuple[int, int, int]:
        ack = self._request(cmd, payload)
        status, err, busy = proto.unpack_ack(ack.payload)
        if status != proto.STATUS_OK:
            raise ProtocolError(
                "%s失败: status=%d err=0x%04X busy=%d" % (what, status, err, busy)
            )
        return status, err, busy

    # ------------------------------------------------------------------
    # 高层命令
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        try:
            self._expect_ack_ok(proto.CMD_PING, what="PING")
            return True
        except (ProtocolError, TimeoutError, ConnectionError):
            return False

    def move_linear(self, x: float, y: float, z: float, vel: float = 0.0) -> None:
        """世界坐标直线插补。vel=0 表示使用 PLC 端默认速度。"""
        self._expect_ack_ok(
            proto.CMD_MOVE_LINEAR,
            proto.pack_move_linear(x, y, z, vel),
            what="MOVE_LINEAR",
        )

    def set_param(
        self, vel: float, acc: float, dec: float, jerk: float, z_up: float, z_down: float
    ) -> None:
        self._expect_ack_ok(
            proto.CMD_SET_PARAM,
            proto.pack_set_param(vel, acc, dec, jerk, z_up, z_down),
            what="SET_PARAM",
        )

    def home(self) -> None:
        self._expect_ack_ok(proto.CMD_HOME, what="HOME")

    def close_session(self) -> None:
        """请求 PLC 关闭当前会话：发送 CLOSE，等待 ACK 后关闭本地连接。

        先发命令再关闭，可以避免 PLC 侧一直停留在旧连接上，
        导致后续重新连接无法被 Accept。
        """
        try:
            self._expect_ack_ok(proto.CMD_CLOSE, what="CLOSE")
        finally:
            self.close()

    # ------------------------------------------------------------------
    # 绘图
    # ------------------------------------------------------------------
    def draw_waypoints(
        self,
        points: Iterable[tuple[float, float, float]],
        vel: float = 0.0,
        on_progress: Callable[[int, int, tuple[float, float, float]], None] | None = None,
    ) -> None:
        """逐点下发 MOVE_LINEAR（逐点握手）。

        参数
        ----
        points:
            ``(x, y, z)`` 序列，z 值已编码笔状态。
        on_progress:
            回调 ``(done, total, last_point)``，可打印进度。
        """
        pts = list(points)
        total = len(pts)
        for i, (x, y, z) in enumerate(pts):
            self.move_linear(x, y, z, vel)
            if on_progress is not None:
                on_progress(i + 1, total, (x, y, z))
