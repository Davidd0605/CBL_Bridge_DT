using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using UnityEngine;

//  Serialisable JSON types  (top-level, public)

[Serializable] public class BridgeNodeData { public int id; public string label; public float x, y, z; }
[Serializable] public class BridgeElementData { public int id; public string type; public int i, j; }
[Serializable] public class BridgeSupportData { public int node; }
[Serializable] public class BridgeSensorData { public string sensor_id; public int node; }
[Serializable]
public class BridgeFileData
{
    public BridgeNodeData[] nodes;
    public BridgeElementData[] elements;
    public BridgeSupportData[] supports;
    public BridgeSensorData[] deflection_sensor_points;
}

// MQTT message format: {"nodes": [{"id": 1, "x": 0.0, "y": -0.001, "z": -0.06}, ...]}
[Serializable] public class MqttNodeUpdate { public int id; public float x, y, z; }
[Serializable] public class MqttNodeMessage { public MqttNodeUpdate[] nodes; }

//  Internal representation of one bridge

internal class BridgeInstance
{
    public readonly string Label;            // "Real" or "Simulation"
    public readonly bool IsSimulation;
    public readonly GameObject Root;         // parent GameObject in the scene

    public readonly Dictionary<int, Vector3> BasePos = new();
    public readonly Dictionary<int, GameObject> NodeObjects = new();
    public readonly Dictionary<int, GameObject> BeamObjects = new();
    public readonly Dictionary<int, (int i, int j)> BeamEnds = new();
    public readonly ConcurrentQueue<MqttNodeMessage> UpdateQueue = new();

    public BridgeInstance(string label, bool isSim, GameObject root)
    {
        Label = label;
        IsSimulation = isSim;
        Root = root;
    }
}

//  Main MonoBehaviour

public class BridgeBuilder : MonoBehaviour
{
    [Header("Bridge Data")]
    public TextAsset bridgeJson;

    // -- MQTT ----------------------------------
    [Header("MQTT - Real Bridge")]
    [Tooltip("Root topic prefix for the physical bridge. Sub-topics: /nodes")]
    public string realTopicPrefix = "real";

    [Header("MQTT - Simulation Bridge")]
    [Tooltip("Root topic prefix for the simulation. Sub-topics: /nodes")]
    public string simTopicPrefix = "simulation";

    // -- Layout --------------------------------
    [Header("Layout")]
    [Tooltip("Offset applied to the simulation bridge root so the two bridges sit side-by-side.")]
    public Vector3 simBridgeOffset = new Vector3(0f, 0f, 30f);

    // -- Scale ---------------------------------
    [Header("Scale")]
    [Tooltip("Multiply all coordinates by this. 10 = 1 real metre -> 10 Unity units.")]
    public float worldScale = 10f;

    // -- Geometry ------------------------------
    [Header("Geometry")]
    public float beamRadius = 0.015f;
    public float nodeRadius = 0.03f;

    // -- Hover UI ------------------------------
    [Header("Hover UI")]
    public GameObject hoverCanvasPrefab;
    public Vector3 hoverUIOffset = new Vector3(0f, 0.1f, 0f);

    // -- Element colours -----------------------
    [Header("Element Colours")]
    public Color colChord = new Color(0.20f, 0.50f, 1.00f);
    public Color colVertical = new Color(0.20f, 0.80f, 0.40f);
    public Color colDiagonal = new Color(1.00f, 0.75f, 0.10f);
    public Color colCrossBeam = new Color(0.70f, 0.30f, 0.90f);
    public Color colBrace = new Color(0.60f, 0.60f, 0.60f);
    public Color colDeck = new Color(0.90f, 0.55f, 0.15f);
    public Color colDefault = new Color(0.50f, 0.50f, 0.50f);

    // -- Node colours --------------------------
    [Header("Node Colours")]
    public Color colNode = Color.white;
    public Color colSupport = Color.red;
    public Color colSensor = Color.cyan;

