"""Joint-mode planner: corridor header first, then room by room.

1. Room rings are cleaned; a ring that covers the whole corridor is a
   building outline, not a room, and is set aside (status "outline").
2. Taps are detected BEFORE heads are assigned, so a room that cannot
   reach the corridor never claims heads - they fall through to another
   room or to the corridor instead of being lost.
3. Heads are assigned to tappable rooms (point-in-polygon), then to the
   corridor; heads in neither are skipped.
4. Each shaft routes a HEADER tree through the corridor graph to the taps
   of its rooms (rooms go to the shaft with the cheapest header path -
   graph distance, not Euclidean: straight-line misjudges U corridors).
5. Each room is fed through its tap by a SUB-HEADER that sits beside the
   sprinkler columns, with branch rows to the heads.
6. Corridor heads hang off the nearest header run with orthogonal stubs;
   when the straight stub would cross a concave gap (U corridor) the head
   is routed through the graph instead, splicing onto the existing tree.
   Nothing is ever drawn outside the corridor: a connection that cannot
   stay inside skips the head (visible) rather than crossing a wall.
"""

import statistics
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Point

from shapely import affinity

from ..geometry import EPS, Pt, clean_polygon, dist, rotate
from ..nfpa import spacing_for
from .angle import grid_angle
from ..routing import (
    _BOUNDARY_SLACK,
    RouteGroup,
    Segment,
    _cluster_rows,
    _infer_spacing,
    _main_segments,
    _seg,
)
from .graph import _HEAD_CLEARANCE, CorridorGraph, merge_collinear, tree_paths
from .paths import ortho_segments
from .rooms import _branch_segments_ortho, route_room_tree
from .tap import detect_tap

_INF = float("inf")


@dataclass
class RoomStatus:
    index: int
    head_count: int
    status: str   # tapped | fallback | empty | outline | skipped
    shaft: int    # -1 when the room is not connected


@dataclass
class JointPlan:
    segments: list[Segment]
    risers: list[Pt]
    groups: list[RouteGroup]
    rooms: list[RoomStatus]
    total_length: float
    skipped_heads: int
    skipped_rooms: int


