using ZwSoft.ZwCAD.Colors;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.Geometry;

namespace SprinklerPlugin
{
    /// <summary>Creates layers, the sprinkler block, and draws all result entities.</summary>
    public static class DrawingService
    {
        public const string HeadsLayer = "SPK-HEADS";
        public const string CoverLayer = "SPK-COVER";
        public const string PipeLayer = "SPK-PIPE";
        public const string FailLayer = "SPK-FAIL";
        public const string HeadBlockName = "SPK_HEAD";

        private const double HeadSymbolRadius = 75.0;    // mm
        private const double FailMarkRadius = 150.0;     // mm

        public static void EnsureLayers(Transaction tr, Database db)
        {
            EnsureLayer(tr, db, HeadsLayer, 3);   // green
            EnsureLayer(tr, db, CoverLayer, 4);   // cyan
            EnsureLayer(tr, db, PipeLayer, 5);    // blue
            EnsureLayer(tr, db, FailLayer, 1);    // red
        }

        public static void EnsureLayer(Transaction tr, Database db, string name, short colorIndex)
        {
            var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
            if (lt.Has(name)) return;
            lt.UpgradeOpen();
            var ltr = new LayerTableRecord
            {
                Name = name,
                Color = Color.FromColorIndex(ColorMethod.ByAci, colorIndex),
            };
            lt.Add(ltr);
            tr.AddNewlyCreatedDBObject(ltr, true);
        }

        /// <summary>Simple head symbol: circle with a 45deg cross, colors ByBlock.</summary>
        public static ObjectId EnsureSprinklerBlock(Transaction tr, Database db)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            if (bt.Has(HeadBlockName)) return bt[HeadBlockName];

            bt.UpgradeOpen();
            var btr = new BlockTableRecord { Name = HeadBlockName, Origin = Point3d.Origin };
            var btrId = bt.Add(btr);
            tr.AddNewlyCreatedDBObject(btr, true);

            var r = HeadSymbolRadius;
            var arm = r * 1.4142;
            Entity[] symbol =
            {
                new Circle(Point3d.Origin, Vector3d.ZAxis, r),
                new Line(new Point3d(-arm / 2, -arm / 2, 0), new Point3d(arm / 2, arm / 2, 0)),
                new Line(new Point3d(-arm / 2, arm / 2, 0), new Point3d(arm / 2, -arm / 2, 0)),
            };
            foreach (var ent in symbol)
            {
                ent.ColorIndex = 0;  // ByBlock: inherits the insert's layer color
                btr.AppendEntity(ent);
                tr.AddNewlyCreatedDBObject(ent, true);
            }
            return btrId;
        }

        public static void DrawHead(Transaction tr, Database db, double[] p, ObjectId blockId)
        {
            var br = new BlockReference(Geo.ToPoint3d(p), blockId) { Layer = HeadsLayer };
            Append(tr, db, br);
        }

        public static void DrawCoverage(Transaction tr, Database db, double[] p, double radius)
        {
            var c = new Circle(Geo.ToPoint3d(p), Vector3d.ZAxis, radius) { Layer = CoverLayer };
            Append(tr, db, c);
        }

        public static void MarkFailing(Transaction tr, Database db, double[] p)
        {
            var c = new Circle(Geo.ToPoint3d(p), Vector3d.ZAxis, FailMarkRadius) { Layer = FailLayer };
            Append(tr, db, c);
        }

        /// <summary>ACI colour for a shaft's pipe network: red, yellow, green, ... (cycles).</summary>
        public static short ShaftColor(int shaft)
        {
            short[] palette = { 1, 2, 3, 4, 6, 5, 30, 92 };  // red yellow green cyan magenta blue orange ...
            return shaft < 0 ? (short)256 : palette[shaft % palette.Length];  // 256 = ByLayer
        }

        /// <summary>Concentric double circle marking a shaft/riser feed point.</summary>
        public static void DrawShaftMarker(Transaction tr, Database db, double[] p, double mainWidth, short colorIndex)
        {
            var r = mainWidth > 50.0 ? mainWidth : 50.0;
            Append(tr, db, new Circle(Geo.ToPoint3d(p), Vector3d.ZAxis, r) { Layer = PipeLayer, ColorIndex = colorIndex });
            Append(tr, db, new Circle(Geo.ToPoint3d(p), Vector3d.ZAxis, r * 1.8) { Layer = PipeLayer, ColorIndex = colorIndex });
        }