    // -- Simulation visual ---------------------
    [Header("Simulation Bridge Appearance")]
    [Tooltip("Alpha (0-1) applied to every material on the simulation bridge.")]
    [Range(0f, 1f)]
    public float simAlpha = 0.35f;

    [Tooltip("Tint multiplied over element colours on the simulation bridge. " +
             "Leave white to keep original hues at reduced opacity.")]
    public Color simTint = new Color(0.7f, 0.9f, 1.0f);  // cool blue-ish ghost

    // -- Stress colours ------------------------
    [Header("Stress Colours (live from MQTT)")]
    public Color colOk = new Color(0.10f, 0.90f, 0.40f);
    public Color colWarning = new Color(1.00f, 0.65f, 0.00f);
    public Color colOverloaded = new Color(0.95f, 0.10f, 0.10f);

    // -- Rendering -----------------------------
    [Header("Rendering")]
    [Tooltip("Optional base material. Must support transparency (e.g. URP/Lit with Surface = Transparent) " +
             "for the simulation bridge to appear ghost-like.")]
    public Material baseMaterial;

    // -- Runtime state -------------------------
    private BridgeInstance _real;
    private BridgeInstance _sim;


    //  Unity lifecycle

    void Start()
    {
        if (bridgeJson == null)
        {
            Debug.LogError("BridgeBuilder: assign a bridge JSON in the Inspector.");
            return;
        }

        // Create two child GameObjects so each bridge lives in its own local space
        var realRoot = new GameObject("Bridge_Real");
        realRoot.transform.SetParent(transform);
        realRoot.transform.localPosition = Vector3.zero;

        var simRoot = new GameObject("Bridge_Simulation");
        simRoot.transform.SetParent(transform);
        simRoot.transform.localPosition = simBridgeOffset;

        _real = new BridgeInstance("Real", false, realRoot);
        _sim = new BridgeInstance("Simulation", true, simRoot);

        Build(_real, bridgeJson.text);
        Build(_sim, bridgeJson.text);

        Subscribe(_real, realTopicPrefix);
        Subscribe(_sim, simTopicPrefix);
    }

    void Update()
    {
        // Drain queues for both bridges on the main thread
        DrainQueue(_real);
        DrainQueue(_sim);

        // R -> reset both bridges to rest positions
        if (Input.GetKeyDown(KeyCode.R))
        {
            ResetPositions(_real);
            ResetPositions(_sim);
            Debug.Log("[BridgeBuilder] Both bridges reset to base positions.");
        }
    }


    //  MQTT subscription

    private void Subscribe(BridgeInstance bridge, string topicPrefix)
    {
        var topic = $"{topicPrefix}/nodes";
        MQTTClient.Instance.Subscribe(topic, (t, payload) =>
        {
            try
            {
                var msg = JsonUtility.FromJson<MqttNodeMessage>(payload);
                if (msg?.nodes == null || msg.nodes.Length == 0)
                {
                    Debug.LogWarning($"[BridgeBuilder:{bridge.Label}] Empty or unparseable node message.");
                    return;
                }
                bridge.UpdateQueue.Enqueue(msg);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[BridgeBuilder:{bridge.Label}] Parse error: {ex.Message}");
            }
        });

        Debug.Log($"[BridgeBuilder:{bridge.Label}] Subscribed to {topic}");
    }


    //  Queue drain (main thread)

    private void DrainQueue(BridgeInstance bridge)
    {
        while (bridge.UpdateQueue.TryDequeue(out var msg))
            ApplyPositionUpdate(bridge, msg);
    }

