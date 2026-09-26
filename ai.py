"""AI 对手策略层（四档难度）。

本模块**只依赖** :class:`~game_engine.GameEngine` 的公开接口，不修改任何游戏规则：
AI 和人类玩家一样，只能通过 ``execute_action()`` 行动，唯一多出来的能力是用
:meth:`~game_engine.GameEngine.clone` 在副本上试走，从而"预判"几步之后的结果。

======== ================ ==================================================
难度     策略              说明
======== ================ ==================================================
简单     随机              8 种合法操作里随便挑一个
普通     贪心（1 层）      8 种操作全试一遍，选当前局面评分最高的
困难     极小化极大（2 层） 自己走一步 + 预判对手的最优回应
地狱     极小化极大（4 层） 再加 Alpha-Beta 剪枝与着法排序
======== ================ ==================================================

因为本游戏是**完全信息、无随机数**的确定性回合制博弈，极小化极大搜索得到的结论
是精确的（不依赖采样），这一点在 README 里值得单独说明。
"""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from game_engine import GameEngine, GameOverError

#: 一次操作 = (我方槽位, 对方槽位, 操作符)
Action = Tuple[int, int, str]

#: 赢棋 / 输棋的分值，必须远大于任何普通局面分，保证搜索优先选择取胜
WIN_SCORE = 10 ** 6
#: 正负无穷的工程近似值
INFINITY = 10 ** 9


