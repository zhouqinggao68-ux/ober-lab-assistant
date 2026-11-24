"""Local WGC + RapidOCR + dhash watcher MCP server for OBER virtual simulation experiments."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import mss
import numpy as np
import win32gui
from fastmcp import FastMCP
from PIL import Image

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[ober-ocr-mcp] %(levelname)s: %(message)s")
logger = logging.getLogger("ober-ocr-mcp")

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PLUGIN_ROOT / "presets"
CAPTURES_DIR = PLUGIN_ROOT / "captures"
PRESETS_DIR.mkdir(parents=True, exist_ok=True)
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DWMWA_EXTENDED_FRAME_BOUNDS = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
UOI_NAME = 2
MAX_WATCH_TIMEOUT_SEC = 140.0

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_MENU = 0x12
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wt.DWORD),
        ("union", _INPUT_UNION),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


def _enable_dpi_awareness() -> None:
    """Ensure physical pixel coordinates match Windows display scaling."""
    with contextlib.suppress(Exception):
        _user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


_enable_dpi_awareness()

_ocr_lock = threading.Lock()
_ocr_engine: Any | None = None


def _get_ocr():
    """Lazily initialize RapidOCR while keeping stdout untouched for MCP stdio."""
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            with contextlib.redirect_stdout(sys.stderr):
                from rapidocr_onnxruntime import RapidOCR

                _ocr_engine = RapidOCR()
    return _ocr_engine


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    win_rect: tuple[int, int, int, int]
    ext_rect: tuple[int, int, int, int]
    client_origin: tuple[int, int]
    client_size: tuple[int, int]
    is_minimized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "hwnd_hex": hex(self.hwnd),
            "title": self.title,
            "process_name": self.process_name,
            "window_rect": list(self.win_rect),
            "extended_rect": list(self.ext_rect),
            "client_origin": list(self.client_origin),
            "client_size": list(self.client_size),
            "is_minimized": self.is_minimized,
        }


def _get_current_desktop_name() -> str:
    """Return the WinSta/Desktop name (e.g. Default vs exebox-*) for diagnostics."""
    try:
        tid = _kernel32.GetCurrentThreadId()
        hdesk = _user32.GetThreadDesktop(tid)
        if not hdesk:
            return "unknown"
        buf = ctypes.create_unicode_buffer(256)
        needed = wt.DWORD(0)
        if _user32.GetUserObjectInformationW(hdesk, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return buf.value
    except Exception:
        pass
    return "unknown"


def _get_process_name(hwnd: int) -> str:
    """Resolve executable basename for a window handle without extra dependencies."""
    pid = wt.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wt.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
    finally:
        _kernel32.CloseHandle(handle)
    return ""


def _get_extended_bounds(hwnd: int, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Read DWM extended frame bounds (without invisible resize shadows)."""
    rect = wt.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr == 0:
        return (rect.left, rect.top, rect.right, rect.bottom)
    return fallback


def _inspect_hwnd(hwnd: int, include_minimized: bool = False) -> WindowInfo | None:
    if not win32gui.IsWindowVisible(hwnd):
        return None
    is_min = bool(win32gui.IsIconic(hwnd))
    if is_min and not include_minimized:
        return None
    title = win32gui.GetWindowText(hwnd)
    if not title:
        return None
    win_rect = win32gui.GetWindowRect(hwnd)
    if not is_min and (win_rect[2] - win_rect[0] < 40 or win_rect[3] - win_rect[1] < 40):
        return None
    if is_min:
        ext_rect = win_rect
        cx, cy = win_rect[0], win_rect[1]
        cw, ch = 0, 0
    else:
        ext_rect = _get_extended_bounds(hwnd, win_rect)
        try:
            cl_l, cl_t, cl_r, cl_b = win32gui.GetClientRect(hwnd)
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            cw, ch = max(1, cl_r - cl_l), max(1, cl_b - cl_t)
        except Exception:
            cx, cy = win_rect[0], win_rect[1]
            cw, ch = max(1, win_rect[2] - win_rect[0]), max(1, win_rect[3] - win_rect[1])
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        process_name=_get_process_name(hwnd),
        win_rect=win_rect,
        ext_rect=ext_rect,
        client_origin=(cx, cy),
        client_size=(cw, ch),
        is_minimized=is_min,
    )


def _list_visible_windows(include_minimized: bool = False) -> list[WindowInfo]:
    """Enumerate top-level visible windows via ctypes EnumWindows (optionally including minimized)."""
    windows: list[WindowInfo] = []
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        info = _inspect_hwnd(hwnd, include_minimized=include_minimized)
        if info is not None:
            windows.append(info)
        return True

    _user32.EnumWindows(enum_proc(_callback), 0)
    return windows


def _resolve_window(window_query: str, include_minimized: bool = False) -> WindowInfo | None:
    """Find best matching window by HWND hex/int, process name, or title substring."""
    if not window_query.strip():
        return None
    q = window_query.strip()
    windows = _list_visible_windows(include_minimized=include_minimized)
    if q.lower().startswith("0x"):
        try:
            target_hwnd = int(q, 16)
            for w in windows:
                if w.hwnd == target_hwnd:
                    return w
        except ValueError:
            pass
    ql = q.lower()
    for w in windows:
        if w.process_name.lower() == ql or ql in w.title.lower():
            return w
    for w in windows:
        if ql in w.process_name.lower():
            return w
    raise ValueError(f"未找到匹配的可见窗口: '{window_query}'。可先调用 probe_desktop_environment 查看可见窗口列表。")


def _capture_wgc(hwnd: int | None, timeout_sec: float = 2.5) -> np.ndarray:
    """Capture a single BGR frame using Windows Graphics Capture (WGC)."""
    import windows_capture

    captured: list[np.ndarray] = []
    err_holder: list[Exception] = []
    done = threading.Event()

    def _run(draw_border: bool | None) -> None:
        kwargs: dict[str, Any] = {"cursor_capture": False}
        if draw_border is not None:
            kwargs["draw_border"] = draw_border
        if hwnd is not None:
            kwargs["window_hwnd"] = hwnd
        else:
            kwargs["monitor_index"] = 1

        cap = windows_capture.WindowsCapture(**kwargs)

        @cap.event
        def on_frame_arrived(frame, capture_control):
            if not captured:
                captured.append(frame.convert_to_bgr().frame_buffer.copy())
            capture_control.stop()
            done.set()

        @cap.event
        def on_closed():
            done.set()

        try:
            cap.start()
        except Exception as exc:
            err_holder.append(exc)
            done.set()

    for border_opt in (False, None):
        captured.clear()
        err_holder.clear()
        done.clear()
        t = threading.Thread(target=_run, args=(border_opt,), daemon=True)
        t.start()
        done.wait(timeout=timeout_sec)
        t.join(timeout=0.5)
        if captured:
            return captured[0]
        if border_opt is False and err_holder:
            continue
        break

    detail = str(err_holder[0]) if err_holder else "timeout waiting for WGC frame"
    raise RuntimeError(f"WGC capture failed: {detail}")


def _capture_mss_rect(left: int, top: int, width: int, height: int) -> np.ndarray:
    """Capture a physical screen rectangle using mss and return BGR array."""
    with mss.mss() as sct:
        shot = sct.grab({"left": int(left), "top": int(top), "width": max(1, int(width)), "height": max(1, int(height))})
        arr = np.asarray(shot, dtype=np.uint8)
        return arr[:, :, :3].copy()


def _get_foreground_info() -> tuple[int, str]:
    """Return (hwnd, title) of the current Windows foreground window."""
    try:
        hwnd = int(_user32.GetForegroundWindow() or 0)
        if hwnd:
            return hwnd, win32gui.GetWindowText(hwnd)
    except Exception:
        pass
    return 0, ""


def _capture_target(
    window_query: str = "", backend: str = "auto", ensure_foreground: bool = False
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int], WindowInfo | None, str, bool, str]:
    """Capture target window or primary monitor.

    In 'auto' mode, prefer fast, crash-free mss capture first and only fall back
    to WGC if mss returns an all-black frame (e.g., exclusive fullscreen DX).
    Returns (bgr_image, frame_origin, client_origin, window_info, backend_used, is_foreground, fg_title).
    """
    win = _resolve_window(window_query, include_minimized=ensure_foreground) if window_query.strip() else None
    backend = backend.lower().strip() or "auto"

    if win is None:
        fg_hwnd, fg_title = _get_foreground_info()
        with mss.mss() as sct:
            mon = sct.monitors[1]
            origin = (int(mon["left"]), int(mon["top"]))
            size = (int(mon["width"]), int(mon["height"]))
        if backend == "wgc":
            img = _capture_wgc(None)
            return img, origin, origin, None, "wgc", True, fg_title
        img = _capture_mss_rect(origin[0], origin[1], size[0], size[1])
        if backend == "auto" and int(img.max()) == 0:
            try:
                img = _capture_wgc(None)
                return img, origin, origin, None, "wgc", True, fg_title
            except Exception as exc:
                logger.warning("Primary WGC fallback failed: %s", exc)
        return img, origin, origin, None, "mss", True, fg_title

    fg_hwnd, fg_title = _get_foreground_info()
    if ensure_foreground and (win.is_minimized or fg_hwnd != win.hwnd):
        was_minimized = win.is_minimized
        _bring_hwnd_to_front(win.hwnd)
        time.sleep(0.12 if was_minimized else 0.08)
        win = _inspect_hwnd(win.hwnd, include_minimized=True) or win
        fg_hwnd, fg_title = _get_foreground_info()

    is_fg = fg_hwnd == win.hwnd

    def _capture_via_wgc() -> tuple[np.ndarray, tuple[int, int], tuple[int, int], WindowInfo | None, str, bool, str]:
        img_wgc = _capture_wgc(win.hwnd)
        fh, fw = img_wgc.shape[:2]
        ew = win.ext_rect[2] - win.ext_rect[0]
        eh = win.ext_rect[3] - win.ext_rect[1]
        ww = win.win_rect[2] - win.win_rect[0]
        wh = win.win_rect[3] - win.win_rect[1]
        if abs(fw - ew) <= 4 and abs(fh - eh) <= 4:
            origin = (win.ext_rect[0], win.ext_rect[1])
        elif abs(fw - ww) <= 4 and abs(fh - wh) <= 4:
            origin = (win.win_rect[0], win.win_rect[1])
        else:
            origin = win.ext_rect[:2]
        return img_wgc, origin, win.client_origin, win, "wgc", is_fg, fg_title

    if backend == "auto" and not is_fg and not win.is_minimized:
        try:
            return _capture_via_wgc()
        except Exception as exc:
            logger.warning("Background WGC fallback failed (%s), falling back to MSS: %s", win.title, exc)

    if backend != "wgc":
        cx, cy = win.client_origin
        cw, ch = win.client_size
        img = _capture_mss_rect(cx, cy, cw, ch)
        if backend == "mss" or int(img.max()) > 0:
            return img, (cx, cy), (cx, cy), win, "mss", is_fg, fg_title
        logger.warning("Window mss returned black frame (%s), trying WGC", win.title)

    return _capture_via_wgc()


