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
| 浮点 | IEEE 754 **float64**，对应 PLC 的 `LREAL`（与 Test 工程 `position[1..3] OF LREAL` 一致） |

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
| 0x01 | MOVE_LINEAR    | C→S  | x, y, z, vel = 4×f64 (**32B**)                 | 世界坐标直线插补，`vel=0` 用 PLC 默认速度 |
| 0x03 | HOME           | C→S  | 空                                             | 轴组回零（`MC_GroupHome`） |
| 0x04 | SET_PARAM      | C→S  | vel, acc, dec, jerk, z_up, z_down = 6×f64 (**48B**) | 设定全局运动参数与笔高度 |
| 0x05 | ENABLE         | C→S  | flag = U8 (**1B**，1 使能 / 0 去使能)          | `MC_Power` / `MC_GroupEnable` |
| 0x06 | RESET          | C→S  | 空                                             | 轴复位（`MC_Reset`） |
| 0x07 | STOP           | C→S  | 空                                             | 终止当前运动 |
| 0x08 | READ_STATUS    | C→S  | 空                                             | 查询状态 |
| 0x09 | GET_POSITION   | C→S  | 空                                             | 查询当前位置 |
| 0x0A | CLOSE          | C→S  | 空                                             | 请求关闭会话；PLC 回 ACK 后关闭 socket 并回到 Accept |
| 0x80 | ACK            | S→C  | status U8 + err U16 + busy U8 (**4B**)          | 命令应答，SEQ 回带 |

> 0x02 保留（预留圆弧/点到点），0x0B~0x7F 保留。

---

## 4. ACK 应答

每个 C→S 命令（除 GET_POSITION 外）PLC 处理完成后回一帧 `ACK`，
`SEQ` 与请求一致，用于逐帧握手与流控。

```
ACK 载荷 (4B):
  status  U8   0=OK, 1=忙, 2=错误, 3=未知命令
  err     U16  错误码（status=2 时有效，具体含义见 PLC 侧 ErrorID）
  busy    U8   0=空闲, 1=轴组忙
```

- `GET_POSITION` 的 ACK 载荷为 **25B**：`x, y, z = 3×f64` + `busy U8`。

---

## 5. 交互模型（v1 同步握手）

SDK 每发一条 `MOVE_LINEAR`，等待 PLC 返回对应 `SEQ` 的 `ACK(status=0)`
后再发下一条；PLC 在 `MC_MoveLinearAbsolute.Done`（或 `Error`）后回 ACK。
抬笔移动与落笔移动都是同一条 `MOVE_LINEAR`，区别仅在 `z` 值：

- `z = z_down`：落笔（绘制段）
- `z = z_up`：抬笔（空行程）

`z_up` / `z_down` 由 SDK 直接编入坐标，PLC 无需区分提笔/落笔。

---

## 6. 载荷编解码示例

### MOVE_LINEAR（32B 载荷）
```
x=10.0, y=20.0, z=0.0, vel=0.0
payload = struct.pack('<4d', 10.0, 20.0, 0.0, 0.0)
```

### SET_PARAM（48B 载荷）
```
vel=20.0, acc=300.0, dec=300.0, jerk=3000.0, z_up=5.0, z_down=0.0
payload = struct.pack('<6d', 20.0, 300.0, 300.0, 3000.0, 5.0, 0.0)
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
   `position[1..3]`（`ARRAY[1..3] OF LREAL`，小端布局与 PLC `LREAL` 一致），
   拉高 `Execute` 触发 `MC_MoveLinearAbsolute`。
4. `Done`/`Error` 后 `StkTCPSend` 回 ACK（含 `SEQ`）。

参见 `docs/plc_wiring.md` 的接线与时序。
