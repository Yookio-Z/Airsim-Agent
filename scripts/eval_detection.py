"""Offline detection evaluation harness.

Purpose: make detection quality **measurable** so a change can be judged instead
of felt. It answers, on a fixed input, four questions:

1. How many pixels does a target actually occupy? (the key diagnostic: too few
   pixels is a sensor problem, not a model problem)
2. How often does it find anything, and how much does that flicker?
3. How stable are the track ids? (id churn is the quantified form of "闪烁")
4. What does it cost in latency, and what detection rate is actually achieved?

Deliberately independent of the product pipeline (`src/modules/yolo_detection.py`):
this harness loads raw ultralytics models so a comparison reflects the *model*,
not the class-canonicalisation / false-positive denylist layered on top. Use
`--via-pipeline` to check the product view of the same clips.

No ground-truth labels are available for the bundled clips, so precision/recall
cannot be computed. Metrics that need labels are marked "proxy" in the output.

Usage:
    python scripts/eval_detection.py --list-models
    python scripts/eval_detection.py --max-frames 150
    python scripts/eval_detection.py --configs coco@640 coco@1280 world@640
    python scripts/eval_detection.py --clips third_party/PixEagle/resources/test1.mp4
"""

from __future__ import annotations

import argparse
import glob
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CLIP_GLOB = "third_party/PixEagle/resources/test*.mp4"

# label -> (weights, imgsz). Kept explicit so a run is reproducible from the CLI.
MODEL_PRESETS: dict[str, tuple[str, int]] = {
    "coco@640": ("models/yolov8s.pt", 640),
    "coco@960": ("models/yolov8s.pt", 960),
    "coco@1280": ("models/yolov8s.pt", 1280),
    "world@640": ("models/yolov8s-worldv2.pt", 640),
    "world@1280": ("models/yolov8s-worldv2.pt", 1280),
}

# Classes we actually care about, mapped from COCO / VisDrone names.
VEHICLE_CLASSES = {"car", "truck", "bus", "van", "motor", "motorcycle", "bicycle", "person"}

# YOLO-World needs an explicit vocabulary (it has no fixed class list).
WORLD_VOCAB = ["car", "truck", "bus", "van", "person", "motorcycle", "bicycle"]

# Small-object thresholds from the literature: COCO's "small" cutoff is 32x32 px.
SMALL_PX = 32.0


@dataclass
class RunStats:
    """Metrics for one (model, resolution) over one clip or the whole set."""

    label: str
    frames: int = 0
    frames_with_det: int = 0
    detections: int = 0
    unique_ids: int = 0
    id_switches: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    box_diagonals: list[float] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    class_counts: dict[str, int] = field(default_factory=dict)
    count_jumps: int = 0
    error: str = ""

    # -- derived ------------------------------------------------------

    @property
    def detection_rate(self) -> float:
        """Proxy for recall: share of frames where anything was found at all."""
        return self.frames_with_det / self.frames if self.frames else 0.0

    @property
    def dets_per_frame(self) -> float:
        return self.detections / self.frames if self.frames else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    @property
    def achieved_fps(self) -> float:
        """Detection rate actually achieved (1 / mean latency), not the target."""
        return 1000.0 / self.mean_latency_ms if self.mean_latency_ms > 0 else 0.0

    @property
    def median_box_px(self) -> float:
        return statistics.median(self.box_diagonals) if self.box_diagonals else 0.0

    @property
    def pct_small(self) -> float:
        """Share of boxes whose diagonal is under the COCO 'small' cutoff."""
        if not self.box_diagonals:
            return 0.0
        return sum(1 for d in self.box_diagonals if d < SMALL_PX) / len(self.box_diagonals)

    @property
    def median_conf(self) -> float:
        return statistics.median(self.confidences) if self.confidences else 0.0

    def merge(self, other: "RunStats") -> None:
        self.frames += other.frames
        self.frames_with_det += other.frames_with_det
        self.detections += other.detections
        self.unique_ids += other.unique_ids
        self.id_switches += other.id_switches
        self.latencies_ms.extend(other.latencies_ms)
        self.box_diagonals.extend(other.box_diagonals)
        self.confidences.extend(other.confidences)
        self.count_jumps += other.count_jumps
        for name, count in other.class_counts.items():
            self.class_counts[name] = self.class_counts.get(name, 0) + count


