"""Robonix XYZ 绘图 SDK —— 二进制帧协议编解码。

帧格式（全部小端 / little-endian）：

    偏移    长度    字段
    0       2      SOF  = 0xAA 0x55
    2       1      CMD   命令码 (U8)
    3       2      LEN   载荷长度 (U16)
    5       1      SEQ   序号 (U8, 0..255，应答原样带回)
    6       LEN    PAYLOAD
    6+LEN   2      CRC16-Modbus (U16)，对 CMD..PAYLOAD 全部字节计算

命令方向约定：C->S 表示 SDK(Client) -> PLC(Server)。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 帧常量
# ---------------------------------------------------------------------------
SOF = b"\xaa\x55"
HEADER_LEN = 6
CRC_LEN = 2
FRAME_OVERHEAD = HEADER_LEN + CRC_LEN

# 命令码
CMD_PING = 0x00
CMD_MOVE_LINEAR = 0x01
CMD_HOME = 0x03
CMD_SET_PARAM = 0x04
CMD_CLOSE = 0x0A
CMD_ACK = 0x80

# ACK status
STATUS_OK = 0
STATUS_BUSY = 1
STATUS_ERROR = 2
STATUS_UNKNOWN_CMD = 3

_CMD_NAMES = {
    CMD_PING: "PING",
    CMD_MOVE_LINEAR: "MOVE_LINEAR",
    CMD_HOME: "HOME",
    CMD_SET_PARAM: "SET_PARAM",
    CMD_CLOSE: "CLOSE",
    CMD_ACK: "ACK",
}


def cmd_name(cmd: int) -> str:
    return _CMD_NAMES.get(cmd, "UNKNOWN(0x%02X)" % cmd)


# ---------------------------------------------------------------------------
# CRC16-Modbus
# ---------------------------------------------------------------------------
_CRC_TABLE = None


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return table


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus (poly 0x8005, 反射), 返回 16 位无符号整数。"""
    global _CRC_TABLE
    if _CRC_TABLE is None:
        _CRC_TABLE = _make_crc_table()
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


# ---------------------------------------------------------------------------
# 帧构建 / 解析
# ---------------------------------------------------------------------------
@dataclass
class Frame:
    cmd: int
    seq: int
    payload: bytes

    @property
    def payload_len(self) -> int:
        return len(self.payload)


def build_frame(cmd: int, seq: int, payload: bytes = b"") -> bytes:
    """把命令封装成完整帧字节（含 SOF / 头 / 载荷 / CRC）。"""
    if not 0 <= cmd <= 0xFF:
        raise ValueError("cmd 越界: %r" % cmd)
    if not 0 <= seq <= 0xFF:
        raise ValueError("seq 越界: %r" % seq)
    if len(payload) > 0xFFFF:
        raise ValueError("payload 过长: %d" % len(payload))

    head = struct.pack("<BHB", cmd, len(payload), seq)
    crc = crc16_modbus(head + payload)
    return SOF + head + payload + struct.pack("<H", crc)


def parse_frame(buf: bytes) -> tuple[Frame | None, int]:
    """尝试从缓冲区解析一帧。

    返回 ``(frame, consumed)``。若缓冲区不完整（半包）返回 ``(None, 0)``。
    调用方应自行处理粘包（用 consumed 截断后继续解析）。CRC 校验失败或
    SOF 不对将抛 :class:`ValueError`，表示数据流损坏。
    """
    if len(buf) < FRAME_OVERHEAD:
        return None, 0

    if buf[:2] != SOF:
        raise ValueError("非法 SOF: %s" % buf[:2].hex())

    cmd, length, seq = struct.unpack_from("<BHB", buf, 2)
    total = FRAME_OVERHEAD + length
    if len(buf) < total:
        return None, 0

    head = buf[2:6]
    payload = buf[6 : 6 + length]
    crc_rx = struct.unpack_from("<H", buf, 6 + length)[0]
    crc_calc = crc16_modbus(head + payload)
    if crc_rx != crc_calc:
        raise ValueError(
            "CRC 校验失败: 收到 0x%04X, 计算 0x%04X" % (crc_rx, crc_calc)
        )

    return Frame(cmd=cmd, seq=seq, payload=payload), total


# ---------------------------------------------------------------------------
# 载荷打包 / 解包
# ---------------------------------------------------------------------------
def pack_f32(*values: float) -> bytes:
    return struct.pack("<%df" % len(values), *values)


def unpack_f32(data: bytes, count: int) -> tuple[float, ...]:
    return struct.unpack("<%df" % count, data[: 4 * count])


def pack_move_linear(x: float, y: float, z: float, vel: float) -> bytes:
    """MOVE_LINEAR 载荷：x, y, z, vel（4 × f32，对应 PLC 的 REAL）。"""
    return pack_f32(x, y, z, vel)


def pack_set_param(
    vel: float, acc: float, dec: float, jerk: float, z_up: float, z_down: float
) -> bytes:
    """SET_PARAM 载荷：vel, acc, dec, jerk, z_up, z_down（6 × f32，对应 PLC 的 REAL）。"""
    return pack_f32(vel, acc, dec, jerk, z_up, z_down)


def unpack_ack(payload: bytes) -> tuple[int, int, int]:
    """ACK 载荷：status(U8), err(U16), busy(U8)。"""
    if len(payload) < 4:
        raise ValueError("ACK 载荷过短: %d" % len(payload))
    status, err, busy = struct.unpack_from("<BHB", payload, 0)
    return status, err, busy
