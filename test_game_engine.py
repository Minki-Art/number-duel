"""GameEngine 的完整单元测试（unittest）。

覆盖：加法 / 减法 / 七大技能 / 防刷机制 / 血量边界 / 游戏结束 / 参数校验 /
渲染数据 / 三大死局回归（全零、奇偶锁定、空过僵持）。

运行方式::

    python -m unittest -v test_game_engine

关于触发技能检查的两种手法
--------------------------
引擎规定"空过要挨打"，所以**不能再用"守方个位为 0"来让数字保持不变**（那正是空过）。
测试改用两种干净手法：

1. :func:`check` —— 直接调用 ``engine.check_skills()``。适合不依赖"行动方"的技能
   （回血 / 钻头 / 旋镖 / 恭喜发财 / 糖葫芦）。
2. :func:`poke` —— 让**另一张牌**做一次必定改变数值、且不触发任何技能的操作，
   从而在不改动目标槽位的前提下走完整的 ``execute_action()`` 流程。
   适合依赖"行动方"的技能（锁链 / 巴掌）与流程集成测试。
"""

from __future__ import annotations

import unittest

from game_engine import GameEngine, GameOverError

# ------------------------------------------------------------------ 测试工具
#: poke() 使用的"诱饵守方槽"数值：个位固定为 4
DECOY_FOE = 14
#: poke() 让我方另一张牌在 5 与 9 之间往复（都 100% 不触发任何技能）
DECOY_CYCLE = (5, 9)


def build_engine(
    a1: int = 1,
    a2: int = 1,
    b1: int = 1,
    b2: int = 1,
    hp_a: int = 3,
    hp_b: int = 3,
    turn: str = "A",
) -> GameEngine:
    """构造一个指定初始状态的引擎，并清空技能防刷记录（仅用于测试）。"""
    engine = GameEngine()
    engine.A1 = a1
    engine.A2 = a2
    engine.B1 = b1
    engine.B2 = b2
    engine.hp_a = hp_a
    engine.hp_b = hp_b
    engine.current_turn = turn
    engine.reset_skill_state()
    return engine


def check(engine: GameEngine):
    """直接触发一次技能检查（不经过 execute_action）。"""
    return engine.check_skills()


def poke(engine: GameEngine, player: str, keep_slot: int = 1):
    """用另一张牌做一次"必定改变数值"的无害操作，触发完整行动流程。

    约定：守方 1 号槽的个位固定为 4；我方另一张牌在 5 与 9 之间往复，
    因此"加减 4"必定改变数值，且 5 / 9 都不会触发任何技能。

    :param keep_slot: 希望保持不变的槽位（技能触发位）
    """
    engine.current_turn = player
    other_slot = 2 if keep_slot == 1 else 1
    value = getattr(engine, f"{player}{other_slot}")
    operation = "+" if value == DECOY_CYCLE[0] else "-"
    return engine.execute_action(player, other_slot, 1, operation)


def nudge(engine: GameEngine, player: str, slot: int, up: bool = True):
    """让某个槽位的数字真正变化一次（守方 1 号槽的个位固定为 4，所以按 ±4 变动）。

    防刷判定看的是"这个数字有没有变化过"，直接改属性是绕不过去的，
    所以这类测试必须真的走一次 ``execute_action``。
    """
    engine.current_turn = player
    return engine.execute_action(player, slot, 1, "+" if up else "-")


def joined(events) -> str:
    """把事件日志拼成一个字符串，便于做包含性断言。"""
    return " | ".join(events)


class TestInitialState(unittest.TestCase):
    """初始状态与回合流转。"""

    def test_initial_values(self):
        engine = GameEngine()
        self.assertEqual((engine.A1, engine.A2, engine.B1, engine.B2), (1, 1, 1, 1))
        self.assertEqual((engine.hp_a, engine.hp_b), (3, 3))
        self.assertEqual(engine.current_turn, "A")

    def test_fresh_game_not_over(self):
        engine = GameEngine()
        self.assertFalse(engine.is_game_over())
        self.assertIsNone(engine.get_winner())

    def test_turn_switches_after_each_action(self):
        engine = GameEngine()
        engine.execute_action("A", 1, 1, "+")
        self.assertEqual(engine.current_turn, "B")
        engine.execute_action("B", 1, 1, "+")
        self.assertEqual(engine.current_turn, "A")


class TestAdditionRule(unittest.TestCase):
    """加法：res = 攻方值 + (守方值 % 10)；res >= 100 时 result = 10 + (res % 10)。"""

    def test_add_uses_defender_ones_digit_only(self):
        # 需求方修订后：86 + (34 % 10 = 4) = 90
        # （原文档示例 86 + 34 = 120 -> 10 随加法规则修订一并作废）
        engine = build_engine(a1=86, a2=1, b1=34, b2=1)
        engine.execute_action("A", 1, 1, "+")
        self.assertEqual(engine.A1, 90)
        self.assertEqual(engine.B1, 34)

    def test_add_result_written_back_to_attacker_slot_only(self):
        engine = build_engine(a1=12, a2=7, b1=25, b2=9)
        engine.execute_action("A", 2, 1, "+")
        self.assertEqual(engine.A2, 7 + 5)
        self.assertEqual(engine.A1, 12)
        self.assertEqual(engine.B1, 25)
        self.assertEqual(engine.B2, 9)

    def test_add_overflow_uses_ten_plus_ones(self):
        engine = build_engine(a1=95, a2=1, b1=19, b2=1)
        engine.execute_action("A", 1, 1, "+")
        # 95 + 9 = 104 -> 10 + (104 % 10) = 14
        self.assertEqual(engine.A1, 14)

    def test_add_result_exactly_one_hundred(self):
        engine = build_engine(a1=91, a2=1, b1=19, b2=1)
        engine.execute_action("A", 1, 1, "+")
        # 91 + 9 = 100 -> 10 + 0 = 10
        self.assertEqual(engine.A1, 10)


