using UnityEngine;
using TMPro;
using System;

public class BridgeUIManager : MonoBehaviour
{
    [Header("Network UI Outlets")]
    [SerializeField] private TextMeshProUGUI statusText;
    [SerializeField] private TextMeshProUGUI lastMessageText;

    [Header("Status Theme Colors")]
    public Color connectedColor = new Color(0.2f, 0.9f, 0.4f);
    public Color disconnectedColor = new Color(0.9f, 0.2f, 0.2f);

    [Header("Last Message Settings")]
    public int maxTrackedTopics = 100;
    public Color timestampColor = new Color(0.6f, 0.6f, 0.6f);
    public Color topicColor = new Color(0.3f, 0.8f, 1.0f);

    private struct MessageEntry
    {
        public string timestamp;
        public string topic;
    }

    private readonly System.Collections.Generic.Queue<MessageEntry> _recentMessages
        = new System.Collections.Generic.Queue<MessageEntry>();

    private readonly string[] _watchedTopics = new string[]
    {
        "cbl/bridge/real/geometry",
        "cbl/bridge/real/state",
        "cbl/bridge/sim/geometry",
        "cbl/bridge/sim/state"
    };

    void Start()
    {
        foreach (var topic in _watchedTopics)
        {
            string capturedTopic = topic;
            MQTTClient.Instance.Subscribe(capturedTopic, (t, payload) =>
            {
                RecordMessage(t);
            });
        }
    }

    void Update()
    {
        UpdateMQTTStatusText();
        UpdateLastMessageText();
    }

    private void RecordMessage(string topic)
    {
        var entry = new MessageEntry
        {
            timestamp = DateTime.Now.ToString("HH:mm:ss.fff"),
            topic = topic
        };
        _recentMessages.Enqueue(entry);
        while (_recentMessages.Count > maxTrackedTopics)
            _recentMessages.Dequeue();
    }

    private void UpdateMQTTStatusText()
    {
        if (statusText == null) return;
        bool connected = MQTTClient.Instance != null && MQTTClient.Instance.IsConnected;
        statusText.text = connected
            ? "MQTT: <color=#" + ColorUtility.ToHtmlStringRGB(connectedColor) + ">CONNECTED</color>"
            : "MQTT: <color=#" + ColorUtility.ToHtmlStringRGB(disconnectedColor) + ">DISCONNECTED</color>";
    }

    private void UpdateLastMessageText()
    {
        if (lastMessageText == null) return;

        if (_recentMessages.Count == 0)
        {
            lastMessageText.text = "<color=#666666>No messages received yet.</color>";
            return;
        }

        string tsHex = ColorUtility.ToHtmlStringRGB(timestampColor);
        string topicHex = ColorUtility.ToHtmlStringRGB(topicColor);

        var sb = new System.Text.StringBuilder();
        var entries = new System.Collections.Generic.List<MessageEntry>(_recentMessages);
        for (int i = entries.Count - 1; i >= 0; i--)
        {
            var e = entries[i];
            sb.AppendLine(
                $"<color=#{tsHex}>[{e.timestamp}]</color> " +
                $"msg on <color=#{topicHex}>{e.topic}</color>"
            );
        }

        lastMessageText.text = sb.ToString().TrimEnd();
    }

    // --- BUTTON EVENT BROKERS ---
    public void OnClickToggleReal() { BridgeBuilder.Instance?.ToggleRealBridge(); }
    public void OnClickToggleSim() { BridgeBuilder.Instance?.ToggleSimulationBridge(); }
    public void OnClickTogglePopups() { BridgeBuilder.Instance?.TogglePopupUI(); }
    public void OnClickToggleColorMode() { BridgeBuilder.Instance?.ToggleColorMode(); }
}