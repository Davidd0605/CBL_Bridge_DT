using UnityEngine;
using TMPro;

public class Hoverable : MonoBehaviour
{
    public GameObject uiInstance;
    private TextMeshProUGUI tmp;

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
        if (uiInstance != null)
            uiInstance.SetActive(true);
    }

    public void HideUI()
    {
        if (uiInstance != null)
            uiInstance.SetActive(false);
    }
}