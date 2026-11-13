# PLC 侧参考接线（基于 Test 工程）

本文档基于 `XYZ_Cass/POUS/Device1/Task0/Test.XML`（FBD 程序），说明 PLC
接收 SDK 轨迹并驱动三轴机器人的接线方式与时序。SDK 侧见 `protocol.md`。

---

## 1. 现有工程概览

`Test` 工程已包含完整的轴组运动功能块链，**无需重写**，只做两处接线修改
（见第 4 节）。现成 FB 如下：

| FB | 作用 | 关键引脚（已在 Test 中配置） |
|----|------|------------------------------|
| `MC_Reset` ×3 | 轴复位 | `Axis_num` = 0/1/2 |
| `MC_Power` ×3 | 轴使能 | `Mode` = 8 |
| `MC_DriverSync` | 驱动器同步 | — |
| `MC_AddAxisToGroup` ×3 (AddX/AddY/AddZ) | 轴加入轴组 | 见轴映射表 |
| `MC_SetKinTransform` | 运动学变换 | `KinTransform` = 4, `ExecutionMode` = 0, DenHart 参数 `dhA`/`dhD`/`jdir`/`jmotor`/`jshaft`/`jofs`/`jlim` 已配 |
| `MC_GroupEnable` | 轴组使能 | `AxesGroup` = 0 |
| `MC_GroupHome` | 轴组回零 | `Position` = `&position_zero[1]`, `CoordSystem` = 2 |
| `MC_MoveLinearAbsolute` | 直线绝对运动 | `Position` = `&position[1]`, `CoordSystem` = 2 (MCS), `BufferMode` = 1 |
| `MC_GroupReadActualPosition` | 读当前位置 | `CoordSystem` = 2, 输出 `position_now[1..3]` |

---

## 2. 轴与轴组映射（关键）

从 Test 工程 `AddX/AddY/AddZ` 的引脚提取：

| 物理轴号 (`Axis`) | 机械轴 | 轴组内序号 (`IdentInGroup`) |
|-------------------|--------|-----------------------------|
| 0 | Z | 2 |
| 1 | Y | 1 |
| 2 | X | 0 |

- SDK 下发的是**轴组世界坐标 (x, y, z)**（`CoordSystem=2` = MCS 世界坐标系，
  toolbox 定义：1=ACS 关节、2=MCS 世界、3=PCS 工件）。
- 因此 SDK 的 `(x, y, z)` 与 `position[1]=x, position[2]=y, position[3]=z` 直接对应，
  无需在 PLC 侧重排。

---

## 3. TCP 通信接线

新增一个 POU（或复用现有 Task），用以下功能块建立 Server 并收发：

```
SktTCPAccept(Execute:=AcceptReq, SrcTcpPort:=2000, TimeOut:=0,
             Done=>, Busy=>, Error=>, ErrorID=>, Socket0=>sock0, Socket1=>sock1);

SktTCPRcv(Execute:=RcvReq, Socket0:=sock0, TimeOut:=10, RcvData:=rxBuf[0],
          Size:=rxBufSize, Socket1:=sock1,
          Done=>RcvDone, Busy=>, Error=>, ErrorID=>, RcvSize=>rcvSize);

SktTCPSend(Execute:=SendReq, Socket0:=sock0, SendData:=txBuf[0], Size:=txSize,
           Socket1:=sock1, Done=>, Busy=>, Error=>, ErrorID=>);
```

- `rxBuf` 建议 `ARRAY[0..1023] OF USINT`，`txBuf` 同理。
- 收帧后按 `protocol.md` 第 2 节切帧：SOF=0xAA55 → 头(CMD/LEN/SEQ) → PAYLOAD → CRC。
- **CRC16-Modbus**：多项式 0x8005、反射、初值 0xFFFF，对 CMD..PAYLOAD 计算
  （与 SDK `robonix_sdk.protocol.crc16_modbus` 一致）。
- 收到 `MOVE_LINEAR`：把载荷前 12 字节按小端 f32 解析为 `x/y/z`，写入
  `Target[1..3]`（`ARRAY[1..3] OF REAL`），拉高 `MC_MoveLinearAbsolute.Execute`。

---

## 4. 需修改的两处接线

| 位置 | 现状 | 改为 |
|------|------|------|
| `MC_MoveLinearAbsolute.Velocity` | 常量 `K20` | 接收到的 `velocity_rx`（REAL，来自 SET_PARAM 或 MOVE_LINEAR 的 vel 字段） |
| `MC_MoveLinearAbsolute.Position` | `&position[1]` | 保持 `&position[1]`，但 `position` 数组改由 TCP 接收缓冲写入 |

其余 FB（使能链、运动学参数、回零、读位置）保持 Test 工程原样即可。

> `Acceleration/Deceleration/Jerk` 同理可从 SET_PARAM 的 `acc/dec/jerk` 动态写入，
> 或保留常量 `K300/K300/K3000`。`z_up/z_down` 由 SDK 编入 z 坐标，PLC 无需区分提笔。

---

## 5. 推荐上电时序

```
1. MC_Reset (三轴)          → 清除故障
2. MC_Power(Enable=TRUE)    → 三轴使能（Mode=8）
3. MC_DriverSync            → 驱动器同步
4. MC_AddAxisToGroup        → X/Y/Z 加入轴组 0（见映射表）
5. MC_SetKinTransform       → 载入运动学（KinTransform=4）
6. MC_GroupEnable           → 轴组使能
7. SktTCPAccept             → 监听 2000，等待 SDK 连接
8. 收到 SET_PARAM           → 更新 vel/acc/dec/jerk/z_up/z_down
9. 收到 MOVE_LINEAR 循环    → 写 Target → Execute 触发 → Done 后回 ACK
10. 收到 HOME               → MC_GroupHome
```

---

## 6. 单位换算提示

- SDK 的 `pixel_to_mm`（scale）决定像素到毫米的映射，需按机械实际行程标定。
- `velocity` 单位为 mm/s（世界坐标），与 `MC_MoveLinearAbsolute.Velocity` 的
  REAL 单位一致；具体量纲以 STF 驱动器与运动学配置为准，建议先用低速（如 5~10）
  空跑校验后再提速。
