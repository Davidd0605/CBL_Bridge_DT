using UnityEngine;
using TMPro;

public class Hoverable : MonoBehaviour
{
    public Vector3 uiOffset = new Vector3(0f, 0.5f, 0f);

    private GameObject _uiInstance;
    private Canvas _uiCanvas;
    private TextMeshProUGUI _telemetryTextComponent;

    private string _baseMetadata = "";
    private string _liveTelemetry = "";

    // Controlled EXCLUSIVELY by your UI Button now
    private static bool _globalPopupsEnabled = true;

    public static void SetGlobalVisibility(bool isEnabled)
    {
        _globalPopupsEnabled = isEnabled;
    }

    public void Init(GameObject uiPrefabAsset, string baseMetadataText)
    {
        _uiInstance = uiPrefabAsset;
        _baseMetadata = baseMetadataText;

        if (_uiInstance != null)
        {
            _uiCanvas = _uiInstance.GetComponent<Canvas>();
            _telemetryTextComponent = _uiInstance.GetComponentInChildren<TextMeshProUGUI>();

            if (_uiCanvas != null) _uiCanvas.enabled = false;

            UpdateVisualText();
        }
    }

    public void UpdateLiveTelemetry(string telemetryText)
    {
        _liveTelemetry = telemetryText;

        // If the UI button turned things off, block live data from overriding it
        if (!_globalPopupsEnabled)
        {
            HideUI();
            return;
        }

        UpdateVisualText();
    }

    private void UpdateVisualText()
    {
        if (_telemetryTextComponent == null) return;

        if (string.IsNullOrEmpty(_liveTelemetry))
            _telemetryTextComponent.text = _baseMetadata;
        else
            _telemetryTextComponent.text = $"{_baseMetadata}\n\n{_liveTelemetry}";
    }

    void Update()
    {
        if (_uiInstance != null && _uiCanvas != null && _uiCanvas.enabled)
        {
            _uiInstance.transform.position = transform.position + uiOffset;
            _uiInstance.transform.rotation = Camera.main.transform.rotation;
        }
    }

    public void ShowUI()
    {
        if (!_globalPopupsEnabled) return;
        if (_uiCanvas != null) _uiCanvas.enabled = true;
    }

    public void HideUI()
    {
        if (_uiCanvas != null) _uiCanvas.enabled = false;
    }

    public static void HideAll()
    {
        var hovers = FindObjectsByType<Hoverable>(FindObjectsSortMode.None);
        foreach (var h in hovers)
        {
            h.HideUI();
        }
    }
}