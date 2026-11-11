"""PNG 读取与二值化（Otsu 阈值法）。

纯 numpy 实现，无 opencv 依赖。墨迹像素记为 True（1），背景记为 False（0）。
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def load_gray(path: str) -> np.ndarray:
    """读入图片并转成 float64 灰度矩阵，取值 0(黑)~255(白)。"""
    with Image.open(path) as im:
        gray = np.asarray(im.convert("L"), dtype=np.float64)
    if gray.ndim != 2:
        raise ValueError("图片需为单通道灰度或可转换的彩色图: %s" % path)
    return gray


def otsu_threshold(gray: np.ndarray) -> float:
    """计算灰度图的 Otsu 全局阈值（返回 t，墨迹 = 像素 < t）。"""
    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    total = gray.size
    if total == 0:
        return 128.0
    sum_total = float(np.dot(np.arange(256), hist))
    sum_bg = 0.0
    weight_bg = 0
    best_thresh = 128.0
    best_var = -1.0
    # 背景 = 灰度 [0, t)，前景 = [t, 255]
    for t in range(1, 256):
        weight_bg += hist[t - 1]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += (t - 1) * hist[t - 1]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_var:
            best_var = between
            best_thresh = t
    return best_thresh


def binarize(
    gray: np.ndarray,
    threshold: float | None = None,
    invert: bool = False,
) -> np.ndarray:
    """二值化，返回 bool 数组，True = 需要绘制的墨迹。

    参数
    ----
    threshold:
        像素阈值（0~255）。None 时用 Otsu 自动计算。
    invert:
        False —— 较暗像素视为墨迹；True —— 较亮像素视为墨迹。
    """
    if threshold is None:
        threshold = otsu_threshold(gray)
    ink = gray < threshold if not invert else gray >= threshold
    return ink.astype(bool)


def load_binary(path: str, threshold: float | None = None, invert: bool = False) -> np.ndarray:
    """读入 PNG 并二值化，返回 bool 数组。"""
    return binarize(load_gray(path), threshold=threshold, invert=invert)
