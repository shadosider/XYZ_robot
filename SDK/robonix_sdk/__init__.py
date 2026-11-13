"""Robonix XYZ 绘图 SDK。

用法示例::

    from robonix_sdk import RobonixClient, PlotConfig, load_binary, to_waypoints

    binary = load_binary("drawing.png")
    cfg = PlotConfig(pixel_to_mm=0.1, z_up=-40.0, z_down=-50.0, strategy="raster")
    points = to_waypoints(binary, cfg)

    with RobonixClient("192.168.1.10") as plc:
        plc.set_param(cfg.vel, cfg.acc, cfg.dec, cfg.jerk, cfg.z_up, cfg.z_down)
        plc.draw_waypoints(points)
"""

from .protocol import (
    CMD_ACK,
    CMD_CLOSE,
    CMD_HOME,
    CMD_MOVE_LINEAR,
    CMD_PING,
    CMD_SET_PARAM,
    STATUS_BUSY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNKNOWN_CMD,
    Frame,
    build_frame,
    cmd_name,
    crc16_modbus,
    parse_frame,
)
from .client import DEFAULT_PORT, ProtocolError, RobonixClient
from .image import binarize, load_binary, load_gray, otsu_threshold, save_binary
from .trajectory import (
    PlotConfig,
    outline_waypoints,
    raster_waypoints,
    simplify_waypoints,
    to_waypoints,
)
from .text import (
    Cursor,
    TextConfig,
    TextOverflowError,
    TextWriter,
    default_cursor,
    layout_text,
    text_to_waypoints,
)

__all__ = [
    "RobonixClient",
    "ProtocolError",
    "DEFAULT_PORT",
    "PlotConfig",
    "to_waypoints",
    "raster_waypoints",
    "outline_waypoints",
    "simplify_waypoints",
    "TextConfig",
    "TextWriter",
    "Cursor",
    "TextOverflowError",
    "default_cursor",
    "layout_text",
    "text_to_waypoints",
    "load_gray",
    "load_binary",
    "binarize",
    "otsu_threshold",
    "save_binary",
    "Frame",
    "build_frame",
    "parse_frame",
    "crc16_modbus",
    "cmd_name",
    "CMD_PING",
    "CMD_MOVE_LINEAR",
    "CMD_HOME",
    "CMD_SET_PARAM",
    "CMD_CLOSE",
    "CMD_ACK",
    "STATUS_OK",
    "STATUS_BUSY",
    "STATUS_ERROR",
    "STATUS_UNKNOWN_CMD",
]

__version__ = "0.1.0"
