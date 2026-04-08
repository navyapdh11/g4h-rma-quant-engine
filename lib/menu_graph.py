"""
DFS-Driven Dynamic Menu Navigation System
==========================================
Implements Depth-First Search over a directed graph of dashboard pages.

Features:
  - Graph-based menu structure with parent-child relationships
  - DFS traversal for path finding and accessibility checks
  - Role-based permission filtering at graph construction time
  - Fuzzy search over all accessible nodes
  - Breadcrumb path reconstruction via DFS backtracking
  - URL hash-based browser history support
  - Keyboard navigation (arrow keys, Enter, Escape)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class MenuNode:
    """A single node in the dashboard navigation graph."""
    id: str
    label: str
    tab_id: str  # Maps to dashboard tab ID
    icon: str  # Emoji or CSS icon class
    description: str
    permissions: List[str]  # ["*"] = all, or specific roles
    children: List[str] = field(default_factory=list)  # Child node IDs
    keywords: List[str] = field(default_factory=list)  # Search keywords
    load_fn: str = ""  # JS function to call when navigating here
    order: int = 0  # Display order among siblings


@dataclass
class MenuPath:
    """A path from root to a node via DFS."""
    node: MenuNode
    breadcrumbs: List[MenuNode]
    depth: int


class DFSMenuNavigator:
    """
    DFS-powered menu navigation engine.

    Builds a directed graph of dashboard pages and provides:
    - DFS traversal from any starting node
    - Path finding between nodes
    - Permission-filtered subgraph extraction
    - Fuzzy search across all accessible nodes
    - Breadcrumb reconstruction
    """

    def __init__(self):
        self._nodes: Dict[str, MenuNode] = {}
        self._adjacency: Dict[str, List[str]] = {}
        self._built = False

    # ── Graph Construction ──────────────────────────────────────────

    def build_default_graph(self) -> "DFSMenuNavigator":
        """Build the default G4H-RMA Quant Engine dashboard graph."""
        nodes = [
            MenuNode(
                id="root",
                label="Dashboard",
                tab_id="dashboard",
                icon="📊",
                description="System health overview, Kalman/EGARCH/MCTS status",
                permissions=["*"],
                children=["agents", "trading", "scanner"],
                keywords=["home", "overview", "health", "status", "main"],
                load_fn="loadDashboard",
                order=0,
            ),
            MenuNode(
                id="agents",
                label="Agents",
                tab_id="agents",
                icon="🤖",
                description="Multi-agent status, signals, GoT reasoning graph",
                permissions=["*"],
                children=["agent-detail"],
                keywords=["bot", "ai", "scout", "analyst", "trader", "risk", "sentinel", "agent", "got"],
                load_fn="loadAgents",
                order=1,
            ),
            MenuNode(
                id="agent-detail",
                label="Agent Detail",
                tab_id="agents",
                icon="🔬",
                description="Detailed agent state, performance metrics, trade history",
                permissions=["*"],
                children=[],
                keywords=["detail", "performance", "metrics", "win rate"],
                load_fn="loadAgentDetail",
                order=0,
            ),
            MenuNode(
                id="trading",
                label="Auto-Trading",
                tab_id="trading",
                icon="⚡",
                description="Auto-trading controls, active trades, execution status",
                permissions=["*"],
                children=[],
                keywords=["trade", "execute", "auto", "live", "paper", "order"],
                load_fn="loadTradingData",
                order=2,
            ),
            MenuNode(
                id="scanner",
                label="Scanner",
                tab_id="scanner",
                icon="🔍",
                description="Market scanner for equity, international, commodity pairs",
                permissions=["*"],
                children=["equities", "intl", "commodities"],
                keywords=["scan", "market", "opportunity", "signal", "pair"],
                load_fn="loadScanner",
                order=3,
            ),
            MenuNode(
                id="equities",
                label="Equity Trading",
                tab_id="equities",
                icon="🏛️",
                description="US equity pair scanning and trading (SPY/QQQ, GLD/SLV, etc.)",
                permissions=["*"],
                children=[],
                keywords=["stock", "equity", "spy", "qqq", "gld", "slv", "us"],
                load_fn="loadEquityPairs",
                order=0,
            ),
            MenuNode(
                id="intl",
                label="International ADR",
                tab_id="intl",
                icon="🌍",
                description="International ADR pair scanning and analysis",
                permissions=["*"],
                children=[],
                keywords=["international", "adr", "foreign", "global", "emerging"],
                load_fn="loadIntlPairs",
                order=1,
            ),
            MenuNode(
                id="commodities",
                label="Commodities Futures",
                tab_id="commodities",
                icon="🧪",
                description="Commodity futures scanning (Gold, Oil, etc.)",
                permissions=["*"],
                children=[],
                keywords=["commodity", "gold", "oil", "futures", "raw material"],
                load_fn="loadCommodityPairs",
                order=2,
            ),
            MenuNode(
                id="connections",
                label="Connections",
                tab_id="connections",
                icon="🔗",
                description="Broker/exchange connections (Alpaca, Binance, Bybit, IBKR, etc.)",
                permissions=["admin", "ops"],
                children=[],
                keywords=["broker", "exchange", "alpaca", "binance", "bybit", "ibkr", "futu", "tiger", "connect"],
                load_fn="loadConnections",
                order=4,
            ),
            MenuNode(
                id="pnl",
                label="P&L",
                tab_id="pnl",
                icon="💰",
                description="Profit & Loss tracking, daily stats, performance charts",
                permissions=["*"],
                children=[],
                keywords=["profit", "loss", "pnl", "money", "performance", "chart", "daily"],
                load_fn="loadPnL",
                order=5,
            ),
            MenuNode(
                id="recommendations",
                label="Recommendations",
                tab_id="recommendations",
                icon="🎯",
                description="AI-powered trading recommendations and signals",
                permissions=["*"],
                children=[],
                keywords=["recommend", "signal", "ai", "suggestion", "tip", "alpha"],
                load_fn="loadRecommendations",
                order=6,
            ),
            MenuNode(
                id="guide",
                label="Guide",
                tab_id="guide",
                icon="📚",
                description="FAQs, How-Tos, system documentation and math",
                permissions=["*"],
                children=[],
                keywords=["help", "faq", "guide", "docs", "documentation", "howto", "math", "theory"],
                load_fn="renderGuide",
                order=7,
            ),
        ]

        self._nodes = {n.id: n for n in nodes}
        self._adjacency = {n.id: list(n.children) for n in nodes if n.children}
        self._built = True
        logger.info(f"DFS menu graph built with {len(self._nodes)} nodes")
        return self

    # ── Permission Filtering ────────────────────────────────────────

    def filter_by_role(self, role: str) -> Dict[str, MenuNode]:
        """Return subgraph accessible to a given role."""
        accessible = {}
        for node_id, node in self._nodes.items():
            if "*" in node.permissions or role in node.permissions:
                accessible[node_id] = node
        return accessible

    def get_accessible_nodes(self, role: str = "*") -> List[MenuNode]:
        """Get all nodes accessible to a role, sorted by order."""
        nodes = self.filter_by_role(role)
        return sorted(nodes.values(), key=lambda n: (n.order, n.id))

    def get_top_level_nodes(self, role: str = "*") -> List[MenuNode]:
        """Get root-level navigation nodes (children of root)."""
        root = self._nodes.get("root")
        if not root:
            return []
        accessible = self.filter_by_role(role)
        result = []
        for child_id in root.children:
            if child_id in accessible:
                result.append(accessible[child_id])
        return sorted(result, key=lambda n: n.order)

    # ── DFS Traversal ───────────────────────────────────────────────

    def dfs_find(
        self,
        start_id: str = "root",
        predicate: Callable[[MenuNode], bool] = lambda n: True,
        role: str = "*",
    ) -> Optional[MenuPath]:
        """
        DFS search from start_id, finding first node matching predicate.

        Returns MenuPath with full breadcrumb trail, or None.
        """
        accessible = self.filter_by_role(role)
        if start_id not in accessible:
            return None

        visited: Set[str] = set()
        # Stack: (current_node_id, path_so_far)
        stack: deque = deque([(start_id, [accessible[start_id]])])

        while stack:
            current_id, path = stack.pop()

            if current_id in visited:
                continue
            if current_id not in accessible:
                continue

            visited.add(current_id)
            current_node = accessible[current_id]

            if predicate(current_node):
                return MenuPath(
                    node=current_node,
                    breadcrumbs=list(path),
                    depth=len(path) - 1,
                )

            # Add children in reverse order for correct DFS ordering
            children = self._adjacency.get(current_id, [])
            for child_id in reversed(children):
                if child_id not in visited and child_id in accessible:
                    new_path = path + [accessible[child_id]]
                    stack.append((child_id, new_path))

        return None

    def dfs_find_by_tab(self, tab_id: str, role: str = "*") -> Optional[MenuPath]:
        """Find a node by its tab_id using DFS."""
        return self.dfs_find(
            predicate=lambda n: n.tab_id == tab_id,
            role=role,
        )

    def dfs_find_by_keyword(self, keyword: str, role: str = "*") -> List[MenuPath]:
        """Find all nodes matching a keyword via DFS traversal."""
        accessible = self.filter_by_role(role)
        keyword_lower = keyword.lower()
        results = []

        visited: Set[str] = set()
        stack: deque = deque([("root", [accessible.get("root")])])

        while stack:
            current_id, path = stack.pop()
            if current_id in visited or current_id not in accessible:
                continue

            visited.add(current_id)
            node = accessible[current_id]

            # Check keyword match
            matches = (
                keyword_lower in node.label.lower()
                or keyword_lower in node.description.lower()
                or any(keyword_lower in kw.lower() for kw in node.keywords)
            )
            if matches:
                results.append(MenuPath(
                    node=node,
                    breadcrumbs=list(path),
                    depth=len(path) - 1,
                ))

            children = self._adjacency.get(current_id, [])
            for child_id in reversed(children):
                if child_id not in visited and child_id in accessible:
                    new_path = path + [accessible[child_id]]
                    stack.append((child_id, new_path))

        return results

    def dfs_all_paths(self, start_id: str = "root", role: str = "*") -> List[List[MenuNode]]:
        """
        Enumerate all root-to-leaf paths via DFS.
        Useful for generating sitemaps and navigation trees.
        """
        accessible = self.filter_by_role(role)
        if start_id not in accessible:
            return []

        paths = []
        visited: Set[str] = set()

        def _dfs(node_id: str, current_path: List[MenuNode]):
            if node_id in visited or node_id not in accessible:
                return
            visited.add(node_id)

            node = accessible[node_id]
            current_path.append(node)

            children = self._adjacency.get(node_id, [])
            has_unvisited_children = False
            for child_id in children:
                if child_id not in visited and child_id in accessible:
                    has_unvisited_children = True
                    _dfs(child_id, current_path)

            if not has_unvisited_children:
                paths.append(list(current_path))

            current_path.pop()
            visited.discard(node_id)

        _dfs(start_id, [])
        return paths

    # ── Breadcrumb & Navigation Helpers ─────────────────────────────

    def get_breadcrumbs(self, node_id: str, role: str = "*") -> List[MenuNode]:
        """Get breadcrumb path from root to node."""
        path = self.dfs_find(
            predicate=lambda n: n.id == node_id,
            role=role,
        )
        return path.breadcrumbs if path else []

    def get_navigation_tree(self, role: str = "*") -> Dict:
        """
        Build a nested navigation tree for frontend rendering.
        Returns a dict structure suitable for JSON serialization.
        """
        accessible = self.filter_by_role(role)
        root = accessible.get("root")
        if not root:
            return {}

        def _build_tree(node_id: str) -> Dict:
            node = accessible.get(node_id)
            if not node:
                return {}

            children = self._adjacency.get(node_id, [])
            return {
                "id": node.id,
                "label": node.label,
                "tabId": node.tab_id,
                "icon": node.icon,
                "description": node.description,
                "loadFn": node.load_fn,
                "order": node.order,
                "children": [_build_tree(c) for c in children if c in accessible],
            }

        return _build_tree("root")

    def fuzzy_search(self, query: str, role: str = "*", limit: int = 10) -> List[Dict]:
        """
        Fuzzy search across all accessible nodes.
        Returns ranked results by relevance.
        """
        accessible = self.filter_by_role(role)
        query_lower = query.lower()
        query_words = query_lower.split()
        results = []

        for node_id, node in accessible.items():
            if node_id == "root":
                continue

            score = 0
            label_lower = node.label.lower()
            desc_lower = node.description.lower()
            keywords_lower = [kw.lower() for kw in node.keywords]

            # Exact label match (highest score)
            if query_lower in label_lower:
                score += 100
            # Word-level label match
            for word in query_words:
                if word in label_lower:
                    score += 30

            # Description match
            if query_lower in desc_lower:
                score += 20
            for word in query_words:
                if word in desc_lower:
                    score += 10

            # Keyword match
            for kw in keywords_lower:
                if query_lower in kw:
                    score += 50
                for word in query_words:
                    if word in kw:
                        score += 15

            if score > 0:
                results.append({
                    "node": node,
                    "score": score,
                    "breadcrumbs": self.get_breadcrumbs(node_id, role),
                })

        # Sort by score descending
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]


# ── Global Instance ─────────────────────────────────────────────────

_navigator: Optional[DFSMenuNavigator] = None


def get_menu_navigator() -> DFSMenuNavigator:
    """Get or create the global menu navigator instance."""
    global _navigator
    if _navigator is None:
        _navigator = DFSMenuNavigator().build_default_graph()
    return _navigator


def reset_navigator():
    """Reset the global navigator instance (useful for testing)."""
    global _navigator
    _navigator = None
