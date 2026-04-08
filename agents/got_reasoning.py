"""
Graph-of-Thought (GoT) Reasoning Engine for KAIROS Tick Loop
=============================================================
Implements a directed acyclic graph (DAG) of thought nodes for
multi-agent reasoning in the G4H-RMA Quant Engine.

Architecture:
  - ThoughtNode: Individual reasoning unit (observation, hypothesis, decision, action)
  - GoTGraph: The full reasoning graph with nodes and weighted edges
  - GoTReasoner: Orchestrates tick-loop reasoning with confidence propagation
  - 4-Phase Consolidation: Aggregate → Resolve → Summarize → Prune (REM-style)

Each tick of the KAIROS loop:
  1. Creates observation nodes from market data
  2. Generates hypothesis nodes (branching alternatives)
  3. Evaluates confidence through edge-weighted propagation
  4. Makes decisions when confidence exceeds threshold
  5. Periodically consolidates to prune redundant thoughts
"""
from __future__ import annotations
import logging
import time
import uuid
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class ThoughtType(str, Enum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    DECISION = "decision"
    ACTION = "action"
    SUMMARY = "summary"


@dataclass
class ThoughtNode:
    """A single unit of reasoning in the GoT graph."""
    id: str
    thought_type: ThoughtType
    content: str
    confidence: float  # 0.0 to 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)  # Parent thought IDs
    created_at: float = field(default_factory=time.time)
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.thought_type.value,
            "content": self.content,
            "confidence": round(self.confidence, 4),
            "metadata": self.metadata,
            "dependencies": self.dependencies,
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "active": self.active,
        }