class TestSubtractionRule(unittest.TestCase):
    """减法：result = max(1, 攻方值 - (守方值 % 10))（下限为 1，见 SUB_FLOOR）。"""

    def test_subtract_defender_ones_digit(self):
        engine = build_engine(a1=15, a2=18, b1=18, b2=4)
        engine.execute_action("A", 1, 1, "-")
        # 15 - (18 % 10) = 7
        self.assertEqual(engine.A1, 7)
        self.assertEqual(engine.B1, 18)

    def test_subtract_floor_is_one(self):
        engine = build_engine(a1=15, a2=18, b1=18, b2=4, turn="B", hp_b=5)
        engine.execute_action("B", 2, 2, "-")
        # 4 - (18 % 10) = -4 -> 下限 1
        # 注：需求文档原示例为 "4 - 8 -> 0"，因奇偶锁定问题把下限提升为 1，该示例随之修订
        self.assertEqual(engine.B2, 1)
        self.assertEqual(engine.A2, 18)

    def test_subtract_never_produces_zero(self):
        engine = build_engine(a1=1, a2=1, b1=9, b2=9, hp_a=5, hp_b=5, turn="A")
        for turn in range(40):
            if engine.is_game_over():
                break
            engine.execute_action(engine.current_turn, (turn % 2) + 1, 1, "-")
            for value in (engine.A1, engine.A2, engine.B1, engine.B2):
                self.assertGreaterEqual(value, 1, "减法不允许把数字压到 0")

    def test_subtract_result_written_back_to_attacker_slot_only(self):
        engine = build_engine(a1=15, a2=18, b1=18, b2=4)
        engine.execute_action("A", 1, 1, "-")
        self.assertEqual(engine.A2, 18)
        self.assertEqual(engine.B1, 18)
        self.assertEqual(engine.B2, 4)


