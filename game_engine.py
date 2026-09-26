"""回合制数字对战游戏的核心引擎（纯逻辑，不含任何 UI）。

规则要点

* 玩家 A 持有 ``A1`` / ``A2``，玩家 B 持有 ``B1`` / ``B2``；存储范围为 ``0 ~ 99``，
  但正常对局中数字永远不会低于 1（见 ``SUB_FLOOR``）。
* 玩家血量 ``hp_a`` / ``hp_b`` 初始值均为 3，取值范围 ``0 ~ 5``。
* ``current_turn`` 记录当前回合属于 ``'A'`` 还是 ``'B'``，每次成功行动后自动切换。
* 加法（按需求方后续修订）：``res = 攻方值 + (守方值 % 10)``；
  ``res < 100`` 时 ``result = res``，``res >= 100`` 时 ``result = 10 + (res % 10)``。
* 减法：``result = max(1, 攻方值 - (守方值 % 10))``，下限为 1（见 ``SUB_FLOOR`` 的说明）。
* 加法 / 减法的结果都写回**攻击方自己的槽位**，防守方数字保持不变。
* 每次行动修改数字后立即执行一次技能检查 ``check_skills()``。
* 三条防僵持规则：减法下限为 1（``SUB_FLOOR``）、空过分级惩罚（``IDLE_PENALTY`` 系列）、
  连续 50 手双方血量无变化则按血量判定胜负（``STALEMATE_LIMIT``）。

本模块只负责游戏规则与状态，不包含 UI、动画或终端输出。
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Tuple


class GameOverError(ValueError):
    """游戏已经结束时仍然尝试执行攻击行动，抛出此异常。

    继承 :class:`ValueError`，因此既满足"抛出明确异常"的要求，
    也兼容按 ``ValueError`` 捕获的调用方。
    """

    def __init__(self, winner: Optional[str] = None) -> None:
        self.winner = winner
        if winner is None:
            message = "游戏已经结束，没有获胜方"
        else:
            message = f"游戏结束，恭喜获胜方 {winner} 获胜"
        super().__init__(message)


class GameEngine:
    """回合制数字对战游戏引擎。

    只保存游戏状态并计算规则结果，不负责渲染。
    """

    # ---------------------------------------------------------------- 基础数值
    HP_INIT: int = 3
    HP_MIN: int = 0
    HP_MAX: int = 5

    VALUE_MIN: int = 0
    VALUE_MAX: int = 99

    # 减法结果下限。
    #
    # 为什么是 1 而不是 0：
    # 加法与减法本质上都是"两个数字相加 / 相减"，而偶数 ∓ 偶数永远是偶数，因此四个数字
    # 一旦全部变成偶数，它们就再也变不回奇数（全偶数是数学上的闭集）。此时个位只可能是
    # 0/2/4/6/8，导致「钻头(个位7)」「锁链(个位9)」「恭喜发财(个位5和0)」「糖葫芦
    # (个位8和1)」「巴掌(个位5、5)」五个技能永久失效，只剩回血与旋镖，血量被回血持续
    # 抬回上限，对局永远无法结束；同时四个数字全为 0 时任何操作都是空转，形成绝对死局。
    # 把减法下限设为奇数 1 后，凡是减到下限的场合都会重新注入奇数，全偶数不再是闭集，
    # 上述两个问题被同时根治。
    SUB_FLOOR: int = 1

    # 空过惩罚（分级，避免"偶尔被迫空过"被一棒子打死）。
    #
    # 为什么需要它：攻方的数字只由"守方数字的个位数"加减而来，所以只要守方两个数字的个位
    # 都是 0，攻方就**完全无法改变任何数字**；而"空过"又是绝对安全的选择，强 AI 会一直空过，
    # 导致对局永远无法结束（实测地狱 vs 地狱 2000 回合打不完）。更糟的是，冻结态下 8 种着法
    # 分值完全相同，Alpha-Beta 剪枝彻底失效，AI 每步耗时从 100ms 暴涨到 1~2 秒。
    #
    # 分级规则（连续计数，一旦做出有效操作立即清零）：
    #   第 1 次空过          -> 仅警告
    #   每满 2 次空过        -> 扣 IDLE_PENALTY 点血
    #   连续 4 次空过        -> 直接判负（视为认输）
    # 这样"想摆烂"的代价会递增，但偶尔被迫空过一次不会被立刻惩罚；
    # 同时因为它终究会扣血、会判负，僵持时血量必然收敛，对局一定能结束。
    IDLE_PENALTY: int = 1
    IDLE_PENALTY_EVERY: int = 2
    IDLE_LOSS_STREAK: int = 4

    # 僵持判定（最后一道保险）。
    #
    # 上面几个补丁都是"针对性堵漏"，但本游戏还存在一类更隐蔽的僵持：**循环僵持**。
    # 实测出现过周期仅 4 手的循环：
    #     A=(18,18) B=(38,31) -> (18,18,46,31) -> (18,17,46,31) -> ... 永远反复
    # 里面 A2=17 触发钻头（B 扣 1）、B1=46 触发回血（B 回 1），**刚好抵消** ——
    # 于是血量一直在变，谁也无法真正削弱谁。
    #
    # 所以判定依据不能是"血量有没有变化"，而是"**双方总血量有没有跌破历史最低点**"：
    # 只要没打出新低，就说明局面在原地打转。这也是传统棋牌的标准做法
    # （国际象棋的 50 步规则、拳击的点数判定）。
    # 阈值取 50 手有数据支撑：正常结束的对局里，最长连续手数只有 20 手左右。
    STALEMATE_LIMIT: int = 50

    PLAYERS: Tuple[str, ...] = ("A", "B")
    SLOTS: Tuple[int, ...] = (1, 2)
    OPERATIONS: Tuple[str, ...] = ("+", "-")

    # ------------------------------------------------------ 技能 1：回血（个位6）
    HEAL_ONES: int = 6
    HEAL_AMOUNT: int = 1

    # ------------------------------------------------------ 技能 2：旋镖（双个位8）
    BOOMERANG_ONES: int = 8
    BOOMERANG_DAMAGE: int = 1
    BOOMERANG_HEAVY_DAMAGE: int = 2
    BOOMERANG_HEAVY_MIN: int = 30

    # ------------------------------- 技能 3：恭喜发财，小命拿来（都≥30 且个位5和0）
    FORTUNE_MIN: int = 30
    FORTUNE_ONES: Tuple[int, int] = (5, 0)
    FORTUNE_DAMAGE: int = 3

    # ------------------------------------------ 技能 4：巴掌（都≥15 且个位都是5）
    SLAP_MIN: int = 15
    SLAP_ONES: int = 5
    SLAP_DAMAGE: int = 1
    SLAP_MAX_TURNS: int = 3

    # ------------------------------------------------------ 技能 5：钻头（个位7）
    DRILL_ONES: int = 7
    DRILL_DAMAGE: int = 1

    # -------------------------------- 技能 6：糖葫芦（都≥15 且个位分别是8和1）
    CANDY_MIN: int = 15
    CANDY_ONES: Tuple[int, int] = (8, 1)
    CANDY_HEAL: int = 2

    # ------------------------------------------ 技能 7：锁链（都≥20 且个位都是9）
    CHAIN_MIN: int = 20
    CHAIN_ONES: int = 9

    # ------------------------------------------------------------------ 初始化
    def __init__(self) -> None:
        self.A1: int = 1
        self.A2: int = 1
        self.B1: int = 1
        self.B2: int = 1

        self.hp_a: int = self.HP_INIT
        self.hp_b: int = self.HP_INIT

        self.current_turn: str = "A"

        # 最近一次 execute_action() 产生的技能事件日志
        self._events: List[str] = []
        # 最近一次真正执行了攻击行动的玩家（锁链 / 巴掌需要）
        self._acting_player: Optional[str] = None
        # 投降产生的获胜方
        self._winner: Optional[str] = None
        # 是否以平局收场（僵持判定时双方血量相同）
        self._drawn: bool = False
        # 连续多少手都没有打出"总血量新低"（僵持判定用）
        self._stall_streak: int = 0
        # 历史最低总血量：只有跌破它才算"有进展"
        self._hp_low_water: int = self.HP_INIT * 2
        # 每个玩家连续空过了几次（空过惩罚分级用）
        self._idle_streak: Dict[str, int] = {"A": 0, "B": 0}

        # 锁链状态：True 表示该玩家下一次行动会被跳过
        self._chained: Dict[str, bool] = {"A": False, "B": False}
        # 巴掌剩余结算次数
        self._slap_turns_left: Dict[str, int] = {"A": 0, "B": 0}

        # 每个槽位的"变化代数"：该数字每真正变化一次就 +1
        self._slot_generation: Dict[str, int] = {
            self._slot_name(player, slot): 0
            for player in self.PLAYERS
            for slot in self.SLOTS
        }
        # 防刷记录 —— 单数字技能：(技能名, 玩家, 槽位) -> 上次触发时该槽位的变化代数
        self._single_records: Dict[Tuple[str, str, int], int] = {}
        # 防刷记录 —— 双数字技能：(技能名, 玩家) -> 上次触发时两个槽位的变化代数
        self._pair_records: Dict[Tuple[str, str], Tuple[int, int]] = {}

    # ============================================================== 公开查询接口
    def is_game_over(self) -> bool:
        """游戏是否已经结束（有人投降、某一方血量归零，或触发僵持判定）。"""
        return (
            self._winner is not None
            or self._drawn
            or self.hp_a <= self.HP_MIN
            or self.hp_b <= self.HP_MIN
        )

    def is_draw(self) -> bool:
        """是否以平局收场。

        平局有两种来源：双方血量同时归零，或触发僵持判定时双方血量相同。
        """
        if not self.is_game_over():
            return False
        return self.get_winner() is None

    def get_winner(self) -> Optional[str]:
        """返回获胜方 ``'A'`` / ``'B'``；游戏尚未结束返回 ``None``。

        对局已经结束但返回 ``None`` 表示平局（可用 :meth:`is_draw` 判定）。
        """
        if self._winner is not None:
            return self._winner
        if self._drawn:
            return None

        a_dead = self.hp_a <= self.HP_MIN
        b_dead = self.hp_b <= self.HP_MIN
        if a_dead and b_dead:
            return None
        if a_dead:
            return "B"
        if b_dead:
            return "A"
        return None

    def get_stall_streak(self) -> int:
        """连续多少手都没有打出"总血量新低"（供 UI 显示僵持判定倒计时）。"""
        return self._stall_streak

    def get_idle_streak(self, player: str) -> int:
        """指定玩家当前"连续空过"了多少次（供 UI 显示警告倒计时）。

        :param player: ``'A'`` 或 ``'B'``
        :raises ValueError: 玩家编号非法
        """
        self._validate_player(player)
        return self._idle_streak[player]

    def is_chained(self, player: str) -> bool:
        """指定玩家是否处于"锁链"状态（下一次行动会被跳过）。

        :param player: ``'A'`` 或 ``'B'``
        :raises ValueError: 玩家编号非法
        """
        self._validate_player(player)
        return self._chained[player]

    def get_slap_turns_left(self, player: str) -> int:
        """指定玩家"巴掌"技能还需要结算的次数。

        :param player: ``'A'`` 或 ``'B'``
        :raises ValueError: 玩家编号非法
        """
        self._validate_player(player)
        return self._slap_turns_left[player]

    def get_render_data(self) -> Dict[str, object]:
        """返回渲染所需的纯数据，不做任何 UI / 动画 / 终端输出。

        结构::

            {
                "A": [A1, A2],
                "B": [B1, B2],
                "hp_a": hp_a,
                "hp_b": hp_b,
                "events": [...],   # 最近一次 execute_action() 的技能事件日志
            }

        备注：当数字大于 9（两位数）时，UI 可用 :meth:`split_digits`
        取到十位与个位，并分别使用小号字体（如 6 号）与大号字体（如 12 号）。
        """
        return {
            "A": [self.A1, self.A2],
            "B": [self.B1, self.B2],
            "hp_a": self.hp_a,
            "hp_b": self.hp_b,
            "events": list(self._events),
        }

    @staticmethod
    def split_digits(value: int) -> Tuple[int, int]:
        """把数字拆成 ``(十位, 个位)``，供 UI 决定大小字体。"""
        return (value // 10, value % 10)

    def clone(self) -> "GameEngine":
        """返回当前状态的**独立副本**，供 AI 推演与批量模拟使用。

        副本与原实例互不影响（修改副本不会改动原对局），
        因此 AI 可以放心地在副本上试走各种操作。
        """
        return copy.deepcopy(self)

    # ============================================================== 对外动作接口
    def surrender(self, player: str) -> None:
        """玩家中途退出，对手直接获胜。

        :param player: 退出的玩家 ``'A'`` 或 ``'B'``
        :raises ValueError: 玩家编号非法
        :return: ``None``（幂等，游戏已结束时不做任何改变）
        """
        self._validate_player(player)
        if self.is_game_over():
            return
        self._winner = self._opponent(player)

    def execute_action(
        self,
        attacker: str,
        attacker_slot: int,
        defender_slot: int,
        operation: str,
    ) -> List[str]:
        """执行一次完整的攻击行动。

        :param attacker: 攻击方 ``'A'`` 或 ``'B'``
        :param attacker_slot: 攻击方使用的数字 ``1`` 或 ``2``
        :param defender_slot: 防守方的数字 ``1`` 或 ``2``
        :param operation: ``'+'`` 加法 / ``'-'`` 减法
        :return: 本次行动产生的技能事件日志（与 ``get_render_data()["events"]`` 一致）
        :raises ValueError: 参数非法或不是当前回合的玩家在操作
        :raises GameOverError: 游戏已经结束
        """
        self._validate_action(attacker, attacker_slot, defender_slot, operation)

        if self.is_game_over():
            raise GameOverError(self.get_winner())

        if attacker != self.current_turn:
            raise ValueError(
                f"当前回合属于 {self.current_turn}，{attacker} 不能跨回合操作"
            )

        # 锁链：被缠住的玩家无法行动，直接跳过本回合
        if self._chained[attacker]:
            self._chained[attacker] = False
            events = [f"{attacker}被锁链缠住，此回合无法行动"]
            events.extend(self._update_stalemate())
            self._events = list(events)
            self._switch_turn()
            return events

        opponent = self._opponent(attacker)
        attacker_value = self._get_slot(attacker, attacker_slot)
        defender_value = self._get_slot(opponent, defender_slot)

        if operation == "+":
            result = self._calculate_add(attacker_value, defender_value)
        else:
            result = self._calculate_sub(attacker_value, defender_value)

        # 结果写回攻击方自己的槽位，防守方数字保持不变
        self._set_slot(attacker, attacker_slot, result)
        # 数字"真正变化"时才推进变化代数（防刷据此判断这个数字是否刷新过）
        if result != attacker_value:
            self._bump_generation(attacker, attacker_slot)

        # 数字变化后立即做一次技能检查
        self._acting_player = attacker
        events = self.check_skills()

        # 空过惩罚：本次行动没有让任何数字发生变化，该玩家受到伤害。
        # 由于一次行动只会改写攻击方自己的槽位，所以"没有变化"等价于 result == attacker_value。
        if result == attacker_value:
            events.extend(self._register_idle(attacker))
        else:
            self._idle_streak[attacker] = 0

        events.extend(self._update_stalemate())

        self._switch_turn()
        return events

    # ============================================================== 技能检查接口
    def check_skills(self) -> List[str]:
        """检查当前所有数字并触发技能，返回事件日志列表。

        技能触发顺序固定为：
        回血 -> 钻头 -> 旋镖 -> 恭喜发财 -> 糖葫芦 -> 锁链 -> 巴掌。

        .. warning::
           本方法会真实修改 HP 与技能状态（含防刷记录、锁链、巴掌持续状态），
           由 :meth:`execute_action` 在每次数字变化后调用一次。
           不要把它当作"只读预览"反复调用。
        """
        events: List[str] = []
        events.extend(self._check_heal())
        events.extend(self._check_drill())
        events.extend(self._check_boomerang())
        events.extend(self._check_fortune())
        events.extend(self._check_candy())
        events.extend(self._check_chain())
        events.extend(self._check_slap())

        self._events = list(events)
        return events

    def reset_skill_state(self) -> None:
        """清空全部技能的防刷记录、锁链状态、巴掌持续状态与僵持计数。

        仅供测试 / 调试使用，正常对局中不应调用。
        """
        self._single_records.clear()
        self._pair_records.clear()
        self._chained = {"A": False, "B": False}
        self._slap_turns_left = {"A": 0, "B": 0}
        self._stall_streak = 0
        self._hp_low_water = self.hp_a + self.hp_b
        self._idle_streak = {"A": 0, "B": 0}
        self._slot_generation = {
            self._slot_name(player, slot): 0
            for player in self.PLAYERS
            for slot in self.SLOTS
        }

    # ============================================================== 数值计算（纯函数）
    def _calculate_add(self, attacker_value: int, defender_value: int) -> int:
        """加法：``res = 攻方值 + (守方值 % 10)``。

        ``res < 100`` 时结果取 ``res``；``res >= 100`` 时结果为 ``10 + (res % 10)``。
        """
        res = attacker_value + (defender_value % 10)
        if res >= 100:
            return 10 + (res % 10)
        return res

    def _calculate_sub(self, attacker_value: int, defender_value: int) -> int:
        """减法：只减去防守方数字的个位数，结果不低于 :attr:`SUB_FLOOR`（= 1）。

        下限取奇数 1 是为了避免数字锁死在全偶数区间，同时保证数字永远不会变成 0。
        """
        defender_ones = defender_value % 10
        return max(self.SUB_FLOOR, attacker_value - defender_ones)

    # ============================================================== 技能判定（内部）
    def _check_heal(self) -> List[str]:
        """技能 1「回血」：某数字个位数为 6，则该数字所属玩家回复 1 点 HP。

        单数字技能按槽位记录防刷状态，且每个技能在一次检查中最多触发一次。
        """
        for player in self.PLAYERS:
            for slot in self.SLOTS:
                value = self._get_slot(player, slot)
                if value % 10 != self.HEAL_ONES:
                    continue

                key = ("heal", player, slot)
                if not self._single_ready(key, player, slot):
                    continue

                self._mark_single(key, player, slot)
                self._heal(player, self.HEAL_AMOUNT)
                # 事件日志一律使用"技能名"，不暴露底层判定细节（个位 6 这类写进规则说明里）
                return [f"{player}触发回血，回复{self.HEAL_AMOUNT}点HP"]
        return []

    def _check_drill(self) -> List[str]:
        """技能 5「钻头」：某数字个位数为 7，则对对手造成 1 点伤害。

        单数字技能按槽位记录防刷状态，且每个技能在一次检查中最多触发一次。
        """
        for player in self.PLAYERS:
            for slot in self.SLOTS:
                value = self._get_slot(player, slot)
                if value % 10 != self.DRILL_ONES:
                    continue

                key = ("drill", player, slot)
                if not self._single_ready(key, player, slot):
                    continue

                self._mark_single(key, player, slot)
                opponent = self._opponent(player)
                self._damage(opponent, self.DRILL_DAMAGE)
                return [f"{player}触发钻头，{opponent}受到{self.DRILL_DAMAGE}点伤害"]
        return []

    def _check_boomerang(self) -> List[str]:
        """技能 2「旋镖」：某玩家两个数字个位数都是 8，则对对手造成伤害。

        两个数字都 ``>= 30`` 时伤害提升为 2，否则为 1。
        防刷：两个数字都必须相对上次触发时发生变化。
        """
        for player in self.PLAYERS:
            value_1, value_2 = self._pair(player)
            if value_1 % 10 != self.BOOMERANG_ONES or value_2 % 10 != self.BOOMERANG_ONES:
                continue

            key = ("boomerang", player)
            if self._is_pair_blocked(key, player):
                continue

            self._mark_pair(key, player)
            damage = (
                self.BOOMERANG_HEAVY_DAMAGE
                if value_1 >= self.BOOMERANG_HEAVY_MIN and value_2 >= self.BOOMERANG_HEAVY_MIN
                else self.BOOMERANG_DAMAGE
            )
            opponent = self._opponent(player)
            self._damage(opponent, damage)
            return [f"{player}触发旋镖，{opponent}受到{damage}点伤害"]
        return []

    def _check_fortune(self) -> List[str]:
        """技能 3「恭喜发财，小命拿来」：某玩家两个数字都 ``>= 30``，
        且个位数分别是 5 和 0（顺序不限），则对对手造成 3 点伤害。

        防刷：两个数字都必须相对上次触发时发生变化。
        """
        for player in self.PLAYERS:
            value_1, value_2 = self._pair(player)
            if value_1 < self.FORTUNE_MIN or value_2 < self.FORTUNE_MIN:
                continue
            if {value_1 % 10, value_2 % 10} != set(self.FORTUNE_ONES):
                continue

            key = ("fortune", player)
            if self._is_pair_blocked(key, player):
                continue

            self._mark_pair(key, player)
            opponent = self._opponent(player)
            self._damage(opponent, self.FORTUNE_DAMAGE)
            return [f"{player}触发恭喜发财，{opponent}受到{self.FORTUNE_DAMAGE}点伤害"]
        return []

    def _check_candy(self) -> List[str]:
        """技能 6「糖葫芦」：某玩家两个数字都 ``>= 15``，
        且个位数分别是 8 和 1（顺序不限），则该玩家回复 2 点 HP（上限 5）。

        防刷：两个数字都必须相对上次触发时发生变化。
        """
        for player in self.PLAYERS:
            value_1, value_2 = self._pair(player)
            if value_1 < self.CANDY_MIN or value_2 < self.CANDY_MIN:
                continue
            if {value_1 % 10, value_2 % 10} != set(self.CANDY_ONES):
                continue

            key = ("candy", player)
            if self._is_pair_blocked(key, player):
                continue

            self._mark_pair(key, player)
            self._heal(player, self.CANDY_HEAL)
            return [f"{player}触发糖葫芦，回复{self.CANDY_HEAL}点HP"]
        return []

    def _check_chain(self) -> List[str]:
        """技能 7「锁链」：行动方两个数字都 ``>= 20`` 且个位数都是 9，
        则给对手附加锁链状态，对手下一次行动被跳过。

        防刷：两个数字都必须相对上次触发时发生变化（即"两个9 -> 不再是两个9 -> 再回到两个9"）。
        """
        player = self._acting_player
        if player is None:
            return []

        value_1, value_2 = self._pair(player)
        if value_1 < self.CHAIN_MIN or value_2 < self.CHAIN_MIN:
            return []
        if value_1 % 10 != self.CHAIN_ONES or value_2 % 10 != self.CHAIN_ONES:
            return []

        key = ("chain", player)
        if self._is_pair_blocked(key, player):
            return []

        self._mark_pair(key, player)
        opponent = self._opponent(player)
        self._chained[opponent] = True
        return [f"{opponent}被锁链，跳过下一回合"]

    def _check_slap(self) -> List[str]:
        """技能 4「巴掌」：行动方两个数字都 ``>= 15`` 且个位数都是 5，触发后进入持续状态。

        持续档位由两个数字的十位数中**较小**的一个决定：
        1 -> 1 回合，2 -> 2 回合，``>= 3`` -> 3 回合（含触发当回合）。
        持续期间不叠加、不刷新，每回合最多造成 1 点伤害。
        """
        player = self._acting_player
        if player is None:
            return []

        opponent = self._opponent(player)

        # 持续状态中：本回合结算一次，不重新判定、不刷新持续时间
        if self._slap_turns_left[player] > 0:
            self._slap_turns_left[player] -= 1
            self._damage(opponent, self.SLAP_DAMAGE)
            return [
                f"{player}触发巴掌，{opponent}受到{self.SLAP_DAMAGE}点伤害"
                f"(剩余{self._slap_turns_left[player]}回合)"
            ]

        value_1, value_2 = self._pair(player)
        if value_1 < self.SLAP_MIN or value_2 < self.SLAP_MIN:
            return []
        if value_1 % 10 != self.SLAP_ONES or value_2 % 10 != self.SLAP_ONES:
            return []

        key = ("slap", player)
        if self._is_pair_blocked(key, player):
            return []

        self._mark_pair(key, player)
        tier = min(value_1 // 10, value_2 // 10)
        tier = max(1, min(self.SLAP_MAX_TURNS, tier))

        self._slap_turns_left[player] = tier
        # 触发当回合立即结算一次
        self._slap_turns_left[player] -= 1
        self._damage(opponent, self.SLAP_DAMAGE)
        return [
            f"{player}触发巴掌，{opponent}受到{self.SLAP_DAMAGE}点伤害"
            f"(剩余{self._slap_turns_left[player]}回合)"
        ]

    # ============================================================== 内部工具方法
    def _generation(self, player: str, slot: int) -> int:
        """读取某个槽位当前的变化代数。"""
        return self._slot_generation[self._slot_name(player, slot)]

    def _bump_generation(self, player: str, slot: int) -> None:
        """数字真正变化后，递增该槽位的变化代数。"""
        name = self._slot_name(player, slot)
        self._slot_generation[name] = self._slot_generation.get(name, 0) + 1

    def _single_ready(self, key: Tuple[str, str, int], player: str, slot: int) -> bool:
        """单数字技能的防刷判定。

        规则：这个槽位自上次触发该技能以来**必须真正变化过**。
        注意比较的是"变化代数"而不是"数值" —— 所以 26 -> 20 -> 26 这样的往复
        同样能让技能再次触发（数值虽然回到了 26，但中间确实变过）。
        """
        recorded = self._single_records.get(key)
        if recorded is None:
            return True
        return self._generation(player, slot) > recorded

    def _mark_single(self, key: Tuple[str, str, int], player: str, slot: int) -> None:
        """记录该技能本次触发时槽位的变化代数。"""
        self._single_records[key] = self._generation(player, slot)

    def _is_pair_blocked(self, key: Tuple[str, str], player: str) -> bool:
        """双数字技能的防刷判定。

        规则：两个数字都**必须自上次触发以来真正变化过**，只要有一个没变过就拦截。
        """
        record = self._pair_records.get(key)
        if record is None:
            return False
        return (
            self._generation(player, 1) <= record[0]
            or self._generation(player, 2) <= record[1]
        )

    def _mark_pair(self, key: Tuple[str, str], player: str) -> None:
        """记录双数字技能本次触发时两个槽位的变化代数。"""
        self._pair_records[key] = (
            self._generation(player, 1),
            self._generation(player, 2),
        )

    def _register_idle(self, player: str) -> List[str]:
        """记录一次空过并施加分级惩罚。

        :return: 事件日志（可能包含判负事件）
        """
        self._idle_streak[player] += 1
        streak = self._idle_streak[player]

        if streak >= self.IDLE_LOSS_STREAK:
            self._winner = self._opponent(player)
            return [
                f"{player}连续{streak}回合空过，视为认输，{self._winner} 获胜"
            ]

        if streak % self.IDLE_PENALTY_EVERY == 0:
            self._damage(player, self.IDLE_PENALTY)
            return [
                f"{player}连续空过{streak}次，受到{self.IDLE_PENALTY}点伤害"
            ]

        # 提示"下一次空过会发生什么"，方便 UI 提前预警
        next_streak = streak + 1
        if next_streak >= self.IDLE_LOSS_STREAK:
            warning = "再空过 1 次将直接判负"
        elif next_streak % self.IDLE_PENALTY_EVERY == 0:
            warning = "再空过 1 次将扣血"
        else:
            warning = "再空过 2 次将扣血"
        return [f"{player}空过（连续{streak}次），{warning}"]

    def _update_stalemate(self) -> List[str]:
        """判断局面是否还在推进，必要时触发僵持判定。

        判定依据是"**双方总血量有没有跌破历史最低点**"，而不是"血量有没有变化"：
        回血与扣血可能刚好抵消（实测出现过每 4 手一个循环、-1 又 +1 的局面），
        只看"有没有变化"会被这种情况骗过去。

        :return: 触发僵持判定时返回事件日志，否则返回空列表
        """
        total = self.hp_a + self.hp_b
        if total < self._hp_low_water:
            self._hp_low_water = total
            self._stall_streak = 0
            return []

        self._stall_streak += 1
        if self._stall_streak < self.STALEMATE_LIMIT or self.is_game_over():
            return []

        if self.hp_a > self.hp_b:
            self._winner = "A"
        elif self.hp_b > self.hp_a:
            self._winner = "B"
        else:
            self._drawn = True

        verdict = "平局" if self._drawn else f"{self._winner} 获胜"
        return [
            f"连续{self.STALEMATE_LIMIT}手双方总血量都未能再降低，判定僵持：{verdict}"
        ]

    def _damage(self, player: str, amount: int) -> int:
        """扣血，血量不会低于 0。返回实际扣除的血量。"""
        before = self.hp_a if player == "A" else self.hp_b
        after = max(self.HP_MIN, before - amount)
        self._set_hp(player, after)
        return before - after

    def _heal(self, player: str, amount: int) -> int:
        """回血，血量不会超过 5。返回实际回复的血量。"""
        before = self.hp_a if player == "A" else self.hp_b
        after = min(self.HP_MAX, before + amount)
        self._set_hp(player, after)
        return after - before

    def _set_hp(self, player: str, value: int) -> None:
        """写入血量（自动限制在 0 ~ 5）。"""
        value = max(self.HP_MIN, min(self.HP_MAX, value))
        if player == "A":
            self.hp_a = value
        else:
            self.hp_b = value

    def _pair(self, player: str) -> Tuple[int, int]:
        """返回该玩家的两个数字 ``(X1, X2)``。"""
        return (self._get_slot(player, 1), self._get_slot(player, 2))

    @staticmethod
    def _slot_name(player: str, slot: int) -> str:
        """把 ``('A', 1)`` 映射为成员变量名 ``'A1'``。"""
        return f"{player}{slot}"

    def _get_slot(self, player: str, slot: int) -> int:
        """读取指定玩家指定槽位的数字。"""
        return int(getattr(self, self._slot_name(player, slot)))

    def _set_slot(self, player: str, slot: int, value: int) -> None:
        """写入指定玩家指定槽位的数字（自动限制在 0 ~ 99）。"""
        value = max(self.VALUE_MIN, min(self.VALUE_MAX, value))
        setattr(self, self._slot_name(player, slot), value)

    def _switch_turn(self) -> None:
        """把回合切换给对手。"""
        self.current_turn = self._opponent(self.current_turn)

    @staticmethod
    def _opponent(player: str) -> str:
        """返回对手的玩家编号。"""
        return "B" if player == "A" else "A"

    @classmethod
    def _validate_player(cls, player: str) -> None:
        """校验玩家编号。"""
        if player not in cls.PLAYERS:
            raise ValueError(f"非法的玩家编号：{player!r}，只允许 {cls.PLAYERS}")

    @classmethod
    def _validate_action(
        cls,
        attacker: str,
        attacker_slot: int,
        defender_slot: int,
        operation: str,
    ) -> None:
        """校验一次行动的四个参数，非法时抛出 :class:`ValueError`。"""
        cls._validate_player(attacker)
        if attacker_slot not in cls.SLOTS:
            raise ValueError(f"非法的攻击方槽位：{attacker_slot!r}，只允许 {cls.SLOTS}")
        if defender_slot not in cls.SLOTS:
            raise ValueError(f"非法的防守方槽位：{defender_slot!r}，只允许 {cls.SLOTS}")
        if operation not in cls.OPERATIONS:
            raise ValueError(f"非法的操作符：{operation!r}，只允许 {cls.OPERATIONS}")