    private void ApplyPositionUpdate(BridgeInstance bridge, MqttNodeMessage msg)
    {
        foreach (var n in msg.nodes)
        {
            if (!bridge.NodeObjects.TryGetValue(n.id, out var sphere))
            {
                Debug.LogWarning($"[BridgeBuilder:{bridge.Label}] Node id {n.id} not found.");
                continue;
            }
            sphere.transform.localPosition = new Vector3(n.x, n.y, n.z) * worldScale;
        }

        foreach (var kvp in bridge.BeamObjects)
        {
            var (ni, nj) = bridge.BeamEnds[kvp.Key];
            RepositionBeam(
                kvp.Value,
                bridge.NodeObjects[ni].transform.localPosition,
                bridge.NodeObjects[nj].transform.localPosition);
        }
    }


    //  Public API

    /// <summary>Apply utilization colouring to the real bridge.</summary>
    public void UpdateRealUtilizations(Dictionary<int, float> utilizations)
        => ApplyUtilizations(_real, utilizations);

    /// <summary>Apply utilization colouring to the simulation bridge.</summary>
    public void UpdateSimUtilizations(Dictionary<int, float> utilizations)
        => ApplyUtilizations(_sim, utilizations);

    private void ApplyUtilizations(BridgeInstance bridge, Dictionary<int, float> utilizations)
    {
        foreach (var kvp in utilizations)
        {
            if (!bridge.BeamObjects.TryGetValue(kvp.Key, out var beam)) continue;
            var col = kvp.Value > 1f ? colOverloaded
                    : kvp.Value > 0.75f ? colWarning
                    : colOk;
            if (bridge.IsSimulation) col = GhostColor(col);
            beam.GetComponent<Renderer>().material.color = col;
        }
    }


    //  Reset

    private void ResetPositions(BridgeInstance bridge)
    {
        foreach (var kvp in bridge.NodeObjects)
            kvp.Value.transform.localPosition = bridge.BasePos[kvp.Key];

        foreach (var kvp in bridge.BeamObjects)
        {
            var (ni, nj) = bridge.BeamEnds[kvp.Key];
            RepositionBeam(kvp.Value, bridge.BasePos[ni], bridge.BasePos[nj]);
        }
    }


    //  Build

    private void Build(BridgeInstance bridge, string json)
    {
        var data = JsonUtility.FromJson<BridgeFileData>(json);
        if (data == null) { Debug.LogError($"BridgeBuilder:{bridge.Label}: failed to parse JSON."); return; }

        var supportNodes = new HashSet<int>();
        if (data.supports != null)
            foreach (var s in data.supports) supportNodes.Add(s.node);

        var sensorNodes = new HashSet<int>();
        if (data.deflection_sensor_points != null)
            foreach (var s in data.deflection_sensor_points) sensorNodes.Add(s.node);

        // -- Nodes --
        foreach (var n in data.nodes)
        {
            var pos = new Vector3(n.x, n.y, n.z) * worldScale;
            bridge.BasePos[n.id] = pos;

            var sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            sphere.name = $"[{bridge.Label}] Node_{n.id}_{n.label}";
            sphere.transform.SetParent(bridge.Root.transform);
            sphere.transform.localPosition = pos;
            sphere.transform.localScale = Vector3.one * nodeRadius * 2f;

            var col = supportNodes.Contains(n.id) ? colSupport
                    : sensorNodes.Contains(n.id) ? colSensor
                    : colNode;

            var sc = sphere.GetComponent<Collider>();
            if (sc != null) sc.isTrigger = true;

            SetColor(sphere, bridge.IsSimulation ? GhostColor(col) : col, bridge.IsSimulation);
            bridge.NodeObjects[n.id] = sphere;

            AttachHoverable(sphere, $"[{bridge.Label}] Node {n.id} - {n.label}");
        }

        // -- Elements / beams --
        foreach (var e in data.elements)
        {
            if (!bridge.BasePos.TryGetValue(e.i, out var a) ||
                !bridge.BasePos.TryGetValue(e.j, out var b))
            {
                Debug.LogWarning($"[{bridge.Label}] Element {e.id}: node {e.i} or {e.j} not found.");
                continue;
            }

            var beam = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            beam.name = $"[{bridge.Label}] Elem_{e.id}_{e.type}";
            beam.transform.SetParent(bridge.Root.transform);
            RepositionBeam(beam, a, b);

            var bc = beam.GetComponent<Collider>();
            if (bc != null) bc.isTrigger = true;

            var col = ElementColor(e.type);
            SetColor(beam, bridge.IsSimulation ? GhostColor(col) : col, bridge.IsSimulation);
            bridge.BeamObjects[e.id] = beam;
            bridge.BeamEnds[e.id] = (e.i, e.j);

            AttachHoverable(beam, $"[{bridge.Label}] Element {e.id} - {e.type}");
        }

        Debug.Log($"[BridgeBuilder:{bridge.Label}] Built {data.nodes.Length} nodes, {data.elements.Length} elements.");
    }


