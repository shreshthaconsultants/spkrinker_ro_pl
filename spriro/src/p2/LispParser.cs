using System;
using System.Collections.Generic;
using System.Globalization;

namespace Spriro.P2
{
    // Per-scenario payload returned from /api/zwcad/scenarios.
    //
    // Inside:  heads to actually place on the SPRINKLERS layer
    //          (PP-CEILING PENDANT block at each x,y rotated by rot).
    // Outside: bbox-grid intersections that fell outside the architecture
    //          polyline beyond the boundary-nudge margin — drawn as green
    //          debug markers on the SPRINKLER OUTSIDE layer so the user can
    //          see what was removed.
    public class ScenarioPoints
    {
        public List<double[]> Inside  = new List<double[]>();   // (x, y, rot)
        public List<double[]> Outside = new List<double[]>();   // (x, y)

        // How many row/column ends fired the alpha (stretch last 3 bays)
        // / gama (new head at 1000 + squeeze last 3) formulas on the
        // backend — printed in the ZWCAD terminal after placement.
        // 0 when an older backend omits the fourth list.
        public int AlphaCount = 0;
        public int GamaCount  = 0;
    }

    // Result of the v2 universal endpoint POST /api/zwcad/auto.
    //
    //   Heads        — every placed sprinkler (x, y, rotation_radians)
    //   Passed       — true when every room passed verification
    //   TotalHeads   — head count reported by the backend
    //   FlaggedRooms — rooms needing human review (guessed hazard / residual)
    //   ReportLines  — the VALIDATION report, one string per line, echoed
    //                  to the ZWCAD terminal.
    public class AutoResult
    {
        public List<double[]> Heads        = new List<double[]>();
        public bool           Passed       = false;
        public int            TotalHeads   = 0;
        public int            FlaggedRooms = 0;
        public double         Coverage     = 0.0;   // area-weighted % across rooms
        public List<string>   ReportLines  = new List<string>();
    }

    // Tiny S-expression parser for the response from POST /api/zwcad/scenarios.
    //
    // Backend returns text shaped like:
    //   ((1 ((1250.0 2000.0 0.0) (3750.0 2000.0 0.0)) ((4000.0 5000.0) ...))
    //    (4 ((1250.0 3000.0 0.523599) ...) ())
    //    (10 () ()))
    //
    // Top-level: list of (scenario_id heads-list outside-list).
    // heads-list:   list of (x y rotation_radians) triples — the real heads
    //               to place. Rotation is the room's principal angle so
    //               tilted-mode blocks line up with the wall direction.
    // outside-list: list of (x y) pairs — bbox-grid points outside the
    //               polyline that the plugin shows as green debug markers.
    //
    // The third element (outside-list) is optional — old backends that emit
    // (sid heads) without it still parse, with Outside left empty.
    // Two-element points (x y) are still accepted with rotation defaulted to
    // 0 in the heads list — preserves compatibility either way.
    //
    // Numbers and parens only — no symbols, strings, dotted pairs, or quoting.

    public static class LispParser
    {
        public static Dictionary<int, ScenarioPoints> ParseScenarios(string source)
        {
            if (string.IsNullOrWhiteSpace(source))
                throw new FormatException("Scenarios response was empty.");

            var root = Parse(source);
            if (!(root is List<object> top))
                throw new FormatException("Expected outer list of scenarios.");

            var result = new Dictionary<int, ScenarioPoints>();
            foreach (var entry in top)
            {
                if (!(entry is List<object> tuple) || tuple.Count < 2)
                    throw new FormatException("Each scenario entry must be (id heads [outside]).");

                if (!(tuple[0] is double idVal))
                    throw new FormatException("Scenario id must be a number.");
                int scenarioId = (int)idVal;

                if (!(tuple[1] is List<object> ptList))
                    throw new FormatException("Scenario heads must be a list.");

                var sp = new ScenarioPoints();
                foreach (var p in ptList)
                {
                    if (!(p is List<object> coords) || coords.Count < 2)
                        throw new FormatException("Each head must be (x y) or (x y rot).");
                    if (!(coords[0] is double x) || !(coords[1] is double y))
                        throw new FormatException("Head coordinates must be numbers.");
                    double rot = 0.0;
                    if (coords.Count >= 3 && coords[2] is double r)
                        rot = r;
                    sp.Inside.Add(new[] { x, y, rot });
                }

                // Optional outside-list (debug markers). Old backends omit it.
                if (tuple.Count >= 3 && tuple[2] is List<object> outsideList)
                {
                    foreach (var p in outsideList)
                    {
                        if (!(p is List<object> coords) || coords.Count < 2) continue;
                        if (!(coords[0] is double ox) || !(coords[1] is double oy)) continue;
                        sp.Outside.Add(new[] { ox, oy });
                    }
                }

                // Optional formula-usage stats: (alpha gama). Old backends omit it.
                if (tuple.Count >= 4 && tuple[3] is List<object> stats
                    && stats.Count >= 2
                    && stats[0] is double aCnt && stats[1] is double gCnt)
                {
                    sp.AlphaCount = (int)aCnt;
                    sp.GamaCount  = (int)gCnt;
                }

                result[scenarioId] = sp;
            }
            return result;
        }

