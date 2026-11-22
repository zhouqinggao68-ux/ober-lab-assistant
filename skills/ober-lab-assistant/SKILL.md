---
name: ober-lab-assistant
description: 面向欧倍尔（OBR）化工虚拟仿真实验的三层自动化工作流（v0.5.0）：兼容三类架构（3D+仿DCS+ModelManage、纯3D+scorerun、单进程3D内嵌查看评分），单凭 ober-ocr-mcp 即可运行，提供红字七段数码管解码、闭环调节阀调参、化学色变终点盯表、滚轮/表格/单选框识别、化工物性计算 CLI 与多平台（豆包工作 / DeepSeek-Harness / Antigravity / Claude Code）接入支持。
---

# 欧倍尔虚拟仿真实验助手（OBR Lab Assistant v0.5.0）

本技能面向在 **Windows 主桌面**运行欧倍尔（OBR）化工虚拟仿真实验的 AI 助手（**豆包工作**、**DeepSeek-Harness**、**Antigravity**、**Claude Code / Cursor** 通用），采用可独立部署的分层架构：

1. **视觉盯表与桌面闭环层（`ober-ocr-mcp`，独立运行，无强制外部二进制依赖）**：
   - WGC / MSS 双通道防黑屏截屏 + 前台校验与最小化窗口自动唤醒（`bring_window_to_front`、`probe_desktop_environment(..., include_minimized=True)`）；
   - RapidOCR 抗裂字与黏连位号清洗（`ocr_window_or_roi` / `find_text_targets`，兼容 `F4103.86`、`49. .6 %` 等读数）；
   - **原生七段数码管解码**（`read_seven_segment_roi`，剥离 `SWP-C40`/`SWP-D40` 暗红未亮 ghost 笔段）；
   - **Unity 表格 / 单选框已选状态 / 红叉 / 滚动条滑块精准检测**（`detect_ui_boxes(kind="input_boxes"|"radio_circles"|"red_close"|"scrollbar_thumb")`）；
   - **闭环调节阀弹窗调参**（`adjust_valve_popup`，内置标题栏中性悬停防遮挡与分档步进/长按闭环）；
   - **多模态盯表**：数值阈值与伴随趋势传播（`watch_value_threshold`，支持 `led_color` 与 `parse_mode="mmss_minutes"`）、化学指示剂/滴定色变终点监测（`watch_color_endpoint`，支持 `yellow`/`red`/`pink`/`blue`/`green`）及 3D 镜头稳定监测（`watch_region_until_stable`）；
   - **独立桌面操控原语**：Win32 DPI 感知点击/长按（`fast_click`）、拖拽旋转/拖滚动条（`drag_mouse`）、垂直与水平滚轮（`scroll_mouse`）、悬停闭环切换 3D 球阀/开关（`hover_and_ocr`、`click_3d_valve_verified`，兼容 `已开`/`已关`/`已打开`/`已关闭`）、抗输入法输入（`fast_type`）与单次串行批处理（`run_action_sequence`）。
   - *(可选)* Antigravity 等多 MCP 环境下可并行加载 `ober-windows-mcp` 处理复杂 Edge 控件树交互。
2. **工艺 SOP 与化工计算层（`references/` + `presets/` + `scripts/chem_calc.py`）**：
   - 精馏实训 SOP 与预设：[distillation-sop.md](references/distillation-sop.md) / `rectification-t701-sop`
   - 流化床干燥 SOP 与预设：[fluidized-bed-drying-sop.md](references/fluidized-bed-drying-sop.md) / `fluidized-bed-drying-sop`
   - 吸收解吸综合实验（7 项子实验全流程）SOP 与预设：[absorption-desorption-sop.md](references/absorption-desorption-sop.md) / `absorption-desorption-1547-sop`
   - 离线公式计算 CLI：`scripts/chem_calc.py`（支持 `fluidized-bed`、`packed-hydro`、`co2-absorption`、`ammonia-absorption`、`acetone-absorption`）

---

## 标准执行四步法

### 第 1 步：判别三类架构、重查句柄与先审后做
1. **每次网页点击 `启动` 后**，等待 12–15 秒，调用 `probe_desktop_environment(include_minimized=True)` 获取全新 `HWND`（严禁复用旧句柄）并判断软件架构：
   - **架构 A（3D + `仿DCS / GUS.exe` + `ModelManage.exe`）**：如精馏实训。若见 `ModelManage.exe` 停在右上角，先 `drag_mouse(1715, 25, 1700, 960, button="left")` 停靠至右下角。
   - **架构 B1（纯 3D + 独立评分窗 `scorerun.exe`）**：如流化床干燥（`9310`）。进 3D 后先去 `系统设置` 关闭 `全局标签` 防遮挡数码管。
   - **架构 B2（单进程纯 3D + 内嵌 `查看评分`）**：如吸收解吸综合实验（`1547`）。无独立 `GUS.exe`/`ModelManage.exe`/`scorerun.exe`。