class TestIdlePenalty(unittest.TestCase):
    """空过分级惩罚：第 1 次警告，每满 2 次扣 1 血，连续 4 次直接判负。"""

    def _idle_once(self, engine: GameEngine, player: str = "A"):
        """做一次必定空过的操作：让攻击方加/减守方个位 0。"""
        engine.current_turn = player
        return engine.execute_action(player, 1, 1, "+")

    def test_first_idle_only_warns(self):
        # B1 = 30 个位为 0 -> A1 = 47 + 0 = 47，数字毫无变化
        engine = build_engine(a1=47, a2=1, b1=30, b2=1, hp_a=5)
        events = self._idle_once(engine)
        self.assertEqual(engine.A1, 47, "空过不应该改变数字")
        self.assertEqual(engine.hp_a, 5, "第 1 次空过只警告，不扣血")
        self.assertEqual(engine.get_idle_streak("A"), 1)
        self.assertIn("空过", joined(events))

    def test_second_idle_costs_one_hp(self):
        engine = build_engine(a1=47, a2=1, b1=30, b2=1, hp_a=5)
        self._idle_once(engine)
        events = self._idle_once(engine)
        self.assertEqual(engine.hp_a, 4, "第 2 次空过扣 1 点血")
        self.assertIn("受到1点伤害", joined(events))

    def test_third_idle_only_warns_again(self):
        engine = build_engine(a1=47, a2=1, b1=30, b2=1, hp_a=5)
        for _ in range(3):
            self._idle_once(engine)
        self.assertEqual(engine.hp_a, 4, "第 3 次空过不扣血")
        self.assertEqual(engine.get_idle_streak("A"), 3)

    def test_fourth_consecutive_idle_loses_the_game(self):
        engine = build_engine(a1=47, a2=1, b1=30, b2=1, hp_a=5, hp_b=5)
        for _ in range(3):
            self._idle_once(engine)
        self.assertFalse(engine.is_game_over())
        events = self._idle_once(engine)
        self.assertTrue(engine.is_game_over(), "连续 4 次空过直接判负")
        self.assertEqual(engine.get_winner(), "B")
        self.assertIn("认输", joined(events))

    def test_valid_move_resets_the_idle_streak(self):
        engine = build_engine(a1=47, a2=1, b1=30, b2=1, hp_a=5)
        self._idle_once(engine)
        self._idle_once(engine)
        self.assertEqual(engine.get_idle_streak("A"), 2)

        # 换一个会真正改变数字的操作
        engine.current_turn = "A"
        events = engine.execute_action("A", 1, 2, "+")     # B2 = 1 -> A1 = 48
        self.assertEqual(engine.A1, 48)
        self.assertEqual(engine.get_idle_streak("A"), 0)
        self.assertNotIn("空过", joined(events))

    def test_changing_action_does_not_warn(self):
        engine = build_engine(a1=47, a2=1, b1=34, b2=1, hp_a=5)
        events = engine.execute_action("A", 1, 1, "+")
        self.assertEqual(engine.A1, 51)
        self.assertEqual(engine.hp_a, 5)
        self.assertNotIn("空过", joined(events))

    def test_idle_streak_is_tracked_per_player(self):
        engine = build_engine(a1=30, a2=1, b1=47, b2=1, hp_a=5, hp_b=5, turn="B")
        self._idle_once(engine, "B")
        self.assertEqual(engine.get_idle_streak("B"), 1)
        self.assertEqual(engine.get_idle_streak("A"), 0)

    def test_chain_skip_is_not_an_idle_turn(self):
        # 被锁链跳过是被动的，不应计入空过
        engine = build_engine(a1=39, a2=25, b1=14, b2=11, hp_a=5, hp_b=5)
        engine.execute_action("A", 2, 1, "+")     # A2 -> 29，触发锁链
        self.assertTrue(engine.is_chained("B"))
        hp_b_before = engine.hp_b
        events = engine.execute_action("B", 1, 1, "+")
        self.assertIn("无法行动", joined(events))
        self.assertNotIn("空过", joined(events))
        self.assertEqual(engine.get_idle_streak("B"), 0)
        self.assertEqual(engine.hp_b, hp_b_before)

    def test_forced_idle_can_end_the_game(self):
        # 双方数字个位全是 0 -> A 只能一直空过 -> 连续 4 次后判负
        engine = build_engine(a1=20, a2=30, b1=10, b2=40, hp_a=5, hp_b=5, turn="A")
        for _ in range(4):
            if engine.is_game_over():
                break
            self._idle_once(engine, "A")
            if not engine.is_game_over():
                self._idle_once(engine, "B")
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "B")

    def test_get_idle_streak_invalid_player(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.get_idle_streak("C")


class TestHealSkill(unittest.TestCase):
    """技能 1：个位数为 6 -> 该数字所属玩家回复 1 点 HP。"""

    def test_ones_six_heals_owner(self):
        engine = build_engine(a1=16, a2=1, b1=14, b2=11)
        events = check(engine)
        self.assertEqual(engine.hp_a, 4)
        self.assertIn("回血", joined(events))

    def test_heal_capped_at_five(self):
        engine = build_engine(a1=16, a2=1, b1=14, b2=11, hp_a=5)
        check(engine)
        self.assertEqual(engine.hp_a, 5)

    def test_heal_applies_to_owner_not_opponent(self):
        engine = build_engine(a1=12, a2=1, b1=16, b2=11)
        check(engine)
        self.assertEqual(engine.hp_b, 4)
        self.assertEqual(engine.hp_a, 3)

    def test_heal_anti_farm_requires_value_change(self):
        engine = build_engine(a1=16, a2=5, b1=14, b2=11)
        poke(engine, "A", keep_slot=1)
        self.assertEqual(engine.hp_a, 4)

        poke(engine, "A", keep_slot=1)      # A1 仍为 16 -> 被防刷拦截
        self.assertEqual(engine.hp_a, 4)

    def test_heal_retriggers_after_value_change(self):
        # A1 先变成 12、再变回 16 -> 中间真的变化过，应该能再次触发
        engine = build_engine(a1=16, a2=5, b1=14, b2=11, hp_a=1)
        poke(engine, "A", keep_slot=1)              # A2 变化、A1 保持 16 -> 首次触发
        self.assertEqual(engine.hp_a, 2)

        nudge(engine, "A", 1, up=False)             # A1: 16 -> 12
        self.assertEqual(engine.A1, 12)

        events = nudge(engine, "A", 1, up=True)     # A1: 12 -> 16 -> 再次触发
        self.assertEqual(engine.A1, 16)
        self.assertIn("回血", joined(events))
        self.assertEqual(engine.hp_a, 3)

    def test_heal_retriggers_on_value_cycle(self):
        """玩家反馈的场景：A1 在 26 与 20 之间往复，每次回到 26 都应能再次触发回血。

        旧实现把"触发过的数值"拉黑，导致回到 26 时被永久拦截；
        新实现只看"A1 自上次触发以来有没有变化过"，所以往复同样能刷新。
        """
        engine = build_engine(a1=26, a2=5, b1=16, b2=11, hp_a=1)

        events = check(engine)                      # A1 个位 6 -> 首次触发
        self.assertEqual(engine.hp_a, 2)
        self.assertIn("回血", joined(events))

        nudge(engine, "A", 1, up=False)             # A1: 26 -> 20
        self.assertEqual(engine.A1, 20)

        events = nudge(engine, "A", 1, up=True)     # A1: 20 -> 26 -> 再次触发
        self.assertEqual(engine.A1, 26)
        self.assertIn("回血", joined(events))
        self.assertEqual(engine.hp_a, 3)

        nudge(engine, "A", 1, up=False)             # A1: 26 -> 20
        events = nudge(engine, "A", 1, up=True)     # A1: 20 -> 26 -> 还能再触发
        self.assertIn("回血", joined(events))
        self.assertEqual(engine.hp_a, 4)

    def test_heal_triggered_through_execute_action(self):
        # 集成验证：走完整行动流程也能触发技能
        engine = build_engine(a1=16, a2=5, b1=14, b2=11)
        events = poke(engine, "A", keep_slot=1)
        self.assertIn("回血", joined(events))
        self.assertEqual(engine.hp_a, 4)


class TestDrillSkill(unittest.TestCase):
    """技能 5：个位数为 7 -> 对对手造成 1 点伤害。"""

    def test_ones_seven_damages_opponent(self):
        engine = build_engine(a1=17, a2=1, b1=14, b2=11)
        events = check(engine)
        self.assertEqual(engine.hp_b, 2)
        self.assertIn("钻头", joined(events))

    def test_drill_works_for_b_side(self):
        engine = build_engine(a1=14, a2=11, b1=17, b2=1)
        check(engine)
        self.assertEqual(engine.hp_a, 2)

    def test_drill_anti_farm_requires_value_change(self):
        engine = build_engine(a1=17, a2=5, b1=14, b2=11)
        poke(engine, "A", keep_slot=1)
        self.assertEqual(engine.hp_b, 2)

        events = poke(engine, "A", keep_slot=1)   # A1 仍为 17 -> 拦截
        self.assertEqual(engine.hp_b, 2)
        self.assertNotIn("钻头", joined(events))


class TestBoomerangSkill(unittest.TestCase):
    """技能 2：两个数字个位数都是 8 -> 固定 2 点伤害（不再区分重击档位）。"""

    def test_a_double_eight_damages_b(self):
        engine = build_engine(a1=18, a2=28)
        events = check(engine)
        self.assertEqual(engine.hp_b, 1)
        self.assertIn("旋镖", joined(events))
        self.assertIn("受到2点伤害", joined(events))

    def test_b_double_eight_damages_a(self):
        engine = build_engine(a1=1, a2=1, b1=18, b2=28)
        events = check(engine)
        self.assertEqual(engine.hp_a, 1)
        self.assertIn("旋镖", joined(events))
        self.assertIn("受到2点伤害", joined(events))

    def test_double_eight_damage_is_two_regardless_of_value_range(self):
        """18/28、38/28、38/38 三种组合伤害都是 2。

        旧规则里只有"两个数字都 >= 30"才是 2 点，另外两种只有 1 点；
        现在取消该档位，三种组合统一 2 点。
        """
        for value_1, value_2 in ((18, 28), (38, 28), (38, 38)):
            with self.subTest(values=(value_1, value_2)):
                engine = build_engine(a1=value_1, a2=value_2, hp_b=5)
                events = check(engine)
                self.assertEqual(engine.hp_b, 3)
                self.assertIn("受到2点伤害", joined(events))

    def test_boomerang_anti_farm_requires_both_numbers_changed(self):
        # A1 = 18 已就位，A2 = 24 -> 加 4 变 28，凑成双个位 8 触发旋镖
        engine = build_engine(a1=18, a2=24, b1=14, b2=11, hp_b=5)
        nudge(engine, "A", 2, up=True)
        self.assertEqual(engine.A2, 28)
        self.assertEqual(engine.hp_b, 3)

        # 只有 A2 在动：回到 24 再回到 28，A1 一直没变过 -> 拦截
        nudge(engine, "A", 2, up=False)
        events = nudge(engine, "A", 2, up=True)
        self.assertEqual(engine.A2, 28)
        self.assertEqual(engine.hp_b, 3)
        self.assertNotIn("旋镖", joined(events))

        # 让 A1 也真正变化一次（18 -> 14 -> 18），两个数字都刷新过 -> 再次触发
        nudge(engine, "A", 1, up=False)
        self.assertEqual(engine.hp_b, 3)
        events = nudge(engine, "A", 1, up=True)
        self.assertEqual(engine.A1, 18)
        self.assertIn("旋镖", joined(events))
        self.assertEqual(engine.hp_b, 1)

    def test_boomerang_triggered_through_execute_action(self):
        # A2 = 24 -> 加上守方个位 4 变成 28，与 A1 = 18 组成双 8
        engine = build_engine(a1=18, a2=24, b1=14, b2=11)
        events = engine.execute_action("A", 2, 1, "+")
        self.assertEqual(engine.A2, 28)
        self.assertIn("旋镖", joined(events))
        self.assertEqual(engine.hp_b, 1)


class TestFortuneSkill(unittest.TestCase):
    """技能 3：两个数字都 >= 30，个位数分别是 5 和 0 -> 对对手 3 点伤害。"""

    def test_fortune_triggers_and_deals_three(self):
        engine = build_engine(a1=35, a2=30, hp_b=5)
        events = check(engine)
        self.assertEqual(engine.hp_b, 2)
        self.assertIn("恭喜发财", joined(events))

    def test_fortune_digit_order_does_not_matter(self):
        engine = build_engine(a1=30, a2=35, hp_b=5)
        check(engine)
        self.assertEqual(engine.hp_b, 2)

    def test_fortune_works_for_b_side(self):
        engine = build_engine(a1=1, a2=1, b1=35, b2=30, hp_a=5)
        check(engine)
        self.assertEqual(engine.hp_a, 2)

    def test_fortune_requires_both_at_least_thirty(self):
        engine = build_engine(a1=25, a2=30, hp_b=5)
        events = check(engine)
        self.assertEqual(engine.hp_b, 5)
        self.assertNotIn("恭喜发财", joined(events))

    def test_fortune_requires_ones_five_and_zero(self):
        engine = build_engine(a1=35, a2=40, hp_b=5)
        events = check(engine)
        self.assertEqual(engine.hp_b, 2)
        self.assertIn("恭喜发财", joined(events))

        engine = build_engine(a1=35, a2=41, hp_b=5)
        events = check(engine)
        self.assertEqual(engine.hp_b, 5)
        self.assertNotIn("恭喜发财", joined(events))

    def test_fortune_anti_farm_sequence(self):
        """文档场景的等价版本：35/30 触发 -> 只有 A2 在动时不触发 -> 两个都动过再触发。"""
        engine = build_engine(a1=35, a2=26, b1=14, b2=11, hp_b=5)

        events = nudge(engine, "A", 2, up=True)     # A2: 26 -> 30，凑成 35/30
        self.assertEqual(engine.A2, 30)
        self.assertEqual(engine.hp_b, 2)
        self.assertIn("恭喜发财", joined(events))

        # 只有 A2 在动：30 -> 26 -> 30，A1 一直没变过 -> 拦截
        nudge(engine, "A", 2, up=False)
        events = nudge(engine, "A", 2, up=True)
        self.assertEqual(engine.A2, 30)
        self.assertEqual(engine.hp_b, 2)
        self.assertNotIn("恭喜发财", joined(events))

        # 让 A1 也真正变化一次（35 -> 31 -> 35）-> 再次触发
        nudge(engine, "A", 1, up=False)
        self.assertEqual(engine.hp_b, 2)
        events = nudge(engine, "A", 1, up=True)
        self.assertEqual(engine.A1, 35)
        self.assertIn("恭喜发财", joined(events))
        self.assertEqual(engine.hp_b, 0)


class TestCandySkill(unittest.TestCase):
    """技能 6：两个数字都 >= 15，个位数分别是 8 和 1 -> 回复 2 点 HP。"""

    def test_candy_heals_two(self):
        engine = build_engine(a1=18, a2=21)
        events = check(engine)
        self.assertEqual(engine.hp_a, 5)
        self.assertIn("糖葫芦", joined(events))

    def test_candy_digit_order_does_not_matter(self):
        engine = build_engine(a1=21, a2=18, hp_a=2)
        check(engine)
        self.assertEqual(engine.hp_a, 4)

    def test_candy_capped_at_five(self):
        engine = build_engine(a1=18, a2=21, hp_a=5)
        check(engine)
        self.assertEqual(engine.hp_a, 5)

    def test_candy_requires_both_at_least_fifteen(self):
        engine = build_engine(a1=11, a2=18)
        events = check(engine)
        self.assertEqual(engine.hp_a, 3)
        self.assertNotIn("糖葫芦", joined(events))

    def test_candy_anti_farm_requires_both_numbers_changed(self):
        engine = build_engine(a1=18, a2=21, hp_a=1)
        check(engine)
        self.assertEqual(engine.hp_a, 3)

        engine.A2 = 31                     # 只有一个数字变化
        events = check(engine)
        self.assertEqual(engine.hp_a, 3)
        self.assertNotIn("糖葫芦", joined(events))


class TestSlapSkill(unittest.TestCase):
    """技能 4：两个数字都 >= 15 且个位都是 5；档位取两个十位中较小者。"""

    def test_slap_tier_one_triggers_once(self):
        # B1 = 15（十位1）、B2 = 11 -> 加 4 变 15，min(1,1) = 1 档
        engine = build_engine(a1=14, a2=11, b1=15, b2=11, hp_a=5, turn="B")
        events = engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.B2, 15)
        self.assertEqual(engine.hp_a, 4)
        self.assertIn("巴掌", joined(events))
        self.assertEqual(engine.get_slap_turns_left("B"), 0)

    def test_slap_tier_two_ticks_twice(self):
        # B1 = 25（十位2）、B2 = 21 -> 25，min(2,2) = 2 档
        engine = build_engine(a1=14, a2=11, b1=25, b2=21, hp_a=5, turn="B")
        engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.hp_a, 4)
        self.assertEqual(engine.get_slap_turns_left("B"), 1)

        engine.current_turn = "B"
        engine.execute_action("B", 2, 1, "+")     # B2 = 29，造成第 2 次伤害
        self.assertEqual(engine.hp_a, 3)
        self.assertEqual(engine.get_slap_turns_left("B"), 0)

    def test_slap_tier_three_ticks_three_times(self):
        # B1 = 35（十位3）、B2 = 41 -> 45，min(3,4) = 3 档
        engine = build_engine(a1=14, a2=11, b1=35, b2=41, hp_a=5, turn="B")
        for expected_hp, expected_left in ((4, 2), (3, 1), (2, 0)):
            engine.current_turn = "B"
            engine.execute_action("B", 2, 1, "+")
            self.assertEqual(engine.hp_a, expected_hp)
            self.assertEqual(engine.get_slap_turns_left("B"), expected_left)

    def test_slap_tier_uses_smaller_tens_digit(self):
        # B1 = 15（十位1）、B2 = 21 -> 25（十位2）-> 取较小者 1 档
        engine = build_engine(a1=14, a2=11, b1=15, b2=21, hp_a=5, turn="B")
        engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.hp_a, 4)
        self.assertEqual(engine.get_slap_turns_left("B"), 0)

    def test_slap_does_not_refresh_while_active(self):
        engine = build_engine(a1=14, a2=11, b1=25, b2=21, hp_a=5, turn="B")
        engine.execute_action("B", 2, 1, "+")     # 触发，剩余 1
        self.assertEqual(engine.get_slap_turns_left("B"), 1)

        engine.current_turn = "B"
        engine.execute_action("B", 2, 1, "+")     # 只结算 1 次，不刷新
        self.assertEqual(engine.hp_a, 3)
        self.assertEqual(engine.get_slap_turns_left("B"), 0)

    def test_slap_cannot_retrigger_while_numbers_unchanged(self):
        engine = build_engine(a1=14, a2=11, b1=35, b2=41, hp_a=5, turn="B")
        for _ in range(3):
            engine.current_turn = "B"
            engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.hp_a, 2)

        engine.current_turn = "B"
        # 用减法让 B2 从 53 落到 49（个位 9），避免踩到个位 7 触发钻头
        events = engine.execute_action("B", 2, 1, "-")
        self.assertEqual(engine.B2, 49)
        self.assertEqual(engine.hp_a, 2)
        self.assertNotIn("巴掌", joined(events))

    def test_slap_retriggers_after_duration_and_number_change(self):
        engine = build_engine(a1=14, a2=11, b1=35, b2=41, hp_a=5, turn="B")
        for _ in range(3):
            engine.current_turn = "B"
            engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.hp_a, 2)
        self.assertEqual(engine.get_slap_turns_left("B"), 0)

        # 相对上次触发的 (35, 45) 两个数字都发生变化，且落点仍是双个位 5
        engine.B1 = 41
        engine.B2 = 65
        engine.current_turn = "B"
        engine.execute_action("B", 1, 1, "+")
        self.assertEqual(engine.B1, 45)
        self.assertEqual(engine.hp_a, 1)
        self.assertEqual(engine.get_slap_turns_left("B"), 2)


