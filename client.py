"""Chat with the local gateway or a shared HTTPS endpoint using Python 3."""
import argparse
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A bearer key belongs to the URL the caller entered.


def main():
    parser = argparse.ArgumentParser(description="千问连续对话终端")
    parser.add_argument("--base-url", default="http://127.0.0.1:18435/v1")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http")) or not parsed.hostname:
        parser.error("远程地址需要使用 HTTPS；本机地址支持 HTTP。")
    if parsed.path != "/v1" or parsed.query or parsed.fragment or parsed.username:
        parser.error("请输入以 /v1 结尾的 Base URL。")
    handlers = [NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    key = getpass.getpass("API Key（输入时隐藏）：").strip()
    if not key:
        parser.error("API Key 不能为空。")
    history = []
    print("开始对话。/clear 清空上下文，/bye 退出。保留最近 8 轮。")
    while True:
        question = input("你 › ").strip()
        if question == "/bye":
            return
        if question == "/clear":
            history.clear()
            print("上下文已清空。")
            continue
        if not question:
            continue
        messages = history + [{"role": "user", "content": question}]
        data = json.dumps({"model": "qwen2.5:1.5b", "messages": messages,
                           "stream": False}, ensure_ascii=False).encode()
        if len(data) > 32 * 1024:
            print("对话内容过长，请输入 /clear 或缩短问题。")
            continue
        req = urllib.request.Request(base + "/chat/completions", data,
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with opener.open(req, timeout=95) as res:
                answer = json.load(res)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as err:
            if err.code == 401:
                sys.exit("API Key 无效，请重新启动客户端并输入正确的 Key。")
            print(f"接口返回 HTTP {err.code}。请检查地址或稍后重试。")
            continue
        except (OSError, ValueError, KeyError, IndexError, TypeError) as err:
            print(f"未收到有效回答：{err}")
            continue
        print("千问 ›", answer)
        history = (messages + [{"role": "assistant", "content": answer}])[-16:]


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
