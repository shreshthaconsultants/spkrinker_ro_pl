using System;
using System.Collections.Generic;
using System.Diagnostics;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;

namespace SprinklerPlugin
{
    /// <summary>
    /// Rooms layout for SPKROUTE: every room is a closed polyline on the
    /// rooms layer, the corridor a closed polyline on the corridor layer.
    /// One main header runs from each shaft through the corridor (at the
    /// median of the corridor sprinklers); rooms and corridor sprinklers
    /// tee off it.  All answers are remembered in the drawing; SPK re-runs
    /// everything with zero questions.
    /// </summary>
    public static class JointFlow
    {
        public static void Run(Document doc, bool quick = false)
        {
            var ed = doc.Editor;
            var db = doc.Database;
            try
            {
                var settings = RunSettings.Load(db);
                if (quick && !settings.HasRun)
                {
                    ed.WriteMessage("\nNo saved setup in this drawing yet - run SPKROUTE once first.");
                    return;
                }

                // 1. Layers (pick an object or type a name; Enter = last answer)
                List<double[]> heads = null, shafts = null, corridor = null;
                List<List<double[]>> rooms = null;

                if (!quick)
                {
                    if (!AskLayers(ed, db, settings, ref heads, ref shafts, ref rooms, ref corridor))
                        return;
                }
                else
                {
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        heads = GeometryReader.CollectPointsOnLayer(tr, db, settings.HeadsLayer);
                        shafts = GeometryReader.CollectPointsOnLayer(tr, db, settings.ShaftLayer);
                        rooms = GeometryReader.AllClosedBoundariesOnLayer(tr, db, settings.RoomsLayer);
                        corridor = GeometryReader.LargestClosedBoundaryOnLayer(tr, db, settings.CorridorLayer);
                        tr.Commit();
                    }
                    if (heads.Count == 0 || shafts.Count == 0 || rooms.Count == 0 || corridor == null)
                    {
                        ed.WriteMessage("\nThe saved layers came up empty - run SPKROUTE to set them again.");
                        return;
                    }
                    ed.WriteMessage("\nSPK: " + heads.Count + " sprinkler(s), " + shafts.Count
                        + " shaft(s), " + rooms.Count + " room(s) + corridor (saved setup).");
                }

                if (shafts.Count == 0)
                {
                    ed.WriteMessage("\nNo shafts found on \"" + settings.ShaftLayer + "\".");
                    if (!Commands.PromptShaftPoints(ed, shafts)) return;
                }

                // 2. Widths / offset (tilt is AUTOMATIC: the backend measures
                //    the grid angle per room, so mixed straight/tilted rooms
                //    each route in their own frame - nothing to ask)
                double branchWidth = settings.BranchWidth;
                double mainWidth = settings.MainWidth;
                double headerOffset = settings.HeaderOffset;
                bool eraseOld = settings.EraseOld;
                if (!quick)
                {
                    var b = Prompts.Distance(ed, "Branch pipe width (mm)", settings.BranchWidth);
                    if (b == null) return;
                    var m = Prompts.Distance(ed, "Main pipe width (mm)", settings.MainWidth);
                    if (m == null) return;
                    var o = Prompts.Distance(ed, "Header offset from sprinklers (mm)", settings.HeaderOffset);
                    if (o == null) return;
                    branchWidth = b.Value; mainWidth = m.Value; headerOffset = o.Value;

                    bool hasOld;
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        hasOld = DrawingService.HasPipeOutput(tr, db);
                        tr.Commit();
                    }
                    if (hasOld)
                    {
                        var erase = Prompts.YesNo(ed,
                            "Erase previous pipe run on " + DrawingService.PipeLayer + "?",
                            settings.EraseOld);
                        if (erase == null) return;
                        eraseOld = erase.Value;
                    }
                }

