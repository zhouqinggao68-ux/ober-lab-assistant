"""Self-contained unit, CLI, and MCP stdio verification suite for ober_ocr_mcp v0.5.0."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

SERVER_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SERVER_DIR.parent
CHEM_CALC_PY = PLUGIN_ROOT / "skills" / "ober-lab-assistant" / "scripts" / "chem_calc.py"
CMD_LAUNCHER = PLUGIN_ROOT / "scripts" / "run-ober-ocr-mcp.cmd"

sys.path.insert(0, str(SERVER_DIR))
import ober_ocr_mcp as srv  # noqa: E402


def _draw_seven_segment_digit(
    canvas: np.ndarray,
    x0: int,
    y0: int,
    w: int,
    h: int,
    bits: tuple[int, ...],
    lit_bgr: tuple[int, int, int] = (20, 20, 250),
    ghost_bgr: tuple[int, int, int] = (25, 25, 125),
) -> None:
    """Draw a synthetic 7-segment digit aligned to _decode_seven_segment zone ratios."""
    t = max(3, int(min(w, h) * 0.14))
    segs = [
        (x0 + int(w * 0.10), y0, x0 + int(w * 0.90), y0 + t),  # a
        (x0 + w - t, y0 + int(h * 0.08), x0 + w, y0 + int(h * 0.48)),  # b
        (x0 + w - t, y0 + int(h * 0.52), x0 + w, y0 + int(h * 0.92)),  # c
        (x0 + int(w * 0.10), y0 + h - t, x0 + int(w * 0.90), y0 + h),  # d
        (x0, y0 + int(h * 0.52), x0 + t, y0 + int(h * 0.92)),  # e
        (x0, y0 + int(h * 0.08), x0 + t, y0 + int(h * 0.48)),  # f
        (x0 + int(w * 0.10), y0 + int(h * 0.46), x0 + int(w * 0.90), y0 + int(h * 0.54)),  # g
    ]
    for on in (0, 1):
        for bit, rect in zip(bits, segs):
            if bit == on:
                color = lit_bgr if bit == 1 else ghost_bgr
                cv2.rectangle(canvas, (rect[0], rect[1]), (rect[2], rect[3]), color, thickness=-1)


def _render_led_string(text: str) -> np.ndarray:
    """Render a synthetic SWP-C40/D40 red LED readout with dim unlit ghost segments."""
    rev_table: dict[str, tuple[int, ...]] = {
        "0": (1, 1, 1, 1, 1, 1, 0),
        "1": (0, 1, 1, 0, 0, 0, 0),
        "2": (1, 1, 0, 1, 1, 0, 1),
        "3": (1, 1, 1, 1, 0, 0, 1),
        "4": (0, 1, 1, 0, 0, 1, 1),
        "5": (1, 0, 1, 1, 0, 1, 1),
        "6": (1, 0, 1, 1, 1, 1, 1),
        "7": (1, 1, 1, 0, 0, 0, 0),
        "8": (1, 1, 1, 1, 1, 1, 1),
        "9": (1, 1, 1, 1, 0, 1, 1),
    }
    canvas = np.full((90, 280, 3), (22, 22, 42), dtype=np.uint8)
    x_cur = 18
    y0, w, h = 20, 28, 50
    for ch in text:
        if ch == ".":
            cv2.rectangle(canvas, (x_cur, y0 + h - 5), (x_cur + 5, y0 + h), (20, 20, 250), thickness=-1)
            x_cur += 11
        elif ch == "-":
            cv2.rectangle(canvas, (x_cur, y0 + h // 2 - 2), (x_cur + 16, y0 + h // 2 + 2), (20, 20, 250), thickness=-1)
            x_cur += 22
        elif ch == "1":
            # Render narrow '1' so the narrow-one heuristic is exercised alongside wide digits.
            _draw_seven_segment_digit(canvas, x_cur, y0, w, h, (0, 0, 0, 0, 0, 0, 0))
            cv2.rectangle(canvas, (x_cur + w - 5, y0 + 2), (x_cur + w, y0 + h - 2), (20, 20, 250), thickness=-1)
            x_cur += w + 8
        else:
            _draw_seven_segment_digit(canvas, x_cur, y0, w, h, rev_table[ch])
            x_cur += w + 8
    return canvas


def test_stdio_clean_import_and_tools() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, r'" + str(SERVER_DIR) + "'); import ober_ocr_mcp"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == "", f"Expected zero stdout on import, got: {proc.stdout!r}"

    async def _list_names() -> set[str]:
        tools = await srv.mcp.list_tools()
        return {t.name for t in tools}

    tool_names = asyncio.run(_list_names())
    required = {
        "probe_desktop_environment",
        "capture_window_wgc",
        "cleanup_captures",
        "ocr_window_or_roi",
        "read_seven_segment_roi",
        "detect_ui_boxes",
        "find_text_targets",
        "watch_value_threshold",
        "watch_color_endpoint",
        "watch_region_until_stable",
        "save_preset",
        "load_preset",
        "list_presets",
        "resolve_preset_point",
        "bring_window_to_front",
        "fast_click",
        "drag_mouse",
        "scroll_mouse",
        "hover_and_ocr",
        "click_3d_valve_verified",
        "adjust_valve_popup",
        "fast_type",
        "run_action_sequence",
    }
    missing = required - tool_names
    assert not missing, f"Missing registered MCP tools: {sorted(missing)}"


def test_cmd_launcher_mcp_initialize() -> None:
    assert CMD_LAUNCHER.exists(), f"Missing launcher: {CMD_LAUNCHER}"
    init_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ober-test", "version": "0.5.0"},
            },
        }
    )
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", str(CMD_LAUNCHER)],
        input=init_msg + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"Expected JSON-RPC initialize output on stdout, stderr={proc.stderr!r}"
    parsed = json.loads(lines[0])
    assert parsed.get("id") == 1, f"Unexpected JSON-RPC id: {parsed}"
    server_info = parsed.get("result", {}).get("serverInfo", {})
    assert server_info.get("name") == "ober-ocr-mcp", f"Unexpected serverInfo: {parsed}"
    assert len(lines) == 1 or all(json.loads(ln) for ln in lines), f"Unexpected extra non-JSON stdout lines: {lines}"


def test_seven_segment_synthetic_with_ghost_segments() -> None:
    for expected_str, expected_val in [("38.68", 38.68), ("25.13", 25.13), ("102.1", 102.1), ("105.0", 105.0), ("-12.5", -12.5)]:
        img = _render_led_string(expected_str)
        res = srv._decode_seven_segment(img, color="red")
        assert res["raw_text"] == expected_str, f"Expected raw {expected_str}, got {res}"
        assert res["value"] == expected_val, f"Expected value {expected_val}, got {res}"
        assert res["confidence"] >= 0.9, f"Low confidence for {expected_str}: {res}"


def test_detect_ui_boxes_synthetic() -> None:
    # 1. Input boxes including wide 230px column
    tbl = np.full((240, 820, 3), (210, 175, 130), dtype=np.uint8)
    for ry in (50, 110):
        for bx, bw in ((40, 130), (200, 160), (390, 230)):
            cv2.rectangle(tbl, (bx, ry), (bx + bw - 1, ry + 21), (255, 255, 255), thickness=-1)
    det = srv._detect_ui_boxes_on_bgr(tbl, (100, 200), (3, 32), (1914, 1007), kind="input_boxes")
    assert det["row_count"] == 2, f"Expected 2 rows, got {det}"
    assert det["rows"][0]["cells"][2]["w"] == 230, f"Expected 230px wide cell, got {det['rows'][0]}"

    # 2. Radio circles: unchecked vs checked center dot
    quiz = np.full((200, 120, 3), (210, 175, 130), dtype=np.uint8)
    cv2.circle(quiz, (50, 50), 12, (255, 255, 255), thickness=-1)
    cv2.circle(quiz, (50, 130), 12, (255, 255, 255), thickness=-1)
    cv2.circle(quiz, (50, 130), 5, (40, 40, 40), thickness=-1)
    qdet = srv._detect_ui_boxes_on_bgr(quiz, (440, 200), (3, 32), (1914, 1007), kind="radio_circles")
    assert qdet["count"] == 2, f"Expected 2 radio circles, got {qdet}"
    assert qdet["items"][0]["checked"] is False, f"Expected first radio unchecked, got {qdet['items'][0]}"
    assert qdet["items"][1]["checked"] is True, f"Expected second radio checked, got {qdet['items'][1]}"

    # 3. Red close X button
    modal = np.full((120, 200, 3), (240, 240, 240), dtype=np.uint8)
    cv2.rectangle(modal, (150, 15), (175, 40), (20, 20, 220), thickness=-1)
    rdet = srv._detect_ui_boxes_on_bgr(modal, (1300, 180), (3, 32), (1914, 1007), kind="red_close")
    assert rdet["count"] == 1, f"Expected 1 red_close hit, got {rdet}"
    assert abs(rdet["items"][0]["screen_x"] - 1462) <= 2

    # 4. Horizontal and vertical scrollbar thumbs
    sb = np.full((300, 500, 3), (242, 242, 242), dtype=np.uint8)
    cv2.rectangle(sb, (80, 250), (220, 265), (205, 155, 105), thickness=-1)  # horizontal thumb
    cv2.rectangle(sb, (470, 40), (484, 150), (205, 155, 105), thickness=-1)  # vertical thumb
    sdet = srv._detect_ui_boxes_on_bgr(sb, (500, 450), (3, 32), (1914, 1007), kind="scrollbar_thumb")
    orientations = {it["orientation"] for it in sdet["items"]}
    assert orientations == {"horizontal", "vertical"}, f"Expected both scrollbar orientations, got {sdet}"


def test_color_endpoint_detector() -> None:
    yellow_flask = np.full((40, 40, 3), (240, 240, 240), dtype=np.uint8)
    yellow_flask[10:30, 10:30] = (15, 245, 245)  # BGR yellow
    assert srv._compute_color_ratio(yellow_flask, "yellow") >= 0.20
    assert srv._compute_color_ratio(yellow_flask, "blue") < 0.01

    blue_flask = np.full((40, 40, 3), (240, 240, 240), dtype=np.uint8)
    blue_flask[10:30, 10:30] = (230, 120, 20)
    assert srv._compute_color_ratio(blue_flask, "blue") >= 0.20

    red_flask = np.full((40, 40, 3), (240, 240, 240), dtype=np.uint8)
    red_flask[10:30, 10:30] = (40, 40, 230)
    assert srv._compute_color_ratio(red_flask, "pink") >= 0.20

    green_flask = np.full((40, 40, 3), (240, 240, 240), dtype=np.uint8)
    green_flask[10:30, 10:30] = (30, 220, 40)
    assert srv._compute_color_ratio(green_flask, "green") >= 0.20


def test_valve_step_controller_and_tooltip_parser() -> None:
    assert srv._compute_valve_step(59.0, 59.0)["action"] == "done"
    assert srv._compute_valve_step(20.0, 100.0) == {
        "action": "hold_endpoint",
        "direction": "open",
        "press_duration_sec": 2.5,
        "diff": 80.0,
    }
    assert srv._compute_valve_step(54.0, 59.0) == {
        "action": "discrete_clicks",
        "direction": "open",
        "count": 5,
        "press_duration_sec": 0.05,
        "diff": 5.0,
    }
    ramp = srv._compute_valve_step(20.0, 60.0)
    assert ramp["action"] == "ramp_hold" and ramp["direction"] == "open" and 0.4 < ramp["press_duration_sec"] < 0.8

    p = srv._parse_valve_tooltip_state
    assert p("VA718: 已开", [], "VA718") == "已开"
    assert p("VA718: 已关", [], "VA718") == "已关"
    assert p("空气旁路阀：已打开", [], "空气旁路阀") == "已开"
    assert p("空气旁路阀：已关闭", [], "空气旁路阀") == "已关"
    assert p("VA706: 已关闭", [], "VA718") is None

    assert srv._wheel_mouse_delta("up", False) == (srv.MOUSEEVENTF_WHEEL, 120)
    assert srv._wheel_mouse_delta("down", False) == (srv.MOUSEEVENTF_WHEEL, -120)
    assert srv._wheel_mouse_delta("right", True) == (srv.MOUSEEVENTF_HWHEEL, 120)
    assert srv._wheel_mouse_delta("left", True) == (srv.MOUSEEVENTF_HWHEEL, -120)


def test_extract_numbers_and_mmss() -> None:
    assert srv._extract_numbers("水流量计F4103.86") == [103.86]
    assert srv._extract_numbers("空气流量计F21.00") == [1.0]
    assert srv._extract_numbers("TI101 50.2") == [50.2]
    assert srv._extract_numbers("49. .6 %") == [49.6]

    m = srv._extract_mmss_minutes
    assert m("00:00.00") == 0.0
    assert abs(m("08:08.57") - 8.1428) < 1e-3
    assert abs(m("01:06:17") - 66.2833) < 1e-3
    assert m("no clock here") is None


def test_chem_calc_cli() -> None:
    def _run_calc(*args: str) -> dict:
        out = subprocess.check_output([sys.executable, str(CHEM_CALC_PY), *args], text=True, encoding="utf-8")
        return json.loads(out)

    fb = _run_calc("fluidized-bed", "--time-mmss", "08:08.57", "--w1", "37.50", "--w2", "35.50")
    assert fb == {"time_min": 8.14, "delta_w_g": 2.0, "dry_w_g": 3.5, "moisture_pct_1e2": 57.14}

    nh3 = _run_calc(
        "ammonia-absorption",
        "--vn-air", "5.83", "--vn-nh3", "0.15", "--l-lh", "30", "--dh-mm", "201",
        "--t1", "25", "--t2", "25", "--t3", "25", "--t4", "25",
        "--v-eud-ml", "500", "--v-sample-ml", "10", "--v-h2so4-abs-ml", "22.06",
    )
    assert nh3["m"] == 0.98 and abs(nh3["KYa_kmol_m3_h"] - 146.54) < 0.2 and abs(nh3["phi_pct"] - 90.89) < 0.05

    ac = _run_calc(
        "acetone-absorption",
        "--vn2", "1.00", "--l2-lh", "103.86",
        "--t1", "25", "--t2", "25", "--t3", "25", "--t4", "25",
        "--y1", "0.1064", "--y2", "0.0004",
    )
    assert ac["m"] == 1.98 and abs(ac["Vp_m3"] - 0.001374) < 1e-5
    assert abs(ac["KYa_mol_m3_s"] - 51.60) < 0.05 and ac["phi_pct"] == 99.66


def test_json_configs_valid() -> None:
    for rel in (
        "plugin.json",
        "mcp_config.json",
        "clients/antigravity-with-windows-mcp.json",
        "clients/deepseek-harness-mcp.json",
        "clients/doubao-mcp-stdio.json",
        "clients/claude-cursor-mcp.json",
        "presets/rectification-t701-sop.json",
        "presets/fluidized-bed-drying-sop.json",
        "presets/absorption-desorption-1547-sop.json",
    ):
        path = PLUGIN_ROOT / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        if rel == "plugin.json":
            assert data.get("version") == "0.5.0", f"Expected plugin version 0.5.0, got {data.get('version')}"
        if rel.startswith("presets/"):
            assert data.get("calibrated") is True, f"Preset {rel} must be calibrated"


if __name__ == "__main__":
    tests = [
        test_stdio_clean_import_and_tools,
        test_cmd_launcher_mcp_initialize,
        test_seven_segment_synthetic_with_ghost_segments,
        test_detect_ui_boxes_synthetic,
        test_color_endpoint_detector,
        test_valve_step_controller_and_tooltip_parser,
        test_extract_numbers_and_mmss,
        test_chem_calc_cli,
        test_json_configs_valid,
    ]
    for fn in tests:
        fn()
        print(f"PASS: {fn.__name__}")
    print("ALL TESTS PASSED")
