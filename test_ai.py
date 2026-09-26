"""AI 策略层的单元测试。

覆盖：构造与参数校验、着法合法性、状态只读性、战术能力、
搜索分支正确性、随机可复现性与强度梯度。
"""

from __future__ import annotations

import unittest

from ai import (
    AI_REGISTRY,
    DIFFICULTY_LABELS,
    GreedyAI,
    HardAI,
    HellAI,
    RandomAI,
    SearchAI,
    WIN_SCORE,
    BaseAI,
    create_ai,
)
from ai_tournament import play_match
from game_engine import GameEngine, GameOverError

ALL_DIFFICULTIES = ("easy", "normal", "hard", "hell")


def build_engine(a1=1, a2=1, b1=1, b2=1, hp_a=3, hp_b=3, turn="A") -> GameEngine:
    """构造指定初始状态的引擎（仅用于测试）。"""
    engine = GameEngine()
    engine.A1, engine.A2, engine.B1, engine.B2 = a1, a2, b1, b2
    engine.hp_a, engine.hp_b = hp_a, hp_b
    engine.current_turn = turn
    engine.reset_skill_state()
    return engine


def snapshot(engine: GameEngine):
    """抓取引擎的完整可见状态，用于验证"未被修改"。"""
    data = engine.get_render_data()
    return (
        tuple(data["A"]),
        tuple(data["B"]),
        data["hp_a"],
        data["hp_b"],
        tuple(data["events"]),
        engine.current_turn,
        engine.is_chained("A"),
        engine.is_chained("B"),
        engine.get_slap_turns_left("A"),
        engine.get_slap_turns_left("B"),
    )


class TestConstruction(unittest.TestCase):
    """构造与注册表。"""

    def test_registry_covers_four_difficulties(self):
        self.assertEqual(set(AI_REGISTRY), set(ALL_DIFFICULTIES))
        self.assertEqual(
            DIFFICULTY_LABELS,
            {"easy": "简单", "normal": "普通", "hard": "困难", "hell": "地狱"},
        )

    def test_create_ai_returns_expected_class(self):
        expected = {
            "easy": RandomAI,
            "normal": GreedyAI,
            "hard": HardAI,
            "hell": HellAI,
        }
        for difficulty, expected_class in expected.items():
            ai = create_ai(difficulty, "B")
            self.assertIsInstance(ai, expected_class)
            self.assertIsInstance(ai, BaseAI)

    def test_create_ai_rejects_unknown_difficulty(self):
        for bad in ("impossible", "", "EASY", None):
            with self.assertRaises(ValueError):
                create_ai(bad, "A")

    def test_create_ai_rejects_invalid_player(self):
        with self.assertRaises(ValueError):
            create_ai("easy", "C")

    def test_opponent_property(self):
        self.assertEqual(create_ai("easy", "A").opponent, "B")
        self.assertEqual(create_ai("easy", "B").opponent, "A")

    def test_search_depth_gradient(self):
        self.assertLess(SearchAI.SEARCH_DEPTH, HellAI.SEARCH_DEPTH)
        self.assertGreater(HellAI.SEARCH_DEPTH, HardAI.SEARCH_DEPTH)


class TestActionLegality(unittest.TestCase):
    """所有难度的 AI 都必须给出合法操作，且不得改写对局。"""

    def test_every_difficulty_returns_legal_action(self):
        for difficulty in ALL_DIFFICULTIES:
            for turn in ("A", "B"):
                engine = build_engine(a1=23, a2=47, b1=35, b2=19, turn=turn)
                ai = create_ai(difficulty, turn, seed=1)
                attacker_slot, defender_slot, operation = ai.choose_action(engine)
                self.assertIn(attacker_slot, (1, 2), difficulty)
                self.assertIn(defender_slot, (1, 2), difficulty)
                self.assertIn(operation, ("+", "-"), difficulty)

    def test_choose_action_does_not_mutate_engine(self):
        for difficulty in ALL_DIFFICULTIES:
            engine = build_engine(a1=16, a2=29, b1=35, b2=38, hp_a=4, hp_b=5)
            before = snapshot(engine)
            create_ai(difficulty, "A", seed=7).choose_action(engine)
            self.assertEqual(snapshot(engine), before, difficulty)

    def test_choose_action_rejects_wrong_turn(self):
        engine = build_engine(turn="A")
        ai = create_ai("normal", "B")
        with self.assertRaises(ValueError):
            ai.choose_action(engine)

    def test_choose_action_rejects_finished_game(self):
        engine = build_engine(hp_b=0)
        ai = create_ai("normal", "A")
        with self.assertRaises(GameOverError):
            ai.choose_action(engine)

    def test_action_space_has_eight_moves(self):
        self.assertEqual(len(create_ai("easy", "A").all_actions()), 8)

    def test_nodes_examined_is_recorded(self):
        engine = build_engine(a1=16, a2=29, b1=35, b2=38)
        for difficulty in ("normal", "hard"):
            ai = create_ai(difficulty, "A", seed=3)
            ai.choose_action(engine)
            self.assertGreater(ai.nodes_examined, 0, difficulty)


