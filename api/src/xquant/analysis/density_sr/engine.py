"""
线上支撑/阻力位引擎（生产适配层）

职责：把已在 srlab 里验证过的 V3Fusion 包装成本仓库 API 层可直接用的结构，
不再复制算法逻辑 —— 避免"回测用一套、线上用另一套"这种最常见的失效模式。

与旧实现（``xquant.analysis.sr_levels`` 里的手写版本）的差异，全部与上游
Detect_support_and_resistance_levels 一致：

  1. 成交量分布体积守恒（旧版把整根 K 线成交量计入每个价格箱）
  2. 分箱宽度 = max(tick, 0.25×ATR)，跨标的可比
  3. 区宽夹逼到 [0.3, 1.2]×ATR
  4. 触及事件去重 + 接近方向校验 + 阈值 ATR 化
  5. 排序分只用两个实测有效的因子：log1p(触及次数) + 0.8×陈旧度
  6. 成交量剖面强度改为独立的 p_stall（停滞倾向），不参与排序
  7. 输出"触及概率 × 守住概率"的双概率，以及该位所属历史分位档的实测表现

数据口径：本仓库 K 线来自 ``research.dataset``，复权由数据同步端负责，
因此 ``adjust_*`` 字段只是把数据集来源透传给前端，不再做因子运算。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .base import Ctx
from .data import MIN_BARS, atr_series, tick_size_guess
from .detectors import V3Fusion, V3Params
from .pivots import zigzag
from .probability import ProbabilityModels, features_from_level

_PACKAGE_DIR = Path(__file__).resolve().parent
CALIB_PATH = _PACKAGE_DIR / "calibration" / "score_calibration.json"
PROB_PATH = _PACKAGE_DIR / "calibration" / "prob_models.json"

# 只输出交易上有意义的距离带（与回测口径一致）
MIN_DIST_ATR = 0.5
MAX_DIST_ATR = 5.0

DIRECTION_LABELS = {"long": "只做多", "short": "只做空", "both": "多空都做"}

_CAVEAT = (
    "概率来自 130 只训练股票拟合、130 只互斥测试股票验收的走查回测。"
    "触及概率区分度强（AUC 0.73~0.77）；守住概率区分度弱（AUC 0.59~0.61）但校准良好"
    "（模型说 75% 的那批实际 75.4% 守住），因此它的绝对值可信，"
    "但不宜用来比较两个相邻的位。"
    "另外，控制距离与带宽后本算法与「把同一个位随机平移 1~2 ATR」的对照组"
    "在守住率上无显著差异，所以关键位应当作「约 1~2 ATR 宽的区域」使用，"
    "不要当成精确到分的价格。"
)


class Calibration:
    """
    回测导出的分位边界 + 每档样本外实测表现。

    界面上显示的"Q4 档 · 该档历史守住率 77.8%"里的 77.8% 就来自这里，
    是 100 只**样本外**股票上真实统计出来的，不是模型的预测概率。
    """

    def __init__(self, path: str | Path = CALIB_PATH) -> None:
        self.ok = False
        self.edge_edges: dict[str, list[float]] = {}
        self.stall_edges: dict[str, list[float]] = {}
        self.buckets: dict[tuple[str, str], dict[str, Any]] = {}
        self.meta: dict[str, Any] = {}
        if not Path(path).exists():
            return
        try:
            import json

            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.edge_edges = payload.get("edge_edges", {})
            self.stall_edges = payload.get("stall_edges", {})
            self.meta = payload.get("meta", {})
            for bucket in payload.get("buckets", []):
                self.buckets[(bucket["kind"], bucket["bucket"])] = bucket
            self.ok = bool(self.edge_edges)
        except (OSError, ValueError, KeyError, TypeError):
            self.ok = False

    def bucket_of(self, kind: str, score: float) -> str | None:
        edges = self.edge_edges.get(kind)
        if not edges or not np.isfinite(score):
            return None
        return f"Q{int(np.digitize([score], edges)[0]) + 1}"

    def stall_bucket_of(self, kind: str, stall: float) -> str | None:
        edges = self.stall_edges.get(kind)
        if not edges or not np.isfinite(stall):
            return None
        return f"Q{int(np.digitize([stall], edges)[0]) + 1}"

    def stats(self, kind: str, bucket: str | None) -> dict[str, Any] | None:
        if bucket is None:
            return None
        return self.buckets.get((kind, bucket))


_CALIB: Calibration | None = None
_PROBS: ProbabilityModels | None = None


def get_calibration() -> Calibration:
    global _CALIB
    if _CALIB is None:
        _CALIB = Calibration()
    return _CALIB


def get_prob_models() -> ProbabilityModels:
    """
    双概率模型（触及 + 守住），进程内只加载一次。

    实测表现（上游 out/final，130 只训练 / 130 只互斥测试股票）：
        触及概率  support AUC 0.770 / resistance 0.732
        守住概率  support AUC 0.587 / resistance 0.612
    守住概率的排序能力弱，但校准误差 < 2pp（模型说 75.1% 实际 75.4%），
    所以绝对值可信，不适合用来比较两个相邻的位。
    """
    global _PROBS
    if _PROBS is None:
        _PROBS = ProbabilityModels(str(PROB_PATH))
    return _PROBS


@dataclass
class SREngine:
    params: V3Params | None = None
    n_zones: int = 6

    def __post_init__(self) -> None:
        self.det = V3Fusion(p=self.params or V3Params())

    @staticmethod
    def build_ctx(df: pd.DataFrame, code: str, t: int | None = None) -> Ctx:
        high = df["high"].to_numpy(np.float64)
        low = df["low"].to_numpy(np.float64)
        close = df["close"].to_numpy(np.float64)
        open_ = df["open"].to_numpy(np.float64)
        volume = df["volume"].to_numpy(np.float64)
        atr = atr_series(high, low, close, 14)
        if t is None:
            t = len(close) - 1
        pivots = zigzag(high, low, atr, k_atr=2.0)
        tick = df.attrs.get("tick_base") or tick_size_guess(close)
        return Ctx(
            code=code,
            t=t,
            open_=open_[: t + 1],
            high=high[: t + 1],
            low=low[: t + 1],
            close=close[: t + 1],
            volume=volume[: t + 1],
            atr=float(atr[t]),
            tick=float(tick),
            atr_arr=atr[: t + 1],
            pivots=pivots.view_at(t, max_lookback=600),
        )

    def detect(self, df: pd.DataFrame, code: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """返回 (zones_json, context_info)"""
        if len(df) < MIN_BARS:
            return [], {"error": f"数据不足：{len(df)} 条"}
        ctx = self.build_ctx(df, code)
        atr = max(ctx.atr, 1e-12)
        price = ctx.price

        levels = self.det.detect(ctx)
        # 与回测完全一致的后处理：距离带 + 每侧限流
        kept: dict[str, list[Any]] = {"support": [], "resistance": []}
        for zone in levels:
            if zone.kind == "support" and zone.high >= price:
                continue
            if zone.kind == "resistance" and zone.low <= price:
                continue
            distance = abs(zone.center - price) / atr
            if distance < MIN_DIST_ATR or distance > MAX_DIST_ATR:
                continue
            kept[zone.kind].append(zone)
        out_levels: list[Any] = []
        per_side = max(1, self.n_zones // 2)
        for kind in ("support", "resistance"):
            out_levels += sorted(kept[kind], key=lambda zone: -zone.score)[:per_side]

        calib = get_calibration()
        probs = get_prob_models()
        zones = [self._zone_json(zone, atr, price, calib, probs) for zone in out_levels]
        zones.sort(key=lambda zone: -zone["edge_score"])

        info = {
            "engine": "V3_fusion",
            "atr": round(atr, 6),
            "atr_pct": round(atr / price * 100, 3) if price else None,
            "calibrated": calib.ok,
            "calib_meta": calib.meta,
            "prob_ready": probs.ok,
            "prob_meta": probs.meta,
            "adjust_mode": df.attrs.get("adjust_mode", "unknown"),
            "adjust_source": df.attrs.get("adjust_source", "unknown"),
        }
        return zones, info

    def _zone_json(
        self,
        zone: Any,
        atr: float,
        price: float,
        calib: Calibration,
        probs: ProbabilityModels,
    ) -> dict[str, Any]:
        meta = zone.meta
        n_events = int(meta.get("n_events", 0))
        bucket = calib.bucket_of(zone.kind, zone.score)
        stats = calib.stats(zone.kind, bucket)
        stall_bucket = calib.stall_bucket_of(zone.kind, meta.get("p_stall", float("nan")))

        # --- 双概率 ---
        # 特征里用到了距离和带宽。这与排序分刻意排除它们并不矛盾：
        # 排序分要回答"算法有没有本事定位"（距离是对照组也有的信息，不能算本事），
        # 这里要回答"这个位会不会有效"（距离是合法且最强的预测因子）。
        p_touch = p_hold = p_effective = None
        if probs.ok:
            features = features_from_level(
                dist_atr=zone.dist_atr,
                width_atr=zone.width_atr,
                n_events=n_events,
                stale=float(meta.get("stale", 0.0)),
                vp=float(meta.get("vp", 0.5)),
                close_p=float(meta.get("close", 0.5)),
                atr=atr,
                price=price,
            )
            p_touch = probs.predict("touch", zone.kind, features)
            p_hold = probs.predict("hold", zone.kind, features)
            if p_touch is not None and p_hold is not None:
                p_effective = p_touch * p_hold

        return {
            # --- 双概率（界面主展示）---
            "p_touch": round(p_touch, 4) if p_touch is not None else None,
            "p_hold": round(p_hold, 4) if p_hold is not None else None,
            "p_effective": round(p_effective, 4) if p_effective is not None else None,
            # --- 价格与几何 ---
            "center": round(zone.center, 4),
            "low": round(zone.low, 4),
            "high": round(zone.high, 4),
            "zone_type": zone.kind,
            "zone_label": "支撑带" if zone.kind == "support" else "压力带",
            "distance_pct": round(zone.dist_pct, 2),
            "distance_atr": round(zone.dist_atr, 2),
            "width_atr": round(zone.width_atr, 2),
            "width_pct": round((zone.high - zone.low) / price * 100, 2) if price else None,
            # --- 排序证据（唯一参与排序的两项）---
            "edge_score": round(float(zone.score), 4),
            "n_events": n_events,
            "stale": round(float(meta.get("stale", 0.0)), 3),
            # --- 停滞倾向（不参与排序，用于放止损/目标）---
            "p_stall": round(float(meta.get("p_stall", 0.0)), 3),
            "stall_bucket": stall_bucket,
            "vp_strength": round(float(meta.get("vp", 0.0)), 3),
            # --- 历史分位档 + 该档样本外实测 ---
            "bucket": bucket,
            "bucket_hold_rate": stats["hold_rate"] if stats else None,
            "bucket_fwd_ret": stats["fwd_ret_pct"] if stats else None,
            "bucket_n": stats["n_decided"] if stats else None,
            # --- 多周期共振（由 attach_mtf 填充）---
            "tf_count": 1,
            "tfs": "",
        }


def attach_mtf(
    zones: list[dict[str, Any]],
    mtf: list[dict[str, Any]] | None,
    atr: float,
    merge_atr: float = 0.8,
) -> list[dict[str, Any]]:
    """
    把其他周期的关键位按 ATR 距离并入主周期关键位，标注共振周期数。

    旧实现用 (low, high) 浮点元组做字典键合并，实测合并次数恒为 0；
    这里按中心价距离 <= merge_atr*ATR 判定同一个位。
    """
    tol = max(merge_atr * atr, 1e-12)
    for zone in zones:
        timeframes: set[str] = set()
        for item in mtf or []:
            for other in item.get("zones", []):
                if abs(other["center"] - zone["center"]) <= tol:
                    timeframes.add(item["tf"])
                    break
        if timeframes:
            zone["tfs"] = "+".join(sorted(timeframes))
            zone["tf_count"] = 1 + len(timeframes)
    return zones


def build_summary(
    zones: list[dict[str, Any]],
    df: pd.DataFrame,
    direction: str,
    info: dict[str, Any],
) -> dict[str, Any]:
    """
    生成面向用户的摘要。

    **不给出 0-100 的"综合评分"**：旧评分把 40% 权重压在一个统计口径错误的
    "守住率"上，而实测该概率零区分度。这里只报告可追溯的事实：最近关键位、
    它的历史分位档、该档样本外实测表现、以及趋势状态。
    """
    focus = "support" if direction == "long" else "resistance" if direction == "short" else None
    candidates = [zone for zone in zones if focus is None or zone["zone_type"] == focus]
    trend_label, trend_detail = trend_of(df)

    if not candidates:
        empty_label = "支撑" if direction == "long" else "压力" if direction == "short" else "关键"
        return {
            "direction": direction,
            "direction_label": DIRECTION_LABELS.get(direction, direction),
            "nearest": None,
            "best": None,
            "trend_label": trend_label,
            "trend_detail": trend_detail,
            "headline": f"距离带内无{empty_label}位",
            "caveat": _CAVEAT,
        }

    nearest = min(candidates, key=lambda zone: abs(zone["distance_atr"]))
    best = max(candidates, key=lambda zone: zone["edge_score"])
    parts = [
        (
            f"最近{nearest['zone_label']} {nearest['center']}"
            f"（距现价 {nearest['distance_pct']:+.2f}% / {nearest['distance_atr']:+.2f} ATR，"
            f"带宽 {nearest['width_atr']:.2f} ATR）"
        ),
    ]
    if nearest.get("p_touch") is not None:
        parts.append(f"触及概率 {nearest['p_touch'] * 100:.0f}%")
    if nearest.get("p_hold") is not None:
        parts.append(f"守住概率 {nearest['p_hold'] * 100:.0f}%")
    parts.append(f"历史被测试 {nearest['n_events']} 次")
    parts.append(f"趋势{trend_label}")

    return {
        "direction": direction,
        "direction_label": DIRECTION_LABELS.get(direction, direction),
        "nearest": nearest,
        "best": best,
        "trend_label": trend_label,
        "trend_detail": trend_detail,
        "headline": " · ".join(parts),
        "caveat": _CAVEAT,
    }


def trend_of(df: pd.DataFrame) -> tuple[str, str]:
    closes = df["close"]
    if len(closes) < 26:
        return "数据不足", ""
    ema = closes.ewm(span=20, adjust=False).mean()
    price = float(closes.iloc[-1])
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-6])
    deviation = (price - ema_now) / ema_now * 100 if ema_now else 0.0
    slope = (ema_now - ema_prev) / ema_prev * 100 if ema_prev else 0.0
    score = deviation * 0.6 + slope * 0.4
    label = "上涨" if score > 1.5 else "下跌" if score < -1.5 else "震荡"
    return label, f"现价vsEMA20 {deviation:+.2f}% · EMA20斜率 {slope:+.2f}%"


__all__ = [
    "CALIB_PATH",
    "DIRECTION_LABELS",
    "MAX_DIST_ATR",
    "MIN_DIST_ATR",
    "PROB_PATH",
    "Calibration",
    "SREngine",
    "attach_mtf",
    "build_summary",
    "get_calibration",
    "get_prob_models",
    "trend_of",
]
