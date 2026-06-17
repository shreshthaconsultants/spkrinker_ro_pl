using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;
using ZwCadApp = ZwSoft.ZwCAD.ApplicationServices.Application;

namespace SprinklerPlugin
{
    // End-of-session "thank you" summary shown after the user exits the
    // AUTOSPRINKLER scenario picker. Displays:
    //   - total number of sprinklers placed (last scenario)
    //   - the top 10 rooms by floor area (or all rooms if fewer than 10),
    //     each with its area in m² and how many of the placed heads fall
    //     inside it.
    //
    // Modal — uses ZWCAD's ShowModalDialog so the dialog cooperates with
    // ZWCAD's main message pump (Form.ShowDialog from a [CommandMethod]
    // can crash the host).
    public sealed class SummaryDialog : Form
    {
        public sealed class RoomRow
        {
            public double AreaSquareMeters { get; set; }
            public int    HeadsInRoom      { get; set; }
        }

        private SummaryDialog(int totalSprinklers, int scenarioId,
                              int totalRoomCount, List<RoomRow> rows)
        {
            Text            = "Sprinkler Plugin — Summary";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition   = FormStartPosition.CenterScreen;
            ControlBox      = false;
            MinimizeBox     = false;
            MaximizeBox     = false;
            ShowInTaskbar   = false;
            TopMost         = true;
            ClientSize      = new Size(440, 380);

            var thanks = new Label
            {
                Text     = "Thank you for using the Sprinkler Plugin!",
                Font     = new Font(SystemFonts.MessageBoxFont, FontStyle.Bold),
                Location = new Point(12, 12),
                Size     = new Size(416, 22),
            };
            var totalLbl = new Label
            {
                Text     = string.Format(
                    "Total sprinklers placed: {0}   (scenario {1})",
                    totalSprinklers, scenarioId),
                Location = new Point(12, 38),
                Size     = new Size(416, 18),
            };

            string roomHeading = totalRoomCount > 10
                ? string.Format("Top 10 rooms by area (of {0} total):", totalRoomCount)
                : string.Format("All {0} room(s) by area:", totalRoomCount);
            var subLbl = new Label
            {
                Text     = roomHeading,
                Location = new Point(12, 64),
                Size     = new Size(416, 18),
            };

            var lv = new ListView
            {
                Location      = new Point(12, 88),
                Size          = new Size(416, 240),
                View          = View.Details,
                FullRowSelect = true,
                GridLines     = true,
                MultiSelect   = false,
                HeaderStyle   = ColumnHeaderStyle.Nonclickable,
            };
            lv.Columns.Add("#",            36);
            lv.Columns.Add("Area (m²)",   140);
            lv.Columns.Add("Heads",        80);
            lv.Columns.Add("Density (m²/head)", 140);

            int rank = 1;
            foreach (var r in rows)
            {
                var item = new ListViewItem(rank.ToString());
                item.SubItems.Add(r.AreaSquareMeters.ToString("F1"));
                item.SubItems.Add(r.HeadsInRoom.ToString());
                string density = r.HeadsInRoom > 0
                    ? (r.AreaSquareMeters / r.HeadsInRoom).ToString("F1")
                    : "—";
                item.SubItems.Add(density);
                lv.Items.Add(item);
                rank++;
            }

            var okBtn = new Button
            {
                Text         = "OK",
                DialogResult = DialogResult.OK,
                Location     = new Point(352, 340),
                Size         = new Size(76, 28),
            };
            okBtn.Click += (s, e) => Close();
            AcceptButton = okBtn;
            CancelButton = okBtn;

            Controls.Add(thanks);
            Controls.Add(totalLbl);
            Controls.Add(subLbl);
            Controls.Add(lv);
            Controls.Add(okBtn);
        }

        // Public entry point: takes the room polylines and the points of
        // the last successfully placed scenario, builds the per-room rows,
        // sorts by area descending, takes the top 10, and shows the dialog.
        public static void Show(
            List<List<double[]>> roomPolys,
            List<double[]>       drawnPts,
            int                  scenarioId)
        {
            if (roomPolys == null) roomPolys = new List<List<double[]>>();
            if (drawnPts  == null) drawnPts  = new List<double[]>();

            var rows = new List<RoomRow>(roomPolys.Count);
            foreach (var poly in roomPolys)
            {
                if (poly == null || poly.Count < 3) continue;
                double areaMm2 = ShoelaceArea(poly);
                int heads = 0;
                foreach (var p in drawnPts)
                {
                    if (p == null || p.Length < 2) continue;
                    if (PointInPoly(p[0], p[1], poly)) heads++;
                }
                rows.Add(new RoomRow
                {
                    AreaSquareMeters = areaMm2 / 1_000_000.0,   // mm² → m²
                    HeadsInRoom      = heads,
                });
            }

            int totalRooms = rows.Count;
            var topRows = rows
                .OrderByDescending(r => r.AreaSquareMeters)
                .Take(10)
                .ToList();

            using (var dlg = new SummaryDialog(
                drawnPts.Count, scenarioId, totalRooms, topRows))
            {
                ZwCadApp.ShowModalDialog(dlg);
            }
        }

        // Number of UNIQUE vertices in a polygon — strips a duplicate
        // closing vertex if present (the LISP request payload doesn't
        // pre-close, but defensive for either form).
        private static int UniqueVertexCount(List<double[]> poly)
        {
            int n = poly.Count;
            if (n < 2) return n;
            if (poly[0][0] == poly[n - 1][0] && poly[0][1] == poly[n - 1][1])
                return n - 1;
            return n;
        }

        // Shoelace formula. Treats the polygon as cyclic so it works for
        // both open and explicitly-closed input. Returns area in
        // input-units squared (mm² for our DXF data).
        private static double ShoelaceArea(List<double[]> poly)
        {
            int n = UniqueVertexCount(poly);
            if (n < 3) return 0.0;
            double a = 0.0;
            for (int i = 0; i < n; i++)
            {
                int j = (i + 1) % n;
                a += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1];
            }
            return Math.Abs(a) / 2.0;
        }

        // Ray-casting point-in-polygon. Treats the polygon as cyclic so it
        // works for both open and explicitly-closed input. Mirrors
        // backend/geometry.point_in_poly's behaviour.
        private static bool PointInPoly(double x, double y, List<double[]> poly)
        {
            int n = UniqueVertexCount(poly);
            if (n < 3) return false;
            bool inside = false;
            int j = n - 1;
            for (int i = 0; i < n; i++)
            {
                double xi = poly[i][0], yi = poly[i][1];
                double xj = poly[j][0], yj = poly[j][1];
                if ((yi > y) != (yj > y))
                {
                    double dy = yj - yi;
                    if (dy != 0.0)
                    {
                        double xCross = (xj - xi) * (y - yi) / dy + xi;
                        if (x < xCross) inside = !inside;
                    }
                }
                j = i;
            }
            return inside;
        }
    }
}
