using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

/// <summary>
/// Singleton MQTT wrapper for Unity, mirroring the Python MQTTClient API.
/// Reads config from Resources/mqttconfig.json
///
/// Usage:
///     var broker = MQTTClient.Instance;
///
///     // publish
///     broker.Publish("sensors/temperature", "{\"value\": 23.5}");
///
///     // subscribe
///     broker.Subscribe("sensors/temperature", (topic, payload) => {
///         Debug.Log($"Got: {payload}");
///     });
///
///     // wildcards work too
///     broker.Subscribe("sensors/#", (topic, payload) => Debug.Log($"{topic}: {payload}"));
/// </summary>
public class MQTTClient : MonoBehaviour
{
    private static MQTTClient _instance;
    public static MQTTClient Instance
    {
        get
        {
            if (_instance == null)
            {
                var go = new GameObject("MQTTClient");
                _instance = go.AddComponent<MQTTClient>();
                DontDestroyOnLoad(go);
            }
            return _instance;
        }
    }

    private MqttClient _client;

    private readonly Dictionary<string, List<Action<string, string>>> _handlers
        = new Dictionary<string, List<Action<string, string>>>();

    private readonly Queue<(string topic, string payload)> _messageQueue
        = new Queue<(string, string)>();
    private readonly object _queueLock = new object();

    //Config
    [Serializable]
    private class MqttConfig
    {
        public string broker;
        public int port = 1883;
        public string username;
        public string password;
    }

    //Lifecycle 
    private void Awake()
    {
        if (_instance != null && _instance != this) { Destroy(gameObject); return; }
        _instance = this;
        DontDestroyOnLoad(gameObject);
        Connect();
    }

    private void Connect()
    {
        var file = Resources.Load<TextAsset>("mqttconfig");
        if (file == null) { Debug.LogError("[MQTT] mqttconfig.json not found in Resources/"); return; }

        var cfg = JsonUtility.FromJson<MqttConfig>(file.text);

        _client = new MqttClient(cfg.broker, cfg.port, false, null, null, MqttSslProtocols.None);
        _client.MqttMsgPublishReceived += OnMessageReceived;
        _client.Connect("UnityClient_" + Guid.NewGuid(), cfg.username, cfg.password);

        Debug.Log($"[MQTT] Connected to {cfg.broker}:{cfg.port}");
    }

    private void Update()
    {
        while (true)
        {
            (string topic, string payload) msg;
            lock (_queueLock)
            {
                if (_messageQueue.Count == 0) break;
                msg = _messageQueue.Dequeue();
            }
            Dispatch(msg.topic, msg.payload);
        }
    }

    private void OnApplicationQuit()
    {
        Disconnect();
    }

    public void Disconnect()
    {
        if (_client != null && _client.IsConnected)
        {
            _client.Disconnect();
            Debug.Log("[MQTT] Disconnected");
        }
    }

    //Public API

    /// <summary>
    /// Publish a string payload to a topic.
    /// </summary>
    public void Publish(string topic, string payload)
    {
        if (_client == null || !_client.IsConnected)
        {
            Debug.LogWarning("[MQTT] Cannot publish: not connected.");
            return;
        }
        _client.Publish(topic, Encoding.UTF8.GetBytes(payload), MqttMsgBase.QOS_LEVEL_AT_LEAST_ONCE, false);
    }

    public void Subscribe(string topic, Action<string, string> callback)
    {
        if (!_handlers.ContainsKey(topic))
        {
            _handlers[topic] = new List<Action<string, string>>();
            _client.Subscribe(new[] { topic }, new[] { MqttMsgBase.QOS_LEVEL_AT_LEAST_ONCE });
            Debug.Log($"[MQTT] Subscribed to {topic}");
        }
        _handlers[topic].Add(callback);
    }

    /// <summary>
    /// Remove all callbacks for a topic and unsubscribe from the broker.
    /// </summary>
    public void Unsubscribe(string topic)
    {
        if (_handlers.Remove(topic))
        {
            _client.Unsubscribe(new[] { topic });
            Debug.Log($"[MQTT] Unsubscribed from {topic}");
        }
    }

    // Fires on M2Mqtt background thread — only enqueue here, dispatch on main thread
    private void OnMessageReceived(object sender, MqttMsgPublishEventArgs e)
    {
        string topic = e.Topic;
        string payload = Encoding.UTF8.GetString(e.Message);
        lock (_queueLock) { _messageQueue.Enqueue((topic, payload)); }
    }

    private void Dispatch(string topic, string payload)
    {
        foreach (var kvp in _handlers)
        {
            if (kvp.Key == topic || Matches(kvp.Key, topic))
            {
                foreach (var cb in kvp.Value)
                {
                    try { cb(topic, payload); }
                    catch (Exception ex) { Debug.LogError($"[MQTT] Handler error for {topic}: {ex}"); }
                }
            }
        }
    }

    /// <summary>
    /// Matches MQTT wildcards: + (single level) and # (multi level).
    /// Mirrors the Python _matches() method exactly.
    /// </summary>
    private static bool Matches(string pattern, string topic)
    {
        string[] pp = pattern.Split('/');
        string[] tp = topic.Split('/');

        for (int i = 0; i < pp.Length; i++)
        {
            if (pp[i] == "#") return true;
            if (i >= tp.Length) return false;
            if (pp[i] != "+" && pp[i] != tp[i]) return false;
        }
        return pp.Length == tp.Length;
    }

    /// <summary>
    /// Exposes whether the underlying M2Mqtt client is currently connected to the broker.
    /// </summary>
    public bool IsConnected
    {
        get
        {
            return _client != null && _client.IsConnected;
        }
    }
}