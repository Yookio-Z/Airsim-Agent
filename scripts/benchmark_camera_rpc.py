"""Bounded, read-only PNG/raw comparison against one AirSim camera."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import airsim
import cv2
import numpy as np

from src.modules.airsim_controller import AirSimController


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=41451)
    parser.add_argument("--camera", default="CameraImage")
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    controller = AirSimController("127.0.0.1", args.port)
    rows = []
    try:
        connection = controller.connect()
        if not connection.connected:
            raise RuntimeError(str(connection.details))
        for pair in range(max(1, min(args.pairs, 10))):
            for compressed in ((True, False) if pair % 2 == 0 else (False, True)):
                if controller.rpc("simIsPause"):
                    raise RuntimeError("Simulator is paused")
                start = time.perf_counter()
                response = controller.rpc(
                    "simGetImages",
                    [airsim.ImageRequest(args.camera, airsim.ImageType.Scene, False, compressed)],
                    timeout=5.0,
                )[0]
                rpc = dict(controller._rpc_timing.last)
                decoded_at = time.perf_counter()
                data = bytes(response.image_data_uint8)
                if compressed:
                    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                else:
                    frame = np.frombuffer(data, np.uint8).reshape(response.height, response.width, 3)
                if frame is None:
                    raise RuntimeError("Invalid frame")
                encode_at = time.perf_counter()
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                end = time.perf_counter()
                row = dict(pair=pair, compressed=compressed, width=response.width,
                           height=response.height, bytes=len(data), **rpc,
                           decode_ms=(encode_at-decoded_at)*1000,
                           jpeg_ms=(end-encode_at)*1000, pipeline_ms=(end-start)*1000)
                rows.append(row)
                print(json.dumps(row), flush=True)
        summary = {}
        for compressed in (True, False):
            selected = [r for r in rows if r["compressed"] == compressed]
            summary["png" if compressed else "raw"] = {
                key: round(statistics.median(r[key] for r in selected), 3)
                for key in ("queue_ms", "service_ms", "decode_ms", "jpeg_ms", "pipeline_ms")
            }
        Path(args.output).write_text(json.dumps(dict(rows=rows, summary=summary), indent=2), encoding="utf-8")
        print(json.dumps(summary), flush=True)
    finally:
        # This client never acquires vehicle control or arms it.
        controller._executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
