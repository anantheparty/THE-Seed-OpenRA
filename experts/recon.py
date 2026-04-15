"""ReconExpert and ReconJob — random-ray exploration with IsExplored grid data.

Exploration algorithm ported from openra_api/jobs/explore.py (ExploreJob):
  1. Read IsExplored grid from WorldModel query("map_raw")["is_explored"]
  2. For each scout actor: cast random rays from current position, score by
     unexplored-cell ratio along path using Bresenham sampling
  3. Expand radius and lower threshold until a suitable target is found
  4. Per-actor _ScoutState tracks visited cells, stuck detection, divergence angle
  5. Repulsion keeps multiple scouts apart
  6. Falls back gracefully when IsExplored is unavailable (treats all cells as unexplored)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from benchmark import span as bm_span

from models import ConstraintEnforcement, JobStatus, ReconJobConfig, ResourceKind, ResourceNeed, SignalKind
from openra_api.models import Actor, Location

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .knowledge import awareness_recovery_package, has_awareness_gateway, radar_loss_impact


# ---------------------------------------------------------------------------
# Grid helpers (adapted from openra_api/jobs/explore.py)
# All positions are cell coordinates (int tuples). Layout describes the axis
# ordering of the IsExplored list-of-lists.
# ---------------------------------------------------------------------------

_AUTO_PARTIAL_GROUP_COUNT = 3

_GOLDEN_ANGLE = 2.399963229728653  # radians


def _is_explored_cell(
    exp: list, x: int, y: int, w: int, h: int, layout: str
) -> bool:
    """Return True if grid cell (x, y) is marked as explored."""
    if x < 0 or y < 0 or x >= w or y >= h:
        return False
    try:
        if layout == "col_major":
            col = exp[x] if x < len(exp) else []
            return bool(col[y]) if y < len(col) else False
        row = exp[y] if y < len(exp) else []
        return bool(row[x]) if x < len(row) else False
    except Exception:
        return False


def _bresenham_pts(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """4-connected Bresenham line cells from (x0,y0) to (x1,y1)."""
    pts: list[tuple[int, int]] = []
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        pts.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return pts


def _unexplored_ratio(
    exp: list,
    w: int,
    h: int,
    layout: str,
    origin: int,
    cur: tuple[int, int],
    tgt: tuple[int, int],
) -> float:
    """Fraction of cells along cur→tgt line (skipping start) that are unexplored."""
    x0 = max(0, min(w - 1, cur[0] - origin))
    y0 = max(0, min(h - 1, cur[1] - origin))
    x1 = max(0, min(w - 1, tgt[0] - origin))
    y1 = max(0, min(h - 1, tgt[1] - origin))
    pts = _bresenham_pts(x0, y0, x1, y1)
    if len(pts) <= 1:
        return 0.0
    total = unexp = 0
    for px, py in pts[1:]:
        total += 1
        if not _is_explored_cell(exp, px, py, w, h, layout):
            unexp += 1
    return unexp / total if total > 0 else 0.0


def _detect_grid_origin(positions: list[tuple[int, int]], w: int, h: int) -> int:
    """Infer whether grid uses 0-indexed or 1-indexed cell coordinates."""
    if not positions or not w or not h:
        return 1
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    if any(v == 0 for v in xs + ys):
        return 0
    if any(v == w or v == h for v in xs + ys):
        return 1
    if max(xs) <= w - 1 and max(ys) <= h - 1:
        return 0
    return 1


def _choose_grid_layout(
    exp: list,
    w: int,
    h: int,
    origin: int,
    positions: list[tuple[int, int]],
) -> str:
    """Determine row_major vs col_major by checking explored-ness at known positions.

    Uses multiple probe positions for accuracy. Ties default to col_major
    (OpenRA's native format).
    """
    def score(layout: str) -> int:
        s = 0
        for pos in positions:
            gx = max(0, min(w - 1, pos[0] - origin))
            gy = max(0, min(h - 1, pos[1] - origin))
            s += 1000 if _is_explored_cell(exp, gx, gy, w, h, layout) else -1000
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    x2, y2 = gx + dx, gy + dy
                    if 0 <= x2 < w and 0 <= y2 < h and _is_explored_cell(exp, x2, y2, w, h, layout):
                        s += 1
        return s
    # Ties default to col_major (OpenRA native format).
    return "row_major" if score("row_major") > score("col_major") else "col_major"


def _xorshift32(v: int) -> int:
    v &= 0xFFFFFFFF
    v ^= (v << 13) & 0xFFFFFFFF
    v ^= (v >> 17) & 0xFFFFFFFF
    v ^= (v << 5) & 0xFFFFFFFF
    return v & 0xFFFFFFFF


def _rand01(seed: int) -> float:
    return (_xorshift32(seed) & 0xFFFFFF) / float(1 << 24)


def _hash_seed(*xs: int) -> int:
    v = 2166136261
    for x in xs:
        v ^= x & 0xFFFFFFFF
        v = (v * 16777619) & 0xFFFFFFFF
    return v


# Directional bias for initial base_angle per search_region.
# Screen coordinates: y increases downward, so "northeast" = +x, −y → angle −π/4.
_REGION_BASE_ANGLES: dict[str, float] = {
    "northeast": -math.pi / 4,
    "northwest": -3 * math.pi / 4,
    "southwest":  3 * math.pi / 4,
    "southeast":  math.pi / 4,
    "full_map":    0.0,
    # "enemy_half" is resolved dynamically via _infer_enemy_half_angle()
}

# Angular half-width for directional search_region constraint.
# Rays are restricted to base_angle ± this value.
_REGION_HALF_WIDTH: dict[str, float] = {
    "northeast": math.pi / 2,
    "northwest": math.pi / 2,
    "southwest": math.pi / 2,
    "southeast": math.pi / 2,
    "enemy_half": math.pi / 2,
    "full_map": math.pi,  # no constraint
}


# ---------------------------------------------------------------------------
# Per-actor scout state
# ---------------------------------------------------------------------------

@dataclass
class _ScoutState:
    target: Optional[tuple[int, int]] = None
    visited: set = field(default_factory=set)   # "x,y" string keys of visited cells
    last_pos: Optional[tuple[int, int]] = None
    stuck_ticks: int = 0
    base_angle: float = 0.0


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

class GameAPILike(Protocol):
    def move_units_by_location(
        self,
        actors: list[Actor],
        location: Location,
        attack_move: bool = False,
    ) -> None: ...


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


# ---------------------------------------------------------------------------
# ReconJob
# ---------------------------------------------------------------------------

class ReconJob(BaseJob):
    """Autonomous scouting job — random-ray exploration using IsExplored grid."""

    tick_interval = 1.0
    _arrival_radius: int = 3            # Manhattan cells — match ExploreJob stick_distance
    _stuck_threshold_ticks: int = 10
    _progress_interval_s = 15.0  # Emit progress signal every N seconds
    _move_resend_interval_s: float = 2.0  # Re-issue move if target unchanged for this long

    # Random-ray algorithm tuning (cell-coordinate scale).
    # Aligned with ExploreJob parameters for reliable close-range exploration.
    _ray_base_radius: int = 18
    _ray_radius_step: int = 8
    _ray_max_radius: int = 60
    _ray_threshold_start: float = 0.70
    _ray_threshold_drop: float = 0.08
    _ray_threshold_min: float = 0.30
    _ray_tries_per_expand: int = 18
    _ray_repulsion_radius: int = 10   # min Manhattan dist between chosen targets

    # Cross-job coordination: all active ReconJob scout targets, keyed by actor_id.
    # Shared across all instances so multiple ReconJobs repel each other's scouts.
    _global_scout_targets: dict[int, tuple[int, int]] = {}

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: ReconJobConfig,
        signal_callback: SignalCallback,
        game_api: GameAPILike,
        world_model: WorldModelLike,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> None:
        super().__init__(
            job_id=job_id,
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )
        self.game_api = game_api
        self.world_model = world_model
        self.phase = "searching"
        self._scout_states: dict[int, _ScoutState] = {}
        self._last_destinations: dict[int, tuple[int, int]] = {}
        self._last_move_times: dict[int, float] = {}
        self._initial_explored_pct: Optional[float] = None
        self._best_explored_pct: Optional[float] = None
        self._visited_waypoints = 0
        self._tracking_target: Optional[tuple[int, int]] = None
        self._tracking_summary_sent = False
        self._awareness_reported = False
        self._last_progress_s: float = 0.0
        self._cached_grid: Optional[dict[str, Any]] = None
        self._grid_cache_time: float = 0.0
        self._grid_cache_ttl: float = 2.0

    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def abort(self) -> None:
        self._cleanup_global_targets()
        super().abort()

    def _cleanup_global_targets(self) -> None:
        for aid in list(self._scout_states):
            ReconJob._global_scout_targets.pop(aid, None)

    def _effective_search_region(self) -> str:
        """Widen enemy_half to full_map when explored_pct > 60%."""
        region = self.config.search_region
        if region == "enemy_half":
            map_info = self._cached_grid or self.world_model.query("map_raw")
            if map_info.get("explored_pct", 0) > 0.6:
                return "full_map"
        return region

    def get_resource_needs(self) -> list[ResourceNeed]:
        if getattr(self.config, "actor_ids", None):
            actor_ids = list(self.config.actor_ids or [])
            needs = [
                ResourceNeed(
                    job_id=self.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(aid), "owner": "self"},
                )
                for aid in actor_ids
            ]
            if not bool(getattr(self.config, "wait_for_full_group", True)):
                needs.insert(
                    0,
                    ResourceNeed(
                        job_id=self.job_id,
                        kind=ResourceKind.ACTOR,
                        count=self._normalized_min_ready_count(
                            actor_ids,
                            int(getattr(self.config, "min_ready_count", 0) or 0),
                        ),
                        predicates={
                            "owner": "self",
                            "actor_ids_any": ",".join(str(aid) for aid in actor_ids),
                        },
                    ),
                )
            return needs
        count = getattr(self.config, "scout_count", 1)
        return [
            ResourceNeed(
                job_id=self.job_id,
                kind=ResourceKind.ACTOR,
                count=count,
                predicates={"owner": "self"},
            )
        ]

    @staticmethod
    def _normalized_min_ready_count(actor_ids: list[int], configured: int) -> int:
        if not actor_ids:
            return 1
        requested = int(configured or 0)
        if requested <= 0:
            requested = min(len(actor_ids), _AUTO_PARTIAL_GROUP_COUNT)
        return max(1, min(len(actor_ids), requested))

    def tick(self) -> None:
        actors = self._all_actors()
        if not actors:
            return

        primary = actors[0]

        # Track exploration progress
        explored_pct = self._current_explored_pct()
        if self._initial_explored_pct is None:
            self._initial_explored_pct = explored_pct
            self._best_explored_pct = explored_pct
        else:
            self._best_explored_pct = max(self._best_explored_pct or explored_pct, explored_pct)

        self._maybe_emit_awareness_status(primary)

        # Global: target found → complete
        target = self._find_primary_target()
        if target is not None:
            self._complete_recon(target)
            return

        # Global: timeout
        if self._should_close_without_target():
            return

        # Per-actor: retreat / track / search
        for actor in actors:
            hp_ratio = self._hp_ratio(actor)
            if hp_ratio <= self.config.retreat_hp_pct:
                self._retreat(actor, hp_ratio)
                continue

            clue = self._find_tracking_clue()
            if clue is not None:
                self._track_clue(actor, clue)
                continue

            self._search(actor)

    # -----------------------------------------------------------------------
    # Actor access
    # -----------------------------------------------------------------------

    def _all_actors(self) -> list[dict[str, Any]]:
        result = []
        for resource_id in self.resources:
            if not resource_id.startswith("actor:"):
                continue
            actor_id = int(resource_id.split(":", 1)[1])
            payload = self.world_model.query("actor_by_id", {"actor_id": actor_id})
            actor = payload.get("actor") if isinstance(payload, dict) else None
            if actor:
                result.append(actor)
        return result

    # kept for single-actor callers (awareness, policy, etc.)
    def _current_actor(self) -> Optional[dict[str, Any]]:
        actors = self._all_actors()
        return actors[0] if actors else None

    # -----------------------------------------------------------------------
    # Search: random-ray algorithm
    # -----------------------------------------------------------------------

    def _search(self, actor: dict[str, Any]) -> None:
        self.phase = "searching"
        actor_id = int(actor["actor_id"])
        cur: tuple[int, int] = (int(actor["position"][0]), int(actor["position"][1]))

        st = self._get_or_init_scout_state(actor_id)

        # Stuck detection
        if st.last_pos is not None and self._manhattan(cur, st.last_pos) <= 2:
            st.stuck_ticks += 1
        else:
            st.stuck_ticks = 0
        st.last_pos = cur

        # Arrived → mark visited; stuck → force retarget
        need_new = st.target is None
        if st.target is not None:
            if self._arrived(cur, st.target):
                st.visited.add(f"{st.target[0]},{st.target[1]}")
                st.target = None
                need_new = True
                self._visited_waypoints += 1
            elif st.stuck_ticks >= self._stuck_threshold_ticks:
                st.target = None
                need_new = True

        if need_new:
            # Include targets from ALL ReconJobs (cross-job coordination)
            other_targets = [
                tgt for aid, tgt in ReconJob._global_scout_targets.items()
                if aid != actor_id
            ]
            with bm_span("expert_logic", name=f"recon:{self.job_id}:pick_target"):
                # Priority: frontier → random-ray �� unexplored centroid
                st.target = self._pick_target_frontier(cur, st, other_targets)
                if st.target is None:
                    st.target = self._pick_target_random_ray(actor_id, cur, st, other_targets)
                if st.target is None:
                    st.target = self._fallback_unexplored_centroid(cur, st)
            # Publish to global registry for cross-job repulsion
            if st.target is not None:
                ReconJob._global_scout_targets[actor_id] = st.target
            else:
                ReconJob._global_scout_targets.pop(actor_id, None)

        if st.target is None:
            return

        # Apply defend_base constraint (may filter out the target)
        constrained = self._apply_defend_base_constraint([st.target], actor)
        if not constrained:
            st.target = None
            ReconJob._global_scout_targets.pop(actor_id, None)
            return
        destination = constrained[0]

        self._move(actor, destination, attack_move=False)

    def _get_or_init_scout_state(self, actor_id: int) -> _ScoutState:
        if actor_id not in self._scout_states:
            st = _ScoutState()
            region = self.config.search_region
            if region == "enemy_half":
                region_angle = self._infer_enemy_half_angle()
            else:
                region_angle = _REGION_BASE_ANGLES.get(region, 0.0)
            # Golden-angle spread across multiple scouts with same region
            st.base_angle = (region_angle + actor_id * _GOLDEN_ANGLE) % math.tau
            self._scout_states[actor_id] = st
        return self._scout_states[actor_id]

    def _infer_enemy_half_angle(self) -> float:
        """Infer enemy direction as diagonal opposite of our base centroid."""
        base = self._base_centroid()
        map_info = self.world_model.query("map_raw")
        w = int(map_info.get("width") or 128)
        h = int(map_info.get("height") or 128)
        if base is None:
            return _REGION_BASE_ANGLES.get("northeast", -math.pi / 4)
        cx, cy = w / 2, h / 2
        dx, dy = cx - base[0], cy - base[1]  # vector from base toward center
        if abs(dx) < 1 and abs(dy) < 1:
            return 0.0
        # Point away from base, through center to opposite side
        return math.atan2(dy, dx)

    def _pick_target_frontier(
        self,
        cur: tuple[int, int],
        st: _ScoutState,
        other_targets: list[tuple[int, int]],
    ) -> Optional[tuple[int, int]]:
        """Frontier-based target selection — find explored/unexplored boundary cells.

        Scans the grid for frontier cells (unexplored cells adjacent to explored ones),
        groups them into 8x8 clusters, and picks the closest cluster center that is
        far from other scouts and within the search_region direction constraint.
        """
        now = self._now()
        if self._cached_grid is None or now - self._grid_cache_time >= self._grid_cache_ttl:
            self._cached_grid = self.world_model.query("map_raw")
            self._grid_cache_time = now
        map_info = self._cached_grid
        exp: list = list(map_info.get("is_explored") or [])
        w = int(map_info.get("width") or 0)
        h = int(map_info.get("height") or 0)
        if not w or not h:
            return None

        origin = _detect_grid_origin([cur], w, h)
        probe_positions = [cur]
        base = self._base_centroid()
        if base:
            probe_positions.append(base)
        layout = _choose_grid_layout(exp, w, h, origin, probe_positions)

        # Scan grid in 8x8 blocks, count frontier cells per block.
        # A frontier cell = unexplored cell with at least one explored neighbor.
        block = 8
        clusters: list[tuple[int, int, int]] = []  # (cx, cy, frontier_count)
        for by in range(0, h, block):
            for bx in range(0, w, block):
                frontier_count = 0
                for dy in range(min(block, h - by)):
                    for dx in range(min(block, w - bx)):
                        gx, gy = bx + dx, by + dy
                        if _is_explored_cell(exp, gx, gy, w, h, layout):
                            continue
                        # Check if any neighbor is explored
                        for nx, ny in ((gx-1, gy), (gx+1, gy), (gx, gy-1), (gx, gy+1)):
                            if 0 <= nx < w and 0 <= ny < h and _is_explored_cell(exp, nx, ny, w, h, layout):
                                frontier_count += 1
                                break
                if frontier_count == 0:
                    continue
                cx = bx + block // 2 + origin
                cy = by + block // 2 + origin
                if f"{cx},{cy}" in st.visited:
                    continue
                clusters.append((cx, cy, frontier_count))

        if not clusters:
            return None

        # Direction constraint: filter by search_region half-width
        half_w = _REGION_HALF_WIDTH.get(self._effective_search_region(), math.pi)
        if half_w < math.pi:
            filtered = []
            for cx, cy, fc in clusters:
                dx, dy = cx - cur[0], cy - cur[1]
                if abs(dx) < 1 and abs(dy) < 1:
                    continue
                angle = math.atan2(dy, dx)
                diff = abs((angle - st.base_angle + math.pi) % math.tau - math.pi)
                if diff <= half_w:
                    filtered.append((cx, cy, fc))
            if filtered:
                clusters = filtered

        # Score: prefer high frontier density, close to scout, far from other scouts
        def score(c: tuple[int, int, int]) -> float:
            cx, cy, fc = c
            dist_self = max(1, abs(cx - cur[0]) + abs(cy - cur[1]))
            min_other_dist = min(
                (abs(cx - o[0]) + abs(cy - o[1]) for o in other_targets),
                default=999,
            )
            repulsion = max(0, self._ray_repulsion_radius - min_other_dist)
            return fc / dist_self - repulsion * 0.5

        clusters.sort(key=score, reverse=True)
        best = clusters[0]
        return (min(best[0], w - 1 + origin), min(best[1], h - 1 + origin))

    def _pick_target_random_ray(
        self,
        actor_id: int,
        cur: tuple[int, int],
        st: _ScoutState,
        other_targets: list[tuple[int, int]],
    ) -> Optional[tuple[int, int]]:
        """Random-ray target selection using IsExplored grid.

        Casts rays from cur in directions biased by st.base_angle. Scores each
        candidate by unexplored-cell ratio along the Bresenham path. Expands
        radius and lowers threshold on each unsuccessful pass. Returns None only
        if all expansions fail (very unlikely on unexplored maps).
        """
        now = self._now()
        if self._cached_grid is None or now - self._grid_cache_time >= self._grid_cache_ttl:
            self._cached_grid = self.world_model.query("map_raw")
            self._grid_cache_time = now
        map_info = self._cached_grid
        exp: list = list(map_info.get("is_explored") or [])
        w = int(map_info.get("width") or 0)
        h = int(map_info.get("height") or 0)
        if not w or not h:
            return None

        origin = _detect_grid_origin([cur], w, h)
        # Use base position + scout position for layout detection — base is
        # far from diagonal and always explored, giving a reliable signal.
        probe_positions = [cur]
        base = self._base_centroid()
        if base:
            probe_positions.append(base)
        layout = _choose_grid_layout(exp, w, h, origin, probe_positions)

        # 1-second time bucket stabilises direction between ticks but allows drift
        t_bucket = int(self._now() // 1.0)

        expands = max(
            1,
            int((self._ray_max_radius - self._ray_base_radius) / max(1, self._ray_radius_step)) + 1,
        )

        for ei in range(expands):
            radius = self._ray_base_radius + ei * self._ray_radius_step
            if radius > self._ray_max_radius:
                break
            thr = max(
                self._ray_threshold_min,
                self._ray_threshold_start - ei * self._ray_threshold_drop,
            )

            half_w = _REGION_HALF_WIDTH.get(self._effective_search_region(), math.pi)
            for ti in range(self._ray_tries_per_expand):
                seed = _hash_seed(actor_id, t_bucket, ei, ti)
                jitter = (_rand01(seed) - 0.5) * 2 * half_w  # constrained to region
                angle = (st.base_angle + jitter) % math.tau

                r01 = _rand01(seed ^ 0x9E3779B9)
                dist = int(radius * (0.65 + 0.35 * r01))

                tx = max(0, min(w - 1, int(round(cur[0] + math.cos(angle) * dist))))
                ty = max(0, min(h - 1, int(round(cur[1] + math.sin(angle) * dist))))
                tgt = (tx, ty)

                if f"{tx},{ty}" in st.visited:
                    continue

                # Target must itself be unexplored
                gx = max(0, min(w - 1, tx - origin))
                gy = max(0, min(h - 1, ty - origin))
                if _is_explored_cell(exp, gx, gy, w, h, layout):
                    continue

                # Repulsion: keep scouts apart
                if any(
                    abs(tx - o[0]) + abs(ty - o[1]) < self._ray_repulsion_radius
                    for o in other_targets
                ):
                    continue

                # Path quality: require sufficient unexplored ratio
                ratio = _unexplored_ratio(exp, w, h, layout, origin, cur, tgt)
                if ratio >= thr:
                    return tgt

        return None

    def _fallback_unexplored_centroid(
        self,
        cur: tuple[int, int],
        st: _ScoutState,
    ) -> Optional[tuple[int, int]]:
        """When random-ray finds no target, scan the grid for the densest unexplored area."""
        now = self._now()
        if self._cached_grid is None or now - self._grid_cache_time >= self._grid_cache_ttl:
            self._cached_grid = self.world_model.query("map_raw")
            self._grid_cache_time = now
        map_info = self._cached_grid
        exp: list = list(map_info.get("is_explored") or [])
        w = int(map_info.get("width") or 0)
        h = int(map_info.get("height") or 0)
        if not w or not h:
            return None

        origin = _detect_grid_origin([cur], w, h)
        probe_positions = [cur]
        base = self._base_centroid()
        if base:
            probe_positions.append(base)
        layout = _choose_grid_layout(exp, w, h, origin, probe_positions)

        # Scan grid in 8x8 blocks, find the block with most unexplored cells
        block = 8
        best_count = 0
        best_cx, best_cy = w // 2, h // 2
        for by in range(0, h, block):
            for bx in range(0, w, block):
                count = 0
                for dy in range(min(block, h - by)):
                    for dx in range(min(block, w - bx)):
                        if not _is_explored_cell(exp, bx + dx, by + dy, w, h, layout):
                            count += 1
                if count > best_count:
                    key = f"{bx + block // 2},{by + block // 2}"
                    if key not in st.visited:
                        best_count = count
                        best_cx = bx + block // 2 + origin
                        best_cy = by + block // 2 + origin
        if best_count == 0:
            return None
        return (min(best_cx, w - 1 + origin), min(best_cy, h - 1 + origin))

    # -----------------------------------------------------------------------
    # Tracking / retreat / completion (unchanged from original)
    # -----------------------------------------------------------------------

    def _track_clue(self, actor: dict[str, Any], clue: dict[str, Any]) -> None:
        self.phase = "tracking"
        position = tuple(clue.get("position") or actor["position"])
        self._tracking_target = position
        if not self._tracking_summary_sent:
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary="发现敌方线索，调整侦察方向",
                expert_state={"phase": self.phase, "progress_pct": 0.4},
                data={"target_type": clue.get("category"), "position": list(position)},
            )
            self._tracking_summary_sent = True
        attack_move = not self.config.avoid_combat
        if self._distance(actor["position"], position) <= 160:
            attack_move = True
        self._move(actor, position, attack_move=attack_move)

    def _retreat(self, actor: dict[str, Any], hp_ratio: float) -> None:
        self.phase = "retreating"
        destination = self._safe_position(actor)
        self.emit_signal(
            kind=SignalKind.RISK_ALERT,
            summary="侦察单位血量过低，开始撤退",
            expert_state={"phase": self.phase, "progress_pct": 0.2},
            data={"hp_ratio": round(hp_ratio, 3), "retreat_to": list(destination)},
        )
        self._move(actor, destination, attack_move=False)

    def _complete_recon(self, target: dict[str, Any]) -> None:
        self.phase = "completed"
        position = tuple(target["position"])
        details = {
            "target_type": self.config.target_type,
            "position": list(position),
            "actor_id": target["actor_id"],
            "name": target.get("name"),
        }
        self.emit_signal(
            kind=SignalKind.TARGET_FOUND,
            summary=f"发现目标 {target.get('display_name') or target.get('name')} at {position}",
            expert_state={"phase": "tracking", "progress_pct": 0.9},
            data=details,
        )
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"侦察完成，发现目标 at {position}",
            world_delta={"target": details},
            expert_state={"phase": self.phase, "progress_pct": 1.0},
            result="succeeded",
            data=details,
        )
        self.status = JobStatus.SUCCEEDED
        self._cleanup_global_targets()

    def _maybe_emit_progress(self) -> None:
        """Emit periodic progress signal so the LLM can decide when to stop."""
        elapsed = self._elapsed_s()
        if elapsed - self._last_progress_s < self._progress_interval_s:
            return
        self._last_progress_s = elapsed
        explored_pct = self._best_explored_pct or self._current_explored_pct()
        explored_gain = max(0.0, explored_pct - (self._initial_explored_pct or explored_pct))
        self.emit_signal(
            kind=SignalKind.PROGRESS,
            summary=(
                f"侦察进行中 {elapsed:.0f}s：探索度 {explored_pct:.1%}"
                f"（+{explored_gain:.1%}）"
            ),
            expert_state={"phase": self.phase, "explored_pct": round(explored_pct, 4)},
            data={
                "explored_pct": round(explored_pct, 4),
                "explored_gain_pct": round(explored_gain, 4),
                "elapsed_s": round(elapsed, 1),
                "actor_count": len(self._scout_states),
            },
        )

    def _complete_timeout(self) -> None:
        self.phase = "completed"
        explored_pct = self._best_explored_pct or self._current_explored_pct()
        explored_gain = max(0.0, explored_pct - (self._initial_explored_pct or explored_pct))
        elapsed_s = round(max(0.0, self._elapsed_s()), 1)
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=(
                "侦察阶段结束，未发现目标；"
                f"已扩大探索度 {explored_gain:.1%}，当前探索度 {explored_pct:.1%}"
            ),
            expert_state={"phase": self.phase, "progress_pct": 1.0},
            result="partial",
            data={
                "target_type": self.config.target_type,
                "explored_pct": round(explored_pct, 4),
                "explored_gain_pct": round(explored_gain, 4),
                "elapsed_s": elapsed_s,
                "waypoints_visited": self._visited_waypoints,
                "awareness": self._awareness_status(),
                "scout_policy": self._scout_policy(actor=None),
            },
        )
        self.status = JobStatus.FAILED  # target not found — let LLM decide to retry
        self._cleanup_global_targets()

    # -----------------------------------------------------------------------
    # Target detection
    # -----------------------------------------------------------------------

    def _find_primary_target(self) -> Optional[dict[str, Any]]:
        enemy_payload = self.world_model.query("enemy_actors")
        actors = list(enemy_payload.get("actors", []))
        if self.config.target_type == "base":
            matches = [a for a in actors if a.get("category") == "building"]
        elif self.config.target_type == "army":
            matches = [a for a in actors if a.get("can_attack") and a.get("category") != "building"]
        else:
            matches = [a for a in actors if a.get("category") in {"building", "mcv"}]
        if not matches:
            return None
        matches.sort(key=lambda a: a["actor_id"])
        return matches[0]

    def _find_tracking_clue(self) -> Optional[dict[str, Any]]:
        if self.config.target_type != "base":
            return None
        enemy_payload = self.world_model.query("enemy_actors")
        actors = list(enemy_payload.get("actors", []))
        harvesters = [a for a in actors if a.get("category") == "harvester"]
        if not harvesters:
            return None
        harvesters.sort(key=lambda a: a["actor_id"])
        return harvesters[0]

    def _should_close_without_target(self) -> bool:
        # No auto-timeout — LLM decides when to stop via abort_job/complete_task.
        # Emit periodic progress so LLM can track exploration.
        self._maybe_emit_progress()
        return False

    # -----------------------------------------------------------------------
    # Constraint helpers
    # -----------------------------------------------------------------------

    def _base_centroid(self) -> Optional[tuple[int, int]]:
        result = self.world_model.query("my_actors", {"category": "building"})
        actors = result.get("actors", []) if isinstance(result, dict) else []
        positions = [a.get("position") for a in actors if a.get("position")]
        if not positions:
            return None
        return (sum(p[0] for p in positions) // len(positions),
                sum(p[1] for p in positions) // len(positions))

    def _apply_defend_base_constraint(
        self,
        candidates: list[tuple[int, int]],
        actor: dict[str, Any],
    ) -> list[tuple[int, int]]:
        for c in self._constraints_of_kind("defend_base"):
            max_dist = c.params.get("max_distance")
            if max_dist is None:
                continue
            base_pos = self._base_centroid()
            if base_pos is None:
                continue
            if c.enforcement == ConstraintEnforcement.CLAMP:
                filtered = [p for p in candidates if self._distance(p, base_pos) <= max_dist]
                if filtered:
                    candidates = filtered
            elif c.enforcement == ConstraintEnforcement.ESCALATE:
                too_far = [p for p in candidates if self._distance(p, base_pos) > max_dist]
                if too_far:
                    self.emit_constraint_violation(
                        "defend_base",
                        {
                            "max_distance": max_dist,
                            "base_position": list(base_pos),
                            "blocked_candidates": [list(p) for p in too_far],
                        },
                    )
        return candidates

    # -----------------------------------------------------------------------
    # Safe position / awareness
    # -----------------------------------------------------------------------

    def _safe_position(self, actor: dict[str, Any]) -> tuple[int, int]:
        buildings = self.world_model.query("my_actors", {"category": "building"}).get("actors", [])
        if buildings:
            return tuple(buildings[0]["position"])
        map_info = self.world_model.query("map_raw")
        width = int(map_info.get("width", 2000) or 2000)
        height = int(map_info.get("height", 2000) or 2000)
        current_x, current_y = actor["position"]
        return (max(int(width * 0.15), int(current_x * 0.25)), min(int(height * 0.85), current_y))

    def _awareness_status(self) -> dict[str, Any]:
        payload = self.world_model.query("my_actors", {"category": "building"})
        actors = payload.get("actors", []) if isinstance(payload, dict) else []
        if has_awareness_gateway(list(actors)):
            return {"status": "online", "impact": None, "recommendation": None}
        return {
            "status": "degraded",
            "impact": radar_loss_impact(),
            "recommendation": awareness_recovery_package(),
        }

    def _maybe_emit_awareness_status(self, actor: dict[str, Any]) -> None:
        if self._awareness_reported:
            return
        awareness = self._awareness_status()
        if awareness["status"] != "degraded":
            return
        self._awareness_reported = True
        self.emit_signal(
            kind=SignalKind.PROGRESS,
            summary="当前缺少雷达支撑，侦察仅依赖前线视野",
            expert_state={
                "phase": self.phase,
                "awareness_status": awareness["status"],
                "scout_policy": self._scout_policy(actor),
            },
            data={"awareness": awareness},
        )

    @staticmethod
    def _scout_policy(actor: Optional[dict[str, Any]]) -> dict[str, Any]:
        if actor is None:
            return {"stage": "report", "preferred_transition": "cheap_fast_vehicle"}
        category = actor.get("category")
        mobility = actor.get("mobility")
        if category == "vehicle" and mobility == "fast":
            return {"stage": "mobile_deep_recon", "preferred_transition": None}
        if category == "infantry":
            return {"stage": "initial_contact", "preferred_transition": "cheap_fast_vehicle"}
        return {"stage": "fallback_recon", "preferred_transition": "cheap_fast_vehicle"}

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def _current_explored_pct(self) -> float:
        map_info = self.world_model.query("map_raw")
        return float(map_info.get("explored_pct", 0.0) or 0.0)

    def _move(self, actor: dict[str, Any], destination: tuple[int, int], *, attack_move: bool) -> None:
        actor_id = int(actor["actor_id"])
        now = self._now()
        # Skip if same destination AND resend cooldown has not elapsed
        if self._last_destinations.get(actor_id) == destination and self.phase != "retreating":
            last_t = self._last_move_times.get(actor_id, 0.0)
            if now - last_t < self._move_resend_interval_s:
                return
        with bm_span("expert_logic", name=f"recon:{self.job_id}:move"):
            unit = Actor(
                actor_id=actor_id,
                type=actor.get("display_name") or actor.get("name"),
                position=Location(*actor["position"]),
                hppercent=int(actor.get("hp", 100)),
            )
            self.game_api.move_units_by_location(
                [unit],
                Location(*destination),
                attack_move=attack_move,
            )
        self._last_destinations[actor_id] = destination
        self._last_move_times[actor_id] = now

    @staticmethod
    def _hp_ratio(actor: dict[str, Any]) -> float:
        hp = float(actor.get("hp", 100) or 0)
        hp_max = float(actor.get("hp_max", 100) or 100)
        return hp / hp_max if hp_max else 0.0

    @staticmethod
    def _distance(a: tuple, b: tuple) -> float:
        ax, ay = a[0], a[1]
        bx, by = b[0], b[1]
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    @staticmethod
    def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _elapsed_s(self) -> float:
        return self._now() - self._created_at

    @staticmethod
    def _arrived(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) <= ReconJob._arrival_radius

    @staticmethod
    def _now() -> float:
        from time import time as _time
        return _time()


# ---------------------------------------------------------------------------
# ReconExpert factory
# ---------------------------------------------------------------------------

class ReconExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def create_job(
        self,
        task_id: str,
        config: ReconJobConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> ReconJob:
        return ReconJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            game_api=self.game_api,
            world_model=self.world_model,
            constraint_provider=constraint_provider,
        )
