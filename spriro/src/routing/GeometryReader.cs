using System;
using System.Collections.Generic;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.Geometry;

namespace Spriro.Routing
{
    /// <summary>Reads geometry out of the drawing: boundary vertices and layer scans.</summary>
    public static class GeometryReader
    {
        /// <summary>Polyline outline as [x, y] mm. Arc segments (bulges) are
        /// tessellated at ~2 degree steps so curved walls (domes) keep their
        /// real shape instead of collapsing to a straight chord.
        /// GetPoint3dAt returns WCS (GetPoint2dAt would return OCS, which is
        /// mirrored for polylines with a flipped extrusion direction).</summary>
        public static List<double[]> ReadBoundary(Polyline pl)
        {
            var pts = new List<double[]>();
            int n = pl.NumberOfVertices;
            int segments = pl.Closed ? n : n - 1;
            for (int i = 0; i < n; i++)
            {
                Point3d v = pl.GetPoint3dAt(i);
                pts.Add(new[] { v.X, v.Y });
                if (i >= segments) continue;
                double bulge = pl.GetBulgeAt(i);
                if (Math.Abs(bulge) < 1e-9) continue;
                AddArcPoints(pts, v, pl.GetPoint3dAt((i + 1) % n), bulge);
            }
            return pts;
        }

        /// <summary>Intermediate points along a bulge arc from a to b
        /// (bulge = tan(theta/4), positive = counterclockwise).</summary>
        private static void AddArcPoints(List<double[]> pts, Point3d a, Point3d b, double bulge)
        {
            double theta = 4.0 * Math.Atan(bulge);            // signed included angle
            double dx = b.X - a.X, dy = b.Y - a.Y;
            double chord = Math.Sqrt(dx * dx + dy * dy);
            if (chord < Geo.Eps) return;

            // Centre sits off the chord midpoint, along the chord's left normal.
            double h = chord / (2.0 * Math.Tan(theta / 2.0));
            double cx = (a.X + b.X) / 2.0 - dy / chord * h;
            double cy = (a.Y + b.Y) / 2.0 + dx / chord * h;
            double radius = Math.Sqrt((a.X - cx) * (a.X - cx) + (a.Y - cy) * (a.Y - cy));
            double startAngle = Math.Atan2(a.Y - cy, a.X - cx);

            int steps = Math.Max(2, (int)Math.Ceiling(Math.Abs(theta) / (Math.PI / 90.0)));
            for (int k = 1; k < steps; k++)
            {
                double ang = startAngle + theta * k / steps;
                pts.Add(new[] { cx + radius * Math.Cos(ang), cy + radius * Math.Sin(ang) });
            }
        }

        /// <summary>
        /// Sprinkler positions on a layer: block reference insertion points,
        /// circle centres and point entities all count. Duplicates are removed.
        /// </summary>
        public static List<double[]> CollectPointsOnLayer(Transaction tr, Database db, string layerName)
        {
            var pts = new List<double[]>();
            var seen = new HashSet<string>();

            foreach (ObjectId id in ModelSpace(tr, db))
            {
                if (!(tr.GetObject(id, OpenMode.ForRead) is Entity ent)) continue;
                if (!string.Equals(ent.Layer, layerName, StringComparison.OrdinalIgnoreCase)) continue;

                Point3d? pos = null;
                switch (ent)
                {
                    case BlockReference br: pos = br.Position; break;
                    case Circle c: pos = c.Center; break;
                    case DBPoint p: pos = p.Position; break;
                }
                if (pos == null) continue;

                var key = Math.Round(pos.Value.X, 3) + "|" + Math.Round(pos.Value.Y, 3);
                if (seen.Add(key)) pts.Add(new[] { pos.Value.X, pos.Value.Y });
            }
            return pts;
        }

        /// <summary>The LARGEST closed polyline on the layer as a boundary, or
        /// null. Largest-by-area picks the building outline when the layer
        /// also carries smaller closed details.</summary>
        public static List<double[]> LargestClosedBoundaryOnLayer(Transaction tr, Database db, string layerName)
        {
            List<double[]> best = null;
            double bestArea = 0.0;
            foreach (ObjectId id in ModelSpace(tr, db))
            {
                if (!(tr.GetObject(id, OpenMode.ForRead) is Polyline pl)) continue;
                if (!string.Equals(pl.Layer, layerName, StringComparison.OrdinalIgnoreCase)) continue;
                bool closed = pl.Closed
                    || pl.GetPoint3dAt(0).DistanceTo(pl.GetPoint3dAt(pl.NumberOfVertices - 1)) < Geo.Eps;
                if (!closed || pl.NumberOfVertices < 3) continue;
                var ring = ReadBoundary(pl);
                var area = Math.Abs(ShoelaceArea(ring));
                if (area > bestArea)
                {
                    best = ring;
                    bestArea = area;
                }
            }
            return best;
        }

        /// <summary>EVERY closed polyline on the layer as a boundary ring
        /// (joint mode: one ring per room). Arcs are tessellated like
        /// ReadBoundary; degenerate (zero-area) rings are dropped.</summary>
        public static List<List<double[]>> AllClosedBoundariesOnLayer(Transaction tr, Database db, string layerName)
        {
            var rings = new List<List<double[]>>();
            foreach (ObjectId id in ModelSpace(tr, db))
            {
                if (!(tr.GetObject(id, OpenMode.ForRead) is Polyline pl)) continue;
                if (!string.Equals(pl.Layer, layerName, StringComparison.OrdinalIgnoreCase)) continue;
                bool closed = pl.Closed
                    || pl.GetPoint3dAt(0).DistanceTo(pl.GetPoint3dAt(pl.NumberOfVertices - 1)) < Geo.Eps;
                if (!closed || pl.NumberOfVertices < 3) continue;
                var ring = ReadBoundary(pl);
                if (Math.Abs(ShoelaceArea(ring)) > Geo.Eps) rings.Add(ring);
            }
            return rings;
        }

        private static double ShoelaceArea(List<double[]> ring)
        {
            double sum = 0.0;
            for (int i = 0; i < ring.Count; i++)
            {
                var a = ring[i];
                var b = ring[(i + 1) % ring.Count];
                sum += a[0] * b[1] - b[0] * a[1];
            }
            return sum / 2.0;
        }

        private static BlockTableRecord ModelSpace(Transaction tr, Database db)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            return (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
        }
    }
}
