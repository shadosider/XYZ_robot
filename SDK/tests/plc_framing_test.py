#!/usr/bin/env python3
"""PLC TCP 分帧 / 粘包 / 半包 / CRC 重同步测试（可选择测试项）。

用于验证 tcp_test 的接收缓冲与切帧逻辑（不依赖运动层）。

用法::

    # 先让 PLC 运行 tcp_test，监听 2000
    python plc_framing_test.py --host 192.168.33.130 --port 2000

    # 不进入菜单，直接顺序跑全部测试
    python plc_framing_test.py --host 192.168.33.130 --port 2000 --run-all

    # 顺序测试时也执行 HOME（真实回零，谨慎）
    python plc_framing_test.py --host 192.168.33.130 --port 2000 --run-all --include-home

菜单测试项：
  1. 正常 PING
  2. 半包 PING（3 字节 + 5 字节）
  3. 粘包 PING（两帧一次发）
  4. 坏 CRC 后重同步
  5. MOVE_LINEAR 16B
  6. SET_PARAM 24B
  7. HOME（真实回零，谨慎）
  8. 全部顺序测试（默认不含 HOME）
  9. 重新连接（先发 CMD_CLOSE 关闭会话）
  0. 退出（先关闭会话）
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import protocol as proto


# ---------------------------------------------------------------------------
# 原始 TCP 客户端（保留拆包/粘包能力）
# ---------------------------------------------------------------------------
class RawClient:
    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buf = b""
        self.seq = 0
        self.connect()

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        # 关闭 Nagle，保证半包测试的第一段 3 字节立即发出
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def next_seq(self) -> int:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return seq

    def send_raw(self, data: bytes) -> None:
        if self.sock is None:
            raise ConnectionError("未连接")
        self.sock.sendall(data)

    def recv_frame(self, timeout: float | None = None):
        if self.sock is None:
            raise ConnectionError("未连接")

        old = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                frame, consumed = proto.parse_frame(self.buf)
                if frame is not None:
                    self.buf = self.buf[consumed:]
                    return frame
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("PLC 关闭了连接")
                self.buf += chunk
        finally:
            if timeout is not None:
                self.sock.settimeout(old)

    def request(self, cmd: int, payload: bytes = b"", timeout: float | None = None) -> tuple[int, proto.Frame]:
        seq = self.next_seq()
        self.send_raw(proto.build_frame(cmd, seq, payload))
        frame = self.recv_frame(timeout)
        return seq, frame

    def close_session(self, quiet: bool = False) -> None:
        """先发 CMD_CLOSE 并等 ACK，再关闭本地 socket。

        这样 PLC 会走状态 90 的关闭流程，释放旧 socket，
        避免反复重连后停在旧连接上无法再次 Accept。
        """
        if self.sock is None:
            return
        try:
            seq, frame = self.request(proto.CMD_CLOSE)
            if frame.cmd != proto.CMD_ACK or frame.seq != seq:
                raise AssertionError("CLOSE: 应答 CMD/SEQ 不匹配")
            status, err, busy = proto.unpack_ack(frame.payload)
            if status != proto.STATUS_OK:
                raise AssertionError("CLOSE: status=%d err=0x%04X" % (status, err))
            if not quiet:
                print("  PASS 0. 关闭会话                       seq=%d status=%d" % (seq, status))
        except Exception as e:
            if not quiet:
                print("[WARN] 关闭会话失败: %s" % e)
        finally:
            self.close()


def check_ack(name: str, seq: int, frame: proto.Frame, expect_status: int = proto.STATUS_OK) -> None:
    if frame.cmd != proto.CMD_ACK:
        raise AssertionError("%s: 期望 ACK，收到 CMD=0x%02X" % (name, frame.cmd))
    if frame.seq != seq:
        raise AssertionError("%s: SEQ 不匹配，期望 %d，收到 %d" % (name, seq, frame.seq))
    status, err, busy = proto.unpack_ack(frame.payload)
    if status != expect_status:
        raise AssertionError("%s: status=%d err=0x%04X busy=%d" % (name, status, err, busy))
    print("  PASS %-38s seq=%d status=%d busy=%d" % (name, seq, status, busy))


# ---------------------------------------------------------------------------
# 单个测试项
# ---------------------------------------------------------------------------
def test_ping(c: RawClient, args: argparse.Namespace) -> None:
    seq, frame = c.request(proto.CMD_PING)
    check_ack("1. 正常 PING", seq, frame)


def test_half_ping(c: RawClient, args: argparse.Namespace) -> None:
    seq = c.next_seq()
    raw = proto.build_frame(proto.CMD_PING, seq)
    c.send_raw(raw[:3])
    time.sleep(args.split_delay)
    c.send_raw(raw[3:])
    check_ack("2. 半包 PING", seq, c.recv_frame())


def test_coalesced_ping(c: RawClient, args: argparse.Namespace) -> None:
    seq1 = c.next_seq()
    seq2 = c.next_seq()
    c.send_raw(
        proto.build_frame(proto.CMD_PING, seq1)
        + proto.build_frame(proto.CMD_PING, seq2)
    )
    check_ack("3. 粘包 PING #1", seq1, c.recv_frame())
    check_ack("3. 粘包 PING #2", seq2, c.recv_frame())


def test_bad_crc(c: RawClient, args: argparse.Namespace) -> None:
    bad = bytearray(proto.build_frame(proto.CMD_PING, c.next_seq()))
    bad[-1] ^= 0xFF
    c.send_raw(bytes(bad))
    seq, frame = c.request(proto.CMD_PING)
    check_ack("4. 坏 CRC 后重同步", seq, frame)


def test_move_linear(c: RawClient, args: argparse.Namespace) -> None:
    seq, frame = c.request(
        proto.CMD_MOVE_LINEAR,
        proto.pack_move_linear(50.0, 40.0, -25, 20.0),
    )
    check_ack("5. MOVE_LINEAR 16B", seq, frame)


def test_set_param(c: RawClient, args: argparse.Namespace) -> None:
    seq, frame = c.request(
        proto.CMD_SET_PARAM,
        proto.pack_set_param(20.0, 300.0, 300.0, 3000.0, 5.0, 0.0),
    )
    check_ack("6. SET_PARAM 24B", seq, frame)


def test_home(c: RawClient, args: argparse.Namespace) -> None:
    """HOME：执行轴组回零。

    注意：HOME 会让轴组回到 Home 数组指定的位置，属于真实运动。
    这里单独使用更长的超时（--home-timeout），因为回零可能比较慢。
    """
    print("  [WARN] HOME 将执行轴组回零，请确认机械行程/原点安全！")
    seq, frame = c.request(proto.CMD_HOME, timeout=args.home_timeout)
    check_ack("7. HOME", seq, frame)


# ---------------------------------------------------------------------------
# 顺序执行
# ---------------------------------------------------------------------------
ORDERED_TESTS = [
    ("1. 正常 PING", test_ping),
    ("2. 半包 PING", test_half_ping),
    ("3. 粘包 PING", test_coalesced_ping),
    ("4. 坏 CRC 后重同步", test_bad_crc),
    ("5. MOVE_LINEAR", test_move_linear),
    ("6. SET_PARAM", test_set_param),
]


def run_all(c: RawClient, args: argparse.Namespace) -> None:
    tests = list(ORDERED_TESTS)

    # HOME 是真实运动，默认不放进 run-all；需要显式加 --include-home
    if getattr(args, "include_home", False):
        tests.insert(4, ("7. HOME", test_home))

    for name, fn in tests:
        print("\n--- %s ---" % name)
        fn(c, args)
    print("\n全部通过")


# ---------------------------------------------------------------------------
# 菜单
# ---------------------------------------------------------------------------
MENU_ITEMS = {
    "1": test_ping,
    "2": test_half_ping,
    "3": test_coalesced_ping,
    "4": test_bad_crc,
    "5": test_move_linear,
    "6": test_set_param,
    "7": test_home,
    "8": run_all,
}

MENU_TEXT = """
========== PLC TCP 分帧/协议测试 ==========
  1. 正常 PING
  2. 半包 PING（3 字节 + 5 字节）
  3. 粘包 PING（两帧一次发）
  4. 坏 CRC 后重同步
  5. MOVE_LINEAR 16B
  6. SET_PARAM 24B
  7. HOME（真实回零，谨慎）
  8. 全部顺序测试（默认不含 HOME）
  9. 重新连接（先发 CMD_CLOSE 关闭会话）
  0. 退出（先关闭会话）
