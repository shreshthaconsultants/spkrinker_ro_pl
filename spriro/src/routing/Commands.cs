using System;
using System.Collections.Generic;
using System.Linq;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;
using ZwSoft.ZwCAD.Runtime;


namespace Spriro.Routing
{
    /// <summary>Prints the available commands when the DLL is NETLOADed.</summary>
    public class PluginApp : IExtensionApplication
    {
        public void Initialize()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            doc?.Editor.WriteMessage(
                "\nSprinklerPlugin loaded." +
                "\n  SPKROUTE - route existing sprinklers (Rooms = rooms + corridor header, Open = one space)." +
                "\n  SPK      - repeat the routing with the saved answers, zero questions." +
                "\n  SPKERASE - erase what the plugin drew (pipes, or everything)." +
                "\n  SPKAUTO  - select a room boundary, place + validate + route sprinklers.\n");
        }

        public void Terminate() { }
    }

    public class Commands
    {
        private const double DefaultBranchWidth = 32.0;  // mm
        private const double DefaultMainWidth = 65.0;    // mm

        // ------------------------------------------------------------------
        // SPKAUTO: boundary -> /place -> /validate -> /route -> draw all
        // ------------------------------------------------------------------
        [CommandMethod("SPKAUTO")]
        public void SpkAuto()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null) return;
            var ed = doc.Editor;
            var db = doc.Database;

            try
            {
                // 1. Room boundary
                var peo = new PromptEntityOptions("\nSelect closed polyline room boundary: ");
                peo.SetRejectMessage("\nEntity must be a polyline.");
                peo.AddAllowedClass(typeof(Polyline), false);
                var per = ed.GetEntity(peo);
                if (per.Status != PromptStatus.OK) return;

                List<double[]> boundary;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var pl = (Polyline)tr.GetObject(per.ObjectId, OpenMode.ForRead);
                    if (!pl.Closed && pl.GetPoint3dAt(0).DistanceTo(pl.GetPoint3dAt(pl.NumberOfVertices - 1)) > Geo.Eps)
                    {
                        ed.WriteMessage("\nBoundary polyline must be closed.");
                        return;
                    }
                    boundary = GeometryReader.ReadBoundary(pl);
                    tr.Commit();
                }

                // 2. Hazard class
                var pko = new PromptKeywordOptions("\nHazard class") { AllowNone = true };
                pko.Keywords.Add("Light");
                pko.Keywords.Add("Ordinary");
                pko.Keywords.Add("Extra");
                pko.Keywords.Default = "Ordinary";
                var pkr = ed.GetKeywords(pko);
                if (pkr.Status != PromptStatus.OK && pkr.Status != PromptStatus.None) return;
                var hazard = string.IsNullOrEmpty(pkr.StringResult) ? "Ordinary" : pkr.StringResult;

                // 3. Pipe widths + building tilt
                if (!PromptWidths(ed, out var branchWidth, out var mainWidth)) return;
                var tilt = PromptTilt(ed, boundary);
                if (tilt == null) return;

                // 4. Backend computation (boundary keeps the pipes inside the
                //    room; tilt aligns grid and pipes with the building axes)
                ed.WriteMessage("\nCalling backend...");
                var place = BackendClient.Place(boundary, hazard, tilt.Value);
                var val = BackendClient.Validate(boundary, place.Points, hazard);
                var route = BackendClient.Route(place.Points, null, boundary, branchWidth, mainWidth, tilt.Value, hazard);

                // 5. Draw everything in one locked transaction
                using (doc.LockDocument())
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    DrawingService.EnsureLayers(tr, db);
                    var blockId = DrawingService.EnsureSprinklerBlock(tr, db);

                    foreach (var p in place.Points)
                    {
                        DrawingService.DrawHead(tr, db, p, blockId);
                        DrawingService.DrawCoverage(tr, db, p, place.CoverageRadius);
                    }
                    foreach (var seg in route.Segments)
                    {
                        DrawingService.DrawPipeLine(tr, db, seg);
                        DrawingService.DrawFlowArrow(tr, db, seg, branchWidth, mainWidth);
                    }
                    foreach (var p in val.FailingHeads)
                        DrawingService.MarkFailing(tr, db, p);

                    tr.Commit();
                }

                // 6. Report
                ed.WriteMessage("\nPlaced " + place.Count + " sprinkler(s), pipe network "
                    + (route.TotalLength / 1000.0).ToString("F1") + " m.");
                ed.WriteMessage(val.Passed
                    ? "\nNFPA-13 validation: PASSED."
                    : "\nNFPA-13 validation: FAILED (" + val.FailingHeads.Count + " head(s) marked on " + DrawingService.FailLayer + "):");
                foreach (var rule in val.Rules.Where(r => !r.Passed))
                    ed.WriteMessage("\n  [FAIL] " + rule.Rule + ": " + rule.Detail);
            }
            catch (BackendException bex)
            {
                ed.WriteMessage("\n" + bex.Message);
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage("\nSPKAUTO error: " + ex.Message);
            }
        }

        // ------------------------------------------------------------------
        // SPKROUTE: sprinkler layer + start point layer -> /route -> draw pipes
        // ------------------------------------------------------------------
        [CommandMethod("SPKROUTE")]
        public void SpkRoute()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null) return;
            var ed = doc.Editor;
            var db = doc.Database;

            try
            {
                var settings = RunSettings.Load(db);

                // 0. Layout: Rooms = rooms + corridor main header (joint),
                //    Open = one open space per shaft.  Default = last used.
                var layout = Prompts.Choice(ed, "Layout (Rooms = rooms + corridor, Open = one space)",
                    new[] { "Rooms", "Open" },
                    string.Equals(settings.Layout, "Open", StringComparison.OrdinalIgnoreCase) ? "Open" : "Rooms");
                if (layout == null) return;
                if (string.Equals(layout, "Rooms", StringComparison.OrdinalIgnoreCase))
                {
                    JointFlow.Run(doc);
                    return;
                }

                // ---- Open layout ----
                // 1. Layers (pick an object or type a name; Enter = last answer)
                List<double[]> heads;
                while (true)
                {
                    var layer = Prompts.LayerPick(ed, db, "Sprinklers", settings.HeadsLayer);
                    if (layer == null) return;
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        heads = GeometryReader.CollectPointsOnLayer(tr, db, layer);
                        tr.Commit();
                    }
                    ed.WriteMessage("\n  -> " + heads.Count + " sprinkler(s) on \"" + layer + "\"");
                    if (heads.Count > 0) { settings.HeadsLayer = layer; break; }
                    if (Prompts.Choice(ed, "No sprinklers there", new[] { "Retry", "Cancel" }, "Retry") != "Retry")
                        return;
                }

                var shaftLayer = Prompts.LayerPick(ed, db, "Shafts", settings.ShaftLayer);
                if (shaftLayer == null) return;
                settings.ShaftLayer = shaftLayer;
                List<double[]> shafts;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    shafts = GeometryReader.CollectPointsOnLayer(tr, db, shaftLayer);
                    tr.Commit();
                }
                ed.WriteMessage("\n  -> " + shafts.Count + " shaft(s) on \"" + shaftLayer + "\"");
                if (shafts.Count == 0 && !PromptShaftPoints(ed, shafts)) return;

                var boundaryLayer = Prompts.LayerPick(ed, db, "Room boundary", settings.BoundaryLayer);
                if (boundaryLayer == null) return;
                settings.BoundaryLayer = boundaryLayer;
                List<double[]> boundary;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    boundary = GeometryReader.LargestClosedBoundaryOnLayer(tr, db, boundaryLayer);
                    tr.Commit();
                }
                ed.WriteMessage(boundary == null
                    ? "\n  -> no closed polyline on \"" + boundaryLayer + "\" - routing without a boundary"
                    : "\n  -> boundary found on \"" + boundaryLayer + "\"");

                // 2. Widths + tilt + erase
                var b = Prompts.Distance(ed, "Branch pipe width (mm)", settings.BranchWidth);
                if (b == null) return;
                var m = Prompts.Distance(ed, "Main pipe width (mm)", settings.MainWidth);
                if (m == null) return;
                // tilt is AUTOMATIC: measured from the sprinkler grid itself
                var detected = Prompts.GridTilt(heads)
                    ?? DefaultTilt(boundary) * 180.0 / Math.PI;
                if (Math.Abs(detected) < 0.5) detected = 0.0;
                double? tilt = detected;
                ed.WriteMessage("\nBuilding tilt: " + detected.ToString("F1") + " deg (auto).");
                bool eraseOld = settings.EraseOld;
                bool hasOld;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    hasOld = DrawingService.HasPipeOutput(tr, db);
                    tr.Commit();
                }
                if (hasOld)
                {
                    var erase = Prompts.YesNo(ed,
                        "Erase previous pipe run on " + DrawingService.PipeLayer + "?", settings.EraseOld);
                    if (erase == null) return;
                    eraseOld = erase.Value;
                }

                // 3. Routing (hazard omitted: backend infers spacing from the
                //    heads; tilt aligns the pipes with the building axes)
                ed.WriteMessage("\nRouting " + heads.Count + " sprinkler(s) from "
                    + shafts.Count + " shaft(s)...");
                var watch = System.Diagnostics.Stopwatch.StartNew();
                var route = BackendClient.Route(heads, shafts, boundary, b.Value, m.Value, tilt.Value);
                watch.Stop();

                // 4. Draw single-line pipes (centrelines) with flow arrows
                //    + shaft markers, one colour per shaft
                using (doc.LockDocument())
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        if (eraseOld)
                            DrawingService.EraseOutput(tr, db, false);
                        DrawingService.EnsureLayer(tr, db, DrawingService.PipeLayer, 5);
                        foreach (var seg in route.Segments)
                        {
                            DrawingService.DrawPipeLine(tr, db, seg,
                                DrawingService.ShaftColor(seg.Shaft));
                            DrawingService.DrawFlowArrow(tr, db, seg, b.Value, m.Value,
                                DrawingService.ShaftColor(seg.Shaft));
                        }
                        for (int i = 0; i < route.Risers.Count; i++)
                            DrawingService.DrawShaftMarker(tr, db, route.Risers[i], m.Value,
                                DrawingService.ShaftColor(i));
                        tr.Commit();
                    }
                    settings.Layout = "Open";
                    settings.BranchWidth = b.Value;
                    settings.MainWidth = m.Value;
                    settings.EraseOld = eraseOld;
                    settings.Tilt = tilt.Value;
                    settings.Save(db);
                }

                // 5. Report
                ed.WriteMessage("\nDone in " + watch.Elapsed.TotalSeconds.ToString("F1") + " s.");
                ed.WriteMessage("\n----------------------------------------------");
                string[] colorNames = { "red", "yellow", "green", "cyan", "magenta", "blue", "orange", "violet" };
                for (int i = 0; i < route.Groups.Count; i++)
                {
                    var g = route.Groups[i];
                    ed.WriteMessage(string.Format("\n  Shaft {0} ({1,-7}) {2,5} head(s) {3,9:F1} m{4}",
                        i + 1, colorNames[i % colorNames.Length], g.HeadCount, g.Length / 1000.0,
                        g.HeadCount == 0 ? "  (nothing routed)" : ""));
                }
                ed.WriteMessage("\n----------------------------------------------");
                ed.WriteMessage("\n  Total: " + (heads.Count - route.SkippedHeads) + " head(s) piped, "
                    + (route.TotalLength / 1000.0).ToString("F1") + " m of pipe on "
                    + DrawingService.PipeLayer + ".  One-step UNDO removes it.");
                if (route.SkippedHeads > 0)
                    ed.WriteMessage("\n  ! " + route.SkippedHeads
                        + " sprinkler(s) outside the room boundary were ignored.");
            }
            catch (BackendException bex)
            {
                ed.WriteMessage("\n" + bex.Message);
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage("\nSPKROUTE error: " + ex.Message);
            }
        }

        // ------------------------------------------------------------------
        // SPK: repeat the last routing with the saved answers - no questions
        // ------------------------------------------------------------------
        [CommandMethod("SPK")]
        public void SpkQuick()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null) return;
            var settings = RunSettings.Load(doc.Database);
            if (string.Equals(settings.Layout, "Open", StringComparison.OrdinalIgnoreCase))
            {
                doc.Editor.WriteMessage("\nSPK quick-run works with the Rooms layout - run SPKROUTE for Open.");
                return;
            }
            JointFlow.Run(doc, quick: true);
        }

        // ------------------------------------------------------------------
        // SPKERASE: remove what the plugin drew
        // ------------------------------------------------------------------
        [CommandMethod("SPKERASE")]
        public void SpkErase()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null) return;
            var ed = doc.Editor;
            var db = doc.Database;
            try
            {
                var choice = Prompts.Choice(ed,
                    "Erase (Pipes = pipe network only, All = also heads/coverage/fail marks)",
                    new[] { "Pipes", "All" }, "Pipes");
                if (choice == null) return;
                int erased;
                using (doc.LockDocument())
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    erased = DrawingService.EraseOutput(tr, db,
                        string.Equals(choice, "All", StringComparison.OrdinalIgnoreCase));
                    tr.Commit();
                }
                ed.WriteMessage("\nErased " + erased + " entit" + (erased == 1 ? "y" : "ies") + ".");
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage("\nSPKERASE error: " + ex.Message);
            }
        }

        // ------------------------------------------------------------------

        /// <summary>Tilt of the building: pick two points along a wall, type
        /// degrees, or Enter for the default (longest boundary edge angle).
        /// Returns degrees, or null when the user cancels.</summary>
        internal static double? PromptTilt(Editor ed, List<double[]> boundary)
        {
            var pao = new PromptAngleOptions(
                "\nBuilding tilt angle (pick two points along a wall, or Enter for default)")
            {
                DefaultValue = DefaultTilt(boundary),  // radians
                UseDefaultValue = true,
                AllowNone = true,
            };
            var res = ed.GetAngle(pao);
            if (res.Status == PromptStatus.Cancel) return null;
            var radians = res.Status == PromptStatus.OK ? res.Value : pao.DefaultValue;
            return radians * 180.0 / Math.PI;
        }

        /// <summary>Angle (radians) of the longest edge of the boundary ring;
        /// 0 when there is no usable boundary.</summary>
        internal static double DefaultTilt(List<double[]> boundary)
        {
            if (boundary == null || boundary.Count < 2) return 0.0;
            double best = 0.0, bestLen = 0.0;
            for (int i = 0; i < boundary.Count; i++)
            {
                var a = boundary[i];
                var b = boundary[(i + 1) % boundary.Count];
                double dx = b[0] - a[0], dy = b[1] - a[1];
                double len = Math.Sqrt(dx * dx + dy * dy);
                if (len > bestLen)
                {
                    bestLen = len;
                    best = Math.Atan2(dy, dx);
                }
            }
            // normalize to (-90, 90]: a wall direction, not a heading
            while (best > Math.PI / 2) best -= Math.PI;
            while (best <= -Math.PI / 2) best += Math.PI;
            return best;
        }

        /// <summary>Click fallback for shafts: pick one or more points; Enter finishes.</summary>
        internal static bool PromptShaftPoints(Editor ed, List<double[]> shafts)
        {
            while (true)
            {
                var ppo = new PromptPointOptions(shafts.Count == 0
                    ? "\nPick the first shaft/riser point: "
                    : "\nPick the next shaft point or press Enter to finish: ")
                {
                    AllowNone = shafts.Count > 0,
                };
                var ppr = ed.GetPoint(ppo);
                if (ppr.Status == PromptStatus.OK)
                {
                    // GetPoint returns UCS coordinates; heads are WCS - transform.
                    var wcs = ppr.Value.TransformBy(ed.CurrentUserCoordinateSystem);
                    shafts.Add(new[] { wcs.X, wcs.Y });
                    continue;
                }
                if (shafts.Count == 0) return false;  // cancelled with nothing picked
                return true;                           // Enter/cancel after >= 1 shaft
            }
        }

        internal static string PromptLayerName(Editor ed, string message, string defaultName)
        {
            var pso = new PromptStringOptions("\n" + message)
            {
                DefaultValue = defaultName,
                UseDefaultValue = true,
                AllowSpaces = true,
            };
            var res = ed.GetString(pso);
            if (res.Status != PromptStatus.OK) return null;
            var name = string.IsNullOrWhiteSpace(res.StringResult) ? defaultName : res.StringResult.Trim();
            return name;
        }

        internal static bool PromptWidths(Editor ed, out double branchWidth, out double mainWidth)
        {
            branchWidth = DefaultBranchWidth;
            mainWidth = DefaultMainWidth;

            var pdo = new PromptDoubleOptions("\nBranch pipe width (mm)")
            {
                DefaultValue = DefaultBranchWidth,
                UseDefaultValue = true,
                AllowNegative = false,
                AllowZero = false,
            };
            var r = ed.GetDouble(pdo);
            if (r.Status == PromptStatus.Cancel) return false;
            if (r.Status == PromptStatus.OK) branchWidth = r.Value;

            pdo = new PromptDoubleOptions("\nMain pipe width (mm)")
            {
                DefaultValue = DefaultMainWidth,
                UseDefaultValue = true,
                AllowNegative = false,
                AllowZero = false,
            };
            r = ed.GetDouble(pdo);
            if (r.Status == PromptStatus.Cancel) return false;
            if (r.Status == PromptStatus.OK) mainWidth = r.Value;

            return true;
        }
    }
}
