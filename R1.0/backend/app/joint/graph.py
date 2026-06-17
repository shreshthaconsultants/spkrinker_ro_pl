"""Rectilinear corridor routing: Hanan grid + bend-aware Dijkstra.

The header must run along the corridor with clean 90-degree geometry, so
the graph is built on the Hanan grid of the inset corridor's vertices plus
every point of interest (shafts, room taps, corridor heads).  Edge costs
encode three preferences:

- wall bias: mid-corridor edges are cheap, wall-hugging edges expensive,
  so the header runs down the corridor CENTRE like a real main;
- sprinkler clearance: edges passing over/next to a sprinkler head cost
  ~8x, so the header threads BESIDE the head columns (grid lines are
  seeded at head +/- header_offset) and never runs on top of a head;
- bend cost: every 90-degree turn costs extra length, so the route takes
  the fewest corners possible (no staircases, no needless jogs).

Shafts enter the graph as real nodes (add_shaft) with straight edges along
their own row/column and one-bend L edges to the end of every column/row -
the shaft connector therefore takes AT MOST ONE 90-degree turn before the
trunk continues straight.

All node coordinates are rounded to 0.01 mm: the de-tilted geometry of a
rotated building carries ~1e-9 fp noise, and rounding makes the graph (and
therefore every tie-break inside Dijkstra) IDENTICAL to the flat run -
this is what keeps /route-joint rotation-equivariant.  Costs are for route
choice only; segment lengths in the response are true geometric lengths.
"""

import heapq
import itertools
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..geometry import EPS, Pt, dist, polygon_parts
from ..nfpa import MIN_WALL_DIST

_INF = float("inf")

#: slack (mm) added to the inset for membership tests, absorbing fp noise
#: so boundary nodes don't flicker in and out under the tilt round-trip
_COVER_SLACK = 0.01

#: wall-avoidance bias: an edge hugging the wall at the minimum clearance
#: costs ~2.5x its length, a mid-corridor edge ~1.3x
_WALL_BIAS = 300.0

#: an edge passing closer than this (mm) to a sprinkler head is "on the
#: sprinkler" and pays _HEAD_PENALTY; the final stub touching its own
#: target head pays it too, uniformly, so connections still happen
_HEAD_CLEARANCE = 150.0
_HEAD_PENALTY = 8.0

#: cost (mm-equivalent) of one 90-degree turn: fewer corners win even
#: when the straight route is slightly longer or closer to a wall
_BEND_COST = 500.0

#: above this many corridor heads the +/-offset seed lines are skipped to
#: keep the grid small (the head positions themselves are always seeded)
_MAX_OFFSET_SEED_HEADS = 300

#: a shaft connects to at most this many one-bend entry points
_MAX_SHAFT_ENTRIES = 8


def _coord_key(v: float) -> float:
    return round(v, 2)


def _center_coords(corridor) -> list:
    """Ring coordinates of the corridor's deepest inset.

    Binary-searching the largest inward buffer that still exists leaves a
    millimetre-thin sliver tracing the corridor's CENTRELINE; its vertices
    give the Hanan grid the mid-corridor lines that the wall-biased
    Dijkstra then runs the header on.  (The sliver's two sides are ~2 mm
    apart - parallel runs that close merge into one outline when drawn.)
    """
    lo = 0.0
    hi = max(corridor.bounds[2] - corridor.bounds[0],
             corridor.bounds[3] - corridor.bounds[1]) / 2.0
    for _ in range(20):
        mid = (lo + hi) / 2.0
        if corridor.buffer(-mid, join_style=2).is_empty:
            hi = mid
        else:
            lo = mid
    coords = []
    inset = corridor.buffer(-lo * 0.999, join_style=2)
    for part in polygon_parts(inset):
        for ring in (part.exterior, *part.interiors):
            coords.extend(list(ring.coords))
    return coords


