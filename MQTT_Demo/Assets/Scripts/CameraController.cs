using UnityEngine;

public class CameraController : MonoBehaviour
{
    [Header("Movement")]
    public float moveSpeed = 5f;
    public float sprintMultiplier = 2.5f;
    public float smoothTime = 0.1f;

    [Header("Look")]
    public float mouseSensitivity = 2f;
    public bool invertY = false;

    [Header("Overlay Setup")]
    [Tooltip("Drag your child UICamera here. If left empty, it will auto-detect via layer.")]
    public Camera overlayCamera;

    [Header("Crosshair Setup")]
    [Tooltip("If you leave this empty, the script will automatically generate a clean default crosshair cross.")]
    public Texture2D crosshairTexture;
    public int crosshairSize = 12;
    public Color crosshairColor = new Color(1f, 1f, 1f, 0.8f);

    private Vector3 _velocity;
    private Vector3 _targetVelocity;
    private float _yaw;
    private float _pitch;
    private bool _cursorLocked;
    private Transform _transform;

    // Track ONLY the single object we are currently looking at
    private Hoverable _currentlyHovered;

    void Start()
    {
        _transform = transform;
        _yaw = _transform.eulerAngles.y;
        _pitch = _transform.eulerAngles.x;

        if (overlayCamera == null)
        {
            foreach (var cam in Camera.allCameras)
            {
                if (cam.cullingMask == (1 << LayerMask.NameToLayer("HoverUI")))
                {
                    overlayCamera = cam;
                    break;
                }
            }
        }

        if (crosshairTexture == null)
        {
            crosshairTexture = Texture2D.whiteTexture;
        }
    }

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.Escape))
            SetCursorLock(false);
        if (Input.GetMouseButtonDown(1))
            SetCursorLock(true);

        if (_cursorLocked)
        {
            _yaw += Input.GetAxis("Mouse X") * mouseSensitivity;
            _pitch -= Input.GetAxis("Mouse Y") * mouseSensitivity * (invertY ? -1f : 1f);
            _pitch = Mathf.Clamp(_pitch, -89f, 89f);
            _transform.rotation = Quaternion.Euler(_pitch, _yaw, 0f);
        }

        float speed = moveSpeed * (Input.GetKey(KeyCode.LeftShift) ? sprintMultiplier : 1f);
        Vector3 input = new Vector3(
            Input.GetAxisRaw("Horizontal"),
            (Input.GetKey(KeyCode.Space) ? 1f : 0f) - (Input.GetKey(KeyCode.LeftControl) ? 1f : 0f),
            Input.GetAxisRaw("Vertical")
        ).normalized;

        _targetVelocity = _transform.TransformDirection(input) * speed;
    }

    void LateUpdate()
    {
        _velocity = Vector3.Lerp(_velocity, _targetVelocity, (smoothTime > 0 ? (Time.deltaTime / smoothTime) : 1f));
        _transform.position += _velocity * Time.deltaTime;

        if (overlayCamera != null)
        {
            Transform overlayTransform = overlayCamera.transform;
            overlayTransform.position = _transform.position;
            overlayTransform.rotation = _transform.rotation;
        }

        HandleRay();
    }

    void HandleRay()
    {
        Ray ray;
        if (_cursorLocked)
        {
            ray = new Ray(_transform.position, _transform.forward);
        }
        else
        {
            ray = Camera.main.ScreenPointToRay(Input.mousePosition);
        }

        RaycastHit hit;
        if (Physics.Raycast(ray, out hit, 100f))
        {
            Hoverable newHover = hit.collider.GetComponent<Hoverable>();

            // If we hit a brand-new target
            if (newHover != _currentlyHovered)
            {
                // Turn off the old UI instance safely
                if (_currentlyHovered != null)
                {
                    _currentlyHovered.HideUI();
                }

                _currentlyHovered = newHover;

                // Turn on the new UI instance safely
                if (_currentlyHovered != null)
                {
                    _currentlyHovered.ShowUI();
                }
            }
        }
        else
        {
            // Raycast hit nothing, clean up whatever was open
            if (_currentlyHovered != null)
            {
                _currentlyHovered.HideUI();
                _currentlyHovered = null;
            }
        }
    }

    void SetCursorLock(bool locked)
    {
        _cursorLocked = locked;
        Cursor.lockState = locked ? CursorLockMode.Locked : CursorLockMode.None;
        Cursor.visible = !locked;

        // Force drop UI focus instantly when dropping cursor lock
        if (!locked && _currentlyHovered != null)
        {
            _currentlyHovered.HideUI();
            _currentlyHovered = null;
        }
    }

    void OnGUI()
    {
        if (!_cursorLocked) return;

        float centerX = Screen.width / 2f;
        float centerY = Screen.height / 2f;

        GUI.color = crosshairColor;

        if (crosshairTexture != Texture2D.whiteTexture)
        {
            Rect position = new Rect(centerX - (crosshairSize / 2f), centerY - (crosshairSize / 2f), crosshairSize, crosshairSize);
            GUI.DrawTexture(position, crosshairTexture);
        }
        else
        {
            float thickness = 2f;

            Rect horizPos = new Rect(centerX - (crosshairSize / 2f), centerY - (thickness / 2f), crosshairSize, thickness);
            GUI.DrawTexture(horizPos, Texture2D.whiteTexture);

            Rect vertPos = new Rect(centerX - (thickness / 2f), centerY - (crosshairSize / 2f), thickness, crosshairSize);
            GUI.DrawTexture(vertPos, Texture2D.whiteTexture);
        }
    }
}