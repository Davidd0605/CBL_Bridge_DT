using UnityEngine;

public class HoverSystem : MonoBehaviour
{
    private Hoverable current;
    private bool uiEnabled = true;

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.Q))
        {
            uiEnabled = !uiEnabled;

            if (!uiEnabled && current != null)
            {
                current.HideUI();
                current = null;
            }
        }

        if (!uiEnabled) return;

        Ray ray = Camera.main.ScreenPointToRay(Input.mousePosition);

        if (Physics.Raycast(ray, out RaycastHit hit))
        {
            var hover = hit.collider.GetComponent<Hoverable>();

            if (hover != current)
            {
                if (current != null)
                    current.HideUI();

                current = hover;

                if (current != null)
                    current.ShowUI();
            }
        }
        else
        {
            if (current != null)
            {
                current.HideUI();
                current = null;
            }
        }
    }
}