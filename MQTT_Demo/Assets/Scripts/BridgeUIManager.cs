using UnityEngine;
using TMPro;

public class BridgeUIManager : MonoBehaviour
{
    [Header("Network UI Outlets")]
    [SerializeField] private TextMeshProUGUI statusText;

    [Header("Status Theme Colors")]
    public Color connectedColor = new Color(0.2f, 0.9f, 0.4f);
    public Color disconnectedColor = new Color(0.9f, 0.2f, 0.2f);

    void Update()
    {
        UpdateMQTTStatusText();
    }

    private void UpdateMQTTStatusText()
    {
        if (statusText == null) return;

        // Reads our brand new public property from MQTTClient!
        bool connected = MQTTClient.Instance != null && MQTTClient.Instance.IsConnected;

        if (connected)
        {
            statusText.text = "MQTT STATUS: <color=#" + ColorUtility.ToHtmlStringRGB(connectedColor) + ">CONNECTED</color>";
        }
        else
        {
            statusText.text = "MQTT STATUS: <color=#" + ColorUtility.ToHtmlStringRGB(disconnectedColor) + ">DISCONNECTED</color>";
        }
    }

    // --- BUTTON EVENT BROKERS ---
    public void OnClickToggleReal()
    {
        if (BridgeBuilder.Instance != null)
            BridgeBuilder.Instance.ToggleRealBridge();
    }

    public void OnClickToggleSim()
    {
        if (BridgeBuilder.Instance != null)
            BridgeBuilder.Instance.ToggleSimulationBridge();
    }

    public void OnClickTogglePopups()
    {
        if (BridgeBuilder.Instance != null)
            BridgeBuilder.Instance.TogglePopupUI();
    }

    public void OnClickToggleColorMode()
    {
        if (BridgeBuilder.Instance != null)
            BridgeBuilder.Instance.ToggleColorMode();
    }
}