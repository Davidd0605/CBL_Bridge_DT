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

    void Start()
    {
        client = new MqttClient("80.113.118.200", 1883, false, null, null, MqttSslProtocols.None);
        client.MqttMsgPublishReceived += OnMessageReceived;
        client.Connect("UnityClient_" + Guid.NewGuid(), "myuser", "cblbroker123");
        client.Subscribe(new string[] { "my/topic" }, new byte[] { 1 });
        Debug.Log("Connected and subscribed to my/topic");
    }

    void Update()
    {
        string msg = null;
        lock (msgLock) { msg = latestMessage; latestMessage = null; }
        if (msg != null) Debug.Log("Received: " + msg);

        if (Input.GetMouseButtonDown(0))
        {
            client.Publish("my/topic", Encoding.UTF8.GetBytes("hello from Unity"), 1, false);
            Debug.Log("Published: hello from Unity");
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