class TestChainSkill(unittest.TestCase):
    """技能 7：行动方两个数字都 >= 20 且个位都是 9 -> 对手下一回合被跳过。"""

    def _chain_engine(self):
        # A1 = 39 已经就位，A2 = 25 -> 加 4 变 29 -> 双 9 且都 >= 20
        return build_engine(a1=39, a2=25, b1=14, b2=11, hp_a=5, hp_b=5)

    def test_chain_marks_opponent(self):
        engine = self._chain_engine()
        events = engine.execute_action("A", 2, 1, "+")
        self.assertEqual(engine.A2, 29)
        self.assertIn("锁链", joined(events))
        self.assertTrue(engine.is_chained("B"))
        self.assertFalse(engine.is_chained("A"))

    def test_chained_player_skips_turn_without_action(self):
        engine = self._chain_engine()
        engine.execute_action("A", 2, 1, "+")
        self.assertEqual(engine.current_turn, "B")

        before = (engine.A1, engine.A2, engine.B1, engine.B2, engine.hp_a, engine.hp_b)
        events = engine.execute_action("B", 1, 1, "+")
        after = (engine.A1, engine.A2, engine.B1, engine.B2, engine.hp_a, engine.hp_b)

        self.assertIn("无法行动", joined(events))
        self.assertEqual(before, after)
        self.assertFalse(engine.is_chained("B"))
        self.assertEqual(engine.current_turn, "A")

    def test_chained_player_can_act_again_after_skip(self):
        engine = self._chain_engine()
        engine.execute_action("A", 2, 1, "+")
        engine.execute_action("B", 1, 1, "+")      # 被跳过

        engine.current_turn = "B"
        events = engine.execute_action("B", 1, 1, "+")
        self.assertNotIn("无法行动", joined(events))
        # B1 = 14 + (A1 = 39 的个位 9) = 23，说明行动真实生效
        self.assertEqual(engine.B1, 23)

    def test_chain_requires_both_numbers_at_least_twenty(self):
        # A1 = 19 < 20，即使个位是 9 也不能触发
        engine = build_engine(a1=19, a2=25, b1=14, b2=11, hp_a=5, hp_b=5)
        events = engine.execute_action("A", 2, 1, "+")
        self.assertNotIn("锁链", joined(events))
        self.assertFalse(engine.is_chained("B"))

    def test_chain_anti_infinite_requires_both_numbers_changed(self):
        engine = self._chain_engine()
        events = engine.execute_action("A", 2, 1, "+")     # A2: 25 -> 29，触发锁链
        self.assertEqual(engine.A2, 29)
        self.assertIn("锁链", joined(events))

        # 只有 A2 在动：29 -> 25 -> 29，A1 一直没变过 -> 拦截
        nudge(engine, "A", 2, up=False)
        events = nudge(engine, "A", 2, up=True)
        self.assertEqual(engine.A2, 29)
        self.assertNotIn("锁链", joined(events))

        # 让 A1 也真正变化一次（39 -> 35 -> 39）-> 再次触发
        nudge(engine, "A", 1, up=False)
        events = nudge(engine, "A", 1, up=True)
        self.assertEqual(engine.A1, 39)
        self.assertIn("锁链", joined(events))

    def test_is_chained_invalid_player(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.is_chained("C")


class TestHpBounds(unittest.TestCase):
    """血量必须限制在 0 ~ 5。"""

    def test_hp_never_below_zero(self):
        engine = build_engine(a1=17, a2=1, b1=14, b2=11, hp_b=1)
        check(engine)
        self.assertEqual(engine.hp_b, 0)

    def test_fortune_damage_clamped_at_zero(self):
        engine = build_engine(a1=35, a2=30, hp_b=2)
        check(engine)
        self.assertEqual(engine.hp_b, 0)

    def test_hp_never_above_five(self):
        engine = build_engine(a1=16, a2=1, hp_a=5)
        check(engine)
        self.assertEqual(engine.hp_a, 5)

    def test_slap_damage_clamped_at_zero(self):
        engine = build_engine(a1=14, a2=11, b1=35, b2=41, hp_a=1, turn="B")
        engine.execute_action("B", 2, 1, "+")
        self.assertEqual(engine.hp_a, 0)


class TestGameOver(unittest.TestCase):
    """游戏结束判定、胜者判定与投降。"""

    def test_zero_hp_ends_game(self):
        engine = build_engine(a1=35, a2=30, hp_b=3)
        check(engine)
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "A")

    def test_zero_hp_a_ends_game_with_b_winner(self):
        engine = build_engine(hp_a=0)
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "B")

    def test_execute_action_raises_when_game_over(self):
        engine = build_engine(hp_a=0)
        with self.assertRaises(GameOverError) as ctx:
            engine.execute_action("B", 1, 1, "+")
        self.assertEqual(ctx.exception.winner, "B")
        self.assertIn("恭喜获胜方 B", str(ctx.exception))
        # GameOverError 同时也是 ValueError，兼容按 ValueError 捕获的调用方
        self.assertIsInstance(ctx.exception, ValueError)

    def test_execute_action_raises_when_b_dead(self):
        engine = build_engine(hp_b=0)
        with self.assertRaises(GameOverError) as ctx:
            engine.execute_action("A", 1, 1, "+")
        self.assertEqual(ctx.exception.winner, "A")

    def test_surrender_a_gives_win_to_b(self):
        engine = GameEngine()
        engine.surrender("A")
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "B")

    def test_surrender_b_gives_win_to_a(self):
        engine = GameEngine()
        engine.surrender("B")
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "A")

    def test_surrender_invalid_player(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.surrender("C")
        self.assertFalse(engine.is_game_over())

    def test_surrender_is_idempotent(self):
        engine = GameEngine()
        engine.surrender("A")
        engine.surrender("B")              # 游戏已结束，不再改变结果
        self.assertEqual(engine.get_winner(), "B")

    def test_execute_action_raises_after_surrender(self):
        engine = GameEngine()
        engine.surrender("A")
        with self.assertRaises(GameOverError):
            engine.execute_action("B", 1, 1, "+")


class TestValidation(unittest.TestCase):
    """参数校验：非法参数必须抛出明确的 ValueError。"""

    def test_invalid_attacker(self):
        engine = GameEngine()
        for bad in ("C", "a", "", None):
            with self.assertRaises(ValueError):
                engine.execute_action(bad, 1, 1, "+")

    def test_invalid_attacker_slot(self):
        engine = GameEngine()
        for bad in (0, 3, -1, None):
            with self.assertRaises(ValueError):
                engine.execute_action("A", bad, 1, "+")

    def test_invalid_defender_slot(self):
        engine = GameEngine()
        for bad in (0, 3, -1, None):
            with self.assertRaises(ValueError):
                engine.execute_action("A", 1, bad, "+")

    def test_invalid_operation(self):
        engine = GameEngine()
        for bad in ("*", "/", "", "add", None):
            with self.assertRaises(ValueError):
                engine.execute_action("A", 1, 1, bad)

    def test_cannot_act_out_of_turn(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.execute_action("B", 1, 1, "+")
        self.assertEqual(engine.current_turn, "A")

    def test_invalid_call_leaves_state_untouched(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.execute_action("B", 1, 1, "+")
        self.assertEqual(engine.current_turn, "A")
        self.assertEqual((engine.A1, engine.A2, engine.B1, engine.B2), (1, 1, 1, 1))
        self.assertEqual((engine.hp_a, engine.hp_b), (3, 3))

    def test_get_slap_turns_left_invalid_player(self):
        engine = GameEngine()
        with self.assertRaises(ValueError):
            engine.get_slap_turns_left("C")


class TestAntiFarmMechanism(unittest.TestCase):
    """防刷机制：数字未发生状态刷新时不允许再次触发技能。"""

    def test_reset_skill_state_clears_records(self):
        engine = build_engine(a1=16, a2=1, hp_a=3)
        check(engine)
        self.assertEqual(engine.hp_a, 4)

        engine.reset_skill_state()
        check(engine)
        self.assertEqual(engine.hp_a, 5)

    def test_write_back_same_value_does_not_refresh_skill(self):
        # 文档场景：B1 = 16 触发回血后，数字仍是 16 时不能再次触发
        engine = build_engine(a1=14, a2=11, b1=16, b2=7, hp_b=3)
        check(engine)
        self.assertEqual(engine.hp_b, 4)

        check(engine)
        self.assertEqual(engine.hp_b, 4)

    def test_single_number_skill_records_are_per_slot(self):
        # A1 与 A2 各自独立记录防刷状态，但同一次检查里每个技能最多触发一次
        engine = build_engine(a1=16, a2=26, hp_a=1)

        events = check(engine)
        self.assertEqual(events.count("A触发回血，回复1点HP"), 1)
        self.assertEqual(engine.hp_a, 2)

        events = check(engine)              # A1 未变化被拦截，A2 首次判定命中
        self.assertEqual(events.count("A触发回血，回复1点HP"), 1)
        self.assertEqual(engine.hp_a, 3)

        events = check(engine)              # 两个数值都没再变化 -> 全部拦截
        self.assertEqual(events, [])
        self.assertEqual(engine.hp_a, 3)


class TestDeadlockRegression(unittest.TestCase):
    """回归测试：三大死局（全零 / 奇偶锁定 / 空过僵持）。

    加法与减法都是"偶数 ∓ 偶数 = 偶数"，若减法下限为 0，四个数字一旦全部变成偶数就
    再也回不到奇数（全偶数是闭集），此时 5 个依赖奇数个位的技能永久失效、对局无法结束；
    四个数字全为 0 时更是任何操作都空转；而守方个位为 0 时攻方只能"空过"。
    """

    def test_sub_floor_is_odd(self):
        # 下限必须是奇数，否则无法打破全偶数闭集
        self.assertEqual(GameEngine.SUB_FLOOR % 2, 1)

    def test_all_even_state_can_be_broken(self):
        """从全偶数状态出发，必须存在某种合法操作能把数字变回奇数。"""
        base = (2, 4, 6, 8)
        escaped = 0
        for player in ("A", "B"):
            for attacker_slot in (1, 2):
                for defender_slot in (1, 2):
                    for operation in ("+", "-"):
                        engine = build_engine(*base, turn=player, hp_a=5, hp_b=5)
                        engine.execute_action(player, attacker_slot, defender_slot, operation)
                        if any(
                            value % 2 for value in (engine.A1, engine.A2, engine.B1, engine.B2)
                        ):
                            escaped += 1
        self.assertGreater(escaped, 0, "全偶数状态是闭集，5 个技能会永久失效")

    def test_all_zero_state_is_not_a_deadlock(self):
        """即使人为构造出全 0 状态，一次减法也会把被操作的数字拉回 1。"""
        engine = build_engine(0, 0, 0, 0, hp_a=5, hp_b=5)
        before = (engine.A1, engine.A2, engine.B1, engine.B2)
        engine.execute_action("A", 1, 1, "-")
        after = (engine.A1, engine.A2, engine.B1, engine.B2)
        self.assertNotEqual(before, after, "全 0 状态下操作必须能改变数字")
        self.assertGreaterEqual(engine.A1, 1)

    def test_legal_play_never_reaches_all_zero(self):
        engine = build_engine(a1=1, a2=1, b1=1, b2=1, hp_a=5, hp_b=5)
        for _ in range(60):
            if engine.is_game_over():
                break
            engine.execute_action(engine.current_turn, 1, 1, "-")
            self.assertNotEqual((engine.A1, engine.A2, engine.B1, engine.B2), (0, 0, 0, 0))

    def test_idle_stalemate_now_terminates(self):
        """四个数字个位全为 0 时任何操作都无法改变数字（绝对僵持）。
        空过惩罚会强制扣血，从而保证对局必然在有限回合内结束。"""
        engine = build_engine(20, 30, 10, 40, hp_a=5, hp_b=5, turn="A")
        turns = 0
        while not engine.is_game_over() and turns < 50:
            engine.execute_action(engine.current_turn, 1, 1, "+")
            turns += 1
        self.assertTrue(engine.is_game_over(), "空过僵持必须在有限回合内结束")
        self.assertEqual(engine.get_winner(), "B")   # A 先手，先累计到 4 次空过判负

    def test_random_play_always_terminates(self):
        """固定随机序列下对局必须能正常结束。"""
        import random

        rng = random.Random(20240607)
        for game in range(20):
            engine = GameEngine()
            turns = 0
            while not engine.is_game_over() and turns < 3000:
                engine.execute_action(
                    engine.current_turn,
                    rng.randint(1, 2),
                    rng.randint(1, 2),
                    rng.choice("+-"),
                )
                turns += 1
            self.assertTrue(engine.is_game_over(), f"第 {game + 1} 局在 3000 回合内未结束")
            # 要么分出胜负，要么是僵持判定产生的平局
            self.assertTrue(
                engine.is_draw() or engine.get_winner() in ("A", "B"),
                f"第 {game + 1} 局结束状态异常",
            )


def silent_exchange(engine: GameEngine, times: int) -> None:
    """双方轮流做若干次"会改变数字、但绝不触发任何技能"的操作。

    约定 A1 = B1 = 14（个位 4），A2 / B2 在 5 与 9 之间往复，
    因此每一步都真实改变数字（不算空过），却永远不掉血。
    """
    for index in range(times):
        if engine.is_game_over():
            return
        player = "A" if index % 2 == 0 else "B"
        engine.current_turn = player
        value = engine.A2 if player == "A" else engine.B2
        operation = "+" if value == DECOY_CYCLE[0] else "-"
        engine.execute_action(player, 2, 1, operation)


class TestStalemateRule(unittest.TestCase):
    """僵持判定：连续 N 手双方血量都没变化 -> 按血量判胜负。"""

    def test_limit_is_safely_above_normal_games(self):
        # 实测正常对局"无人掉血"最长只有 20 手，阈值必须留足余量
        self.assertGreaterEqual(GameEngine.STALEMATE_LIMIT, 40)

    def test_higher_hp_wins_at_stalemate(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=5, hp_b=3)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT)
        self.assertTrue(engine.is_game_over())
        self.assertEqual(engine.get_winner(), "A")
        self.assertFalse(engine.is_draw())

    def test_draw_when_hp_is_equal(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=4, hp_b=4)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT)
        self.assertTrue(engine.is_game_over())
        self.assertIsNone(engine.get_winner())
        self.assertTrue(engine.is_draw())

    def test_stalemate_event_is_reported(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=5, hp_b=3)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT - 1)
        self.assertFalse(engine.is_game_over())
        events = engine.execute_action("B", 2, 1, "-")     # 第 50 手
        self.assertTrue(engine.is_game_over())
        self.assertIn("僵持", joined(events))

    def test_counter_resets_when_hp_changes(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=5, hp_b=5)
        silent_exchange(engine, 5)
        self.assertEqual(engine.get_stall_streak(), 5)

        # A1 = 3 -> 加 4 变 7，个位 7 触发钻头，B 掉 1 血（总血量创新低）-> 计数清零
        engine.A1 = 3
        engine.current_turn = "A"
        engine.execute_action("A", 1, 1, "+")
        self.assertEqual(engine.hp_b, 4)
        self.assertEqual(engine.get_stall_streak(), 0)

    def test_execute_action_raises_after_stalemate(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=5, hp_b=3)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT)
        self.assertTrue(engine.is_game_over())
        with self.assertRaises(GameOverError) as ctx:
            engine.execute_action("A", 2, 1, "+")
        self.assertEqual(ctx.exception.winner, "A")

    def test_observed_four_turn_cycle_terminates(self):
        """复现真实观测到的 4 手循环：

        A=(18,18) B=(38,31) 反复摆动（B1 在 38/46 之间、A2 在 17/18 之间），
        因为防刷记录恰好封印了 A2=17 的钻头与 B1=46 的回血，血量可以永远不变。
        加入僵持判定后，这个循环必须被强制收敛。
        """
        engine = build_engine(a1=18, a2=18, b1=38, b2=31, hp_a=5, hp_b=3, turn="B")
        cycle = [
            ("B", 1, 2, "+"),   # B1: 38 -> 46
            ("A", 2, 2, "-"),   # A2: 18 -> 17
            ("B", 1, 1, "-"),   # B1: 46 -> 38
            ("A", 2, 2, "+"),   # A2: 17 -> 18
        ]
        for _ in range(GameEngine.STALEMATE_LIMIT):
            for turn, attacker_slot, defender_slot, operation in cycle:
                if engine.is_game_over():
                    break
                engine.current_turn = turn
                engine.execute_action(turn, attacker_slot, defender_slot, operation)
            if engine.is_game_over():
                break
        self.assertTrue(engine.is_game_over(), "循环僵持必须被强制收敛")


