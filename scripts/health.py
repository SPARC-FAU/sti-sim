"""Desktop/process liveness only. Use check.py for sensor and motion validation."""
import subprocess
import os
import urllib.request

native = os.environ.get('STI_DISPLAY', 'browser') == 'native'
if not native:
    with urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=3) as response:
        assert response.status == 200
result = subprocess.run(
    ['supervisorctl', '-c', '/opt/sti/config/supervisord.conf', 'status'],
    capture_output=True, text=True,
)
for name in (('demo',) if native else ('desktop', 'vnc', 'web', 'demo')):
    assert any(line.split()[0] == name and line.split()[1] == 'RUNNING'
               for line in result.stdout.splitlines()), result.stdout