        // Parse the v2 /api/zwcad/auto response:
        //   ((0 ((x y rot) ...) () (passed total flagged)) ("line" "line" ...))
        // Top-level list of two: [scenarioChunk, reportLines].
        //   scenarioChunk = (0 heads outside (passed total flagged))
        //   reportLines   = list of strings.
        public static AutoResult ParseAuto(string source)
        {
            if (string.IsNullOrWhiteSpace(source))
                throw new FormatException("Auto response was empty.");

            var root = Parse(source);
            if (!(root is List<object> top) || top.Count < 1)
                throw new FormatException("Expected (scenarioChunk reportLines).");

            var res = new AutoResult();

            if (!(top[0] is List<object> chunk) || chunk.Count < 2)
                throw new FormatException("Auto scenario chunk malformed.");

            if (chunk[1] is List<object> ptList)
            {
                foreach (var p in ptList)
                {
                    if (!(p is List<object> coords) || coords.Count < 2) continue;
                    if (!(coords[0] is double x) || !(coords[1] is double y)) continue;
                    double rot = (coords.Count >= 3 && coords[2] is double r) ? r : 0.0;
                    res.Heads.Add(new[] { x, y, rot });
                }
            }

            // stats list = (passed total flagged coverage_pct)
            if (chunk.Count >= 4 && chunk[3] is List<object> stats)
            {
                if (stats.Count >= 1 && stats[0] is double pv) res.Passed = pv != 0.0;
                if (stats.Count >= 2 && stats[1] is double tv) res.TotalHeads = (int)tv;
                if (stats.Count >= 3 && stats[2] is double fv) res.FlaggedRooms = (int)fv;
                if (stats.Count >= 4 && stats[3] is double cv) res.Coverage = cv;
            }

            // trailing report lines (optional)
            if (top.Count >= 2 && top[1] is List<object> lines)
            {
                foreach (var l in lines)
                    if (l is string str) res.ReportLines.Add(str);
            }
            return res;
        }

        // ---- Generic recursive parser ----

        private static object Parse(string source)
        {
            int pos = 0;
            SkipWs(source, ref pos);
            var node = ParseExpr(source, ref pos);
            SkipWs(source, ref pos);
            if (pos != source.Length)
                throw new FormatException($"Trailing characters at position {pos}.");
            return node;
        }

        private static object ParseExpr(string s, ref int pos)
        {
            SkipWs(s, ref pos);
            if (pos >= s.Length)
                throw new FormatException("Unexpected end of input.");

            if (s[pos] == '(')
            {
                pos++;
                var items = new List<object>();
                while (true)
                {
                    SkipWs(s, ref pos);
                    if (pos >= s.Length)
                        throw new FormatException("Unterminated list.");
                    if (s[pos] == ')')
                    {
                        pos++;
                        return items;
                    }
                    items.Add(ParseExpr(s, ref pos));
                }
            }
            if (s[pos] == '"')
                return ParseString(s, ref pos);
            return ParseNumber(s, ref pos);
        }

        // Double-quoted string token (used by the v2 /api/zwcad/auto report
        // lines). No escape sequences — the backend replaces inner quotes
        // with apostrophes before sending, so a string runs to the next ".
        private static string ParseString(string s, ref int pos)
        {
            pos++;                       // opening quote
            int start = pos;
            while (pos < s.Length && s[pos] != '"')
                pos++;
            if (pos >= s.Length)
                throw new FormatException("Unterminated string.");
            string val = s.Substring(start, pos - start);
            pos++;                       // closing quote
            return val;
        }

        private static double ParseNumber(string s, ref int pos)
        {
            int start = pos;
            if (pos < s.Length && (s[pos] == '+' || s[pos] == '-'))
                pos++;
            while (pos < s.Length)
            {
                char c = s[pos];
                if (char.IsDigit(c) || c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-')
                    pos++;
                else
                    break;
            }
            string token = s.Substring(start, pos - start);
            if (token.Length == 0)
                throw new FormatException($"Expected number at position {start}.");
            if (!double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out double v))
                throw new FormatException($"Invalid number '{token}' at position {start}.");
            return v;
        }

        private static void SkipWs(string s, ref int pos)
        {
            while (pos < s.Length && char.IsWhiteSpace(s[pos])) pos++;
        }
    }
}
