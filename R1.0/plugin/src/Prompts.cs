using System;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;

namespace SprinklerPlugin
{
    /// <summary>Shared command-line prompts: pick-an-object layer answers
    /// (no typing, no spelling mistakes), keyword choices, yes/no and
    /// distances - all with saved defaults so Enter repeats the last run.</summary>
    internal static class Prompts
    {
        /// <summary>Layer answered by PICKING any object on it; the Name
        /// keyword types it instead; Enter keeps the default.
        /// Returns null when the user cancels.</summary>
        internal static string LayerPick(Editor ed, Database db, string what, string defaultLayer)
        {
            while (true)
            {
                var peo = new PromptEntityOptions(
                    "\n" + what + " - select an object on that layer, or [Name] <" + defaultLayer + ">")
                {
                    AllowNone = true,
                };
                peo.Keywords.Add("Name");
                var res = ed.GetEntity(peo);
                if (res.Status == PromptStatus.Cancel) return null;
                if (res.Status == PromptStatus.Keyword)
                    return Commands.PromptLayerName(ed, what + " layer name", defaultLayer);
                if (res.Status == PromptStatus.OK)
                {
                    string layer = null;
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        if (tr.GetObject(res.ObjectId, OpenMode.ForRead) is Entity ent)
                            layer = ent.Layer;
                        tr.Commit();
                    }
                    if (layer != null) return layer;
                    continue;  // not a drawable entity: ask again
                }
                return defaultLayer;  // Enter (or anything else): keep the default
            }
        }

        /// <summary>Keyword choice; Enter keeps the default; null on cancel.</summary>
        internal static string Choice(Editor ed, string message, string[] keywords, string defaultKeyword)
        {
            var pko = new PromptKeywordOptions("\n" + message) { AllowNone = true };
            foreach (var keyword in keywords)
                pko.Keywords.Add(keyword);
            pko.Keywords.Default = defaultKeyword;
            var res = ed.GetKeywords(pko);
            if (res.Status == PromptStatus.Cancel) return null;
            return string.IsNullOrEmpty(res.StringResult) ? defaultKeyword : res.StringResult;
        }

        /// <summary>Yes/No question; null on cancel.</summary>
        internal static bool? YesNo(Editor ed, string question, bool defaultYes)
        {
            var answer = Choice(ed, question, new[] { "Yes", "No" }, defaultYes ? "Yes" : "No");
            if (answer == null) return null;
            return string.Equals(answer, "Yes", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>Positive distance in mm; Enter keeps the default; null on cancel.</summary>
        internal static double? Distance(Editor ed, string message, double defaultValue)
        {
            var pdo = new PromptDoubleOptions("\n" + message)
            {
                DefaultValue = defaultValue,
                UseDefaultValue = true,
                AllowNegative = false,
                AllowZero = false,
            };
            var res = ed.GetDouble(pdo);
            if (res.Status == PromptStatus.Cancel) return null;
            return res.Status == PromptStatus.OK ? res.Value : defaultValue;
        }

        /// <summary>The sprinkler grid's tilt in degrees (-45, 45], from the
        /// circular mean of nearest-neighbour directions folded mod 90 deg;
        /// null when there are too few heads to tell.</summary>
        internal static double? GridTilt(System.Collections.Generic.List<double[]> heads)
        {
            if (heads == null || heads.Count < 8) return null;
            int step = Math.Max(1, heads.Count / 150);  // sample ~150 heads
            double sumX = 0.0, sumY = 0.0;
            int used = 0;
            for (int i = 0; i < heads.Count; i += step)
            {
                double best = double.MaxValue;
                int nearest = -1;
                for (int j = 0; j < heads.Count; j++)
                {
                    if (j == i) continue;
                    double dx = heads[j][0] - heads[i][0];
                    double dy = heads[j][1] - heads[i][1];
                    double d2 = dx * dx + dy * dy;
                    if (d2 < best && d2 > 1e-6) { best = d2; nearest = j; }
                }
                if (nearest < 0) continue;
                var angle = Math.Atan2(heads[nearest][1] - heads[i][1],
                                       heads[nearest][0] - heads[i][0]);
                sumX += Math.Cos(4.0 * angle);  // orientation mod 90 degrees
                sumY += Math.Sin(4.0 * angle);
                used++;
            }
            if (used < 5) return null;
            return Math.Atan2(sumY, sumX) / 4.0 * 180.0 / Math.PI;
        }
    }
}