class TestEvaluation(unittest.TestCase):
    """局面评估函数。"""

    def test_win_and_loss_scores(self):
        ai = create_ai("hard", "A")
        win_engine = build_engine(hp_b=0)
        lose_engine = build_engine(hp_a=0)
        self.assertEqual(ai.evaluate(win_engine), WIN_SCORE)
        self.assertEqual(ai.evaluate(lose_engine), -WIN_SCORE)

    def test_hp_difference_dominates(self):
        ai = create_ai("hard", "A")
        healthy = build_engine(hp_a=5, hp_b=1)
        hurt = build_engine(hp_a=1, hp_b=5)
        self.assertGreater(ai.evaluate(healthy), ai.evaluate(hurt))


class TestTactics(unittest.TestCase):
    """战术能力：能算到并走出一击制胜的着法。"""

    def test_search_ai_takes_immediate_win(self):
        # A2=18 已经是"旋镖"的一半，B 只剩 1 血。
        # 用 A1(12) 减去 B1 的个位 4 -> A1 变 8 -> 双个位 8 触发旋镖 -> B 阵亡。
        # 初始局面刻意不含任何个位 6/7/8 的组合，避免技能在 AI 决策前先自行触发。
        for difficulty in ("normal", "hard", "hell"):
            engine = build_engine(a1=12, a2=18, b1=14, b2=11, hp_b=1)
            engine.check_skills()
            self.assertFalse(engine.is_game_over(), "初始局面不应该是结束状态")

            ai = create_ai(difficulty, "A", seed=5)
            action = ai.choose_action(engine)

            after = engine.clone()
            after.execute_action("A", action[0], action[1], action[2])
            self.assertTrue(after.is_game_over(), f"{difficulty} 没有抓住制胜着法")
            self.assertEqual(after.get_winner(), "A", difficulty)

    def test_ai_handles_chained_opponent(self):
        engine = build_engine(a1=29, a2=39, b1=16, b2=14, turn="A")
        engine.execute_action("A", 1, 1, "+")   # 触发锁链，B 被缠住
        self.assertEqual(engine.current_turn, "B")
        ai = create_ai("hell", "B", seed=2)
        action = ai.choose_action(engine)       # 被跳过也必须有合法返回值
        engine.execute_action("B", action[0], action[1], action[2])
        self.assertEqual(engine.current_turn, "A")
        self.assertFalse(engine.is_chained("B"))

    def test_ai_respects_locked_turn_and_survives_long_search(self):
        engine = build_engine(a1=35, a2=38, b1=25, b2=29, hp_a=5, hp_b=5)
        for _ in range(6):
            if engine.is_game_over():
                break
            ai = create_ai("hell", engine.current_turn, seed=11)
            action = ai.choose_action(engine)
            engine.execute_action(engine.current_turn, action[0], action[1], action[2])


class TestDeterminism(unittest.TestCase):
    """相同种子 + 相同局面 -> 相同决策。"""

    def test_same_seed_same_action(self):
        for difficulty in ALL_DIFFICULTIES:
            engine = build_engine(a1=16, a2=29, b1=35, b2=38)
            first = create_ai(difficulty, "A", seed=42).choose_action(engine)
            second = create_ai(difficulty, "A", seed=42).choose_action(engine)
            self.assertEqual(first, second, difficulty)

    def test_clone_is_independent(self):
        engine = build_engine(a1=16, a2=29, b1=35, b2=38)
        clone = engine.clone()
        clone.execute_action("A", 1, 1, "+")
        clone.surrender("B")
        self.assertNotEqual(snapshot(engine), snapshot(clone))
        self.assertFalse(engine.is_game_over())
        self.assertEqual(engine.current_turn, "A")


class TestStrength(unittest.TestCase):
    """强度梯度：至少保证"普通"明显强于"简单"。"""

    def test_normal_beats_easy_over_many_games(self):
        wins = {"A": 0, "B": 0, None: 0}
        games = 30
        for index in range(games):
            # 每局交换先后手，避免先手优势污染结论
            if index % 2 == 0:
                winner, _ = play_match("normal", "easy", index, index + 1)
                normal_won = winner == "A"
            else:
                winner, _ = play_match("easy", "normal", index, index + 1)
                normal_won = winner == "B"
            wins[winner] += 1
            self.assertIsNotNone(winner, "对局不应超时")
            if normal_won:
                wins["normal"] = wins.get("normal", 0) + 1

        self.assertGreaterEqual(
            wins.get("normal", 0), games * 0.7, f"普通难度胜率过低：{wins}"
        )

    def test_hell_beats_easy_over_many_games(self):
        normal_wins = 0
        games = 10
        for index in range(games):
            winner, _ = play_match("hell", "easy", index, index + 1)
            if winner == "A":
                normal_wins += 1
        self.assertGreaterEqual(normal_wins, games * 0.8, "地狱难度应当稳定压制简单")


if __name__ == "__main__":
    unittest.main(verbosity=2)
