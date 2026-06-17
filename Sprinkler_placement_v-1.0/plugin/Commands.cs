using System;
using System.Collections.Generic;
using System.Net;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;
using ZwSoft.ZwCAD.Geometry;
using ZwSoft.ZwCAD.Runtime;
using Exception = System.Exception;
  
[assembly: CommandClass(typeof(SprinklerPlugin.Commands))]

namespace SprinklerPlugin
{
    // ZWCAD command surface for AUTOSPRINKLER:
    //   1. Enter the polyline layer name (default: layerx). The bounding
    //      rectangle of all polylines on that layer becomes the placement area —
    //      sprinklers fill the full rectangle, not just the polyline interior.
    //   2. Enter the obstacle-polyline layer name (optional — Enter to skip).
    //      Sprinklers will not be placed inside any closed polyline on this layer.
    //   3. Choose Straight/Tilted for architecture orientation (default Straight).
    //   4. Choose Yes/No for gap-fill (default Yes).
    //   5. POST the bounding rectangle (as a single room polyline) to
    //      /api/zwcad/scenarios. The backend runs scenarios 11, 12, 13
    //      (Fixed 3000/2700/3000 mm head spacing) in parallel. The HTTP
    //      call runs on a background thread; the modal ProgressDialog shows
    //      a progress bar with a Cancel button.
    //   6. Loop: choose scenario [1/2/3/Exit] → insert PP-CEILING PENDANT
    //      blocks on layer SPRINKLERS at every returned point. Picking another
    //      scenario erases the previous heads first.
    public class Commands
    {

        // Sanity check after NETLOAD.
        [CommandMethod("HELLOSPK")]
        public void HelloSprinkler()
        {
            var ed = Application.DocumentManager.MdiActiveDocument.Editor;
            ed.WriteMessage("\nSprinkler plugin loaded successfully!");
        }

        // Backend connectivity probe.

        [CommandMethod("SPKHEALTH")]
        public void SprinklerHealth()
        {
            var ed = Application.DocumentManager.MdiActiveDocument.Editor;
            HealthResponse h;
            try
            {
                
                h = ApiClient.GetHealth();
            }
            catch (Exception ex)
            {
                PrintBackendError(ed, ex);
                return;
            }

            int scenarioCount = h.scenarios != null ? h.scenarios.Count : 0;
            ed.WriteMessage("\nBackend OK. status={0} version={1} ezdxf={2} scenarios={3}",
                h.status, h.version, h.ezdxf, scenarioCount);
            if (h.scenarios != null)
            {
                foreach (var s in h.scenarios)
                {
                    ed.WriteMessage("\n  [{0}] {1}", s.id, s.name);
                }
            }
        }

        // Alias to mirror backend/zwcad_plugin.lsp's command name (SPK).
        [CommandMethod("SPK")]
        public void Spk() => AutoSprinkler();

        [CommandMethod("/AUTO-SPRINKLER")]
        public void AutoSprinkler()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            var ed  = doc.Editor;
            var db  = doc.Database;

            // ── 1. Polyline layer name (default: layerx) ──
            //         Build the placement rectangle as the bounding box of every
            //         polyline on this layer. Sprinklers will fill that full
            //         rectangle, not just the polyline interior.
            ed.WriteMessage("\nSprinkler plugin: enter polyline layer name.");
            var roomLayerOpts = new PromptStringOptions("\nEnter polyline layer name <layerx>: ")
            {
                AllowSpaces = true,
            };
            var roomLayerRes = ed.GetString(roomLayerOpts);
            if (roomLayerRes.Status != PromptStatus.OK
                && roomLayerRes.Status != PromptStatus.None) return;
            string layerName = string.IsNullOrWhiteSpace(roomLayerRes.StringResult)
                ? "layerx"
                : roomLayerRes.StringResult.Trim();

