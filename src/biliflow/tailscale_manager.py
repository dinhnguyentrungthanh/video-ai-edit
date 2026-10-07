"""Tailscale managed from BiliFlow: Dashboard V2 → Cài đặt → Tailscale (docs/TAILSCALE_PLAN.md).

The user chose (2026-10-06) to let BiliFlow download, install, configure, sign in and run Tailscale in
the background, so the phone mode can be opened from outside the home Wi-Fi. Tailscale goes to its
default folder, %ProgramFiles%\\Tailscale (the user's choice on 2026-10-07 after the security review:
its SYSTEM service must not run from E:\\DungChung\\BiliFlow, which every local account can modify);
the installer cache, logs and temp files stay under the BiliFlow root. Nothing here runs by itself: every action is a task the user starts on the PC
(127.0.0.1 + token), one at a time. The only elevated code is scripts/tailscale-setup.ps1, started
through windows_elevation after the UAC prompt; the installer is checked (published SHA-256 and the
Tailscale Inc. signature) before the prompt and again inside the script.

Imported only by control_center.py (outside the stage-cache fingerprint). Standard library only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from biliflow import __version__, phone_access, windows_elevation

SERVICE_NAME = "Tailscale"
SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Tailscale"
PKGS_INDEX_URL = "https://pkgs.tailscale.com/stable/?mode=json"
PKGS_BASE_URL = "https://pkgs.tailscale.com/stable/"
MSI_NAME = re.compile(r"tailscale-setup-[0-9]+(?:\.[0-9]+){1,3}-amd64\.msi")  # ASCII digits only
SHA256_HEX = re.compile(r"\b[0-9a-fA-F]{64}\b")
SHA256_FULL = re.compile(r"[0-9a-f]{64}")
ELEVATED_ACTIONS = ("install", "start", "firewall")  # what scripts/tailscale-setup.ps1 knows
LOGIN_URL = re.compile(r"https://login\.tailscale\.com/[A-Za-z0-9/_\-]+")
SIGNER = re.compile(r"(?:^|,\s*)(?:O|CN)=Tailscale Inc\.(?:,|$)")
FIREWALL_RULE = "BiliFlow phone mode Tailscale (Python, TCP 8767)"
MAX_INDEX_BYTES = 1 << 20
MAX_MSI_BYTES = 300 << 20
DOWNLOAD_TIMEOUT_SECONDS = 60.0     # per socket read
DOWNLOAD_DEADLINE_SECONDS = 30 * 60  # the whole download: a trickling server cannot hold the one task slot
STATUS_CACHE_SECONDS = 2.0          # GET /api/tailscale runs sc.exe and the CLI at most this often
CLI_TIMEOUT_SECONDS = 10.0
SIGNATURE_TIMEOUT_SECONDS = 60.0
ELEVATED_TIMEOUT_SECONDS = 15 * 60
LOGIN_WAIT_SECONDS = 10 * 60
CONNECT_WAIT_SECONDS = 30.0
SERVICE_WAIT_SECONDS = 30.0
POLL_SECONDS = 0.5
FIREWALL_CACHE_SECONDS = 30.0
# The user's choice (2026-10-07): a device of the PC's own Tailscale account opens the phone mode
# without the code. Who a device is comes from `tailscale whois` (tailscaled knows the WireGuard key
# each packet came with); answers are reused per address for a while (a refusal for less).
WHOIS_CACHE_SECONDS = 60.0
WHOIS_MISS_SECONDS = 10.0
WHOIS_TIMEOUT_SECONDS = 5.0
WHOIS_CACHE_SIZE = 256
WHOIS_WAIT_SECONDS = 1.0  # how long a request waits for another address's lookup before the code page
_MISS = object()
DEVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,62}")
UNNAMED_DEVICE = "thiết bị Tailscale"
ACTIONS = {
    "install": "Cài và cấu hình Tailscale",
    "start-service": "Khởi động dịch vụ Tailscale",
    "firewall": "Tạo rule tường lửa",
    "login": "Đăng nhập Tailscale",
    "up": "Kết nối Tailscale",
    "down": "Ngắt Tailscale",
    "logout": "Đăng xuất Tailscale",
    "remote-on": "Mở cho điện thoại ngoài nhà",
}
SERVICE_STATES = {"STOPPED": "stopped", "START_PENDING": "starting", "STOP_PENDING": "stopping",
                  "RUNNING": "running", "CONTINUE_PENDING": "starting", "PAUSE_PENDING": "stopping",
                  "PAUSED": "stopped"}
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BUSY_MESSAGE = "Đang có một việc Tailscale chạy; chờ nó xong rồi bấm lại."
UNKNOWN_ACTION_MESSAGE = "Thao tác Tailscale không hợp lệ."
NOT_INSTALLED_MESSAGE = "Chưa cài Tailscale: bấm “Cài và cấu hình Tailscale”."
SERVICE_DOWN_MESSAGE = "Dịch vụ Tailscale đang tắt: bấm “Khởi động dịch vụ Tailscale”."
NEEDS_LOGIN_MESSAGE = "Chưa đăng nhập Tailscale: bấm “Đăng nhập Tailscale” trước."
DECLINED_MESSAGE = "Bạn đã từ chối hộp hỏi quyền Admin của Windows nên bước này không chạy."
URL_MESSAGE = "Chỉ tải từ máy chủ chính thức của Tailscale (https://…tailscale.com)."
TOO_BIG_MESSAGE = "File tải về lớn bất thường; đã dừng."
TOO_SLOW_MESSAGE = "Tải bản cài quá 30 phút; đã dừng. Bấm lại để thử tiếp."
WORK_DIR_MESSAGE = "Thư mục temp\\tailscale hoặc cache\\tailscale là liên kết (junction/symlink); không chạy bước quyền Admin."
INDEX_MESSAGE = "Không đọc được danh sách bản cài của Tailscale."
HASH_MESSAGE = "Bản cài tải về không khớp mã SHA-256 Tailscale công bố; đã dừng, không cài."
SIGNATURE_MESSAGE = "Bản cài không có chữ ký số hợp lệ của Tailscale Inc. ({status}; {subject}); đã dừng, không cài."
SCRIPT_MESSAGE = "Thiếu scripts\\tailscale-setup.ps1 trong thư mục BiliFlow."
NO_RESULT_MESSAGE = "Bước chạy quyền Admin không để lại kết quả (mã thoát {code})."
STEP_MESSAGE = "Bước chạy quyền Admin báo lỗi: {detail}"
LOGIN_TIMEOUT_MESSAGE = "Quá 10 phút chưa đăng nhập xong; bấm “Đăng nhập Tailscale” để thử lại."
LOGIN_FAILED_MESSAGE = "Chưa đăng nhập được Tailscale ({detail})."
CONNECT_TIMEOUT_MESSAGE = "Tailscale chưa kết nối xong sau 30 giây ({backend})."
SERVICE_TIMEOUT_MESSAGE = "Dịch vụ Tailscale chưa chạy sau 30 giây."
CLI_MESSAGE = "Lệnh tailscale {args} báo lỗi: {detail}"
PHONE_WIFI_MESSAGE = ("Chế độ điện thoại đang bật cho Wi-Fi nhà. Tắt nó trong khung “Mở trên điện thoại” "
                      "rồi bấm lại “Mở cho điện thoại ngoài nhà”.")


def _system_directory() -> str:
    """The real System32 from Windows (GetSystemDirectoryW), not from an environment variable."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):
            return buffer.value
    except (ImportError, AttributeError, OSError):
        pass
    return r"C:\Windows\System32"


