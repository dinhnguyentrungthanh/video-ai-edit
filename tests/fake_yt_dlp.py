"""A stand-in for ``python -m yt_dlp`` in the downloader tests. No network.

Behaviour comes from the JSON file named by FAKE_YTDLP_SCENARIO:
  {"probe": {...}, "download": {...}, "by_url": {"<url>": {"probe": ..., "download": ...}}}
Every call appends {"argv", "env", "pid"} to the JSON-lines file FAKE_YTDLP_LOG.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ENV_KEYS = ("DENO_DIR", "DENO_NO_UPDATE_CHECK", "PYTHONUTF8", "TEMP", "TMP", "XDG_CACHE_HOME")


def option_values(argv, name):
    values = []
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def hang():
    while True:
        time.sleep(1)


def probe(scenario):
    if scenario.get("sleep"):
        time.sleep(scenario["sleep"])
    if scenario.get("hang"):
        hang()
    if scenario.get("stderr"):
        sys.stderr.write(scenario["stderr"] + "\n")
    if "raw" in scenario:
        sys.stdout.write(scenario["raw"])
    elif "json" in scenario:
        sys.stdout.write(json.dumps(scenario["json"], ensure_ascii=False))
    return int(scenario.get("exit", 0))


def download(argv, scenario):
    paths = dict(item.split(":", 1) for item in option_values(argv, "-P"))
    home = Path(paths["home"])
    home.mkdir(parents=True, exist_ok=True)
    video_id = scenario.get("id", "vid1")
    part = home / f"{video_id}.mp4.part"
    resumed = "--continue" in argv and part.exists()
    print(f"[fake] resumed={resumed} from={part.stat().st_size if part.exists() else 0}", flush=True)
    if scenario.get("pid_file"):
        Path(scenario["pid_file"]).write_text(str(os.getpid()), encoding="utf-8")
    if scenario.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        Path(scenario["child_pid_file"]).write_text(str(child.pid), encoding="utf-8")
    for line in scenario.get("lines", []):
        print(line, flush=True)
    for step in scenario.get("progress", []):
        downloaded, total, estimate, speed, eta = (["NA" if value is None else value for value in step[:5]])
        name = step[5] if len(step) > 5 else str(part)
        with part.open("ab") as handle:
            handle.write(b"x" * 16)
        print(f"BFPROG downloading {downloaded} {total} {estimate} {speed} {eta} {name}", flush=True)
        time.sleep(scenario.get("step_sleep", 0.01))
    if scenario.get("hang"):
        hang()
    if scenario.get("stderr"):
        sys.stderr.write(scenario["stderr"] + "\n")
    code = int(scenario.get("exit", 0))
    if code:
        return code
    final = home / f"{video_id}.mp4"
    if scenario.get("fixture"):
        shutil.copyfile(scenario["fixture"], final)
    else:
        final.write_bytes(b"fake video")
    part.unlink(missing_ok=True)
    targets = option_values(argv, "--print-to-file")
    if targets and not scenario.get("skip_print"):
        index = argv.index("--print-to-file")
        with open(argv[index + 2], "a", encoding="utf-8") as handle:
            handle.write(str(final) + "\n")
    return 0


def main():
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_YTDLP_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": argv, "pid": os.getpid(),
                                     "env": {key: os.environ.get(key) for key in ENV_KEYS}}) + "\n")
    url = argv[argv.index("--") + 1] if "--" in argv else ""
    with open(os.environ["FAKE_YTDLP_SCENARIO"], encoding="utf-8") as handle:
        scenario = json.load(handle)
    scenario = {**scenario, **scenario.get("by_url", {}).get(url, {})}
    if "-J" in argv or "--dump-single-json" in argv:
        return probe(scenario.get("probe", {}))
    return download(argv, scenario.get("download", {}))


if __name__ == "__main__":
    sys.exit(main())
