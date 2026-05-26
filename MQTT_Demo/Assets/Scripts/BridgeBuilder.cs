using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using UnityEngine;

[Serializable] public class BridgeNodeData { public int id; public string label; public float x, y, z; }
[Serializable] public class BridgeElementData { public int id; public string type; public int i, j; }
[Serializable] public class BridgeSupportData { public int node; }
[Serializable] public class BridgeSensorData { public string sensor_id; public int node; }

[Serializable]
public class BridgeGeometryPayload
{
    public string type;
    public string bridge_name;
    public BridgeNodeData[] nodes;
    public BridgeElementData[] elements;
    public BridgeSupportData[] supports;
    public BridgeSensorData[] deflection_sensor_points;
}

[Serializable]
public class BridgeStatePayload
{
    public string type;
    public bool analysis_completed;
    public float live_load_n;
    public float self_weight_n;
    public float visual_defo_scale;

    public int[] node_ids;
    public float[] disp_x;
    public float[] disp_y;
    public float[] disp_z;

    public int[] element_ids;
    public float[] utilization;
    public float[] axial_strain;
    public float[] bending_strain;
    public float[] combined_strain;

    public SensorReading[] sensor_readings;
}

[Serializable]
public class SensorReading
{
    public string sensor_id;
    public int node;
    public float live_uy_m;
    public float total_uy_m;
}

internal class BridgeInstance
{
    public readonly string Label;
    public readonly bool IsSimulation;
    public readonly GameObject Root;

    public readonly Dictionary<int, Vector3> BasePos = new();
    public readonly Dictionary<int, GameObject> NodeObjects = new();
    public readonly Dictionary<int, GameObject> BeamObjects = new();
    public readonly Dictionary<int, (int i, int j)> BeamEnds = new();

    public readonly Dictionary<int, string> BeamTypes = new();
    public readonly Dictionary<int, float> BeamUtilization = new();

    public readonly ConcurrentQueue<BridgeStatePayload> StateQueue = new();
    public readonly ConcurrentQueue<BridgeGeometryPayload> GeometryQueue = new();

    public BridgeInstance(string label, bool isSim, GameObject root)
    {
        Label = label;
        IsSimulation = isSim;
        Root = root;
    }
}

public class BridgeBuilder : MonoBehaviour
{
    public enum BridgeRenderMode { ComponentColors, StressAnalysis }

    public static BridgeBuilder Instance { get; private set; }

    [Header("Bridge Data (fallback if MQTT geometry not received)")]
    public TextAsset bridgeJsonFallback;

    [Header("MQTT Topics — Real Bridge")]
    public string topicGeometryReal = "cbl/bridge/real/geometry";
    public string topicStateReal = "cbl/bridge/real/state";

    [Header("MQTT Topics — Simulation Bridge")]
    public string topicGeometrySim = "cbl/bridge/sim/geometry";
    public string topicStateSim = "cbl/bridge/sim/state";

    [Header("Layout")]
    public Vector3 simBridgeOffset = new Vector3(0f, 0f, 30f);

    [Header("Scale & Exaggeration")]
    public float worldScale = 10f;
    public float manualVisualBoost = 500f;

    [Header("Geometry")]
    public float beamRadius = 0.015f;
    public float nodeRadius = 0.03f;

    [Header("Hover UI")]
    public GameObject hoverCanvasPrefab;
    public Vector3 hoverUIOffset = new Vector3(0f, 0.1f, 0f);

    [Header("Element Colours")]
    public Color colChord = new Color(0.20f, 0.50f, 1.00f);
    public Color colVertical = new Color(0.20f, 0.80f, 0.40f);
    public Color colDiagonal = new Color(1.00f, 0.75f, 0.10f);
    public Color colCrossBeam = new Color(0.70f, 0.30f, 0.90f);
    public Color colBrace = new Color(0.60f, 0.60f, 0.60f);
    public Color colDeck = new Color(0.90f, 0.55f, 0.15f);
    public Color colDefault = new Color(0.50f, 0.50f, 0.50f);

    [Header("Node Colours")]
    public Color colNode = Color.white;
    public Color colSupport = Color.red;
    public Color colSensor = Color.cyan;

    [Header("Simulation Bridge Appearance")]
    [Range(0f, 1f)] public float simAlpha = 0.35f;
    public Color simTint = new Color(0.7f, 0.9f, 1.0f);

    [Header("Stress Gradient Colors")]
    public Color colOk = new Color(0.10f, 0.90f, 0.40f);
    public Color colWarning = new Color(1.00f, 0.65f, 0.00f);
    public Color colOverloaded = new Color(0.95f, 0.10f, 0.10f);

    [Header("Rendering")]
    public Material baseMaterial;

