using System;
using System.Collections.Generic;
using ZwSoft.ZwCAD.Colors;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.Geometry;

namespace Spriro.P1
{
    // Drawing primitives shared across command methods.
    //
    // All callers must already hold an open Transaction and pass the live
    // BlockTableRecord (typically ModelSpace, opened ForWrite). All work
    // happens on the calling thread — never call these from a background
    // thread or timer not bound to ZWCAD's main UI thread.
    public static class DrawingHelper
    {
        // Sprinkler heads are inserted on layer SPRINKLERS, color 1 (red).
        public const string HeadLayer      = "SPRINKLERS";
        public const short  HeadLayerColor = 1;            // red

        // Per-room bounding rectangle (the "place sprinklers in this rectangle
        // then cull what falls outside the polyline" strategy) is drawn on a
        // dedicated green layer so the user can see / hide it easily.
        public const string BboxLayer      = "SPRINKLER BBOX";
        public const short  BboxLayerColor = 3;            // green

        // Debug markers for bbox-grid points that were placed in the rectangle
        // but fell outside the architecture polyline. Drawn as blue
        // sprinkler-shaped markers so the user can verify the cull visually.
        // Distinct layer from BBOX so the user can hide it independently;
        // blue keeps it visually separate from the green bbox border.
        public const string OutsideLayer      = "SPRINKLER OUTSIDE";
        public const short  OutsideLayerColor = 5;         // blue

        // PP-CEILING PENDANT block — built in C# from primitives so we don't
        // need an external blockssprinkler.dxf at runtime.
        public const string  SprinklerBlockName = "PP-CEILING PENDANT";
        private const string PendentLayer       = "PENDENT SPRINKLER";
        private const string CoverageLayer      = "SPRINKLER COVERAGE";

        // Create the layer if it doesn't exist; idempotent. If the layer
        // already exists with a different ACI, update it so callers that
        // changed the color (e.g. SPRINKLER OUTSIDE moving green → blue)
        // see the new colour without the user having to purge the layer.
        public static void EnsureLayer(Transaction tr, Database db, string name, short colorIndex)
        {
            var layerTable = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
            if (layerTable.Has(name))
            {
                var existing = (LayerTableRecord)tr.GetObject(layerTable[name], OpenMode.ForRead);
                if (existing.Color.ColorIndex != colorIndex)
                {
                    existing.UpgradeOpen();
                    existing.Color = Color.FromColorIndex(ColorMethod.ByAci, colorIndex);
                }
                return;
            }

            layerTable.UpgradeOpen();
            var layer = new LayerTableRecord
            {
                Name  = name,
                Color = Color.FromColorIndex(ColorMethod.ByAci, colorIndex),
            };
            layerTable.Add(layer);
            tr.AddNewlyCreatedDBObject(layer, true);
        }

        // Draw the axis-aligned bounding rectangle around a list of polyline
        // vertices as a closed 4-vertex Polyline on the green BboxLayer.
        // Idempotent against the layer itself (created on first call). The
        // entity's ObjectId is appended to `created` so the caller can erase
        // it later if needed. Empty / single-point input is a no-op.
        public static void DrawBboxAroundPoly(
            Transaction tr, BlockTableRecord ms, Database db,
            IList<Point2d> verts, List<ObjectId> created)
        {
            if (verts == null || verts.Count == 0) return;

            double minX = verts[0].X, maxX = verts[0].X;
            double minY = verts[0].Y, maxY = verts[0].Y;
            for (int i = 1; i < verts.Count; i++)
            {
                double x = verts[i].X, y = verts[i].Y;
                if (x < minX) minX = x; else if (x > maxX) maxX = x;
                if (y < minY) minY = y; else if (y > maxY) maxY = y;
            }
            if (maxX - minX <= 0.0 || maxY - minY <= 0.0) return;

            EnsureLayer(tr, db, BboxLayer, BboxLayerColor);

            var pl = new Polyline
            {
                Layer  = BboxLayer,
                Closed = true,
            };
            pl.AddVertexAt(0, new Point2d(minX, minY), 0, 0, 0);
            pl.AddVertexAt(1, new Point2d(maxX, minY), 0, 0, 0);
            pl.AddVertexAt(2, new Point2d(maxX, maxY), 0, 0, 0);
            pl.AddVertexAt(3, new Point2d(minX, maxY), 0, 0, 0);

            ObjectId id = ms.AppendEntity(pl);
            tr.AddNewlyCreatedDBObject(pl, true);
            if (created != null) created.Add(id);
        }