def _system32(*parts: str) -> str:
    return str(Path(_system_directory(), *parts))


def _is_link(path: Path) -> bool:
    """A symlink or a junction (any reparse point): the elevated step never works through one."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & 0x400) or path.is_symlink()


POWERSHELL = _system32("WindowsPowerShell", "v1.0", "powershell.exe")


def program_files_dir(program_files: str | None = None) -> Path:
    """Where the MSI installs Tailscale: %ProgramFiles%\\Tailscale, writable by administrators only."""
    return Path(program_files or os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale"


def cache_dir(root: Path) -> Path:
    return Path(root) / "cache" / "tailscale"


def work_dir(root: Path) -> Path:
    return Path(root) / "temp" / "tailscale"


def setup_script(root: Path) -> Path:
    return Path(root) / "scripts" / "tailscale-setup.ps1"


# ---------------------------------------------------------------------------- finding Tailscale
def read_service_image_path() -> str | None:
    """ImagePath of the Tailscale service (tailscaled.exe), or None when it is not installed."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SERVICE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "ImagePath")
    except OSError:
        return None
    return str(value)


def image_dir(image_path: str | None) -> Path | None:
    """Folder of the service executable in an ImagePath (quoted or not, maybe with arguments)."""
    match = re.match(r'\s*"?(.+?\.exe)', os.path.expandvars(image_path or ""), re.IGNORECASE)
    return Path(match.group(1)).parent if match else None