    private BridgeInstance _real;
    private BridgeInstance _sim;
    private bool _builtFromMqtt = false;

    private bool _showRealBridge = true;
    private bool _showSimBridge = true;
    private bool _showPopupUI = true;
    private BridgeRenderMode _currentRenderMode = BridgeRenderMode.StressAnalysis;

    private List<GameObject> _instantiatedPopups = new List<GameObject>();

    void Awake()
    {
        if (Instance == null) Instance = this;
        else Destroy(gameObject);
    }

    void Start()
    {
        var realRoot = new GameObject("Bridge_Real");
        realRoot.transform.SetParent(transform);
        realRoot.transform.localPosition = Vector3.zero;

        var simRoot = new GameObject("Bridge_Simulation");
        simRoot.transform.SetParent(transform);
        simRoot.transform.localPosition = simBridgeOffset;

        _real = new BridgeInstance("Real", false, realRoot);
        _sim = new BridgeInstance("Simulation", true, simRoot);

        if (bridgeJsonFallback != null)
        {
            var geo = JsonUtility.FromJson<BridgeGeometryPayload>(bridgeJsonFallback.text);
            if (geo?.nodes != null)
            {
                Build(_real, geo);
                Build(_sim, geo);
            }
        }

        SubscribeGeometry(_real, topicGeometryReal);
        SubscribeGeometry(_sim, topicGeometrySim);
        SubscribeState(_real, topicStateReal);
        SubscribeState(_sim, topicStateSim);
    }

    void Update()
    {
        DrainGeometryQueue(_real);
        DrainGeometryQueue(_sim);

        if (!_builtFromMqtt && bridgeJsonFallback == null) return;

        DrainStateQueue(_real);
        DrainStateQueue(_sim);

        if (Input.GetKeyDown(KeyCode.R))
        {
            ResetPositions(_real);
            ResetPositions(_sim);
        }
    }

    public void ToggleRealBridge()
    {
        _showRealBridge = !_showRealBridge;
        if (_real?.Root != null) _real.Root.SetActive(_showRealBridge);
    }

    public void ToggleSimulationBridge()
    {
        _showSimBridge = !_showSimBridge;
        if (_sim?.Root != null) _sim.Root.SetActive(_showSimBridge);
    }

    public void TogglePopupUI()
    {
        // Toggle our master tracking state
        _showPopupUI = !_showPopupUI;

        // Tell Hoverable whether popups are allowed globally right now
        Hoverable.SetGlobalVisibility(_showPopupUI);

        // If the button turned popups OFF, clean the screen instantly
        if (!_showPopupUI)
        {
            Hoverable.HideAll();
        }
    }

    public void ToggleColorMode()
    {
        if (_currentRenderMode == BridgeRenderMode.StressAnalysis)
            _currentRenderMode = BridgeRenderMode.ComponentColors;
        else
            _currentRenderMode = BridgeRenderMode.StressAnalysis;

        RefreshBridgeColors(_real);
        RefreshBridgeColors(_sim);
    }

    private void RefreshBridgeColors(BridgeInstance bridge)
    {
        foreach (var kvp in bridge.BeamObjects)
        {
            int id = kvp.Key;
            GameObject beam = kvp.Value;
            if (beam == null) continue;

            Color targetColor = GetBeamColorForCurrentMode(bridge, id);

            Renderer rend = beam.GetComponent<Renderer>();
            if (rend != null)
            {
                rend.material.color = targetColor;
                if (rend.material.HasProperty("_BaseColor"))
                    rend.material.SetColor("_BaseColor", targetColor);
            }
        }
    }

    private Color GetBeamColorForCurrentMode(BridgeInstance bridge, int elementId)
    {
        Color baseColor;

        if (_currentRenderMode == BridgeRenderMode.ComponentColors)
        {
            bridge.BeamTypes.TryGetValue(elementId, out string type);
            baseColor = ElementColor(type);
        }
        else
        {
            bridge.BeamUtilization.TryGetValue(elementId, out float u);
            if (u <= 0.5f)
                baseColor = Color.Lerp(colOk, colWarning, u / 0.5f);
            else if (u <= 1.0f)
                baseColor = Color.Lerp(colWarning, colOverloaded, (u - 0.5f) / 0.5f);
            else
                baseColor = colOverloaded;
        }

        return bridge.IsSimulation ? GhostColor(baseColor) : baseColor;
    }

    private void SubscribeGeometry(BridgeInstance bridge, string topic)
    {
        MQTTClient.Instance.Subscribe(topic, (t, payload) =>
        {
            try
            {
                var geo = JsonUtility.FromJson<BridgeGeometryPayload>(payload);
                if (geo?.nodes == null || geo.nodes.Length == 0) return;
                bridge.GeometryQueue.Enqueue(geo);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[BridgeBuilder:{bridge.Label}] Geometry parse error: {ex.Message}");
            }
        });
    }