            var rect = ComputeLayerBbox(db, layerName);
            if (rect == null)
            {
                ed.WriteMessage("\nNo polyline found on layer '{0}'.", layerName);
                return;
            }
            ed.WriteMessage("\nFound polyline(s) on layer '{0}'.", layerName);

            // ── 2. Layer name for obstacle polylines (optional — Enter to skip) ──
            var obsLayerOpts = new PromptStringOptions(
                "\nEnter obstacle layer name (Enter to skip) : ")
            {
                AllowSpaces = true,
            };
            // PromptStringOptions doesn't support "no input is OK" directly,
            // but GetString returns PromptStatus.None when the user just hits
            // Enter on an empty line. Treat that as "no obstacle layer".
            var obsLayerRes = ed.GetString(obsLayerOpts);
            string obstacleLayer =
                (obsLayerRes.Status == PromptStatus.OK && !string.IsNullOrWhiteSpace(obsLayerRes.StringResult))
                    ? obsLayerRes.StringResult
                    : null;

            // ── 3. Architecture orientation (Straight by default — runs the
            //         original axis-aligned placement; Tilted detects each room's
            //         longest-edge angle on the backend and rotates the grid +
            //         block inserts to match) ──
            var orientOpt = new PromptKeywordOptions(
                "\nArchitecture orientation [Straight/Tilted] <Straight>: ");
            orientOpt.Keywords.Add("Straight");
            orientOpt.Keywords.Add("Tilted");
            orientOpt.Keywords.Default = "Straight";
            orientOpt.AllowNone = true;
            var orientPick = ed.GetKeywords(orientOpt);
            bool tilted = orientPick.Status == PromptStatus.OK
                          && orientPick.StringResult == "Tilted";
            ed.WriteMessage("\nArchitecture: {0}.",
                tilted ? "tilted (auto-rotate to longest wall)" : "straight (axis-aligned)");

            // ── 4. Gap-fill toggle (Yes by default — adds extra heads in
            //         uncovered pockets, but is the most expensive backend phase) ──
            var gapOpt = new PromptKeywordOptions("\nEnable gap-fill? [Yes/No] <Yes>: ");
            gapOpt.Keywords.Add("Yes");
            gapOpt.Keywords.Add("No");
            gapOpt.Keywords.Default = "Yes";
            gapOpt.AllowNone = true;
            var gapPick = ed.GetKeywords(gapOpt);
            // PromptStatus.None (Enter on no input) → default "Yes"; explicit
            // "No" disables gap-fill for a faster backend run.
            bool enableGapFill =
                !(gapPick.Status == PromptStatus.OK && gapPick.StringResult == "No");
            ed.WriteMessage("\nGap-fill: {0}.", enableGapFill ? "enabled" : "disabled");

            // ── 5. Real polylines on layerx become the room geometry.
            //         Each closed polyline is its own placement zone — the
            //         backend lays a grid across each polyline's bbox, keeps
            //         heads inside, slides outside heads ≤600 mm along the
            //         grid back inside, and discards anything farther out. ──
            var roomPolys = CollectPolylinesOnLayer(db, layerName, rect);
            if (roomPolys.Count == 0)
            {
                ed.WriteMessage(
                    "\nNo qualifying polylines (>=4 verts) found on layer '{0}'.",
                    layerName);
                return;
            }
            ed.WriteMessage("\nSending {0} polyline(s) from layer '{1}' to backend.",
                roomPolys.Count, layerName);

            // ── 6. Collect obstacle polylines (if a layer was supplied) ──
            var obsPolys = new List<List<double[]>>();
            if (obstacleLayer != null)
            {
                obsPolys = CollectPolylinesOnLayer(db, obstacleLayer, rect);
                ed.WriteMessage("\nFound {0} obstacle polyline(s) on layer '{1}'.",
                    obsPolys.Count, obstacleLayer);
            }