def route_joint(points: list[Pt], rooms: list[list[Pt]], corridor: list[Pt],
                risers: list[Pt], hazard: str | None = None,
                header_offset: float = 300.0, auto_tilt: bool = False) -> JointPlan:
    if not points:
        raise ValueError("no sprinkler points to route")
    if not risers:
        raise ValueError("joint mode needs at least one shaft start point")

    # ---- automatic grid angle (the user never types the tilt): rotate
    # the whole job into the corridor's grid frame; tilted ROOMS get their
    # own relative angle later, so mixed straight/tilted buildings work ----
    global_tilt = 0.0
    if auto_tilt:
        global_tilt = _global_grid_tilt(points, rooms, corridor)
        if abs(global_tilt) < 0.25:
            global_tilt = 0.0
        if global_tilt:
            points = rotate(points, -global_tilt)
            rooms = [rotate(ring, -global_tilt) for ring in rooms]
            corridor = rotate(corridor, -global_tilt)
            risers = rotate(risers, -global_tilt)

    corridor_poly = clean_polygon(corridor)
    corridor_cover = corridor_poly.buffer(_BOUNDARY_SLACK)

    # ---- rooms: clean polygons; a ring covering the corridor is the
    # building outline (very common on shared layers), not a room ----
    room_polys: dict[int, object] = {}
    statuses: list[RoomStatus] = []
    for i, ring in enumerate(rooms):
        status = RoomStatus(index=i, head_count=0, status="skipped", shaft=-1)
        statuses.append(status)
        try:
            poly = clean_polygon(ring)
        except ValueError:
            continue
        if poly.covers(corridor_poly):
            status.status = "outline"
            continue
        room_polys[i] = poly

    # ---- taps FIRST: a room that cannot reach the corridor never claims
    # heads, so they fall through to another room or the corridor ----
    taps: dict[int, object] = {}
    for i, poly in room_polys.items():
        tap = detect_tap(poly, corridor_poly)
        if tap is not None:
            taps[i] = tap

    # ---- assign heads: tappable rooms, then the corridor, else skipped ----
    geoms = shapely.points(np.asarray(points, dtype=float))
    owner = np.full(len(points), -1, dtype=int)  # room index; -2 = corridor
    for i in taps:
        mask = shapely.covers(room_polys[i].buffer(_BOUNDARY_SLACK), geoms) & (owner == -1)
        owner[mask] = i
    owner[shapely.covers(corridor_cover, geoms) & (owner == -1)] = -2
    skipped_heads = int(np.count_nonzero(owner == -1))

    room_heads = {i: [points[j] for j in np.flatnonzero(owner == i)] for i in taps}
    corridor_heads = [points[j] for j in np.flatnonzero(owner == -2)]

    if not any(room_heads.values()) and not corridor_heads:
        raise ValueError("no sprinkler points inside any room or the corridor")

    for i in list(taps):
        statuses[i].head_count = len(room_heads[i])
        if not room_heads[i]:
            statuses[i].status = "empty"
            del taps[i]          # nothing to pipe
            del room_heads[i]
        else:
            statuses[i].status = taps[i].status

    # ---- corridor graph + per-shaft Dijkstra ----
    # The graph knows the sprinklers (edges over a head cost ~8x and grid
    # lines are seeded beside every head) and the shafts enter as real
    # nodes, so Dijkstra plans the connector and the trunk together: at
    # most ONE 90-degree turn from the shaft, and the header threads
    # BESIDE the head columns instead of running on top of them.
    seeds = ([list(r) for r in risers]          # grid lines for straight entries
             + [t.tap for t in taps.values()]
             + [list(h) for h in corridor_heads])
    graph = CorridorGraph(corridor_poly, seeds,
                          heads=corridor_heads, head_offset=header_offset)
    if not graph.nodes:
        raise ValueError("corridor polygon is too small to route a header inside")

    sources, costs, prevs = [], [], []
    for shaft in risers:
        src = graph.add_shaft(list(shaft))
        cost, prev = graph.shortest_paths(src)
        sources.append(src)
        costs.append(cost)
        prevs.append(prev)

    # ---- rooms -> shafts by cheapest header path ----
    tap_nodes: dict[int, int] = {}
    shaft_rooms: list[list[int]] = [[] for _ in risers]
    for i, tap in list(taps.items()):
        node = graph.node_at(tap.tap)
        if node is None:
            node = graph.nearest_node(tap.tap)
        best_cost, best_shaft = min(
            (graph.reach_cost(costs[k], node), k)
            for k in range(len(risers))
        )
        if best_cost == _INF:
            # tap unreachable in the graph: the room cannot be fed, but its
            # heads may still sit inside the corridor - reroute them there
            statuses[i].status = "skipped"
            del taps[i]
            for head in room_heads.pop(i):
                if corridor_cover.covers(Point(head)):
                    corridor_heads.append(head)
                else:
                    skipped_heads += 1
            continue
        tap_nodes[i] = node
        statuses[i].shaft = best_shaft
        shaft_rooms[best_shaft].append(i)

    # Corridor heads must stay segment endpoints even when they sit ON a
    # pipe run: their graph nodes break every span merge below (a no-op
    # for heads off the runs).
    corridor_head_nodes: set = set()
    for head in corridor_heads:
        node = graph.node_at(head)
        if node is None:
            node = graph.nearest_node(head)
        corridor_head_nodes.add(node)

    # ---- corridor sprinklers FIRST: their MEDIAN HEADER is the corridor
    # main (one line forward per limb), and the rooms then tee off it ----
    # The header runs along the band BETWEEN the sprinkler rows so the
    # heads split as evenly as possible (2 -> 1|1, 10 -> 5|5, 11 -> 5|6)
    # and every column tees into it.  Heads it cannot serve (single heads,
    # unreachable spots) fall back to per-head routing below.
    segments: list[Segment] = []
    in_trees: list[set | None] = [None] * len(risers)
    sealed = [False] * len(risers)
    shaft_headers: list[list[Segment]] = [[] for _ in risers]
    corridor_head_count = [0] * len(risers)
    leftovers: list[Pt] = []
    shared_done = False
    if corridor_heads and len(risers) >= 2:
        # "divide the corridor equally in a line": ONE median main split
        # into a contiguous run per shaft (no parallel second main).  Only
        # the shafts that actually reach the field take part.
        reachable = []
        for k in range(len(risers)):
            node = graph.nearest_node(corridor_heads[0])
            if any(graph.reach_cost(costs[k], graph.node_at(h) or graph.nearest_node(h)) < _INF
                   for h in corridor_heads):
                reachable.append(k)
        if len(reachable) >= 2:
            shared = _shared_corridor_band([list(h) for h in corridor_heads],
                                           [list(risers[k]) for k in reachable],
                                           corridor_cover, header_offset, hazard,
                                           corridor_heads)
            if shared is not None:
                for local_k, segs, head_count in shared:
                    k = reachable[local_k]
                    for seg in segs:
                        seg.shaft = k
                    segments += segs
                    shaft_headers[k] += [s for s in segs if s.kind == "header"]
                    corridor_head_count[k] += head_count
                shared_done = True

    if corridor_heads and not shared_done:
        # per-head shaft preference by graph distance
        head_reach: list[list[float]] = []
        for head in corridor_heads:
            node = graph.node_at(head)
            if node is None:
                node = graph.nearest_node(head)
            head_reach.append([graph.reach_cost(costs[k2], node)
                               for k2 in range(len(risers))])

        raw_groups: list[list[int]] = [[] for _ in risers]
        for i, reaches in enumerate(head_reach):
            if min(reaches) == _INF:
                leftovers.append(corridor_heads[i])  # per-head loop reports it
            else:
                raw_groups[reaches.index(min(reaches))].append(i)

        # With several shafts, prefer the BALANCED division (whole columns,
        # one contiguous cut) over the ragged per-head split; fall back to
        # the raw groups when the balanced field is not divisible into bands.
        candidates = [(raw_groups, False)]
        balanced = _balanced_groups(raw_groups, head_reach, corridor_heads,
                                    hazard, len(risers))
        if balanced is not None:
            candidates.insert(0, (balanced, True))

        for groups, strict in candidates:
            emitted: list[tuple[int, list[Segment], int]] = []
            spare: list[Pt] = []
            failed = False
            for k, idxs in enumerate(groups):
                group = [corridor_heads[i] for i in idxs]
                if len(group) < 2:
                    spare += group
                    continue

                def graph_feed(tap_point, k=k):
                    """Feed a band THROUGH the graph: enter the building
                    promptly, travel INSIDE to the band's tap - never a
                    long run along the outside."""
                    node = graph.node_at(tap_point)
                    if node is None:
                        node = graph.nearest_node(tap_point)
                    if graph.reach_cost(costs[k], node) == _INF:
                        return None
                    if in_trees[k] is None:
                        in_trees[k] = {sources[k]}
                    edges, _ = tree_paths(graph, costs[k], prevs[k], sources[k],
                                          [node], in_trees[k])
                    segs = _emit_spans(graph, edges, corridor_head_nodes, "header",
                                       sources[k], list(risers[k]), corridor_cover)
                    segs += ortho_segments(graph.nodes[node], tap_point,
                                           corridor_cover, "header")
                    if not sealed[k]:
                        sealed[k] = True
                        graph.seal(sources[k])
                        costs[k], prevs[k] = graph.shortest_paths(in_trees[k])
                    return segs

                # graph-feeding mutates shared state, so it runs only on the
                # final (raw) attempt and rolls back when the group fails
                src = sources[k]
                snapshot = (None if in_trees[k] is None else set(in_trees[k]),
                            sealed[k], costs[k], prevs[k], list(graph.adj[src]))
                tree = _band_trees(group, [], list(risers[k]), corridor_cover,
                                   header_offset, hazard, corridor_heads,
                                   feeder=None if strict else graph_feed)
                if tree is None:
                    in_trees[k], sealed[k] = snapshot[0], snapshot[1]
                    costs[k], prevs[k] = snapshot[2], snapshot[3]
                    graph.adj[src] = snapshot[4]
                    if strict:
                        failed = True  # not divisible into bands: retry raw
                        break
                    spare += group     # raw groups: per-head fallback
                else:
                    emitted.append((k, tree, len(group)))
            if failed:
                continue
            for k, tree, count in emitted:
                for seg in tree:
                    seg.shaft = k
                segments += tree
                shaft_headers[k] += [s for s in tree if s.kind == "header"]
                corridor_head_count[k] += count
            leftovers += spare
            break

    # ---- rooms: tee off the corridor main where one exists, otherwise
    # route a trunk through the corridor graph ----
    for k in range(len(risers)):
        if not shaft_rooms[k]:
            continue
        room_feed: dict[int, list[Segment]] = {}
        graph_fed: list[int] = []
        for i in shaft_rooms[k]:
            if shaft_headers[k]:
                foot = _nearest_on(shaft_headers[k], taps[i].tap)
                feed = _feed_segments(foot, taps[i].tap, corridor_cover, corridor_heads)
                if feed is not None and _outside_len(feed, corridor_cover) <= EPS:
                    for seg in feed:
                        seg.kind = "header"
                    room_feed[i] = feed
                    continue
            graph_fed.append(i)

        if graph_fed:
            # The closest tap defines the trunk; the shaft is then SEALED
            # so every other tap splices onto the tree instead of pulling
            # its own connector from the shaft.  A trunk built earlier for
            # the corridor heads is reused (spliced onto), never rebuilt.
            order = sorted(graph_fed,
                           key=lambda i: graph.reach_cost(costs[k], tap_nodes[i]))
            edges: list = []
            if in_trees[k] is None:
                edges, in_tree = tree_paths(graph, costs[k], prevs[k], sources[k],
                                            [tap_nodes[order[0]]])
                in_trees[k] = in_tree
                sealed[k] = True
                graph.seal(sources[k])
                costs[k], prevs[k] = graph.shortest_paths(in_tree)
                rest = order[1:]
            else:
                rest = order
            for i in rest:
                if graph.reach_cost(costs[k], tap_nodes[i]) == _INF:
                    # only the shaft's own (now sealed) entry reached this
                    # tap: the room cannot be fed from the shared trunk
                    statuses[i].status = "skipped"
                    statuses[i].shaft = -1
                    shaft_rooms[k].remove(i)
                    graph_fed.remove(i)
                    del taps[i]
                    for head in room_heads.pop(i):
                        if corridor_cover.covers(Point(head)):
                            corridor_heads.append(head)
                            leftovers.append(head)
                        else:
                            skipped_heads += 1
            remaining = [i for i in rest if i in taps]
            if remaining:
                more, _ = tree_paths(graph, costs[k], prevs[k], sources[k],
                                     [tap_nodes[i] for i in remaining],
                                     in_trees[k])
                edges += more
            headers = _emit_spans(graph, edges, corridor_head_nodes, "header",
                                  sources[k], list(risers[k]), corridor_cover)
            for seg in headers:
                seg.shaft = k
            shaft_headers[k] += [s for s in headers if s.kind == "header"]
            segments += headers
            for i in graph_fed:
                # graph node -> exact tap point (when the tap missed the grid)
                room_feed[i] = ortho_segments(graph.nodes[tap_nodes[i]],
                                              taps[i].tap, corridor_cover, "header")

        for i in shaft_rooms[k]:
            if i not in room_feed:
                continue  # removed as unreachable above
            tap = taps[i]
            room_segs = list(room_feed[i])
            # wall crossing: corridor tap -> room entry
            room_segs += ortho_segments(tap.tap, tap.entry, None, "riser")
            room_segs += _route_room_auto(
                room_heads[i], hazard, tap.entry,
                room_polys[i].buffer(_BOUNDARY_SLACK), header_offset, auto_tilt,
            )
            for seg in room_segs:
                seg.shaft = k
            segments += room_segs

    # ---- leftover corridor heads: stub onto the nearest run, or route
    # through the corridor graph ----
    extra_edges: list[list[tuple[int, int]]] = [[] for _ in risers]   # no-room shafts: header
    branch_edges: list[list[tuple[int, int]]] = [[] for _ in risers]  # splices onto room trees
    for head in leftovers:
        stubbed = False
        if any(shaft_headers):
            best = None  # (distance, shaft, line)
            for k, headers in enumerate(shaft_headers):
                for seg in headers:
                    line = LineString([seg.start, seg.end])
                    # rounded like every other joint tie-break, so fp noise
                    # from the tilt round-trip cannot flip an equidistant pick
                    d = round(line.distance(Point(head)), 3)
                    if best is None or d < best[0]:
                        best = (d, k, line)
            _, k, line = best
            foot = line.interpolate(line.project(Point(head)))
            stubs = ortho_segments([foot.x, foot.y], list(head), corridor_cover, "branch")
            if not stubs:                       # head sits on the header
                corridor_head_count[k] += 1
                stubbed = True
            elif (_outside_len(stubs, corridor_cover) <= EPS
                    and _clear_of_heads(stubs, corridor_heads, head)):
                for seg in stubs:
                    seg.shaft = k
                segments += stubs
                corridor_head_count[k] += 1
                stubbed = True
        if stubbed:
            continue

        node = graph.node_at(head)
        if node is None:
            node = graph.nearest_node(head)
        best_cost, k = min(
            (graph.reach_cost(costs[k2], node), k2)
            for k2 in range(len(risers))
        )
        if best_cost == _INF:
            skipped_heads += 1
            continue
        node_pt = graph.nodes[node]
        final = (ortho_segments(node_pt, list(head), corridor_cover, "branch")
                 if dist(node_pt, head) > EPS else [])
        if final and _outside_len(final, corridor_cover) > EPS:
            skipped_heads += 1  # cannot reach the head without leaving the corridor
            continue
        if in_trees[k] is None:
            in_trees[k] = {sources[k]}
        edges, _ = tree_paths(graph, costs[k], prevs[k], sources[k], [node], in_trees[k])
        bucket = branch_edges[k] if shaft_rooms[k] else extra_edges[k]
        bucket += edges
        for seg in final:
            seg.shaft = k
        segments += final
        corridor_head_count[k] += 1
        if not sealed[k]:
            # the first head built this shaft's trunk: seal it and route
            # every later head from the tree, not from the shaft
            sealed[k] = True
            graph.seal(sources[k])
            costs[k], prevs[k] = graph.shortest_paths(in_trees[k])

    for k in range(len(risers)):
        for kind, edge_list in (("header", extra_edges[k]), ("branch", branch_edges[k])):
            if not edge_list:
                continue
            runs = _emit_spans(graph, edge_list, corridor_head_nodes, kind,
                               sources[k], list(risers[k]), corridor_cover)
            for seg in runs:
                seg.shaft = k
            segments += runs

    # ---- groups + plan ----
    groups, roots = [], []
    for k, shaft in enumerate(risers):
        roots.append(list(shaft))
        head_count = corridor_head_count[k] + sum(
            st.head_count for st in statuses if st.shaft == k)
        length = sum(s.length for s in segments if s.shaft == k)
        groups.append(RouteGroup(riser=list(shaft), head_count=head_count, length=length))

    if global_tilt:  # back to the world frame
        for seg in segments:
            seg.start, seg.end = rotate([seg.start, seg.end], global_tilt)
        roots = rotate(roots, global_tilt)
        for group in groups:
            group.riser = rotate([group.riser], global_tilt)[0]

    return JointPlan(
        segments=segments,
        risers=roots,
        groups=groups,
        rooms=statuses,
        total_length=sum(s.length for s in segments),
        skipped_heads=skipped_heads,
        skipped_rooms=sum(1 for st in statuses if st.status == "skipped"),
    )