def _resolve_roi_in_frame(
    img_shape: tuple[int, ...],
    frame_origin: tuple[int, int],
    client_origin: tuple[int, int],
    client_size: tuple[int, int],
    roi: dict[str, Any] | None,
) -> tuple[int, int, int, int, tuple[int, int]]:
    """Convert normalized/pixel client ROI into clamped frame slice + screen origin."""
    fh, fw = img_shape[:2]
    cw, ch = client_size
    offset_x = client_origin[0] - frame_origin[0]
    offset_y = client_origin[1] - frame_origin[1]

    if not roi:
        x1 = max(0, min(fw - 1, offset_x))
        y1 = max(0, min(fh - 1, offset_y))
        x2 = max(x1 + 1, min(fw, offset_x + cw))
        y2 = max(y1 + 1, min(fh, offset_y + ch))
        return x1, y1, x2, y2, (frame_origin[0] + x1, frame_origin[1] + y1)

    if "rx" in roi and "ry" in roi:
        cx = int(round(float(roi["rx"]) * cw))
        cy = int(round(float(roi["ry"]) * ch))
        rw = int(round(float(roi.get("rw", 0.1)) * cw))
        rh = int(round(float(roi.get("rh", 0.05)) * ch))
    elif "x" in roi and "y" in roi:
        cx = int(round(float(roi["x"])))
        cy = int(round(float(roi["y"])))
        rw = int(round(float(roi.get("w", 120))))
        rh = int(round(float(roi.get("h", 40))))
    else:
        raise ValueError("roi 必须包含归一化键 (rx, ry, rw, rh) 或像素键 (x, y, w, h)")

    x1 = max(0, min(fw - 1, offset_x + cx))
    y1 = max(0, min(fh - 1, offset_y + cy))
    x2 = max(x1 + 1, min(fw, offset_x + cx + max(4, rw)))
    y2 = max(y1 + 1, min(fh, offset_y + cy + max(4, rh)))
    return x1, y1, x2, y2, (frame_origin[0] + x1, frame_origin[1] + y1)


def _compute_dhash(bgr: np.ndarray, hash_size: int = 8) -> int:
    """Compute a 64-bit difference hash for fast stability comparison."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    bits = 0
    for val in diff.flatten():
        bits = (bits << 1) | int(bool(val))
    return bits


def _hamming_distance(h1: int, h2: int) -> int:
    return (h1 ^ h2).bit_count()


def _save_bgr_png(bgr: np.ndarray, out_path: Path) -> str:
    """Save BGR image safely even when path contains non-ASCII Windows usernames."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(out_path, format="PNG")
    return str(out_path)