class TestRenderData(unittest.TestCase):
    """渲染数据接口。"""

    def test_render_data_shape(self):
        engine = GameEngine()
        data = engine.get_render_data()
        self.assertEqual(set(data.keys()), {"A", "B", "hp_a", "hp_b", "events"})
        self.assertEqual(data["A"], [1, 1])
        self.assertEqual(data["B"], [1, 1])
        self.assertEqual(data["hp_a"], 3)
        self.assertEqual(data["hp_b"], 3)
        self.assertEqual(data["events"], [])

    def test_render_data_events_follow_last_action(self):
        engine = build_engine(a1=16, a2=5, b1=14, b2=11)
        engine.execute_action("A", 2, 1, "+")
        data = engine.get_render_data()
        self.assertEqual(data["A"], [16, 9])
        self.assertEqual(data["B"], [14, 11])
        self.assertTrue(any("回血" in e for e in data["events"]))

    def test_split_digits_helper(self):
        self.assertEqual(GameEngine.split_digits(7), (0, 7))
        self.assertEqual(GameEngine.split_digits(35), (3, 5))
        self.assertEqual(GameEngine.split_digits(99), (9, 9))

    def test_values_stay_within_zero_to_ninety_nine(self):
        engine = build_engine(a1=99, a2=99, b1=99, b2=99, hp_a=5, hp_b=5)
        engine.execute_action("A", 1, 1, "+")
        # 99 + 9 = 108 -> 10 + 8 = 18
        self.assertEqual(engine.A1, 18)
        for value in (engine.A1, engine.A2, engine.B1, engine.B2):
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 99)

    def test_clone_is_independent(self):
        engine = build_engine(a1=16, a2=29, b1=35, b2=38)
        clone = engine.clone()
        clone.execute_action("A", 1, 1, "+")
        clone.surrender("B")
        self.assertTrue(clone.is_game_over())
        self.assertFalse(engine.is_game_over())
        self.assertEqual(engine.current_turn, "A")
        self.assertEqual((engine.A1, engine.A2), (16, 29))