                // 3. Backend (auto-tilt: grid angle measured per room)
                ed.WriteMessage("\nRouting " + heads.Count + " sprinkler(s) from "
                    + shafts.Count + " shaft(s), tilt detected automatically...");
                var watch = Stopwatch.StartNew();
                var route = BackendClient.RouteJoint(heads, rooms, corridor, shafts,
                    branchWidth, mainWidth, headerOffset, 0.0, null, autoTilt: true);
                watch.Stop();

                // 4. Draw single-line pipes with flow arrows + shaft markers,
                //    one colour per shaft (one undo step, old run erased first)
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
                            DrawingService.DrawFlowArrow(tr, db, seg, branchWidth, mainWidth,
                                DrawingService.ShaftColor(seg.Shaft));
                        }
                        for (int i = 0; i < route.Risers.Count; i++)
                            DrawingService.DrawShaftMarker(tr, db, route.Risers[i], mainWidth,
                                DrawingService.ShaftColor(i));
                        tr.Commit();
                    }

                    // remember every answer for the next run / SPK
                    settings.Layout = "Rooms";
                    settings.BranchWidth = branchWidth;
                    settings.MainWidth = mainWidth;
                    settings.HeaderOffset = headerOffset;
                    settings.EraseOld = eraseOld;
                    settings.Save(db);
                }

                Report(ed, route, heads.Count, shafts.Count, watch.Elapsed.TotalSeconds);
            }
            catch (BackendException bex)
            {
                ed.WriteMessage("\n" + bex.Message);
            }
            catch (System.Exception ex)
            {
                ed.WriteMessage("\nSPKROUTE (Rooms) error: " + ex.Message);
            }
        }

        // ------------------------------------------------------------------

        /// <summary>All four layer questions, each with instant feedback
        /// (how many objects were found) and a retry when a layer is empty.
        /// Updates the settings; returns false when the user cancels.</summary>
        private static bool AskLayers(Editor ed, Database db, RunSettings settings,
            ref List<double[]> heads, ref List<double[]> shafts,
            ref List<List<double[]>> rooms, ref List<double[]> corridor)
        {
            while (true)  // sprinklers: must find at least one
            {
                var layer = Prompts.LayerPick(ed, db, "Sprinklers", settings.HeadsLayer);
                if (layer == null) return false;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    heads = GeometryReader.CollectPointsOnLayer(tr, db, layer);
                    tr.Commit();
                }
                ed.WriteMessage("\n  -> " + heads.Count + " sprinkler(s) on \"" + layer + "\"");
                if (heads.Count > 0) { settings.HeadsLayer = layer; break; }
                if (Prompts.Choice(ed, "No sprinklers there", new[] { "Retry", "Cancel" }, "Retry") != "Retry")
                    return false;
            }

            var shaftLayer = Prompts.LayerPick(ed, db, "Shafts", settings.ShaftLayer);
            if (shaftLayer == null) return false;
            settings.ShaftLayer = shaftLayer;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                shafts = GeometryReader.CollectPointsOnLayer(tr, db, shaftLayer);
                tr.Commit();
            }
            ed.WriteMessage("\n  -> " + shafts.Count + " shaft(s) on \"" + shaftLayer + "\"");

            while (true)  // rooms: at least one closed polyline
            {
                var layer = Prompts.LayerPick(ed, db, "Rooms", settings.RoomsLayer);
                if (layer == null) return false;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    rooms = GeometryReader.AllClosedBoundariesOnLayer(tr, db, layer);
                    tr.Commit();
                }
                ed.WriteMessage("\n  -> " + rooms.Count + " room polyline(s) on \"" + layer + "\"");
                if (rooms.Count > 0) { settings.RoomsLayer = layer; break; }
                if (Prompts.Choice(ed, "No closed room polylines there", new[] { "Retry", "Cancel" }, "Retry") != "Retry")
                    return false;
            }

            // never offer a corridor default equal to the rooms layer, or
            // Enter would dead-end on the must-differ guard below
            var corridorDefault = string.Equals(settings.CorridorLayer, settings.RoomsLayer,
                    StringComparison.OrdinalIgnoreCase)
                ? "SPK-CORRIDOR"
                : settings.CorridorLayer;
            while (true)  // corridor: one closed polyline, on a DIFFERENT layer
            {
                var layer = Prompts.LayerPick(ed, db, "Corridor", corridorDefault);
                if (layer == null) return false;
                if (string.Equals(layer, settings.RoomsLayer, StringComparison.OrdinalIgnoreCase))
                {
                    ed.WriteMessage("\n  !  Rooms and corridor layers must differ - otherwise the corridor is read as a room.");
                    continue;
                }
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    corridor = GeometryReader.LargestClosedBoundaryOnLayer(tr, db, layer);
                    tr.Commit();
                }
                if (corridor != null) { settings.CorridorLayer = layer; break; }
                ed.WriteMessage("\n  -> no closed corridor polyline on \"" + layer + "\"");
                if (Prompts.Choice(ed, "No corridor there", new[] { "Retry", "Cancel" }, "Retry") != "Retry")
                    return false;
            }
            return true;
        }

        /// <summary>Aligned per-shaft table, room summary, warnings with !.</summary>
        private static void Report(Editor ed, RouteJointResponse route,
            int headCount, int shaftCount, double seconds)
        {
            string[] colorNames = { "red", "yellow", "green", "cyan", "magenta", "blue", "orange", "violet" };
            ed.WriteMessage("\nDone in " + seconds.ToString("F1") + " s.");
            ed.WriteMessage("\n----------------------------------------------");
            for (int i = 0; i < route.Groups.Count; i++)
            {
                var g = route.Groups[i];
                ed.WriteMessage(string.Format("\n  Shaft {0} ({1,-7}) {2,5} head(s) {3,9:F1} m{4}",
                    i + 1, colorNames[i % colorNames.Length], g.HeadCount, g.Length / 1000.0,
                    g.HeadCount == 0 ? "  (nothing routed)" : ""));
            }
            ed.WriteMessage("\n----------------------------------------------");

            int connected = 0, empty = 0;
            var warnings = new List<string>();
            foreach (var room in route.Rooms)
            {
                switch (room.Status)
                {
                    case "tapped":
                        connected++;
                        break;
                    case "fallback":
                        connected++;
                        warnings.Add("\n  ! Room " + (room.Index + 1)
                            + ": no shared wall with the corridor - connected through the nearest point.");
                        break;
                    case "empty":
                        empty++;
                        break;
                    case "outline":
                        warnings.Add("\n  ! Room " + (room.Index + 1)
                            + ": ignored - this polyline covers the corridor (building outline, not a room).");
                        break;
                    case "skipped":
                        warnings.Add("\n  ! Room " + (room.Index + 1) + ": SKIPPED (" + room.HeadCount
                            + " head(s)) - it cannot reach the corridor; move it next to the corridor or redraw it.");
                        break;
                }
            }
            int roomHeads = 0;
            foreach (var room in route.Rooms)
                if (room.Shaft >= 0) roomHeads += room.HeadCount;
            int routed = headCount - route.SkippedHeads;

            ed.WriteMessage("\n  " + connected + " of " + route.Rooms.Count + " room(s) connected"
                + (empty > 0 ? " (" + empty + " without sprinklers)" : "")
                + " - " + (routed - roomHeads) + " corridor head(s)");
            ed.WriteMessage("\n  Total: " + routed + " head(s) piped, "
                + (route.TotalLength / 1000.0).ToString("F1") + " m of pipe on "
                + DrawingService.PipeLayer + ".  One-step UNDO removes it.");
            if (route.SkippedHeads > 0)
                ed.WriteMessage("\n  ! " + route.SkippedHeads
                    + " sprinkler(s) could not be piped (outside every room and the corridor, or unreachable).");
            foreach (var warning in warnings)
                ed.WriteMessage(warning);
        }
    }
}
