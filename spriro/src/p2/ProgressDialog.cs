using System;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;
using ZwCadApp = ZwSoft.ZwCAD.ApplicationServices.Application;

namespace Spriro.P2
{
    // Modal "Processing..." dialog with a progress bar driven by polling, plus
    // a Cancel button that aborts the in-flight HTTP request.
    //
    // The HTTP call to the backend is synchronous and may take anywhere from
    // a couple of seconds to several minutes for large room sets. We run it
    // on a background Thread and poll its completion flag from a Forms.Timer
    // ticking on the UI thread (every 100 ms). The bar advances continuously
    // along a two-phase curve (fast 0→60% in 3 s, then slow asymptotic
    // 60→99% with τ = 120 s) so it never plateaus, and snaps to 100% the
    // moment the worker actually finishes. The label shows live elapsed time.
    //
    // Usage:
    //     var resp = ProgressDialog.RunWithPolling(
    //         "Requesting scenarios from backend",
    //         () => ApiClient.PostZwcadScenarios(req),
    //         onCancel: ApiClient.AbortCurrent);
    //
    // If the user clicks Cancel, the worker's HTTP request is aborted and
    // RunWithPolling throws OperationCanceledException — callers should catch
    // it and report cancellation to the user.
    public sealed class ProgressDialog : Form
    {
        private readonly System.Windows.Forms.Timer _timer;
        private readonly ProgressBar _bar;
        private readonly Label       _label;
        private readonly Button      _cancelBtn;
        private readonly DateTime    _start;
        private readonly Func<bool>  _isDone;
        private readonly Action      _onCancel;
        private readonly string      _baseTitle;

        // Set when the user clicks Cancel. Polled by RunWithPolling after the
        // dialog closes to decide whether to throw OperationCanceledException.
        public bool Cancelled { get; private set; }

        private ProgressDialog(string title, Func<bool> isDone, Action onCancel)
        {
            _baseTitle = title;
            _isDone    = isDone;
            _onCancel  = onCancel;
            _start     = DateTime.Now;

            Text            = "Sprinkler Plugin";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition   = FormStartPosition.CenterScreen;
            ControlBox      = false;
            MinimizeBox     = false;
            MaximizeBox     = false;
            ShowInTaskbar   = false;
            TopMost         = true;
            ClientSize      = new Size(400, 120);

            _label = new Label
            {
                Text     = title + "...",
                Location = new Point(12, 12),
                Size     = new Size(376, 18),
                AutoSize = false,
            };
            // Maximum = 1000 (0.1% resolution) so the bar visibly micro-ticks
            // every poll even when the modeled progress only inches forward
            // by a fraction of a percent. With Maximum=100 the int-quantised
            // value would freeze between polls and the dialog looks stuck.
            _bar = new ProgressBar
            {
                Style    = ProgressBarStyle.Continuous,
                Minimum  = 0,
                Maximum  = 1000,
                Value    = 0,
                Location = new Point(12, 38),
                Size     = new Size(376, 22),
            };
            _cancelBtn = new Button
            {
                Text     = "Cancel",
                Location = new Point(312, 78),
                Size     = new Size(76, 28),
            };
            _cancelBtn.Click += OnCancelClicked;
            CancelButton = _cancelBtn;   // makes Esc trigger Cancel

            Controls.Add(_label);
            Controls.Add(_bar);
            Controls.Add(_cancelBtn);

            _timer = new System.Windows.Forms.Timer { Interval = 100 };
            _timer.Tick += OnPoll;
        }

        private void OnCancelClicked(object sender, EventArgs e)
        {
            if (Cancelled) return;     // already pressed once
            Cancelled = true;
            _cancelBtn.Enabled = false;
            _label.Text = _baseTitle + "... cancelling";
            try { _onCancel?.Invoke(); } catch { /* swallow */ }
            // Don't Close() here — wait for the worker to actually finish
            // (it will throw a WebException from Abort()), so OnPoll's
            // _isDone() check fires and closes the dialog cleanly.
        }

        // Polled every 100ms on the UI thread. Updates the bar with a
        // heuristic curve (we don't know real progress) and closes the
        // dialog when the worker thread finishes.
        //
        // Curve is two-phase, tuned so the bar never plateaus even on
        // multi-minute requests:
        //   - phase 1 (0–3 s):  linear ramp 0% → 60%   (fast at the start)
        //   - phase 2 (>3 s):   asymptotic 60% → 99%   with τ = 120 s
        //                       (slow at the end — ~84% at 2 min, ~96% at 5 min)
        // The bar is capped at 99% so 100% only fires when the worker
        // actually finishes.
        private void OnPoll(object sender, EventArgs e)
        {
            if (_isDone())
            {
                _timer.Stop();
                _bar.Value  = _bar.Maximum;
                _label.Text = Cancelled
                    ? _baseTitle + "... cancelled"
                    : _baseTitle + "... done";
                Close();
                return;
            }

            if (Cancelled) return;     // freeze the bar while we wait for abort

            double secs = (DateTime.Now - _start).TotalSeconds;

            double pct;
            if (secs < 3.0)
                pct = 60.0 * (secs / 3.0);
            else
                pct = 60.0 + 39.0 * (1.0 - Math.Exp(-(secs - 3.0) / 120.0));

            // Scale to bar's 0–1000 range, clamp to monotonically-increasing,
            // and cap at 990 (99%) so 100% is reserved for actual completion.
            int target = (int)(pct * 10.0);
            if (target < _bar.Value) target = _bar.Value;
            if (target > 990)        target = 990;
            _bar.Value = target;

            int displayPct = target / 10;
            int mins       = (int)(secs / 60.0);
            int secsPart   = (int)secs % 60;
            _label.Text = string.Format("{0}... {1}%   elapsed {2:00}:{3:00}",
                _baseTitle, displayPct, mins, secsPart);
        }

        // Run `work` on a background thread, show this dialog, poll completion
        // every 100ms from the UI thread. If the user clicks Cancel, `onCancel`
        // is invoked (typically aborts the in-flight HTTP request) and this
        // method throws OperationCanceledException once the worker unwinds.
        // Any other exception from `work` is re-thrown.
        public static T RunWithPolling<T>(string title, Func<T> work, Action onCancel = null)
        {
            T         result = default(T);
            Exception err    = null;
            bool      done   = false;

            var thread = new Thread(() =>
            {
                try               { result = work(); }
                catch (Exception ex) { err = ex; }
                finally           { done = true; }
            })
            {
                IsBackground = true,
                Name         = "SprinklerPlugin.BackendCall",
            };

            bool cancelled;
            using (var dlg = new ProgressDialog(title, () => done, onCancel))
            {
                thread.Start();
                dlg._timer.Start();
                // Must use ZWCAD's modal-dialog host instead of Form.ShowDialog().
                // Showing a Forms dialog directly from a [CommandMethod] aborts
                // the ZWCAD session — the dialog's message pump conflicts with
                // the host's main window.
                ZwCadApp.ShowModalDialog(dlg);
                cancelled = dlg.Cancelled;
            }
            thread.Join();

            if (cancelled) throw new OperationCanceledException("Request cancelled by user.");
            if (err != null) throw err;
            return result;
        }
    }
}
