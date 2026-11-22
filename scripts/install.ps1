[CmdletBinding()]
param(
    [ValidateSet("all", "antigravity", "doubao", "deepseek", "claude")]
    [string]$Target = "all",
    [switch]$InstallAntigravity,
    [switch]$StartHttp,
    [switch]$SkipSync,
    [switch]$SkipTests,
    [string]$HttpHost = "127.0.0.1",
    [int]$HttpPort = 8011
)

$ErrorActionPreference = "Stop"

if ($env:OS -notlike "*Windows*") {
    throw "ober-lab-assistant 依赖 Windows 桌面图形采集 (WGC/MSS) 与 Win32 SendInput，请在 Windows 宿主机运行。"
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServerDir = Join-Path $RepoRoot "server"
$VenvPython = Join-Path $ServerDir ".venv\Scripts\python.exe"
$ServerPy = Join-Path $ServerDir "ober_ocr_mcp.py"
$TestPy = Join-Path $ServerDir "test_ober_ocr_mcp.py"
$LauncherCmd = Join-Path $RepoRoot "scripts\run-ober-ocr-mcp.cmd"
$SkillDir = Join-Path $RepoRoot "skills\ober-lab-assistant"
$OutDir = Join-Path $RepoRoot "generated-configs"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "未检测到 uv，请先执行以下命令安装 uv 后重试：" -ForegroundColor Yellow
    Write-Host 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"' -ForegroundColor Cyan
    exit 1
}

if (-not $SkipSync) {
    Write-Host "[1/4] 正在使用 uv 同步 Python 依赖..." -ForegroundColor Cyan
    Push-Location $ServerDir
    try {
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync 失败 (exit code: $LASTEXITCODE)"
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[1/4] 已按要求跳过依赖同步 (-SkipSync)" -ForegroundColor Yellow
}

if (-not (Test-Path $VenvPython)) {
    throw "未找到虚拟环境 Python: $VenvPython"
}

if (-not $SkipTests) {
    Write-Host "[2/4] 正在运行自包含单元测试与 MCP 握手验证..." -ForegroundColor Cyan
    & $VenvPython $TestPy
    if ($LASTEXITCODE -ne 0) {
        throw "单元测试未通过 (exit code: $LASTEXITCODE)"
    }
}
else {
    Write-Host "[2/4] 已按要求跳过单元测试 (-SkipTests)" -ForegroundColor Yellow
}

Write-Host "[3/4] 正在生成各 AI 客户端专属配置 ($OutDir)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if ($Target -in @("all", "doubao")) {
    $doubaoTxt = @"
============================================================
豆包工作 (Doubao Work) 接入配置指南 (已自动填充本机路径)
============================================================

1) 导入工作技能 (Skills)
   - 打开 豆包PC端 -> 「技能·连接器·伙伴」 -> 「我的技能」 -> 「新建 - 上传技能」
   - 选择并上传此完整目录：
     $SkillDir

2) 添加本地 STDIO MCP 插件（二选一，推荐方案 A）
   [方案 A - 通过便携启动脚本]
   - 名称：ober-ocr-mcp
   - 传输类型：STDIO
   - 命令 (Command)：cmd.exe
   - 参数 (Args)：/d /c "$LauncherCmd"

   [方案 B - 直连 Python 解释器]
   - 名称：ober-ocr-mcp
   - 传输类型：STDIO
   - 命令 (Command)：$VenvPython
   - 参数 (Args)：$ServerPy

3) 如使用 HTTP 远程模式（如容器/隔离环境访问本机桌面）
   - 先运行：powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -StartHttp -SkipTests
   - 豆包工作 HTTP 类型 URL：http://$HttpHost`:$HttpPort/mcp
"@
    Set-Content -Path (Join-Path $OutDir "doubao-stdio-setup.txt") -Value $doubaoTxt -Encoding UTF8
}

if ($Target -in @("all", "deepseek")) {
    $dshObj = [ordered]@{
        mcpServers = [ordered]@{
            "ober-ocr-mcp" = [ordered]@{
                command = $VenvPython
                args = @($ServerPy)
                env = [ordered]@{
                    PYTHONUNBUFFERED = "1"
                }
            }
        }
        skillPath = $SkillDir
        rulePath = (Join-Path $RepoRoot "rules\AGENTS.md")
    }
    ($dshObj | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir "deepseek-harness-mcp.json") -Encoding UTF8
}

if ($Target -in @("all", "antigravity")) {
    $agyObj = [ordered]@{
        mcpServers = [ordered]@{
            "ober-ocr-mcp" = [ordered]@{
                command = "cmd.exe"
                args = @("/d", "/c", $LauncherCmd)
            }
        }
    }
    ($agyObj | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir "antigravity-mcp_config.json") -Encoding UTF8
}

if ($Target -in @("all", "claude")) {
    $claudeObj = [ordered]@{
        mcpServers = [ordered]@{
            "ober-ocr-mcp" = [ordered]@{
                command = $VenvPython
                args = @($ServerPy)
            }
        }
    }
    ($claudeObj | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir "claude-cursor-mcp.json") -Encoding UTF8
}

if ($InstallAntigravity) {
    $geminiHome = if ($env:GEMINI_HOME) { $env:GEMINI_HOME } else { Join-Path $env:USERPROFILE ".gemini" }
    $destPlugin = Join-Path $geminiHome "config\plugins\ober-lab-assistant"
    Write-Host "[3.5/4] 正在安装到 Antigravity 插件目录: $destPlugin" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $destPlugin | Out-Null

    foreach ($item in @("plugin.json", "mcp_config.json", "README.md", "CHANGELOG.md", "LICENSE", "assets", "presets", "rules", "skills", "scripts", "docs", "clients")) {
        $srcItem = Join-Path $RepoRoot $item
        if (Test-Path $srcItem) {
            Copy-Item $srcItem $destPlugin -Recurse -Force
        }
    }
    $destServer = Join-Path $destPlugin "server"
    New-Item -ItemType Directory -Force -Path $destServer | Out-Null
    Copy-Item (Join-Path $ServerDir "ober_ocr_mcp.py"),(Join-Path $ServerDir "pyproject.toml"),(Join-Path $ServerDir "test_ober_ocr_mcp.py") $destServer -Force
    Push-Location $destServer
    try {
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "Antigravity 目标目录 uv sync 失败"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "[4/4] 安装完成！生成文件位于: $OutDir" -ForegroundColor Green
Get-ChildItem $OutDir | ForEach-Object { Write-Host ("  - " + $_.FullName) }

if ($StartHttp) {
    Write-Host "`n正在本机桌面以 Streamable HTTP 启动 ober-ocr-mcp (http://$HttpHost`:$HttpPort/mcp)..." -ForegroundColor Cyan
    $env:OBER_OCR_MCP_TRANSPORT = "streamable-http"
    $env:OBER_OCR_MCP_HOST = $HttpHost
    $env:OBER_OCR_MCP_PORT = [string]$HttpPort
    & $VenvPython $ServerPy
}