def _shared_corridor_band(heads: list[Pt], shafts: list[Pt], poly,
                          offset: float, hazard, all_heads: list[Pt]):
    """ONE median main line for the whole corridor field, split among the
    shafts so each feeds a CONTIGUOUS run of it - "divide the corridor
    equally in a line".

    The single main runs along the axis the shafts are spread on (so each
    shaft owns one stretch of the one line, never a parallel second main);
    every cross-row tees off; each shaft risers to its own end.  Returns a
    list of (shaft_index, segments) or None when the shafts are not spread
    along an axis (co-located) or the line cannot stay inside - the caller
    then falls back to per-shaft bands.
    """
    xs = [h[0] for h in heads]
    ys = [h[1] for h in heads]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    # the main runs along the axis the shafts are SPREAD on, so dividing
    # the line between them is a clean contiguous cut
    sx = max(s[0] for s in shafts) - min(s[0] for s in shafts)
    sy = max(s[1] for s in shafts) - min(s[1] for s in shafts)
    axis = 0 if round(sx, 3) >= round(sy, 3) else 1   # 0 = main along X
    if max(sx, sy) < max(span_x, span_y) * 0.15:
        return None  # shafts co-located: no axis separates them
    flip = (axis == 0)

    def local(p):
        return [p[1], p[0]] if flip else [p[0], p[1]]

    pts = [local(h) for h in heads]
    s = spacing_for(hazard) if hazard else _infer_spacing(pts)

    # median line level (local x), between the lateral rows
    rows: list[list[Pt]] = []
    for p in sorted(pts, key=lambda q: q[0]):
        if rows and p[0] - rows[-1][0][0] <= s / 2:
            rows[-1].append(p)
        else:
            rows.append([p])
    if len(rows) == 1:
        level = statistics.median(p[0] for p in rows[0])
        main_l = level + offset
    else:
        levels = [statistics.median(p[0] for p in row) for row in rows]
        best = None
        for i in range(len(rows) - 1):
            one = sum(len(r) for r in rows[: i + 1])
            mid = (levels[i] + levels[i + 1]) / 2.0
            key = (abs(one - (len(pts) - one)), round(mid, 3))
            if best is None or key < best[0]:
                best = (key, mid)
        main_l = best[1]

    # one branch per cross-row, at its entry head's long-axis position
    branch_rows = _cluster_rows(pts, tol=s / 2)
    tees = []
    for row in branch_rows:
        entry_head = min(row, key=lambda p: abs(p[0] - main_l))
        tees.append((entry_head[1], row))      # tee_y = position ALONG the main
    tees.sort(key=lambda t: t[0])

    # each shaft's position along the main; assign every branch to the
    # nearest shaft -> contiguous spans (1-D nearest), then split the main
    shaft_ly = sorted(range(len(shafts)),
                      key=lambda k: round(local(shafts[k])[1], 3))
    order = [(round(local(shafts[k])[1], 3), k) for k in shaft_ly]

    def owner(tee_y):
        return min(order, key=lambda o: (abs(o[0] - tee_y), o[0]))[1]

    by_shaft: dict[int, list] = {}
    for tee_y, row in tees:
        by_shaft.setdefault(owner(tee_y), []).append((tee_y, row))
    if len(by_shaft) < 2:
        return None  # one shaft would own everything: not a real division

    out: list[tuple[int, list[Segment]]] = []
    for k, group in by_shaft.items():
        group.sort(key=lambda t: t[0])
        gtees = [tee_y for tee_y, _ in group]
        f = local(shafts[k])
        tap_y = min(max(f[1], gtees[0]), gtees[-1])
        local_segs: list[Segment] = []
        header = _main_segments(main_l, tap_y, gtees)
        for seg in header:
            seg.kind = "header"
        local_segs += header
        for tee_y, row in group:
            local_segs += _branch_segments_ortho(main_l, tee_y, row)
        segs = [Segment(start=local(sg.start), end=local(sg.end),
                        kind=sg.kind, length=sg.length) for sg in local_segs]
        if _outside_len(segs, poly) > EPS:
            return None  # wraps a corner: fall back to bands
        feed = _feed_segments(list(shafts[k]), local([main_l, tap_y]), poly, all_heads)
        if feed is None:
            return None
        head_count = sum(len(row) for _, row in group)
        out.append((k, feed + segs, head_count))
    return out