    //  Helpers

    /// <summary>Returns a tinted, semi-transparent version of a colour for the ghost bridge.</summary>
    private Color GhostColor(Color src)
    {
        var tinted = new Color(src.r * simTint.r, src.g * simTint.g, src.b * simTint.b, simAlpha);
        return tinted;
    }

    private void AttachHoverable(GameObject go, string label)
    {
        if (hoverCanvasPrefab == null) return;
        var hoverable = go.AddComponent<Hoverable>();
        var ui = Instantiate(hoverCanvasPrefab, go.transform.position, Quaternion.identity);
        ui.transform.SetParent(transform);
        hoverable.uiOffset = hoverUIOffset;
        hoverable.Init(ui, label);
    }

    private void RepositionBeam(GameObject go, Vector3 a, Vector3 b)
    {
        var dir = b - a;
        var len = dir.magnitude;
        if (len < 1e-6f) return;
        go.transform.localPosition = (a + b) * 0.5f;
        go.transform.up = dir.normalized;
        go.transform.localScale = new Vector3(beamRadius * 2f, len * 0.5f, beamRadius * 2f);
    }

    private void SetColor(GameObject go, Color col, bool transparent)
    {
        go.GetComponent<Renderer>().material = MakeMaterial(col, transparent);
    }

    private Material MakeMaterial(Color col, bool transparent)
    {
        Material mat;
        if (baseMaterial != null)
        {
            mat = new Material(baseMaterial);
        }
        else
        {
            // Prefer a pipeline-specific Lit shader; fall back to Standard
            var shader = Shader.Find("Universal Render Pipeline/Lit")
                      ?? Shader.Find("HDRP/Lit")
                      ?? Shader.Find("Standard");
            mat = new Material(shader);
        }

        mat.color = col;
        if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", col);

        if (transparent)
        {
            // URP transparent surface type
            if (mat.HasProperty("_Surface"))
            {
                mat.SetFloat("_Surface", 1f);   // 1 = Transparent in URP
                mat.SetFloat("_Blend", 0f);     // 0 = Alpha blend
                mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
                mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
                mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                mat.SetInt("_ZWrite", 0);
                mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            }
            else
            {
                // Standard shader fallback
                mat.SetFloat("_Mode", 3f);   // Transparent
                mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
                mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                mat.SetInt("_ZWrite", 0);
                mat.DisableKeyword("_ALPHATEST_ON");
                mat.DisableKeyword("_ALPHABLEND_ON");
                mat.EnableKeyword("_ALPHAPREMULTIPLY_ON");
                mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            }
        }

        return mat;
    }

    private Color ElementColor(string type)
    {
        if (string.IsNullOrEmpty(type)) return colDefault;
        var t = type.ToLowerInvariant();
        if (t.Contains("chord")) return colChord;
        if (t.Contains("vertical")) return colVertical;
        if (t.Contains("diagonal")) return colDiagonal;
        if (t.Contains("cross_beam") || t.Contains("cross")) return colCrossBeam;
        if (t.Contains("brace")) return colBrace;
        if (t.Contains("floor") || t.Contains("stringer") || t.Contains("deck")) return colDeck;
        return colDefault;
    }
}