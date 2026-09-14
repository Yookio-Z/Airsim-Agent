"""轻量单/少目标跟踪器：IoU 关联 + 恒速外推 + 短时保持。

设计目标（对应"检测框一直锁定、不抖不丢"）：
- 检测结果做 IoU 关联，保持稳定 track_id；
- 某帧漏检时，用恒速外推的预测框继续显示（predict），而不是立刻消失；
- 超过 keep_alive_s 仍未重捕获才判定丢失。

只依赖 numpy，避免引入 MOT 框架；单/少目标 + 有深度先验的场景足够。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Track:
    track_id: int
    cls: str
    bbox: list[float]
    conf: float
    last_ts: float
    hits: int = 1
    # 检测器的原始类别标签（规范类别统一为 class，原始值保留用于排查）
    raw_class: str = ""
    # 中心点速度（像素/秒），用于漏检时的外推
    vx: float = 0.0
    vy: float = 0.0
    _prev_center: tuple[float, float] | None = field(default=None, repr=False)


class IouTracker:
    """极简跟踪器：贪心 IoU 关联 + 恒速外推保持。"""

    def __init__(
        self,
        iou_threshold: float = 0.25,
        keep_alive_s: float = 2.0,
        predict_decay: float = 0.7,
        max_tracks: int = 12,
    ) -> None:
        self.iou_threshold = float(iou_threshold)
        self.keep_alive_s = float(keep_alive_s)
        self.predict_decay = float(predict_decay)
        self.max_tracks = int(max_tracks)
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1

    @staticmethod
    def _center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    def update(self, detections: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """关联本帧检测，返回带 track_id / predicted 标记的目标列表。"""
        dets = [d for d in (detections or []) if isinstance(d, dict) and d.get("bbox")]
        unmatched_dets = list(range(len(dets)))
        matched_track_ids: set[int] = set()

        # 贪心：按 IoU 从高到低配对
        pairs: list[tuple[float, int, int]] = []
        track_ids = list(self._tracks.keys())
        for di, det in enumerate(dets):
            for tid in track_ids:
                score = _iou([float(v) for v in det["bbox"]], self._tracks[tid].bbox)
                if score >= self.iou_threshold:
                    pairs.append((score, di, tid))
        pairs.sort(reverse=True, key=lambda item: item[0])
        used_dets: set[int] = set()
        for _score, di, tid in pairs:
            if di in used_dets or tid in matched_track_ids:
                continue
            det = dets[di]
            track = self._tracks[tid]
            bbox = [float(v) for v in det["bbox"]]
            center = self._center(bbox)
            if track._prev_center is not None:
                dt = max(now - track.last_ts, 1e-3)
                nvx = (center[0] - track._prev_center[0]) / dt
                nvy = (center[1] - track._prev_center[1]) / dt
                # EMA 平滑，避免单帧跳变
                track.vx = 0.6 * track.vx + 0.4 * nvx
                track.vy = 0.6 * track.vy + 0.4 * nvy
            track._prev_center = center
            track.bbox = bbox
            track.conf = float(det.get("confidence") or 0.0)
            track.cls = str(det.get("class") or track.cls)
            if det.get("raw_class"):
                track.raw_class = str(det["raw_class"])
            track.last_ts = now
            track.hits += 1
            # 外部跟踪器（ByteTrack）给出的 id 优先：把轨迹改键到该 id，
            # 保证显示编号与跟踪器一致。
            external = det.get("track_id")
            try:
                ext_id = int(external) if external is not None else None
            except (TypeError, ValueError):
                ext_id = None
            if ext_id is not None and ext_id != tid and ext_id not in self._tracks:
                self._tracks.pop(tid, None)
                track.track_id = ext_id
                self._tracks[ext_id] = track
                matched_track_ids.add(ext_id)
                self._next_id = max(self._next_id, ext_id + 1)
            used_dets.add(di)
            matched_track_ids.add(tid)
        unmatched_dets = [i for i in range(len(dets)) if i not in used_dets]

        # 未匹配检测 → 新轨迹（当帧是实测，不是预测）。
        # 若检测自带外部 track_id（ByteTrack），直接采用它作为轨迹 id，
        # 这样"外推保持"的预测框与 ByteTrack 的 id 保持一致，不会出现两套编号。
        fresh_ids: set[int] = set()
        for di in unmatched_dets:
            det = dets[di]
            bbox = [float(v) for v in det["bbox"]]
            external = det.get("track_id")
            try:
                tid = int(external) if external is not None else None
            except (TypeError, ValueError):
                tid = None
            if tid is None or tid in self._tracks:
                tid = self._next_id
                self._next_id += 1
            else:
                self._next_id = max(self._next_id, tid + 1)
            self._tracks[tid] = _Track(
                raw_class=str(det.get("raw_class") or ""),
                track_id=tid,
                cls=str(det.get("class") or ""),
                bbox=bbox,
                conf=float(det.get("confidence") or 0.0),
                last_ts=now,
                _prev_center=self._center(bbox),
            )
            fresh_ids.add(tid)

        # 汇总输出：命中轨迹 + 未超时的外推轨迹
        out: list[dict[str, Any]] = []
        expired: list[int] = []
        for tid, track in self._tracks.items():
            age = now - track.last_ts
            if age > self.keep_alive_s:
                expired.append(tid)
                continue
            if tid in matched_track_ids or tid in fresh_ids:
                bbox = list(track.bbox)
                conf = track.conf
                predicted = False
            else:
                dt = age
                w = track.bbox[2] - track.bbox[0]
                h = track.bbox[3] - track.bbox[1]
                bbox = [
                    track.bbox[0] + track.vx * dt,
                    track.bbox[1] + track.vy * dt,
                    track.bbox[0] + track.vx * dt + w,
                    track.bbox[1] + track.vy * dt + h,
                ]
                conf = float(track.conf) * (self.predict_decay ** max(dt, 0.0))
                predicted = True
            cx, cy = self._center(bbox)
            entry = {
                "class": track.cls,
                "confidence": round(max(0.0, min(1.0, conf)), 2),
                "bbox": [round(v, 1) for v in bbox],
                "center": [round(cx), round(cy)],
                "track_id": tid,
                "predicted": predicted,
                "age_s": round(age, 2),
            }
            raw = getattr(track, "raw_class", "")
            if raw:
                entry["raw_class"] = raw
            out.append(entry)
        for tid in expired:
            self._tracks.pop(tid, None)

        # 轨迹数上限：丢弃最久未更新的
        if len(self._tracks) > self.max_tracks:
            for tid, _ in sorted(self._tracks.items(), key=lambda kv: kv[1].last_ts)[: len(self._tracks) - self.max_tracks]:
                self._tracks.pop(tid, None)
        return out

    @staticmethod
    def pick_primary(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
        """目标优先级：实测命中 > 外推；同组内取置信度高者。"""
        if not targets:
            return None
        measured = [t for t in targets if not t.get("predicted")]
        pool = measured or targets
        return max(pool, key=lambda t: float(t.get("confidence") or 0.0))