def find_cli(*, image_path: Callable[[], str | None] = read_service_image_path,
             program_files: str | None = None) -> str | None:
    """tailscale.exe beside the installed service, else in %ProgramFiles%\\Tailscale.

    PATH, the current folder and the BiliFlow tree (writable by every local account) are never
    searched. The service comes first: an auto-update may have moved the program.
    """
    folders = [image_dir(image_path()), program_files_dir(program_files)]
    for folder in folders:
        if folder is not None and (folder / "tailscale.exe").is_file():
            return str(folder / "tailscale.exe")
    return None


def _quiet(run: Callable[..., Any], command: list[str], *, timeout: float = CLI_TIMEOUT_SECONDS,
           env: dict[str, str] | None = None) -> Any:
    return run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
               timeout=timeout, creationflags=_CREATE_NO_WINDOW, **({"env": env} if env else {}))


def service_state(run: Callable[..., Any] = subprocess.run) -> str:
    """running / stopped / starting / stopping / missing / unknown, from `sc.exe query Tailscale`."""
    try:
        done = _quiet(run, [_system32("sc.exe"), "query", SERVICE_NAME])
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if done.returncode == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
        return "missing"
    match = re.search(r"\b\d\s+(" + "|".join(SERVICE_STATES) + r")\b", done.stdout or "")
    return SERVICE_STATES[match.group(1)] if match else "unknown"


def parse_status(raw: dict[str, Any]) -> dict[str, Any]:
    """The fields the panel shows, from `tailscale status --json` (no address of another device)."""
    me = raw.get("Self") if isinstance(raw.get("Self"), dict) else {}
    ips = [ip for ip in me.get("TailscaleIPs") or [] if phone_access.is_tailscale_ipv4(ip)]
    users = raw.get("User") if isinstance(raw.get("User"), dict) else {}
    user = users.get(str(me.get("UserID"))) or {}
    peers = []
    for peer in (raw.get("Peer") or {}).values() if isinstance(raw.get("Peer"), dict) else []:
        if isinstance(peer, dict):
            peers.append({"name": str(peer.get("HostName") or "?")[:80], "os": str(peer.get("OS") or "")[:20],
                          "online": bool(peer.get("Online"))})
    peers.sort(key=lambda peer: (not peer["online"], peer["name"].casefold()))
    auth = str(raw.get("AuthURL") or "")
    return {
        "backend": str(raw.get("BackendState") or "Unknown"),
        "version": str(raw.get("Version") or "") or None,
        "ip": ips[0] if ips else None,
        "hostname": str(me.get("HostName") or "") or None,
        "tailnet": str((raw.get("CurrentTailnet") or {}).get("Name") or "") or None,
        "user": str(user.get("LoginName") or "") or None,
        "peers": peers[:50],
        "auth_url": auth if LOGIN_URL.fullmatch(auth) else None,
    }


def firewall_rule_state(run: Callable[..., Any] = subprocess.run) -> str:
    """enabled / disabled / missing / unknown for the BiliFlow Tailscale rule (reading needs no admin)."""
    script = (f"$r = Get-NetFirewallRule -DisplayName '{FIREWALL_RULE}' -ErrorAction SilentlyContinue; "
              "if ($r) { [string]$r[0].Enabled } else { 'missing' }")
    try:
        done = _quiet(run, [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], timeout=20.0)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return {"True": "enabled", "False": "disabled", "missing": "missing"}.get((done.stdout or "").strip(), "unknown")


# ---------------------------------------------------------------------------- the installer
def require_tailscale_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").casefold()
    if parts.scheme != "https" or not (host == "tailscale.com" or host.endswith(".tailscale.com")):
        raise ValueError(URL_MESSAGE)


