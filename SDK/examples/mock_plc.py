#!/usr/bin/env python3
"""假 PLC —— 无硬件联调用。

在本地监听 TCP（默认 2000），按 Robonix 协议解析帧、校验 CRC、
按命令回 ACK，并打印收到的 MOVE_LINEAR 点数与坐标范围。

用法::

    python mock_plc.py --port 2000
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import threading

# 允许从任意目录直接运行本脚本
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import protocol as proto


class MockPLC:
    def __init__(self, host: str, port: int, ack_delay: float = 0.0):
        self.host = host
        self.port = port
        self.ack_delay = ack_delay
        self.count = 0
        self.x_range = [float("inf"), float("-inf")]
        self.y_range = [float("inf"), float("-inf")]
        self.z_range = [float("inf"), float("-inf")]

    def _ack(self, conn: socket.socket, seq: int, status: int, err: int = 0, busy: int = 0) -> None:
        payload = struct.pack("<BHB", status, err, busy)
        conn.sendall(proto.build_frame(proto.CMD_ACK, seq, payload))

    def _handle(self, frame: proto.Frame, conn: socket.socket) -> None:
        seq = frame.seq
        if frame.cmd == proto.CMD_PING:
            self._ack(conn, seq, proto.STATUS_OK)
        elif frame.cmd == proto.CMD_MOVE_LINEAR:
            x, y, z, vel = proto.unpack_f32(frame.payload, 4)
            self.count += 1
            self.x_range = [min(self.x_range[0], x), max(self.x_range[1], x)]
            self.y_range = [min(self.y_range[0], y), max(self.y_range[1], y)]
            self.z_range = [min(self.z_range[0], z), max(self.z_range[1], z)]
            self._ack(conn, seq, proto.STATUS_OK)
            if self.count % 200 == 0:
                print("  已收到 %d 个点" % self.count, flush=True)
        elif frame.cmd == proto.CMD_SET_PARAM:
            vel, acc, dec, jerk, z_up, z_down = proto.unpack_f32(frame.payload, 6)
            print(
                "SET_PARAM vel=%.3f acc=%.3f dec=%.3f jerk=%.3f z_up=%.3f z_down=%.3f"
                % (vel, acc, dec, jerk, z_up, z_down),
                flush=True,
            )
            self._ack(conn, seq, proto.STATUS_OK)
        elif frame.cmd == proto.CMD_HOME:
            print("HOME", flush=True)
            self._ack(conn, seq, proto.STATUS_OK)
        elif frame.cmd == proto.CMD_CLOSE:
            print("CLOSE", flush=True)
            self._ack(conn, seq, proto.STATUS_OK)
        else:
            print("未知命令 0x%02X" % frame.cmd, flush=True)
            self._ack(conn, seq, proto.STATUS_UNKNOWN_CMD)

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    try:
                        frame, consumed = proto.parse_frame(buf)
                    except ValueError as e:
                        print("协议错误: %s" % e, flush=True)
                        buf = b""
                        break
                    if frame is None:
                        break
                    buf = buf[consumed:]
                    self._handle(frame, conn)
        finally:
            conn.close()

    def run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(1)
        print("MockPLC 监听 %s:%d ..." % (self.host, self.port), flush=True)
        try:
            while True:
                conn, addr = srv.accept()
                print("客户端连接: %s" % (addr,), flush=True)
                self.count = 0
                t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
                t.start()
        except KeyboardInterrupt:
            print("\n退出", flush=True)
        finally:
            srv.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mock PLC (Robonix 协议 Server)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=2000)
    args = p.parse_args(argv)
    MockPLC(args.host, args.port).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