class GoTGraph:
    """
    Directed acyclic graph of thought nodes.
    Supports confidence propagation along weighted edges.
    """

    def __init__(self):
        self._nodes: Dict[str, ThoughtNode] = {}
        self._edges: Dict[str, List[Tuple[str, float]]] = defaultdict(list)  # parent -> [(child, weight)]
        self._reverse_edges: Dict[str, List[str]] = defaultdict(list)  # child -> [parent]
        self._tick_count: int = 0
        self._consolidation_count: int = 0
        self._last_consolidated_at: float = 0.0

    # ── Node Operations ─────────────────────────────────────────────

    def add_node(self, node: ThoughtNode) -> None:
        """Add a thought node to the graph."""
        self._nodes[node.id] = node
        # Register dependency edges
        for dep_id in node.dependencies:
            self._edges[dep_id].append((node.id, 1.0))
            self._reverse_edges[node.id].append(dep_id)

    def get_node(self, node_id: str) -> Optional[ThoughtNode]:
        return self._nodes.get(node_id)

    def get_active_nodes(self, thought_type: Optional[ThoughtType] = None) -> List[ThoughtNode]:
        """Get all active nodes, optionally filtered by type."""
        nodes = [n for n in self._nodes.values() if n.active]
        if thought_type:
            nodes = [n for n in nodes if n.thought_type == thought_type]
        return nodes

    def get_recent_nodes(self, limit: int = 20, thought_type: Optional[ThoughtType] = None) -> List[ThoughtNode]:
        """Get most recent active nodes, sorted by creation time."""
        nodes = self.get_active_nodes(thought_type)
        nodes.sort(key=lambda n: n.created_at, reverse=True)
        return nodes[:limit]

    def deactivate_node(self, node_id: str) -> bool:
        """Deactivate a node (soft deletion)."""
        node = self._nodes.get(node_id)
        if node:
            node.active = False
            return True
        return False

    def get_node_count(self) -> int:
        return len(self._nodes)

    def get_active_count(self) -> int:
        return sum(1 for n in self._nodes.values() if n.active)

    # ── Confidence Propagation ──────────────────────────────────────

    def propagate_confidence(self, source_id: str) -> Dict[str, float]:
        """
        Propagate confidence from a source node through the graph.
        Uses weighted edge traversal with decay.

        Returns: dict of node_id -> propagated_confidence
        """
        result = {}
        visited: Set[str] = set()
        queue: deque = deque([(source_id, 1.0)])  # (node_id, confidence)

        while queue:
            current_id, current_conf = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            node = self._nodes.get(current_id)
            if not node or not node.active:
                continue

            result[current_id] = current_conf

            # Propagate to children with decay
            for child_id, weight in self._edges.get(current_id, []):
                child = self._nodes.get(child_id)
                if child and child.active:
                    decay = 0.85  # Exponential decay factor
                    new_conf = current_conf * weight * decay
                    if new_conf > 0.01:
                        queue.append((child_id, new_conf))

        return result

    # ── Dependency Resolution ───────────────────────────────────────

    def get_ancestors(self, node_id: str, max_depth: int = 5) -> List[ThoughtNode]:
        """Get all ancestor nodes up to max_depth."""
        ancestors = []
        visited: Set[str] = set()
        queue: deque = deque([(node_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if current_id in visited or depth >= max_depth:
                continue
            visited.add(current_id)

            for parent_id in self._reverse_edges.get(current_id, []):
                parent = self._nodes.get(parent_id)
                if parent and parent.active:
                    ancestors.append(parent)
                    queue.append((parent_id, depth + 1))

        return ancestors

    def get_descendants(self, node_id: str, max_depth: int = 5) -> List[ThoughtNode]:
        """Get all descendant nodes up to max_depth."""
        descendants = []
        visited: Set[str] = set()
        queue: deque = deque([(node_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if current_id in visited or depth >= max_depth:
                continue
            visited.add(current_id)

            for child_id, _ in self._edges.get(current_id, []):
                child = self._nodes.get(child_id)
                if child and child.active:
                    descendants.append(child)
                    queue.append((child_id, depth + 1))

        return descendants

    # ── Consolidation (4-Phase REM-style) ──────────────────────────

    def consolidate(self) -> Dict[str, Any]:
        """
        4-Phase REM-style consolidation:
        Phase 1: Aggregate related observations (temporal + semantic clustering)
        Phase 2: Resolve contradictions (keep highest-confidence)
        Phase 3: Create summary thought from remaining nodes
        Phase 4: Prune low-confidence redundant thoughts

        Returns: consolidation statistics
        """
        stats = {
            "phase1_aggregated": 0,
            "phase2_resolved": 0,
            "phase3_summarized": 0,
            "phase4_pruned": 0,
            "remaining_active": 0,
        }

        # Phase 1: Cluster observations by pair/temporal proximity
        observations = self.get_active_nodes(ThoughtType.OBSERVATION)
        clusters = self._cluster_observations(observations)
        stats["phase1_aggregated"] = len(observations) - len(clusters)

        # Phase 2: Resolve contradictory decisions
        decisions = self.get_active_nodes(ThoughtType.DECISION)
        resolved = self._resolve_contradictions(decisions)
        stats["phase2_resolved"] = len(decisions) - len(resolved)

        # Phase 3: Create summary if enough material
        all_active = self.get_active_nodes()
        if len(all_active) > 5:
            summary = self._create_summary(all_active)
            if summary:
                self.add_node(summary)
                stats["phase3_summarized"] = 1

        # Phase 4: Prune low-confidence nodes
        pruned = 0
        for node in self.get_active_nodes():
            if node.confidence < 0.15 and node.thought_type != ThoughtType.OBSERVATION:
                self.deactivate_node(node.id)
                pruned += 1
            # Also prune very old hypotheses/decisions
            elif (node.thought_type in (ThoughtType.HYPOTHESIS, ThoughtType.DECISION)
                  and time.time() - node.created_at > 3600):  # 1 hour TTL
                self.deactivate_node(node.id)
                pruned += 1
        stats["phase4_pruned"] = pruned

        stats["remaining_active"] = self.get_active_count()
        self._consolidation_count += 1
        self._last_consolidated_at = time.time()

        logger.info(f"GoT consolidation #{self._consolidation_count}: {stats}")
        return stats

    def _cluster_observations(self, observations: List[ThoughtNode]) -> List[List[ThoughtNode]]:
        """Cluster observations by pair (from metadata)."""
        clusters: Dict[str, List[ThoughtNode]] = defaultdict(list)
        for obs in observations:
            pair = obs.metadata.get("pair", "unknown")
            clusters[pair].append(obs)

        # Keep only the most recent 3 observations per cluster
        for pair in clusters:
            clusters[pair].sort(key=lambda n: n.created_at, reverse=True)
            for old_obs in clusters[pair][3:]:
                self.deactivate_node(old_obs.id)

        return [nodes for nodes in clusters.values() if nodes]

    def _resolve_contradictions(self, decisions: List[ThoughtNode]) -> List[ThoughtNode]:
        """Resolve contradictory decisions by keeping highest-confidence."""
        by_pair: Dict[str, List[ThoughtNode]] = defaultdict(list)
        for dec in decisions:
            pair = dec.metadata.get("pair", "unknown")
            by_pair[pair].append(dec)

        resolved = []
        for pair, pair_decisions in by_pair.items():
            if len(pair_decisions) <= 1:
                resolved.extend(pair_decisions)
                continue

            # Group by signal direction
            buy_decisions = [d for d in pair_decisions if "BUY" in d.content]
            sell_decisions = [d for d in pair_decisions if "SELL" in d.content]
            hold_decisions = [d for d in pair_decisions if "HOLD" in d.content]

            # Keep only the highest-confidence group AND prune redundant within group
            if buy_decisions and sell_decisions:
                buy_max = max(buy_decisions, key=lambda d: d.confidence)
                sell_max = max(sell_decisions, key=lambda d: d.confidence)
                winner = buy_max if buy_max.confidence >= sell_max.confidence else sell_max
                # Deactivate ALL losing side
                losers = buy_decisions if winner in sell_decisions else sell_decisions
                for loser in losers:
                    self.deactivate_node(loser.id)
                # Also deactivate redundant within winning side (keep only best)
                winners = sell_decisions if winner in sell_decisions else buy_decisions
                for w in winners:
                    if w.id != winner.id:
                        self.deactivate_node(w.id)
                resolved.append(winner)
            else:
                # Only one direction — keep highest confidence, deactivate rest
                best = max(pair_decisions, key=lambda d: d.confidence)
                for d in pair_decisions:
                    if d.id != best.id:
                        self.deactivate_node(d.id)
                resolved.append(best)

        return resolved

    def _create_summary(self, nodes: List[ThoughtNode]) -> Optional[ThoughtNode]:
        """Create a summary thought from active nodes."""
        if not nodes:
            return None

        pairs_seen = set()
        signals_seen = []
        for n in nodes:
            pair = n.metadata.get("pair", "")
            if pair:
                pairs_seen.add(pair)
            if n.thought_type == ThoughtType.DECISION:
                signals_seen.append(n.content)

        summary_content = (
            f"GoT Summary: {len(nodes)} active thoughts across {len(pairs_seen)} pairs. "
            f"Recent signals: {'; '.join(signals_seen[-3:]) if signals_seen else 'None'}."
        )

        return ThoughtNode(
            id=f"summary-{uuid.uuid4().hex[:8]}",
            thought_type=ThoughtType.SUMMARY,
            content=summary_content,
            confidence=0.95,
            metadata={"pairs": list(pairs_seen), "signal_count": len(signals_seen)},
            dependencies=[n.id for n in nodes[-5:]],  # Reference 5 most recent
        )

    # ── State Export ────────────────────────────────────────────────

    def export_state(self) -> Dict[str, Any]:
        """Export full graph state for dashboard visualization."""
        nodes = [n.to_dict() for n in self.get_active_nodes()]
        edges = []
        for parent_id, children in self._edges.items():
            for child_id, weight in children:
                child = self._nodes.get(child_id)
                if child and child.active:
                    edges.append({
                        "from": parent_id,
                        "to": child_id,
                        "weight": round(weight, 3),
                    })

        return {
            "nodes": nodes,
            "edges": edges,
            "tick_count": self._tick_count,
            "consolidation_count": self._consolidation_count,
            "last_consolidated_at": datetime.fromtimestamp(
                self._last_consolidated_at, tz=timezone.utc
            ).isoformat() if self._last_consolidated_at else None,
            "total_nodes": self.get_node_count(),
            "active_nodes": self.get_active_count(),
        }

    def tick(self) -> int:
        """Increment tick counter."""
        self._tick_count += 1
        return self._tick_count


class GoTReasoner:
    """
    Orchestrates Graph-of-Thought reasoning for the KAIROS tick loop.

    Each tick:
    1. Create observation nodes from market data
    2. Generate hypothesis nodes (what-if scenarios)
    3. Evaluate decisions based on propagated confidence
    4. Return actionable output with full reasoning trail
    """

    def __init__(self, decision_threshold: float = 0.7):
        self.graph = GoTGraph()
        self.decision_threshold = decision_threshold
        self._consolidation_interval = 50  # Ticks between consolidations

    def process_tick(
        self,
        pair: str,
        kalman_z: float,
        egarch_regime: str,
        egarch_vol: float,
        mcts_ev: float,
        mcts_action: str,
        agent_signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Process a single tick of market data through GoT reasoning.

        Returns: dict with final decision, confidence, and full reasoning trail.
        """
        tick_num = self.graph.tick()

        # Phase 1: Create observation node
        observation = self._create_observation(
            pair, kalman_z, egarch_regime, egarch_vol, mcts_ev, mcts_action
        )
        self.graph.add_node(observation)

        # Phase 2: Generate hypothesis nodes (branching alternatives)
        hypotheses = self._generate_hypotheses(observation, pair, agent_signals)
        for hyp in hypotheses:
            self.graph.add_node(hyp)

        # Phase 3: Evaluate and create decision node
        decision = self._evaluate_decision(
            observation, hypotheses, pair, agent_signals
        )
        self.graph.add_node(decision)

        # Phase 4: Confidence propagation
        propagated = self.graph.propagate_confidence(decision.id)

        # Phase 5: Determine actionability
        actionable = decision.confidence >= self.decision_threshold

        # Phase 6: Periodic consolidation
        consolidation_stats = None
        if tick_num % self._consolidation_interval == 0:
            consolidation_stats = self.graph.consolidate()

        return {
            "tick": tick_num,
            "observation_id": observation.id,
            "hypothesis_ids": [h.id for h in hypotheses],
            "decision": decision.to_dict(),
            "actionable": actionable,
            "propagated_confidence": propagated,
            "consolidation": consolidation_stats,
        }

    def _create_observation(
        self,
        pair: str,
        kalman_z: float,
        egarch_regime: str,
        egarch_vol: float,
        mcts_ev: float,
        mcts_action: str,
    ) -> ThoughtNode:
        """Create an observation node from market data."""
        content = (
            f"{pair}: Kalman Z={kalman_z:.2f}, "
            f"Vol={egarch_vol:.1%} [{egarch_regime}], "
            f"MCTS EV={mcts_ev:.3f} [{mcts_action}]"
        )

        # Base confidence from signal strength
        base_conf = min(
            abs(kalman_z) / 3.0 * 0.4 +
            abs(mcts_ev) * 10 * 0.4 +
            (0.2 if egarch_regime in ("LOW", "NORMAL") else 0.1),
            1.0,
        )

        return ThoughtNode(
            id=f"obs-{pair.replace('/', '-')}-{uuid.uuid4().hex[:8]}",
            thought_type=ThoughtType.OBSERVATION,
            content=content,
            confidence=base_conf,
            metadata={
                "pair": pair,
                "kalman_z": round(kalman_z, 4),
                "egarch_regime": egarch_regime,
                "egarch_vol": round(egarch_vol, 4),
                "mcts_ev": round(mcts_ev, 4),
                "mcts_action": mcts_action,
            },
        )

    def _generate_hypotheses(
        self,
        observation: ThoughtNode,
        pair: str,
        agent_signals: List[Dict[str, Any]],
    ) -> List[ThoughtNode]:
        """Generate hypothesis nodes based on market conditions."""
        hypotheses = []
        meta = observation.metadata
        kalman_z = meta.get("kalman_z", 0)
        mcts_ev = meta.get("mcts_ev", 0)

        # Hypothesis 1: Mean reversion
        if abs(kalman_z) > 1.0:
            direction = "bearish" if kalman_z > 0 else "bullish"
            hypotheses.append(ThoughtNode(
                id=f"hyp-meanrev-{uuid.uuid4().hex[:8]}",
                thought_type=ThoughtType.HYPOTHESIS,
                content=f"Mean reversion likely for {pair} ({direction}, Z={kalman_z:.2f})",
                confidence=min(abs(kalman_z) / 4.0, 0.9),
                metadata={"pair": pair, "hypothesis_type": "mean_reversion"},
                dependencies=[observation.id],
            ))

        # Hypothesis 2: Volatility regime shift
        egarch_regime = meta.get("egarch_regime", "NORMAL")
        if egarch_regime in ("ELEVATED", "CRISIS"):
            hypotheses.append(ThoughtNode(
                id=f"hyp-volshift-{uuid.uuid4().hex[:8]}",
                thought_type=ThoughtType.HYPOTHESIS,
                content=f"Volatility regime shift for {pair}: {egarch_regime} — reduce exposure",
                confidence=0.7 if egarch_regime == "CRISIS" else 0.5,
                metadata={"pair": pair, "hypothesis_type": "volatility_shift"},
                dependencies=[observation.id],
            ))

        # Hypothesis 3: Agent consensus alignment
        if agent_signals:
            buy_count = sum(1 for s in agent_signals if "BUY" in s.get("signal", ""))
            sell_count = sum(1 for s in agent_signals if "SELL" in s.get("signal", ""))
            total = len(agent_signals)

            if buy_count >= total * 0.6:
                hypotheses.append(ThoughtNode(
                    id=f"hyp-consensus-{uuid.uuid4().hex[:8]}",
                    thought_type=ThoughtType.HYPOTHESIS,
                    content=f"Agent consensus: {buy_count}/{total} agents bullish on {pair}",
                    confidence=buy_count / total,
                    metadata={"pair": pair, "hypothesis_type": "agent_consensus", "buy_count": buy_count, "total": total},
                    dependencies=[observation.id],
                ))
            elif sell_count >= total * 0.6:
                hypotheses.append(ThoughtNode(
                    id=f"hyp-consensus-{uuid.uuid4().hex[:8]}",
                    thought_type=ThoughtType.HYPOTHESIS,
                    content=f"Agent consensus: {sell_count}/{total} agents bearish on {pair}",
                    confidence=sell_count / total,
                    metadata={"pair": pair, "hypothesis_type": "agent_consensus", "sell_count": sell_count, "total": total},
                    dependencies=[observation.id],
                ))

        return hypotheses

    def _evaluate_decision(
        self,
        observation: ThoughtNode,
        hypotheses: List[ThoughtNode],
        pair: str,
        agent_signals: List[Dict[str, Any]],
    ) -> ThoughtNode:
        """Evaluate and create the final decision node."""
        meta = observation.metadata
        kalman_z = meta.get("kalman_z", 0)
        mcts_ev = meta.get("mcts_ev", 0)
        mcts_action = meta.get("mcts_action", "HOLD")
        egarch_regime = meta.get("egarch_regime", "NORMAL")

        # Weighted composite from all evidence
        evidence_score = 0.0
        evidence_parts = []

        # Kalman signal (weight 0.3)
        kalman_weight = 0.3
        kalman_signal = abs(kalman_z) / 3.0
        if kalman_z < -1.5:
            evidence_score += kalman_signal * kalman_weight
            evidence_parts.append(f"Kalman bullish ({kalman_z:.2f})")
        elif kalman_z > 1.5:
            evidence_score -= kalman_signal * kalman_weight
            evidence_parts.append(f"Kalman bearish ({kalman_z:.2f})")

        # MCTS signal (weight 0.3)
        mcts_weight = 0.3
        mcts_signal = min(abs(mcts_ev) * 10, 1.0)
        if mcts_ev > 0.05:
            evidence_score += mcts_signal * mcts_weight
            evidence_parts.append(f"MCTS bullish (EV={mcts_ev:.3f})")
        elif mcts_ev < -0.05:
            evidence_score -= mcts_signal * mcts_weight
            evidence_parts.append(f"MCTS bearish (EV={mcts_ev:.3f})")

        # Agent consensus (weight 0.25)
        agent_weight = 0.25
        if agent_signals:
            buy_count = sum(1 for s in agent_signals if "BUY" in s.get("signal", ""))
            sell_count = sum(1 for s in agent_signals if "SELL" in s.get("signal", ""))
            total = len(agent_signals)
            agent_score = (buy_count - sell_count) / total
            evidence_score += agent_score * agent_weight
            evidence_parts.append(f"Agents: {buy_count}B/{sell_count}S/{total - buy_count - sell_count}H")

        # Volatility penalty (weight 0.15)
        vol_weight = 0.15
        vol_penalty = 0.0
        if egarch_regime == "CRISIS":
            vol_penalty = 0.8
            evidence_parts.append("CRISIS regime — strong penalty")
        elif egarch_regime == "ELEVATED":
            vol_penalty = 0.4
            evidence_parts.append("ELEVATED regime — moderate penalty")
        evidence_score -= vol_penalty * vol_weight

        # Normalize to [0, 1]
        normalized_score = (evidence_score + 1.0) / 2.0
        normalized_score = max(0.0, min(1.0, normalized_score))

        # Determine signal direction
        if normalized_score > 0.65:
            signal = "STRONG_BUY"
            action = "EXECUTE_LONG"
        elif normalized_score > 0.55:
            signal = "BUY"
            action = "CONSIDER_LONG"
        elif normalized_score < 0.35:
            signal = "STRONG_SELL"
            action = "EXECUTE_SHORT"
        elif normalized_score < 0.45:
            signal = "SELL"
            action = "CONSIDER_SHORT"
        else:
            signal = "HOLD"
            action = "WAIT"

        confidence = max(abs(normalized_score - 0.5) * 2, 0.1)

        # Dependency on all hypotheses
        dep_ids = [observation.id] + [h.id for h in hypotheses]

        return ThoughtNode(
            id=f"dec-{pair.replace('/', '-')}-{uuid.uuid4().hex[:8]}",
            thought_type=ThoughtType.DECISION,
            content=f"GoT Decision: {signal} {pair} — {action} (confidence={confidence:.3f})",
            confidence=confidence,
            metadata={
                "pair": pair,
                "signal": signal,
                "action": action,
                "evidence_parts": evidence_parts,
                "evidence_score": round(evidence_score, 4),
                "normalized_score": round(normalized_score, 4),
            },
            dependencies=dep_ids,
        )

    def get_dashboard_state(self) -> Dict[str, Any]:
        """Export GoT state for dashboard visualization."""
        return self.graph.export_state()


# ── Global Instance ─────────────────────────────────────────────────

_reasoner: Optional[GoTReasoner] = None


def get_got_reasoner() -> GoTReasoner:
    """Get or create the global GoT reasoner instance."""
    global _reasoner
    if _reasoner is None:
        _reasoner = GoTReasoner(decision_threshold=0.65)
    return _reasoner


def reset_reasoner():
    """Reset the global reasoner instance (useful for testing)."""
    global _reasoner
    _reasoner = None