            // ── 7. POST to backend ──
            var request = new ZwcadScenarioRequest
            {
                room_polys      = roomPolys,
                obs_polys       = obsPolys,
                scenario_ids    = new List<int> { 11, 12, 13 },
                obs_min_offset  = 150.0,
                enable_gap_fill = enableGapFill,
                tilted          = tilted,
            };
            ed.WriteMessage("\nRequesting scenarios from backend...");
            Dictionary<int, ScenarioPoints> scenarios;
            try
            {
                // Background thread does the HTTP call; the modal dialog
                // polls a `done` flag on a Forms.Timer to drive the bar.
                // The Cancel button on the dialog calls ApiClient.AbortCurrent
                // to abort the in-flight request.
                scenarios = ProgressDialog.RunWithPolling(
                    "Requesting scenarios from backend",
                    () => ApiClient.PostZwcadScenarios(request),
                    onCancel: ApiClient.AbortCurrent);
            }
            catch (OperationCanceledException)
            {
                ed.WriteMessage("\nRequest cancelled by user.");
                return;
            }
            catch (Exception ex)
            {
                PrintBackendError(ed, ex);
                return;
            }
            ed.WriteMessage(
                "\nReceived {0} scenario(s). 1→Fixed 3000mm, 2→Fixed 2700mm, 3→3050-3100mm.",
                scenarios.Count);

            // ── 8. Loop: pick scenario → place blocks directly ──
            var drawn = new List<ObjectId>();
            try
            {
                while (true)
                {
                    var opt = new PromptKeywordOptions("\nChoose scenario [1/2/3/Exit] <Exit>: ");
                    opt.Keywords.Add("1");
                    opt.Keywords.Add("2");
                    opt.Keywords.Add("3");
                    opt.Keywords.Add("Exit");
                    opt.Keywords.Default = "Exit";
                    opt.AllowNone = true;

                    var pick = ed.GetKeywords(opt);
                    if (pick.Status != PromptStatus.OK) break;
                    string key = pick.StringResult ?? "Exit";
                    if (key == "Exit") break;

                    int scenarioId =
                        key == "1" ? 11 :
                        key == "2" ? 12 :
                        key == "3" ? 13 : 0;
                    if (!scenarios.TryGetValue(scenarioId, out var pts))
                    {
                        ed.WriteMessage("\nScenario {0} not in response.", scenarioId);
                        continue;
                    }
                    if (pts.Inside.Count == 0)
                    {
                        ed.WriteMessage("\nScenario {0} has 0 heads.", scenarioId);
                        continue;
                    }

                    try
                    {
                        int oldErased = PlaceBlocks(doc, pts, drawn);
                        if (oldErased > 0)
                            ed.WriteMessage(
                                "\nAuto-erased {0} old head(s)/marker(s) from earlier runs.",
                                oldErased);
                        ed.WriteMessage(
                            "\nPlaced {0} {1} block(s) for scenario {2}.",
                            pts.Inside.Count, DrawingHelper.SprinklerBlockName,
                            scenarioId);
                        // Which wall-gap formulas the backend applied
                        // (alpha = stretch last 3 bays to land at radius;
                        //  gama  = new head at 1000 + squeeze last 3).
                        ed.WriteMessage(
                            "\nuse alpha formula x{0}, use gama formula x{1}",
                            pts.AlphaCount, pts.GamaCount);
                        // Info text box next to the layout (tracked in
                        // `drawn`, so the next pick replaces it).
                        DrawScenarioInfo(doc, scenarioId, pts, roomPolys, drawn);
                    }
                    catch (Exception ex)
                    {
                        ed.WriteMessage("\nBlock insertion failed: {0}", ex.Message);
                    }
                }
            }
            finally
            {
                ed.WriteMessage("\nFinished.");
            }
        }

        // ---- Helpers ----

