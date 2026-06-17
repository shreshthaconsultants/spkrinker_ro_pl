using System.Collections.Generic;
using Newtonsoft.Json;

namespace SprinklerPlugin
{
    // DTOs mirroring docs/json-contract.md. Points are [x, y] in mm.

    public class PlaceRequest
    {
        [JsonProperty("boundary")] public List<double[]> Boundary { get; set; }
        [JsonProperty("hazard")] public string Hazard { get; set; }

        // Degrees CCW: tilt of the building grid vs the X axis.
        [JsonProperty("rotation")] public double Rotation { get; set; }
    }

    public class PlaceResponse
    {
        [JsonProperty("points")] public List<double[]> Points { get; set; }
        [JsonProperty("spacing")] public double Spacing { get; set; }
        [JsonProperty("coverage_radius")] public double CoverageRadius { get; set; }
        [JsonProperty("count")] public int Count { get; set; }
    }

    public class ValidateRequest
    {
        [JsonProperty("boundary")] public List<double[]> Boundary { get; set; }
        [JsonProperty("points")] public List<double[]> Points { get; set; }
        [JsonProperty("hazard")] public string Hazard { get; set; }
    }

    public class RuleResult
    {
        [JsonProperty("rule")] public string Rule { get; set; }
        [JsonProperty("passed")] public bool Passed { get; set; }
        [JsonProperty("detail")] public string Detail { get; set; }
    }

    public class ValidateResponse
    {
        [JsonProperty("passed")] public bool Passed { get; set; }
        [JsonProperty("rules")] public List<RuleResult> Rules { get; set; }
        [JsonProperty("failing_heads")] public List<double[]> FailingHeads { get; set; }
    }

    public class RouteRequest
    {
        [JsonProperty("points")] public List<double[]> Points { get; set; }

        // Omitted when null: the backend then infers spacing from the heads.
        [JsonProperty("hazard", NullValueHandling = NullValueHandling.Ignore)]
        public string Hazard { get; set; }

        // Multiple shafts: heads are divided by nearest-shaft distance.
        [JsonProperty("risers", NullValueHandling = NullValueHandling.Ignore)]
        public List<double[]> Risers { get; set; }

        // Room polygon: heads outside it are ignored, pipes kept inside it.
        [JsonProperty("boundary", NullValueHandling = NullValueHandling.Ignore)]
        public List<double[]> Boundary { get; set; }

        // Double-line widths: the backend merges the pipe outlines at these.
        [JsonProperty("branch_width")] public double BranchWidth { get; set; }
        [JsonProperty("main_width")] public double MainWidth { get; set; }

        // Degrees CCW: tilt of the building grid vs the X axis.
        [JsonProperty("rotation")] public double Rotation { get; set; }
    }

    public class SegmentDto
    {
        // Flow direction is start -> end (upstream -> downstream).
        [JsonProperty("start")] public double[] Start { get; set; }
        [JsonProperty("end")] public double[] End { get; set; }
        [JsonProperty("kind")] public string Kind { get; set; }   // riser | main | branch
        [JsonProperty("length")] public double Length { get; set; }
        [JsonProperty("shaft")] public int Shaft { get; set; }    // index into risers
    }

    public class RouteGroupDto
    {
        [JsonProperty("riser")] public double[] Riser { get; set; }
        [JsonProperty("head_count")] public int HeadCount { get; set; }
        [JsonProperty("length")] public double Length { get; set; }
    }

    public class OutlineDto
    {
        // One closed ring of a shaft's merged pipe outline (clean junctions).
        [JsonProperty("shaft")] public int Shaft { get; set; }
        [JsonProperty("points")] public List<double[]> Points { get; set; }
    }

    public class RouteResponse
    {
        [JsonProperty("segments")] public List<SegmentDto> Segments { get; set; }
        [JsonProperty("outlines")] public List<OutlineDto> Outlines { get; set; }
        [JsonProperty("risers")] public List<double[]> Risers { get; set; }
        [JsonProperty("groups")] public List<RouteGroupDto> Groups { get; set; }
        [JsonProperty("total_length")] public double TotalLength { get; set; }
        [JsonProperty("skipped_heads")] public int SkippedHeads { get; set; }
    }

    public class ErrorResponse
    {
        [JsonProperty("error")] public string Error { get; set; }
        [JsonProperty("message")] public string Message { get; set; }
    }
}
