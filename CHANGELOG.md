# Changelog

## v0.7.0 (2026-11-24) — CLI-Anything Agent-Native CLI, Slider Direct-Jump & 1522 Normal Startup (`92.04`)
- **融合 HKUDS/CLI-Anything 范式，新增 Agent-Native CLI (`server/ober_cli.py`)**：
  - 在 `server/pyproject.toml` 注册 `ober-cli` 与 `cli-anything-ober` 双入口，支持 **一键子命令（`--json` 结构化输出）+ 交互式 Stateful REPL** 双模式，让 Antigravity / Claude Code / Codex / OpenClaw 可在无 MCP 或单次 shell 调用中完成批量操作，显著减少截图与往返轮次；
  - 覆盖 `probe`（自动识别架构 A/B1/B2/Hybrid）、`focus`、`ocr`、`led`、`boxes`、`valve set/toggle`、`batch`、`preset list/show/resolve`、`calc`（直通 `chem_calc.py`）与带排他文件锁的 `session` 持久化（遵循 CLI-Anything `session-locking` 规范）。
- **调节阀弹窗滑条首跳直达（Slider Direct-Jump）**：
  - `adjust_valve_popup` 新增 `use_slider_jump`、`slider_zero_xy`、`slider_full_xy` 与 `_compute_slider_click_xy`，针对 1522 等高速调节阀（`0%→100% < 0.7s`），直接线性插值点击底部 0–100% 滑条（实测误差 `<= 0.25%`：`55.66%` / `50.00%` / `30.20%` / `61.79%`），彻底根治手轮长按震荡；
  - `run_action_sequence` 兼容 `op`/`type` 及 `wait` 的 `seconds`/`sec` 别名，并透传滑条参数。
- **1522 正常开车实机 `92.04` 通关与 SOP 补全**：
  - 完成 1522 序号 2 `正常开车`（`92.04` 分，`00:40:31`，P04 数据处理 `25.00` 满分、P05 实验分析 `15.00` 满分，总计 27 项次实机通关）；
  - 更新 `distillation-1522-sop.md` 与 `presets/distillation-1522-sop.json`（新增 `valve_popup_slider_100`，修正 `chart_close_x` 为 `(31, 60)`，补充 `查看图表` 双击 `保存结果 -> 保存` 机制）。

## v0.6.0 (2026-11-23) — OBER 1522 Distillation Suite, Adaptive Control & Real Foreground/WGC Resilience
- **1522《精馏综合拓展 3D》全套实机经验沉淀**：完成项目 3–16（共 14 项：`100 × 6`、`94.44`、`91.41`、`94.35`、`99.91`、`99.67`、`96.15`）端到端验证，新增 [distillation-1522-sop.md](skills/ober-lab-assistant/references/distillation-1522-sop.md) 与标定预设 `presets/distillation-1522-sop.json`（修正调节阀手轮默认坐标为 `ry=0.47964 / y=515` 并新增 `chart_save`、`chart_close_x`、`quiz_confirm` 等点位）。
- **多级窗口激活与后台 WGC 自动回退**：升级 `_bring_hwnd_to_front`（标准前置 → `AttachThreadInput` → `Topmost` 翻转 → `Alt+Ctrl`），让 `bring_window_to_front` 返回真实 `is_foreground` 状态；当 `backend="auto"` 且目标窗口未处前台时优先走 WGC，避免 MSS 采集遮挡窗口。
- **短格式 `VA29:开/关` 校验与自适应调节阀闭环**：
  - `_normalize_valve_state` / `_parse_valve_tooltip_state` / `click_3d_valve_verified` 原生支持 `开`/`关`/`开启` 短提示，并将跨位号隔离扩展至 `VA02` 与 `VA29` 等 2 位数字位号；`click_3d_valve_verified` 新增 `press_duration_sec`。
  - `_compute_valve_step` 与 `adjust_valve_popup` 新增**实测速率在线估算 + 过冲阻尼防震荡 + 连续 3 次零变化卡死早停诊断**。
- **盯表、检测与批处理增强**：`watch_value_threshold` 新增 `valid_min`/`valid_max` 异常 OCR 跳变过滤；`detect_ui_boxes` 支持 `radio_boxes`/`close_x`/`scrollbar` 别名；`run_action_sequence` 新增 `default_window_query` 及 `move`/`wait`/`adjust_valve`/`detect_boxes`/`capture` 原语。
- **精馏三物系工程计算 CLI**：`chem_calc.py` 新增 `distillation-1522` 子命令（覆盖 `ethanol-propanol` + McCabe-Thiele 级数计算、`ethanol-water` 与 `benzene-toluene`）。

## v0.5.0 (2026-11-22) — Multi-Harness Standalone & Full Absorption-Desorption Suite
- **独立可移植架构**：新增 `scroll_mouse` MCP 工具以及 `run_action_sequence` 中的 `scroll` / `click_valve` 动作，去除对 `windows-mcp.exe` 的强制依赖；新增 `scripts/run-ober-ocr-mcp.cmd` 便携启动脚本与 `scripts/install.ps1` 一键安装及多客户端配置生成器（支持 豆包工作 / DeepSeek-Harness / Antigravity / Claude Code）。
- **视觉与闭环控制增强**：
  - 修复 `_extract_numbers` 中 `F4103.86` / `F21.00` 等位号与读数黏连导致解析失败的问题；
  - 新增 `adjust_valve_popup` 闭环调节阀弹窗工具（内置标题栏中性悬停防提示条遮挡机制）；
  - 新增 `watch_color_endpoint` 指示剂色变终点监测工具（支持 `yellow` / `red` / `pink` / `blue` / `green`）；
  - 升级 `detect_ui_boxes`：`radio_circles` 返回 `checked` 勾选状态，新增 `scrollbar_thumb` 滚动滑块定位，放宽 `input_boxes` 宽度上限至 `max(260, 0.85*w)`。
- **化工工程计算与全系列 SOP 扩展**：
  - 新增离线计算 CLI `skills/ober-lab-assistant/scripts/chem_calc.py`（支持流化床干燥、填料塔流体力学、CO₂ 吸收解吸、氨吸收 23 项、丙酮吸收 21 项）；
  - 新增《吸收解吸综合 3D 实验【1547】》完整 SOP 与 `absorption-desorption-1547-sop.json` 实机标定预设；
  - 将 `rules/AGENTS.md` 与 `SKILL.md` 升级为三架构（架构 A / B1 / B2）规范，并将测试套件重构为 100% 不依赖本地截图的自包含验证测试。

## v0.3.0 (2026-11-21) — Fluidized-Bed Drying & Seven-Segment Decoder
- 新增 `read_seven_segment_roi`（红/绿/琥珀/白七段数码管原生解码）、`detect_ui_boxes`（输入框/单选圆圈/红叉）、`watch_value_threshold(led_color=..., parse_mode="mmss_minutes")`、`cleanup_captures`；
- 完成架构 B1（`流化床干燥 9310`，`95.238` 分）端到端实机验证。

## v0.2.0 — Rectification Training Unit & Dual Architecture
- 初版 WGC/MSS + RapidOCR + `dhash` 稳定守望与 `click_3d_valve_verified`，完成架构 A（`精馏实训 T701` 开车/停车/全回流/R=3）端到端实机验证。