class CorridorGraph:
    """Hanan-grid routing graph inside the (inset) corridor polygon."""

    def __init__(self, corridor, seeds: list[Pt], heads: list[Pt] = (),
                 head_offset: float = 300.0):
        # Pipes keep MIN_WALL_DIST clear of the walls.  If that inset is
        # empty or breaks the corridor into pieces (very thin corridor),
        # fall back to the raw polygon: a thin corridor must not kill
        # every path.  Never inset by main_width/2 for the same reason.
        inset = corridor.buffer(1.0 - MIN_WALL_DIST, join_style=2)
        if len(polygon_parts(inset)) != 1:
            inset = corridor.buffer(1.0, join_style=2)
        self.poly = inset
        self._wall = corridor.boundary
        self._cover = corridor.buffer(1.0, join_style=2)
        covered = prep(inset.buffer(_COVER_SLACK))

        self._head_pts = [Point(h) for h in heads]
        self._head_tree = STRtree(self._head_pts) if self._head_pts else None

        xs, ys = set(), set()
        for part in polygon_parts(inset):
            for ring in (part.exterior, *part.interiors):
                for x, y in ring.coords:
                    xs.add(_coord_key(x))
                    ys.add(_coord_key(y))
        for x, y in _center_coords(corridor):
            xs.add(_coord_key(x))
            ys.add(_coord_key(y))
        for p in seeds:
            xs.add(_coord_key(p[0]))
            ys.add(_coord_key(p[1]))
        # lines BESIDE every head, so the trunk has somewhere clean to run
        if heads and len(heads) <= _MAX_OFFSET_SEED_HEADS:
            for h in heads:
                for d in (-head_offset, head_offset):
                    xs.add(_coord_key(h[0] + d))
                    ys.add(_coord_key(h[1] + d))

        self.nodes: list[Pt] = []
        self._index: dict[tuple, int] = {}
        for x in sorted(xs):
            for y in sorted(ys):
                if covered.covers(Point(x, y)):
                    self._index[(x, y)] = len(self.nodes)
                    self.nodes.append([x, y])

        # Edges between grid-adjacent nodes whose connecting segment stays
        # inside the corridor (gaps across a U-shaped opening are rejected).
        self.adj: list[list[tuple[int, float, str]]] = [[] for _ in self.nodes]
        by_x: dict = defaultdict(list)
        by_y: dict = defaultdict(list)
        for i, (x, y) in enumerate(self.nodes):
            by_x[x].append(i)
            by_y[y].append(i)
        for column in by_x.values():
            column.sort(key=lambda i: self.nodes[i][1])
            self._link(column, covered, "v")
        for row in by_y.values():
            row.sort(key=lambda i: self.nodes[i][0])
            self._link(row, covered, "h")

        self._grid_count = len(self.nodes)  # shaft nodes appended after this

    def _edge_cost(self, pa: Pt, pb: Pt) -> float:
        """Biased cost of one straight leg: wall-hugging and running over
        sprinkler heads both cost extra.  The clearance is rounded to
        integer mm so fp noise from the tilt round-trip cannot flip it."""
        length = dist(pa, pb)
        if length <= EPS:
            return 0.0  # degenerate leg (e.g. an L collapsing to a straight)
        line = LineString([pa, pb])
        clear = round(line.distance(self._wall))
        w = length * (1.0 + _WALL_BIAS / (clear + 100.0))
        # outside the corridor a line is FAR from any wall, which would make
        # outside runs the cheapest in the graph - the opposite of reality.
        # Charge them triple: outside is a last resort (the shaft's own
        # unavoidable approach), never a shortcut along the building.
        outside = round(line.difference(self._cover).length)
        if outside > 0:
            w += 2.0 * outside
        if self._on_a_head(line):
            w *= _HEAD_PENALTY
        return w

    def _link(self, ordered: list[int], covered, direction: str) -> None:
        for a, b in zip(ordered, ordered[1:]):
            pa, pb = self.nodes[a], self.nodes[b]
            if not covered.covers(LineString([pa, pb])):
                continue
            w = self._edge_cost(pa, pb)
            self.adj[a].append((b, w, direction))
            self.adj[b].append((a, w, direction))

    def _on_a_head(self, line) -> bool:
        if self._head_tree is None:
            return False
        for i in self._head_tree.query(line.buffer(_HEAD_CLEARANCE)):
            if line.distance(self._head_pts[i]) <= _HEAD_CLEARANCE:
                return True
        return False

    def add_shaft(self, p: Pt) -> int:
        """The shaft as a graph node.

        Straight edges run along the shaft's own column/row; one-bend L
        edges (with the bend already costed) reach the nearest end of
        every other column/row.  The connector therefore takes at most
        ONE 90-degree turn before the trunk continues straight - Dijkstra
        plans the entry and the trunk together instead of gluing a
        connector onto an arbitrary nearest node.
        """
        existing = self.node_at(p)
        if existing is not None:
            return existing
        idx = len(self.nodes)
        self.nodes.append([p[0], p[1]])
        self.adj.append([])

        key_x, key_y = _coord_key(p[0]), _coord_key(p[1])
        by_x: dict = defaultdict(list)
        by_y: dict = defaultdict(list)
        for i in range(self._grid_count):
            x, y = self.nodes[i]
            by_x[x].append(i)
            by_y[y].append(i)
        for i in by_x.get(key_x, []):  # straight up/down the shaft's column
            self.adj[idx].append((i, self._edge_cost(list(p), self.nodes[i]), "v"))
        for i in by_y.get(key_y, []):  # straight along the shaft's row
            self.adj[idx].append((i, self._edge_cost(list(p), self.nodes[i]), "h"))

        # One-bend L entries: both legs pay the wall/head bias, and only
        # the cheapest few are kept - the connector must enter LOCALLY,
        # not bridge to a far-away part of the corridor.
        candidates = []
        for x, column in by_x.items():
            if x == key_x:
                continue
            n = min(column, key=lambda i: abs(self.nodes[i][1] - p[1]))
            corner = [self.nodes[n][0], p[1]]
            w = (self._edge_cost(list(p), corner)
                 + self._edge_cost(corner, self.nodes[n]) + _BEND_COST)
            candidates.append((round(w, 3), self.nodes[n][0], self.nodes[n][1], n, "v"))
        for y, row in by_y.items():
            if y == key_y:
                continue
            n = min(row, key=lambda i: abs(self.nodes[i][0] - p[0]))
            corner = [p[0], self.nodes[n][1]]
            w = (self._edge_cost(list(p), corner)
                 + self._edge_cost(corner, self.nodes[n]) + _BEND_COST)
            candidates.append((round(w, 3), self.nodes[n][0], self.nodes[n][1], n, "h"))
        candidates.sort()
        for w, _, _, n, direction in candidates[:_MAX_SHAFT_ENTRIES]:
            self.adj[idx].append((n, w, direction))
        return idx

    def seal(self, shaft_idx: int) -> None:
        """Remove the shaft's outgoing edges once its trunk exists, so
        every later target splices onto the tree instead of pulling its
        own connector from the shaft (one trunk per shaft).  Grid nodes
        (a shaft sitting exactly on the grid) are never sealed - paths of
        other targets may legitimately pass through them."""
        if shaft_idx >= self._grid_count:
            self.adj[shaft_idx] = []

    def node_at(self, p: Pt):
        return self._index.get((_coord_key(p[0]), _coord_key(p[1])))

    def nearest_node(self, p: Pt):
        if self._grid_count == 0:
            return None
        return min(range(self._grid_count), key=lambda i: dist(self.nodes[i], p))

    def shortest_paths(self, sources):
        """Bend-aware Dijkstra over states (node, incoming direction).

        Primary cost = biased length + _BEND_COST per turn; turn count
        breaks remaining ties.  `sources` is one node index or an iterable
        of them (multi-source: distances from a whole existing tree).
        Returns (cost, prev) dicts keyed by state; direction is 'h', 'v'
        or '' for a start state.
        """
        if isinstance(sources, int):
            sources = (sources,)
        tie = itertools.count()  # heap tie-break: states never compared
        cost: dict = {}
        prev: dict = {}
        heap = []
        for source in sources:
            cost[(source, "")] = (0.0, 0)
            heap.append((0.0, 0, next(tie), source, ""))
        heapq.heapify(heap)
        while heap:
            d, bends, _, u, direction = heapq.heappop(heap)
            if (d, bends) > cost.get((u, direction), (_INF, 0)):
                continue  # stale heap entry
            for v, w, new_dir in self.adj[u]:
                turn = 1 if (direction and new_dir != direction) else 0
                nb = bends + turn
                nd = d + w + turn * _BEND_COST
                if (nd, nb) < cost.get((v, new_dir), (_INF, 0)):
                    cost[(v, new_dir)] = (nd, nb)
                    prev[(v, new_dir)] = (u, direction)
                    heapq.heappush(heap, (nd, nb, next(tie), v, new_dir))
        return cost, prev

    def best_state(self, cost: dict, node: int):
        """Cheapest state at a node, or None when unreachable."""
        reachable = [(cost[s], s) for s in ((node, "h"), (node, "v"), (node, "")) if s in cost]
        return min(reachable)[1] if reachable else None

    def reach_cost(self, cost: dict, node: int) -> float:
        state = self.best_state(cost, node)
        return cost[state][0] if state else _INF


