# 在 Windows 上部署千问

用一台 Windows 电脑运行 `qwen2.5:1.5b`，先在 PowerShell 里连续聊天，再把同一个模型接到网页和 HTTPS 接口。下文适用于 Windows PowerShell 5.1 和 PowerShell 7。文中的“主机”指保存模型、执行推理的电脑。朋友如果只是调用你的模型，可以直接跳到[在另一台设备使用](#在另一台设备使用)。

## 1 · 准备主机

- **系统与架构**。Ollama 官方要求 Windows 10 22H2 或更新的家庭版、专业版，Windows 11 也适用。打开“设置 → 系统 → 关于”查看系统类型。Intel 和 AMD 的 64 位 PC 为 x64，骁龙等 ARM 设备通常为 ARM64。官方安装程序会按架构选择组件，独立 CLI 压缩包也分别提供 `windows-amd64` 和 `windows-arm64` 版本。[Windows 要求](https://docs.ollama.com/windows) · [安装器支持的架构](https://github.com/ollama/ollama/blob/main/app/ollama.iss) · [官方发布包](https://github.com/ollama/ollama/releases)
- **磁盘与内存**。Windows 版 Ollama 本身至少需要 4 GB 磁盘，另需约 986 MB 下载本例的 Q4_K_M 量化权重，并为临时文件和系统更新留出余量。16 GB 内存适合用这个模型练习单人文本对话。生成时的内存占用还包括上下文和运行缓冲区。[安装空间](https://docs.ollama.com/windows) · [模型规格](https://ollama.com/library/qwen2.5:1.5b)
- **计算设备**。可以先用 CPU 跑通。若希望用显卡加速，请核对具体型号和驱动。Ollama 的 Windows 页面列出 NVIDIA 驱动 551.61 或更新版本。AMD 的 ROCm 后端要求支持 ROCm v7 / HIP7 的驱动栈，Vulkan 后端要求兼容 Vulkan 的 Radeon 驱动。实际推理使用哪种设备，以运行时的 `ollama ps` 为准。[Windows 硬件要求](https://docs.ollama.com/windows) · [支持的 GPU](https://docs.ollama.com/gpu)
- **程序**。从官网下载并运行 `OllamaSetup.exe`，从开始菜单启动 Ollama。安装 Python 3.9 或更新版本，优先选择仍在维护且匹配 CPU 架构的版本。克隆本仓库，或通过 GitHub **Code → Download ZIP** 解压，在包含 `server.py` 的文件夹里打开 PowerShell。[Ollama 下载](https://ollama.com/download/windows) · [Python Windows 安装指南](https://docs.python.org/3/using/windows.html)

```powershell
ollama --version
py -3 --version
Get-Item .\server.py, .\client.py
```

如果已安装 Python 而没有 `py` 启动器，可先运行 `python --version`，确认版本后将下文的 `py -3` 替换为 `python`。

## 2 · 下载权重并进行第一次对话

```powershell
ollama pull qwen2.5:1.5b
ollama list
ollama run qwen2.5:1.5b
```

看到 `>>>` 后输入“请记住，我的小猫叫豆包”，等模型回答，再问“我的小猫叫什么？”。输入 `/clear` 清空当前上下文，输入 `/bye` 退出。在另一个终端运行 `ollama ps` 查看模型是否使用 GPU。默认模型目录可用 `explorer.exe "$env:USERPROFILE\.ollama\models"` 打开。模型较多时，可按官方说明设置用户环境变量 `OLLAMA_MODELS`，退出托盘里的 Ollama 后重新启动，再下载到指定磁盘。[模型目录与运行说明](https://docs.ollama.com/windows)

## 3 · 分别验证本机 API 和项目网关

**先测试推理服务。** 在 PowerShell 中将对象转成 JSON，再显式编码成 UTF-8 字节，避免 Windows PowerShell 5.1 把中文请求编码成其他字符。以下命令保留 `$body` 变量，后续测试会复用。

```powershell
$payload = @{
    model = 'qwen2.5:1.5b'
    messages = @(@{ role = 'user'; content = '2 加 3 等于几？只回答数字。' })
    stream = $false
}
$body = [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress))
$request = @{
    Method = 'Post'
    ContentType = 'application/json; charset=utf-8'
    Body = $body
}
$answer = Invoke-RestMethod @request -Uri 'http://127.0.0.1:11434/api/chat'
$answer.message.content
```

**再启动带密钥的网关。** 另开一个位于项目根目录的 PowerShell 窗口，运行以下命令并保持窗口打开。

```powershell
$keyFile = Join-Path $env:LOCALAPPDATA 'LocalQwenApi\api-key'
py -3 .\server.py --port 18435 --key-file "$keyFile"
```

首次启动会在当前用户的应用数据目录生成密钥。回到前一个终端，为同一个问题加上 Bearer 密钥并从网关发送。原生 API 的回答在 `message.content`，网关的回答在 `choices[0].message.content`。

```powershell
$keyFile = Join-Path $env:LOCALAPPDATA 'LocalQwenApi\api-key'
$key = (Get-Content -Raw -LiteralPath $keyFile).Trim()
$auth = @{ Authorization = "Bearer $key" }
$answer = Invoke-RestMethod @request -Headers $auth -Uri 'http://127.0.0.1:18435/v1/chat/completions'
$answer.choices[0].message.content
```

Ollama 的本地兼容接口会忽略客户端提供的 API Key。隧道应始终转发到校验密钥的网关端口 **18435**。网关只接收这个千问模型的非流式文本聊天，一次处理一个生成任务。[Ollama 本地认证行为](https://docs.ollama.com/api/openai-compatibility)

**进入网页版或终端版连续对话。** 把密钥复制到剪贴板并打开网页。也可以从项目目录运行 `py -3 .\client.py`，在隐藏输入提示中粘贴密钥，然后逐行提问，用 `/clear` 或 `/bye` 控制会话。客户端把最近的问答随下一条消息发送给模型。

```powershell
Set-Clipboard -Value $key
Start-Process 'http://127.0.0.1:18435/'
```

## 4 · 发布临时 HTTPS 地址

安装 Cloudflare 官方的 `cloudflared`。x64 Windows 可使用下面的 WinGet 命令。也可以从官方下载页获取 Windows 可执行文件，将其命名为 `cloudflared.exe` 并放进用户的 PATH 目录。重新打开 PowerShell，运行 `cloudflared --version` 验证。[官方下载](https://developers.cloudflare.com/tunnel/downloads/)

```powershell
winget install --exact --id Cloudflare.cloudflared
```

**ARM64 主机选择隧道程序时要单独核对架构。** 官方 Windows 下载表列出 x86 和 x64。Windows 11 ARM 支持运行 x64 程序，Windows 10 ARM 的模拟层支持 x86。可按系统选择对应的 `.exe`，先验证其能启动。Ollama 和 Python 仍可使用各自的 ARM64 版本。[Cloudflare Windows 下载表](https://developers.cloudflare.com/tunnel/downloads/) · [微软的 ARM 模拟说明](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation)

保持 Ollama 和项目网关运行。新开一个终端，将临时 HTTPS 入口转发到本机网关。

```powershell
cloudflared tunnel --url http://127.0.0.1:18435
```

复制输出中的完整 `https://…trycloudflare.com` 地址，通过私信将密钥交给朋友。隧道终端按 `Ctrl+C` 即关闭公网入口。下次运行会分配新主机名，Quick Tunnel 不支持 SSE 流式响应。[Quick Tunnels 官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

### 在另一台设备使用

朋友打开你提供的 **HTTPS 网址**，在聊天页输入密钥即可。喜欢用终端的朋友可以只下载仓库里的 [client.py](../client.py)，有 Python 即可运行。将下面的示例地址替换为你实际提供的域名。

```powershell
py -3 .\client.py --base-url 'https://qwen.example.com/v1'
```

脚本会先提示输入密钥，随后进入连续对话。朋友处于手机热点、校园网或家中网络都可以访问公开域名。调用方程序使用 `POST /v1/chat/completions`、`Authorization: Bearer <密钥>` 和 `stream: false`。朋友设备上的 `127.0.0.1` 只代表朋友自己的设备。

**只想发一次问题，也可以直接用 PowerShell。** 复制下面整段，按提示依次粘贴部署者发给你的网址、密钥，再输入问题。地址填写 HTTPS 首页，结尾不带 `/v1`。

```powershell
$site = [uri](Read-Host 'HTTPS home URL')
if (-not $site.IsAbsoluteUri -or $site.Scheme -ne 'https' -or $site.PathAndQuery -ne '/') {
    throw 'Enter the full HTTPS site origin without /v1 or query parameters.'
}
$secret = Read-Host 'API Key' -AsSecureString
$question = Read-Host 'Question'
$data = @{ model = 'qwen2.5:1.5b'; stream = $false; messages = @(@{ role = 'user'; content = $question }) }
$argsForApi = @{
    Method = 'Post'
    Uri = [uri]::new($site, '/v1/chat/completions')
    MaximumRedirection = 0
    Headers = @{ Authorization = 'Bearer ' + [Net.NetworkCredential]::new('', $secret).Password }
    ContentType = 'application/json; charset=utf-8'
    Body = [Text.Encoding]::UTF8.GetBytes(($data | ConvertTo-Json -Depth 5 -Compress))
}
try { (Invoke-RestMethod @argsForApi).choices[0].message.content }
finally { Remove-Variable argsForApi, secret }
```

## 5 · 使用固定域名

准备一个通过 Cloudflare 管理 DNS 的域名。在模型主机上登录并创建命名隧道，授权时选择这个域名，然后记下终端输出中的隧道 UUID 与凭据 JSON 的路径。[创建命名隧道](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)

```powershell
cloudflared tunnel login
cloudflared tunnel create local-qwen-api
```

以下脚本会向你询问 UUID 和子域名。它将在用户目录创建专用配置文件 `.cloudflared\local-qwen-api.yml`，并验证入口规则。请填写自己的子域名，例如 `qwen.example.com` 的实际替代值，不带 `https://`。

```powershell
$tunnelId = (Read-Host 'Tunnel UUID').Trim()
$domain = (Read-Host 'API hostname').Trim()
$cfDir = Join-Path $env:USERPROFILE '.cloudflared'
$configPath = Join-Path $cfDir 'local-qwen-api.yml'
$credential = (Resolve-Path -LiteralPath (Join-Path $cfDir "$tunnelId.json")).Path.Replace('\', '/')
$yaml = @"
tunnel: $tunnelId
credentials-file: "$credential"
ingress:
  - hostname: $domain
    service: http://127.0.0.1:18435
  - service: http_status:404
"@
[IO.File]::WriteAllText($configPath, $yaml, [Text.UTF8Encoding]::new($false))
cloudflared tunnel --config "$configPath" ingress validate
```

创建 DNS 记录后前台运行隧道，换另一台设备打开自己的域名并完成一道问答。以后重启隧道仍可使用同一地址。隧道凭据 JSON、账户证书和聊天密钥都保存在仓库之外。[入口配置说明](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)

```powershell
cloudflared tunnel route dns local-qwen-api $domain
cloudflared tunnel --config "$configPath" run local-qwen-api
```

### 当前用户登录后自动启动

确认固定域名可用后，在**手动运行网关和隧道**的窗口分别按 `Ctrl+C`。在 Windows 的“启动应用”中确认 Ollama 已启用。在项目根目录以运行 Ollama 的同一用户执行安装脚本。它暂存网关和界面，并注册当前用户的两个计划任务。

```powershell
$configPath = Join-Path $env:USERPROFILE '.cloudflared\local-qwen-api.yml'
.\ops\install-windows.ps1 -ConfigPath "$configPath"
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Get-ScheduledTask -TaskName "LocalQwenApi-*-$sid" | Select-Object TaskName, State
```

如果 Windows 报“禁止运行脚本”，先用 `Get-ExecutionPolicy -List` 查看策略。个人电脑上，先核对仓库脚本内容。若文件带下载标记，可仅对这个文件执行 `Unblock-File -LiteralPath .\ops\install-windows.ps1`，再用 `powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\ops\install-windows.ps1 -ConfigPath "$configPath"` 单次运行。组织组策略有优先级，受管理的电脑应使用获准策略。[微软执行策略说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies)

长期可用依赖该用户已登录、电脑唤醒、联网且 Ollama 正在运行。升级项目后，重新执行安装脚本以更新已部署的副本。如果只需要停止共享并保留电脑上的本地模型，停用并停止隧道任务即可。

```powershell
$tunnelTask = "LocalQwenApi-Tunnel-$sid"
Disable-ScheduledTask -TaskName $tunnelTask
Stop-ScheduledTask -TaskName $tunnelTask
```

## 逐层排查

| 现象 | 在哪里检查 |
| --- | --- |
| 找不到 `ollama`、`py` 或 `cloudflared` | 安装后新开窗口，用 `Get-Command ollama, py, cloudflared -ErrorAction SilentlyContinue` 查看实际路径 |
| 本机 `11434` 不通 | 从开始菜单启动 Ollama，运行 `Invoke-RestMethod http://127.0.0.1:11434/api/tags`，确认有 `qwen2.5:1.5b` |
| 网关起不来或提示端口占用 | 运行 `Get-NetTCPConnection -State Listen -LocalPort 18435`，检查是否同时启动了手动实例和计划任务 |
| 网页能打开，但提问返回 `401` | 使用部署网关的 Windows 用户目录中的密钥。URL 和密钥需对应同一个网关 |
| 返回 `413` 或 `429` | 前者表示请求体超过 32 KiB，可缩短输入或清空上下文。后者表示已有生成任务，请稍后再试 |
| 公网返回 `502` 或连接不上 | 先用本机网页完成问答，再核对隧道日志、主机名、端口和休眠状态。局域网 IP 或 `localhost` 都不能替代朋友需要的 HTTPS 主机名 |

[返回项目首页](../README.md)