def _run_ocr_on_crop(
    crop_bgr: np.ndarray,
    crop_screen_origin: tuple[int, int],
    client_origin: tuple[int, int],
    client_size: tuple[int, int],
    scale: float = 2.0,
    invert: bool = False,
) -> list[dict[str, Any]]:
    """Run RapidOCR on a BGR crop and map boxes back to physical & normalized coordinates."""
    proc = crop_bgr
    if invert:
        proc = cv2.bitwise_not(proc)
    scale = max(1.0, min(4.0, float(scale)))
    if scale > 1.01:
        proc = cv2.resize(proc, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    ocr = _get_ocr()
    raw_items, _ = ocr(proc)
    if not raw_items:
        return []

    cw, ch = client_size
    results: list[dict[str, Any]] = []
    for box, text, score in raw_items:
        pts = np.asarray(box, dtype=np.float32) / scale
        cx_local = float(np.mean(pts[:, 0]))
        cy_local = float(np.mean(pts[:, 1]))
        sx = int(round(crop_screen_origin[0] + cx_local))
        sy = int(round(crop_screen_origin[1] + cy_local))
        results.append(
            {
                "text": str(text),
                "score": round(float(score), 4),
                "screen_x": sx,
                "screen_y": sy,
                "rx": round((sx - client_origin[0]) / max(1, cw), 5),
                "ry": round((sy - client_origin[1]) / max(1, ch), 5),
            }
        )
    return results


def _extract_numbers(text: str) -> list[float]:
    cleaned = text.replace(",", "").replace("，", "")
    # Separate ASCII single-digit gauge tags glued directly onto decimal readings (e.g. "F4103.86", "F21.00").
    cleaned = re.sub(r"([FTPVL]\d)(\d+\.\d+)", r"\1 \2", cleaned)
    # Normalize RapidOCR split decimals such as "49. .6 %", "12. .0 %", or "49 .1 %".
    cleaned = re.sub(r"(\d+)\.\s+\.(\d+)", r"\1.\2", cleaned)
    cleaned = re.sub(r"(\d+)\s+\.(\d+)", r"\1.\2", cleaned)
    # Skip digits embedded inside DCS instrument tags such as TI101 or PI202.
    return [float(m) for m in re.findall(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:\.\d+)?", cleaned)]


def _extract_mmss_minutes(text: str) -> float | None:
    """Parse HH:MM:SS(.cc) or MM:SS(.cc) clock strings into total minutes."""
    normalized = text.replace("：", ":").strip()
    m_hms = re.search(r"(\d+)\s*:\s*(\d{1,2})\s*:\s*(\d{1,2}(?:\.\d+)?)", normalized)
    if m_hms:
        hours = int(m_hms.group(1))
        mins = int(m_hms.group(2))
        secs = float(m_hms.group(3))
        return round(hours * 60.0 + mins + secs / 60.0, 4)
    m_ms = re.search(r"(\d+)\s*:\s*(\d{1,2}(?:\.\d+)?)", normalized)
    if m_ms:
        mins = int(m_ms.group(1))
        secs = float(m_ms.group(2))
        return round(mins + secs / 60.0, 4)
    return None


def _extract_primary_number(
    items: list[dict[str, Any]],
    crop_origin: tuple[int, int],
    crop_size: tuple[int, int],
) -> float | None:
    """Pick the most reliable numeric reading in an ROI crop, filtering edge noise."""
    if not items:
        return None
    crop_h = max(1, int(crop_size[1]))
    center_y = crop_origin[1] + crop_h / 2.0
    row_tol = max(8.0, crop_h * 0.22)

    sorted_items = sorted(items, key=lambda it: (float(it.get("screen_y", center_y)), float(it.get("screen_x", 0))))
    rows: list[list[dict[str, Any]]] = []
    for it in sorted_items:
        sy = float(it.get("screen_y", center_y))
        if not rows:
            rows.append([it])
            continue
        row_y = sum(float(x.get("screen_y", center_y)) for x in rows[-1]) / len(rows[-1])
        if abs(sy - row_y) <= row_tol:
            rows[-1].append(it)
        else:
            rows.append([it])

    candidates: list[tuple[float, float, float]] = []  # (val, score, sy)
    for row in rows:
        row_sorted = sorted(row, key=lambda x: float(x.get("screen_x", 0)))
        row_text = " ".join(str(x.get("text", "")) for x in row_sorted)
        row_nums = _extract_numbers(row_text)
        if not row_nums:
            continue
        row_sy = sum(float(x.get("screen_y", center_y)) for x in row_sorted) / len(row_sorted)
        num_scores = [
            float(x.get("score", 0.0))
            for x in row_sorted
            if re.search(r"\d", str(x.get("text", "")))
        ]
        row_score = min(num_scores) if num_scores else max(float(x.get("score", 0.0)) for x in row_sorted)
        candidates.append((row_nums[0], row_score, row_sy))

    if candidates:
        has_high_conf = any(sc >= 0.85 for _, sc, _ in candidates)
        filtered = [c for c in candidates if not (has_high_conf and c[1] < 0.75)] or candidates
        best = max(
            filtered,
            key=lambda c: (
                c[1] >= 0.85,
                -(abs(c[2] - center_y) / max(1.0, crop_h / 2.0)),
                c[1],
            ),
        )
        return best[0]

    joined = " ".join(str(it.get("text", "")) for it in items)
    nums = _extract_numbers(joined)
    return nums[0] if nums else None


def _prune_captures(keep_latest: int, captures_dir: Path = CAPTURES_DIR) -> dict[str, Any]:
    """Delete older capture_*.png files beyond keep_latest while preserving newest ones."""
    keep = max(0, int(keep_latest))
    if not captures_dir.exists():
        return {"deleted_count": 0, "kept_count": 0, "freed_bytes": 0}
    files = sorted(
        captures_dir.glob("capture_*.png"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    to_keep = files[:keep] if keep > 0 else []
    to_delete = files[keep:]
    freed = 0
    deleted = 0
    for f in to_delete:
        try:
            sz = f.stat().st_size
            f.unlink()
            deleted += 1
            freed += sz
        except OSError:
            pass
    return {"deleted_count": deleted, "kept_count": len(to_keep), "freed_bytes": freed}


def _preset_path(preset_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", preset_id.strip())
    if not safe:
        raise ValueError("preset_id 不能为空")
    return PRESETS_DIR / f"{safe}.json"


mcp = FastMCP(
    "ober-ocr-mcp",
    instructions=(
        "Standalone local WGC/MSS + RapidOCR + 7-segment LED + closed-loop valve/color watcher for OBER chemical simulation experiments. "
        "Always call probe_desktop_environment first to verify window visibility and DPI geometry."
    ),
)


@mcp.tool()
def probe_desktop_environment(filter_text: str = "", include_minimized: bool = False) -> dict[str, Any]:
    """Inspect active desktop name, monitor geometry, and visible/minimized windows seen by this MCP process."""
    _enable_dpi_awareness()
    with mss.mss() as sct:
        monitors = sct.monitors
    windows = _list_visible_windows(include_minimized=include_minimized)
    if filter_text.strip():
        ft = filter_text.strip().lower()
        windows = [w for w in windows if ft in w.title.lower() or ft in w.process_name.lower()]
    return {
        "desktop_name": _get_current_desktop_name(),
        "monitors": monitors,
        "visible_window_count": len(windows),
        "windows": [w.to_dict() for w in windows[:40]],
    }


def _clamp_timeout(timeout_sec: float) -> tuple[float, bool]:
    """Clamp wait timeout below the MCP 3-minute hard deadline."""
    raw = max(0.5, float(timeout_sec))
    clamped = min(raw, MAX_WATCH_TIMEOUT_SEC)
    return clamped, raw > MAX_WATCH_TIMEOUT_SEC


def _foreground_warning(win: WindowInfo | None, backend_used: str, is_fg: bool) -> dict[str, Any]:
    """Attach foreground metadata and an occlusion warning if MSS captured a background window."""
    if win is None:
        return {}
    meta: dict[str, Any] = {"is_foreground": is_fg}
    if backend_used == "mss" and not is_fg:
        meta["warning"] = "目标窗口当前不在前台，MSS 截图可能读到遮挡窗口，请先前置窗口或传 ensure_foreground=True"
    return meta


def _capture_crop(
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> tuple[
    np.ndarray,
    tuple[int, int],
    tuple[int, int],
    tuple[int, int],
    tuple[int, int],
    WindowInfo | None,
    str,
    bool,
    str,
]:
    """Capture a window or screen and slice an optional normalized/pixel ROI."""
    img, frame_origin, client_origin, win, used, is_fg, fg_title = _capture_target(
        window_query, backend, ensure_foreground=ensure_foreground
    )
    client_size = win.client_size if win else (int(img.shape[1]), int(img.shape[0]))
    x1, y1, x2, y2, crop_origin = _resolve_roi_in_frame(img.shape, frame_origin, client_origin, client_size, roi)
    crop = img[y1:y2, x1:x2]
    crop_size = (x2 - x1, y2 - y1)
    return crop, crop_origin, crop_size, client_origin, client_size, win, used, is_fg, fg_title


@mcp.tool()
def capture_window_wgc(
    window_query: str = "",
    save_path: str = "",
    backend: str = "auto",
    ensure_foreground: bool = False,
    auto_prune_keep: int = 80,
) -> dict[str, Any]:
    """Capture a window (or primary screen if window_query is empty) via WGC/MSS and save a PNG."""
    img, frame_origin, client_origin, win, used, is_fg, fg_title = _capture_target(
        window_query, backend, ensure_foreground=ensure_foreground
    )
    out = Path(save_path) if save_path.strip() else CAPTURES_DIR / f"capture_{int(time.time() * 1000)}.png"
    saved = _save_bgr_png(img, out)
    if not save_path.strip() and auto_prune_keep > 0:
        _prune_captures(auto_prune_keep, CAPTURES_DIR)
    result = {
        "backend_used": used,
        "saved_path": saved,
        "frame_size": [int(img.shape[1]), int(img.shape[0])],
        "frame_origin": list(frame_origin),
        "client_origin": list(client_origin),
        "dhash_hex": hex(_compute_dhash(img)),
        "window": win.to_dict() if win else None,
        "foreground_title": fg_title,
    }
    result.update(_foreground_warning(win, used, is_fg))
    return result


@mcp.tool()
def cleanup_captures(keep_latest: int = 30) -> dict[str, Any]:
    """Delete older PNG files in captures/ while keeping the newest keep_latest screenshots."""
    stats = _prune_captures(keep_latest, CAPTURES_DIR)
    return {"captures_dir": str(CAPTURES_DIR), "keep_latest": max(0, int(keep_latest)), **stats}


@mcp.tool()
def ocr_window_or_roi(
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    scale: float = 2.0,
    invert: bool = False,
    numeric_only: bool = False,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Run RapidOCR on a full window or normalized/pixel ROI and return text plus clickable coordinates."""
    crop, crop_origin, crop_size, client_origin, client_size, win, used, is_fg, fg_title = _capture_crop(
        window_query=window_query, roi=roi, backend=backend, ensure_foreground=ensure_foreground
    )
    items = _run_ocr_on_crop(crop, crop_origin, client_origin, client_size, scale=scale, invert=invert)
    joined = " ".join(item["text"] for item in items)
    numbers = _extract_numbers(joined) if numeric_only or items else []
    primary = _extract_primary_number(items, crop_origin, crop_size) if items else None
    result = {
        "backend_used": used,
        "window": win.to_dict() if win else None,
        "foreground_title": fg_title,
        "crop_rect_screen": [crop_origin[0], crop_origin[1], crop_size[0], crop_size[1]],
        "joined_text": joined,
        "primary_number": primary,
        "numbers": numbers,
        "items": items,
    }
    result.update(_foreground_warning(win, used, is_fg))
    return result


_SEVEN_SEG_TABLE: dict[tuple[int, ...], str] = {
    (1, 1, 1, 1, 1, 1, 0): "0",
    (0, 1, 1, 0, 0, 0, 0): "1",
    (0, 0, 0, 0, 1, 1, 0): "1",
    (1, 1, 0, 1, 1, 0, 1): "2",
    (1, 1, 1, 1, 0, 0, 1): "3",
    (0, 1, 1, 0, 0, 1, 1): "4",
    (1, 0, 1, 1, 0, 1, 1): "5",
    (1, 0, 1, 1, 1, 1, 1): "6",
    (0, 0, 1, 1, 1, 1, 1): "6",
    (1, 1, 1, 0, 0, 0, 0): "7",
    (1, 1, 1, 0, 0, 1, 0): "7",
    (1, 1, 1, 1, 1, 1, 1): "8",
    (1, 1, 1, 1, 0, 1, 1): "9",
    (1, 1, 1, 0, 0, 1, 1): "9",
}


def _extract_led_mask(bgr: np.ndarray, color: str = "red") -> tuple[np.ndarray, dict[str, float]]:
    """Isolate brightly lit 7-segment LED elements while suppressing dim unlit ghost segments."""
    if bgr.size == 0:
        return np.zeros((0, 0), dtype=np.uint8), {"s_hi": 0.0, "c_hi": 0.0}
    arr = bgr.astype(np.float32)
    b, g, r = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    ckey = color.lower().strip() or "red"
    if ckey == "red":
        score = r - np.maximum(g, b)
        chan = r
        score_floor, chan_floor, score_frac, chan_frac = 85.0, 140.0, 0.84, 0.85
    elif ckey == "green":
        score = g - np.maximum(r, b)
        chan = g
        score_floor, chan_floor, score_frac, chan_frac = 75.0, 135.0, 0.84, 0.85
    elif ckey == "amber":
        score = 0.5 * (r + g) - b
        chan = np.maximum(r, g)
        score_floor, chan_floor, score_frac, chan_frac = 75.0, 145.0, 0.84, 0.85
    elif ckey == "white":
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        score = gray
        chan = gray
        score_floor, chan_floor, score_frac, chan_frac = 180.0, 180.0, 0.85, 0.85
    else:
        raise ValueError(f"不支持的 LED 颜色: '{color}'，请使用 red / green / amber / white")

    s_hi = float(np.percentile(score, 99.5))
    c_hi = float(np.percentile(chan, 99.5))
    stats = {"s_hi": round(s_hi, 2), "c_hi": round(c_hi, 2)}
    if s_hi < score_floor or c_hi < chan_floor:
        return np.zeros(bgr.shape[:2], dtype=np.uint8), stats

    raw_mask = (
        (score >= max(score_floor, s_hi * score_frac)) & (chan >= max(chan_floor, c_hi * chan_frac))
    ).astype(np.uint8) * 255
    num_labels, labels, cc_stats, _ = cv2.connectedComponentsWithStats(raw_mask, connectivity=8)
    clean = np.zeros_like(raw_mask)
    for lbl in range(1, num_labels):
        if int(cc_stats[lbl, cv2.CC_STAT_AREA]) >= 4:
            clean[labels == lbl] = 255
    return clean, stats


def _decode_seven_segment(bgr: np.ndarray, color: str = "red") -> dict[str, Any]:
    """Decode lit 7-segment LED digits (SWP-C40/D40 style) deterministically without RapidOCR."""
    mask, stats = _extract_led_mask(bgr, color=color)
    if int((mask > 0).sum()) < 12:
        return {"value": None, "raw_text": "", "digits": [], "confidence": 0.0, "mask": mask, "stats": stats}

    num_labels, labels, cc_stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_comp_area = 8 if any(int(cc_stats[l, cv2.CC_STAT_AREA]) >= 8 for l in range(1, num_labels)) else 4
    ys_top: list[int] = []
    ys_bot: list[int] = []
    for lbl in range(1, num_labels):
        if int(cc_stats[lbl, cv2.CC_STAT_AREA]) >= min_comp_area:
            top = int(cc_stats[lbl, cv2.CC_STAT_TOP])
            height = int(cc_stats[lbl, cv2.CC_STAT_HEIGHT])
            ys_top.append(top)
            ys_bot.append(top + height - 1)
    if not ys_top:
        return {"value": None, "raw_text": "", "digits": [], "confidence": 0.0, "mask": mask, "stats": stats}

    y_top = min(ys_top)
    y_bot = max(ys_bot)
    band_h = max(1, y_bot - y_top + 1)
    band = mask[y_top : y_bot + 1, :] > 0
    col_active = band.any(axis=0)

    spans: list[tuple[int, int]] = []
    in_span = False
    span_start = 0
    for x, active in enumerate(col_active):
        if active and not in_span:
            span_start = x
            in_span = True
        elif not active and in_span:
            spans.append((span_start, x - 1))
            in_span = False
    if in_span:
        spans.append((span_start, len(col_active) - 1))

    chars: list[str] = []
    digit_details: list[dict[str, Any]] = []
    confs: list[float] = []

    def _zone_frac(sub: np.ndarray, sw: int, y0f: float, y1f: float, x0f: float, x1f: float) -> float:
        ya = max(0, min(band_h - 1, int(y0f * band_h)))
        yb = max(ya + 1, min(band_h, int(y1f * band_h)))
        xa = max(0, min(sw - 1, int(x0f * sw)))
        xb = max(xa + 1, min(sw, int(x1f * sw)))
        return float(sub[ya:yb, xa:xb].mean())

    for x1, x2 in spans:
        sub = band[:, x1 : x2 + 1]
        active_ys = np.where(sub.any(axis=1))[0]
        if active_ys.size == 0:
            continue
        sy1, sy2 = int(active_ys[0]), int(active_ys[-1])
        sh = sy2 - sy1 + 1
        sw = x2 - x1 + 1
        yc_rel = ((sy1 + sy2) / 2.0) / band_h

        if sh < 0.35 * band_h and sw < 0.38 * band_h and yc_rel >= 0.62:
            chars.append(".")
            confs.append(0.99)
            digit_details.append({"char": ".", "x_span": [x1, x2], "confidence": 0.99})
            continue
        if sh < 0.28 * band_h and 0.32 <= yc_rel <= 0.68:
            chars.append("-")
            confs.append(0.98)
            digit_details.append({"char": "-", "x_span": [x1, x2], "confidence": 0.98})
            continue
        if sh < 0.50 * band_h:
            continue

        if sw < 0.32 * band_h:
            chars.append("1")
            confs.append(0.99)
            digit_details.append({"char": "1", "x_span": [x1, x2], "narrow_one": True, "confidence": 0.99})
            continue

        zones = (
            _zone_frac(sub, sw, 0.00, 0.18, 0.25, 0.75),  # a
            _zone_frac(sub, sw, 0.12, 0.48, 0.72, 1.00),  # b
            _zone_frac(sub, sw, 0.52, 0.88, 0.72, 1.00),  # c
            _zone_frac(sub, sw, 0.82, 1.00, 0.25, 0.75),  # d
            _zone_frac(sub, sw, 0.52, 0.88, 0.00, 0.28),  # e
            _zone_frac(sub, sw, 0.12, 0.48, 0.00, 0.28),  # f
            _zone_frac(sub, sw, 0.42, 0.58, 0.25, 0.75),  # g
        )
        bits = tuple(1 if v >= 0.22 else 0 for v in zones)
        ch = _SEVEN_SEG_TABLE.get(bits, "?")
        c_val = 0.98 if ch != "?" else 0.0
        chars.append(ch)
        confs.append(c_val)
        digit_details.append(
            {
                "char": ch,
                "x_span": [x1, x2],
                "bits": list(bits),
                "zones": [round(v, 3) for v in zones],
                "confidence": c_val,
            }
        )

    raw_text = "".join(chars)
    parsed: float | None = None
    if raw_text and re.fullmatch(r"-?\d+(?:\.\d+)?", raw_text) and "?" not in raw_text:
        parsed = float(raw_text)
    avg_conf = round(sum(confs) / len(confs), 4) if confs else 0.0
    if parsed is None:
        avg_conf = 0.0
    return {
        "value": parsed,
        "raw_text": raw_text,
        "digits": digit_details,
        "confidence": avg_conf,
        "mask": mask,
        "stats": stats,
    }


@mcp.tool()
def read_seven_segment_roi(
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    color: str = "red",
    save_mask_crop: bool = False,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Read a glowing 7-segment LED gauge (e.g. SWP-C40/SWP-D40 red displays) with luminance thresholding."""
    crop, crop_origin, crop_size, _client_origin, _client_size, win, used, is_fg, fg_title = _capture_crop(
        window_query=window_query, roi=roi, backend=backend, ensure_foreground=ensure_foreground
    )
    decoded = _decode_seven_segment(crop, color=color)
    mask_path: str | None = None
    if save_mask_crop:
        out_path = CAPTURES_DIR / f"led_mask_{int(time.time() * 1000)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(decoded["mask"]).save(out_path, format="PNG")
        mask_path = str(out_path)

    result: dict[str, Any] = {
        "backend_used": used,
        "window": win.to_dict() if win else None,
        "foreground_title": fg_title,
        "crop_rect_screen": [crop_origin[0], crop_origin[1], crop_size[0], crop_size[1]],
        "color": color,
        "value": decoded["value"],
        "raw_text": decoded["raw_text"],
        "confidence": decoded["confidence"],
        "digits": decoded["digits"],
        "stats": decoded["stats"],
    }
    if mask_path is not None:
        result["mask_saved_path"] = mask_path
    result.update(_foreground_warning(win, used, is_fg))
    return result


def _detect_ui_boxes_on_bgr(
    bgr: np.ndarray,
    crop_origin: tuple[int, int],
    client_origin: tuple[int, int],
    client_size: tuple[int, int],
    kind: str = "input_boxes",
    min_brightness: int = 228,
) -> dict[str, Any]:
    """Detect Unity input cells, radio circles, red close X buttons, or scrollbar thumbs and return screen coordinates."""
    kind_aliases = {
        "input_boxes": "input_boxes",
        "inputs": "input_boxes",
        "cells": "input_boxes",
        "input": "input_boxes",
        "radio_circles": "radio_circles",
        "radio_boxes": "radio_circles",
        "radios": "radio_circles",
        "radio": "radio_circles",
        "checkboxes": "radio_circles",
        "red_close": "red_close",
        "close": "red_close",
        "close_x": "red_close",
        "red_x": "red_close",
        "scrollbar_thumb": "scrollbar_thumb",
        "scrollbar": "scrollbar_thumb",
        "scrollbars": "scrollbar_thumb",
        "thumb": "scrollbar_thumb",
    }
    raw_k = kind.lower().strip() or "input_boxes"
    k = kind_aliases.get(raw_k)
    if k is None:
        raise ValueError("kind 必须是 'input_boxes'、'radio_circles'、'red_close' 或 'scrollbar_thumb'")

    cw_client, ch_client = client_size
    crop_h, crop_w = bgr.shape[:2]
    b, g, r = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    if k == "input_boxes":
        thr = max(0, min(255, int(min_brightness)))
        mask = ((b >= thr) & (g >= thr) & (r >= thr)).astype(np.uint8) * 255
    elif k == "radio_circles":
        thr = max(238, min(255, int(min_brightness)))
        mask = ((b >= thr) & (g >= thr) & (r >= thr)).astype(np.uint8) * 255
    elif k == "red_close":
        raw_red = ((r >= 170) & (g <= 70) & (b <= 70)).astype(np.uint8) * 255
        mask = cv2.dilate(raw_red, np.ones((3, 3), dtype=np.uint8), iterations=1)
    else:
        k3 = np.ones((3, 3), dtype=np.uint8)
        grad = np.maximum.reduce([cv2.morphologyEx(ch, cv2.MORPH_GRADIENT, k3) for ch in (b, g, r)])
        mask = ((grad <= 6) & (gray >= 50) & (gray <= 228)).astype(np.uint8) * 255

    num_labels, _labels, cc_stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates: list[dict[str, Any]] = []
    for lbl in range(1, num_labels):
        x = int(cc_stats[lbl, cv2.CC_STAT_LEFT])
        y = int(cc_stats[lbl, cv2.CC_STAT_TOP])
        w = int(cc_stats[lbl, cv2.CC_STAT_WIDTH])
        h = int(cc_stats[lbl, cv2.CC_STAT_HEIGHT])
        area = int(cc_stats[lbl, cv2.CC_STAT_AREA])
        fill_ratio = area / max(1, w * h)

        extra: dict[str, Any] = {}
        if k == "input_boxes":
            if w < 25 or h < 12 or w > max(260, int(crop_w * 0.85)) or h > max(60, int(crop_h * 0.40)):
                continue
            if fill_ratio < 0.45:
                continue
        elif k == "radio_circles":
            if not (12 <= w <= 36 and 12 <= h <= 36):
                continue
            aspect = w / max(1, h)
            if not (0.72 <= aspect <= 1.38 and 0.40 <= fill_ratio <= 0.95):
                continue
            cy0 = y + int(h * 0.30)
            cy1 = max(cy0 + 1, y + int(h * 0.70))
            cx0 = x + int(w * 0.30)
            cx1 = max(cx0 + 1, x + int(w * 0.70))
            patch = gray[cy0:cy1, cx0:cx1]
            center_mean = float(patch.mean())
            dark_ratio = float((patch < 150).mean())
            extra = {
                "checked": bool(dark_ratio >= 0.12 or center_mean < 205.0),
                "center_brightness": round(center_mean, 1),
            }
        elif k == "red_close":
            if not (10 <= w <= 55 and 10 <= h <= 55):
                continue
            aspect = w / max(1, h)
            if not (0.65 <= aspect <= 1.55):
                continue
        else:
            if fill_ratio < 0.70:
                continue
            aspect = w / max(1, h)
            is_horiz = w >= 30 and 8 <= h <= 30 and aspect >= 2.2
            is_vert = h >= 30 and 8 <= w <= 30 and aspect <= 0.45
            if not (is_horiz or is_vert):
                continue
            if float(gray[y : y + h, x : x + w].std()) >= 25.0:
                continue
            extra = {"orientation": "horizontal" if is_horiz else "vertical"}

        sx = int(round(crop_origin[0] + x + (w - 1) / 2.0))
        sy = int(round(crop_origin[1] + y + (h - 1) / 2.0))
        candidates.append(
            {
                "screen_x": sx,
                "screen_y": sy,
                "w": w,
                "h": h,
                "rx": round((sx - client_origin[0]) / max(1, cw_client), 5),
                "ry": round((sy - client_origin[1]) / max(1, ch_client), 5),
                **extra,
            }
        )

    candidates.sort(key=lambda it: (it["screen_y"], it["screen_x"]))
    out: dict[str, Any] = {"kind": k, "count": len(candidates), "items": candidates}
    if k == "input_boxes":
        rows: list[list[dict[str, Any]]] = []
        for item in candidates:
            if not rows:
                rows.append([item])
                continue
            row_cy = sum(c["screen_y"] for c in rows[-1]) / len(rows[-1])
            if abs(item["screen_y"] - row_cy) <= 10.0:
                rows[-1].append(item)
            else:
                rows.append([item])
        rows_out = []
        for r_idx, row in enumerate(rows):
            row.sort(key=lambda c: c["screen_x"])
            rows_out.append(
                {
                    "row_index": r_idx,
                    "screen_y": int(round(sum(c["screen_y"] for c in row) / len(row))),
                    "cells": row,
                }
            )
        out["row_count"] = len(rows_out)
        out["rows"] = rows_out
    return out


@mcp.tool()
def detect_ui_boxes(
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    kind: str = "input_boxes",
    min_brightness: int = 228,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Detect bright input cells, quiz radio circles (with checked state), red close buttons, or scrollbar thumbs."""
    crop, crop_origin, crop_size, client_origin, client_size, win, used, is_fg, fg_title = _capture_crop(
        window_query=window_query, roi=roi, backend=backend, ensure_foreground=ensure_foreground
    )
    detected = _detect_ui_boxes_on_bgr(
        crop,
        crop_origin=crop_origin,
        client_origin=client_origin,
        client_size=client_size,
        kind=kind,
        min_brightness=min_brightness,
    )
    result: dict[str, Any] = {
        "backend_used": used,
        "window": win.to_dict() if win else None,
        "foreground_title": fg_title,
        "crop_rect_screen": [crop_origin[0], crop_origin[1], crop_size[0], crop_size[1]],
        **detected,
    }
    result.update(_foreground_warning(win, used, is_fg))
    return result


@mcp.tool()
def find_text_targets(
    query: str,
    window_query: str = "",
    backend: str = "auto",
    min_score: float = 0.45,
    scale: float = 1.5,
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Locate UI labels/buttons inside a Unity or DCS window by OCR text match."""
    res = ocr_window_or_roi(
        window_query=window_query, roi=None, scale=scale, backend=backend, ensure_foreground=ensure_foreground
    )
    q = query.strip().lower()
    matches = [
        item
        for item in res["items"]
        if item["score"] >= min_score and (q in item["text"].lower() or item["text"].lower() in q)
    ]
    out = {
        "query": query,
        "backend_used": res["backend_used"],
        "window": res["window"],
        "match_count": len(matches),
        "matches": matches,
    }
    if "is_foreground" in res:
        out["is_foreground"] = res["is_foreground"]
    if "warning" in res:
        out["warning"] = res["warning"]
    return out


def _read_companion_rois(
    window_query: str,
    companion_rois: dict[str, dict[str, Any]] | None,
    ensure_foreground: bool,
    backend: str,
) -> dict[str, Any]:
    """Sample secondary ROIs once at exit so callers see column/upstream propagation."""
    if not companion_rois:
        return {}
    readings: dict[str, Any] = {}
    for name, comp_roi in companion_rois.items():
        try:
            cres = ocr_window_or_roi(
                window_query=window_query,
                roi=comp_roi,
                numeric_only=True,
                backend=backend,
                ensure_foreground=ensure_foreground,
            )
            readings[name] = {"val": cres.get("primary_number"), "text": cres.get("joined_text", "")}
        except Exception as exc:
            readings[name] = {"val": None, "error": str(exc)}
    return readings


@mcp.tool()
def watch_value_threshold(
    window_query: str,
    roi: dict[str, Any],
    target_op: str = "ge",
    target_value: float = 0.0,
    target_value_max: float = 0.0,
    stable_samples: int = 3,
    poll_interval_sec: float = 0.5,
    timeout_sec: float = 60.0,
    scale: float = 2.5,
    invert: bool = False,
    backend: str = "auto",
    ensure_foreground: bool = False,
    companion_rois: dict[str, dict[str, Any]] | None = None,
    led_color: str = "",
    parse_mode: str = "number",
    valid_min: float | None = None,
    valid_max: float | None = None,
) -> dict[str, Any]:
    """Locally poll a numeric DCS gauge, 7-segment LED, or MM:SS timer ROI until a condition holds."""
    op = target_op.lower().strip()
    valid_ops = {"ge", "le", "gt", "lt", "in_range", "stable"}
    if op not in valid_ops:
        raise ValueError(f"target_op 必须是 {sorted(valid_ops)} 之一")
    mode = parse_mode.lower().strip() or "number"
    if mode not in {"number", "mmss_minutes"}:
        raise ValueError("parse_mode 必须是 'number' 或 'mmss_minutes'")
    led = led_color.strip()

    eff_timeout, timeout_clamped = _clamp_timeout(timeout_sec)
    start = time.monotonic()
    consecutive = 0
    history: list[dict[str, Any]] = []
    first_sample: tuple[float, float] | None = None
    last_is_fg = True
    tolerance = abs(target_value) if op == "stable" else 0.0

    def _compute_trend() -> float | None:
        if first_sample is None:
            return None
        valid_hist = [(h["t"], h["val"]) for h in history if h["val"] is not None and not h.get("ignored_glitch")]
        if not valid_hist:
            return None
        t_end, v_end = valid_hist[-1]
        dt = float(t_end) - float(first_sample[0])
        if dt < 1.0:
            return None
        return round((float(v_end) - float(first_sample[1])) / dt * 60.0, 2)

    while time.monotonic() - start <= eff_timeout:
        if led:
            res = read_seven_segment_roi(
                window_query=window_query,
                roi=roi,
                color=led,
                save_mask_crop=False,
                backend=backend,
                ensure_foreground=ensure_foreground,
            )
            val = res.get("value")
            txt = res.get("raw_text", "")
        else:
            res = ocr_window_or_roi(
                window_query=window_query,
                roi=roi,
                scale=scale,
                invert=invert,
                numeric_only=(mode == "number"),
                backend=backend,
                ensure_foreground=ensure_foreground,
            )
            txt = res.get("joined_text", "")
            if mode == "mmss_minutes":
                val = _extract_mmss_minutes(txt)
            else:
                val = res.get("primary_number")
                if val is None and res.get("numbers"):
                    val = res["numbers"][0]

        last_is_fg = bool(res.get("is_foreground", True))
        elapsed = round(time.monotonic() - start, 2)
        glitch_ignored = False
        if val is not None and (
            (valid_min is not None and float(val) < float(valid_min))
            or (valid_max is not None and float(val) > float(valid_max))
        ):
            glitch_ignored = True

        hist_entry: dict[str, Any] = {"t": elapsed, "val": val, "text": txt}
        if glitch_ignored:
            hist_entry["ignored_glitch"] = True
        else:
            if val is not None and first_sample is None:
                first_sample = (elapsed, float(val))
        history.append(hist_entry)
        history = history[-20:]

        if glitch_ignored:
            time.sleep(max(0.15, poll_interval_sec))
            continue

        matched = False
        if val is not None:
            if op == "ge":
                matched = val >= target_value
            elif op == "le":
                matched = val <= target_value
            elif op == "gt":
                matched = val > target_value
            elif op == "lt":
                matched = val < target_value
            elif op == "in_range":
                lo, hi = min(target_value, target_value_max), max(target_value, target_value_max)
                matched = lo <= val <= hi
            elif op == "stable":
                prev_vals = [
                    h["val"]
                    for h in history[:-1]
                    if h["val"] is not None and not h.get("ignored_glitch")
                ]
                matched = bool(prev_vals) and abs(val - prev_vals[-1]) <= max(1e-6, tolerance)

        consecutive = consecutive + 1 if matched else 0
        if consecutive >= max(1, stable_samples):
            return {
                "satisfied": True,
                "elapsed_sec": round(time.monotonic() - start, 2),
                "effective_timeout_sec": round(eff_timeout, 2),
                "timeout_clamped": timeout_clamped,
                "is_foreground": last_is_fg,
                "parse_mode": mode,
                "led_color": led or None,
                "final_value": val,
                "trend_per_min": _compute_trend(),
                "consecutive_samples": consecutive,
                "companion_readings": _read_companion_rois(window_query, companion_rois, ensure_foreground, backend),
                "recent_history": history,
            }
        time.sleep(max(0.15, poll_interval_sec))

    valid_vals = [h["val"] for h in history if h["val"] is not None and not h.get("ignored_glitch")]
    fallback_val = valid_vals[-1] if valid_vals else (history[-1]["val"] if history else None)
    return {
        "satisfied": False,
        "elapsed_sec": round(time.monotonic() - start, 2),
        "effective_timeout_sec": round(eff_timeout, 2),
        "timeout_clamped": timeout_clamped,
        "is_foreground": last_is_fg,
        "parse_mode": mode,
        "led_color": led or None,
        "final_value": fallback_val,
        "trend_per_min": _compute_trend(),
        "consecutive_samples": consecutive,
        "companion_readings": _read_companion_rois(window_query, companion_rois, ensure_foreground, backend),
        "recent_history": history,
    }


def _compute_color_ratio(bgr: np.ndarray, target_color: str = "yellow") -> float:
    """Compute fraction of pixels in a BGR crop matching a target chemical indicator hue."""
    ckey = target_color.lower().strip() or "yellow"
    if ckey not in {"yellow", "red", "pink", "blue", "green"}:
        raise ValueError("target_color 必须是 'yellow'、'red'、'pink'、'blue' 或 'green'")
    if bgr.size == 0:
        return 0.0
    arr = bgr.astype(np.int16)
    b, g, r = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    if ckey == "yellow":
        mask = (r >= 190) & (g >= 190) & (b <= 110) & (np.minimum(r, g) - b >= 90)
    elif ckey in {"red", "pink"}:
        mask = (r >= 170) & ((r - g) >= 55) & ((r - b) >= 40)
    elif ckey == "blue":
        mask = (b >= 160) & ((b - r) >= 55)
    else:
        mask = (g >= 160) & ((g - r) >= 45) & ((g - b) >= 45)
    return round(float(mask.mean()), 5)


@mcp.tool()
def watch_color_endpoint(
    window_query: str,
    roi: dict[str, Any],
    target_color: str = "yellow",
    min_pixel_ratio: float = 0.04,
    stable_samples: int = 2,
    poll_interval_sec: float = 1.0,
    timeout_sec: float = 120.0,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Monitor a glassware/flask ROI until a target indicator color ratio holds for stable_samples consecutive polls."""
    _compute_color_ratio(np.zeros((1, 1, 3), dtype=np.uint8), target_color=target_color)
    eff_timeout, timeout_clamped = _clamp_timeout(timeout_sec)
    start = time.monotonic()
    consecutive = 0
    history: list[dict[str, Any]] = []
    last_is_fg = True

    while time.monotonic() - start <= eff_timeout:
        crop, _origin, _size, _co, _cs, _win, _used, is_fg, _fg = _capture_crop(
            window_query=window_query,
            roi=roi,
            backend=backend,
            ensure_foreground=ensure_foreground,
        )
        last_is_fg = is_fg
        ratio = _compute_color_ratio(crop, target_color=target_color)
        elapsed = round(time.monotonic() - start, 2)
        history.append({"t": elapsed, "ratio": ratio})
        history = history[-20:]
        consecutive = consecutive + 1 if ratio >= float(min_pixel_ratio) else 0
        if consecutive >= max(1, int(stable_samples)):
            return {
                "satisfied": True,
                "elapsed_sec": elapsed,
                "effective_timeout_sec": round(eff_timeout, 2),
                "timeout_clamped": timeout_clamped,
                "is_foreground": last_is_fg,
                "target_color": target_color.lower().strip(),
                "final_ratio": ratio,
                "consecutive_samples": consecutive,
                "recent_history": history,
            }
        time.sleep(max(0.15, float(poll_interval_sec)))

    return {
        "satisfied": False,
        "elapsed_sec": round(time.monotonic() - start, 2),
        "effective_timeout_sec": round(eff_timeout, 2),
        "timeout_clamped": timeout_clamped,
        "is_foreground": last_is_fg,
        "target_color": target_color.lower().strip(),
        "final_ratio": history[-1]["ratio"] if history else 0.0,
        "consecutive_samples": consecutive,
        "recent_history": history,
    }


@mcp.tool()
def watch_region_until_stable(
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    max_hamming: int = 3,
    stable_duration_sec: float = 2.0,
    poll_interval_sec: float = 0.4,
    timeout_sec: float = 30.0,
    backend: str = "auto",
    ensure_foreground: bool = False,
) -> dict[str, Any]:
    """Wait until a window or ROI stops changing beyond dhash max_hamming (filters 3D render jitter)."""
    eff_timeout, timeout_clamped = _clamp_timeout(timeout_sec)
    start = time.monotonic()
    prev_hash: int | None = None
    stable_since: float | None = None

    while time.monotonic() - start <= eff_timeout:
        img, frame_origin, client_origin, win, used, is_fg, _ = _capture_target(
            window_query, backend, ensure_foreground=ensure_foreground
        )
        client_size = win.client_size if win else (int(img.shape[1]), int(img.shape[0]))
        x1, y1, x2, y2, _ = _resolve_roi_in_frame(img.shape, frame_origin, client_origin, client_size, roi)
        cur_hash = _compute_dhash(img[y1:y2, x1:x2])
        now = time.monotonic()
        dist = _hamming_distance(prev_hash, cur_hash) if prev_hash is not None else 999
        if dist <= max_hamming:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= stable_duration_sec:
                return {
                    "stable": True,
                    "backend_used": used,
                    "is_foreground": is_fg,
                    "elapsed_sec": round(now - start, 2),
                    "timeout_clamped": timeout_clamped,
                    "last_hamming": dist,
                }
        else:
            stable_since = None
        prev_hash = cur_hash
        time.sleep(max(0.15, poll_interval_sec))

    return {
        "stable": False,
        "elapsed_sec": round(time.monotonic() - start, 2),
        "timeout_clamped": timeout_clamped,
    }


@mcp.tool()
def save_preset(preset_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Save calibrated normalized coordinates and gauge ROIs for an OBER experiment preset."""
    path = _preset_path(preset_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"preset_id": preset_id, "path": str(path)}


@mcp.tool()
def load_preset(preset_id: str) -> dict[str, Any]:
    """Load an existing experiment preset JSON."""
    path = _preset_path(preset_id)
    if not path.exists():
        raise FileNotFoundError(f"Preset 不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@mcp.tool()
def list_presets() -> dict[str, Any]:
    """List all saved experiment presets in the plugin presets directory."""
    files = sorted(p.stem for p in PRESETS_DIR.glob("*.json"))
    return {"presets_dir": str(PRESETS_DIR), "presets": files}


@mcp.tool()
def resolve_preset_point(preset_id: str, target_name: str, window_query: str = "") -> dict[str, Any]:
    """Convert a normalized button/valve coordinate or gauge ROI from a preset into current physical screen pixels."""
    preset = load_preset(preset_id)
    if not preset.get("calibrated", False):
        raise ValueError(f"Preset '{preset_id}' 尚未完成实机校准 (calibrated=false)，禁止直接换算点击。")
    wq = window_query.strip() or str(preset.get("window_query", "")).strip()
    win = _resolve_window(wq)
    if win is None:
        raise ValueError("必须提供有效的 window_query 或在 preset 中配置 window_query。")
    cx, cy = win.client_origin
    cw, ch = win.client_size

    points = preset.get("points", {})
    rois = preset.get("rois", {})
    if target_name in points:
        pt = points[target_name]
        sx = int(round(cx + float(pt["rx"]) * cw))
        sy = int(round(cy + float(pt["ry"]) * ch))
        return {"kind": "point", "target": target_name, "screen_x": sx, "screen_y": sy, "window": win.to_dict()}
    if target_name in rois:
        r = rois[target_name]
        return {"kind": "roi", "target": target_name, "roi": r, "window": win.to_dict()}
    raise KeyError(f"Preset '{preset_id}' 中未找到 '{target_name}'。")


def _bring_hwnd_to_front(hwnd: int) -> bool:
    """Restore if minimized and escalate across Win32 foreground-lock strategies until foreground."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    with contextlib.suppress(Exception):
        win32gui.SetForegroundWindow(hwnd)
    if _get_foreground_info()[0] == hwnd:
        return True

    # Strategy 2: Attach input queues between current, foreground, and target threads.
    cur_tid = int(_kernel32.GetCurrentThreadId())
    fg_hwnd = _get_foreground_info()[0]
    fg_tid = int(_user32.GetWindowThreadProcessId(fg_hwnd, None)) if fg_hwnd else 0
    tgt_tid = int(_user32.GetWindowThreadProcessId(hwnd, None))
    attached: list[int] = []
    try:
        for tid in (fg_tid, tgt_tid):
            if tid and tid != cur_tid and tid not in attached:
                if _user32.AttachThreadInput(cur_tid, tid, True):
                    attached.append(tid)
        with contextlib.suppress(Exception):
            win32gui.ShowWindow(hwnd, 5)  # SW_SHOW
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
    finally:
        for tid in reversed(attached):
            with contextlib.suppress(Exception):
                _user32.AttachThreadInput(cur_tid, tid, False)
    if _get_foreground_info()[0] == hwnd:
        return True

    # Strategy 3: Brief Z-order HWND_TOPMOST -> HWND_NOTOPMOST toggle + BringWindowToTop.
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    with contextlib.suppress(Exception):
        _user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0, flags)
        _user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    if _get_foreground_info()[0] == hwnd:
        return True

    # Strategy 4: Benign Alt+Ctrl chord to unlock foreground switching without opening menu bars.
    with contextlib.suppress(Exception):
        alt_scan = int(_user32.MapVirtualKeyW(VK_MENU, 0))
        ctrl_scan = int(_user32.MapVirtualKeyW(VK_CONTROL, 0))
        _send_inputs(
            [
                _make_key_input(vk=VK_MENU, scan=alt_scan, flags=0),
                _make_key_input(vk=VK_CONTROL, scan=ctrl_scan, flags=0),
                _make_key_input(vk=VK_CONTROL, scan=ctrl_scan, flags=KEYEVENTF_KEYUP),
                _make_key_input(vk=VK_MENU, scan=alt_scan, flags=KEYEVENTF_KEYUP),
            ]
        )
        time.sleep(0.02)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    return _get_foreground_info()[0] == hwnd


def _activate_window_info(win: WindowInfo) -> WindowInfo:
    """Restore if minimized or bring to foreground if unfocused, skipping sleep when already active."""
    fg_hwnd, _ = _get_foreground_info()
    if win.is_minimized or fg_hwnd != win.hwnd:
        was_minimized = win.is_minimized
        _bring_hwnd_to_front(win.hwnd)
        time.sleep(0.12 if was_minimized else 0.06)
        return _inspect_hwnd(win.hwnd, include_minimized=True) or win
    return win


@mcp.tool()
def bring_window_to_front(window_query: str) -> dict[str, Any]:
    """Activate and bring a matching window (including minimized ones) to foreground."""
    _enable_dpi_awareness()
    win = _resolve_window(window_query, include_minimized=True)
    if win is None:
        raise ValueError("window_query 不能为空")
    win = _activate_window_info(win)
    fg_hwnd, fg_title = _get_foreground_info()
    is_fg = fg_hwnd == win.hwnd
    out: dict[str, Any] = {
        "activated": is_fg,
        "is_foreground": is_fg,
        "foreground_title": fg_title,
        "window": win.to_dict(),
    }
    if not is_fg:
        out["warning"] = "Windows 前台锁阻止切换，视觉读取将自动回退 WGC，键鼠点击前请确认目标窗口可见"
    return out


def _activate_if_requested(window_query: str) -> WindowInfo | None:
    """Restore/activate matching window when window_query is non-empty."""
    if not window_query.strip():
        return None
    win = _resolve_window(window_query, include_minimized=True)
    if win is not None:
        win = _activate_window_info(win)
    return win


def _make_key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    return _INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def _send_inputs(inputs: list[_INPUT]) -> None:
    if not inputs:
        return
    arr = (_INPUT * len(inputs))(*inputs)
    _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _tap_vk(vk: int) -> None:
    scan = int(_user32.MapVirtualKeyW(vk, 0))
    _send_inputs([_make_key_input(vk=vk, scan=scan, flags=0), _make_key_input(vk=vk, scan=scan, flags=KEYEVENTF_KEYUP)])


@mcp.tool()
def fast_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    window_query: str = "",
    hover_delay_sec: float = 0.08,
    press_duration_sec: float = 0.04,
) -> dict[str, Any]:
    """Fast DPI-aware physical-pixel mouse click without UIA tree overhead (ideal for Unity 3D / DCS)."""
    _enable_dpi_awareness()
    _activate_if_requested(window_query)

    btn = button.lower().strip()
    btn_map = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    if btn not in btn_map:
        raise ValueError(f"button 必须是 {sorted(btn_map)} 之一")
    down_flag, up_flag = btn_map[btn]

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(max(0.01, float(hover_delay_sec)))
    press_delay = max(0.01, float(press_duration_sec))
    for _ in range(max(1, clicks)):
        _user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(press_delay)
        _user32.mouse_event(up_flag, 0, 0, 0, 0)
        time.sleep(0.04)
    return {"clicked": [int(x), int(y)], "button": btn, "clicks": max(1, clicks)}


def _wheel_mouse_delta(direction: str, horizontal: bool = False) -> tuple[int, int]:
    """Return (Win32 wheel flag, signed wheel delta) for vertical or horizontal scrolling."""
    d = direction.lower().strip()
    if d in {"up", "down"}:
        if horizontal:
            raise ValueError("horizontal=True 时 direction 必须为 'left' 或 'right'")
        return MOUSEEVENTF_WHEEL, WHEEL_DELTA if d == "up" else -WHEEL_DELTA
    if d in {"left", "right"}:
        return MOUSEEVENTF_HWHEEL, WHEEL_DELTA if d == "right" else -WHEEL_DELTA
    raise ValueError("direction 必须是 'up'、'down'、'left' 或 'right'")


@mcp.tool()
def scroll_mouse(
    x: int,
    y: int,
    direction: str = "down",
    notches: int = 6,
    window_query: str = "",
) -> dict[str, Any]:
    """Scroll vertically (up/down) or horizontally (left/right) at screen pixel (x, y) via Win32 SendInput/mouse_event."""
    _enable_dpi_awareness()
    _activate_if_requested(window_query)
    d = direction.lower().strip()
    flag, unit_delta = _wheel_mouse_delta(d, horizontal=d in {"left", "right"})
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    count = max(1, int(notches))
    encoded_delta = ctypes.c_uint(unit_delta & 0xFFFFFFFF).value
    for _ in range(count):
        _user32.mouse_event(flag, 0, 0, encoded_delta, 0)
        time.sleep(0.025)
    return {"scrolled": True, "at": [int(x), int(y)], "direction": d, "notches": count}


@mcp.tool()
def drag_mouse(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    button: str = "right",
    duration_sec: float = 0.25,
    steps: int = 12,
    window_query: str = "",
) -> dict[str, Any]:
    """Drag mouse from (from_x, from_y) to (to_x, to_y) with left/right/middle button (supports Unity 3D camera rotation)."""
    _enable_dpi_awareness()
    _activate_if_requested(window_query)

    btn = button.lower().strip()
    btn_map = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    if btn not in btn_map:
        raise ValueError(f"button 必须是 {sorted(btn_map)} 之一")
    down_flag, up_flag = btn_map[btn]

    n_steps = max(2, int(steps))
    step_delay = max(0.005, float(duration_sec) / n_steps)

    _user32.SetCursorPos(int(from_x), int(from_y))
    time.sleep(0.04)
    _user32.mouse_event(down_flag, 0, 0, 0, 0)
    time.sleep(0.03)
    for i in range(1, n_steps + 1):
        nx = int(round(from_x + (to_x - from_x) * i / n_steps))
        ny = int(round(from_y + (to_y - from_y) * i / n_steps))
        _user32.SetCursorPos(nx, ny)
        time.sleep(step_delay)
    _user32.mouse_event(up_flag, 0, 0, 0, 0)
    return {
        "dragged": True,
        "button": btn,
        "from": [int(from_x), int(from_y)],
        "to": [int(to_x), int(to_y)],
    }


@mcp.tool()
def hover_and_ocr(
    x: int,
    y: int,
    window_query: str = "",
    roi: dict[str, Any] | None = None,
    hover_delay_sec: float = 0.18,
    box_width: int = 380,
    box_height: int = 240,
    scale: float = 2.0,
    backend: str = "auto",
) -> dict[str, Any]:
    """Hover cursor at (x, y) and OCR the local region to read dynamic Unity tooltips (e.g., 已开/已关)."""
    _enable_dpi_awareness()
    win = _activate_if_requested(window_query)
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(max(0.05, float(hover_delay_sec)))

    effective_roi = roi
    if effective_roi is None:
        bw = max(80, int(box_width))
        bh = max(60, int(box_height))
        if win is not None:
            ox, oy = win.client_origin
            cw, ch = win.client_size
        else:
            ox, oy = 0, 0
            with mss.mss() as sct:
                mon = sct.monitors[1]
                cw, ch = int(mon["width"]), int(mon["height"])
        # Unity tooltips appear slightly right and below the cursor while pin labels sit just above.
        left = max(0, min(cw - 40, (int(x) + 40 - bw // 2) - ox))
        top = max(0, min(ch - 40, (int(y) + 10 - bh // 2) - oy))
        effective_roi = {"x": left, "y": top, "w": min(bw, cw - left), "h": min(bh, ch - top)}

    res = ocr_window_or_roi(
        window_query=window_query,
        roi=effective_roi,
        scale=scale,
        backend=backend,
        ensure_foreground=bool(window_query.strip()),
    )
    res["hovered"] = [int(x), int(y)]
    return res


def _normalize_valve_state(raw: str) -> str | None:
    """Normalize 开/已开/已打开 -> '已开' and 关/已关/已关闭 -> '已关'."""
    s = raw.strip()
    if s in {"已开", "已打开", "打开", "开启", "开", "开着"}:
        return "已开"
    if s in {"已关", "已关闭", "关闭", "关", "关着"}:
        return "已关"
    return None


def _parse_valve_tooltip_state(joined_text: str, items: list[dict[str, Any]], target_tag: str) -> str | None:
    """Extract normalized '已开' or '已关' specifically for ASCII or Chinese target_tag from tooltip OCR."""
    tag_u = target_tag.strip().upper()
    texts = [str(it.get("text", "")) for it in items] + [joined_text]
    state_pat = r"(已打开|已开|打开|开启|开|已关闭|已关|关闭|关)(?![\u4e00-\u9fffA-Za-z0-9])"
    for txt in texts:
        m = re.search(rf"{re.escape(tag_u)}\s*[:：]\s*{state_pat}", txt.upper())
        if m:
            return _normalize_valve_state(m.group(1))
    has_cjk_tag = bool(re.search(r"[\u4e00-\u9fff]", tag_u))
    for txt in texts:
        u = txt.upper()
        other = re.search(rf"([A-Z]{{1,3}}\d{{2,3}})\s*[:：]\s*{state_pat}", u)
        if other and other.group(1) != tag_u:
            continue
        if has_cjk_tag:
            other_cn = re.search(rf"([\u4e00-\u9fff]{{2,10}})\s*[:：]\s*{state_pat}", u)
            if other_cn:
                found_cn = other_cn.group(1)
                if tag_u not in found_cn and found_cn not in tag_u:
                    continue
        has_open = ("已打开" in u) or ("已开" in u)
        has_close = ("已关闭" in u) or ("已关" in u)
        if has_open and not has_close:
            return "已开"
        if has_close and not has_open:
            return "已关"
        m_short = re.search(r"[:：]\s*(开|关)(?![\u4e00-\u9fffA-Za-z0-9])", u)
        if m_short:
            return _normalize_valve_state(m_short.group(1))
    return None


@mcp.tool()
def click_3d_valve_verified(
    window_query: str,
    target_tag: str,
    desired_state: str,
    probe_points: list[list[int]],
    hover_delay_sec: float = 0.18,
    post_click_sleep_sec: float = 0.25,
    press_duration_sec: float = 0.04,
) -> dict[str, Any]:
    """Idempotently set a 3D Unity ball/hand valve or switch by probing hover tooltips before & after click."""
    state_map = {
        "open": "已开",
        "on": "已开",
        "开": "已开",
        "开启": "已开",
        "已开": "已开",
        "已打开": "已开",
        "打开": "已开",
        "close": "已关",
        "closed": "已关",
        "off": "已关",
        "关": "已关",
        "已关": "已关",
        "已关闭": "已关",
        "关闭": "已关",
    }
    want = state_map.get(desired_state.strip().lower())
    if want is None:
        raise ValueError("desired_state 必须是 'open'/'开'/'已开'/'已打开' 或 'close'/'关'/'已关'/'已关闭'")
    if not probe_points:
        raise ValueError("probe_points 不能为空")

    probe_log: list[dict[str, Any]] = []
    matched_pt: list[int] | None = None
    initial_state: str | None = None

    for pt in probe_points:
        px, py = int(pt[0]), int(pt[1])
        hres = hover_and_ocr(x=px, y=py, window_query=window_query, hover_delay_sec=hover_delay_sec)
        parsed = _parse_valve_tooltip_state(hres.get("joined_text", ""), hres.get("items", []), target_tag)
        probe_log.append({"point": [px, py], "parsed": parsed, "text": hres.get("joined_text", "")})
        if parsed is not None:
            matched_pt = [px, py]
            initial_state = parsed
            break

    if matched_pt is None or initial_state is None:
        return {
            "target_tag": target_tag,
            "desired_state": want,
            "changed": False,
            "already_in_state": False,
            "verified": False,
            "error": f"未能在给定候选点识别到 {target_tag} 的开/关/已开/已关/已打开/已关闭提示",
            "probes": probe_log,
        }

    if initial_state == want:
        return {
            "target_tag": target_tag,
            "desired_state": want,
            "changed": False,
            "already_in_state": True,
            "verified": True,
            "verified_state": initial_state,
            "matched_point": matched_pt,
            "probes": probe_log,
        }

    fast_click(
        x=matched_pt[0],
        y=matched_pt[1],
        window_query=window_query,
        hover_delay_sec=0.12,
        press_duration_sec=float(press_duration_sec),
    )
    time.sleep(max(0.05, float(post_click_sleep_sec)))

    # Re-probe all candidate points because the 90-degree handle rotation shifts its hitbox.
    recheck_log: list[dict[str, Any]] = []
    final_state: str | None = None
    final_pt: list[int] | None = None
    for pt in probe_points:
        px, py = int(pt[0]), int(pt[1])
        hres = hover_and_ocr(x=px, y=py, window_query=window_query, hover_delay_sec=hover_delay_sec)
        parsed = _parse_valve_tooltip_state(hres.get("joined_text", ""), hres.get("items", []), target_tag)
        recheck_log.append({"point": [px, py], "parsed": parsed, "text": hres.get("joined_text", "")})
        if parsed is not None:
            final_state = parsed
            final_pt = [px, py]
            break

    return {
        "target_tag": target_tag,
        "desired_state": want,
        "changed": True,
        "already_in_state": False,
        "verified": final_state == want,
        "initial_state": initial_state,
        "verified_state": final_state,
        "clicked_point": matched_pt,
        "verified_point": final_pt,
        "probes": probe_log,
        "rechecks": recheck_log,
    }


def _compute_valve_step(
    current_pct: float,
    target_pct: float,
    tolerance: float = 0.6,
    rate_pct_per_sec: float | None = None,
    overshoot_count: int = 0,
    last_duration_sec: float | None = None,
) -> dict[str, Any]:
    """Choose the next valve popup adjustment action with optional rate calibration & overshoot damping."""
    diff = float(target_pct) - float(current_pct)
    if abs(diff) <= max(0.0, float(tolerance)):
        return {"action": "done", "direction": None, "diff": round(diff, 3)}
    direction = "open" if diff > 0 else "close"
    if float(target_pct) <= 0.0 or float(target_pct) >= 100.0:
        return {
            "action": "hold_endpoint",
            "direction": direction,
            "press_duration_sec": 2.5,
            "diff": round(diff, 3),
        }
    est_time = (abs(diff) / float(rate_pct_per_sec)) if (rate_pct_per_sec and float(rate_pct_per_sec) > 5.0) else None
    if abs(diff) <= 8.0 or (est_time is not None and est_time < 0.16):
        max_clicks = 3 if overshoot_count > 0 else 5
        scale = 0.6 if overshoot_count > 0 else 1.0
        return {
            "action": "discrete_clicks",
            "direction": direction,
            "count": min(max_clicks, max(1, round(abs(diff) * scale))),
            "press_duration_sec": 0.05,
            "diff": round(diff, 3),
        }
    if est_time is not None:
        damping = 0.55 if overshoot_count > 0 else 0.78
        dur = max(0.06, min(1.2, est_time * damping))
    elif overshoot_count > 0 and last_duration_sec and float(last_duration_sec) > 0:
        dur = max(0.06, min(1.2, float(last_duration_sec) * 0.45))
    else:
        dur = min(1.2, 0.26 + abs(diff) * 0.0085)
    return {
        "action": "ramp_hold",
        "direction": direction,
        "press_duration_sec": round(dur, 3),
        "diff": round(diff, 3),
    }


def _resolve_win_norm_point(win: WindowInfo | None, rx: float, ry: float) -> tuple[int, int]:
    if win is not None:
        return (
            int(round(win.client_origin[0] + rx * win.client_size[0])),
            int(round(win.client_origin[1] + ry * win.client_size[1])),
        )
    with mss.mss() as sct:
        mon = sct.monitors[1]
        return (
            int(round(int(mon["left"]) + rx * int(mon["width"]))),
            int(round(int(mon["top"]) + ry * int(mon["height"]))),
        )


def _compute_slider_click_xy(
    target_pct: float,
    zero_xy: tuple[int, int],
    full_xy: tuple[int, int],
) -> tuple[int, int]:
    """Linearly interpolate a 0-100% valve slider click coordinate."""
    frac = max(0.0, min(1.0, float(target_pct) / 100.0))
    x = int(round(zero_xy[0] + frac * (full_xy[0] - zero_xy[0])))
    y = int(round(zero_xy[1] + frac * (full_xy[1] - zero_xy[1])))
    return x, y


@mcp.tool()
def adjust_valve_popup(
    window_query: str,
    target_pct: float,
    tolerance: float = 0.6,
    open_xy: list[int] | None = None,
    close_xy: list[int] | None = None,
    neutral_xy: list[int] | None = None,
    close_popup_xy: list[int] | None = None,
    pct_roi: dict[str, Any] | None = None,
    max_iterations: int = 12,
    close_when_done: bool = False,
    slider_zero_xy: list[int] | None = None,
    slider_full_xy: list[int] | None = None,
    use_slider_jump: bool = False,
) -> dict[str, Any]:
    """Closed-loop tune an already-open Unity valve popup (%) to target_pct with optional slider direct-jump and neutral title hover guard."""
    _enable_dpi_awareness()
    win = _activate_if_requested(window_query)
    open_pt = (int(open_xy[0]), int(open_xy[1])) if open_xy else _resolve_win_norm_point(win, 0.47492, 0.47964)
    close_pt = (int(close_xy[0]), int(close_xy[1])) if close_xy else _resolve_win_norm_point(win, 0.52508, 0.47964)
    neutral_pt = (int(neutral_xy[0]), int(neutral_xy[1])) if neutral_xy else _resolve_win_norm_point(win, 0.50052, 0.40020)
    close_popup_pt = (
        (int(close_popup_xy[0]), int(close_popup_xy[1]))
        if close_popup_xy
        else _resolve_win_norm_point(win, 0.55225, 0.39722)
    )
    eff_roi = pct_roi or {"rx": 0.46, "ry": 0.54, "rw": 0.08, "rh": 0.06}

    slider_enabled = bool(use_slider_jump or slider_zero_xy or slider_full_xy)
    slider_zero_pt = (
        (int(slider_zero_xy[0]), int(slider_zero_xy[1]))
        if slider_zero_xy
        else _resolve_win_norm_point(win, 0.44514, 0.58491)
    )
    slider_full_pt = (
        (int(slider_full_xy[0]), int(slider_full_xy[1]))
        if slider_full_xy
        else _resolve_win_norm_point(win, 0.55486, 0.58491)
    )
    slider_jumped = False

    def _read_pct() -> tuple[float | None, str]:
        _activate_if_requested(window_query)
        _user32.SetCursorPos(neutral_pt[0], neutral_pt[1])
        time.sleep(0.12)
        res = ocr_window_or_roi(
            window_query=window_query,
            roi=eff_roi,
            scale=2.0,
            numeric_only=True,
            ensure_foreground=True,
        )
        val = res.get("primary_number")
        if val is None and res.get("numbers"):
            val = res["numbers"][0]
        return val, str(res.get("joined_text", ""))

    history: list[dict[str, Any]] = []
    achieved = False
    stalled = False
    diagnostic: str | None = None
    final_pct: float | None = None
    prev_pct: float | None = None
    prev_dir: str | None = None
    prev_action: str | None = None
    prev_dur: float | None = None
    acted_since_read = False
    rate_est: float | None = None
    overshoot_count = 0
    stall_count = 0

    for it_idx in range(max(1, int(max_iterations))):
        cur_pct, raw_txt = _read_pct()
        if cur_pct is None:
            history.append({"iter": it_idx, "pct": None, "raw_text": raw_txt, "action": "retry_ocr"})
            acted_since_read = False
            time.sleep(0.2)
            continue

        final_pct = float(cur_pct)
        if acted_since_read and prev_pct is not None:
            delta = abs(final_pct - prev_pct)
            if delta < 0.05:
                stall_count += 1
            else:
                stall_count = 0
            if (
                prev_action == "ramp_hold"
                and prev_dur
                and prev_dur > 0
                and 0.5 < final_pct < 99.5
                and 0.5 < prev_pct < 99.5
                and delta >= 2.0
            ):
                inst_rate = delta / float(prev_dur)
                rate_est = (0.5 * rate_est + 0.5 * inst_rate) if rate_est else inst_rate

        if slider_enabled and not slider_jumped and abs(float(target_pct) - final_pct) > max(float(tolerance), 2.0):
            sx, sy = _compute_slider_click_xy(target_pct, slider_zero_pt, slider_full_pt)
            fast_click(x=sx, y=sy, window_query=window_query, hover_delay_sec=0.05, press_duration_sec=0.05)
            history.append(
                {
                    "iter": it_idx,
                    "pct": final_pct,
                    "action": "slider_jump",
                    "clicked": [sx, sy],
                    "diff": round(float(target_pct) - final_pct, 2),
                }
            )
            slider_jumped = True
            prev_pct = final_pct
            prev_action = "slider_jump"
            prev_dur = 0.05
            acted_since_read = True
            time.sleep(0.25)
            continue

        desired_dir = (
            ("open" if float(target_pct) > final_pct else "close")
            if abs(float(target_pct) - final_pct) > tolerance
            else None
        )
        if prev_dir is not None and desired_dir is not None and desired_dir != prev_dir:
            overshoot_count += 1

        step = _compute_valve_step(
            final_pct,
            target_pct,
            tolerance=tolerance,
            rate_pct_per_sec=rate_est,
            overshoot_count=overshoot_count,
            last_duration_sec=prev_dur,
        )
        if step["action"] == "done":
            history.append({"iter": it_idx, "pct": final_pct, **step})
            achieved = True
            break

        if acted_since_read and stall_count >= 3:
            stalled = True
            diagnostic = "阀门开度连续 3 次未变化，请核对 open_xy/close_xy 是否点中阀门图标（如 1522 为 y=515 红/绿手轮，非 y=565 文字）且弹窗处于激活状态"
            history.append({"iter": it_idx, "pct": final_pct, "action": "stalled", "diff": step["diff"]})
            break

        history.append({"iter": it_idx, "pct": final_pct, **step})

        btn = open_pt if step["direction"] == "open" else close_pt
        if step["action"] == "discrete_clicks":
            for c_idx in range(int(step["count"])):
                fast_click(
                    x=btn[0],
                    y=btn[1],
                    window_query=window_query,
                    hover_delay_sec=0.05,
                    press_duration_sec=float(step["press_duration_sec"]),
                )
                if c_idx + 1 < int(step["count"]):
                    time.sleep(0.10)
        else:
            fast_click(
                x=btn[0],
                y=btn[1],
                window_query=window_query,
                hover_delay_sec=0.05,
                press_duration_sec=float(step["press_duration_sec"]),
            )
        prev_pct = final_pct
        prev_dir = step["direction"]
        prev_action = step["action"]
        prev_dur = float(step["press_duration_sec"])
        acted_since_read = True
        time.sleep(0.25)

    if not achieved and not stalled:
        last_pct, _ = _read_pct()
        if last_pct is not None:
            final_pct = float(last_pct)
            achieved = abs(final_pct - float(target_pct)) <= max(0.0, float(tolerance))

    popup_closed = False
    if achieved and close_when_done:
        fast_click(x=close_popup_pt[0], y=close_popup_pt[1], window_query=window_query)
        popup_closed = True

    result: dict[str, Any] = {
        "achieved": achieved,
        "target_pct": float(target_pct),
        "final_pct": final_pct,
        "iterations": len(history),
        "popup_closed": popup_closed,
        "stalled": stalled,
        "observed_rate_pct_per_sec": round(rate_est, 2) if rate_est else None,
        "history": history,
    }
    if diagnostic:
        result["diagnostic"] = diagnostic
    return result


@mcp.tool()
def fast_type(
    x: int,
    y: int,
    text: str,
    clear: bool = True,
    press_enter: bool = True,
    window_query: str = "",
) -> dict[str, Any]:
    """Click input box at (x, y) and type text via SendInput KEYEVENTF_UNICODE (IME-safe)."""
    fast_click(x=int(x), y=int(y), window_query=window_query, hover_delay_sec=0.05)
    time.sleep(0.06)

    if clear:
        _send_inputs(
            [
                _make_key_input(vk=VK_CONTROL, scan=int(_user32.MapVirtualKeyW(VK_CONTROL, 0)), flags=0),
                _make_key_input(vk=0x41, scan=int(_user32.MapVirtualKeyW(0x41, 0)), flags=0),
                _make_key_input(vk=0x41, scan=int(_user32.MapVirtualKeyW(0x41, 0)), flags=KEYEVENTF_KEYUP),
                _make_key_input(vk=VK_CONTROL, scan=int(_user32.MapVirtualKeyW(VK_CONTROL, 0)), flags=KEYEVENTF_KEYUP),
            ]
        )
        time.sleep(0.03)
        _tap_vk(VK_BACK)
        time.sleep(0.03)

    if text:
        utf16 = text.encode("utf-16-le")
        key_events: list[_INPUT] = []
        for idx in range(0, len(utf16), 2):
            code_unit = int.from_bytes(utf16[idx : idx + 2], "little")
            key_events.append(_make_key_input(vk=0, scan=code_unit, flags=KEYEVENTF_UNICODE))
            key_events.append(_make_key_input(vk=0, scan=code_unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send_inputs(key_events)
        time.sleep(0.03)

    if press_enter:
        _tap_vk(VK_RETURN)

    return {"typed": text, "at": [int(x), int(y)], "cleared": clear, "pressed_enter": press_enter}


@mcp.tool()
def run_action_sequence(
    actions: list[dict[str, Any]],
    stop_on_error: bool = True,
    default_window_query: str = "",
) -> dict[str, Any]:
    """Execute a local serial batch of window/click/move/type/drag/scroll/valve/ocr/read_led steps in a single MCP call."""
    results: list[dict[str, Any]] = []
    all_ok = True

    for idx, act in enumerate(actions):
        op = str(act.get("op") or act.get("type") or "").strip().lower()
        wq = str(act.get("window_query", "") or default_window_query).strip()
        try:
            if op == "activate":
                if not wq:
                    raise ValueError("activate 动作缺少 window_query")
                res = bring_window_to_front(window_query=wq)
            elif op == "move":
                _enable_dpi_awareness()
                _activate_if_requested(wq)
                _user32.SetCursorPos(int(act["x"]), int(act["y"]))
                delay = max(0.0, float(act.get("hover_delay_sec", 0.05)))
                if delay > 0:
                    time.sleep(delay)
                res = {"moved": [int(act["x"]), int(act["y"])]}
            elif op == "click":
                res = fast_click(
                    x=int(act["x"]),
                    y=int(act["y"]),
                    button=str(act.get("button", "left")),
                    clicks=int(act.get("clicks", 1)),
                    window_query=wq,
                    hover_delay_sec=float(act.get("hover_delay_sec", 0.08)),
                    press_duration_sec=float(act.get("press_duration_sec", 0.04)),
                )
            elif op == "type":
                res = fast_type(
                    x=int(act["x"]),
                    y=int(act["y"]),
                    text=str(act["text"]),
                    clear=bool(act.get("clear", True)),
                    press_enter=bool(act.get("press_enter", True)),
                    window_query=wq,
                )
            elif op == "drag":
                res = drag_mouse(
                    from_x=int(act["from_x"]),
                    from_y=int(act["from_y"]),
                    to_x=int(act["to_x"]),
                    to_y=int(act["to_y"]),
                    button=str(act.get("button", "left")),
                    duration_sec=float(act.get("duration_sec", 0.25)),
                    steps=int(act.get("steps", 12)),
                    window_query=wq,
                )
            elif op == "scroll":
                res = scroll_mouse(
                    x=int(act["x"]),
                    y=int(act["y"]),
                    direction=str(act.get("direction", "down")),
                    notches=int(act.get("notches", 6)),
                    window_query=wq,
                )
            elif op == "click_valve":
                res = click_3d_valve_verified(
                    window_query=wq,
                    target_tag=str(act["target_tag"]),
                    desired_state=str(act["desired_state"]),
                    probe_points=act["probe_points"],
                    hover_delay_sec=float(act.get("hover_delay_sec", 0.18)),
                    post_click_sleep_sec=float(act.get("post_click_sleep_sec", 0.25)),
                    press_duration_sec=float(act.get("press_duration_sec", 0.04)),
                )
            elif op == "adjust_valve":
                res = adjust_valve_popup(
                    window_query=wq,
                    target_pct=float(act["target_pct"]),
                    tolerance=float(act.get("tolerance", 0.6)),
                    open_xy=act.get("open_xy"),
                    close_xy=act.get("close_xy"),
                    neutral_xy=act.get("neutral_xy"),
                    close_popup_xy=act.get("close_popup_xy"),
                    pct_roi=act.get("pct_roi"),
                    max_iterations=int(act.get("max_iterations", 12)),
                    close_when_done=bool(act.get("close_when_done", False)),
                    slider_zero_xy=act.get("slider_zero_xy"),
                    slider_full_xy=act.get("slider_full_xy"),
                    use_slider_jump=bool(act.get("use_slider_jump", False)),
                )
            elif op == "hover_ocr":
                res = hover_and_ocr(
                    x=int(act["x"]),
                    y=int(act["y"]),
                    window_query=wq,
                    roi=act.get("roi"),
                    hover_delay_sec=float(act.get("hover_delay_sec", 0.18)),
                    box_width=int(act.get("box_width", 380)),
                    box_height=int(act.get("box_height", 240)),
                )
            elif op == "ocr":
                res = ocr_window_or_roi(
                    window_query=wq,
                    roi=act.get("roi"),
                    scale=float(act.get("scale", 2.0)),
                    invert=bool(act.get("invert", False)),
                    numeric_only=bool(act.get("numeric_only", False)),
                    backend=str(act.get("backend", "auto")),
                    ensure_foreground=bool(act.get("ensure_foreground", False)),
                )
            elif op == "detect_boxes":
                res = detect_ui_boxes(
                    window_query=wq,
                    roi=act.get("roi"),
                    kind=str(act.get("kind", "input_boxes")),
                    min_brightness=int(act.get("min_brightness", 228)),
                    backend=str(act.get("backend", "auto")),
                    ensure_foreground=bool(act.get("ensure_foreground", False)),
                )
            elif op == "capture":
                res = capture_window_wgc(
                    window_query=wq,
                    save_path=str(act.get("save_path", "")),
                    backend=str(act.get("backend", "auto")),
                    ensure_foreground=bool(act.get("ensure_foreground", False)),
                )
            elif op == "read_led":
                res = read_seven_segment_roi(
                    window_query=wq,
                    roi=act.get("roi"),
                    color=str(act.get("color", "red")),
                    save_mask_crop=bool(act.get("save_mask_crop", False)),
                    ensure_foreground=bool(act.get("ensure_foreground", False)),
                )
            elif op in {"sleep", "wait"}:
                raw_secs = act.get("seconds") if act.get("seconds") is not None else act.get("sec", 0.5)
                secs = max(0.0, min(15.0, float(raw_secs)))
                time.sleep(secs)
                res = {"slept_sec": secs}
            elif op == "wait_stable":
                res = watch_region_until_stable(
                    window_query=wq,
                    roi=act.get("roi"),
                    stable_duration_sec=float(act.get("stable_duration_sec", 0.8)),
                    timeout_sec=float(act.get("timeout_sec", 5.0)),
                    ensure_foreground=bool(act.get("ensure_foreground", False)),
                )
            else:
                raise ValueError(f"不支持的 action op: '{op}'")
            results.append({"index": idx, "op": op, "ok": True, "result": res})
        except Exception as exc:
            all_ok = False
            results.append({"index": idx, "op": op, "ok": False, "error": str(exc)})
            if stop_on_error:
                break

    return {
        "all_ok": all_ok,
        "completed_count": sum(1 for r in results if r["ok"]),
        "total_actions": len(actions),
        "results": results,
    }


def main() -> None:
    transport = os.environ.get("OBER_OCR_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        host = os.environ.get("OBER_OCR_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("OBER_OCR_MCP_PORT", "8011"))
        mcp.run(transport="streamable-http", show_banner=False, host=host, port=port)
    else:
        mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
