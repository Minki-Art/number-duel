"""Number Duel —— 局域网 / 在线对战服务端。

一套服务，三种玩法：

* **单人 vs AI**：服务端用 :mod:`ai` 里的四档策略接管 B 方
* **局域网对战**：房主创建房间拿到 4 位房间码，对手输入房间码（或点分享链接）加入
* **在线对战**：本文件部署到云服务器后，玩法与局域网完全一致

服务端是**权威端**：所有规则判定都在这里跑，浏览器只负责显示和点击，
因此客户端无法作弊，也不需要把游戏规则在 JS 里重写一遍。

启动::

    python server.py                 # 默认 8000 端口，监听 0.0.0.0
    python server.py --port 9000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import socket
import string
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ai import DIFFICULTY_LABELS, create_ai
from game_engine import GameEngine, GameOverError

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_FILE = WEB_DIR / "index.html"

#: 房间码字母表：去掉容易看错的 0/O/1/I
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4

#: AI 出手前的思考停顿，让人类玩家看清上一步发生了什么
AI_THINK_DELAY = 0.6


# ================================================================ 数据模型
@dataclass
class Seat:
    """一个座位（A 或 B）。"""

    role: str
    name: str = ""
    websocket: Optional[WebSocket] = None
    connected: bool = False


@dataclass
class Room:
    """一个对局房间。"""

    code: str
    mode: str = "ai"                       # 'ai' 或 'pvp'
    difficulty: str = "normal"
    engine: GameEngine = field(default_factory=GameEngine)
    seats: Dict[str, Seat] = field(default_factory=dict)
    ai: Optional[object] = None
    started: bool = False
    pending: Optional[str] = None          # 等待房主确认的座位
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def seat(self, role: str) -> Seat:
        if role not in self.seats:
            self.seats[role] = Seat(role=role)
        return self.seats[role]

    def name_of(self, role: str) -> str:
        seat = self.seats.get(role)
        return seat.name if seat else ""

    def reset(self) -> None:
        """重开一局。"""
        self.engine = GameEngine()
        if self.mode == "ai":
            self.ai = create_ai(self.difficulty, "B", random.randint(0, 10 ** 6))


rooms: Dict[str, Room] = {}


# ================================================================ 工具函数
def lan_addresses() -> List[str]:
    """列出本机可供局域网访问的 IPv4 地址。"""
    found: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass

    if not found:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            found.append(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass

    # 常见私网网段排前面，虚拟网卡（VMware / WSL）排后面
    def priority(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        return 3

    return sorted(found, key=priority)


def new_code() -> str:
    """生成一个没被占用的房间码。"""
    while True:
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in rooms:
            return code


def build_state(room: Room, viewer: str) -> dict:
    """打包一份发给客户端的完整状态。"""
    engine = room.engine
    return {
        "type": "state",
        "you": viewer,
        "code": room.code,
        "mode": room.mode,
        "difficulty": room.difficulty,
        "difficulty_label": DIFFICULTY_LABELS.get(room.difficulty, ""),
        "started": room.started,
        "names": {"A": room.name_of("A"), "B": room.name_of("B")},
        "connected": {
            "A": bool(room.seats.get("A") and room.seats["A"].connected),
            "B": room.mode == "ai" or bool(room.seats.get("B") and room.seats["B"].connected),
        },
        "game": {
            "started": room.started,
            "A": [engine.A1, engine.A2],
            "B": [engine.B1, engine.B2],
            "hp_a": engine.hp_a,
            "hp_b": engine.hp_b,
            "events": list(engine.get_render_data()["events"]),
            "turn": engine.current_turn,
            "winner": engine.get_winner(),
            "over": engine.is_game_over(),
            "draw": engine.is_draw(),
            "chained": {"A": engine.is_chained("A"), "B": engine.is_chained("B")},
            "slap": {
                "A": engine.get_slap_turns_left("A"),
                "B": engine.get_slap_turns_left("B"),
            },
            "idle": {"A": engine.get_idle_streak("A"), "B": engine.get_idle_streak("B")},
            "stall_streak": engine.get_stall_streak(),
            "stalemate_limit": GameEngine.STALEMATE_LIMIT,
            "idle_loss_streak": GameEngine.IDLE_LOSS_STREAK,
            # 防刷状态：每个槽位当前"已触发过、等数字刷新"的技能名（UI 据此把数字变灰）
            "locks": {
                "A": engine.get_skill_locks("A"),
                "B": engine.get_skill_locks("B"),
            },
            # 败因说明（对局未结束时为空字符串）
            "end_reason": engine.get_end_reason(),
            # 操作预演：该玩家 8 种操作各自会触发什么技能（UI 在加减弹层里提示）
            "previews": engine.get_action_previews(viewer),
        },
    }


async def send_to(room: Room, role: str, payload: dict) -> None:
    """给某个座位发消息（座位不在线就静默跳过）。"""
    seat = room.seats.get(role)
    if seat and seat.websocket is not None and seat.connected:
        try:
            await seat.websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            seat.connected = False


async def broadcast_state(room: Room) -> None:
    """把最新状态发给房间里所有在线座位。"""
    for role in ("A", "B"):
        seat = room.seats.get(role)
        if seat and seat.connected:
            await send_to(room, role, build_state(room, role))


async def broadcast(room: Room, payload: dict) -> None:
    """把任意消息发给房间里所有在线座位。"""
    for role in ("A", "B"):
        await send_to(room, role, payload)


async def maybe_ai_move(room: Room) -> None:
    """如果轮到 AI 行动，就让它走一步（在线程里算，避免卡住事件循环）。"""
    if room.mode != "ai" or not room.started:
        return
    if room.engine.is_game_over() or room.engine.current_turn != "B":
        return

    await asyncio.sleep(AI_THINK_DELAY)
    try:
        action = await asyncio.to_thread(room.ai.choose_action, room.engine)
        room.engine.execute_action("B", action[0], action[1], action[2])
    except (GameOverError, ValueError):
        pass
    await broadcast_state(room)


# ================================================================ HTTP 接口
app = FastAPI(title="Number Duel", version="0.1.0")


@app.get("/")
@app.get("/play")
async def index() -> FileResponse:
    """游戏页面（/play 是给分享链接用的别名）。"""
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=500, detail="缺少 web/index.html")
    return FileResponse(INDEX_FILE)


@app.get("/api/info")
async def api_info() -> dict:
    """前端用来显示"手机该访问哪个地址"。"""
    return {
        "addresses": lan_addresses(),
        "difficulties": DIFFICULTY_LABELS,
        "stalemate_limit": GameEngine.STALEMATE_LIMIT,
        "idle_loss_streak": GameEngine.IDLE_LOSS_STREAK,
    }


@app.post("/api/rooms")
async def api_create_room(payload: dict) -> dict:
    """创建房间。房主固定坐 A 位。"""
    mode = payload.get("mode", "ai")
    if mode not in ("ai", "pvp"):
        raise HTTPException(status_code=400, detail="mode 只能是 ai 或 pvp")

    difficulty = payload.get("difficulty", "normal")
    if difficulty not in DIFFICULTY_LABELS:
        raise HTTPException(status_code=400, detail="未知的 AI 难度")

    name = str(payload.get("name") or "").strip()[:12] or "房主"

    room = Room(code=new_code(), mode=mode, difficulty=difficulty)
    room.seat("A").name = name
    if mode == "ai":
        room.started = True
        room.ai = create_ai(difficulty, "B", random.randint(0, 10 ** 6))
        room.seat("B").name = f"AI·{DIFFICULTY_LABELS[difficulty]}"
    rooms[room.code] = room

    return {"code": room.code, "role": "A", "mode": mode, "difficulty": difficulty}


@app.get("/api/rooms/{code}")
async def api_room_info(code: str) -> dict:
    """查询房间是否还能加入（客户端连 WebSocket 之前先看一眼）。"""
    room = rooms.get(code.upper())
    if room is None:
        return {"exists": False}
    return {
        "exists": True,
        "mode": room.mode,
        "started": room.started,
        "difficulty": room.difficulty,
        "host": room.name_of("A"),
        "b_taken": bool(room.seats.get("B") and room.seats["B"].connected),
    }


# ================================================================ WebSocket
@app.websocket("/ws/{code}")
async def ws_room(websocket: WebSocket, code: str) -> None:
    """房间主循环：一条连接 = 一个座位。"""
    await websocket.accept()

    room = rooms.get(code.upper())
    role = websocket.query_params.get("role", "A").upper()
    name = (websocket.query_params.get("name") or "").strip()[:12]

    if room is None:
        await websocket.send_json({"type": "error", "message": "房间不存在或已解散"})
        await websocket.close()
        return

    if role not in ("A", "B"):
        await websocket.send_json({"type": "error", "message": "座位号只能是 A 或 B"})
        await websocket.close()
        return

    seat = room.seat(role)
    if seat.connected:
        await websocket.send_json({"type": "error", "message": f"{role} 位已经有人了"})
        await websocket.close()
        return

    seat.websocket = websocket
    seat.connected = True
    if name:
        seat.name = name

    try:
        if role == "A":
            await websocket.send_json({"type": "welcome", "role": "A"})
            if room.mode == "ai":
                await websocket.send_json(build_state(room, "A"))
                await maybe_ai_move(room)
            else:
                await websocket.send_json({"type": "waiting", "message": "等待对手加入…"})
        else:
            if room.mode == "ai":
                await websocket.send_json(
                    {"type": "error", "message": "这是人机房间，B 位由 AI 占据"}
                )
                await websocket.close()
                return
            if room.started:
                await websocket.send_json({"type": "error", "message": "对局已经开始了"})
                await websocket.close()
                return

            await websocket.send_json({"type": "welcome", "role": "B"})
            # 先请房主确认，再由房主 accept
            room.pending = "B"
            await send_to(
                room,
                "A",
                {"type": "join_request", "name": seat.name or "对手", "role": "B"},
            )
            await websocket.send_json(
                {"type": "waiting_approval", "message": "已发送加入请求，等待房主确认…"}
            )
            await broadcast(room, {"type": "presence", "role": "B", "connected": True})

        while True:
            data = await websocket.receive_json()
            await handle_message(room, role, data)

    except WebSocketDisconnect:
        pass
    except (RuntimeError, ValueError):
        pass
    finally:
        seat.connected = False
        seat.websocket = None
        if room.pending == role:
            room.pending = None
        await broadcast(
            room, {"type": "presence", "role": role, "connected": False}
        )
        # 房间空了就回收
        if not any(s.connected for s in room.seats.values()):
            rooms.pop(room.code, None)


async def handle_message(room: Room, role: str, data: dict) -> None:
    """处理客户端消息。"""
    kind = data.get("type")

    if kind == "accept":
        if role != "A" or room.pending is None:
            return
        room.started = True
        room.pending = None
        await broadcast(room, {"type": "started"})
        await broadcast_state(room)
        return

    if kind == "reject":
        if role != "A" or room.pending is None:
            return
        target = room.seat(room.pending)
        await send_to(room, room.pending, {"type": "rejected", "message": "房主拒绝了加入请求"})
        if target.websocket is not None:
            try:
                await target.websocket.close()
            except RuntimeError:
                pass
        target.connected = False
        target.websocket = None
        room.pending = None
        return

    if kind == "action":
        if not room.started:
            await send_to(room, role, {"type": "error", "message": "对局还没开始"})
            return
        if role != room.engine.current_turn:
            await send_to(room, role, {"type": "error", "message": "还没轮到你行动"})
            return
        try:
            room.engine.execute_action(
                role,
                int(data["attacker_slot"]),
                int(data["defender_slot"]),
                str(data["operation"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            await send_to(room, role, {"type": "error", "message": f"非法操作：{exc}"})
            return
        except GameOverError as exc:
            await send_to(room, role, {"type": "error", "message": str(exc)})
            return

        await broadcast_state(room)
        await maybe_ai_move(room)
        return

    if kind == "surrender":
        if not room.started:
            return
        room.engine.surrender(role)
        await broadcast_state(room)
        return

    if kind == "rematch":
        if room.mode == "pvp" and not all(
            room.seats.get(r) and room.seats[r].connected for r in ("A", "B")
        ):
            await send_to(room, role, {"type": "error", "message": "对手不在线，无法重开"})
            return
        room.reset()
        await broadcast(room, {"type": "started"})
        await broadcast_state(room)
        return


# ================================================================ 启动
def open_browser_soon(url: str, delay: float = 1.2) -> None:
    """等服务端起来后，用默认浏览器自动打开页面，省得用户自己找地址。"""

    def worker() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # 打不开浏览器不影响服务端运行
            pass

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Number Duel 服务端")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="改动代码自动重载（开发用）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    local_url = f"http://127.0.0.1:{args.port}"

    print("=" * 62)
    print("  Number Duel 服务端已启动")
    print("=" * 62)
    print(f"  本机访问（就玩这个地址）  {local_url}")
    for ip in lan_addresses():
        print(f"  手机 / 局域网访问        http://{ip}:{args.port}")
    print("-" * 62)
    print("  ⚠ 不要直接双击 web/index.html —— 那样没有服务器，必然连不上。")
    print("    正确做法是运行本脚本，然后访问上面打印出来的地址。")
    print("-" * 62)
    print("  如果手机连不上，多半是 Windows 防火墙拦了入站连接，")
    print("  用管理员 PowerShell 执行一次（只需一次）：")
    print(
        f'    New-NetFirewallRule -DisplayName "NumberDuel {args.port}" '
        f"-Direction Inbound -Protocol TCP -LocalPort {args.port} -Action Allow"
    )
    print("=" * 62)

    if not args.no_browser:
        open_browser_soon(local_url)

    uvicorn.run(
        "server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