class TestSkillLocks(unittest.TestCase):
    """防刷状态的对外查询：UI 用它把"已触发、等刷新"的数字显示成灰色。"""

    def test_single_skill_lock_and_release(self):
        engine = build_engine(a1=6, a2=1, b1=13, b2=11)
        check(engine)  # A1 个位 6 -> 回血
        self.assertEqual(engine.get_skill_locks("A"), {"A1": ["回血"], "A2": []})

        # 6 -> 9（守方 B1 个位 3），数字变过且 9 不触发任何技能 -> 锁解除
        nudge(engine, "A", 1, up=True)
        self.assertEqual(engine.A1, 9)
        self.assertEqual(engine.get_skill_locks("A"), {"A1": [], "A2": []})

    def test_pair_skill_locks_both_slots(self):
        engine = build_engine(a1=18, a2=28)
        check(engine)  # 双个位 8 -> 旋镖
        self.assertEqual(engine.get_skill_locks("A"), {"A1": ["旋镖"], "A2": ["旋镖"]})

    def test_locks_are_per_player(self):
        engine = build_engine(a1=18, a2=28, b1=1, b2=1)
        check(engine)
        self.assertEqual(engine.get_skill_locks("B"), {"B1": [], "B2": []})

    def test_no_locks_before_any_trigger(self):
        engine = build_engine(a1=6, a2=7)
        self.assertEqual(engine.get_skill_locks("A"), {"A1": [], "A2": []})

    def test_invalid_player_rejected(self):
        with self.assertRaises(ValueError):
            build_engine().get_skill_locks("C")


