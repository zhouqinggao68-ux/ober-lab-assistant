# ober-lab-assistant（欧倍尔化工虚拟仿真实验 AI 助手 v0.7.0）

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg)](server/pyproject.toml)
[![MCP](https://img.shields.io/badge/Protocol-MCP%20%2B%20CLI--Anything-green.svg)](mcp_config.json)

面向 **欧倍尔（OBR）化工虚拟仿真实验** 的跨平台 AI Agent 插件。通过本地 **WGC / MSS 双通道防遮挡视觉感知 + RapidOCR + 原生七段数码管解码 + 滑条首跳/自适应闭环阀门控制 + CLI-Anything 兼容 Agent-Native CLI（`ober-cli` / `cli-anything-ober`，支持 `--json` 与 Stateful REPL）+ 多实验化工物性计算 CLI + 实机校准 SOP**，可在 **Antigravity（AGY）**、**Claude Code / Codex / Cursor**、**豆包工作（Doubao Work）**、**DeepSeek-Harness（DSH）** 等工作流中通过 MCP 或 CLI 开箱即用。

> **合规说明**：本工具仅用于在用户本人合法登录的 Windows 本地练习/实训窗口中辅助视觉读表、坐标标定、工程计算与标准 SOP 执行；不包含任何内存注入、反编译或绕过账号授权/考试监管之机制。

---

## 实机端到端验证成绩

已在四大欧倍尔典型仿真架构下完成 **27 项次完整实验** 实机端到端验证，全部达成 **成绩 ≥ 90 且累计/单次用时 < 2 小时**：

| 仿真项目与软件架构 | 覆盖子实验 | 实测平台成绩（百分制） | 关键技术验证点 |
| :--- | :--- | :---: | :--- |
| **精馏实训装置 3D**<br>（架构 A：`UnityVR` + `仿DCS` + `ModelManage`） | 开车 / 停车 / 全回流 / `R=3` | 全项通过（`>= 90`） | 热波多板同步盯表、`VA` 手动球阀 90° 旋转悬停闭环、悬浮条停靠 |
| **流化床干燥实验 3D【9310】**<br>（架构 B1：`UnityVR` + 独立 `scorerun.exe`） | 干燥速率曲线测定 | **`95.238`** | `SWP-C40/D40` 红字七段数码管原生解码、6.7x 倍速计时盯表、12×7 四象限填表 |
| **吸收解吸综合 3D【1547】**<br>（架构 B2：单进程 `UnityVR` + 内嵌查看评分） | 干塔 / 湿塔 / 传质（指导）<br>填料塔流体力学 / CO₂ / 氨 / 丙酮 | **`100 / 100 / 98.6`**<br>**`97.53 / 98.18 / 97.02 / 94.8`** | 旁路放空调节阀标题栏中性防遮挡闭环、`三通阀1` 变黄终点盯表、`VA13` 色谱 HUD、`chem_calc.py` 23/21 项全自动核算 |
| **精馏综合拓展 3D【1522】**<br>（混合：`ModelManage`/纯 B2 + 内嵌评分） | 2–16 项实测通过（`15/17 已通过`）：正常开车 / 正常停车 / 4 类异常 / 3 类变参 / 常压 / 减压 / 加压 / 塔板数 / 乙醇-水 | **`92.04`、`100 × 6`、`94.44`**<br>**`91.41 / 94.35 / 99.91 / 99.67 / 96.15`** | 滑条首跳直达 (`use_slider_jump=True`)、短格式 `VA29:开/关` 校验、`y=515` 手轮防误触、`VA25->100%` 速冷、`记录数据`+`查看图表` 双提交、`distillation-1522` 三物系核算 |

---

## 核心能力升级（v0.6.0 → v0.7.0）

- **CLI-Anything 兼容 Agent-Native CLI（`ober-cli` / `cli-anything-ober`）**：
  - 遵循 [HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything) 规范，新增 `server/ober_cli.py`，支持 `--json` 结构化子命令与无参交互式 Stateful REPL（带排他文件锁会话持久化），涵盖 `probe` / `focus` / `ocr` / `led` / `boxes` / `valve set|toggle` / `batch` / `preset` / `calc` / `session`，让 Codex / Claude Code / Antigravity 在无 MCP 或批处理场景下直接通过命令行操控，显著降低截图轮次。
- **调节阀弹窗滑条首跳直达（Slider Direct-Jump）**：
  - `adjust_valve_popup` 新增 `use_slider_jump=True` 及 `slider_zero_xy` / `slider_full_xy`（1522 默认 `rx=0.44514..0.55486, ry=0.58491`），一发线性插值点击滑条误差 `<= 0.25%`，彻底避免高速阀门在 `0% <-> 100%` 间反复震荡；`run_action_sequence` 兼容 `op`/`type` 与 `wait` 的 `seconds`/`sec`。

- **真实前台唤醒与后台自动 WGC 防遮挡**：`bring_window_to_front` 新增 `AttachThreadInput`、`HWND_TOPMOST/NOTOPMOST` 翻转、`Alt+Ctrl` 多级解锁并返回真实 `is_foreground`；`_capture_target(backend="auto")` 在目标窗口未获前台时优先走 WGC，彻底消除 MSS 拍到遮挡窗口的现象。
- **自适应闭环调节阀与短格式 `VA29:开/关` 支持**：
  - `adjust_valve_popup` 修正 `1522` 默认可点击手轮坐标（`ry=0.47964 / y=515`，避开 `y=565` 纯文字），并增加**开关速率在线估算、过冲减半阻尼与连续 3 次卡死早停诊断**；
  - `click_3d_valve_verified` 与 `_parse_valve_tooltip_state` 原生支持 `VA29:开`/`VA29:关` 短格式及 2 位/3 位跨位号隔离，新增 `press_duration_sec` 参数。
- **盯表、检测与批处理增强**：
  - `watch_value_threshold` 新增 `valid_min`/`valid_max` 异常识别滤除，防止单帧 OCR 跳变触发误判或清零计数；
  - `detect_ui_boxes` 支持 `radio_boxes`、`close_x`、`scrollbar` 等常用别名；
  - `run_action_sequence` 支持 `default_window_query` 全局默认窗口、以及 `move` / `wait` / `adjust_valve` / `detect_boxes` / `capture` 新原语。
- **精馏综合拓展【1522】完整计算器与 SOP**：`chem_calc.py` 新增 `distillation-1522` 子命令（覆盖 `ethanol-propanol` + McCabe-Thiele、`ethanol-water`、`benzene-toluene`）；新增 `presets/distillation-1522-sop.json` 与 [distillation-1522-sop.md](skills/ober-lab-assistant/references/distillation-1522-sop.md)。

---

## 三分钟快速安装（Windows）

### 前置条件
- **Windows 10 / 11**（需在与欧倍尔仿真软件同一物理桌面会话中运行）
- 已安装 **Git** 与 **[uv](https://docs.astral.sh/uv/)**（Python 包管理器，会自动下载 Python 3.12+ 与依赖；若无 `uv`，在 PowerShell 运行：
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  ）

### 一键部署与自检
```powershell
git clone https://github.com/zhouqinggao68-ux/ober-lab-assistant.git
cd ober-lab-assistant
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```
> 目录建议：请将仓库克隆到常规本机路径（如 `D:\ober-lab-assistant`），避免放在带沙箱/`Low Mandatory Level` 限制的目录下导致 OpenCV/`_ctypes` DLL 加载失败。

脚本会自动完成：
1. 在 `server\.venv` 中同步 `fastmcp`、`mss`、`windows-capture`、`rapidocr-onnxruntime`、`opencv-python` 等全部依赖；
2. 运行自包含测试集 `server\test_ober_ocr_mcp.py`（含 `cmd.exe` STDIO MCP 握手测试与公式校验）；
3. 在本机 `generated-configs\` 目录生成**已填好你本机绝对路径**的各类客户端配置：
   - `generated-configs\doubao-stdio-setup.txt` → 给 **豆包工作（Doubao Work）**
   - `generated-configs\deepseek-harness-mcp.json` → 给 **DeepSeek-Harness（DSH）**
   - `generated-configs\antigravity-mcp_config.json` → 给 **Antigravity**
   - `generated-configs\claude-cursor-mcp.json` → 给 **Claude Code / Cursor / Codex**

---

## 不同 AI 工作流 / 深度学习容器接入方式

### 1. 豆包工作（Doubao Work PC 客户端）
详见图文步骤手册：[docs/install-doubao.md](docs/install-doubao.md)。核心两步：
1. **上传技能**：在豆包「技能·连接器·伙伴 → 我的技能 → 新建 - 上传技能」导入本仓库下的 `skills\ober-lab-assistant` 文件夹；
2. **添加 MCP 连接器**：在「插件 → `+ 添加`」选择 **STDIO 类型**，复制 `generated-configs\doubao-stdio-setup.txt` 中的 `命令` 与 `参数` 保存即可。

### 2. DeepSeek-Harness（DSH）
详见：[docs/install-deepseek-harness.md](docs/install-deepseek-harness.md)。
可直接将 `generated-configs\deepseek-harness-mcp.json` 填入 `dsh-skill-mcp-manager` 或 DSH 工作区 MCP 配置，并加载 `skills\ober-lab-assistant` 与 `rules\AGENTS.md`。

### 3. Antigravity（AGY）一键安装为原生插件
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Target antigravity -InstallAntigravity
```
安装完成后在 Antigravity 中重启/重载插件即可生效（可选配合 `clients\antigravity-with-windows-mcp.json` 同时启用 `ober-windows-mcp`）。

### 4. Linux / WSL2 / Docker 深度学习 Agent 容器如何操控 Windows 桌面实验？
**重要架构提示**：欧倍尔虚拟仿真软件是 Windows DirectX / Unity 桌面程序，屏幕采集 (`WGC/MSS`) 与键鼠驱动 (`SendInput`) **必须在 Windows 宿主机桌面运行**；如果你将 DeepSeek-Harness、开源 Agent 或深度学习/强化学习调度容器运行在 Docker / WSL2 中，请在 Windows 宿主机桌面执行：

```powershell
# 1. 仅限本机访问（推荐 WSL2 / 本机客户端）
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests

# 2. 若 Docker 容器通过 host.docker.internal 访问 Windows 宿主机，可按需放开监听（请确保处于受信内网并配置防火墙）
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests -HttpHost 0.0.0.0 -HttpPort 8011
```
随后在容器内将 Agent MCP URL 指向 `http://127.0.0.1:8011/mcp` 或 `http://host.docker.internal:8011/mcp`（Streamable HTTP 协议）。
*注：若 Antigravity 等宿主工具触发了 `exebox-*` 虚拟桌面隔离导致 `probe_desktop_environment` 看不到主桌面窗口，同样使用 `-StartHttp` 在用户主桌面终端启动即可。*

---

## `ober-ocr-mcp` 工具清单（23 个工具）

| 分类 | 工具名 | 核心用途 |
| :--- | :--- | :--- |
| **桌面/窗口** | `probe_desktop_environment` | 检测桌面名称（排查 `exebox-*`）、显示器分辨率与可见/最小化窗口句柄 |
| | `bring_window_to_front` | 按关键字/进程名/HWND 激活并还原最小化窗口 |
| | `capture_window_wgc` / `cleanup_captures` | MSS 优先 + WGC 兜底防黑屏截屏与冗余截图清理 |
| **视觉/读表** | `ocr_window_or_roi` / `find_text_targets` | 区域或整窗 RapidOCR 文字与像素坐标定位（自动清洗 `F4103.86` 等读数） |
| | `read_seven_segment_roi` | `SWP-C40`/`SWP-D40` 红/绿/琥珀/白七段数码管原生解码 |
| | `detect_ui_boxes` | 检出表格输入框 (`input_boxes`)、带已选状态的单/多选框 (`radio_circles` + `checked`)、红叉 (`red_close`)、滚动滑块 (`scrollbar_thumb`) |
| **盯表守望** | `watch_value_threshold` | 140s 安全限内轮询 DCS 仪表、红字 LED 或 `MM:SS` 计时器（支持 `companion_rois`） |
| | `watch_color_endpoint` | 监测吸收瓶/滴定瓶指示剂变色终点（`yellow`/`red`/`pink`/`blue`/`green`） |
| | `watch_region_until_stable` | 基于 `dhash` 汉明距离等待 3D 镜头或界面动画静止 |
| **桌面交互** | `fast_click` / `drag_mouse` / `scroll_mouse` / `fast_type` | DPI 感知点击/长按、拖拽旋转/拖滚动条、垂直/水平滚轮、抗中文输入法键盘录入 |
| | `hover_and_ocr` / `click_3d_valve_verified` | 3D 球阀/开关悬停读提示 + 幂等切换前后双核 |
| | `adjust_valve_popup` | 闭环调节 0–100% 阀门弹窗（自动标题栏避让悬浮提示遮挡） |
| | `run_action_sequence` | 单次 MCP 调用串行执行多步点击/输入/拖拽/滚轮/读表/休眠，降低大模型往返耗时 |
| **预设校准** | `save_preset` / `load_preset` / `list_presets` / `resolve_preset_point` | 归一化 `(rx, ry)` 坐标预设读写与分辨率自适应换算 |

---

## 离线工程计算 CLI（`chem_calc.py`）示例

```powershell
# 丙酮吸收实验（21 项数据一键计算）
.\server\.venv\Scripts\python.exe .\skills\ober-lab-assistant\scripts\chem_calc.py acetone-absorption `
  --vn2 1.00 --l2-lh 103.86 --t1 25 --t2 25 --t3 25 --t4 25 --y1 0.1064 --y2 0.0004

# 氨吸收实验（23 项数据一键计算）
.\server\.venv\Scripts\python.exe .\skills\ober-lab-assistant\scripts\chem_calc.py ammonia-absorption `
  --vn-air 5.83 --vn-nh3 0.15 --l-lh 30 --dh-mm 201 --t1 25 --t2 25 --t3 25 --t4 25 `
  --v-eud-ml 500 --v-sample-ml 10 --v-h2so4-abs-ml 22.06

# 流化床干燥（含水率与干基计算）
.\server\.venv\Scripts\python.exe .\skills\ober-lab-assistant\scripts\chem_calc.py fluidized-bed `
  --time-mmss 08:08.57 --w1 37.50 --w2 35.50 --w0 32.0

# 精馏综合拓展 1522（乙醇-水体系 P05 全套计算；支持 ethanol-propanol / ethanol-water / benzene-toluene）
.\server\.venv\Scripts\python.exe .\skills\ober-lab-assistant\scripts\chem_calc.py distillation-1522 `
  --system ethanol-water --wd-full 87.97 --ww-full 0.39 `
  --wd-part 75.06 --ww-part 0.38 --wf-part 31.53 --l-lh 9.60 --d-lh 2.40 --t-feed 38.3 --np 10
```
