using System;
using System.IO;
using System.Reflection;
using Newtonsoft.Json;

namespace Spriro
{
    /// <summary>
    /// Resolves the three backend base URLs from a JSON config file shipped next
    /// to Spriro.dll, so the server address can be changed WITHOUT rebuilding the
    /// plugin. Edit spriro.config.json, restart ZWCAD (or re-NETLOAD), done.
    ///
    /// spriro.config.json (keys are optional; missing ones use the defaults):
    ///   {
    ///     "host": "146.190.72.89",
    ///     "routing_port": 9000,
    ///     "p1_port": 9001,
    ///     "p2_port": 9002
    ///   }
    /// Full per-engine overrides also work: "routing_url" / "p1_url" / "p2_url".
    ///
    /// Lookup order for the file:
    ///   1. the SPRIRO_CONFIG environment variable (full path), if set
    ///   2. spriro.config.json next to Spriro.dll
    ///   3. spriro.config.json in the current directory
    /// If none is found / parseable, it falls back to localhost on 9000/9001/9002.
    /// </summary>
    public static class SpriroConfig
    {
        public const string FileName = "spriro.config.json";

        public static string RoutingBaseUrl { get; }
        public static string P1BaseUrl { get; }
        public static string P2BaseUrl { get; }

        /// <summary>Where the values came from: the config path, or a "defaults" note.</summary>
        public static string Source { get; }

        static SpriroConfig()
        {
            string host = "127.0.0.1";
            int routingPort = 9000, p1Port = 9001, p2Port = 9002;
            string routingUrl = null, p1Url = null, p2Url = null;
            string source = "defaults (no config file found; using localhost)";

            try
            {
                string path = ResolvePath();
                if (path != null && File.Exists(path))
                {
                    var cfg = JsonConvert.DeserializeObject<ConfigFile>(File.ReadAllText(path));
                    if (cfg != null)
                    {
                        if (!string.IsNullOrWhiteSpace(cfg.Host)) host = cfg.Host.Trim();
                        if (cfg.RoutingPort > 0) routingPort = cfg.RoutingPort;
                        if (cfg.P1Port > 0) p1Port = cfg.P1Port;
                        if (cfg.P2Port > 0) p2Port = cfg.P2Port;
                        routingUrl = Clean(cfg.RoutingUrl);
                        p1Url = Clean(cfg.P1Url);
                        p2Url = Clean(cfg.P2Url);
                        source = path;
                    }
                }
            }
            catch (Exception ex)
            {
                source = "defaults (config error: " + ex.Message + ")";
            }

            RoutingBaseUrl = routingUrl ?? ("http://" + host + ":" + routingPort);
            P1BaseUrl = p1Url ?? ("http://" + host + ":" + p1Port);
            P2BaseUrl = p2Url ?? ("http://" + host + ":" + p2Port);
            Source = source;
        }

        private static string Clean(string s)
        {
            s = s == null ? null : s.Trim();
            return string.IsNullOrEmpty(s) ? null : s.TrimEnd('/');
        }

        private static string ResolvePath()
        {
            string env = Environment.GetEnvironmentVariable("SPRIRO_CONFIG");
            if (!string.IsNullOrWhiteSpace(env)) return env.Trim();

            try
            {
                string dll = Assembly.GetExecutingAssembly().Location;
                if (!string.IsNullOrEmpty(dll))
                {
                    string dir = Path.GetDirectoryName(dll);
                    if (!string.IsNullOrEmpty(dir))
                        return Path.Combine(dir, FileName);
                }
            }
            catch { /* fall through to current directory */ }

            return Path.GetFullPath(FileName);
        }

        private class ConfigFile
        {
            [JsonProperty("host")] public string Host { get; set; }
            [JsonProperty("routing_port")] public int RoutingPort { get; set; }
            [JsonProperty("p1_port")] public int P1Port { get; set; }
            [JsonProperty("p2_port")] public int P2Port { get; set; }
            [JsonProperty("routing_url")] public string RoutingUrl { get; set; }
            [JsonProperty("p1_url")] public string P1Url { get; set; }
            [JsonProperty("p2_url")] public string P2Url { get; set; }
        }
    }
}
