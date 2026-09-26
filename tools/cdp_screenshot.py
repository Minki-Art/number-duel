"""用 Chrome DevTools Protocol 给页面截图（可以等待真实时间，适合验证动态页面）。

普通的 ``--screenshot`` 在页面加载完成时就截图，等不到 WebSocket 握手 / 定时器；
``--virtual-time-budget`` 又会把虚拟时钟直接快进到预算耗尽。本工具通过 CDP
真实地等待指定秒数后再截图。

用法::

    python tools/cdp_screenshot.py http://127.0.0.1:8000/ out.png --width 430 --height 900
    python tools/cdp_screenshot.py "http://127.0.0.1:8000/?auto=ai" game.png --wait 8

依赖：标准库 + websockets（已在项目依赖里）。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def find_browser() -> str:
    for path in BROWSER_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("没有找到 Edge / Chrome，无法截图")


def wait_for_devtools(port: int, timeout: float = 20.0) -> str:
    """等待调试端口就绪，返回第一个页面 target 的 WebSocket 地址。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("浏览器调试端口没有就绪")


async def capture(
    ws_url: str,
    url: str,
    out_path: Path,
    width: int,
    height: int,
    wait_seconds: float,
    full_page: bool,
    script: str = "",
) -> None:
    async with connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        counter = {"id": 0}

        async def call(method: str, params: dict | None = None) -> dict:
            counter["id"] += 1
            message_id = counter["id"]
            await ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(raw)
                if data.get("id") == message_id:
                    if "error" in data:
                        raise RuntimeError(f"{method} 失败: {data['error']}")
                    return data.get("result", {})

        await call("Page.enable")
        await call("Runtime.enable")
        await call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": width < 600,
            },
        )
        await call("Page.navigate", {"url": url})
        await asyncio.sleep(wait_seconds)

        if script:
            result = await call(
                "Runtime.evaluate",
                {"expression": script, "awaitPromise": True, "returnByValue": True},
            )
            print(f"脚本执行结果: {result.get('result', {}).get('value')}")
            await asyncio.sleep(1.0)

        params = {"format": "png"}
        if full_page:
            metrics = await call("Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            if size.get("height"):
                params["captureBeyondViewport"] = True
                params["clip"] = {
                    "x": 0,
                    "y": 0,
                    "width": size.get("width", width),
                    "height": size["height"],
                    "scale": 1,
                }

        result = await call("Page.captureScreenshot", params)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(result["data"]))
    print(f"已保存 {out_path}  ({out_path.stat().st_size} 字节)")


def main() -> None:
    parser = argparse.ArgumentParser(description="CDP 截图工具")
    parser.add_argument("url", help="要截图的地址")
    parser.add_argument("output", help="输出 PNG 路径")
    parser.add_argument("--width", type=int, default=430)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--wait", type=float, default=4.0, help="等待秒数（留给 WebSocket/动画）")
    parser.add_argument("--full-page", action="store_true", help="截取整页")
    parser.add_argument("--eval", dest="script", default="", help="截图前执行的 JS（可用来点按钮）")
    parser.add_argument("--port", type=int, default=9333)
    args = parser.parse_args()

    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="duel-shot-")
    process = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = wait_for_devtools(args.port)
        asyncio.run(
            capture(
                ws_url,
                args.url,
                Path(args.output),
                args.width,
                args.height,
                args.wait,
                args.full_page,
                args.script,
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
