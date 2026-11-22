# DeepSeek-Harness (DSH) 与 Docker / 深度学习工作流接入指南

本指南适用于使用 **DeepSeek-Harness (DSH)**、`dsh-skill-mcp-manager`、或基于 Docker / WSL2 部署开源 Agent / 深度学习控制环路来操作欧倍尔仿真实验的同学。

---

## 一、 Windows 本机 DeepSeek-Harness 直接接入（STDIO 模式）

### 1. 一键安装依赖并生成本机 DSH 配置
```powershell
git clone https://github.com/zhouqinggao68-ux/ober-lab-assistant.git
cd ober-lab-assistant
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Target deepseek
```
执行完成后会在 `generated-configs\deepseek-harness-mcp.json` 生成已填好本机 Python 虚拟环境路径的完整配置。

### 2. 注册 MCP 与 Skill
- **MCP 配置**：将 `generated-configs\deepseek-harness-mcp.json` 中的 `"mcpServers": { "ober-ocr-mcp": ... }` 合并到你的 DeepSeek-Harness MCP 配置文件，或在 **DSH Skill MCP Manager** 可视化面板中直接新增 STDIO Server；
- **Skill 与规则注入**：将下列路径挂载为 DSH 技能目录与工作区系统规则：
  - Skill 目录：`<REPO_DIR>\skills\ober-lab-assistant`
  - 行为规则：`<REPO_DIR>\rules\AGENTS.md`

---

## 二、 Docker / WSL2 / 远程推理容器接入（Streamable HTTP 桥接）

当你的推理 Harness、LLM 服务或深度学习容器运行在 Linux / WSL2 / Docker 中，而 **欧倍尔客户端运行在 Windows 宿主机桌面** 时，必须让 `ober-ocr-mcp` 跑在 **Windows 宿主机物理桌面会话**，再通过 Streamable HTTP 暴露给容器内的 Harness：

### 1. 在 Windows 宿主机桌面启动视觉控制桥接服务
```powershell
# 仅本机回环（WSL2 / Windows 本机客户端适用）
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests

# Docker 容器通过 host.docker.internal 访问 Windows 宿主机时使用（请确认本机防火墙安全策略）
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests -HttpHost 0.0.0.0 -HttpPort 8011
```

### 2. 在 Docker / DeepSeek-Harness 中配置 Streamable HTTP 端点
```json
{
  "mcpServers": {
    "ober-ocr-mcp": {
      "type": "streamable-http",
      "url": "http://host.docker.internal:8011/mcp"
    }
  }
}
```
> 若容器与 Windows 共享宿主机网络（或在本机进程中连接），将 `host.docker.internal` 改为 `127.0.0.1` 即可。

---

## 三、 多模态 / 工具调用最佳实践建议（适配 DeepSeek 等推理模型）

1. **先审计后操刀**：让 Harness 首轮先调 `probe_desktop_environment` 与 `ocr_window_or_roi` 读全 `查看评分`、`文件管理`、`记录数据` 表头及 `设备列表`，把关键坐标和公式参数写入工作上下文，避免盲点错顺序。
2. **用批处理降延迟**：尽量用单次 `run_action_sequence` 串联“打开设备列表 → 选分类 → 选设备 → 等待镜头稳定 2.0s → 点击/悬停检查”，减少模型轮次耗时（牢记平台 2 小时红线）。
3. **调阀与计算交给确定性工具**：百分比调节统一调 `adjust_valve_popup`，数据计算统一在命令行执行 `skills/ober-lab-assistant/scripts/chem_calc.py` 获取精确 JSON，再填表提交。
