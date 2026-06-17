using System.Collections.Generic;
using Newtonsoft.Json;

namespace SprinklerPlugin
{
    // DTOs for the joint-architecture endpoint (/route-joint).
    // Segments/outlines/groups reuse the full-mode DTOs: SegmentDto.Kind is
    // a plain string, so the joint kinds (riser|header|subheader|branch)
    // pass straight through.

    public class RouteJointRequest
    {
        [JsonProperty("points")] public List<double[]> Points { get; set; }
        [JsonProperty("rooms")] public List<List<double[]>> Rooms { get; set; }
        [JsonProperty("corridor")] public List<double[]> Corridor { get; set; }
        [JsonProperty("risers")] public List<double[]> Risers { get; set; }

        [JsonProperty("hazard", NullValueHandling = NullValueHandling.Ignore)]
        public string Hazard { get; set; }

        [JsonProperty("branch_width")] public double BranchWidth { get; set; }
        [JsonProperty("main_width")] public double MainWidth { get; set; }

        // The room sub-header sits this far BESIDE the sprinkler column.
        [JsonProperty("header_offset")] public double HeaderOffset { get; set; }

        // Degrees CCW: manual tilt override (rarely needed).
        [JsonProperty("rotation")] public double Rotation { get; set; }

        // Backend measures the grid angle automatically (globally from the
        // corridor heads and per room) - the user never types the tilt.
        [JsonProperty("auto_tilt")] public bool AutoTilt { get; set; }
    }

    public class RoomStatusDto
    {
        [JsonProperty("index")] public int Index { get; set; }
        [JsonProperty("head_count")] public int HeadCount { get; set; }
        [JsonProperty("status")] public string Status { get; set; }  // tapped | fallback | empty | skipped
        [JsonProperty("shaft")] public int Shaft { get; set; }       // -1 when not connected
    }

    public class RouteJointResponse
    {
        [JsonProperty("segments")] public List<SegmentDto> Segments { get; set; }
        [JsonProperty("outlines")] public List<OutlineDto> Outlines { get; set; }
        [JsonProperty("risers")] public List<double[]> Risers { get; set; }
        [JsonProperty("groups")] public List<RouteGroupDto> Groups { get; set; }
        [JsonProperty("rooms")] public List<RoomStatusDto> Rooms { get; set; }
        [JsonProperty("total_length")] public double TotalLength { get; set; }
        [JsonProperty("skipped_heads")] public int SkippedHeads { get; set; }
        [JsonProperty("skipped_rooms")] public int SkippedRooms { get; set; }
    }
}
