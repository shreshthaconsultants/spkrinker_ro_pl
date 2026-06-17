using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

namespace SprinklerPlugin
{
    // Synchronous HTTP client for the FastAPI backend. The backend in
    // backend/main.py is fully synchronous, so callers (Commands.cs) just
    // invoke these from the [CommandMethod] thread.
    //
    // Two endpoints we use:
    //   GET  /api/health             -> HealthResponse
    //   POST /api/zwcad/scenarios    -> plain-text LISP S-expression
    //
    // No NuGet packages — only BCL (HttpWebRequest + JavaScriptSerializer).
    public static class ApiClient
    {
        public const string BaseUrl = "http://localhost:9001";

        // Tracks the currently-running HttpWebRequest so the UI can abort it
        // (e.g. the Cancel button on ProgressDialog calls AbortCurrent()).
        // volatile because writes happen on the worker thread and reads on the UI.
        private static volatile HttpWebRequest _currentRequest;

        public static void AbortCurrent()
        {
            var r = _currentRequest;
            if (r == null) return;
            try { r.Abort(); } catch { /* already aborted/disposed */ }
        }

        // ---- Endpoints ----

        public static HealthResponse GetHealth()
        {
            return GetJson<HealthResponse>(BaseUrl + "/api/health", timeoutMs: 5000);
        }

        // Returns scenario_id -> ScenarioPoints { Inside heads, Outside debug markers }.
        // Timeout is 10 min — GA over many rooms × 3 scenarios can run past
        // the default 100s, and the LSP plugin sets no timeout at all.
        public static Dictionary<int, ScenarioPoints> PostZwcadScenarios(ZwcadScenarioRequest req)
        {
            string lisp = PostJsonExpectText(
                BaseUrl + "/api/zwcad/scenarios",
                req,
                timeoutMs: 600000);
            return LispParser.ParseScenarios(lisp);
        }

        // ---- HTTP helpers ----

        private static TResp GetJson<TResp>(string url, int timeoutMs)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "GET";
            req.Accept = "application/json";
            req.Timeout = timeoutMs;

            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var reader = new StreamReader(resp.GetResponseStream()))
            {
                string json = reader.ReadToEnd();
                return new JavaScriptSerializer().Deserialize<TResp>(json);
            }
        }

        private static string PostJsonExpectText<TReq>(string url, TReq body, int timeoutMs)
        {
            var serializer = new JavaScriptSerializer();
            string jsonBody = serializer.Serialize(body);
            byte[] bodyBytes = Encoding.UTF8.GetBytes(jsonBody);

            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "POST";
            req.ContentType = "application/json";
            req.Accept = "text/plain";
            req.Timeout = timeoutMs;
            req.ReadWriteTimeout = timeoutMs;
            req.ContentLength = bodyBytes.Length;

            _currentRequest = req;
            try
            {
                using (var stream = req.GetRequestStream())
                {
                    stream.Write(bodyBytes, 0, bodyBytes.Length);
                }

                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream()))
                {
                    return reader.ReadToEnd();
                }
            }
            finally
            {
                _currentRequest = null;
            }
        }
    }
}
