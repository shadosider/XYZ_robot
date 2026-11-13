# Robonix XYZ 绘图 —— 通信协议说明

本文档定义 PC 端 SDK（Python，下称 **Client**）与 PLC（MatrixBox MX4001，
下称 **Server**）之间的 TCP 二进制通信协议。PLC 侧使用功能块
`StkTCPAccept` / `StkTCPRcv` / `StkTCPSend` 实现（见 `toolbox.txt` 3.1.8~3.1.11）。

romote ip : 192.168.33.130

---

## 1. 传输层

| 项 | 约定 |
|----|------|
| 协议 | TCP |
| 角色 | **PLC = Server**（`StkTCPAccept` 监听），**SDK = Client** 主动连接 |
| 默认端口 | **2000** |
| 字节序 | **小端**（little-endian，与 Intel 平台 PLC 一致） |
| 浮点 | 位置/坐标（`MOVE_LINEAR` 的 x/y/z）为 IEEE 754 **float64**，对应 PLC `LREAL`；速度/加速度等（`SET_PARAM` 的 6 个参数）为 **float32**，对应 PLC `REAL` |

推荐 PLC 侧 `StkTCPRcv.TimeOut` 设为 `10`（10 ms 级，视具体单位调整），
`StkTCPAccept.TimeOut` 设为 0 表示无限等待连接。

---

## 2. 帧格式

```
偏移        长度      字段
0           2        SOF  帧头 = 0xAA 0x55
2           1        CMD  命令码 (U8)
3           2        LEN  载荷长度 (U16, LE)
5           1        SEQ  序号 (U8, 0..255，应答原样带回)
6           LEN      PAYLOAD
6+LEN       2        CRC  CRC16-Modbus (U16, LE)
```

- 帧总长 = `6 + LEN + 2` 字节。
- **CRC16-Modbus**（多项式 0x8005，反射，初值 0xFFFF），
  计算范围为 `CMD(1) + LEN(2) + SEQ(1) + PAYLOAD(LEN)`，即偏移 2 到 6+LEN-1 的字节。
  SOF 与 CRC 自身不参与校验。
- 命令方向：**C→S** 表示 SDK→PLC，**S→C** 表示 PLC→SDK。

---

## 3. 命令表

| CMD  | 名称           | 方向 | 载荷（字节）                                   | 说明 |
|------|----------------|------|------------------------------------------------|------|
| 0x00 | PING           | C→S  | 空                                             | 连通性测试 |
| 0x01 | MOVE_LINEAR    | C→S  | x, y, z = 3×f64 (**24B**)                       | 绝对直线插补（x/y/z 为 MCS 绝对坐标），速度由 SET_PARAM 的 `vel` 决定 |
| 0x03 | HOME           | C→S  | 空                                             | 轴组回零（`MC_GroupHome`） |
| 0x04 | SET_PARAM      | C→S  | vel, acc, dec, jerk, z_up, z_down = 6×f32 (**24B**) | 设定全局运动参数与笔高度 |
| 0x0A | CLOSE          | C→S  | 空                                             | 请求关闭会话；PLC 回 ACK 后关闭 socket 并回到 Accept |
| 0x80 | ACK            | S→C  | status U8 + err U16 + busy U8 (**4B**)          | 命令应答，SEQ 回带 |


---

## 4. ACK 应答

每个 C→S 命令 PLC 处理完成后回一帧 `ACK`，
`SEQ` 与请求一致，用于逐帧握手与流控。

```
ACK 载荷 (4B):
  status  U8   0=OK, 1=忙, 2=错误, 3=未知命令
  err     U16  错误码（status=2 时有效，具体含义见 PLC 侧 ErrorID）
  busy    U8   0=空闲, 1=轴组忙
```

- `busy` 表示轴组当前是否忙（1=忙，0=空闲），用于 ACK 附带信息。

> **MotionSim 调试模式**：ACK 载荷 = 4B `status/err/busy` + 原始请求帧
> （含 SOF/CMD/LEN/SEQ/PAYLOAD/CRC）。客户端只读取前 4B，不影响握手；
> 抓包时可以看到 PLC 实际收到的完整输入帧。

---

## 5. 交互模型（v1 同步握手）

SDK 每发一条 `MOVE_LINEAR`，等待 PLC 返回对应 `SEQ` 的 `ACK(status=0)`
后再发下一条；PLC 在 `MC_MoveLinearAbsolute.Done`（或 `Error`）后回 ACK。

> PLC 侧 `MOVE_LINEAR` 使用 `MC_MoveLinearAbsolute`，x/y/z 是 **MCS 绝对坐标**，
> 需确保轴组已回零/有有效参考位置。

抬笔移动与落笔移动都是同一条 `MOVE_LINEAR`，区别仅在 `z` 值：

- `z = z_down`：落笔（绘制段）
- `z = z_up`：抬笔（空行程）

`z_up` / `z_down` 由 SDK 直接编入坐标，PLC 无需区分提笔/落笔。

运动速度不在 `MOVE_LINEAR` 中传递，由之前的 `SET_PARAM(vel, ...)` 设定；
未收到 `SET_PARAM` 时 PLC 使用自身默认速度（`vel := 15`）。

---

## 6. 载荷编解码示例

### MOVE_LINEAR（24B 载荷）
```
x=10.0, y=20.0, z=0.0
payload = struct.pack('<3d', 10.0, 20.0, 0.0)
```

### SET_PARAM（24B 载荷）
```
vel=20.0, acc=300.0, dec=300.0, jerk=3000.0, z_up=-40.0, z_down=-50.0
payload = struct.pack('<6f', 20.0, 300.0, 300.0, 3000.0, -40.0, -50.0)
```

### 完整帧示例（MOVE_LINEAR, SEQ=0, 无载荷简化示意）

以 `PING`（载荷 0）为例，SEQ=1：

```
CMD=0x00 LEN=0x0000 SEQ=0x01
CRC 对 [00 00 00 01] 计算，得 0xC0C1
帧 = AA 55 00 00 00 01 C0 C1
```

（实际字节以 `robonix_sdk.protocol.build_frame` 输出为准。）

---

## 7. PLC 侧实现要点

1. `StkTCPAccept(Execute:=TRUE, SrcTcpPort:=2000, TimeOut:=0)` 得到 Socket。
2. `StkTCPRcv` 收字节流到缓冲数组，按 `SOF(2) + 头(4) + LEN + CRC(2)` 切帧；
   用**同一套 CRC16-Modbus** 校验（多项式 0x8005，反射，初值 0xFFFF）。
3. 校验通过后按 CMD 分发；`MOVE_LINEAR` 把 3×f64 写入
   `Target[1..3]`（`ARRAY[1..3] OF LREAL`，小端布局与 PLC `LREAL` 一致），
   拉高 `Execute` 触发 `MC_MoveLinearAbsolute`。
4. `Done`/`Error` 后 `StkTCPSend` 回 ACK（含 `SEQ`）。

参见 `docs/plc_wiring.md` 的接线与时序。
