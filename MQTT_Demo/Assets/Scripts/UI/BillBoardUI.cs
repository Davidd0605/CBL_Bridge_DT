using UnityEngine;

public class BillboardUI : MonoBehaviour
{
    private Camera cam;

    private Vector3 initialScale;
    private float initialDistance;

    [Header("Scaling")]
    [SerializeField] private float baseScale = 3f;      // Size when close
    [SerializeField] private float growthExponent = 2f; // Higher = grows faster
    [SerializeField] private float maxScale = 10f;      // Cap

    void Start()
    {
        cam = Camera.main;

        initialScale = transform.localScale;
        initialDistance = Vector3.Distance(
            transform.position,
            cam.transform.position
        );
    }

    void LateUpdate()
    {
        if (cam == null)
            return;

        // Face camera
        transform.LookAt(transform.position + cam.transform.forward);

        // Distance-based scaling
        float currentDistance = Vector3.Distance(
            transform.position,
            cam.transform.position
        );

        float distanceRatio = currentDistance / initialDistance;

        // Start at baseScale and only grow from there
        float scale = baseScale * Mathf.Pow(distanceRatio, growthExponent);

        // Never smaller than baseScale, never larger than maxScale
        scale = Mathf.Clamp(scale, baseScale, maxScale);

        transform.localScale = initialScale * scale;
    }
}