        // Draw a green "sprinkler" marker at p on the OutsideLayer for
        // bbox-grid intersections that fell outside the architecture
        // polyline. Two concentric circles so it reads like a real
        // sprinkler-with-coverage at a glance, just green instead of red:
        //   outer = 1500 mm (matches the real-head coverage circle)
        //   inner =   80 mm (center dot)
        public static void DrawOutsideMarker(
            Transaction tr, BlockTableRecord ms, Database db, Point3d p,
            List<ObjectId> created)
        {
            EnsureLayer(tr, db, OutsideLayer, OutsideLayerColor);

            var outer = new Circle(p, Vector3d.ZAxis, 1500.0) { Layer = OutsideLayer };
            ObjectId oid = ms.AppendEntity(outer);
            tr.AddNewlyCreatedDBObject(outer, true);
            if (created != null) created.Add(oid);

            var inner = new Circle(p, Vector3d.ZAxis, 80.0) { Layer = OutsideLayer };
            ObjectId iid = ms.AppendEntity(inner);
            tr.AddNewlyCreatedDBObject(inner, true);
            if (created != null) created.Add(iid);
        }

        // Erase every PP-CEILING PENDANT block reference in model space,
        // plus everything on the SPRINKLER OUTSIDE / SPRINKLER INFO layers
        // — including heads left over from PREVIOUS command runs that the
        // per-session `drawn` list doesn't know about (re-running the
        // command used to stack new lattices on top of old ones).
        // Caller must hold an open Transaction. Returns the erase count.
        public static int EraseAllPlacedHeads(Transaction tr, Database db)
        {
            int erased = 0;
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(
                bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);

            foreach (ObjectId oid in ms)
            {
                if (oid.IsErased) continue;
                Entity ent;
                try { ent = tr.GetObject(oid, OpenMode.ForRead) as Entity; }
                catch { continue; }
                if (ent == null) continue;

                bool isOld = false;
                var bref = ent as BlockReference;
                if (bref != null)
                {
                    try
                    {
                        var btr = (BlockTableRecord)tr.GetObject(
                            bref.BlockTableRecord, OpenMode.ForRead);
                        isOld = string.Equals(btr.Name, SprinklerBlockName,
                            StringComparison.OrdinalIgnoreCase);
                    }
                    catch { /* unreadable definition — leave it alone */ }
                }
                if (!isOld)
                {
                    isOld = string.Equals(ent.Layer, OutsideLayer,
                                StringComparison.OrdinalIgnoreCase)
                         || string.Equals(ent.Layer, InfoLayer,
                                StringComparison.OrdinalIgnoreCase);
                }
                if (!isOld) continue;

                try
                {
                    ent.UpgradeOpen();
                    ent.Erase();
                    erased++;
                }
                catch { /* locked layer etc. — skip */ }
            }
            return erased;
        }

        // Place a single BlockReference at p on layer SPRINKLERS, scale 1.
        // `rotationRadians` is the principal angle of the room the point came
        // from — 0 for axis-aligned rooms, non-zero for tilted ones, so the
        // inserted block aligns with the wall direction instead of staying
        // axis-aligned across a rotated grid.
        public static void InsertBlockAt(
            Transaction tr, BlockTableRecord ms, ObjectId blockDefId, Point3d p,
            List<ObjectId> created, double rotationRadians = 0.0)
        {
            var bref = new BlockReference(p, blockDefId)
            {
                Layer    = HeadLayer,
                Rotation = rotationRadians,
            };
            ObjectId id = ms.AppendEntity(bref);
            tr.AddNewlyCreatedDBObject(bref, true);
            created.Add(id);
        }

        // ── Build the PP-CEILING PENDANT block from simple primitives ──────
        //
        // Pendent head seen from below, proportioned exactly like the user's
        // reference image where the OUTERMOST circle is the 1500 mm coverage
        // circle:
        //   * coverage circle r=1500 (off-white, SPRINKLER COVERAGE layer)
        //   * rim edge r=1380 + inner ring r=1020 (off-white)
        //   * gear-toothed deflector r=440..540 (14 teeth) + centre hole
        //     r=60, yellow
        // The definition carries a version tag in its Comments; when the
        // design changes the old definition is wiped and rebuilt in place,
        // so already-open drawings update on regen.
        private const string BlockVersion = "CADELI-HEAD v8";