def _snap_header_to_shaft(main_l: float, feed_l: float,
                          row_levels: list[float], offset: float) -> float:
    """Put the corridor median header on the shaft's column so the feed
    runs straight up, with no 2x90 jog beside the band.

    The header normally sits `offset` to one side of the sprinklers; when
    the shaft is within `offset` of its nearest head row it is aligned with
    the shaft instead (the feed then simply continues as the header, the
    short branch drops absorbing the offset).  The `offset` window keeps
    every branch <= `offset` and stops the header drifting outward past the
    corridor wall - a shaft farther out keeps the default offset header and
    its short connector.

    The distance is rounded (like every joint tie-break) so fp noise from
    the auto-tilt round-trip cannot flip the snap decision.
    """
    nearest = min(abs(level - feed_l) for level in row_levels)
    return feed_l if round(nearest, 3) <= offset else main_l


def _corridor_tree(heads: list[Pt], feed: Pt, poly, offset: float,
                   hazard, all_heads: list[Pt], feeder=None):
    """Median header for a straight band of corridor heads.

    The header runs ALONG the band, placed BETWEEN the sprinkler rows so
    the heads split as evenly as possible (2 -> 1|1, 10 -> 5|5,
    11 -> 5|6); every column of heads tees into it with a branch run -
    full-mode geometry, turned to follow the corridor.  A single row gets
    the header `offset` mm beside it on the feed side.  Returns None when
    the field is not a straight band (some pipe would leave the corridor)
    so the caller can fall back to per-head routing.
    """
    span_x = max(h[0] for h in heads) - min(h[0] for h in heads)
    span_y = max(h[1] for h in heads) - min(h[1] for h in heads)
    flip = round(span_x, 3) >= round(span_y, 3)  # band along X: work transposed

    def local(p: Pt) -> Pt:
        return [p[1], p[0]] if flip else [p[0], p[1]]

    pts = [local(h) for h in heads]
    f = local(feed)
    s = spacing_for(hazard) if hazard else _infer_spacing(pts)

    # Lateral row structure (local x): the header goes between the rows,
    # splitting the heads as evenly as possible; ties resolve toward the
    # feed (keys rounded so tilt fp noise cannot flip them).
    rows: list[list[Pt]] = []
    for p in sorted(pts, key=lambda q: q[0]):
        if rows and p[0] - rows[-1][0][0] <= s / 2:
            rows[-1].append(p)
        else:
            rows.append([p])
    row_levels = [statistics.median(p[0] for p in row) for row in rows]
    if len(rows) == 1:
        level = row_levels[0]
        main_l = level + (offset if f[0] >= level else -offset)
    else:
        best = None
        for i in range(len(rows) - 1):
            one_side = sum(len(row) for row in rows[: i + 1])
            other = len(pts) - one_side
            mid = (row_levels[i] + row_levels[i + 1]) / 2.0
            key = (abs(one_side - other), round(abs(mid - f[0]), 3), round(mid, 3))
            if best is None or key < best[0]:
                best = (key, mid)
        main_l = best[1]
    # Run the header straight up FROM the shaft (no 2x90 jog beside the
    # band) when the shaft sits within `offset` of the heads: the feed
    # simply continues as the header instead of stepping across the offset.
    main_l = _snap_header_to_shaft(main_l, f[0], row_levels, offset)

    # Branch runs: one per column of heads, teeing into the header at the
    # entry head (full-mode pattern in the local frame).
    branch_rows = _cluster_rows(pts, tol=s / 2)
    tees = []
    for row in branch_rows:
        entry_head = min(row, key=lambda p: abs(p[0] - main_l))
        tees.append((entry_head[1], row))
    tee_ys = [tee for tee, _ in tees]
    tap_y = min(max(f[1], min(tee_ys)), max(tee_ys))

    local_segs: list[Segment] = []
    header = _main_segments(main_l, tap_y, tee_ys)
    for seg in header:
        seg.kind = "header"
    local_segs += header
    for tee_y, row in tees:
        local_segs += _branch_segments_ortho(main_l, tee_y, row)

    segs = [Segment(start=local(seg.start), end=local(seg.end),
                    kind=seg.kind, length=seg.length) for seg in local_segs]
    if _outside_len(segs, poly) > EPS:
        return None  # not a straight band (wraps a corner): graph-route instead
    # the feed connector may legitimately run outside near the shaft, but
    # never cut across the outside and never cross a sprinkler; when the
    # direct feed is unclean, the graph feeder routes it INSIDE instead
    tap_point = local([main_l, tap_y])
    feed_segs = _feed_segments(feed, tap_point, poly, all_heads)
    if feed_segs is None and feeder is not None:
        feed_segs = feeder(tap_point)
    if feed_segs is None:
        return None  # unreachable cleanly: split the band / graph-route
    return feed_segs + segs


