using System;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

public class EPyTMQTT : MonoBehaviour
{
    private MqttClient client; //unity client objects


    //connect to mosquito broker
    public string brokerHost = "localhost";
    public int brokerPort = 1883;

    //live values
    public float J1_Pressure;
    public float J2_Pressure;
    public float P1_Flow;
    public float P2_Flow;
    public float TimeHours;

    private string latestMessage = null; //shared buffer between mqtt backround thread and unity main thread
    private readonly object msgLock = new object(); //mutex for thread safety (latestmsg can only be accessed by one thread at once)

    void Start()
    {
        client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
        client.MqttMsgPublishReceived += OnMessageReceived;

        string clientId = "UnityClient_" + Guid.NewGuid();
        client.Connect(clientId);

        // Subscribe to simulation data
        client.Subscribe(
            new string[] { "epanet/data" },
            new byte[] { 1 }
        );

        Debug.Log("Connected to MQTT broker and subscribed to epanet/data");
    }

    void Update()
    {
        // Parse on main thread (Unity requires this) read msg from buffer
        string msg = null;
        lock (msgLock) { msg = latestMessage; latestMessage = null; }

        if (msg != null)
        {
            EPyTData data = JsonUtility.FromJson<EPyTData>(msg); //parse json received into epyt data type
            J1_Pressure = data.pressures.J1;
            J2_Pressure = data.pressures.J2;
            P1_Flow = data.flows.P1;
            P2_Flow = data.flows.P2;
            TimeHours = data.time_hours;

            Debug.Log($"[EPyT] Hour {TimeHours}: J1={J1_Pressure}m | J2={J2_Pressure}m");
        }

        //send shit to python too on click
        if (Input.GetMouseButtonDown(0))
        {
            SendCommand("mouse_click");
            Debug.Log("Click sent to Python!");
        }
    }

    void OnMessageReceived(object sender, MqttMsgPublishEventArgs e)
    {
        string msg = Encoding.UTF8.GetString(e.Message);
        lock (msgLock) { latestMessage = msg; } //write message to buffer
    }

    public void SendCommand(string action, int node = 1, float demand = 50f)
    {
        string payload = action == "set_demand"
            ? $"{{\"action\":\"set_demand\",\"node\":{node},\"demand\":{demand}}}"
            : $"{{\"action\":\"{action}\"}}";

        client.Publish(
            "epanet/control",
            Encoding.UTF8.GetBytes(payload),
            1,
            false
        );
    }

    void OnApplicationQuit()
    {
        if (client != null && client.IsConnected)
            client.Disconnect();
    }

    [Serializable]
    public class EPyTData
    {
        public int step;
        public float time_hours;
        public Pressures pressures;
        public Flows flows;
    }

    [Serializable] public class Pressures { public float J1; public float J2; }
    [Serializable] public class Flows { public float P1; public float P2; }
}