        public static ObjectId EnsureBlockBuilt(Database db)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);

                BlockTableRecord btr;
                ObjectId btrId;
                if (bt.Has(SprinklerBlockName))
                {
                    btrId = bt[SprinklerBlockName];
                    btr = (BlockTableRecord)tr.GetObject(btrId, OpenMode.ForWrite);
                    if (btr.Comments == BlockVersion)
                    {
                        tr.Commit();           // already the current symbol
                        return btrId;
                    }
                    var old = new List<ObjectId>();
                    foreach (ObjectId id in btr)
                    {
                        if (!id.IsErased) old.Add(id);
                    }
                    foreach (var id in old)    // old definition → wipe and rebuild
                    {
                        var e = (Entity)tr.GetObject(id, OpenMode.ForWrite);
                        e.Erase();
                    }
                }
                else
                {
                    bt.UpgradeOpen();
                    btr = new BlockTableRecord
                    {
                        Name   = SprinklerBlockName,
                        Origin = Point3d.Origin,
                    };
                    btrId = bt.Add(btr);
                    tr.AddNewlyCreatedDBObject(btr, true);
                }

                EnsureLayer(tr, db, PendentLayer,  2);     // yellow
                EnsureLayer(tr, db, CoverageLayer, 254);   // off-white

                DrawHeadSymbol(tr, btr, 0, 0, 1.0, null);             // head graphic
                AddCircle(tr, btr, 0, 0, 1500.0, CoverageLayer, 254); // coverage (outermost)
                btr.Comments = BlockVersion;

