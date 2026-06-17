using System;
using ZwSoft.ZwCAD.Geometry;

namespace SprinklerPlugin
{
    /// <summary>Plane geometry helpers for double-line pipes and flow arrows.</summary>
    public static class Geo
    {
        public const double Eps = 1e-6;

        public static Point3d ToPoint3d(double[] p)
        {
            return new Point3d(p[0], p[1], 0.0);
        }

        /// <summary>Unit vector from a to b; Vector3d.ZAxis fallback for coincident points.</summary>
        public static Vector3d Direction(Point3d a, Point3d b)
        {
            var v = b - a;
            return v.Length < Eps ? Vector3d.XAxis : v / v.Length;
        }

        /// <summary>Unit vector perpendicular to d in the XY plane (d rotated +90deg).</summary>
        public static Vector3d Perpendicular(Vector3d d)
        {
            return new Vector3d(-d.Y, d.X, 0.0);
        }

        /// <summary>
        /// Flow arrow triangle at the segment midpoint pointing from a to b
        /// (the downstream direction). Returns tip, base-left, base-right;
        /// false when the segment is too short to carry a readable arrow.
        /// </summary>
        public static bool ArrowAtMidpoint(
            Point3d a, Point3d b, double pipeWidth,
            out Point3d tip, out Point3d baseLeft, out Point3d baseRight)
        {
            tip = baseLeft = baseRight = Point3d.Origin;

            var len = (b - a).Length;
            if (len < Eps) return false;

            // Slim triangle: 4 widths long, 2 widths across, capped to half
            // the segment so short pipes still read correctly.
            var arrowLen = Math.Min(4.0 * pipeWidth, 0.5 * len);
            if (arrowLen < Eps) return false;
            var halfBase = arrowLen / 4.0;

            var d = Direction(a, b);
            var n = Perpendicular(d);
            var mid = a + (b - a) / 2.0;

            tip = mid + d * (arrowLen / 2.0);
            var baseCentre = mid - d * (arrowLen / 2.0);
            baseLeft = baseCentre + n * halfBase;
            baseRight = baseCentre - n * halfBase;
            return true;
        }
    }
}