def _load_model(weights: str, label: str):
    from ultralytics import YOLO

    path = REPO_ROOT / weights
    if not path.exists():
        raise FileNotFoundError(f"weights not found: {path}")
    model = YOLO(str(path))
    if "world" in label:
        # Open-vocabulary model: it has no fixed class list, so the vocabulary
        # must be set before inference.
        model.set_classes(WORLD_VOCAB)
    return model


def _iter_frames(clip: Path, max_frames: int, stride: int):
    import cv2

    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        cap.release()
        return
    try:
        index = 0
        emitted = 0
        while emitted < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                emitted += 1
                yield frame
            index += 1
    finally:
        cap.release()


def evaluate_clip(
    model,
    label: str,
    clip: Path,
    max_frames: int,
    stride: int,
    conf: float,
    warmup: int,
    target_class: str = "",
) -> RunStats:
    """Run one model over one clip, collecting GT-free stability metrics.

    ``target_class`` restricts counting to one class. This matters because a
    mixed-class aggregate is dominated by whichever class is most common (person,
    on the bundled clips), which hides the car-specific behaviour.
    """
    import numpy as np

    stats = RunStats(label=label)
    imgsz = MODEL_PRESETS[label][1]
    wanted = str(target_class or "").strip().lower()
    previous_ids: set[int] = set()
    previous_count: int | None = None

    for frame_index, frame in enumerate(_iter_frames(clip, max_frames, stride)):
        started = time.perf_counter()
        try:
            # model.track keeps ByteTrack state across calls, which is what makes
            # id-churn measurable; the first frames are warmup.
            results = model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                verbose=False,
                conf=conf,
                imgsz=imgsz,
            )
        except Exception as exc:  # noqa: BLE001
            stats.error = f"{type(exc).__name__}: {exc}"
            return stats
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if frame_index < warmup:
            continue

        stats.frames += 1
        stats.latencies_ms.append(elapsed_ms)

        boxes = results[0].boxes if results else None
        count = 0
        current_ids: set[int] = set()
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            confs = boxes.conf.cpu().numpy()
            ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None
            names = results[0].names

            for i in range(len(xyxy)):
                class_name = str(names.get(int(cls[i]), cls[i]))
                if class_name not in VEHICLE_CLASSES:
                    continue
                if wanted and class_name != wanted:
                    continue
                count += 1
                width = float(xyxy[i][2] - xyxy[i][0])
                height = float(xyxy[i][3] - xyxy[i][1])
                stats.box_diagonals.append(float(np.hypot(width, height)))
                stats.confidences.append(float(confs[i]))
                stats.class_counts[class_name] = stats.class_counts.get(class_name, 0) + 1
                if ids is not None:
                    current_ids.add(int(ids[i]))

        stats.detections += count
        if count > 0:
            stats.frames_with_det += 1

        # Id churn: new ids appearing per frame. A perfectly stable tracker on N
        # concurrent objects creates N ids total; churn means ids keep being
        # re-created, which is what flicker looks like numerically.
        stats.unique_ids += len(current_ids - previous_ids)
        previous_ids = current_ids

        # Count discontinuity: detections appearing/disappearing frame to frame.
        if previous_count is not None and abs(count - previous_count) > 0:
            stats.count_jumps += 1
        previous_count = count

    return stats


def evaluate_pipeline(clip: Path, max_frames: int, stride: int, conf: float) -> RunStats:
    """Evaluate the PRODUCT pipeline instead of raw models.

    Uses `detect_objects_stateless`, so this view includes the canonical-class
    mapping and the false-positive denylist the product applies.
    """
    from src.modules.yolo_detection import detect_objects_stateless

    stats = RunStats(label="pipeline")
    for frame_index, frame in enumerate(_iter_frames(clip, max_frames, stride)):
        started = time.perf_counter()
        try:
            detections = detect_objects_stateless(frame, target_class="car", confidence=conf)
        except Exception as exc:  # noqa: BLE001
            stats.error = f"{type(exc).__name__}: {exc}"
            return stats
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if frame_index == 0:
            continue  # first call loads the model

        stats.frames += 1
        stats.latencies_ms.append(elapsed_ms)
        stats.detections += len(detections)
        if detections:
            stats.frames_with_det += 1
        for det in detections:
            bbox = det.get("bbox") or [0, 0, 0, 0]
            width = float(bbox[2] - bbox[0])
            height = float(bbox[3] - bbox[1])
            stats.box_diagonals.append(float((width * width + height * height) ** 0.5))
            stats.confidences.append(float(det.get("confidence") or 0.0))
            name = str(det.get("class") or "?")
            stats.class_counts[name] = stats.class_counts.get(name, 0) + 1
    return stats