def _global_grid_tilt(points: list[Pt], rooms: list[list[Pt]],
                      corridor: list[Pt]) -> float:
    """The building's main grid angle: measured from the CORRIDOR heads
    (the corridor graph and median headers live in that frame), falling
    back to all heads, then to 0."""
    try:
        corridor_cover = clean_polygon(corridor).buffer(_BOUNDARY_SLACK)
    except ValueError:
        return grid_angle(points) or 0.0
    geoms = shapely.points(np.asarray(points, dtype=float))
    in_corridor = shapely.covers(corridor_cover, geoms)
    for ring in rooms:
        try:
            room_cover = clean_polygon(ring).buffer(_BOUNDARY_SLACK)
        except ValueError:
            continue
        in_corridor &= ~shapely.covers(room_cover, geoms)
    corridor_heads = [p for p, ok in zip(points, in_corridor) if ok]
    angle = grid_angle(corridor_heads)
    if angle is None:
        angle = grid_angle(points)
    return angle or 0.0


def _route_room_auto(heads: list[Pt], hazard, entry: Pt, room_cover,
                     header_offset: float, auto_tilt: bool) -> list[Segment]:
    """Room tree in the room's OWN grid frame.

    With auto_tilt each room's grid angle is measured separately, so a
    building where some rooms are straight and some are tilted routes
    every room like a straight one (rotate in, route, rotate back)."""
    relative = grid_angle(heads) if auto_tilt else None
    if relative is None or abs(relative) < 0.25:
        return route_room_tree(heads, hazard, entry, room_cover, header_offset)
    local_poly = affinity.rotate(room_cover, -relative, origin=(0, 0))
    segments = route_room_tree(
        rotate(heads, -relative), hazard,
        rotate([entry], -relative)[0], local_poly, header_offset,
    )
    for seg in segments:
        seg.start, seg.end = rotate([seg.start, seg.end], relative)
    return segments


