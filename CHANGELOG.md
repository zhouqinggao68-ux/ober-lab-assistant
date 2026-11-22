# Changelog

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