def resolve_clips(pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        found = sorted(Path(p) for p in glob.glob(str(REPO_ROOT / pattern)))
    else:
        found = [REPO_ROOT / pattern]
    return [p for p in found if p.exists()]


def print_report(stats_by_label: dict[str, RunStats], clips: list[Path]) -> None:
    print()
    print("=" * 118)
    print(f"DETECTION EVALUATION — {len(clips)} clip(s), no ground truth (metrics marked proxy cannot be precision/recall)")
    print("=" * 118)
    header = (
        f"{'model':<12} {'frames':>7} {'det/frame':>10} {'found%':>7} {'proxy recall':>13} "
        f"{'box px med':>11} {'small%':>7} {'id churn/f':>11} {'jump%':>7} "
        f"{'conf med':>9} {'ms med':>8} {'Hz':>6}"
    )
    print(header)
    print("-" * 118)
    for label, stats in stats_by_label.items():
        if stats.error:
            print(f"{label:<12} ERROR: {stats.error}")
            continue
        ordered = sorted(stats.latencies_ms)
        median_ms = ordered[len(ordered) // 2] if ordered else 0.0
        found_pct = stats.detection_rate * 100.0
        # The pipeline path is stateless (no tracker), so id churn and count
        # jumps are not measured there -- show n/a rather than a misleading 0.00.
        if label == "pipeline":
            churn_cell, jump_cell = "n/a", "n/a"
            churn, jump_pct = 0.0, 0.0
        else:
            churn = stats.unique_ids / stats.frames if stats.frames else 0.0
            jump_pct = (stats.count_jumps / stats.frames * 100.0) if stats.frames else 0.0
            churn_cell, jump_cell = f"{churn:.2f}", f"{jump_pct:.1f}%"
        print(
            f"{label:<12} {stats.frames:>7} {stats.dets_per_frame:>10.2f} {found_pct:>6.1f}% "
            f"{found_pct:>12.1f}% {stats.median_box_px:>11.1f} {stats.pct_small * 100:>6.1f}% "
            f"{churn_cell:>11} {jump_cell:>7} {stats.median_conf:>9.2f} {median_ms:>8.1f} "
            f"{1000.0 / median_ms if median_ms else 0:>6.2f}"
        )
        if stats.class_counts:
            top = sorted(stats.class_counts.items(), key=lambda kv: -kv[1])[:6]
            print(f"{'':<12}   classes: " + ", ".join(f"{n}={c}" for n, c in top))
    print("-" * 118)
    print("legend:")
    print("  det/frame     mean detections per frame")
    print("  found%        share of frames with >=1 detection  (PROXY for recall — no labels)")
    print("  box px med    median bbox diagonal in pixels  <- the key diagnostic")
    print(f"  small%        share of boxes under {SMALL_PX:.0f}px diagonal (COCO 'small' cutoff)")
    print("  id churn/f    new track ids created per frame  <- quantified flicker (lower is better)")
    print("  jump%         share of frames whose detection count changed vs the previous frame")
    print("  conf med      median confidence of kept detections (threshold tuning input)")
    print("  Hz            1 / median latency = detection rate actually achieved")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline detection evaluation harness")
    parser.add_argument("--clips", default=DEFAULT_CLIP_GLOB, help="clip path or glob")
    parser.add_argument("--max-frames", type=int, default=120, help="frames sampled per clip")
    parser.add_argument("--stride", type=int, default=5, help="sample every Nth frame")
    parser.add_argument("--conf", type=float, default=0.08, help="confidence threshold (product default is 0.08)")
    parser.add_argument("--target-class", default="", help="restrict counting to one class, e.g. car (default: all)")
    parser.add_argument("--sweep-conf", default="", help="comma-separated thresholds: run only the sweep table for --target-class")
    parser.add_argument("--warmup", type=int, default=3, help="frames skipped while the tracker warms up")
    parser.add_argument("--configs", nargs="*", default=["coco@640", "coco@1280", "world@640"],
                        help=f"subset of {sorted(MODEL_PRESETS)}")
    parser.add_argument("--via-pipeline", action="store_true",
                        help="also evaluate the product pipeline (canonical class + denylist)")
    parser.add_argument("--list-models", action="store_true", help="print presets and exit")
    args = parser.parse_args()

    if args.list_models:
        print("available configs (label -> weights @ imgsz):")
        for label, (weights, imgsz) in MODEL_PRESETS.items():
            exists = (REPO_ROOT / weights).exists()
            print(f"  {label:<12} {weights} @ {imgsz}  {'OK' if exists else 'MISSING'}")
        return 0

    clips = resolve_clips(args.clips)
    if not clips:
        print(f"no clips matched: {args.clips}", file=sys.stderr)
        return 2

    unknown = [c for c in args.configs if c not in MODEL_PRESETS and c != "pipeline"]
    if unknown:
        print(f"unknown config(s): {unknown}. known: {sorted(MODEL_PRESETS)}", file=sys.stderr)
        return 2

    print(f"clips: {len(clips)} | frames/clip: {args.max_frames} | stride: {args.stride} | conf: {args.conf}")
    if args.target_class:
        print(f"counting class: {args.target_class} only")

    # Threshold sweep: the highest-value single experiment, because the shipped
    # threshold (0.08) sits far below the observed median confidence, so it
    # admits a long tail of marginal detections -- which is where flicker lives.
    if args.sweep_conf:
        thresholds = [float(v) for v in args.sweep_conf.split(",") if v.strip()]
        label = args.configs[0]
        model = _load_model(MODEL_PRESETS[label][0], label)
        print()
        print("=" * 100)
        print(f"CONFIDENCE SWEEP — {label}, class={args.target_class or 'all'}")
        print("=" * 100)
        print(f"{'conf':>6} {'found%':>8} {'det/frame':>10} {'box px':>8} {'id churn/f':>11} {'jump%':>7} {'Hz':>6}")
        print("-" * 100)
        for threshold in thresholds:
            combined = RunStats(label=f"{label}@{threshold}")
            for clip in clips:
                combined.merge(
                    evaluate_clip(model, label, clip, args.max_frames, args.stride, threshold,
                                  args.warmup, args.target_class)
                )
            if combined.error:
                print(f"{threshold:>6.2f}  ERROR: {combined.error}")
                continue
            ordered = sorted(combined.latencies_ms)
            median_ms = ordered[len(ordered) // 2] if ordered else 0.0
            jump_pct = (combined.count_jumps / combined.frames * 100.0) if combined.frames else 0.0
            churn = combined.unique_ids / combined.frames if combined.frames else 0.0
            print(
                f"{threshold:>6.2f} {combined.detection_rate * 100:>7.1f}% {combined.dets_per_frame:>10.2f} "
                f"{combined.median_box_px:>8.1f} {churn:>11.2f} {jump_pct:>6.1f}% "
                f"{1000.0 / median_ms if median_ms else 0:>6.2f}"
            )
        print("-" * 100)
        print("read it as: higher threshold should cut churn/jumps (stability) at some cost in found%")
        print("(recall). Pick the knee -- the point where found% has not yet dropped but churn has.")
        print()
        return 0

    stats_by_label: dict[str, RunStats] = {}
    for label in args.configs:
        try:
            model = _load_model(MODEL_PRESETS[label][0], label)
        except Exception as exc:  # noqa: BLE001
            stats_by_label[label] = RunStats(label=label, error=str(exc))
            continue
        combined = RunStats(label=label)
        for clip in clips:
            print(f"  running {label:<12} on {clip.name} ...", flush=True)
            combined.merge(
                evaluate_clip(model, label, clip, args.max_frames, args.stride, args.conf,
                              args.warmup, args.target_class)
            )
        stats_by_label[label] = combined
        del model

    if args.via_pipeline:
        combined = RunStats(label="pipeline")
        for clip in clips:
            print(f"  running {'pipeline':<12} on {clip.name} ...", flush=True)
            combined.merge(evaluate_pipeline(clip, args.max_frames, args.stride, args.conf))
        stats_by_label["pipeline"] = combined

    print_report(stats_by_label, clips)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
