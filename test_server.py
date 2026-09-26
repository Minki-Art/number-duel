"""服务端集成测试。

会在后台线程里真起一个 uvicorn，然后用 ``websockets`` 客户端走完整流程，
覆盖：HTTP 接口 / 人机对战 / 局域网红蓝双方加入 + 房主确认 + 轮流行动 / 非法操作。

运行::

    python -m unittest test_server
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
import urllib.request

import uvicorn
from websockets.asyncio.client import connect

import server as server_module

HOST = "127.0.0.1"
PORT = 8765
BASE = f"http://{HOST}:{PORT}"
WS_BASE = f"ws://{HOST}:{PORT}"


def http_json(path: str, payload: dict | None = None) -> dict:
    """极简 HTTP 客户端（标准库，避免额外依赖）。"""
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


async def recv_until(ws, wanted: str, limit: int = 15) -> dict:
    """一直收消息，直到收到指定类型（或超时）。"""
    for _ in range(limit):
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        message = json.loads(raw)
        if message.get("type") == wanted:
            return message
    raise AssertionError(f"没有收到类型为 {wanted} 的消息")


class ServerTestCase(unittest.TestCase):
    """起一个真实服务端进程。"""

    server: uvicorn.Server
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        config = uvicorn.Config(
            server_module.app, host=HOST, port=PORT, log_level="error"
        )
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()

        deadline = time.time() + 15
        while time.time() < deadline:
            if getattr(cls.server, "started", False):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("服务端没能在 15 秒内启动")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    # ------------------------------------------------------------ HTTP
    def test_api_info(self):
        info = http_json("/api/info")
        self.assertIn("addresses", info)
        self.assertEqual(
            info["difficulties"],
            {"easy": "简单", "normal": "普通", "hard": "困难", "hell": "地狱"},
        )
        self.assertGreaterEqual(info["stalemate_limit"], 40)

    def test_create_ai_room(self):
        room = http_json("/api/rooms", {"mode": "ai", "difficulty": "normal", "name": "测试"})
        self.assertEqual(room["mode"], "ai")
        self.assertEqual(room["role"], "A")
        self.assertEqual(len(room["code"]), 4)

        info = http_json(f"/api/rooms/{room['code']}")
        self.assertTrue(info["exists"])
        self.assertTrue(info["started"])

    def test_room_not_found(self):
        info = http_json("/api/rooms/ZZZZ")
        self.assertFalse(info["exists"])

    def test_create_room_rejects_bad_mode(self):
        with self.assertRaises(Exception):
            http_json("/api/rooms", {"mode": "solo"})

    def test_create_room_rejects_bad_difficulty(self):
        with self.assertRaises(Exception):
            http_json("/api/rooms", {"mode": "ai", "difficulty": "impossible"})

    # ------------------------------------------------------------ 人机
    def test_ai_game_flow(self):
        room = http_json("/api/rooms", {"mode": "ai", "difficulty": "easy", "name": "我"})
        asyncio.run(self._play_ai(room["code"]))

    async def _play_ai(self, code: str) -> None:
        async with connect(f"{WS_BASE}/ws/{code}?role=A&name=%E6%88%91") as ws:
            state = await recv_until(ws, "state")
            self.assertEqual(state["you"], "A")
            self.assertEqual(state["game"]["turn"], "A")
            self.assertEqual(state["game"]["A"], [1, 1])

            # A 出手（1 + 1 = 2）
            await ws.send(json.dumps({
                "type": "action", "attacker_slot": 1,
                "defender_slot": 1, "operation": "+",
            }))
            state = await recv_until(ws, "state")
            self.assertEqual(state["game"]["A"][0], 2)

            # AI 应当随后自动行动，轮到 A 时数字已变化
            state = await recv_until(ws, "state")
            self.assertEqual(state["game"]["turn"], "A", "AI 走完后应轮回 A")

    def test_ai_move_rejected_when_not_your_turn(self):
        room = http_json("/api/rooms", {"mode": "ai", "difficulty": "easy", "name": "我"})
        asyncio.run(self._wrong_turn(room["code"]))

    async def _wrong_turn(self, code: str) -> None:
        async with connect(f"{WS_BASE}/ws/{code}?role=A") as ws:
            await recv_until(ws, "state")
            await ws.send(json.dumps({
                "type": "action", "attacker_slot": 0,
                "defender_slot": 1, "operation": "+",
            }))
            error = await recv_until(ws, "error")
            self.assertIn("非法操作", error["message"])

    # ------------------------------------------------------------ 联机
    def test_pvp_join_accept_and_play(self):
        asyncio.run(self._pvp())

    async def _pvp(self) -> None:
        room = http_json("/api/rooms", {"mode": "pvp", "name": "房主"})
        code = room["code"]

        async with connect(f"{WS_BASE}/ws/{code}?role=A&name=%E6%88%BF%E4%B8%BB") as host:
            await recv_until(host, "welcome")
            await recv_until(host, "waiting")

            async with connect(f"{WS_BASE}/ws/{code}?role=B&name=%E6%8C%91%E6%88%98%E8%80%85") as guest:
                await recv_until(guest, "welcome")
                await recv_until(guest, "waiting_approval")

                # 房主收到加入请求并同意
                request = await recv_until(host, "join_request")
                self.assertEqual(request["name"], "挑战者")
                await host.send(json.dumps({"type": "accept"}))

                await recv_until(host, "started")
                await recv_until(guest, "started")
                host_state = await recv_until(host, "state")
                guest_state = await recv_until(guest, "state")

                self.assertEqual(host_state["you"], "A")
                self.assertEqual(guest_state["you"], "B")
                self.assertEqual(host_state["game"]["turn"], "A")

                # 轮到 A 时 B 抢先出手应被拒绝
                await guest.send(json.dumps({
                    "type": "action", "attacker_slot": 1,
                    "defender_slot": 1, "operation": "+",
                }))
                error = await recv_until(guest, "error")
                self.assertIn("还没轮到你", error["message"])

                # A 出手后轮到 B
                await host.send(json.dumps({
                    "type": "action", "attacker_slot": 1,
                    "defender_slot": 1, "operation": "+",
                }))
                await recv_until(host, "state")
                state = await recv_until(guest, "state")
                self.assertEqual(state["game"]["turn"], "B")

                # B 出手后轮到 A
                await guest.send(json.dumps({
                    "type": "action", "attacker_slot": 2,
                    "defender_slot": 2, "operation": "+",
                }))
                state = await recv_until(host, "state")
                self.assertEqual(state["game"]["turn"], "A")

    def test_pvp_reject_join(self):
        asyncio.run(self._pvp_reject())

    async def _pvp_reject(self) -> None:
        room = http_json("/api/rooms", {"mode": "pvp", "name": "房主"})
        code = room["code"]

        async with connect(f"{WS_BASE}/ws/{code}?role=A") as host:
            await recv_until(host, "waiting")
            async with connect(f"{WS_BASE}/ws/{code}?role=B") as guest:
                await recv_until(host, "join_request")
                await host.send(json.dumps({"type": "reject"}))
                rejected = await recv_until(guest, "rejected")
                self.assertIn("拒绝", rejected["message"])

    def test_seat_taken(self):
        asyncio.run(self._seat_taken())

    async def _seat_taken(self) -> None:
        room = http_json("/api/rooms", {"mode": "pvp", "name": "房主"})
        code = room["code"]
        async with connect(f"{WS_BASE}/ws/{code}?role=A") as first:
            await recv_until(first, "waiting")
            async with connect(f"{WS_BASE}/ws/{code}?role=A") as second:
                error = await recv_until(second, "error")
                self.assertIn("已经有人", error["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
