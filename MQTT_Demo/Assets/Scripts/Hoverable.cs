using UnityEngine;
using TMPro;

public class Hoverable : MonoBehaviour
{
    public GameObject uiInstance;
    public Vector3 uiOffset = new Vector3(0f, 0.1f, 0f);

    private TextMeshProUGUI tmp;
    private static Hoverable _current;

    public void Init(GameObject ui, string text)
    {
        uiInstance = ui;
        tmp = uiInstance.GetComponentInChildren<TextMeshProUGUI>();
        if (tmp != null)
            tmp.text = text;
        uiInstance.SetActive(false);
    }

    public void ShowUI()
    {
        if (!_popupsEnabled) return;
        if (_current != null && _current != this)
            _current.HideUI();

        _current = this;

        if (uiInstance != null)
        {
            uiInstance.transform.position = transform.position + uiOffset;
            uiInstance.transform.rotation = Camera.main.transform.rotation;
            uiInstance.SetActive(true);
        }
    }
    private static bool _popupsEnabled = true;

    public static void TogglePopups()
    {
        _popupsEnabled = !_popupsEnabled;
        if (!_popupsEnabled)
            HideAll();
    }
    public void HideUI()
    {
        if (uiInstance != null)
            uiInstance.SetActive(false);
    }

    public static void HideAll()
    {
        if (_current != null)
        {
            _current.HideUI();
            _current = null;
        }
    }
}