class TestActionPreviews(unittest.TestCase):
    """操作预演：玩家在选择 ＋/－ 时提示这一手能触发什么技能。

    预演在副本上真实执行，因此不需要在 UI 层复制任何技能条件。
    """

    def make_engine(self, **kwargs):
        # A1=15、A2=18；B1=13（个位 3）
        return build_engine(a1=15, a2=18, b1=13, b2=11, **kwargs)

    def test_preview_reports_boomerang(self):
        engine = self.make_engine()
        # A1 15 + 3 = 18，与 A2 = 18 凑成双个位 8
        preview = engine.get_action_previews("A")["1"]["1"]["+"]
        self.assertEqual(preview["skills"], [{"player": "A", "skill": "旋镖"}])
        self.assertFalse(preview["finish"])

    def test_preview_reports_slap(self):
        engine = self.make_engine()
        # A2 18 - 3 = 15，与 A1 = 15 凑成双个位 5 且都 >= 15
        preview = engine.get_action_previews("A")["2"]["1"]["-"]
        self.assertEqual(preview["skills"], [{"player": "A", "skill": "巴掌"}])

    def test_preview_empty_when_result_triggers_nothing(self):
        engine = self.make_engine()
        # A2 18 + 3 = 21 -> A1 个位 5、A2 个位 1，不满足任何技能
        preview = engine.get_action_previews("A")["2"]["1"]["+"]
        self.assertEqual(preview["skills"], [])

    def test_preview_marks_finishing_move(self):
        engine = self.make_engine(hp_b=2)
        # 旋镖造成 2 点伤害，B 剩 2 血 -> 这一手直接终结对局
        preview = engine.get_action_previews("A")["1"]["1"]["+"]
        self.assertTrue(preview["finish"])

    def test_preview_covers_all_eight_actions(self):
        engine = self.make_engine()
        previews = engine.get_action_previews("A")
        self.assertEqual(sorted(previews), ["1", "2"])
        for slots in previews.values():
            self.assertEqual(sorted(slots), ["1", "2"])
            for by_operation in slots.values():
                self.assertEqual(sorted(by_operation), ["+", "-"])

    def test_preview_matches_real_execution(self):
        """预演结果必须与真实执行完全一致（防止两套逻辑出现偏差）。"""
        engine = self.make_engine(hp_b=4)
        previews = engine.get_action_previews("A")
        for slot in (1, 2):
            for foe_slot in (1, 2):
                for operation in ("+", "-"):
                    real = engine.clone()
                    events = real.execute_action("A", slot, foe_slot, operation)
                    preview = previews[str(slot)][str(foe_slot)][operation]
                    self.assertEqual(
                        preview["skills"],
                        GameEngine._skills_in_events(events),
                        f"预演与实走不一致：{slot} 打 {foe_slot} 用 {operation}",
                    )
                    self.assertEqual(preview["finish"], real.is_game_over())

    def test_preview_does_not_mutate_real_game(self):
        engine = self.make_engine()
        before = (engine.A1, engine.A2, engine.B1, engine.B2, engine.hp_a, engine.hp_b)
        engine.get_action_previews("A")
        self.assertEqual(
            (engine.A1, engine.A2, engine.B1, engine.B2, engine.hp_a, engine.hp_b), before
        )

    def test_preview_empty_when_not_your_turn(self):
        engine = build_engine(turn="B")
        self.assertEqual(engine.get_action_previews("A"), {})

    def test_preview_empty_after_game_over(self):
        engine = build_engine()
        engine.surrender("A")
        self.assertEqual(engine.get_action_previews("B"), {})


