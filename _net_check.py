import os
import socket

print("proxy env:", {k: v for k, v in os.environ.items() if "proxy" in k.lower() or k.upper().startswith(("PIP_", "UV_"))})
for host in ("pypi.org", "files.pythonhosted.org"):
    s = socket.socket()
    s.settimeout(8)
    try:
        s.connect((host, 443))
        print(host, "TCP connect OK")
    except Exception as exc:
        print(host, "FAIL:", type(exc).__name__, exc)
    finally:
        s.close()
