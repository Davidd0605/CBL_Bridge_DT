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

    private Vector3 _velocity;
    private Vector3 _targetVelocity;
    private float _yaw;
    private float _pitch;
    private bool _cursorLocked;

    void Start()
    {
        _yaw = transform.eulerAngles.y;
        _pitch = transform.eulerAngles.x;
    }

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.Q))
            Hoverable.TogglePopups();
        if (Input.GetKeyDown(KeyCode.Escape))
            SetCursorLock(false);
        if (Input.GetMouseButtonDown(1))
            SetCursorLock(true);

        if (_cursorLocked)
        {
            _yaw += Input.GetAxis("Mouse X") * mouseSensitivity;
            _pitch -= Input.GetAxis("Mouse Y") * mouseSensitivity * (invertY ? -1f : 1f);
            _pitch = Mathf.Clamp(_pitch, -89f, 89f);
            transform.rotation = Quaternion.Euler(_pitch, _yaw, 0f);
        }

        float speed = moveSpeed * (Input.GetKey(KeyCode.LeftShift) ? sprintMultiplier : 1f);
        Vector3 input = new Vector3(
            Input.GetAxisRaw("Horizontal"),
            (Input.GetKey(KeyCode.Space) ? 1f : 0f) - (Input.GetKey(KeyCode.LeftControl) ? 1f : 0f),
            Input.GetAxisRaw("Vertical")
        ).normalized;

        _targetVelocity = transform.TransformDirection(input) * speed;
        _velocity = Vector3.Lerp(_velocity, _targetVelocity, smoothTime / Time.deltaTime * 10f);
        transform.position += _velocity * Time.deltaTime;

        HandleRay();
    }

    void HandleRay()
    {
        Ray ray = Camera.main.ScreenPointToRay(Input.mousePosition);
        RaycastHit hit;

        if (Physics.Raycast(ray, out hit))
        {
            Hoverable hoverable = hit.collider.GetComponent<Hoverable>();
            if (hoverable != null)
            {
                hoverable.ShowUI();
                return;
            }
        }

        // nothing hit (or hit something without Hoverable) — hide all
        Hoverable.HideAll();
    }
    void SetCursorLock(bool locked)
    {
        _cursorLocked = locked;
        Cursor.lockState = locked ? CursorLockMode.Locked : CursorLockMode.None;
        Cursor.visible = !locked;
    }
}