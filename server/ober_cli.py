"""Agent-Native CLI for OBER Lab Assistant (CLI-Anything compatible).

Provides both one-shot structured subcommands (`--json`) and a stateful REPL
so AI agents (Antigravity, Codex, Claude Code, OpenClaw) can operate OBER
chemical simulation labs with minimal screenshot/MCP round-trips.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import ober_ocr_mcp as engine

VERSION = "0.7.0"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CHEM_CALC_SCRIPT = PLUGIN_ROOT / "skills" / "ober-lab-assistant" / "scripts" / "chem_calc.py"
DEFAULT_SESSION_FILE = Path(
    os.environ.get("OBER_CLI_SESSION", str(Path.home() / ".ober-lab-assistant" / "session.json"))
)


def _locked_save_json(path: Path, data: dict[str, Any]) -> None:
    """Save JSON with an exclusive file lock (CLI-Anything session-locking pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    with path.open("r+", encoding="utf-8") as fh:
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
        except OSError:
            locked = False
        try:
            fh.seek(0)
            fh.truncate()
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
        finally:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def load_session(path: Path = DEFAULT_SESSION_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"window_query": "", "preset_id": "", "history": [], "updated_at": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("window_query", "")
            raw.setdefault("preset_id", "")
            raw.setdefault("history", [])
            return raw
    except Exception:
        pass
    return {"window_query": "", "preset_id": "", "history": [], "updated_at": None}


def save_session(state: dict[str, Any], path: Path = DEFAULT_SESSION_FILE) -> dict[str, Any]:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(state.get("history"), list) and len(state["history"]) > 100:
        state["history"] = state["history"][-100:]
    _locked_save_json(path, state)
    return state


def parse_roi(spec: str | None) -> dict[str, float | int] | None:
    """Parse 'rx,ry,rw,rh' (<=1.0 floats) or 'x,y,w,h' (pixel ints) into ROI dict."""
    if not spec:
        return None
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError(f"ROI 需要 4 个逗号分隔数值 (rx,ry,rw,rh 或 x,y,w,h)，收到: {spec!r}")
    vals = [float(p) for p in parts]
    if all(0.0 <= v <= 1.0 for v in vals):
        return {"rx": vals[0], "ry": vals[1], "rw": vals[2], "rh": vals[3]}
    return {"x": int(vals[0]), "y": int(vals[1]), "w": int(vals[2]), "h": int(vals[3])}


def parse_probe_points(spec: str) -> list[list[int]]:
    """Parse 'x1,y1;x2,y2;...' into [[x1, y1], [x2, y2], ...]."""
    points: list[list[int]] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        xy = [p.strip() for p in chunk.split(",")]
        if len(xy) != 2:
            raise ValueError(f"探针坐标格式应为 x,y;x2,y2，收到: {chunk!r}")
        points.append([int(xy[0]), int(xy[1])])
    if not points:
        raise ValueError("至少需要一个探针坐标点")
    return points


def _detect_architecture(windows: list[dict[str, Any]]) -> str:
    procs = {str(w.get("process_name", "")).lower() for w in windows}
    if "gus.exe" in procs and "modelmanage.exe" in procs:
        return "A (3D + 仿DCS/GUS + ModelManage)"
    if "scorerun.exe" in procs:
        return "B1 (纯3D + 独立scorerun)"
    if "unityvr.exe" in procs and "modelmanage.exe" in procs:
        return "Hybrid (UnityVR + ModelManage)"
    if "unityvr.exe" in procs:
        return "B2 (单进程纯3D + 内嵌查看评分)"
    return "None (未检测到运行中的欧倍尔仿真窗口)"


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def _resolve_wq(arg_wq: str | None, session: dict[str, Any]) -> str:
    return (arg_wq or session.get("window_query") or "").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ober-cli",
        description="CLI-Anything compatible Agent-Native CLI for OBER Chemical Simulation Labs.",
    )
    parser.add_argument("--version", action="version", version=f"ober-cli {VERSION}")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出结构化 JSON（Agent 推荐）")
    parser.add_argument("--window", "-w", default="", help="目标窗口标题关键字（缺省使用会话默认窗口）")
    parser.add_argument("--session-file", default=str(DEFAULT_SESSION_FILE), help="会话状态 JSON 路径")

    sub = parser.add_subparsers(dest="command")

    # probe
    p_probe = sub.add_parser("probe", help="探测桌面环境、窗口与欧倍尔软件架构 (A/B1/B2/Hybrid)")
    p_probe.add_argument("--filter", default="", help="按窗口标题过滤")
    p_probe.add_argument("--all", dest="include_minimized", action="store_true", help="包含最小化窗口")

    # focus
    p_focus = sub.add_parser("focus", help="多级前台唤醒目标窗口并校验 is_foreground")
    p_focus.add_argument("query", nargs="?", default="", help="窗口关键字")

    # ocr
    p_ocr = sub.add_parser("ocr", help="对窗口或 ROI 执行 RapidOCR 识别")
    p_ocr.add_argument("--roi", default="", help="rx,ry,rw,rh 或 x,y,w,h")
    p_ocr.add_argument("--numeric", action="store_true", help="仅提取数值")
    p_ocr.add_argument("--scale", type=float, default=2.0)

    # led
    p_led = sub.add_parser("led", help="解码七段数码管（如 SWP-C40/D40 红字表头）")
    p_led.add_argument("--roi", required=True, help="rx,ry,rw,rh 或 x,y,w,h")
    p_led.add_argument("--color", default="red", choices=["red", "green", "amber", "any"])

    # boxes
    p_boxes = sub.add_parser("boxes", help="定位输入框 / 单选框 / 红叉 / 滚动条滑块")
    p_boxes.add_argument("--kind", default="input_boxes")
    p_boxes.add_argument("--roi", default="", help="rx,ry,rw,rh 或 x,y,w,h")
    p_boxes.add_argument("--min-brightness", type=int, default=228)

    # valve
    p_valve = sub.add_parser("valve", help="调节阀闭环调参或 3D 球阀悬停校验切换")
    valve_sub = p_valve.add_subparsers(dest="valve_cmd", required=True)

    pv_set = valve_sub.add_parser("set", help="调节已打开的调节阀弹窗到目标开度 (%)")
    pv_set.add_argument("target_pct", type=float)
    pv_set.add_argument("--tolerance", type=float, default=0.6)
    pv_set.add_argument("--slider", dest="use_slider_jump", action="store_true", help="启用滑条首跳直达 (1522 推荐)")
    pv_set.add_argument("--close", dest="close_when_done", action="store_true", help="完成后关闭弹窗")
    pv_set.add_argument("--max-iter", type=int, default=12)

    pv_tog = valve_sub.add_parser("toggle", help="悬停校验切换 3D 球阀/开关 (兼容 VA29:开/关)")
    pv_tog.add_argument("tag", help="位号，如 VA02 / VA29")
    pv_tog.add_argument("state", choices=["open", "close", "已开", "已关"])
    pv_tog.add_argument("--probes", required=True, help="探针点列表，格式 x1,y1;x2,y2")

    # batch
    p_batch = sub.add_parser("batch", help="一次调用串行执行 JSON 动作序列 (run_action_sequence)")
    p_batch.add_argument("spec", help="JSON 字符串或 .json 文件路径")
    p_batch.add_argument("--continue-on-error", action="store_true")

    # preset
    p_preset = sub.add_parser("preset", help="管理与换算实验预置坐标模板")
    preset_sub = p_preset.add_subparsers(dest="preset_cmd", required=True)
    preset_sub.add_parser("list", help="列出所有可用预设")
    pp_show = preset_sub.add_parser("show", help="查看指定预设内容")
    pp_show.add_argument("preset_id")
    pp_res = preset_sub.add_parser("resolve", help="换算预设归一化坐标到当前窗口物理像素")
    pp_res.add_argument("preset_id")
    pp_res.add_argument("point_name")

    # calc
    p_calc = sub.add_parser("calc", help="调用 chem_calc.py 执行化工实验数据处理计算")
    p_calc.add_argument("calc_args", nargs=argparse.REMAINDER, help="透传给 chem_calc.py 的参数")

    # session
    p_sess = sub.add_parser("session", help="管理持久化会话状态（默认窗口、预设、历史）")
    sess_sub = p_sess.add_subparsers(dest="session_cmd", required=True)
    sess_sub.add_parser("status", help="查看当前会话状态")
    ps_use = sess_sub.add_parser("use", help="设置会话默认窗口关键字")
    ps_use.add_argument("query")
    ps_pre = sess_sub.add_parser("set-preset", help="设置会话默认预设 ID")
    ps_pre.add_argument("preset_id")
    sess_sub.add_parser("reset", help="清空会话状态")

    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    session_path = Path(args.session_file)
    session = load_session(session_path)
    cmd = args.command

    if cmd == "probe":
        res = engine.probe_desktop_environment(
            include_minimized=bool(args.include_minimized),
            filter_text=str(args.filter),
        )
        res["detected_architecture"] = _detect_architecture(res.get("windows", []))
        return res

    if cmd == "focus":
        wq = _resolve_wq(args.query or args.window, session)
        if not wq:
            raise ValueError("请指定窗口关键字或先运行 `ober-cli session use <window>`")
        return engine.bring_window_to_front(window_query=wq)

    if cmd == "ocr":
        wq = _resolve_wq(args.window, session)
        return engine.ocr_window_or_roi(
            window_query=wq,
            roi=parse_roi(args.roi),
            scale=float(args.scale),
            numeric_only=bool(args.numeric),
        )

    if cmd == "led":
        wq = _resolve_wq(args.window, session)
        return engine.read_seven_segment_roi(
            window_query=wq,
            roi=parse_roi(args.roi),
            color=str(args.color),
        )

    if cmd == "boxes":
        wq = _resolve_wq(args.window, session)
        return engine.detect_ui_boxes(
            window_query=wq,
            roi=parse_roi(args.roi),
            kind=str(args.kind),
            min_brightness=int(args.min_brightness),
        )

    if cmd == "valve":
        wq = _resolve_wq(args.window, session)
        if not wq:
            raise ValueError("valve 子命令需要 --window 或会话默认窗口")
        if args.valve_cmd == "set":
            return engine.adjust_valve_popup(
                window_query=wq,
                target_pct=float(args.target_pct),
                tolerance=float(args.tolerance),
                max_iterations=int(args.max_iter),
                close_when_done=bool(args.close_when_done),
                use_slider_jump=bool(args.use_slider_jump),
            )
        if args.valve_cmd == "toggle":
            return engine.click_3d_valve_verified(
                window_query=wq,
                target_tag=str(args.tag),
                desired_state=str(args.state),
                probe_points=parse_probe_points(args.probes),
            )

    if cmd == "batch":
        wq = _resolve_wq(args.window, session)
        spec_path = Path(args.spec)
        raw_text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else args.spec
        actions = json.loads(raw_text)
        if not isinstance(actions, list):
            raise ValueError("batch 输入必须是 JSON 动作数组")
        return engine.run_action_sequence(
            actions=actions,
            stop_on_error=not bool(args.continue_on_error),
            default_window_query=wq,
        )

    if cmd == "preset":
        if args.preset_cmd == "list":
            return engine.list_presets()
        if args.preset_cmd == "show":
            return engine.load_preset(args.preset_id)
        if args.preset_cmd == "resolve":
            wq = _resolve_wq(args.window, session)
            return engine.resolve_preset_point(
                preset_id=str(args.preset_id),
                point_name=str(args.point_name),
                window_query=wq,
            )

    if cmd == "calc":
        calc_args = list(args.calc_args or [])
        if calc_args and calc_args[0] == "--":
            calc_args = calc_args[1:]
        proc = subprocess.run(
            [sys.executable, str(CHEM_CALC_SCRIPT), *calc_args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"chem_calc 退出码 {proc.returncode}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"output": proc.stdout.strip()}

    if cmd == "session":
        if args.session_cmd == "status":
            return {"session_file": str(session_path), **session}
        if args.session_cmd == "use":
            session["window_query"] = str(args.query)
            session["history"].append({"action": "use", "window_query": str(args.query)})
            return save_session(session, session_path)
        if args.session_cmd == "set-preset":
            session["preset_id"] = str(args.preset_id)
            session["history"].append({"action": "set-preset", "preset_id": str(args.preset_id)})
            return save_session(session, session_path)
        if args.session_cmd == "reset":
            fresh = {"window_query": "", "preset_id": "", "history": []}
            return save_session(fresh, session_path)

    raise ValueError(f"未知命令: {cmd}")


def run_repl(session_file: str, as_json: bool = False) -> int:
    """Interactive stateful REPL (CLI-Anything dual-mode pattern)."""
    parser = build_parser()
    print(f"ober-cli v{VERSION} (CLI-Anything compatible REPL) — 输入 'help' 查看命令，'exit' 退出")
    while True:
        sess = load_session(Path(session_file))
        tag = sess.get("window_query") or "no-window"
        try:
            line = input(f"ober[{tag}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"exit", "quit", "q"}:
            return 0
        if line == "help":
            parser.print_help()
            continue
        try:
            tokens = shlex.split(line)
            if "--session-file" not in tokens:
                tokens = ["--session-file", session_file, *tokens]
            if as_json and "--json" not in tokens:
                tokens = ["--json", *tokens]
            ns = parser.parse_args(tokens)
            if not ns.command:
                continue
            out = dispatch(ns)
            _emit(out, bool(ns.as_json or as_json))
        except SystemExit:
            continue
        except Exception as exc:
            _emit({"ok": False, "error": str(exc)}, as_json)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        return run_repl(args.session_file, as_json=bool(args.as_json))
    try:
        result = dispatch(args)
        _emit(result, bool(args.as_json))
        return 0
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, bool(args.as_json))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
