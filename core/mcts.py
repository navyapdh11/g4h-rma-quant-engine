"""
Monte Carlo Tree Search — V8.0 Institutional Implementation
============================================================
Proper MCTS with:
  1. SELECTION   — UCB1 tree traversal
  2. EXPANSION   — add unexplored action node
  3. SIMULATION  — Ornstein-Uhlenbeck rollout
  4. BACKPROPAGATION — reward propagation

V8.0 Enhancements:
  - Parallel search with multi-threading
  - Dynamic tree depth based on volatility
  - Kelly criterion integration for position sizing
  - Enhanced simulation with regime switching
  - Improved numerical stability
  - Performance profiling and metrics
"""
from __future__ import annotations
import math
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
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
    depth: int = 0  # V8.0: Track depth for dynamic pruning

    def ucb1(self, c: float = 1.414) -> float:
        if self.visits == 0:
            return float("inf")
        exploit = self.total_reward / self.visits
        # V8.0: Add depth penalty to encourage exploration
        depth_penalty = 0.1 * self.depth
        explore = c * math.sqrt(math.log(self.parent.visits) / self.visits) - depth_penalty
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
        self._current_seed = self.cfg.seed
        self._rng = np.random.default_rng(self.cfg.seed)
        self._total_searches: int = 0
        # V8.0: Performance metrics
        self._search_times: List[float] = []
        self._avg_rewards: List[float] = []

    def set_seed(self, seed: int):
        self._current_seed = seed
        self._rng = np.random.default_rng(seed)

    def get_seed(self) -> Optional[int]:
        return self._current_seed

    def _rollout(self, spread: float, S: float, vol_scale: float, rng: np.random.Generator = None) -> float:
        """V8.0: Enhanced rollout with regime switching."""
        if rng is None:
            rng = self._rng
        
        dt = 1.0 / 252.0
        lam = self.cfg.mean_reversion_speed
        sigma = np.sqrt(max(S, 1e-8)) * vol_scale
        z = spread

        for step in range(self.cfg.rollout_steps):
            noise = rng.normal()
            # V8.0: Add small regime-switching probability
            regime_shift = 0.0
            if rng.random() < 0.05:  # 5% chance of regime shift
                regime_shift = rng.normal(0, sigma * 0.5)
            
            z += lam * (0.0 - z) * dt + sigma * np.sqrt(dt) * noise + regime_shift

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

    def _get_max_depth(self, vol_scale: float) -> int:
        """V8.0: Dynamic depth based on volatility."""
        if not self.cfg.dynamic_depth:
            return self.cfg.max_depth
        # Higher volatility = deeper search needed
        if vol_scale >= 3.0:
            return self.cfg.max_depth
        elif vol_scale >= 2.0:
            return int(self.cfg.max_depth * 0.75)
        else:
            return int(self.cfg.max_depth * 0.5)

    def _parallel_search(self, current_spread: float, current_S: float, 
                        vol_scale: float, iterations: int, worker_id: int) -> _MCTSNode:
        """V8.0: Parallel search worker."""
        # Each worker uses a different seed derived from worker_id
        worker_rng = np.random.default_rng(self._current_seed + worker_id if self._current_seed else worker_id)
        
        root = _MCTSNode(state_spread=current_spread, state_S=current_S, depth=0)
        max_depth = self._get_max_depth(vol_scale)

        for _ in range(iterations):
            node = root

            # SELECTION with depth limit
            while (node.is_leaf() and node.is_fully_expanded() and node.children 
                   and node.depth < max_depth):
                node = node.best_child_ucb(self.cfg.exploration_constant)

            # Early exit if max depth reached
            if node.depth >= max_depth:
                continue

            # EXPANSION
            if not node.is_fully_expanded():
                action = node.untried_actions.pop()
                child = _MCTSNode(
                    state_spread=node.state_spread, state_S=node.state_S,
                    action=action, parent=node, depth=node.depth + 1,
                )
                node.children.append(child)
                node = child

            # SIMULATION with worker-specific RNG
            future_spread = self._rollout(node.state_spread, node.state_S, vol_scale, worker_rng)
            reward = self._compute_pnl(node.state_spread, future_spread, node.action)

            # BACKPROPAGATION
            while node is not None:
                node.visits += 1
                node.total_reward += reward
                node = node.parent

        return root

    def _merge_trees(self, roots: List[_MCTSNode]) -> _MCTSNode:
        """V8.0: Merge multiple search trees into one."""
        merged = _MCTSNode(
            state_spread=roots[0].state_spread,
            state_S=roots[0].state_S,
            depth=0
        )

        # Aggregate children statistics
        action_children = {}
        for root in roots:
            for child in root.children:
                if child.action not in action_children:
                    # Create merged child
                    new_child = _MCTSNode(
                        state_spread=child.state_spread,
                        state_S=child.state_S,
                        action=child.action,
                        parent=merged,
                        depth=child.depth,
                        visits=child.visits,
                        total_reward=child.total_reward,
                    )
                    action_children[child.action] = new_child
                else:
                    # Merge statistics
                    existing = action_children[child.action]
                    existing.visits += child.visits
                    existing.total_reward += child.total_reward

        merged.children = list(action_children.values())
        merged.visits = sum(c.visits for c in merged.children)
        merged.total_reward = sum(c.total_reward for c in merged.children)

        return merged

    def _calculate_kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """V8.0: Calculate optimal Kelly fraction for position sizing."""
        if avg_loss == 0 or win_rate == 0:
            return 0.0
        
        b = avg_win / abs(avg_loss)  # Win/loss ratio
        q = 1 - win_rate
        kelly = (b * win_rate - q) / b
        
        # Apply conservative fraction
        return max(0, kelly * self.cfg.kelly_fraction)

    def search(self, current_spread: float, current_S: float, vol_scale: float) -> MCTSResult:
        """V8.0: Enhanced search with parallel execution."""
        start_time = time.time()
        self._total_searches += 1

        if abs(current_spread) < self.cfg.min_spread_magnitude:
            return MCTSResult(
                action=ActionType.HOLD, expected_value=0.0,
                visit_distribution={"HOLD": self.cfg.iterations},
                avg_reward_distribution={"HOLD": 0.0},
            )

        iterations = self._get_adaptive_iterations(vol_scale)
        
        # V8.0: Parallel search
        if self.cfg.parallel_workers > 1:
            worker_iterations = max(100, iterations // self.cfg.parallel_workers)
            
            with ThreadPoolExecutor(max_workers=self.cfg.parallel_workers) as executor:
                futures = []
                for i in range(self.cfg.parallel_workers):
                    future = executor.submit(
                        self._parallel_search,
                        current_spread, current_S, vol_scale,
                        worker_iterations, i
                    )
                    futures.append(future)
                
                roots = [f.result() for f in as_completed(futures)]
            
            root = self._merge_trees(roots)
        else:
            # Single-threaded fallback
            root = self._parallel_search(current_spread, current_S, vol_scale, iterations, 0)

        elapsed = time.time() - start_time
        self._search_times.append(elapsed)
        if len(self._search_times) > 100:
            self._search_times.pop(0)

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

        # Track metrics for Kelly calculation
        self._avg_rewards.append(avg_ev)
        if len(self._avg_rewards) > 100:
            self._avg_rewards.pop(0)

        return MCTSResult(
            action=action, expected_value=round(avg_ev, 4),
            visit_distribution=visit_dist, avg_reward_distribution=reward_dist,
        )

    def get_kelly_position_size(self, capital: float) -> float:
        """V8.0: Calculate position size using Kelly criterion."""
        if not self.cfg.kelly_enabled or not self._avg_rewards:
            return capital * 0.1  # Default 10% of capital
        
        # Estimate win rate from positive EV searches
        wins = sum(1 for r in self._avg_rewards if r > 0)
        total = len(self._avg_rewards)
        win_rate = wins / total if total > 0 else 0.5
        
        avg_win = np.mean([r for r in self._avg_rewards if r > 0]) if wins > 0 else 0.0
        avg_loss = abs(np.mean([r for r in self._avg_rewards if r < 0])) if (total - wins) > 0 else 0.001
        
        kelly_frac = self._calculate_kelly_fraction(win_rate, avg_win, avg_loss)
        return capital * kelly_frac

    @property
    def total_searches(self) -> int:
        return self._total_searches

    def get_performance_metrics(self) -> Dict[str, float]:
        """V8.0: Return search performance metrics."""
        if not self._search_times:
            return {"avg_search_time_ms": 0, "total_searches": 0}
        
        return {
            "avg_search_time_ms": np.mean(self._search_times) * 1000,
            "max_search_time_ms": max(self._search_times) * 1000,
            "min_search_time_ms": min(self._search_times) * 1000,
            "total_searches": self._total_searches,
        }

    def reset_stats(self):
        self._total_searches = 0
        self._search_times.clear()
        self._avg_rewards.clear()
