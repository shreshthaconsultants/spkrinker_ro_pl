"""
autoplace — Sprinkler Auto-Placement v2.0 "universal model" package.

DXF in → compliant sprinkler layout out → zero human touches. The 10-step
roadmap (see ../../UNIVERSAL_MODEL_ROADMAP.md), built on top of the v1
grid+alpha/gama/pull placement engine in ../placement.py:

  nfpa_rules   (Step 5)  hazard class → spacing / coverage numbers (single
                         source of truth for placement AND verification)
  classify     (Step 4)  read room labels → hazard class
  obstructions (Step 7)  three-times rule, heads under wide obstructions
  verifier     (Step 2)  coverage proof + rule violations (internal)
  autofix      (Step 6)  place → verify → fix → repeat until 0 violations
  ga_fallback  (Step 9)  min-heads GA for rooms the rules can't solve
  pipeline     (Step 10) orchestrates the whole flow + feedback logging
  cli          (Step 8)  headless DXF→DXF+report batch runner

Steps 2 (room auto-detection) and 3 wording in the roadmap are intentionally
NOT implemented in this version — rooms still come from the polyline layer.
"""

__version__ = "2.0.0"