        /// <summary>Single-line pipe: the segment centreline as a plain
        /// line on the pipe layer (the user prefers single-line drawings;
        /// the double-line outline drawing below is kept but unused).</summary>
        public static void DrawPipeLine(Transaction tr, Database db, SegmentDto seg, short colorIndex = 256)
        {
            var a = Geo.ToPoint3d(seg.Start);
            var b = Geo.ToPoint3d(seg.End);
            if ((b - a).Length < Geo.Eps) return;
            var line = new Line(a, b) { Layer = PipeLayer, ColorIndex = colorIndex };
            Append(tr, db, line);
        }

        /// <summary>
        /// One closed ring of the backend's merged pipe outline. Junctions
        /// arrive pre-fitted (90 degree elbows, tees, 4-way crosses) so no
        /// construction lines cross the pipe interior.
        /// colorIndex 256 = ByLayer; otherwise the shaft's network colour.
        /// </summary>
        public static void DrawPipeOutline(Transaction tr, Database db,
            System.Collections.Generic.List<double[]> ring, short colorIndex = 256)
        {
            if (ring == null || ring.Count < 3) return;
            var pl = new Polyline();
            for (int i = 0; i < ring.Count; i++)
                pl.AddVertexAt(i, new Point2d(ring[i][0], ring[i][1]), 0, 0, 0);
            pl.Closed = true;
            pl.Layer = PipeLayer;
            pl.ColorIndex = colorIndex;
            Append(tr, db, pl);
        }

        /// <summary>Centred flow arrow for one segment, pointing downstream
        /// (start -> end is the backend's flow direction).</summary>
        public static void DrawFlowArrow(Transaction tr, Database db, SegmentDto seg,
            double branchWidth, double mainWidth, short colorIndex = 256)
        {
            var a = Geo.ToPoint3d(seg.Start);
            var b = Geo.ToPoint3d(seg.End);
            if ((b - a).Length < Geo.Eps) return;

            var width = seg.Kind == "branch" ? branchWidth : mainWidth;
            if (Geo.ArrowAtMidpoint(a, b, width, out var tip, out var left, out var right))
            {
                var arrow = new Solid(tip, left, right) { Layer = PipeLayer, ColorIndex = colorIndex };
                Append(tr, db, arrow);
            }
        }

        /// <summary>Any previous plugin output on the pipe layer?</summary>
        public static bool HasPipeOutput(Transaction tr, Database db)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
            foreach (ObjectId id in ms)
            {
                if (tr.GetObject(id, OpenMode.ForRead) is Entity ent
                    && string.Equals(ent.Layer, PipeLayer, System.StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        /// <summary>Erase what the plugin drew: everything on the pipe layer;
        /// with everything=true also coverage circles, fail marks and the
        /// SPK_HEAD block inserts. The user's own sprinklers/layers are
        /// never touched. Returns how many entities were erased.</summary>
        public static int EraseOutput(Transaction tr, Database db, bool everything)
        {
            int erased = 0;
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
            foreach (ObjectId id in ms)
            {
                if (!(tr.GetObject(id, OpenMode.ForRead) is Entity ent)) continue;
                bool target = string.Equals(ent.Layer, PipeLayer, System.StringComparison.OrdinalIgnoreCase);
                if (everything && !target)
                {
                    target = string.Equals(ent.Layer, CoverLayer, System.StringComparison.OrdinalIgnoreCase)
                        || string.Equals(ent.Layer, FailLayer, System.StringComparison.OrdinalIgnoreCase)
                        || (ent is BlockReference br && br.Name == HeadBlockName);
                }
                if (!target) continue;
                ent.UpgradeOpen();
                ent.Erase();
                erased++;
            }
            return erased;
        }

        private static void Append(Transaction tr, Database db, Entity ent)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
            ms.AppendEntity(ent);
            tr.AddNewlyCreatedDBObject(ent, true);
        }
    }
}