==========================================
"""


def make_client(args: argparse.Namespace) -> RawClient | None:
    try:
        c = RawClient(args.host, args.port, args.timeout)
        print("已连接 %s:%d" % (args.host, args.port))
        return c
    except OSError as e:
        print("[ERROR] 连接失败: %s" % e)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PLC TCP framing test (interactive)")
    ap.add_argument("--host", default="192.168.33.130")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--split-delay", type=float, default=0.1, help="半包测试两段之间的间隔（秒）")
    ap.add_argument("--home-timeout", type=float, default=60.0, help="HOME 测试等待 ACK 的超时（秒）")
    ap.add_argument("--run-all", action="store_true", help="不进入菜单，直接顺序跑全部测试")
    ap.add_argument("--include-home", action="store_true", help="run-all 时也执行 HOME（会移动轴组，谨慎）")
    args = ap.parse_args(argv)

    c = make_client(args)
    if c is None:
        return 1

    if args.run_all:
        try:
            run_all(c, args)
            return 0
        except Exception as e:
            print("[FAIL] %s: %s" % (type(e).__name__, e))
            return 1
        finally:
            c.close_session(quiet=True)

    try:
        while True:
            print(MENU_TEXT)
            try:
                choice = input("请选择测试项: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n退出")
                break

            if choice == "0":
                break

            if choice == "9":
                # 先让 PLC 走关闭流程，再重新连接
                c.close_session()
                time.sleep(0.5)
                c2 = make_client(args)
                if c2 is not None:
                    c = c2
                continue

            fn = MENU_ITEMS.get(choice)
            if fn is None:
                print("无效选项")
                continue

            try:
                fn(c, args)
            except TimeoutError:
                print("[FAIL] 等待 ACK 超时")
                print("  提示：")
                print("   1) PLC 是否已重启并运行 tcp_test；")
                print("   2) Task0 周期是否过大（PERIOD=1000 可能是 1 秒）；")
                print("   3) 可先选 9 重新连接，再重试。")
            except (ConnectionError, OSError, AssertionError, ValueError) as e:
                print("[FAIL] %s" % e)
                print("  提示：可先选 9 重新连接，再重试。")
            except Exception as e:
                print("[ERROR] %s: %s" % (type(e).__name__, e))
    finally:
        c.close_session(quiet=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())