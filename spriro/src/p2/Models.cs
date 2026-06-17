using System.Collections.Generic;

namespace Spriro.P2
{
    // DTOs that serialize directly to/from the FastAPI backend's JSON.
    // Property names are lowercase / snake_case on purpose — JavaScriptSerializer
    // is name-sensitive and the backend's JSON keys are exactly these.
    //
    // Source of truth: backend/API_CONTRACT.md
    //
    // /api/zwcad/scenarios returns plain-text LISP, not JSON; see LispParser.cs.

    public class HealthResponse
    {
        public string status { get; set; }
        public string version { get; set; }
        public string ezdxf { get; set; }
        public List<ScenarioInfo> scenarios { get; set; }
    }

    public class ScenarioInfo
    {
        public int id { get; set; }
        public string name { get; set; }
    }

    public class ZwcadScenarioRequest
    {
        // Each polyline is a list of [x, y] points in millimetres.
        // Outer list = multiple rooms; inner list = one closed polyline.
        public List<List<double[]>> room_polys { get; set; }
        // Obstacle polylines (columns, equipment) where no sprinkler is placed.
        // Same shape as room_polys; may be empty.
        public List<List<double[]>> obs_polys { get; set; }
        public List<int> scenario_ids { get; set; }
        public double obs_min_offset { get; set; }
        public bool enable_gap_fill { get; set; }
        // If true, backend detects each room's longest-edge angle and
        // rotates the placement grid (and inserted blocks) to match.
        // If false (default), placement is axis-aligned and blocks are
        // inserted upright — matches the original pre-rotation behaviour.
        public bool tilted { get; set; }
    }

    // Body for the v2 universal endpoint POST /api/zwcad/auto.
    // Source of truth: routes/auto.py AutoRequest. JavaScriptSerializer is
    // name-sensitive — keep these snake_case to match the backend.
    public class AutoRequest
    {
        // Closed room polylines, each [[x,y], ...].
        public List<List<double[]>> room_polys { get; set; }
        // Closed obstacle polylines (columns, equipment), same shape.
        public List<List<double[]>> obs_polys { get; set; }
        // Room labels for hazard classification: each [x, y, "TEXT"].
        public List<object[]> labels { get; set; }
        public double obs_min_offset { get; set; }
        // Hazard used when a room has no recognisable label.
        public string default_hazard { get; set; }
        // Force the tightest hazard for the whole job if any room is unlabeled.
        public bool conservative { get; set; }
        // Auto-detect each room's longest-edge angle and rotate the grid.
        public bool tilted { get; set; }
        // Strip redundant heads down to the minimum (the "best" pass).
        public bool minimise { get; set; }
        // Allow the GA fallback on rooms self-fix can't solve.
        public bool enable_ga { get; set; }
    }
}