def _balanced_groups(groups: list[list[int]], reach: list[list[float]],
                     heads: list[Pt], hazard, shaft_count: int):
    """Re-divide corridor heads cleanly between the shafts.

    Per-head nearest-shaft assignment cuts a dense field raggedly: the
    networks interleave (mixed columns, branches crossing each other).
    Instead the head COLUMNS are divided: every column goes wholly to one
    shaft, and with two shafts the division is the contiguous cut of the
    column sequence with the lowest total pipe reach - left block to one
    shaft, right block to the other, meeting at ONE clean boundary.
    Returns None when there is nothing to balance.
    """
    indices = [i for group in groups for i in group]
    if shaft_count < 2 or len(indices) < 4:
        return None
    pts = [heads[i] for i in indices]
    span_x = max(p[0] for p in pts) - min(p[0] for p in pts)
    span_y = max(p[1] for p in pts) - min(p[1] for p in pts)
    along = 0 if round(span_x, 3) >= round(span_y, 3) else 1
    s = spacing_for(hazard) if hazard else _infer_spacing(pts)

    columns: list[list[int]] = []
    anchor = None
    for i in sorted(indices, key=lambda j: heads[j][along]):
        if anchor is not None and heads[i][along] - anchor <= s / 2:
            columns[-1].append(i)
        else:
            columns.append([i])
            anchor = heads[i][along]
    if len(columns) < 2:
        return None

    cost = [[sum(reach[i][k] for i in column) for k in range(shaft_count)]
            for column in columns]
    out: list[list[int]] = [[] for _ in range(shaft_count)]
    if shaft_count == 2:
        best = None
        for split in range(len(columns) + 1):
            for first, second in ((0, 1), (1, 0)):
                total = (sum(cost[c][first] for c in range(split))
                         + sum(cost[c][second] for c in range(split, len(columns))))
                key = (round(total, 3), split, first)
                if best is None or key < best[0]:
                    best = (key, split, first, second)
        _, split, first, second = best
        for c, column in enumerate(columns):
            out[first if c < split else second] += column
    else:
        # 3+ shafts: whole columns to their cheapest shaft (no mixed columns)
        for c, column in enumerate(columns):
            k = min(range(shaft_count), key=lambda k2: (round(cost[c][k2], 3), k2))
            out[k] += column
    return out


