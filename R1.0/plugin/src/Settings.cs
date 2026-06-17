using System.Globalization;
using ZwSoft.ZwCAD.DatabaseServices;

namespace SprinklerPlugin
{
    /// <summary>
    /// Per-drawing settings: every answer the user gives is saved in the
    /// drawing (Named Objects Dictionary), so the next run defaults to the
    /// previous answers - press Enter a few times and it routes.  The SPK
    /// quick command runs entirely from these saved values.
    /// </summary>
    public class RunSettings
    {
        private const string DictKey = "SPRINKLER_PLUGIN";

        public string HeadsLayer = DrawingService.HeadsLayer;
        public string ShaftLayer = "SPK-SHAFT";
        public string RoomsLayer = "SPK-ROOM";
        public string CorridorLayer = "SPK-CORRIDOR";
        public string BoundaryLayer = "SPK-ROOM";   // open-layout room boundary
        public string Layout = "Rooms";             // Rooms | Open
        public double BranchWidth = 32.0;           // mm
        public double MainWidth = 65.0;             // mm
        public double HeaderOffset = 300.0;         // mm
        public double Tilt;                         // degrees CCW (0 = straight)
        public bool EraseOld = true;
        public bool HasRun;                         // a previous run was saved

        public static RunSettings Load(Database db)
        {
            var settings = new RunSettings();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForRead);
                if (nod.Contains(DictKey))
                {
                    settings.HasRun = true;
                    var record = (Xrecord)tr.GetObject(nod.GetAt(DictKey), OpenMode.ForRead);
                    if (record.Data != null)
                        foreach (TypedValue value in record.Data.AsArray())
                            settings.Apply(value.Value as string);
                }
                tr.Commit();
            }
            return settings;
        }

        /// <summary>Call inside a document lock.</summary>
        public void Save(Database db)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForWrite);
                using (var data = new ResultBuffer(
                    Text("heads=" + HeadsLayer),
                    Text("shaft=" + ShaftLayer),
                    Text("rooms=" + RoomsLayer),
                    Text("corridor=" + CorridorLayer),
                    Text("boundary=" + BoundaryLayer),
                    Text("layout=" + Layout),
                    Text("branch=" + BranchWidth.ToString(CultureInfo.InvariantCulture)),
                    Text("main=" + MainWidth.ToString(CultureInfo.InvariantCulture)),
                    Text("offset=" + HeaderOffset.ToString(CultureInfo.InvariantCulture)),
                    Text("tilt=" + Tilt.ToString(CultureInfo.InvariantCulture)),
                    Text("erase=" + (EraseOld ? "1" : "0"))))
                {
                    // Xrecord.Data COPIES the buffer, so disposing ours is safe
                    if (nod.Contains(DictKey))
                    {
                        var record = (Xrecord)tr.GetObject(nod.GetAt(DictKey), OpenMode.ForWrite);
                        record.Data = data;
                    }
                    else
                    {
                        var record = new Xrecord { Data = data };
                        nod.SetAt(DictKey, record);
                        tr.AddNewlyCreatedDBObject(record, true);
                    }
                    tr.Commit();
                }
            }
            HasRun = true;
        }

        private void Apply(string pair)
        {
            if (pair == null) return;
            int eq = pair.IndexOf('=');
            if (eq <= 0) return;
            var key = pair.Substring(0, eq);
            var value = pair.Substring(eq + 1);
            switch (key)
            {
                case "heads": HeadsLayer = value; break;
                case "shaft": ShaftLayer = value; break;
                case "rooms": RoomsLayer = value; break;
                case "corridor": CorridorLayer = value; break;
                case "boundary": BoundaryLayer = value; break;
                case "layout": Layout = value; break;
                case "branch": TryNumber(value, ref BranchWidth); break;
                case "main": TryNumber(value, ref MainWidth); break;
                case "offset": TryNumber(value, ref HeaderOffset); break;
                case "tilt":  // unlike widths, tilt may be 0 or negative
                    if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var degrees))
                        Tilt = degrees;
                    break;
                case "erase": EraseOld = value == "1"; break;
            }
        }

        private static void TryNumber(string value, ref double target)
        {
            if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)
                && parsed > 0)
            {
                target = parsed;
            }
        }

        private static TypedValue Text(string value)
        {
            return new TypedValue((int)DxfCode.Text, value);
        }
    }
}
