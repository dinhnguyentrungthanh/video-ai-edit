"""fix/ticker-subtitle gate, part 2: is a repeated watermark still on screen between its moments?

Runs after gate_rebuild.py jobs. For each job, the subtitle-band repeated-watermark card
of the candidate's fresh queue: EasyOCR (CPU) reads the card's region of the source at
the middle of every moment (the control) and, in every gap between two moments, 1.5 s
after the first moment, at the gap's middle and 1.5 s before the next moment. Per
sample: letters read, a reading of at least LETTERS letters, a web address.

Reads only the sources, the copied queues and models/easyocr; writes only --output.
Prints job ids, seconds and counts: no film names, file names, OCR text or links.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scrolling-text"))
from export_check import FFMPEG, INSTALL, LETTERS, queue_folder  # noqa: E402

PAD = 4  # source pixels read around the card's region
EDGE = 1.5  # seconds after a moment and before the next one


def frame(source: Path, seconds: float, region: dict, frame_size: list[int]) -> tuple[bytes, int, int]:
    x = max(0, int(region["x"]) - PAD)
    y = max(0, int(region["y"]) - PAD)
    width = min(int(frame_size[0]) - x, int(region["width"]) + 2 * PAD) // 2 * 2
    height = min(int(frame_size[1]) - y, int(region["height"]) + 2 * PAD) // 2 * 2
    raw = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", f"crop={width}:{height}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120, check=True).stdout
    return raw, width, height


def reading(reader, raw: bytes, width: int, height: int) -> dict:
    import numpy
    from biliflow.scrolling_text import fold_alnum, web_address_tokens
    image = numpy.frombuffer(raw[:width * height * 3], dtype=numpy.uint8).reshape(height, width, 3)
    texts = [str(text) for _, text, confidence in reader.readtext(image) if float(confidence) >= 0.3]
    letters = sum(len(fold_alnum(text)) for text in texts)
    return {"letters": letters, "readable": any(len(fold_alnum(text)) >= LETTERS for text in texts),
            "web_address": any(web_address_tokens(text) for text in texts)}


def check_job(args, job_id: int, reader) -> dict:
    folder = queue_folder(job_id)
    queue = json.loads((Path(args.gate_work) / "candidate" / folder / "gate-fresh" / "review-queue.json")
                       .read_text(encoding="utf-8"))
    cards = [item for item in queue["items"] if item.get("candidate_type") == "repeated_watermark"
             and item["suggested_region_source_pixels"]["y"] > 0.6 * item["source_frame_size"][1]]
    card = max(cards, key=lambda item: len(item["detected_intervals"]))
    source = Path(queue["source"]["path"])
    region, size = card["suggested_region_source_pixels"], card["source_frame_size"]
    moments = [(float(m["start_seconds"]), float(m["end_seconds"])) for m in card["detected_intervals"]]
    controls = [reading(reader, *frame(source, (a + b) / 2, region, size)) for a, b in moments]
    gaps = []
    for (_, end), (start, _) in zip(moments, moments[1:]):
        length = start - end
        times = [end + EDGE, (end + start) / 2, start - EDGE] if length > 2 * EDGE else [(end + start) / 2]
        samples = [reading(reader, *frame(source, seconds, region, size)) for seconds in times]
        gaps.append({"start": end, "end": start, "seconds": round(length, 3), "samples": samples})
    return {"job": job_id, "moments": len(moments), "region": region, "controls": controls, "gaps": gaps}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-work", required=True, help="the --work folder of gate_rebuild.py jobs")
    parser.add_argument("--candidate", required=True, help="src folder of the candidate tree")
    parser.add_argument("--job", type=int, action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.candidate).resolve(strict=True)))
    import easyocr
    model = INSTALL / "models" / "easyocr"
    reader = easyocr.Reader(["vi", "en"], gpu=False, model_storage_directory=str(model),
                            user_network_directory=str(model), download_enabled=False)
    results = [check_job(args, job_id, reader) for job_id in args.job]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"jobs": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    for result in results:
        controls = result["controls"]
        print(f"job {result['job']}: moments {result['moments']}, readable at the middle of "
              f"{sum(c['readable'] for c in controls)} of {len(controls)}", flush=True)
        for low, high in ((0, 6), (6, 12), (12, 30), (30, 60), (60, 10_000)):
            band = [gap for gap in result["gaps"] if low < gap["seconds"] <= high]
            samples = [sample for gap in band for sample in gap["samples"]]
            print(f"  gaps {low}-{high} s: {len(band)}, any sample readable in "
                  f"{sum(any(s['readable'] for s in g['samples']) for g in band)}, "
                  f"samples readable {sum(s['readable'] for s in samples)} of {len(samples)}, "
                  f"web address {sum(s['web_address'] for s in samples)}", flush=True)


if __name__ == "__main__":
    main()
