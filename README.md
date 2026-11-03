# Local Qwen API

在一台 Mac 上运行千问，先在终端和它聊天，再把同一个模型接到网页或分享给异地的朋友。模型权重保存在 Mac 上，推理也在 Mac 上完成。用浏览器访问时，朋友只需要网址和一把 API Key。

| 我想做什么 | 从哪里开始 |
| --- | --- |
| 在自己的 Mac 上连续提问 | [安装模型](#安装模型) → [终端对话](#终端对话) |
| 做一个能验证密钥的聊天 API | [启动网关与网页](#启动网关与网页) → [发送 API 请求](#发送-api-请求) |
| 给朋友一个临时 HTTPS 地址 | [临时分享](#临时分享) |
| 让同一个域名长期可用 | [固定域名与登录自启](#固定域名与登录自启) |

## 安装模型

参考机器是 Apple M1、16 GB 内存、256 GB SSD，运行 macOS 14。示例选用的 `qwen2.5:1.5b` 有约 15.4 亿参数，Ollama 提供的 Q4_K_M 量化版本下载约 986 MB。推理时还会使用内存存放上下文和计算缓冲区。[模型规格](https://ollama.com/library/qwen2.5:1.5b)

1. 从官网下载 macOS 安装包，将 Ollama 拖到“应用程序”后打开。如果首次启动提示安装命令行工具，按提示完成。[官方下载](https://ollama.com/download/mac) · [macOS 安装说明](https://docs.ollama.com/macos)
2. 在 Mac 终端运行以下命令。`pull` 下载权重，`list` 列出本机模型。

```sh
ollama --version
ollama pull qwen2.5:1.5b
ollama list
```

看到 `qwen2.5:1.5b` 后，模型已经在本机。默认模型目录是 `~/.ollama/models/`，其中 `blobs/` 存放按内容哈希命名的文件，`manifests/` 记录标签对应的文件。模型下载一次即可。[Ollama 常见问题](https://docs.ollama.com/faq)

## 终端对话

```sh
ollama run qwen2.5:1.5b
```

看到 `>>>` 后，先输入“请记住，我的小猫叫豆包”，等它回答，再问“我的小猫叫什么？”。同一次对话会携带前文。输入 `/clear` 清空上下文，输入 `/bye` 返回终端。在另一个终端运行 `ollama ps` 可以看到当前已加载的模型及使用的处理器。

如果只想问一个问题，可以把提问放在模型名后面。回答完成时终端会回到命令行。

```sh
ollama run qwen2.5:1.5b "用一句话解释什么是模型推理。"
```

## 启动网关与网页

Ollama 启动后，本机已有 `127.0.0.1:11434` 推理接口。Ollama 本地的 OpenAI 兼容接口会忽略客户端填写的 API Key。本项目在它前面加一层 Python 网关，只接受指定模型的文本聊天，并在调用模型前校验密钥。[Ollama 接口说明](https://docs.ollama.com/api/openai-compatibility)

```text
浏览器或命令行 ── HTTPS ── Cloudflare Tunnel ──┐
                                             ▼
本机浏览器 ──────────────────────── 127.0.0.1:18435   网关校验 Bearer Key
                                             │
                                             ▼
                                  127.0.0.1:11434   Ollama 运行千问
```

从 GitHub 的 **Code → Download ZIP** 下载并解压本仓库，或用 Git 克隆仓库。在包含 `server.py` 的目录里检查 Python 版本并启动网关。

```sh
python3 --version  # 需要 Python 3.9 或更新版本，仅使用标准库
python3 server.py --port 18435 --key-file "$HOME/.config/local-qwen-api/api-key"
```

网关首次启动会生成随机密钥并写入指定文件，以后启动时复用。保留这个终端窗口，在另一个终端把密钥复制到剪贴板，然后打开网页。

```sh
pbcopy < "$HOME/.config/local-qwen-api/api-key"
open http://127.0.0.1:18435/
```

在网页的密钥框粘贴，输入问题即可。网页把这一轮的问答随下一次请求发送给模型。清空对话后可以换话题。给朋友使用时，请通过私信发送密钥，域名和下面的示例命令都可以单独分享。

## 发送 API 请求

**先看 Ollama 原生接口。** 在部署模型的 Mac 上运行，回答在 `message.content` 字段。

```sh
curl -sS http://127.0.0.1:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"2 加 3 等于几？只回答数字。"}],"stream":false}'
```

**再调用项目网关。** 下面的命令从本机密钥文件构造请求头。网关沿用 OpenAI 聊天响应格式，答案在 `choices[0].message.content`。调用方应设置 `stream: false`，等待一条完整的 JSON 响应。

```sh
printf 'Authorization: Bearer %s\n' "$(cat "$HOME/.config/local-qwen-api/api-key")" |
  curl -sS http://127.0.0.1:18435/v1/chat/completions \
    -H @- -H 'Content-Type: application/json' \
    -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"你好，请介绍自己。"}],"stream":false}'
```

**用 API 连续聊天。** 在本机运行 `python3 client.py`。客户端会提示输入密钥，输入时屏幕不回显。随后每行输入一个问题，输入 `/clear` 新开对话，输入 `/bye` 离开。自己编写客户端时，将历史的 `user`、`assistant` 消息和本次提问一起放入有序的 `messages` 数组，模型就能承接前文。

网关每次处理一个生成任务。每次请求最多携带 40 条文本消息，JSON 请求体上限为 32 KiB。通过 `max_tokens` 可将回答长度设为 1 到 512 个 token，默认 256。对话变长时，网页会在发送前裁剪较早的轮次。

## 临时分享

在模型所在的 Mac 安装 `cloudflared`。已有 Homebrew 可运行 `brew install cloudflared`，也可以从官方选择适合 Apple Silicon 的 macOS ARM64 二进制文件，加入命令搜索路径后运行 `cloudflared --version`。[Cloudflare 官方下载](https://developers.cloudflare.com/tunnel/downloads/)

保持 Ollama 和网关运行。在第三个终端启动临时隧道，将请求转发到网关端口。

```sh
cloudflared tunnel --url http://127.0.0.1:18435
```

复制输出中完整的 `https://…trycloudflare.com` 地址。朋友与 Mac 可以处于不同网络，选择哪个客户端都使用你发给他的网关密钥。

| 朋友使用的工具 | 如何填写 |
| --- | --- |
| 浏览器 | 打开隧道输出的网址，粘贴密钥后直接对话 |
| 本项目的终端客户端 | 复制 `client.py` 到朋友的设备，运行 `python3 client.py --base-url "https://实际主机名/v1"`，按提示粘贴密钥 |
| 可自定义接口的程序 | 设置 Base URL 为 `https://实际主机名/v1`，模型为 `qwen2.5:1.5b`，关闭流式输出。自行构造 HTTP 时发送 `POST /v1/chat/completions` 和 `Authorization: Bearer <密钥>` |

本机的 `127.0.0.1` 始终指向执行命令的那台设备，远端客户端须填入**隧道输出的主机名**。测试完，在运行 `cloudflared` 的终端按 `Ctrl+C` 即可关闭临时入口。重新建立 Quick Tunnel 会得到新地址，且它不支持 SSE 流式响应。[Quick Tunnels 官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## 固定域名与登录自启

要让朋友长期保存同一个 Base URL，需要一个交给 Cloudflare 管理 DNS 的域名和一个命名隧道。下文以 `qwen.example.com` 为占位域名，请统一替换为自己的子域名。固定域名可以跨重启保留。请求成功还取决于 Mac 在线、处于唤醒状态，并且 Ollama、网关和隧道都在运行。[创建命名隧道](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)

**① 在模型所在的 Mac 上创建隧道。** 登录后在浏览器中选择你的域名授权。本机终端会打印新隧道的 UUID 和凭据 JSON 文件路径。

```sh
cloudflared tunnel login
cloudflared tunnel create local-qwen-api
```

**② 配置入口。** 在 `~/.cloudflared/local-qwen-api.yml` 保存以下 YAML。替换 `YOUR-TUNNEL-UUID`、`credentials-file` 的完整绝对路径和域名。最后一条规则给未匹配的主机返回 404。隧道凭据和网关密钥分别用于连接 Cloudflare 和验证聊天请求，都应保存在仓库之外。

```yaml
tunnel: YOUR-TUNNEL-UUID
credentials-file: /Users/YOUR-MAC-USER/.cloudflared/YOUR-TUNNEL-UUID.json
ingress:
  - hostname: qwen.example.com
    service: http://127.0.0.1:18435
  - service: http_status:404
```

**③ 将域名指向隧道并试运行。** 以下 DNS 命令只需执行一次。使用同一个域名替换示例，保持网关运行，再从另一台设备打开该 HTTPS 网址并发起一次真实提问。[入口规则说明](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)

```sh
cloudflared tunnel --config "$HOME/.cloudflared/local-qwen-api.yml" ingress validate
cloudflared tunnel route dns local-qwen-api qwen.example.com
cloudflared tunnel --config "$HOME/.cloudflared/local-qwen-api.yml" run local-qwen-api
```

**④ 设置登录自启。** 前台验证成功后，分别在运行网关和隧道的终端按 `Ctrl+C`，避免端口重复占用。保留 Ollama，并在 macOS 登录项里启用它。回到项目根目录，安装两个用户服务。

```sh
sh ops/install-agents.sh "$HOME/.cloudflared/local-qwen-api.yml"
```

安装脚本将服务代码与界面复制到 `~/Library/Application Support/local-qwen-api/`，并为当前用户安装网关和隧道两个 LaunchAgent。登录这个 macOS 账号后服务会启动。更新仓库中的代码后重新执行安装脚本以部署新版本。固定地址的 Base URL 现在是 `https://你的子域名/v1`。

```sh
tail -n 30 "$HOME/Library/Logs/local-qwen-api/gateway.log"
tail -n 30 "$HOME/Library/Logs/local-qwen-api/tunnel.log"
```

如果要关闭公开入口，同时保留本机网页与模型，执行下面两行。第一行禁用隧道的登录启动，第二行停止当前隧道。

```sh
launchctl disable "gui/$(id -u)/com.localqwen.tunnel"
launchctl bootout "gui/$(id -u)/com.localqwen.tunnel"
```

## 检查一次完整调用

| 现象 | 下一步 |
| --- | --- |
| `ollama: command not found` | 从“应用程序”打开 Ollama，按提示安装 CLI，然后新开一个终端运行 `ollama --version` |
| 本机聊天失败 | 运行 `ollama list` 检查模型标签，再用上面的原生 `/api/chat` 请求确认推理可用 |
| 网关返回 `401` | 重新粘贴部署者提供的 API Key。远端电脑读取的是自己的文件，无法用部署者的 Mac 文件路径获取密钥 |
| 网关返回 `429` | 当前已有生成任务，等它完成后再发起下一次请求 |
| 浏览器能打开，聊天请求失败 | 先用本机网关发出同一个请求，再检查密钥、模型名和 `stream` 设置 |
| 公网出现隧道错误或 `502` | 确认 Mac 已唤醒。本机页面可打开后，再核对隧道日志、目标端口与 HTTPS 主机名 |

## 代码入口

| 文件 | 负责什么 |
| --- | --- |
| [server.py](server.py) | 提供网页、校验密钥、限制模型与并发，向本机 Ollama 转发聊天 |
| [client.py](client.py) | 终端提示输入密钥，维护多轮问答历史 |
| [web/index.html](web/index.html) | 无需构建的浏览器聊天界面 |
| [ops/](ops/) | macOS 用户服务模板与安装脚本 |