2. **窗口前置统一用 `bring_window_to_front("关键字")`**（支持中文部分匹配与最小化还原）。
3. **进场先做“四项审计”再动任何开关/阀门**：
   - 查看实机 `查看评分` / `scorerun.exe` 的 `P01..P04` 全部步骤与先后约束（超 12 行时用 `scroll_mouse` 下翻）；
   - 打开 `文件管理` 核实是否需切换对应预置文件名（如 `100L水流量`、`0.15氨`、`104L水流量`）；
   - 打开 `记录数据` 记下表头常量并分段拖滚核对全列单位与精度；
   - 打开 `设备列表` 逐一 OCR 各分类与子菜单条目（不同子实验分类顺序会互换，切勿跨项目沿用坐标）。

### 第 2 步：加载预设或实测标定
1. 先调用 `list_presets()` / `load_preset()`；无现成预设时按 [presets-schema.md](references/presets-schema.md) 实测归一化 `(rx, ry)` 后再通过 `resolve_preset_point` 换算。
2. 表格/选项定位统一使用 `detect_ui_boxes`（`radio_circles` 会返回每项的 `checked` 状态，`scrollbar_thumb` 会自动找出滚动滑块坐标）。

### 第 3 步：精准盯表、闭环阀门、色变监测与计算填报
1. **红字数码管**：必须用 `read_seven_segment_roi(..., color="red")` 或 `watch_value_threshold(..., led_color="red")`，确认 `SP` 合规后再开电加热。
2. **手动球阀/开关与调节阀弹窗**：
   - 2 态 3D 手阀/开关：用 `click_3d_valve_verified` 或 `hover_and_ocr` 点击前后双核。
   - 调节阀开度弹窗（0–100%）：优先用 `adjust_valve_popup(window_query=..., target_pct=..., close_when_done=True)`；若手动控制，读数前务必先悬停弹窗标题栏中性位防止提示条盖住数字。
   - 旁路型 `空气流量调节阀` 注意：`100%` 通常代表全旁路零风量，关小开度才增风量；停车停风机前需按评分顺序先全开至 `100%`。
3. **指示剂色变与计时器**：
   - 氨吸收变黄终点等场景用 `watch_color_endpoint(..., target_color="yellow")`，达标后立即复原三通阀并执行 HUD 读数/取样/滴定；
   - 仿真计时器用 `watch_value_threshold(..., parse_mode="mmss_minutes")` 盯表。
4. **数据处理（`chem_calc.py` + 抗输入法填报）**：
   - 先用本地 `scripts/chem_calc.py` 严格按表头常量算出全部待填项，分象限用 `drag_mouse(..., button="left", duration_sec=0.45, steps=20)` 滚动并用 `fast_type(..., clear=True, press_enter=False)` 填表，逐段 OCR 复核后**只点一次 `提交`**，见 `保存成功` 再关闭。

### 第 4 步：有序停车、按架构交卷与云平台解锁核验
1. 严格按 `P02` 倒序停车（确认每一只阀门 `0.00%`/`100.00%`、风机/加热 `已关` 或冷却温度达标后再断总电源）。
2. 完成 `实验分析`（逐屏读题、`detect_ui_boxes(kind="radio_circles")` 勾选复核后提交一次），在 `查看评分` 确认 **`百分制得分 >= 90`** 并截屏留证，**严禁误点 `保存成绩`**。
3. **按架构退出与云端解锁**：
   - **架构 A**：关闭评分窗 → 点击 `ModelManage.exe` 悬浮条红色停止圆钮 `[left+59, top+77]` → 确认 `是(Y)` → 等待 10 秒。
   - **架构 B1 / B2**：保留终分截图后，切回 `UnityVR.exe` 点顶部 **`退出`** → 在确认框点 **`确定`** → 等待 10 秒。
   - 切回浏览器（先核对当前标签页是实验平台页），点击 **`更新题目状态`** → 核验 `最高成绩 >= 90` 与 `时间 < 02:00:00` → 如显示 **`结束本项目`** 则点击解锁后再点 **`更新题目状态`** 确认恢复为 **`启动`**；最后调用 `probe_desktop_environment(include_minimized=True)` 确认无残留本地仿真进程。