def tree_paths(graph: CorridorGraph, cost: dict, prev: dict,
               source: int, targets: list[int],
               in_tree: set | None = None):
    """Directed edges (a_idx, b_idx) of the union of best paths from the
    source to each target, spliced into a TREE; returns (edges, in_tree).

    Each path is walked back only until it meets a node already in the
    tree, so two equal-cost paths can never close a loop of pipe.  Edges
    come out oriented start -> end in the flow direction (away from the
    shaft) because the walk-back is reversed before appending.  Passing an
    existing in_tree set splices the new paths onto that tree (the set is
    mutated) - used to hang corridor heads off an already-built header.
    """
    if in_tree is None:
        in_tree = {source}
    in_tree.add(source)
    edges: list[tuple[int, int]] = []
    for target in sorted(targets, key=lambda t: graph.reach_cost(cost, t)):
        state = graph.best_state(cost, target)
        if state is None:
            continue  # unreachable: the caller reports it
        chain = []
        node, direction = state
        while node not in in_tree:
            parent = prev.get((node, direction))
            if parent is None:
                break
            p_node, p_dir = parent
            chain.append((p_node, node))
            node, direction = p_node, p_dir
        for a, b in reversed(chain):
            edges.append((a, b))
            in_tree.add(b)
    return edges, in_tree


