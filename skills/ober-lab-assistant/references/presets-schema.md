# ROI 与坐标预设（Preset）标定规范

所有点位与仪表框均以**目标窗口客户区（Client Area，不含 Windows 标题栏）的归一化比例 `(rx, ry)`** 保存，范围为 `[0.0, 1.0]`：

- `rx = (screen_x - client_origin_x) / client_width`
- `ry = (screen_y - client_origin_y) / client_height`
- `rw = region_width / client_width`
- `rh = region_height / client_height`

## 字段说明

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `experiment_id` | string | 唯一标识，如 `rectification-startup-1522` |
| `calibrated` | boolean | **仅当已在当前实验界面实机核对所有点位后才允许设为 `true`** |
| `window_query` | string | 窗口标题关键字或进程名，如 `"精馏"` 或 `"Unity"` |
| `points` | object | 按按钮/阀门名称索引的 `{ "rx", "ry", "label" }` 映射 |
| `rois` | object | 按仪表名称索引的 `{ "rx", "ry", "rw", "rh", "unit" }` 映射 |

## 标定流程

1. 调用 `probe_desktop_environment()` 获取欧倍尔 Unity 窗口标题与 `client_size`。
2. 调用 `capture_window_wgc(window_query=...)` 截一张图并查看布局，或用 `find_text_targets(query=..., window_query=...)` 直接搜文字按钮的 `(rx, ry)`。
3. 对数字仪表区域，用 `ocr_window_or_roi(window_query=..., roi={"rx": ..., "ry": ..., "rw": ..., "rh": ...}, scale=2.5, numeric_only=True)` 验证能否稳定识别出正确读数（深色背景亮色数字建议开启 `invert=True`）。
4. 全部核对通过后，将 `calibrated` 设为 `true` 并调用 `save_preset` 保存，后续即可用 `resolve_preset_point` 毫秒级换算真实像素坐标。