    private void SubscribeState(BridgeInstance bridge, string topic)
    {
        MQTTClient.Instance.Subscribe(topic, (t, payload) =>
        {
            try
            {
                var state = JsonUtility.FromJson<BridgeStatePayload>(payload);
                if (state == null || state.node_ids == null) return;
                bridge.StateQueue.Enqueue(state);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[BridgeBuilder:{bridge.Label}] State parse error: {ex.Message}");
            }
        });
    }

    private void DrainGeometryQueue(BridgeInstance bridge)
    {
        BridgeGeometryPayload latest = null;
        while (bridge.GeometryQueue.TryDequeue(out var msg))
            latest = msg;

        if (latest == null) return;

        foreach (var go in bridge.NodeObjects.Values) if (go != null) Destroy(go);
        foreach (var go in bridge.BeamObjects.Values) if (go != null) Destroy(go);

        bridge.NodeObjects.Clear();
        bridge.BeamObjects.Clear();
        bridge.BeamEnds.Clear();
        bridge.BasePos.Clear();
        bridge.BeamTypes.Clear();
        bridge.BeamUtilization.Clear();

        Build(bridge, latest);
        _builtFromMqtt = true;
    }

    private void DrainStateQueue(BridgeInstance bridge)
    {
        if (bridge.NodeObjects.Count == 0 || bridge.BeamObjects.Count == 0) return;

        BridgeStatePayload latest = null;
        while (bridge.StateQueue.TryDequeue(out var msg))
            latest = msg;

        if (latest == null) return;
        ApplyState(bridge, latest);
    }

    private void ApplyState(BridgeInstance bridge, BridgeStatePayload state)
    {
        var sensorMap = new Dictionary<int, SensorReading>();
        if (state.sensor_readings != null)
        {
            foreach (var s in state.sensor_readings)
                sensorMap[s.node] = s;
        }

        if (state.node_ids != null)
        {
            float factorBoost = (state.visual_defo_scale > 0.05f) ? state.visual_defo_scale : manualVisualBoost;

            for (int k = 0; k < state.node_ids.Length; k++)
            {
                int id = state.node_ids[k];
                if (!bridge.NodeObjects.TryGetValue(id, out var sphere)) continue;
                if (!bridge.BasePos.TryGetValue(id, out var basePos)) continue;

                float dx = state.disp_x != null && k < state.disp_x.Length ? state.disp_x[k] : 0f;
                float dy = state.disp_y != null && k < state.disp_y.Length ? state.disp_y[k] : 0f;
                float dz = state.disp_z != null && k < state.disp_z.Length ? state.disp_z[k] : 0f;

                Vector3 disp = new Vector3(dx, dy, dz) * worldScale * factorBoost;
                sphere.transform.localPosition = basePos + disp;

                var hover = sphere.GetComponent<Hoverable>();
                if (hover != null)
                {
                    string telemetryText = $"<color=#AAAAAA>Disp X:</color> {dx * 1000f:F1} mm\n" +
                                           $"<color=#AAAAAA>Disp Y:</color> {dy * 1000f:F1} mm\n" +
                                           $"<color=#AAAAAA>Disp Z:</color> {dz * 1000f:F1} mm";

                    if (sensorMap.TryGetValue(id, out var reading))
                    {
                        telemetryText += $"\n<color=#00FFFF>Sensor: {reading.sensor_id}</color>\n" +
                                         $"Live Uy: {reading.live_uy_m * 1000f:F1} mm\n" +
                                         $"Total Uy: {reading.total_uy_m * 1000f:F1} mm";
                    }
                    hover.UpdateLiveTelemetry(telemetryText);
                }
            }
        }

        foreach (var kvp in bridge.BeamObjects)
        {
            var (ni, nj) = bridge.BeamEnds[kvp.Key];
            if (!bridge.NodeObjects.TryGetValue(ni, out var goI)) continue;
            if (!bridge.NodeObjects.TryGetValue(nj, out var goJ)) continue;

            RepositionBeam(kvp.Value, goI.transform.localPosition, goJ.transform.localPosition);
        }

        if (state.element_ids != null && state.utilization != null)
        {
            for (int k = 0; k < state.element_ids.Length; k++)
            {
                int id = state.element_ids[k];
                float u = k < state.utilization.Length ? state.utilization[k] : 0f;

                bridge.BeamUtilization[id] = u;

                if (!bridge.BeamObjects.TryGetValue(id, out var beam)) continue;

                float axial = state.axial_strain != null && k < state.axial_strain.Length ? state.axial_strain[k] : 0f;
                float bending = state.bending_strain != null && k < state.bending_strain.Length ? state.bending_strain[k] : 0f;
                float combined = state.combined_strain != null && k < state.combined_strain.Length ? state.combined_strain[k] : 0f;

                Color col = GetBeamColorForCurrentMode(bridge, id);

                Renderer rend = beam.GetComponent<Renderer>();
                if (rend != null)
                {
                    rend.material.color = col;
                    if (rend.material.HasProperty("_BaseColor"))
                        rend.material.SetColor("_BaseColor", col);
                }

                var hover = beam.GetComponent<Hoverable>();
                if (hover != null)
                {
                    string telemetryText = $"<color=#FFDD55>Utilization:</color> {u * 100.0f:F1}%\n" +
                                           $"<color=#AAAAAA>Axial Strain:</color> {axial:E3}\n" +
                                           $"<color=#AAAAAA>Bending Strain:</color> {bending:E3}\n" +
                                           $"<color=#AAAAAA>Combined:</color> {combined:E3}";
                    hover.UpdateLiveTelemetry(telemetryText);
                }
            }
        }
    }