def _band_trees(heads: list[Pt], built: list[Segment], shaft: Pt, poly,
                offset: float, hazard, all_heads: list[Pt], depth: int = 0,
                feeder=None):
    """Median trees for a corridor head field, split into straight bands.

    A field wrapping a corridor corner (L/U legs) cannot be served by one
    straight median header: the field is split at the balanced boundary
    of its longer axis and each part is tried again (depth-capped).  The
    first band feeds from the shaft; every further band tees off the
    mains already built - ONE line runs forward per corridor limb.
    """
    feed = _feed_point(built, heads, shaft)
    tree = _corridor_tree(heads, feed, poly, offset, hazard, all_heads, feeder)
    if tree is not None:
        return tree
    if depth >= 3 or len(heads) < 4:
        return None

    span_x = max(h[0] for h in heads) - min(h[0] for h in heads)
    span_y = max(h[1] for h in heads) - min(h[1] for h in heads)
    axis = 0 if round(span_x, 3) >= round(span_y, 3) else 1
    ordered = sorted(heads, key=lambda h: (round(h[axis], 3),
                                           round(h[1 - axis], 3)))
    half = len(ordered) // 2
    best = None  # split between distinct coordinates, closest to half
    for cut in range(1, len(ordered)):
        if abs(ordered[cut][axis] - ordered[cut - 1][axis]) <= EPS:
            continue  # never cut through a column
        key = (abs(cut - half), round(ordered[cut][axis], 3))
        if best is None or key < best[0]:
            best = (key, cut)
    if best is None:
        return None
    parts = [ordered[: best[1]], ordered[best[1]:]]
    # the part nearer the feed goes first, so the second tees off its main
    parts.sort(key=lambda part: round(dist(feed, [
        sum(h[0] for h in part) / len(part),
        sum(h[1] for h in part) / len(part)]), 3))

    segs: list[Segment] = []
    for part in parts:
        grown = built + [s for s in segs if s.kind == "header"]
        sub = _band_trees(part, grown, shaft, poly, offset, hazard,
                          all_heads, depth + 1, feeder)
        if sub is None:
            return None
        segs += sub
    return segs


