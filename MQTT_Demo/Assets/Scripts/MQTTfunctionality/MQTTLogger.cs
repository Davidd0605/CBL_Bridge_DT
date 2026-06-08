using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

public class MQTTLogger : MonoBehaviour
{
    public static MQTTLogger Instance;

    [Header("Topics to Log")]
    public List<string> topicsToLog = new List<string>();

    [Header("Settings")]
    public string logFolderName = "mqtt_logs";
    public string uploadUrl = "http://80.113.118.200:4500/logs/upload";

    private string _sessionFolder;
    private Dictionary<string, StringBuilder> _topicLogs = new Dictionary<string, StringBuilder>();
    private string _sessionStart;

    void Awake() { Instance = this; }

    void Start()
    {
        _sessionStart = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss");
        string rootFolder = Path.Combine(Application.persistentDataPath, logFolderName);
        _sessionFolder = Path.Combine(rootFolder, $"session_{_sessionStart}");
        Directory.CreateDirectory(_sessionFolder);
        Debug.Log($"[MQTTLogger] Logging to: {_sessionFolder}");

        foreach (var topic in topicsToLog)
        {
            _topicLogs[topic] = new StringBuilder();
            _topicLogs[topic].AppendLine($"=== Topic: {topic} ===");
            _topicLogs[topic].AppendLine($"Session Start: {_sessionStart}");
            _topicLogs[topic].AppendLine(new string('=', 40));
            _topicLogs[topic].AppendLine();

            string capturedTopic = topic;
            MQTTClient.Instance.Subscribe(capturedTopic, (t, payload) => LogMessage(t, payload));
        }
    }

    private void LogMessage(string topic, string payload)
    {
        if (!_topicLogs.ContainsKey(topic)) return;
        string timestamp = DateTime.Now.ToString("HH:mm:ss.fff");
        _topicLogs[topic].AppendLine($"[{timestamp}]");
        _topicLogs[topic].AppendLine(payload);
        _topicLogs[topic].AppendLine();
    }

    void OnApplicationQuit()
    {
        string zipPath = SaveAndZip();
        // OnApplicationQuit can't run coroutines, so use a fire-and-forget upload
        if (zipPath != null)
            StartCoroutine(UploadZip(zipPath));
    }

    private string SaveAndZip()
    {
        foreach (var kvp in _topicLogs)
        {
            kvp.Value.AppendLine();
            kvp.Value.AppendLine(new string('=', 40));
            kvp.Value.AppendLine($"Session End: {DateTime.Now:yyyy-MM-dd_HH-mm-ss}");
            string safeFileName = kvp.Key.Replace("/", "_") + ".txt";
            File.WriteAllText(Path.Combine(_sessionFolder, safeFileName), kvp.Value.ToString());
        }

        string zipPath = _sessionFolder + ".zip";
        if (File.Exists(zipPath)) File.Delete(zipPath);
        ZipFile.CreateFromDirectory(_sessionFolder, zipPath);
        Debug.Log($"[MQTTLogger] Zipped to: {zipPath}");
        return zipPath;
    }

    public void SaveNow()
    {
        string zipPath = SaveAndZip();
        if (zipPath != null)
            StartCoroutine(UploadZip(zipPath));
    }

    private IEnumerator UploadZip(string zipPath)
    {
        byte[] fileData = File.ReadAllBytes(zipPath);
        string fileName = Path.GetFileName(zipPath);

        WWWForm form = new WWWForm();
        form.AddBinaryData("file", fileData, fileName, "application/zip");

        Debug.Log($"[MQTTLogger] Uploading {fileName}...");

        using UnityWebRequest req = UnityWebRequest.Post(uploadUrl, form);
        yield return req.SendWebRequest();

        if (req.result == UnityWebRequest.Result.Success)
            Debug.Log($"[MQTTLogger] Upload success: {req.downloadHandler.text}");
        else
            Debug.LogError($"[MQTTLogger] Upload failed: {req.error}");
    }
}