using System;
using System.Collections.Generic;
using UnityEngine;

public class BridgeBuilder : MonoBehaviour
{
    [Header("Bridge Data")]
    public TextAsset bridgeJson;

    [Header("Scale")]
    [Tooltip("Multiply all coordinates by this. 10 = 1 real metre becomes 10 Unity units.")]
    public float worldScale = 10f;

    [Header("Geometry")]
    public float beamRadius = 0.015f;
    public float nodeRadius = 0.03f;

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

    [Header("Stress Colours (live from MQTT)")]
    public Color colOk = new Color(0.10f, 0.90f, 0.40f);
    public Color colWarning = new Color(1.00f, 0.65f, 0.00f);
    public Color colOverloaded = new Color(0.95f, 0.10f, 0.10f);

    [Header("Rendering")]
    public Material baseMaterial;

    private Dictionary<int, Vector3> _basePos = new();
    private Dictionary<int, GameObject> _nodeObjects = new();
    private Dictionary<int, GameObject> _beamObjects = new();
    private Dictionary<int, (int i, int j)> _beamEnds = new();

    [Serializable] private class NodeData { public int id; public string label; public float x, y, z; }
    [Serializable] private class ElementData { public int id; public string type; public int i, j; }
    [Serializable] private class SupportData { public int node; }
    [Serializable] private class SensorData { public string sensor_id; public int node; }
    [Serializable]
    private class BridgeData
    {
        public NodeData[] nodes;
        public ElementData[] elements;
        public SupportData[] supports;
        public SensorData[] deflection_sensor_points;
    }

    void Start()
    {
        if (bridgeJson == null)
        {
            Debug.LogError("BridgeBuilder: assign a bridge JSON in the Inspector.");
            return;
        }
        Build(bridgeJson.text);
    }

    public void UpdateDisplacements(Dictionary<int, Vector3> displacements, float visualScale = 200f)
    {
        foreach (var kvp in displacements)
        {
            if (!_nodeObjects.TryGetValue(kvp.Key, out var sphere)) continue;
            sphere.transform.localPosition = _basePos[kvp.Key] + kvp.Value * (visualScale * worldScale);
        }
        foreach (var kvp in _beamObjects)
        {
            var (ni, nj) = _beamEnds[kvp.Key];
            RepositionBeam(kvp.Value,
                _nodeObjects[ni].transform.localPosition,
                _nodeObjects[nj].transform.localPosition);
        }
    }

    public void UpdateUtilizations(Dictionary<int, float> utilizations)
    {
        foreach (var kvp in utilizations)
        {
            if (!_beamObjects.TryGetValue(kvp.Key, out var beam)) continue;
            var col = kvp.Value > 1f ? colOverloaded
                    : kvp.Value > 0.75f ? colWarning
                    : colOk;
            beam.GetComponent<Renderer>().material.color = col;
        }
    }

    public void ResetDeformation()
    {
        foreach (var kvp in _nodeObjects)
            kvp.Value.transform.localPosition = _basePos[kvp.Key];
        foreach (var kvp in _beamObjects)
        {
            var (ni, nj) = _beamEnds[kvp.Key];
            RepositionBeam(kvp.Value, _basePos[ni], _basePos[nj]);
        }
    }

    private void Build(string json)
    {
        var data = JsonUtility.FromJson<BridgeData>(json);
        if (data == null) { Debug.LogError("BridgeBuilder: failed to parse JSON."); return; }

        var supportNodes = new HashSet<int>();
        if (data.supports != null)
            foreach (var s in data.supports) supportNodes.Add(s.node);

        var sensorNodes = new HashSet<int>();
        if (data.deflection_sensor_points != null)
            foreach (var s in data.deflection_sensor_points) sensorNodes.Add(s.node);

        foreach (var n in data.nodes)
        {
            var pos = new Vector3(n.x, n.y, n.z) * worldScale;
            _basePos[n.id] = pos;

            var sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            sphere.name = $"Node_{n.id}_{n.label}";
            sphere.transform.SetParent(transform);
            sphere.transform.localPosition = pos;
            sphere.transform.localScale = Vector3.one * nodeRadius * 2f;
            Destroy(sphere.GetComponent<Collider>());

            var col = supportNodes.Contains(n.id) ? colSupport
                    : sensorNodes.Contains(n.id) ? colSensor
                    : colNode;
            SetColor(sphere, col);
            _nodeObjects[n.id] = sphere;
        }

        foreach (var e in data.elements)
        {
            if (!_basePos.TryGetValue(e.i, out var a) ||
                !_basePos.TryGetValue(e.j, out var b))
            {
                Debug.LogWarning($"Element {e.id}: node {e.i} or {e.j} not found.");
                continue;
            }
            var beam = CreateBeam(a, b, ElementColor(e.type));
            beam.name = $"Elem_{e.id}_{e.type}";
            beam.transform.SetParent(transform);
            _beamObjects[e.id] = beam;
            _beamEnds[e.id] = (e.i, e.j);
        }

        Debug.Log($"BridgeBuilder: {data.nodes.Length} nodes, {data.elements.Length} elements spawned.");
    }

    private GameObject CreateBeam(Vector3 a, Vector3 b, Color col)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        Destroy(go.GetComponent<Collider>());
        RepositionBeam(go, a, b);
        SetColor(go, col);
        return go;
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

    private Material MakeMaterial(Color col)
    {
        Material mat;
        if (baseMaterial != null)
        {
            mat = new Material(baseMaterial);
        }
        else
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Lit")
                         ?? Shader.Find("HDRP/Lit")
                         ?? Shader.Find("Standard");
            mat = new Material(shader);
        }

        mat.color = col;

        if (mat.HasProperty("_BaseColor"))
            mat.SetColor("_BaseColor", col);

        return mat;
    }

    private void SetColor(GameObject go, Color col)
    {
        go.GetComponent<Renderer>().material = MakeMaterial(col);
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