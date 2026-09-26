"""AI 强度对战验证工具。

用法::

    python ai_tournament.py                 # 跑完整循环赛
    python ai_tournament.py -n 30           # 每种对阵跑 30 局
    python ai_tournament.py --a easy --b hell

脚本会输出每一档 AI 的胜率、平均回合数与平均决策耗时，
用来验证"地狱 > 困难 > 普通 > 简单"的强度梯度是否成立。
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from typing import Dict, Optional, Tuple

from ai import AI_REGISTRY, DIFFICULTY_LABELS, create_ai
from game_engine import GameEngine

TURN_CAP = 3000


def play_match(
    difficulty_a: str,
    difficulty_b: str,
    seed_a: Optional[int] = None,
    seed_b: Optional[int] = None,
    turn_cap: int = TURN_CAP,
) -> Tuple[Optional[str], int]:
    """让两档 AI 对打一局，返回 ``(胜者, 回合数)``。"""
    engine = GameEngine()
    ais: Dict[str, object] = {
        "A": create_ai(difficulty_a, "A", seed_a),
        "B": create_ai(difficulty_b, "B", seed_b),
    }

    turns = 0
    while not engine.is_game_over() and turns < turn_cap:
        ai = ais[engine.current_turn]
        action = ai.choose_action(engine)
        engine.execute_action(engine.current_turn, action[0], action[1], action[2])
        turns += 1

    return engine.get_winner(), turns


def run_series(
    difficulty_a: str,
    difficulty_b: str,
    games: int,
    turn_cap: int = TURN_CAP,
    base_seed: int = 0,
) -> Tuple[Counter, float, float]:
    """跑若干局，返回 ``(胜负统计, 平均回合数, 平均耗时/局)``。"""
    wins: Counter = Counter()
    total_turns = 0
    total_time = 0.0

    for index in range(games):
        start = time.perf_counter()
        winner, turns = play_match(
            difficulty_a,
            difficulty_b,
            base_seed + 2 * index,
            base_seed + 2 * index + 1,
            turn_cap,
        )
        total_time += time.perf_counter() - start
        total_turns += turns
        wins[winner] += 1

    return wins, total_turns / games, total_time / games


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 强度对战验证")
    parser.add_argument("-n", "--games", type=int, default=10, help="每种对阵的局数")
    parser.add_argument("--a", dest="diff_a", default=None, help="先手 AI 难度")
    parser.add_argument("--b", dest="diff_b", default=None, help="后手 AI 难度")
    parser.add_argument("--cap", type=int, default=TURN_CAP, help="单局回合上限")
    parser.add_argument("--seed", type=int, default=0, help="随机种子基准值")
    args = parser.parse_args()

    if args.diff_a and args.diff_b:
        pairs = [(args.diff_a, args.diff_b)]
    else:
        levels = list(AI_REGISTRY)
        pairs = [(a, b) for a in levels for b in levels if a != b]

    print(f"每种对阵 {args.games} 局，单局回合上限 {args.cap}\n")
    print(f"{'先手':<6}{'后手':<6}{'先手胜':>8}{'后手胜':>8}{'平局/超时':>12}"
          f"{'平均回合':>10}{'平均耗时':>10}")
    print("-" * 62)

    for diff_a, diff_b in pairs:
        wins, avg_turns, avg_time = run_series(
            diff_a, diff_b, args.games, args.cap, args.seed
        )
        print(
            f"{DIFFICULTY_LABELS[diff_a]:<6}{DIFFICULTY_LABELS[diff_b]:<6}"
            f"{wins['A']:>8}{wins['B']:>8}{wins[None]:>12}"
            f"{avg_turns:>10.1f}{avg_time:>9.2f}s"
        )


if __name__ == "__main__":
    main()
