using UnityEngine;

public class CameraManager : MonoBehaviour
{
    public static CameraManager Instance;

    [Header("References")]
    public CameraController freeroamController;
    public Transform cameraTransform;

    [Header("Orbit Settings")]
    public float orbitSpeed = 3f;
    public float zoomSpeed = 3f;
    public float minZoom = 2f;
    public float maxZoom = 15f;
    public float transitionSpeed = 5f;

    private bool _isOrbiting = false;
    private Transform _target;
    private float _currentRadius = 5f;
    private float _yaw = 0f;
    private float _pitch = 20f;

    void Awake()
    {
        Instance = this;
    }

    void Update()
    {
        if (!_isOrbiting) return;

        if (Input.GetKeyDown(KeyCode.Escape))
        {
            ExitOrbit();
            return;
        }

        // Orbit with left mouse drag
        if (Input.GetMouseButton(0))
        {
            _yaw += Input.GetAxis("Mouse X") * orbitSpeed;
            _pitch -= Input.GetAxis("Mouse Y") * orbitSpeed;
            _pitch = Mathf.Clamp(_pitch, -45f, 80f);
        }

        // Zoom with scroll wheel
        float scroll = Input.GetAxis("Mouse ScrollWheel");
        _currentRadius = Mathf.Clamp(_currentRadius - scroll * zoomSpeed, minZoom, maxZoom);

        // Calculate orbit position
        Quaternion rotation = Quaternion.Euler(_pitch, _yaw, 0f);
        Vector3 targetPosition = _target.position + rotation * new Vector3(0f, 0f, -_currentRadius);

        // Smoothly move camera
        cameraTransform.position = Vector3.Lerp(cameraTransform.position, targetPosition, Time.deltaTime * transitionSpeed);
        cameraTransform.LookAt(_target.position);
    }

    public void FocusOn(Transform target)
    {
        _target = target;
        _currentRadius = 5f;
        _yaw = 0f;
        _pitch = 20f;

        freeroamController.enabled = false;
        _isOrbiting = true;
    }

    private void ExitOrbit()
    {
        _isOrbiting = false;
        _target = null;
        freeroamController.enabled = true;
    }
}