class TestEndReason(unittest.TestCase):
    """败因说明：结算界面用它告诉玩家"你是怎么赢/输的"。"""

    def test_empty_while_running(self):
        self.assertEqual(build_engine().get_end_reason(), "")

    def test_hp_zero(self):
        engine = build_engine(a1=17, b1=11, b2=11, hp_b=1)
        check(engine)  # 钻头 -> B 血量归零
        self.assertEqual(engine.get_end_reason(), "B血量归零")

    def test_surrender(self):
        engine = build_engine()
        engine.surrender("A")
        self.assertEqual(engine.get_end_reason(), "A认输")

    def test_idle_loss(self):
        engine = build_engine(a1=5, a2=5, b1=10, b2=10)
        for _ in range(GameEngine.IDLE_LOSS_STREAK):
            engine.current_turn = "A"
            engine.execute_action("A", 1, 1, "-")  # 5 - 个位 0 = 5，永远空过
        self.assertEqual(engine.get_end_reason(), "A连续4回合空过判负")

    def test_stalemate_by_hp(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=5, hp_b=3)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT)
        self.assertEqual(engine.get_winner(), "A")
        self.assertIn("按血量判定 A 胜", engine.get_end_reason())

    def test_stalemate_draw(self):
        engine = build_engine(a1=14, a2=5, b1=14, b2=5, hp_a=4, hp_b=4)
        silent_exchange(engine, GameEngine.STALEMATE_LIMIT)
        self.assertTrue(engine.is_draw())
        self.assertIn("判平局", engine.get_end_reason())


if __name__ == "__main__":
    unittest.main(verbosity=2)
