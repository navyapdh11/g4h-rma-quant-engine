"""
Monte Carlo Tree Search — V6.0 Production Implementation
=========================================================
Proper MCTS with:
  1. SELECTION   — UCB1 tree traversal
  2. EXPANSION   — add unexplored action node
  3. SIMULATION  — Ornstein-Uhlenbeck rollout
  4. BACKPROPAGATION — reward propagation

V6.0 Enhancements:
  - Adaptive iterations based on volatility regime
  - Improved simulation with spread evolution
  - Better numerical stability
"""
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from config import MCTSConfig
from models import ActionType, MCTSResult
import logging

logger = logging.getLogger(__name__)


@dataclass
class _MCTSNode:
    state_spread: float
    state_S: float
    action: Optional[str] = None
    parent: Optional["_MCTSNode"] = None
    children: List["_MCTSNode"] = field(default_factory=list)
    visits: int = 0
    total_reward: float = 0.0
    untried_actions: List[str] = field(default_factory=lambda: ["LONG_SPREAD", "SHORT_SPREAD", "HOLD"])

    def ucb1(self, c: float = 1.414) -> float:
        if self.visits == 0:
            return float("inf")
        exploit = self.total_reward / self.visits
        explore = c * math.sqrt(math.log(self.parent.visits) / self.visits)
        return exploit + explore

    def best_child_ucb(self, c: float = 1.414) -> "_MCTSNode":
        return max(self.children, key=lambda ch: ch.ucb1(c))

    def best_child_robust(self) -> "_MCTSNode":
        return max(self.children, key=lambda ch: ch.visits)

    def is_fully_expanded(self) -> bool:
        return len(self.untried_actions) == 0

    def is_leaf(self) -> bool:
        return self.action is None


class MCTSEngine:
    """Monte Carlo Tree Search for spread trading decisions."""
    ACTIONS = ["LONG_SPREAD", "SHORT_SPREAD", "HOLD"]

    def __init__(self, cfg: Optional[MCTSConfig] = None):
        self.cfg = cfg or MCTSConfig()
        self._rng = np.random.default_rng()
        self._total_searches: int = 0

    def _rollout(self, spread: float, S: float, vol_scale: float) -> float:
        dt = 1.0 / 252.0
        lam = self.cfg.mean_reversion_speed
        sigma = np.sqrt(max(S, 1e-8)) * vol_scale
        z = spread
        
        for _ in range(self.cfg.rollout_steps):
            noise = self._rng.normal()
            z += lam * (0.0 - z) * dt + sigma * np.sqrt(dt) * noise
        
        return z

    @staticmethod
    def _compute_pnl(entry_spread: float, exit_spread: float, action: str) -> float:
        if action == "LONG_SPREAD":
            return exit_spread - entry_spread
        elif action == "SHORT_SPREAD":
            return entry_spread - exit_spread
        return 0.0

    def _get_adaptive_iterations(self, vol_scale: float) -> int:
        if not self.cfg.adaptive_iterations:
            return self.cfg.iterations
        if vol_scale >= 3.0:
            return int(self.cfg.iterations * 1.5)
        elif vol_scale >= 2.0:
            return int(self.cfg.iterations * 1.25)
        elif vol_scale <= 0.8:
            return int(self.cfg.iterations * 0.75)
        return self.cfg.iterations

    def search(self, current_spread: float, current_S: float, vol_scale: float) -> MCTSResult:
        self._total_searches += 1
        
        if abs(current_spread) < self.cfg.min_spread_magnitude:
            return MCTSResult(
                action=ActionType.HOLD, expected_value=0.0,
                visit_distribution={"HOLD": self.cfg.iterations},
                avg_reward_distribution={"HOLD": 0.0},
            )
        
        iterations = self._get_adaptive_iterations(vol_scale)
        root = _MCTSNode(state_spread=current_spread, state_S=current_S)
        
        for _ in range(iterations):
            node = root
            
            # SELECTION
            while node.is_leaf() and node.is_fully_expanded() and node.children:
                node = node.best_child_ucb(self.cfg.exploration_constant)
            
            # EXPANSION
            if not node.is_fully_expanded():
                action = node.untried_actions.pop()
                child = _MCTSNode(
                    state_spread=node.state_spread, state_S=node.state_S,
                    action=action, parent=node,
                )
                node.children.append(child)
                node = child
            
            # SIMULATION
            future_spread = self._rollout(node.state_spread, node.state_S, vol_scale)
            reward = self._compute_pnl(node.state_spread, future_spread, node.action)
            
            # BACKPROPAGATION
            while node is not None:
                node.visits += 1
                node.total_reward += reward
                node = node.parent
        
        if not root.children:
            return MCTSResult(
                action=ActionType.HOLD, expected_value=0.0,
                visit_distribution={}, avg_reward_distribution={},
            )
        
        best = root.best_child_robust()
        action = ActionType(best.action) if best.action else ActionType.HOLD
        avg_ev = best.total_reward / best.visits if best.visits > 0 else 0.0
        
        if abs(avg_ev) < self.cfg.min_ev_threshold:
            action = ActionType.HOLD
        
        visit_dist = {c.action: c.visits for c in root.children}
        reward_dist = {
            c.action: (c.total_reward / c.visits if c.visits > 0 else 0.0)
            for c in root.children
        }
        
        return MCTSResult(
            action=action, expected_value=round(avg_ev, 4),
            visit_distribution=visit_dist, avg_reward_distribution=reward_dist,
        )
    
    @property
    def total_searches(self) -> int:
        return self._total_searches
    
    def reset_stats(self):
        self._total_searches = 0
