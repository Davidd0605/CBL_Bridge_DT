using System;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

public class EPyTMQTT : MonoBehaviour
{
    private MqttClient client;
    private string latestMessage = null;
    private readonly object msgLock = new object();

    [System.Serializable]
    private class MqttConfig
    {
        public string broker;
        public int port;
        public string username;
        public string password;
    }

    private MqttConfig LoadConfig()
    {
        var file = Resources.Load<TextAsset>("mqttconfig");
        if (file == null) { Debug.LogError("mqttconfig.json not found in Resources!"); return null; }
        return JsonUtility.FromJson<MqttConfig>(file.text);
    }

    void Start()
    {
        var config = LoadConfig();
        if (config == null) return;

        client = new MqttClient(config.broker, config.port, false, null, null, MqttSslProtocols.None);
        client.MqttMsgPublishReceived += OnMessageReceived;
        client.Connect("UnityClient_" + Guid.NewGuid(), config.username, config.password);
        client.Subscribe(new string[] { "my/topic" }, new byte[] { 1 });
        Debug.Log("Connected and subscribed to my/topic");
    }

    void Update()
    {
        string msg = null;
        lock (msgLock) { msg = latestMessage; latestMessage = null; }
        if (Input.GetMouseButtonDown(0))
        {
            client.Publish("my/topic", Encoding.UTF8.GetBytes("hello from Unity"), 1, false);
        }
    }

    void OnMessageReceived(object sender, MqttMsgPublishEventArgs e)
    {
        lock (msgLock) { latestMessage = Encoding.UTF8.GetString(e.Message); }
    }

    void OnApplicationQuit()
    {
        if (client != null && client.IsConnected) client.Disconnect();
    }
}