def _nearest_on(headers: list[Segment], p: Pt) -> Pt:
    """Nearest point on any of the runs (rounded tie-break)."""
    target = Point(p)
    best = None
    for seg in headers:
        line = LineString([seg.start, seg.end])
        d = round(line.distance(target), 3)
        if best is None or d < best[0]:
            best = (d, line)
    foot = best[1].interpolate(best[1].project(target))
    return [foot.x, foot.y]


def _feed_point(headers: list[Segment], group: list[Pt], shaft: Pt) -> Pt:
    """Where a corridor-head header tees off: the nearest point of the
    shaft's existing trunk, or the shaft itself when there is no trunk."""
    if not headers:
        return list(shaft)
    return _nearest_on(headers, [sum(h[0] for h in group) / len(group),
                                 sum(h[1] for h in group) / len(group)])


def _feed_segments(feed: Pt, tap: Pt, poly, heads: list[Pt]):
    """Feed -> header tap, axis-aligned, never crossing a sprinkler.

    Straight when aligned; otherwise the L corner is chosen first by how
    many heads the legs would cross (never over a sprinkler), then by
    pipe outside the corridor (keys rounded for tilt equivariance).

    A feed may run outside ONLY as far as physically unavoidable (the
    feed point's own distance to the polygon, plus slack) - a connector
    that would CUT ACROSS the outside returns None, and the caller falls
    back to the inside graph (connect to the nearest run instead).
    """
    if dist(feed, tap) <= EPS:
        return []
    # cap the unavoidable approach: a far-away feed must not buy itself a
    # long run ALONG the outside - it gets graph-routed inside instead
    allowed_outside = min(Point(feed).distance(poly), 3000.0) + 500.0
    if abs(feed[0] - tap[0]) <= EPS or abs(feed[1] - tap[1]) <= EPS:
        legs = [_seg(feed, tap, "riser")]
        return legs if _outside_len(legs, poly) <= allowed_outside else None

    # Containment is a HARD filter: among corners that stay inside (modulo
    # the unavoidable approach), prefer the one crossing the fewest heads.
    # Ranking head-avoidance ABOVE containment let a feed run far along the
    # outside to dodge a head, which then split the whole band.
    best = None
    for corner in ([tap[0], feed[1]], [feed[0], tap[1]]):
        legs = [_seg(feed, corner, "riser"), _seg(corner, tap, "riser")]
        outside = _outside_len(legs, poly)
        if outside > allowed_outside:
            continue
        crossed = 0
        for head in heads:
            for leg in legs:
                if LineString([leg.start, leg.end]).distance(Point(head)) <= _HEAD_CLEARANCE:
                    crossed += 1
                    break
        key = (crossed, round(outside, 3), round(corner[0], 3))
        if best is None or key < best[0]:
            best = (key, legs)
    if best is None:
        return None  # neither corner stays inside: graph-route instead
    return best[1]


def _outside_len(segs: list[Segment], poly) -> float:
    """Total pipe length falling outside the polygon."""
    return sum(LineString([s.start, s.end]).difference(poly).length for s in segs)


def _clear_of_heads(segs: list[Segment], heads: list[Pt], target: Pt) -> bool:
    """True when no OTHER sprinkler head sits on/next to the stub run."""
    for seg in segs:
        line = LineString([seg.start, seg.end])
        for other in heads:
            if other is target:
                continue
            if line.distance(Point(other)) <= _HEAD_CLEARANCE:
                return False
    return True


def _emit_spans(graph, edges, breaks, kind: str, source: int,
                shaft_pt: Pt, corridor_cover) -> list[Segment]:
    """Merged spans as Segments.

    A span starting at the shaft node becomes the riser connector: a
    straight run when aligned, otherwise ONE 90-degree L whose last leg
    lines up with the trunk's continuation - so the entry never zigzags.
    """
    segs: list[Segment] = []
    spans = merge_collinear(graph, edges, breaks)
    outgoing = {a: graph.nodes[b] for a, b in spans}
    for a, b in spans:
        if a != source:
            segs.append(_seg(graph.nodes[a], graph.nodes[b], kind))
            continue
        entry = graph.nodes[b]
        if dist(shaft_pt, entry) <= EPS:
            continue
        if abs(shaft_pt[0] - entry[0]) <= EPS or abs(shaft_pt[1] - entry[1]) <= EPS:
            segs.append(_seg(shaft_pt, entry, "riser"))
            continue
        follow = outgoing.get(b)
        if follow is None:
            segs += ortho_segments(shaft_pt, entry, corridor_cover, "riser")
            continue
        if abs(follow[0] - entry[0]) <= EPS:        # trunk continues vertically
            corner = [entry[0], shaft_pt[1]]        # ...so the L ends vertically
        else:                                       # trunk continues horizontally
            corner = [shaft_pt[0], entry[1]]
        segs.append(_seg(shaft_pt, corner, "riser"))
        segs.append(_seg(corner, entry, "riser"))
    return segs