class BaseAI:
    """AI 基类：公共的合法性校验、局面评估与推演工具。"""

    LABEL: str = "AI"
    DIFFICULTY: str = "base"

    SLOTS: Tuple[int, ...] = (1, 2)
    OPERATIONS: Tuple[str, ...] = ("+", "-")

    def __init__(self, player: str, seed: Optional[int] = None) -> None:
        """
        :param player: 该 AI 执掌的玩家 ``'A'`` 或 ``'B'``
        :param seed: 随机种子；传入固定值可复现同一局对局（测试与模拟用）
        """
        if player not in ("A", "B"):
            raise ValueError(f"非法的玩家编号：{player!r}，只允许 'A' 或 'B'")
        self.player: str = player
        self._rng = random.Random(seed)
        #: 本次决策中推演过的局面数量（用于 UI 展示"AI 思考了多少种可能"）
        self.nodes_examined: int = 0

    @property
    def opponent(self) -> str:
        """对手的玩家编号。"""
        return "B" if self.player == "A" else "A"

    # ------------------------------------------------------------------ 对外入口
    def choose_action(self, engine: GameEngine) -> Action:
        """为 ``self.player`` 选择一步操作。

        :param engine: 当前对局（不会被本方法修改）
        :return: ``(我方槽位, 对方槽位, 操作符)``
        :raises ValueError: 当前不是该 AI 的回合
        :raises GameOverError: 对局已经结束
        """
        if engine.is_game_over():
            raise GameOverError(engine.get_winner())
        if engine.current_turn != self.player:
            raise ValueError(
                f"当前回合属于 {engine.current_turn}，AI({self.player}) 无法行动"
            )
        self.nodes_examined = 0
        return self._decide(engine)

    def _decide(self, engine: GameEngine) -> Action:
        """子类实现真正的决策逻辑。"""
        raise NotImplementedError

    # ------------------------------------------------------------------ 工具箱
    def all_actions(self) -> List[Action]:
        """列出全部 8 种合法操作（4 种槽位组合 × 2 种操作符）。"""
        return [
            (attacker_slot, defender_slot, operation)
            for attacker_slot in self.SLOTS
            for defender_slot in self.SLOTS
            for operation in self.OPERATIONS
        ]

    def simulate(self, engine: GameEngine, action: Action) -> GameEngine:
        """在**副本**上执行一次操作，返回推演后的新引擎（原 engine 不受影响）。"""
        self.nodes_examined += 1
        child = engine.clone()
        try:
            child.execute_action(child.current_turn, action[0], action[1], action[2])
        except GameOverError:  # 对局已结束时推演无意义，原样返回
            pass
        return child

    @staticmethod
    def _hp_of(engine: GameEngine, player: str) -> int:
        return engine.hp_a if player == "A" else engine.hp_b

    @staticmethod
    def _pair_of(engine: GameEngine, player: str) -> Tuple[int, int]:
        return (engine.A1, engine.A2) if player == "A" else (engine.B1, engine.B2)

    def evaluate(self, engine: GameEngine) -> int:
        """从 ``self.player`` 的视角给局面打分，分值越高越有利。

        评估维度（全部是公开信息，无隐藏状态）：

        * 血量差（权重最高）
        * 双方距离"触发技能"的接近程度
        * 巴掌剩余结算次数
        * 锁链状态
        """
        if engine.is_game_over():
            winner = engine.get_winner()
            if winner == self.player:
                return WIN_SCORE
            if winner == self.opponent:
                return -WIN_SCORE
            return 0

        score = 40 * (self._hp_of(engine, self.player) - self._hp_of(engine, self.opponent))
        score += self._skill_potential(engine, self.player)
        score -= self._skill_potential(engine, self.opponent)
        score += 10 * engine.get_slap_turns_left(self.player)
        score -= 10 * engine.get_slap_turns_left(self.opponent)
        score += 12 if engine.is_chained(self.opponent) else 0
        score -= 12 if engine.is_chained(self.player) else 0
        return score

    @classmethod
    def _skill_potential(cls, engine: GameEngine, player: str) -> int:
        """粗略衡量该玩家"距离触发技能有多近"，让 AI 会主动去凑技能。"""
        value_1, value_2 = cls._pair_of(engine, player)
        lowest = min(value_1, value_2)
        ones = {value_1 % 10, value_2 % 10}

        score = 0
        if lowest >= 15:
            score += 3
        if lowest >= 20:
            score += 3
        if lowest >= 30:
            score += 3
        if lowest >= 30 and ones == {5, 0}:            # 恭喜发财：3 点伤害
            score += 6
        if lowest >= 20 and ones == {9}:               # 锁链：双 9 且都 >= 20
            score += 5
        if lowest >= 15 and ones == {8, 1}:            # 糖葫芦：回 2 点血
            score += 5
        if lowest >= 15 and ones == {5}:               # 巴掌：持续伤害
            score += 4
        if ones == {8}:                                # 旋镖
            score += 3
        score += sum(1 for value in (value_1, value_2) if value % 10 in (6, 7))
        return score


class RandomAI(BaseAI):
    """简单：在 8 种合法操作里随机挑一个。"""

    LABEL = "简单"
    DIFFICULTY = "easy"

    def _decide(self, engine: GameEngine) -> Action:
        return self._rng.choice(self.all_actions())


class GreedyAI(BaseAI):
    """普通：贪心 1 层 —— 把 8 种操作都试一遍，选局面评分最高的。"""

    LABEL = "普通"
    DIFFICULTY = "normal"

    def _decide(self, engine: GameEngine) -> Action:
        best_score = -INFINITY
        best_actions: List[Action] = []
        for action in self.all_actions():
            score = self.evaluate(self.simulate(engine, action))
            if score > best_score:
                best_score, best_actions = score, [action]
            elif score == best_score:
                best_actions.append(action)
        return self._rng.choice(best_actions)


