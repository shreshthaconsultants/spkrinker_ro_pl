using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace SprinklerPlugin
{
    public class BackendException : Exception
    {
        public BackendException(string message) : base(message) { }
    }

    /// <summary>
    /// Synchronous HTTP client for the FastAPI backend. Calls block on purpose:
    /// commands stay in the document execution context (no async-void
    /// re-entrancy inside ZWCAD).
    /// </summary>
    public static class BackendClient
    {
        public const string BaseUrl = "http://127.0.0.1:9000";

        private static readonly HttpClient Http = new HttpClient
        {
            BaseAddress = new Uri(BaseUrl),
            Timeout = TimeSpan.FromSeconds(120),  // headroom for very large drawings
        };

        public static PlaceResponse Place(List<double[]> boundary, string hazard, double rotation = 0.0)
        {
            return Post<PlaceResponse>("/place", new PlaceRequest
            {
                Boundary = boundary,
                Hazard = hazard,
                Rotation = rotation,
            });
        }

        public static ValidateResponse Validate(List<double[]> boundary, List<double[]> points, string hazard)
        {
            return Post<ValidateResponse>("/validate", new ValidateRequest { Boundary = boundary, Points = points, Hazard = hazard });
        }

        public static RouteResponse Route(List<double[]> points, List<double[]> risers,
            List<double[]> boundary, double branchWidth, double mainWidth,
            double rotation = 0.0, string hazard = null)
        {
            return Post<RouteResponse>("/route", new RouteRequest
            {
                Points = points,
                Risers = risers,
                Boundary = boundary,
                BranchWidth = branchWidth,
                MainWidth = mainWidth,
                Rotation = rotation,
                Hazard = hazard,
            });
        }

        public static RouteJointResponse RouteJoint(List<double[]> points, List<List<double[]>> rooms,
            List<double[]> corridor, List<double[]> risers, double branchWidth, double mainWidth,
            double headerOffset, double rotation = 0.0, string hazard = null, bool autoTilt = false)
        {
            return Post<RouteJointResponse>("/route-joint", new RouteJointRequest
            {
                Points = points,
                Rooms = rooms,
                Corridor = corridor,
                Risers = risers,
                BranchWidth = branchWidth,
                MainWidth = mainWidth,
                HeaderOffset = headerOffset,
                Rotation = rotation,
                Hazard = hazard,
                AutoTilt = autoTilt,
            });
        }

        private static T Post<T>(string path, object body)
        {
            string text;
            HttpResponseMessage resp;
            try
            {
                var json = JsonConvert.SerializeObject(body);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                resp = Http.PostAsync(path, content).GetAwaiter().GetResult();
                text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            }
            catch (HttpRequestException)
            {
                throw new BackendException(
                    "Backend not reachable at " + BaseUrl + " - start it with:  py -m uvicorn app.main:app --port 9000  (from the backend folder).");
            }
            catch (TaskCanceledException)
            {
                throw new BackendException("Backend request timed out after 120 s.");
            }

            if (!resp.IsSuccessStatusCode)
            {
                throw new BackendException("Backend error (HTTP " + (int)resp.StatusCode + "): " + ExtractMessage(text));
            }

            try
            {
                return JsonConvert.DeserializeObject<T>(text);
            }
            catch (JsonException)
            {
                throw new BackendException("Backend returned unparseable JSON for " + path + ".");
            }
        }

        private static string ExtractMessage(string body)
        {
            try
            {
                var err = JsonConvert.DeserializeObject<ErrorResponse>(body);
                if (err != null && !string.IsNullOrEmpty(err.Message)) return err.Message;
            }
            catch (JsonException) { /* fall through to raw body */ }
            return string.IsNullOrEmpty(body) ? "(empty response)" : body;
        }
    }
}