                tr.Commit();
                return btrId;
            }
        }

        // ── Scenario info box ───────────────────────────────────────────
        public const string InfoLayer      = "SPRINKLER INFO";
        public const short  InfoLayerColor = 2;            // yellow

        // Draw a bordered text box at `topLeft` (model space, mm) listing
        // placement facts for the current scenario. All created ids go into
        // `created` so the next scenario pick erases the box too.
        public static void DrawInfoBox(
            Transaction tr, BlockTableRecord ms, Database db,
            Point3d topLeft, string[] lines, List<ObjectId> created)
        {
            EnsureLayer(tr, db, InfoLayer, InfoLayerColor);

            var mt = new MText
            {
                Location   = topLeft,
                // Monospace "blueprint" font for the whole box; per-line
                // colors come from inline \C codes in `lines`.
                Contents   = "\\fConsolas|b0|i0|c0|p49;" + string.Join("\\P", lines),
                TextHeight = 400.0,
                Width      = 10000.0,
                Layer      = InfoLayer,
                Attachment = AttachmentPoint.TopLeft,
            };
            ObjectId mid = ms.AppendEntity(mt);
            tr.AddNewlyCreatedDBObject(mt, true);
            if (created != null) created.Add(mid);

            // Border rectangle around the text, styled as a "3D card":
            // thick cyan frame + grey drop shadow on the bottom-right.
            double w, h;
            try { w = mt.ActualWidth; h = mt.ActualHeight; }
            catch { w = 10000.0; h = lines.Length * 400.0 * 1.7; }
            const double margin = 400.0;
            double left   = topLeft.X - margin;
            double right  = topLeft.X + w + margin;
            double top    = topLeft.Y + margin;
            double bottom = topLeft.Y - h - margin;

            // Drop shadow: thick L-shape just outside the bottom-right edge.
            const double off = 300.0;
            var shadow = new Polyline
            {
                Layer         = InfoLayer,
                Color         = Color.FromColorIndex(ColorMethod.ByAci, 251),  // dark grey
                ConstantWidth = 150.0,
            };
            shadow.AddVertexAt(0, new Point2d(left + off,  bottom - off), 0, 0, 0);
            shadow.AddVertexAt(1, new Point2d(right + off, bottom - off), 0, 0, 0);
            shadow.AddVertexAt(2, new Point2d(right + off, top - off),    0, 0, 0);
            ObjectId sid = ms.AppendEntity(shadow);
            tr.AddNewlyCreatedDBObject(shadow, true);
            if (created != null) created.Add(sid);

            // Main frame: thick cyan border.
            var pl = new Polyline
            {
                Layer         = InfoLayer,
                Closed        = true,
                Color         = Color.FromColorIndex(ColorMethod.ByAci, 4),    // cyan
                ConstantWidth = 40.0,
            };
            pl.AddVertexAt(0, new Point2d(left,  top),    0, 0, 0);
            pl.AddVertexAt(1, new Point2d(right, top),    0, 0, 0);
            pl.AddVertexAt(2, new Point2d(right, bottom), 0, 0, 0);
            pl.AddVertexAt(3, new Point2d(left,  bottom), 0, 0, 0);
            ObjectId bid = ms.AppendEntity(pl);
            tr.AddNewlyCreatedDBObject(pl, true);
            if (created != null) created.Add(bid);
        }

        // Draw the head graphic (off-white rim + rings, yellow gear deflector
        // + centre hole) into `owner` at (cx, cy), scaled. Used both by the
        // block definition (origin, scale 1) and as the small illustration
        // inside the info box. Ids are tracked in `created` when non-null.
        public static void DrawHeadSymbol(
            Transaction tr, BlockTableRecord owner,
            double cx, double cy, double scale, List<ObjectId> created)
        {
            Track(created, AddCircleRgb(tr, owner, cx, cy, 1020.0 * scale, 109, 111, 114));  // single ring — grey RGB(109,111,114), thin
            Track(created, AddGear(tr, owner, cx, cy, 540.0 * scale, 440.0 * scale, 14, 2)); // gear        — yellow
            Track(created, AddCircle(tr, owner, cx, cy, 60.0 * scale, PendentLayer, 2));     // centre hole — yellow
            Track(created, AddCircle(tr, owner, cx, cy, 10.0 * scale, PendentLayer, 2));     // centre dot  — yellow
        }

        private static void Track(List<ObjectId> created, ObjectId id)
        {
            if (created != null) created.Add(id);
        }

        private static ObjectId AddCircle(Transaction tr, BlockTableRecord owner,
            double cx, double cy, double radius, string layer, short aci,
            bool thin = false)
        {
            var c = new Circle(new Point3d(cx, cy, 0), Vector3d.ZAxis, radius)
            {
                Layer = layer,
                Color = Color.FromColorIndex(ColorMethod.ByAci, aci),
            };
            if (thin) c.LineWeight = LineWeight.LineWeight000;
            ObjectId id = owner.AppendEntity(c);
            tr.AddNewlyCreatedDBObject(c, true);
            return id;
        }

        private static ObjectId AddCircleRgb(Transaction tr, BlockTableRecord owner,
            double cx, double cy, double radius, byte r, byte g, byte b)
        {
            var c = new Circle(new Point3d(cx, cy, 0), Vector3d.ZAxis, radius)
            {
                Layer      = PendentLayer,
                Color      = Color.FromRgb(r, g, b),
                LineWeight = LineWeight.LineWeight000,
            };
            ObjectId id = owner.AppendEntity(c);
            tr.AddNewlyCreatedDBObject(c, true);
            return id;
        }

        // Closed gear-tooth polyline — the deflector seen from below.
        // `teeth` square teeth alternating between rOuter and rInner with
        // radial flanks; tooth top takes ~45% of each cycle.
        private static ObjectId AddGear(Transaction tr, BlockTableRecord owner,
            double cx, double cy, double rOuter, double rInner, int teeth, short aci)
        {
            var pl = new Polyline
            {
                Layer  = PendentLayer,
                Closed = true,
                Color  = Color.FromColorIndex(ColorMethod.ByAci, aci),
            };
            double cycle = 2.0 * Math.PI / teeth;
            double tooth = cycle * 0.45;
            int v = 0;
            for (int i = 0; i < teeth; i++)
            {
                double a = i * cycle;
                pl.AddVertexAt(v++, new Point2d(cx + rOuter * Math.Cos(a),         cy + rOuter * Math.Sin(a)),         0, 0, 0);
                pl.AddVertexAt(v++, new Point2d(cx + rOuter * Math.Cos(a + tooth), cy + rOuter * Math.Sin(a + tooth)), 0, 0, 0);
                pl.AddVertexAt(v++, new Point2d(cx + rInner * Math.Cos(a + tooth), cy + rInner * Math.Sin(a + tooth)), 0, 0, 0);
                pl.AddVertexAt(v++, new Point2d(cx + rInner * Math.Cos(a + cycle), cy + rInner * Math.Sin(a + cycle)), 0, 0, 0);
            }
            ObjectId id = owner.AppendEntity(pl);
            tr.AddNewlyCreatedDBObject(pl, true);
            return id;
        }

    }
}
