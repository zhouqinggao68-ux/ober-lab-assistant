# 豆包工作（Doubao Work PC 客户端）安装与使用指南

本指南帮助使用 **豆包工作**（豆包 PC 端「技能·连接器·伙伴」）的同学在 **3 分钟内** 部署 `ober-lab-assistant`，无需配置任何其它第三方桌面控制软件即可完成欧倍尔化工虚拟仿真实验。

## 第 1 步：克隆仓库并运行一键安装脚本

在 Windows PowerShell 中运行：

```powershell
git clone https://github.com/zhouqinggao68-ux/ober-lab-assistant.git
cd ober-lab-assistant
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Target doubao
```

安装成功后，脚本会生成：
`generated-configs\doubao-stdio-setup.txt`（已自动填好你本机的绝对路径）。

---

## 第 2 步：在豆包工作中导入工作技能（Skills）

1. 打开 **豆包 PC 客户端**，进入左侧 **「技能·连接器·伙伴」**（或技能管理页面）；
2. 切换到 **「我的技能」**，点击右上角 **「新建 — 上传技能」**；
3. 选择并上传本仓库下的技能目录：
   ```text
   <你的仓库路径>\ober-lab-assistant\skills\ober-lab-assistant
   ```
   > 该目录内已完整包含 `SKILL.md`、四大装置/三大架构 SOP 文档（`references/*.md`）以及化工物性计算 CLI（`scripts/chem_calc.py`）。

---

## 第 3 步：在豆包工作中配置本地 STDIO MCP 插件

1. 在豆包左侧进入 **「插件·技能·伙伴」→「插件」**，点击右上角 **`+ 添加`**（新建自定义连接器）；
2. **传输类型** 选择 **`STDIO`**；
3. 打开本机刚刚生成的 `generated-configs\doubao-stdio-setup.txt`，按其中【方案 A】填写：
   - **插件名称**：`ober-ocr-mcp`
   - **命令 (Command)**：`cmd.exe`
   - **参数 (Args)**：`/d /c "<你的仓库路径>\ober-lab-assistant\scripts\run-ober-ocr-mcp.cmd"`
4. 保存并启用该连接器。

> **备选方案（Docker / 隔离沙箱环境下用 HTTP 连接本机桌面）**：
> 在 Windows 宿主机终端运行 `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests`，随后在豆包连接器选择 **HTTP 类型**，服务器 URL 填 `http://127.0.0.1:8011/mcp`。

---

## 第 4 步：推荐开场指令（直接复制发给豆包）

在浏览器中打开欧倍尔平台并点击某项实验的【启动】等待实验窗口出现后，对豆包发送：

> 请加载我上传的 `ober-lab-assistant` 技能，使用 `ober-ocr-mcp` 工具先调用 `probe_desktop_environment(include_minimized=True)` 识别当前欧倍尔仿真窗口及所属架构，并先打开【查看评分】、【文件管理】、【记录数据】和【设备列表】完成审计，然后再按防扣分 SOP 指导或协同我完成本实验并安全退房解锁，目标总分 >= 90 且耗时 < 2 小时。
