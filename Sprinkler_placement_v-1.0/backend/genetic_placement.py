"""
genetic_placement.py — Genetic Algorithm for Sprinkler Placement Optimization v1.2

KEY CHANGES FROM v1.1:
  - Pre-computed sample grid eliminates per-evaluation point_in_poly overhead
  - SpatialHash coverage checks: O(1) per sample instead of O(n_sprinklers)
  - SpatialHash spacing penalty: O(n) instead of O(n²)
  - Gap scans once every 3 generations (shared across children), not per-child
  - All preset / result / operator APIs unchanged

Strategy:
  - Chromosome: list of (x, y) sprinkler positions
  - Fitness:    coverage% x weight - count_penalty - violation_penalty - spacing_penalty
  - Operators:  tournament selection, uniform crossover, Gaussian mutation,
                insert mutation (add head at gap), delete mutation (remove redundant head)
  - Elitism:    top N individuals survive each generation

Usage:
    from genetic_placement import GeneticOptimiser, GA_PRESETS
    opt = GeneticOptimiser(floor_poly, excl_polys, obs_polys, zone_wall_segs,
                            coverage_radius, wall_min, space_min,
                            preset="balanced")
    result = opt.run(initial_points)
"""

import math
import random
import copy
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Callable

from geometry import (
    point_in_poly, bbox, poly_to_segs,
    min_dist_to_segs, min_dist_to_polys,
    find_uncovered_gaps, coverage_fraction,
    precompute_sample_grid, coverage_from_samples,
    SpatialHash, TOLERANCE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

Point      = Tuple[float, float]
Chromosome = List[Point]


# ─────────────────────────────────────────────────────────────────────────────
# GA Presets
# ─────────────────────────────────────────────────────────────────────────────

GA_PRESETS = {
    "fast": {
        "description":     "Quick optimisation (2-5s). 30 generations x 20 pop.",
        "pop_size":        20,
        "generations":     30,
        "mutation_rate":   0.25,
        "mutation_sigma":  150.0,
        "crossover_rate":  0.7,
        "tournament_k":    3,
        "elitism_n":       2,
        "coverage_weight": 1.0,
        "count_penalty":   0.002,
        "overlap_penalty": 0.5,
        "sample_divisor":  8,
    },
    "balanced": {
        "description":     "Balanced quality/speed (10-20s). 60 gen x 40 pop.",
        "pop_size":        40,
        "generations":     60,
        "mutation_rate":   0.20,
        "mutation_sigma":  200.0,
        "crossover_rate":  0.75,
        "tournament_k":    4,
        "elitism_n":       4,
        "coverage_weight": 1.0,
        "count_penalty":   0.003,
        "overlap_penalty": 0.8,
        "sample_divisor":  15,
    },
    "thorough": {
        "description":     "High-quality (30-60s). 120 gen x 80 pop.",
        "pop_size":        80,
        "generations":     120,
        "mutation_rate":   0.18,
        "mutation_sigma":  250.0,
        "crossover_rate":  0.8,
        "tournament_k":    5,
        "elitism_n":       6,
        "coverage_weight": 1.0,
        "count_penalty":   0.005,
        "overlap_penalty": 1.0,
        "sample_divisor":  20,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GAResult:
    best_points:     List[Point]
    initial_points:  List[Point]
    fitness_log:     List[float]   # best fitness per generation
    coverage_log:    List[float]   # best coverage% per generation
    count_log:       List[int]     # sprinkler count per generation
    stats:           dict = field(default_factory=dict)
    generations_run: int  = 0
    converged:       bool = False

    @property
    def improvement_pct(self) -> float:
        if not self.fitness_log or len(self.fitness_log) < 2:
            return 0.0
        return round(
            (self.fitness_log[-1] - self.fitness_log[0])
            / max(abs(self.fitness_log[0]), 1e-9) * 100, 2
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fitness evaluator (pre-computed samples + SpatialHash)
# ─────────────────────────────────────────────────────────────────────────────

class FitnessEvaluator:
    """
    Fitness = coverage_pct x coverage_weight
             - count_penalty x n
             - overlap_penalty x invalid_count
             - spacing_penalty x overcrowded_pairs

    Pre-computes valid sample grid in __init__ (expensive point_in_poly
    calls happen once, not per evaluate).  Each evaluate() builds a
    SpatialHash of the chromosome for O(1) coverage lookups.
    """

    def __init__(
        self,
        floor_poly:      List[Point],
        excl_polys:      List[List[Point]],
        obs_polys:       List[List[Point]],
        zone_wall_segs:  list,
        coverage_radius: float,
        wall_min:        float,
        space_min:       float,
        obs_min_offset:  float = 150.0,
        sample_step:     Optional[float] = None,
        coverage_weight: float = 1.0,
        count_penalty:   float = 0.003,
        overlap_penalty: float = 0.8,
    ):
        self.floor_poly      = floor_poly
        self.excl_polys      = excl_polys
        self.obs_polys       = obs_polys
        self.zone_wall_segs  = zone_wall_segs
        self.coverage_radius = coverage_radius
        self.wall_min        = wall_min
        self.space_min       = space_min
        self.obs_min_offset  = obs_min_offset
        self.coverage_weight = coverage_weight
        self.count_penalty   = count_penalty
        self.overlap_penalty = overlap_penalty

        self.sample_step = sample_step if sample_step is not None \
                           else max(50.0, coverage_radius / 10.0)

        self._minx, self._maxx, self._miny, self._maxy = bbox(floor_poly)
    

    
        self._samples = precompute_sample_grid(
            floor_poly,
            excl_polys or [],
            obs_polys  or [],
            self.sample_step,
        )

    def evaluate(self, chromosome: Chromosome) -> float:
        """Score a chromosome.  Higher = better."""
        if not chromosome:
            return 0.0

        cov = coverage_from_samples(
            self._samples, chromosome, self.coverage_radius
        )

        n         = len(chromosome)
        count_pen = self.count_penalty * n

        invalid       = self._count_violations(chromosome)
        violation_pen = self.overlap_penalty * invalid

        spacing_pen = self._spacing_penalty(chromosome)

        return (
            cov * self.coverage_weight * 100.0
            - count_pen
            - violation_pen
            - spacing_pen
        )

    def _spacing_penalty(self, chromosome: Chromosome) -> float:
        """O(n * avg_neighbours) via incremental SpatialHash."""
        if len(chromosome) < 2:
            return 0.0

        sp_hash = SpatialHash(self.space_min)
        pen  = 0.0
        sm   = self.space_min
        tol  = sm - TOLERANCE
        tol_sq = tol * tol
        inv_sm = 0.1 / sm
        _floor = math.floor
        _sqrt  = math.sqrt

        for x, y in chromosome:
            inv  = sp_hash.inv
            cx   = int(_floor(x * inv))
            cy   = int(_floor(y * inv))
            grid = sp_hash.grid
            for ddx in range(-1, 2):
                kx = cx + ddx
                for ddy in range(-1, 2):
                    b = grid.get((kx, cy + ddy))
                    if b:
                        for px, py in b:
                            dx = x - px
                            dy = y - py
                            dsq = dx * dx + dy * dy
                            if dsq < tol_sq:
                                d = _sqrt(dsq)
                                if d < tol:
                                    pen += inv_sm * (sm - d)
            sp_hash.insert(x, y)

        return pen

    def _count_violations(self, chromosome: Chromosome) -> int:
        """Count sprinklers that violate hard placement constraints."""
        count = 0
        for px, py in chromosome:
            if any(point_in_poly(px, py, ex) for ex in self.excl_polys):
                count += 1
                continue
            if any(point_in_poly(px, py, ob) for ob in self.obs_polys):
                count += 1
                continue
            if self.zone_wall_segs:
                d = min_dist_to_segs(px, py, self.zone_wall_segs)
                if d < self.wall_min - TOLERANCE:
                    count += 1
                    continue
            if not point_in_poly(px, py, self.floor_poly):
                count += 1
        return count

    def coverage_pct(self, chromosome: Chromosome) -> float:
        """Return 0-100 coverage percentage for a chromosome."""
        return coverage_from_samples(
            self._samples, chromosome, self.coverage_radius
        ) * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Genetic operators
# ─────────────────────────────────────────────────────────────────────────────

def _tournament_select(
    population: List[Chromosome],
    fitnesses:  List[float],
    k:          int,
) -> Chromosome:
    indices  = random.sample(range(len(population)), min(k, len(population)))
    best_idx = max(indices, key=lambda i: fitnesses[i])
    return copy.deepcopy(population[best_idx])


def _uniform_crossover(
    parent_a: Chromosome,
    parent_b: Chromosome,
    rate:     float = 0.75,
) -> Tuple[Chromosome, Chromosome]:
    n = min(len(parent_a), len(parent_b))
    if n == 0:
        return copy.deepcopy(parent_a), copy.deepcopy(parent_b)

    child_a: Chromosome = []
    child_b: Chromosome = []

    for i in range(n):
        if random.random() < rate:
            child_a.append(parent_a[i])
            child_b.append(parent_b[i])
        else:
            child_a.append(parent_b[i])
            child_b.append(parent_a[i])

    if len(parent_a) > n:
        child_a.extend(parent_a[n:])
        child_b.extend(parent_a[n:])
    elif len(parent_b) > n:
        child_a.extend(parent_b[n:])
        child_b.extend(parent_b[n:])

    return child_a, child_b


def _gaussian_mutate(
    chromosome:    Chromosome,
    floor_poly:    List[Point],
    mutation_rate: float,
    sigma:         float,
    minx:          float,
    maxx:          float,
    miny:          float,
    maxy:          float,
) -> Chromosome:
    mutated = []
    for (x, y) in chromosome:
        if random.random() < mutation_rate:
            nx = max(minx, min(maxx, x + random.gauss(0, sigma)))
            ny = max(miny, min(maxy, y + random.gauss(0, sigma)))
            mutated.append((round(nx, 3), round(ny, 3)))
        else:
            mutated.append((x, y))
    return mutated


def _insert_mutation(
    chromosome: Chromosome,
    floor_poly: List[Point],
    gap_pts:    List[Point],
    rate:       float = 0.05,
) -> Chromosome:
    if gap_pts and random.random() < rate:
        new_pt = random.choice(gap_pts)
        idx    = random.randint(0, len(chromosome))
        chromosome = chromosome[:idx] + [new_pt] + chromosome[idx:]
    return chromosome


def _deletion_mutation(
    chromosome: Chromosome,
    rate:       float = 0.03,
) -> Chromosome:
    if len(chromosome) > 1 and random.random() < rate:
        idx        = random.randint(0, len(chromosome) - 1)
        chromosome = chromosome[:idx] + chromosome[idx + 1:]
    return chromosome


# ─────────────────────────────────────────────────────────────────────────────
# Main optimiser
# ─────────────────────────────────────────────────────────────────────────────

class GeneticOptimiser:
    """
    Genetic Algorithm sprinkler placement optimiser.

    Parameters
    ----------
    floor_poly      : closed polygon vertices [(x,y), ...]
    excl_polys      : list of exclusion zone polygons
    obs_polys       : list of obstacle polygons
    zone_wall_segs  : wall segments [(x1,y1,x2,y2), ...] for wall-band check
    coverage_radius : sprinkler coverage radius in mm
    wall_min        : minimum distance from sprinkler to wall in mm
    space_min       : minimum sprinkler-to-sprinkler distance in mm
    obs_min_offset  : minimum distance from sprinkler to obstacle edge in mm
    preset          : "fast" | "balanced" | "thorough"
    seed            : integer random seed for reproducibility (None = random)
    progress_cb     : optional callable(generation, best_fitness, coverage_pct)
    """

    def __init__(
        self,
        floor_poly:      List[Point],
        excl_polys:      List[List[Point]],
        obs_polys:       List[List[Point]],
        zone_wall_segs:  list,
        coverage_radius: float,
        wall_min:        float,
        space_min:       float,
        obs_min_offset:  float = 150.0,
        preset:          str   = "balanced",
        seed:            Optional[int] = None,
        progress_cb:     Optional[Callable] = None,
    ):
        self.floor_poly      = floor_poly
        self.excl_polys      = excl_polys or []
        self.obs_polys       = obs_polys  or []
        self.zone_wall_segs  = zone_wall_segs or []
        self.coverage_radius = coverage_radius
        self.wall_min        = wall_min
        self.space_min       = space_min
        self.obs_min_offset  = obs_min_offset
        self.preset_name     = preset
        self.cfg             = GA_PRESETS.get(preset, GA_PRESETS["balanced"]).copy()
        self.progress_cb     = progress_cb

        if seed is not None:
            random.seed(seed)

        self._minx, self._maxx, self._miny, self._maxy = bbox(floor_poly)

        divisor          = self.cfg.get("sample_divisor", 10)
        self.sample_step = max(50.0, coverage_radius / divisor)

        self.evaluator = FitnessEvaluator(
            floor_poly      = floor_poly,
            excl_polys      = self.excl_polys,
            obs_polys       = self.obs_polys,
            zone_wall_segs  = self.zone_wall_segs,
            coverage_radius = coverage_radius,
            wall_min        = wall_min,
            space_min       = space_min,
            obs_min_offset  = obs_min_offset,
            sample_step     = self.sample_step,
            coverage_weight = self.cfg["coverage_weight"],
            count_penalty   = self.cfg["count_penalty"],
            overlap_penalty = self.cfg["overlap_penalty"],
        )

    # ── Population initialisation ─────────────────────────────────

    def _init_population(self, seed_points: List[Point]) -> List[Chromosome]:
        pop        = [copy.deepcopy(seed_points)]
        sigma_init = self.cfg["mutation_sigma"] * 2.0

        for _ in range(self.cfg["pop_size"] - 1):
            individual = _gaussian_mutate(
                copy.deepcopy(seed_points),
                self.floor_poly,
                mutation_rate = 0.4,
                sigma         = sigma_init,
                minx=self._minx, maxx=self._maxx,
                miny=self._miny, maxy=self._maxy,
            )
            pop.append(individual)
        return pop

    # ── Main run ──────────────────────────────────────────────────

    def run(self, initial_points: List[Point]) -> GAResult:
        if not initial_points:
            return GAResult(
                best_points    = [],
                initial_points = [],
                fitness_log    = [],
                coverage_log   = [],
                count_log      = [],
                stats          = {"error": "No initial points provided"},
            )

        cfg            = self.cfg
        pop_size       = cfg["pop_size"]
        generations    = cfg["generations"]
        mutation_rate  = cfg["mutation_rate"]
        mutation_sigma = cfg["mutation_sigma"]
        crossover_rate = cfg["crossover_rate"]
        tournament_k   = cfg["tournament_k"]
        elitism_n      = cfg["elitism_n"]
        divisor        = cfg.get("sample_divisor", 10)

        gap_scan_step     = max(100.0, self.coverage_radius / max(1, divisor - 2))
        gap_scan_interval = 3

        # ── Initialise population ─────────────────────────────────
        population  = self._init_population(initial_points)
        fitnesses   = [self.evaluator.evaluate(ind) for ind in population]

        fitness_log:  List[float] = []
        coverage_log: List[float] = []
        count_log:    List[int]   = []

        best_idx     = max(range(len(fitnesses)), key=lambda i: fitnesses[i])
        best_chromo  = copy.deepcopy(population[best_idx])
        best_fitness = fitnesses[best_idx]
        stagnation   = 0
        cached_gap_pts: List[Point] = []

        # ── Generation loop ───────────────────────────────────────
        for gen in range(generations):

            do_gap_scan = (
                gen % gap_scan_interval == 0
                and (not coverage_log or coverage_log[-1] < 85.0)
            )
            if do_gap_scan:
                cached_gap_pts = find_uncovered_gaps(
                    self.floor_poly,
                    best_chromo,
                    self.coverage_radius,
                    self.excl_polys,
                    self.obs_polys,
                    sample_step=gap_scan_step,
                )

            sorted_idx = sorted(
                range(len(population)),
                key=lambda i: fitnesses[i],
                reverse=True,
            )
            new_pop = [copy.deepcopy(population[i]) for i in sorted_idx[:elitism_n]]

            while len(new_pop) < pop_size:
                parent_a = _tournament_select(population, fitnesses, tournament_k)
                parent_b = _tournament_select(population, fitnesses, tournament_k)

                if random.random() < crossover_rate:
                    child_a, child_b = _uniform_crossover(parent_a, parent_b)
                else:
                    child_a = copy.deepcopy(parent_a)
                    child_b = copy.deepcopy(parent_b)

                for child in (child_a, child_b):

                    child = _gaussian_mutate(
                        child,
                        self.floor_poly,
                        mutation_rate,
                        mutation_sigma,
                        self._minx, self._maxx,
                        self._miny, self._maxy,
                    )

                    if cached_gap_pts:
                        child = _insert_mutation(
                            child, self.floor_poly, cached_gap_pts, rate=0.08
                        )

                    if coverage_log and coverage_log[-1] >= 95.0:
                        child = _deletion_mutation(child, rate=0.05)

                    new_pop.append(child)
                    if len(new_pop) >= pop_size:
                        break

            # Evaluate new population
            population = new_pop[:pop_size]
            fitnesses  = [self.evaluator.evaluate(ind) for ind in population]

            gen_best_idx = max(range(len(fitnesses)), key=lambda i: fitnesses[i])
            gen_fitness  = fitnesses[gen_best_idx]
            gen_cov      = self.evaluator.coverage_pct(population[gen_best_idx])
            gen_count    = len(population[gen_best_idx])

            fitness_log.append(round(gen_fitness, 4))
            coverage_log.append(round(gen_cov, 2))
            count_log.append(gen_count)

            if gen_fitness > best_fitness:
                best_fitness = gen_fitness
                best_chromo  = copy.deepcopy(population[gen_best_idx])
                stagnation   = 0
            else:
                stagnation  += 1

            if self.progress_cb:
                self.progress_cb(gen + 1, best_fitness, gen_cov)

            if stagnation >= 15:
                break

        # ── Final evaluation of best chromosome ───────────────────
        final_cov        = self.evaluator.coverage_pct(best_chromo)
        final_violations = self.evaluator._count_violations(best_chromo)

        improvement = round(
            (fitness_log[-1] - fitness_log[0]) / max(abs(fitness_log[0]), 1e-9) * 100, 2
        ) if len(fitness_log) > 1 else 0.0

        return GAResult(
            best_points     = [(round(x, 3), round(y, 3)) for x, y in best_chromo],
            initial_points  = initial_points,
            fitness_log     = fitness_log,
            coverage_log    = coverage_log,
            count_log       = count_log,
            generations_run = len(fitness_log),
            converged       = stagnation >= 15,
            stats = {
                "preset":                self.preset_name,
                "sample_step_mm":        round(self.sample_step, 1),
                "sample_divisor":        divisor,
                "generations_run":       len(fitness_log),
                "converged":             stagnation >= 15,
                "initial_count":         len(initial_points),
                "optimised_count":       len(best_chromo),
                "count_delta":           len(best_chromo) - len(initial_points),
                "final_fitness":         round(best_fitness, 4),
                "final_coverage_pct":    round(final_cov, 2),
                "constraint_violations": final_violations,
                "improvement_pct":       improvement,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper — mirrors placement.py interface
# ─────────────────────────────────────────────────────────────────────────────

def optimise_placement(
    zone_result:     dict,
    floor_poly:      List[Point],
    excl_polys:      List[List[Point]],
    obs_polys:       List[List[Point]],
    zone_wall_segs:  list,
    coverage_radius: float,
    wall_min:        float,
    space_min:       float,
    obs_min_offset:  float = 150.0,
    preset:          str   = "balanced",
    seed:            Optional[int] = None,
    progress_cb:     Optional[Callable] = None,
) -> dict:
    """
    Convenience wrapper.

    Takes the dict returned by generate_zone_sprinklers() and optimises
    the sprinkler positions via GA.

    Returns the same dict extended with:
      points           : GA-optimised grid sprinkler positions (replaces original)
      extra_points     : gap-fill points (unchanged)
      ga_result        : full GAResult object
      ga_stats         : stats dict (preset, sample_step_mm, coverage, count, etc.)
      ga_fitness_log   : list of best fitness per generation
      ga_coverage_log  : list of best coverage% per generation
      ga_count_log     : list of sprinkler count per generation
    """
    initial_points = zone_result.get("points", [])

    opt = GeneticOptimiser(
        floor_poly      = floor_poly,
        excl_polys      = excl_polys,
        obs_polys       = obs_polys,
        zone_wall_segs  = zone_wall_segs,
        coverage_radius = coverage_radius,
        wall_min        = wall_min,
        space_min       = space_min,
        obs_min_offset  = obs_min_offset,
        preset          = preset,
        seed            = seed,
        progress_cb     = progress_cb,
    )

    ga_result = opt.run(initial_points)

    return {
        **zone_result,
        "points":          ga_result.best_points,
        "ga_result":       ga_result,
        "ga_stats":        ga_result.stats,
        "ga_fitness_log":  ga_result.fitness_log,
        "ga_coverage_log": ga_result.coverage_log,
        "ga_count_log":    ga_result.count_log,
    }
