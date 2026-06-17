using System;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.Runtime;

// The ONE command class ZWCAD scans. Because a [CommandClass] attribute is
// present, ZWCAD registers commands only from the listed type(s); the three
// merged engines (Spriro.P1.Commands, Spriro.P2.Commands, Spriro.Routing.Commands)
// keep their original [CommandMethod] attributes but stay dormant, so their
// duplicate names (SPK, HELLOSPK, /AUTO-SPRINKLER, ...) never collide.
[assembly: CommandClass(typeof(Spriro.SpriroCommands))]
[assembly: ExtensionApplication(typeof(Spriro.SpriroApp))]

namespace Spriro
{
    /// <summary>Greets the user and lists the three commands on NETLOAD.</summary>
    public class SpriroApp : IExtensionApplication
    {
        public void Initialize()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            doc?.Editor.WriteMessage(
                "\nspriro loaded - one plugin, three engines (backend URLs from " + SpriroConfig.FileName + "):" +
                "\n  -routing       route existing sprinklers   -> " + SpriroConfig.RoutingBaseUrl +
                "\n  -sprinkler_p1  place sprinklers, v1 picker  -> " + SpriroConfig.P1BaseUrl +
                "\n  -sprinkler_p2  place sprinklers, v2 model   -> " + SpriroConfig.P2BaseUrl +
                "\n  config: " + SpriroConfig.Source + "\n");
        }

        public void Terminate() { }
    }

    /// <summary>
    /// The single command surface of the merged plugin. Each command delegates
    /// to the original per-version command class, now living in its own
    /// namespace. The version commands read the active document themselves, so
    /// these wrappers just construct the class and call the entry method.
    /// </summary>
    public class SpriroCommands
    {
        // -routing -> R1.0 SPKROUTE: route sprinklers that already exist in the
        // drawing (Rooms = rooms + corridor header, Open = one open space).
        // For full design (place + validate + route) use Spriro.Routing.Commands.SpkAuto.
        [CommandMethod("-routing")]
        public void Routing()
        {
            Run("-routing  (R1.0 routing, backend :9000)",
                () => new global::Spriro.Routing.Commands().SpkRoute());
        }

        // -sprinkler_p1 -> v1 /AUTO-SPRINKLER: the scenario-picker placement flow.
        [CommandMethod("-sprinkler_p1")]
        public void SprinklerP1()
        {
            Run("-sprinkler_p1  (v1 placement, backend :9001)",
                () => new global::Spriro.P1.Commands().AutoSprinkler());
        }

        // -sprinkler_p2 -> v2 AUTOSPRINKLER2: the universal one-shot placement
        // (classify -> place -> verify -> autofix -> minimise -> GA). For v2's
        // scenario picker instead, call Spriro.P2.Commands.AutoSprinkler.
        [CommandMethod("-sprinkler_p2")]
        public void SprinklerP2()
        {
            Run("-sprinkler_p2  (v2 universal placement, backend :9002)",
                () => new global::Spriro.P2.Commands().AutoSprinkler2());
        }

        private static void Run(string label, Action body)
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            doc?.Editor.WriteMessage("\n[spriro] " + label + "\n");
            try
            {
                body();
            }
            catch (System.Exception ex)
            {
                // The version flows handle their own backend/geometry errors;
                // this is just a backstop so an unexpected throw can't crash ZWCAD.
                doc?.Editor.WriteMessage("\n[spriro] command failed: " + ex.Message + "\n");
            }
        }
    }
}