        // Draw a bordered info text box to the right of the placed layout
        // listing the scenario facts (head count, spacing, coverage, room
        // area, density) with a small sprinkler illustration and the
        // "Powered by CADELI" footer. Entities are tracked in `drawn` so
        // picking another scenario erases and replaces the box.
        private static void DrawScenarioInfo(
            Document doc, int scenarioId, ScenarioPoints pts,
            List<List<double[]>> roomPolys, List<ObjectId> drawn)
        {
            if (pts.Inside.Count == 0) return;

            double maxX = double.MinValue, maxY = double.MinValue;
            foreach (var p in pts.Inside)
            {
                if (p[0] > maxX) maxX = p[0];
                if (p[1] > maxY) maxY = p[1];
            }

            string name    = scenarioId == 12 ? "2 - Fixed 2700mm"
                           : scenarioId == 13 ? "3 - Spacing 3050-3100mm"
                           :                    "1 - Fixed 3000mm";
            string spacing = scenarioId == 12 ? "2700"
                           : scenarioId == 13 ? "3050-3100"
                           :                    "3000";
            int    n       = pts.Inside.Count;
            double circle  = Math.PI * 1.5 * 1.5;     // m2 per head at r=1500

            int    roomCount;
            double areaM2  = TotalRoomAreaM2(roomPolys, out roomCount);
            string density = n > 0 && areaM2 > 0
                ? (areaM2 / n).ToString("F1") + " m\\U+00B2/head"
                : "-";

            // "Blueprint amber" style: monospace font (set in DrawInfoBox),
            // amber labels/title (ACI 40), white values, dim grey footer.
            string[] lines =
            {
                "{\\C40;— S P R I N K L E R   D A T A —}",
                "",
                "{\\C40;scenario :} {\\C7;" + name + "}",
                "{\\C40;placed   :} {\\C7;" + n + "}",
                "{\\C40;pitch    :} {\\C7;" + spacing + " mm}",
                "{\\C40;radius   :} {\\C7;1500 mm}",
                "{\\C40;coverage :} {\\C7;" + (n * circle).ToString("F1") + " m\\U+00B2}",
                "{\\C40;density  :} {\\C40;" + density + "}",
                "",
                "{\\C251;------------------------------}",
                "{\\H0.85x;\\C251;Powered by CADELI · "
                    + DateTime.Now.ToString("yyyy-MM-dd HH:mm") + "}",
            };

            var db = doc.Database;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var ms = (BlockTableRecord)tr.GetObject(
                    bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
                DrawingHelper.DrawInfoBox(
                    tr, ms, db,
                    new Point3d(maxX + 3000.0, maxY, 0),
                    lines, drawn);
                tr.Commit();
            }
        }

        // Shoelace area of one polyline (mm2).
        private static double PolyAreaMm2(List<double[]> poly)
        {
            double a = 0;
            for (int i = 0; i < poly.Count; i++)
            {
                var p1 = poly[i];
                var p2 = poly[(i + 1) % poly.Count];
                a += p1[0] * p2[1] - p2[0] * p1[1];
            }
            return Math.Abs(a) / 2.0;
        }

        // Total room area in m2, ignoring container polylines (a bounding
        // rectangle / outline drawn around the real rooms) — mirrors the
        // backend's container filter so the reported area matches what was
        // actually sprinklered. Also returns the counted room number.
        private static double TotalRoomAreaM2(
            List<List<double[]>> rooms, out int roomCount)
        {
            int nRooms = rooms.Count;
            var areas = new double[nRooms];
            var cenX  = new double[nRooms];
            var cenY  = new double[nRooms];
            for (int i = 0; i < nRooms; i++)
            {
                areas[i] = PolyAreaMm2(rooms[i]);
                double sx = 0, sy = 0;
                foreach (var p in rooms[i]) { sx += p[0]; sy += p[1]; }
                cenX[i] = sx / rooms[i].Count;
                cenY[i] = sy / rooms[i].Count;
            }

            double total = 0;
            roomCount = 0;
            for (int i = 0; i < nRooms; i++)
            {
                bool isContainer = false;
                for (int j = 0; j < nRooms && !isContainer; j++)
                {
                    if (i == j || areas[j] >= areas[i]) continue;
                    isContainer = PointInPolyD(cenX[j], cenY[j], rooms[i]);
                }
                if (!isContainer)
                {
                    total += areas[i];
                    roomCount++;
                }
            }
            return total / 1e6;
        }

