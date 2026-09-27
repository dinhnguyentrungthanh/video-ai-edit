from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def motion_features(video: Path, center: float, seconds: float = 2.0, fps: float = 8.0) -> dict:
    capture = cv2.VideoCapture(str(video))
    start = max(0.0, center - seconds / 2)
    frames = []
    for offset in np.arange(0.0, seconds, 1.0 / fps):
        capture.set(cv2.CAP_PROP_POS_MSEC, (start + float(offset)) * 1000.0)
        ok, frame = capture.read()
        if not ok:
            continue
        gray = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
        frames.append(gray)
    capture.release()
    differences = []
    flows = []
    for before, after in zip(frames, frames[1:]):
        differences.append(float(cv2.absdiff(before, after).mean()) / 255.0)
        flow = cv2.calcOpticalFlowFarneback(before, after, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        magnitude = np.linalg.norm(flow, axis=2)
        flows.append(float(np.percentile(magnitude, 90)))
    return {
        "frames": len(frames),
        "mean_absdiff": round(float(np.mean(differences)) if differences else 0.0, 6),
        "p90_absdiff": round(float(np.percentile(differences, 90)) if differences else 0.0, 6),
        "mean_flow_p90": round(float(np.mean(flows)) if flows else 0.0, 6),
        "max_flow_p90": round(float(np.max(flows)) if flows else 0.0, 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scan = json.loads(args.scan.read_text(encoding="utf-8"))
    rows = []
    for item in scan.get("top_candidates", [])[: args.top]:
        timestamp = float(item["timestamp_seconds"])
        rows.append({**item, **motion_features(args.video, timestamp)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