class SearchAI(BaseAI):
    """困难 / 地狱：极小化极大搜索（可选 Alpha-Beta 剪枝）。

    :attr:`SEARCH_DEPTH` 表示前瞻的总层数，2 层 = 我走一步 + 对手最优回应。
    """

    LABEL = "搜索"
    DIFFICULTY = "search"
    SEARCH_DEPTH: int = 2
    USE_PRUNING: bool = True

    def _decide(self, engine: GameEngine) -> Action:
        best_value = -INFINITY
        best_actions: List[Action] = []
        for action in self._ordered_actions(engine):
            value = self._search(
                self.simulate(engine, action), self.SEARCH_DEPTH - 1, -INFINITY, INFINITY
            )
            if value > best_value:
                best_value, best_actions = value, [action]
            elif value == best_value:
                best_actions.append(action)
        return self._rng.choice(best_actions)

    def _search(self, engine: GameEngine, depth: int, alpha: int, beta: int) -> int:
        """在 ``engine`` 局面上继续推演 ``depth`` 层，返回该 AI 视角的分值。"""
        if depth <= 0 or engine.is_game_over():
            return self.evaluate(engine)

        mover = engine.current_turn

        # 被锁链缠住的玩家无法选择操作，本回合必然被跳过
        if engine.is_chained(mover):
            return self._search(self.simulate(engine, (1, 1, "+")), depth - 1, alpha, beta)

        # 深层节点才做着法排序：它本身要额外推演，太浅反而拖慢
        actions = self._ordered_actions(engine) if depth >= 2 else self.all_actions()

        if mover == self.player:
            value = -INFINITY
            for action in actions:
                value = max(
                    value,
                    self._search(self.simulate(engine, action), depth - 1, alpha, beta),
                )
                if self.USE_PRUNING:
                    alpha = max(alpha, value)
                    if beta <= alpha:
                        break
            return value

        value = INFINITY
        for action in actions:
            value = min(
                value,
                self._search(self.simulate(engine, action), depth - 1, alpha, beta),
            )
            if self.USE_PRUNING:
                beta = min(beta, value)
                if beta <= alpha:
                    break
        return value

    def _ordered_actions(self, engine: GameEngine) -> List[Action]:
        """着法排序：先试"看起来更好"的着法，显著提升 Alpha-Beta 剪枝效率。"""
        actions = self.all_actions()
        if not self.USE_PRUNING:
            return actions

        mover = engine.current_turn
        scored = []
        for action in actions:
            value = self.evaluate(self.simulate(engine, action))
            # 轮到自己时希望分值高，轮到对手时希望分值低，统一成"越可能优先展开越靠前"
            scored.append((value if mover == self.player else -value, action))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [action for _, action in scored]


class HardAI(SearchAI):
    """困难：2 层搜索（我走一步 + 对手最优回应）。"""

    LABEL = "困难"
    DIFFICULTY = "hard"
    SEARCH_DEPTH = 2
    USE_PRUNING = True


class HellAI(SearchAI):
    """地狱：4 层搜索 + Alpha-Beta 剪枝 + 着法排序。"""

    LABEL = "地狱"
    DIFFICULTY = "hell"
    SEARCH_DEPTH = 4
    USE_PRUNING = True


#: 难度标识 -> AI 实现类
AI_REGISTRY = {
    RandomAI.DIFFICULTY: RandomAI,
    GreedyAI.DIFFICULTY: GreedyAI,
    HardAI.DIFFICULTY: HardAI,
    HellAI.DIFFICULTY: HellAI,
}

#: 难度标识 -> 中文名（UI 直接取用）
DIFFICULTY_LABELS = {key: cls.LABEL for key, cls in AI_REGISTRY.items()}


def create_ai(difficulty: str, player: str, seed: Optional[int] = None) -> BaseAI:
    """按难度创建 AI 实例。

    :param difficulty: ``'easy'`` / ``'normal'`` / ``'hard'`` / ``'hell'``
    :param player: 该 AI 执掌的玩家 ``'A'`` 或 ``'B'``
    :param seed: 随机种子，用于复现对局
    :raises ValueError: 难度标识非法
    """
    if difficulty not in AI_REGISTRY:
        raise ValueError(
            f"未知难度：{difficulty!r}，可选 {tuple(AI_REGISTRY)}"
        )
    return AI_REGISTRY[difficulty](player, seed)