        // Ray-casting point-in-polygon for [x,y] vertex lists.
        private static bool PointInPolyD(double x, double y, List<double[]> poly)
        {
            bool inside = false;
            int n = poly.Count;
            for (int i = 0, j = n - 1; i < n; j = i++)
            {
                double yi = poly[i][1], yj = poly[j][1];
                if ((yi > y) != (yj > y))
                {
                    double xc = poly[j][0]
                        + (y - yj) * (poly[i][0] - poly[j][0]) / (yi - yj);
                    if (x < xc) inside = !inside;
                }
            }
            return inside;
        }

        // Walk model space, find every Polyline on `layerName`, and return the
        // closed rectangle (5 Point2d, last == first) that bounds them all.
        // Returns null if no polyline is found on that layer.
        private static List<Point2d> ComputeLayerBbox(Database db, string layerName)
        {
            double xmin = double.MaxValue, ymin = double.MaxValue;
            double xmax = double.MinValue, ymax = double.MinValue;
            bool found = false;

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var ms = (BlockTableRecord)tr.GetObject(
                    bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);

                foreach (ObjectId oid in ms)
                {
                    var ent = tr.GetObject(oid, OpenMode.ForRead);
                    var pl  = ent as Polyline;
                    if (pl == null) continue;
                    if (!string.Equals(pl.Layer, layerName, StringComparison.OrdinalIgnoreCase)) continue;

                    // Tessellated points so bulged (arc) segments extend the
                    // bbox to the top of the curve, not just its chord.
                    foreach (var p in TessellatePolyline(pl))
                    {
                        if (p[0] < xmin) xmin = p[0];
                        if (p[1] < ymin) ymin = p[1];
                        if (p[0] > xmax) xmax = p[0];
                        if (p[1] > ymax) ymax = p[1];
                        found = true;
                    }
                }
                tr.Commit();
            }
            if (!found) return null;
            return new List<Point2d>
            {
                new Point2d(xmin, ymin),
                new Point2d(xmax, ymin),
                new Point2d(xmax, ymax),
                new Point2d(xmin, ymax),
                new Point2d(xmin, ymin),
            };
        }

        // Walk model space and return every Polyline on `layerName` whose centroid
        // is inside `rect`. Each polyline is returned as a list of [x, y] points.
        // Used for both room polylines and obstacle polylines.
        private static List<List<double[]>> CollectPolylinesOnLayer(
            Database db, string layerName, List<Point2d> rect)
        {
            var rooms = new List<List<double[]>>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var ms = (BlockTableRecord)tr.GetObject(
                    bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);

                foreach (ObjectId oid in ms)
                {
                    var ent = tr.GetObject(oid, OpenMode.ForRead);
                    var pl  = ent as Polyline;
                    if (pl == null) continue;
                    if (!string.Equals(pl.Layer, layerName, StringComparison.OrdinalIgnoreCase)) continue;

                    // Bulged (arc) segments are tessellated into short straight
                    // segments so curved walls survive the trip to the backend.
                    // The >=4 point filter runs AFTER tessellation, so e.g. a
                    // circle drawn as a 2-vertex bulged polyline still qualifies.
                    var poly = TessellatePolyline(pl);
                    if (poly.Count < 4) continue;

                    double cx = 0, cy = 0;
                    foreach (var p in poly)
                    {
                        cx += p[0];
                        cy += p[1];
                    }
                    cx /= poly.Count;
                    cy /= poly.Count;

                    if (PointInPolygon(cx, cy, rect))
                        rooms.Add(poly);
                }
                tr.Commit();
            }
            return rooms;
        }

        // ---- Bulged-polyline tessellation ----

        // Expand a (possibly bulged) polyline into straight-line vertices.
        // LWPOLYLINE arc segments (bulge != 0) are tessellated at ~10° steps
        // so curved walls (domes, rounded bays, circular rooms) survive the
        // trip to the backend. Previously only the raw vertices were read,
        // so an arc segment collapsed to its straight chord and the area
        // under the curve was treated as outside the room (no heads there).
        private static List<double[]> TessellatePolyline(Polyline pl)
        {
            int n = pl.NumberOfVertices;
            var pts = new List<double[]>(n);
            for (int i = 0; i < n; i++)
            {
                var p1 = pl.GetPoint2dAt(i);
                pts.Add(new[] { p1.X, p1.Y });

                double bulge = pl.GetBulgeAt(i);
                if (Math.Abs(bulge) < 1e-9) continue;

                bool isLast = i == n - 1;
                if (isLast && !pl.Closed) continue;   // open pline: no segment after last vertex
                var p2 = pl.GetPoint2dAt(isLast ? 0 : i + 1);
                AppendBulgeArcPoints(pts, p1, p2, bulge);
            }
            return pts;
        }

        // Insert intermediate points along the arc segment p1→p2 described by
        // a DXF bulge (= tan(sweep/4); positive = counter-clockwise sweep).
        // Center/radius/sweep math verified against ezdxf.math.bulge_to_arc.
        // p1 is already in dst; the caller's loop appends p2.
        private static void AppendBulgeArcPoints(
            List<double[]> dst, Point2d p1, Point2d p2, double bulge)
        {
            double theta = 4.0 * Math.Atan(bulge);              // signed sweep angle
            double dx = p2.X - p1.X, dy = p2.Y - p1.Y;
            double chord = Math.Sqrt(dx * dx + dy * dy);
            if (chord < 1e-9 || Math.Abs(theta) < 1e-9) return;

            double r = chord / (2.0 * Math.Sin(Math.Abs(theta) / 2.0));
            double h = chord / (2.0 * Math.Tan(theta / 2.0));   // signed offset along left normal
            double cx = (p1.X + p2.X) / 2.0 - dy / chord * h;
            double cy = (p1.Y + p2.Y) / 2.0 + dx / chord * h;

            double a1 = Math.Atan2(p1.Y - cy, p1.X - cx);
            int steps = Math.Max(2, (int)Math.Ceiling(Math.Abs(theta) / (Math.PI / 18.0)));
            for (int k = 1; k < steps; k++)
            {
                double a = a1 + theta * k / steps;
                dst.Add(new[] { cx + r * Math.Cos(a), cy + r * Math.Sin(a) });
            }
        }

        // Ray-casting point-in-polygon test (the polygon is assumed closed —
        // last vertex equals first). Mirrors backend/zwcad_plugin.lsp's logic.
        private static bool PointInPolygon(double x, double y, List<Point2d> poly)
        {
            bool inside = false;
            for (int i = 0; i < poly.Count - 1; i++)
            {
                double x1 = poly[i].X,     y1 = poly[i].Y;
                double x2 = poly[i + 1].X, y2 = poly[i + 1].Y;
                if (y1 == y2) continue;
                if (Math.Min(y1, y2) > y) continue;
                if (Math.Max(y1, y2) <= y) continue;
                double xCross = x1 + ((y - y1) / (y2 - y1)) * (x2 - x1);
                if (x < xCross) inside = !inside;
            }
            return inside;
        }

        // Erase any previously-placed heads + outside markers, then:
        //   * Insert one PP-CEILING PENDANT block per Inside point (the real
        //     sprinklers — red, on SPRINKLERS layer).
        //   * Draw one green 150mm circle per Outside point (the bbox-grid
        //     intersections that were placed-then-culled — on SPRINKLER
        //     OUTSIDE layer, for visual verification of the strategy).
        // `drawn` tracks both kinds so the next scenario pick erases them all.
        // Returns how many LEFTOVER entities from earlier command runs were
        // auto-erased (drawing-wide sweep, beyond the session's own `drawn`).
        private static int PlaceBlocks(
            Document doc, ScenarioPoints pts, List<ObjectId> drawn)
        {
            var db = doc.Database;
            ObjectId blockDefId = DrawingHelper.EnsureBlockBuilt(db);
            int oldErased;

            using (var tr = db.TransactionManager.StartTransaction())
            {
                EraseTracked(tr, drawn);
                // Auto-erase heads from PREVIOUS runs too: every
                // PP-CEILING PENDANT insert + everything on the OUTSIDE /
                // INFO layers, so re-running the command never stacks
                // lattices on top of old ones.
                oldErased = DrawingHelper.EraseAllPlacedHeads(tr, db);
                DrawingHelper.EnsureLayer(tr, db, DrawingHelper.HeadLayer, DrawingHelper.HeadLayerColor);

                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var ms = (BlockTableRecord)tr.GetObject(
                    bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                foreach (var p in pts.Inside)
                {
                    // p is { x, y, rotation_radians }; rotation is 0 for
                    // axis-aligned rooms, the room's principal angle for
                    // tilted ones (so the block aligns with the wall).
                    double rot = p.Length >= 3 ? p[2] : 0.0;
                    DrawingHelper.InsertBlockAt(
                        tr, ms, blockDefId,
                        new Point3d(p[0], p[1], 0),
                        drawn,
                        rot);
                }

                // Outside markers — the "removed" sprinklers from the
                // place-then-cull strategy, drawn so the user can see the
                // bbox grid that was generated and verify the cull.
                foreach (var p in pts.Outside)
                {
                    DrawingHelper.DrawOutsideMarker(
                        tr, ms, db,
                        new Point3d(p[0], p[1], 0),
                        drawn);
                }

                tr.Commit();
            }
            return oldErased;
        }

        // Erase every entity in `drawn` (idempotent — skips already-erased ids)
        // and clear the list. Caller must have an active transaction.
        private static void EraseTracked(Transaction tr, List<ObjectId> drawn)
        {
            foreach (var id in drawn)
            {
                if (id.IsErased) continue;
                try
                {
                    var ent = (Entity)tr.GetObject(id, OpenMode.ForWrite);
                    ent.Erase();
                }
                catch
                {
                    // entity already gone — ignore
                }
            }
            drawn.Clear();
        }

        // Map common exceptions to clean messages on the command line.
        private static void PrintBackendError(Editor ed, Exception ex)
        {
            if (ex is WebException we)
            {
                if (we.Status == WebExceptionStatus.ConnectFailure ||
                    we.Status == WebExceptionStatus.NameResolutionFailure)
                {
                    ed.WriteMessage("\nBackend not reachable - is uvicorn running on {0}?",
                        ApiClient.BaseUrl);
                    return;
                }
                if (we.Status == WebExceptionStatus.Timeout)
                {
                    ed.WriteMessage("\nBackend is taking too long. Aborted.");
                    return;
                }
                if (we.Response is HttpWebResponse hr)
                {
                    if (hr.StatusCode == HttpStatusCode.NotFound)
                    {
                        ed.WriteMessage("\nEndpoint not found - is backend updated?");
                        return;
                    }
                    ed.WriteMessage("\nBackend HTTP {0}: {1}", (int)hr.StatusCode, hr.StatusDescription);
                    return;
                }
                ed.WriteMessage("\nBackend connection error: {0}", we.Message);
                return;
            }
            if (ex is FormatException)
            {
                ed.WriteMessage("\nBackend returned malformed response: {0}", ex.Message);
                return;
            }
            ed.WriteMessage("\nUnexpected error: {0}", ex.Message);
        }
    }
}