class _TailscaleOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        require_tailscale_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_get(url: str, *, max_bytes: int, dest: Path | None = None,
              on_progress: Callable[[int, int | None], None] | None = None) -> bytes:
    """GET from *.tailscale.com only (redirects too). With `dest`: streamed to dest.part, then renamed."""
    require_tailscale_url(url)
    opener = urllib.request.build_opener(_TailscaleOnlyRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": f"BiliFlow/{__version__}"})
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        require_tailscale_url(response.geturl())
        total = int(response.headers.get("Content-Length") or 0) or None
        if total and total > max_bytes:
            raise ValueError(TOO_BIG_MESSAGE)
        if dest is None:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(TOO_BIG_MESSAGE)
            return data
        part = dest.with_name(dest.name + ".part")
        received, deadline = 0, time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
        try:
            with part.open("wb") as handle:
                while chunk := response.read(1 << 16):
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(TOO_BIG_MESSAGE)
                    if time.monotonic() > deadline:
                        raise ValueError(TOO_SLOW_MESSAGE)
                    handle.write(chunk)
                    if on_progress is not None:
                        on_progress(received, total)
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)  # only BiliFlow's own unfinished download
        return b""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def authenticode(path: Path, run: Callable[..., Any] = subprocess.run) -> tuple[str, str]:
    """(status, signer subject) of a file's Authenticode signature; the path goes through the environment."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:BILIFLOW_SIGNED_FILE; "
              "[pscustomobject]@{status = [string]$s.Status; subject = [string]$s.SignerCertificate.Subject} "
              "| ConvertTo-Json -Compress")
    done = _quiet(run, [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                  timeout=SIGNATURE_TIMEOUT_SECONDS, env={**os.environ, "BILIFLOW_SIGNED_FILE": str(path)})
    try:
        value = json.loads(done.stdout or "")
    except ValueError:
        return "Unknown", ""
    return str(value.get("status") or "Unknown"), str(value.get("subject") or "")


def require_tailscale_signature(path: Path, run: Callable[..., Any] = subprocess.run) -> None:
    status, subject = authenticode(path, run)
    if status != "Valid" or not SIGNER.search(subject):
        raise ValueError(SIGNATURE_MESSAGE.format(status=status, subject=subject[:200] or "không có"))


# ---------------------------------------------------------------------------- the manager
class TailscaleManager:
    """Status and one-at-a-time tasks; every method is thread-safe."""

    def __init__(self, root: Path, *, run: Callable[..., Any] = subprocess.run,
                 popen: Callable[..., Any] = subprocess.Popen,
                 fetch: Callable[..., bytes] = https_get,
                 elevate: Callable[..., int] = windows_elevation.run_elevated,
                 image_path: Callable[[], str | None] = read_service_image_path,
                 program_files: str | None = None,
                 phone_enable: Callable[[], dict[str, Any]] | None = None,
                 on_event: Callable[[str, str, dict[str, Any]], None] | None = None):
        self.root = Path(root)
        self._run, self._popen, self._fetch, self._elevate = run, popen, fetch, elevate
        self._image_path, self._program_files = image_path, program_files
        self.phone_enable = phone_enable
        self.on_event = on_event
        self._lock = threading.Lock()
        self._task: dict[str, Any] | None = None
        self._login: Any = None
        self._auth_url: str | None = None
        self._firewall: tuple[float, str] | None = None
        self._stopping = threading.Event()
        # status(): one read at a time, reused for STATUS_CACHE_SECONDS; a task step or end drops it.
        self._status_lock = threading.Lock()
        self._status_cache: tuple[float, dict[str, Any]] | None = None
        self._status_generation = 0
        # same_account_device(): one lookup at a time, {ip: (monotonic time, device name or None)}.
        self._whois_lock = threading.Lock()
        self._whois: dict[str, tuple[float, str | None]] = {}

    # ------------------------------------------------------------------ reading
    def cli(self) -> str | None:
        return find_cli(image_path=self._image_path, program_files=self._program_files)

    def address(self) -> str:
        """The PC's Tailscale IPv4 for the phone mode (`tailscale ip -4`)."""
        return phone_access.tailscale_address(run=self._run, cli=self.cli)

    def _status_json(self, cli: str, *, timeout: float = CLI_TIMEOUT_SECONDS) -> dict[str, Any]:
        done = _quiet(self._run, [cli, "status", "--json"], timeout=timeout)
        try:
            value = json.loads(done.stdout or "")
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise ValueError(CLI_MESSAGE.format(args="status", detail=_first_line(done)))
        return value

    def same_account_device(self, ip: Any) -> str | None:
        """The name of the Tailscale device at `ip` when it belongs to the PC's own Tailscale user.

        None for anything else: not a 100.64.0.0/10 address, another user's device, a device shared in
        from another tailnet, a tagged device, no CLI, or any error. Then the phone types the code.
        """
        if not isinstance(ip, str) or not phone_access.is_tailscale_ipv4(ip):
            return None
        cached = self._cached_device(ip)
        if cached is not _MISS:
            return cached
        # One lookup at a time; a cached answer never waits for it, and a busy lookup is a refusal.
        if not self._whois_lock.acquire(timeout=WHOIS_WAIT_SECONDS):
            return None
        try:
            cached = self._cached_device(ip)
            if cached is not _MISS:
                return cached
            device = self._lookup_device(ip)
            with self._lock:
                if len(self._whois) >= WHOIS_CACHE_SIZE:
                    self._whois.clear()
                self._whois[ip] = (time.monotonic(), device)
            return device
        finally:
            self._whois_lock.release()

    def _cached_device(self, ip: str) -> Any:
        """The cached answer for `ip` (a name or None) while it is fresh, else _MISS."""
        with self._lock:
            cached = self._whois.get(ip)
        if cached is None:
            return _MISS
        fresh = WHOIS_CACHE_SECONDS if cached[1] else WHOIS_MISS_SECONDS
        return cached[1] if time.monotonic() - cached[0] < fresh else _MISS

    def _lookup_device(self, ip: str) -> str | None:
        cli = self.cli()
        if cli is None:
            return None
        try:
            me = self._status_json(cli, timeout=WHOIS_TIMEOUT_SECONDS).get("Self")
            done = _quiet(self._run, [cli, "whois", "--json", ip], timeout=WHOIS_TIMEOUT_SECONDS)
            who = json.loads(done.stdout or "") if done.returncode == 0 else None
        except (ValueError, OSError, subprocess.SubprocessError):
            return None
        if not isinstance(me, dict) or not isinstance(who, dict):
            return None
        # Security review (MEDIUM): the PC's own address is never a device without the code; a relay on
        # the PC (port proxy, tunnel, another account's process) would arrive from it.
        own = me.get("TailscaleIPs")
        if not isinstance(own, list) or ip in own:
            return None
        node, profile = who.get("Node"), who.get("UserProfile")
        owner = me.get("UserID")
        if not isinstance(node, dict) or not isinstance(profile, dict):
            return None
        if not isinstance(owner, int) or isinstance(owner, bool) or owner <= 0:
            return None
        if node.get("User") != owner or profile.get("ID") != owner or node.get("Sharer") or node.get("Tags"):
            return None
        name = node.get("ComputedName")
        return name if isinstance(name, str) and DEVICE_NAME.fullmatch(name) else UNNAMED_DEVICE

    def _firewall_state(self) -> str:
        now = time.monotonic()
        with self._lock:
            cached = self._firewall
        if cached is not None and now - cached[0] < FIREWALL_CACHE_SECONDS:
            return cached[1]
        state = firewall_rule_state(self._run)
        with self._lock:
            self._firewall = (now, state)
        return state

    def status(self) -> dict[str, Any]:
        """The panel's state; the task part is always current, the rest at most STATUS_CACHE_SECONDS old."""
        with self._status_lock:
            with self._lock:
                cached, generation = self._status_cache, self._status_generation
            if cached is not None and time.monotonic() - cached[0] < STATUS_CACHE_SECONDS:
                value = dict(cached[1])
            else:
                value = self._read_status()
                with self._lock:
                    if self._status_generation == generation:
                        self._status_cache = (time.monotonic(), dict(value))
        with self._lock:
            value["task"] = dict(self._task) if self._task else None
            if self._auth_url:
                value["auth_url"] = self._auth_url
        return value

    def _drop_status(self) -> None:
        """Call with self._lock held: the next status() reads Tailscale again."""
        self._status_cache = None
        self._status_generation += 1

    def _read_status(self) -> dict[str, Any]:
        cli = self.cli()
        service = service_state(self._run) if cli is not None else "missing"
        value: dict[str, Any] = {
            "installed": cli is not None, "cli": cli,
            "install_dir": str(Path(cli).parent) if cli else str(program_files_dir(self._program_files)),
            "service": service, "backend": None, "version": None, "ip": None, "hostname": None,
            "tailnet": None, "user": None, "peers": [], "auth_url": None, "status_error": None,
            "firewall_rule": self._firewall_state(),
        }
        if cli is not None and service == "running":
            try:
                value.update(parse_status(self._status_json(cli)))
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                value["status_error"] = str(error)
        return value

    # ------------------------------------------------------------------ tasks
    def start(self, action: Any) -> dict[str, Any]:
        """Start one task in the background; the panel follows it through status()."""
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError(UNKNOWN_ACTION_MESSAGE)
        worker = getattr(self, "_do_" + action.replace("-", "_"))
        with self._lock:
            if self._task is not None and self._task.get("running"):
                raise ValueError(BUSY_MESSAGE)
            self._task = {"action": action, "label": ACTIONS[action], "running": True, "ok": None,
                          "stage": "start", "message": ACTIONS[action] + "…", "error": None,
                          "progress": None, "started_at": time.time(), "finished_at": None}
        threading.Thread(target=self._run_task, args=(action, worker), daemon=True,
                         name=f"biliflow-tailscale-{action}").start()
        return self.status()

    def _run_task(self, action: str, worker: Callable[[], str]) -> None:
        ok, message, error = False, None, None
        try:
            message, ok = worker(), True
        except windows_elevation.ElevationDeclined:
            error = DECLINED_MESSAGE
        except (ValueError, OSError, TimeoutError, subprocess.SubprocessError) as problem:
            error = str(problem) or type(problem).__name__
        except Exception as problem:  # noqa: BLE001 - a task must always end with a reason
            error = f"Lỗi không mong đợi: {type(problem).__name__}"
        with self._lock:
            if self._task is not None:
                self._task.update(running=False, ok=ok, error=error, finished_at=time.time(),
                                  message=message if ok else self._task.get("message"))
            self._firewall = None  # read the rule again after any task
            self._drop_status()
        self._event("TAILSCALE_TASK", f"Tailscale: {ACTIONS[action]} — {message if ok else 'lỗi: ' + str(error)}",
                    action=action, ok=ok, error=error)

    def _stage(self, stage: str, message: str, progress: dict[str, Any] | None = None) -> None:
        with self._lock:
            if self._task is not None:
                if self._task.get("stage") != stage:
                    self._drop_status()  # a new step (e.g. installed, signed in): read Tailscale again
                self._task.update(stage=stage, message=message, progress=progress)

    def _event(self, event_type: str, message: str, **payload: Any) -> None:
        callback = self.on_event
        if callback is not None:
            try:
                callback(event_type, message, payload)
            except Exception:  # noqa: BLE001 - an event store problem must not break a task
                pass

    def close(self) -> None:
        """Control Center stop: end a waiting sign-in."""
        self._stopping.set()
        with self._lock:
            process = self._login
        if process is not None and process.poll() is None:
            with contextlib.suppress(OSError):  # it may end on its own meanwhile; the stop goes on
                process.terminate()

    # ------------------------------------------------------------------ helpers
    def _require_cli(self) -> str:
        cli = self.cli()
        if cli is None:
            raise ValueError(NOT_INSTALLED_MESSAGE)
        return cli

    def _state(self, cli: str) -> dict[str, Any]:
        if service_state(self._run) != "running":
            raise ValueError(SERVICE_DOWN_MESSAGE)
        return parse_status(self._status_json(cli))

    def _cli_ok(self, cli: str, args: list[str], *, timeout: float = 60.0) -> None:
        done = _quiet(self._run, [cli, *args], timeout=timeout)
        if done.returncode != 0:
            raise ValueError(CLI_MESSAGE.format(args=" ".join(args), detail=_first_line(done)))

    def _wait(self, check: Callable[[], bool], seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not self._stopping.is_set():
            if check():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)
        return False

    def _wait_running(self, cli: str) -> None:
        last = {"backend": "?"}

        def running() -> bool:
            try:
                last.update(parse_status(self._status_json(cli)))
            except (ValueError, OSError, subprocess.SubprocessError):
                return False
            return last["backend"] == "Running"
        if not self._wait(running, CONNECT_WAIT_SECONDS):
            raise ValueError(CONNECT_TIMEOUT_MESSAGE.format(backend=last["backend"]))

    def _ensure_service(self) -> None:
        if service_state(self._run) == "running":
            return
        self._stage("service", "Đang khởi động dịch vụ Tailscale: Windows sẽ hỏi quyền Admin…")
        self._elevated("start")
        if not self._wait(lambda: service_state(self._run) == "running", SERVICE_WAIT_SECONDS):
            raise ValueError(SERVICE_TIMEOUT_MESSAGE)

    def _elevated(self, action: str, *, msi_name: str | None = None, sha256: str | None = None) -> dict[str, Any]:
        """Run scripts/tailscale-setup.ps1 as administrator and read its JSON result.

        Everything the script acts on is on its command line, fixed when Windows starts it: the action,
        and for an install the MSI name and its expected SHA-256 (no request file another process could
        rewrite while the UAC prompt waits). Only the result comes back through temp\\tailscale.
        """
        script = setup_script(self.root)
        if not script.is_file() or '"' in str(script):
            raise ValueError(SCRIPT_MESSAGE)
        if action not in ELEVATED_ACTIONS:
            raise ValueError(UNKNOWN_ACTION_MESSAGE)
        arguments = f"-Action {action}"
        if action == "install":
            if not (msi_name and MSI_NAME.fullmatch(msi_name) and sha256 and SHA256_FULL.fullmatch(sha256)):
                raise ValueError(INDEX_MESSAGE)
            arguments += f" -MsiName {msi_name} -Sha256 {sha256}"
        folder = work_dir(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        if _is_link(folder.parent) or _is_link(folder):
            raise ValueError(WORK_DIR_MESSAGE)
        stamp = uuid.uuid4().hex[:16]
        result = folder / f"result-{stamp}.json"
        result.unlink(missing_ok=True)
        try:
            code = self._elevate(POWERSHELL, f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden '
                                             f'-File "{script}" {arguments} -ResultId {stamp}',
                                 timeout=ELEVATED_TIMEOUT_SECONDS)
            try:
                value = json.loads(result.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as error:
                raise ValueError(NO_RESULT_MESSAGE.format(code=code)) from error
        finally:
            result.unlink(missing_ok=True)
        # Both must say so: the script exits 0 only after it wrote a successful result.
        if not isinstance(value, dict) or value.get("ok") is not True or code != 0:
            detail = value.get("error") if isinstance(value, dict) else None
            raise ValueError(STEP_MESSAGE.format(detail=detail or f"mã thoát {code}"))
        return value

    # ------------------------------------------------------------------ the tasks
    def _do_install(self) -> str:
        self._stage("index", "Đang hỏi bản Tailscale mới nhất…")
        try:
            index = json.loads(self._fetch(PKGS_INDEX_URL, max_bytes=MAX_INDEX_BYTES).decode("utf-8"))
        except ValueError as error:
            raise ValueError(INDEX_MESSAGE) from error
        name = str(((index or {}).get("MSIs") or {}).get("amd64") or "") if isinstance(index, dict) else ""
        if not MSI_NAME.fullmatch(name):
            raise ValueError(INDEX_MESSAGE)
        published = SHA256_HEX.search(self._fetch(PKGS_BASE_URL + name + ".sha256", max_bytes=4096)
                                      .decode("ascii", "replace"))
        if published is None:
            raise ValueError(INDEX_MESSAGE)
        expected = published.group(0).lower()
        cache_dir(self.root).mkdir(parents=True, exist_ok=True)
        if _is_link(cache_dir(self.root).parent) or _is_link(cache_dir(self.root)):
            raise ValueError(WORK_DIR_MESSAGE)
        msi = cache_dir(self.root) / name
        if not (msi.is_file() and sha256_of(msi) == expected):
            self._stage("download", f"Đang tải {name}…")
            self._fetch(PKGS_BASE_URL + name, max_bytes=MAX_MSI_BYTES, dest=msi,
                        on_progress=lambda done, total: self._stage(
                            "download", f"Đang tải {name}…", {"done": done, "total": total}))
        self._stage("verify", "Đang kiểm mã SHA-256 và chữ ký số Tailscale Inc.…")
        if sha256_of(msi) != expected:
            raise ValueError(HASH_MESSAGE)
        require_tailscale_signature(msi, self._run)
        self._stage("elevate", "Đang cài: Windows sẽ hỏi quyền Admin (không thấy hộp hỏi thì bấm biểu tượng "
                               "nhấp nháy trên thanh tác vụ)…")
        self._elevated("install", msi_name=name, sha256=expected)
        self._stage("check", "Đang kiểm tra Tailscale vừa cài…")
        self._require_cli()
        if not self._wait(lambda: service_state(self._run) == "running", SERVICE_WAIT_SECONDS):
            raise ValueError(SERVICE_TIMEOUT_MESSAGE)
        return "Đã cài và cấu hình Tailscale. Bước tiếp theo: “Đăng nhập Tailscale”."

    def _do_start_service(self) -> str:
        self._require_cli()
        self._ensure_service()
        return "Dịch vụ Tailscale đang chạy."

    def _do_firewall(self) -> str:
        self._stage("elevate", "Đang tạo rule tường lửa: Windows sẽ hỏi quyền Admin…")
        self._elevated("firewall")
        return "Đã tạo rule tường lửa cho Tailscale (cổng 8767)."

    def _do_login(self) -> str:
        cli = self._require_cli()
        self._ensure_service()
        if self._state(cli)["backend"] == "Running":
            return "Tailscale đã đăng nhập."
        self._stage("login", "Đang lấy link đăng nhập Tailscale…")
        process = self._popen([cli, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                              creationflags=_CREATE_NO_WINDOW)
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=_pump, args=(process, lines), daemon=True).start()
        with self._lock:
            self._login = process
        try:
            return self._await_login(cli, process, lines)
        finally:
            with self._lock:
                self._login, self._auth_url = None, None
            if process.poll() is None:
                process.terminate()
            with contextlib.suppress(OSError, ValueError, AttributeError):
                process.stdout.close()  # the reader thread ends on the closed pipe

    def _await_login(self, cli: str, process: Any, lines: queue.Queue[str]) -> str:
        deadline = time.monotonic() + LOGIN_WAIT_SECONDS
        tail: list[str] = []
        while process.poll() is None:
            if self._stopping.is_set() or time.monotonic() >= deadline:
                raise TimeoutError(LOGIN_TIMEOUT_MESSAGE)
            try:
                line = lines.get(timeout=POLL_SECONDS)
            except queue.Empty:
                continue
            # The sign-in link never reaches an error text (and the event log): it signs this PC in.
            tail = (tail + [LOGIN_URL.sub("<link>", line.strip())])[-3:]
            url = LOGIN_URL.search(line)
            if url and not self._auth_url:
                with self._lock:
                    self._auth_url = url.group(0)
                self._stage("waiting_login", "Bấm “Mở trang đăng nhập Tailscale” và đăng nhập trong trình duyệt…")
        state = parse_status(self._status_json(cli))
        if state["backend"] != "Running":
            raise ValueError(LOGIN_FAILED_MESSAGE.format(detail=" ".join(tail)[:200] or state["backend"]))
        return f"Đã đăng nhập Tailscale{' (' + state['user'] + ')' if state['user'] else ''}."

    def _do_up(self) -> str:
        cli = self._require_cli()
        self._ensure_service()
        state = self._state(cli)
        if state["backend"] == "NeedsLogin":
            raise ValueError(NEEDS_LOGIN_MESSAGE)
        if state["backend"] != "Running":
            self._stage("connect", "Đang kết nối Tailscale…")
            self._cli_ok(cli, ["up"])
            self._wait_running(cli)
        return "Tailscale đã kết nối."

    def _do_down(self) -> str:
        self._cli_ok(self._require_cli(), ["down"])
        return "Đã ngắt Tailscale (điện thoại ngoài nhà không vào được cho tới khi kết nối lại)."

    def _do_logout(self) -> str:
        self._cli_ok(self._require_cli(), ["logout"])
        return "Đã đăng xuất Tailscale trên PC."

    def _do_remote_on(self) -> str:
        self._do_up()
        if self.phone_enable is None:
            raise ValueError("Control Center chưa sẵn sàng để bật chế độ điện thoại.")
        self._stage("phone", "Đang bật chế độ điện thoại qua Tailscale…")
        status = self.phone_enable()
        if status.get("network") != "tailscale":
            raise ValueError(PHONE_WIFI_MESSAGE)
        return f"Đã mở cho điện thoại ngoài nhà: {status.get('url')} (mã trong khung “Mở trên điện thoại”)."


def _pump(process: Any, lines: "queue.Queue[str]") -> None:
    """Copy a process's output lines into a queue (a reader that blocks must not block the task)."""
    try:
        for line in process.stdout:
            lines.put(line)
    except (OSError, ValueError):
        pass


def _first_line(done: Any) -> str:
    for text in (getattr(done, "stderr", "") or "", getattr(done, "stdout", "") or ""):
        for line in text.splitlines():
            if line.strip():
                return line.strip()[:200]
    return f"mã thoát {getattr(done, 'returncode', '?')}"