def merge_collinear(graph: CorridorGraph, edges: list[tuple[int, int]],
                    breaks: frozenset | set = frozenset()):
    """Collapse runs a->b->c with the same direction and a pass-through
    middle node into single (start_idx, end_idx) spans.

    Nodes in `breaks` (e.g. corridor heads sitting ON the run) always end
    a span, so every head stays a segment endpoint and gets its own flow
    arrow.  Shaft entry edges never merge with the trunk: their pseudo
    direction (computed from the L's endpoints) doesn't match an axis.
    """
    if not edges:
        return []
    outs = defaultdict(list)
    ins = defaultdict(list)
    for e in edges:
        outs[e[0]].append(e)
        ins[e[1]].append(e)

    def direction(e):
        a, b = graph.nodes[e[0]], graph.nodes[e[1]]
        dx, dy = b[0] - a[0], b[1] - a[1]
        return ((dx > EPS) - (dx < -EPS), (dy > EPS) - (dy < -EPS))

    spans = []
    consumed = set()
    for e in edges:
        if e in consumed:
            continue
        head = e  # walk back to the start of this straight run
        while True:
            a = head[0]
            if a in breaks:
                break
            if len(ins[a]) == 1 and len(outs[a]) == 1 and direction(ins[a][0]) == direction(head):
                head = ins[a][0]
            else:
                break
        cur = head
        consumed.add(cur)
        while True:  # walk forward, consuming the whole run
            b = cur[1]
            if b in breaks:
                break
            nxt = outs[b]
            if (len(nxt) == 1 and len(ins[b]) == 1
                    and direction(nxt[0]) == direction(cur) and nxt[0] not in consumed):
                cur = nxt[0]
                consumed.add(cur)
            else:
                break
        spans.append((head[0], cur[1]))
    return spans