    private void Build(BridgeInstance bridge, BridgeGeometryPayload geo)
    {
        var supportNodes = new HashSet<int>();
        if (geo.supports != null)
            foreach (var s in geo.supports) supportNodes.Add(s.node);

        var sensorNodes = new HashSet<int>();
        if (geo.deflection_sensor_points != null)
            foreach (var s in geo.deflection_sensor_points) sensorNodes.Add(s.node);

        foreach (var n in geo.nodes)
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

            string baseMeta = $"<b>Node {n.id}</b> ({n.label})";
            AttachHoverable(sphere, baseMeta);
        }

        foreach (var e in geo.elements)
        {
            if (!bridge.BasePos.TryGetValue(e.i, out var a) ||
                !bridge.BasePos.TryGetValue(e.j, out var b))
                continue;

            bridge.BeamTypes[e.id] = e.type;
            bridge.BeamUtilization[e.id] = 0f;

            var beam = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            beam.name = $"[{bridge.Label}] Elem_{e.id}_{e.type}";
            beam.transform.SetParent(bridge.Root.transform);
            RepositionBeam(beam, a, b);

            var bc = beam.GetComponent<Collider>();
            if (bc != null) bc.isTrigger = true;

            Color col = GetBeamColorForCurrentMode(bridge, e.id);
            SetColor(beam, col, bridge.IsSimulation);
            bridge.BeamObjects[e.id] = beam;
            bridge.BeamEnds[e.id] = (e.i, e.j);

            string baseMeta = $"<b>Element {e.id}</b>\nType: {e.type}\nNodes: {e.i} <-> {e.j}";
            AttachHoverable(beam, baseMeta);
        }
    }

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

    private void RepositionBeam(GameObject go, Vector3 a, Vector3 b)
    {
        var dir = b - a;
        var len = dir.magnitude;
        if (len < 1e-6f) return;
        go.transform.localPosition = (a + b) * 0.5f;
        go.transform.up = dir.normalized;
        go.transform.localScale = new Vector3(beamRadius * 2f, len * 0.5f, beamRadius * 2f);
    }

    private Color GhostColor(Color src) =>
        new Color(src.r * simTint.r, src.g * simTint.g, src.b * simTint.b, simAlpha);

    private void SetColor(GameObject go, Color col, bool transparent) =>
        go.GetComponent<Renderer>().material = MakeMaterial(col, transparent);

    private Material MakeMaterial(Color col, bool transparent)
    {
        Material mat = baseMaterial != null
            ? new Material(baseMaterial)
            : new Material(Shader.Find("Universal Render Pipeline/Lit")
                        ?? Shader.Find("HDRP/Lit")
                        ?? Shader.Find("Standard"));

        mat.color = col;
        if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", col);

        if (transparent)
        {
            if (mat.HasProperty("_Surface"))
            {
                mat.SetFloat("_Surface", 1f);
                mat.SetFloat("_Blend", 0f);
                mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
                mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
                mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                mat.SetInt("_ZWrite", 0);
                mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            }
            else
            {
                mat.SetFloat("_Mode", 3f);
                mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
                mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                mat.SetInt("_ZWrite", 0);
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

    private void AttachHoverable(GameObject go, string baseMetaText)
    {
        if (hoverCanvasPrefab == null) return;
        var hoverable = go.AddComponent<Hoverable>();
        var ui = Instantiate(hoverCanvasPrefab, go.transform.position, Quaternion.identity);
        ui.transform.SetParent(transform);

        _instantiatedPopups.Add(ui);

        Canvas c = ui.GetComponent<Canvas>();
        if (c != null) c.enabled = _showPopupUI;

        hoverable.uiOffset = hoverUIOffset;
        hoverable.Init(ui, baseMetaText);
    }
}