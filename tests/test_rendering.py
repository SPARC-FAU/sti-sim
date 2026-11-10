"""Exercise renderer process environments without requiring a display or GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RendererEnvironment(unittest.TestCase):
    def render_environment(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root/'render-mode'
            state.write_text(mode)
            script = root/'render.sh'
            script.write_text((ROOT/'scripts/render.sh').read_text().replace(
                '/tmp/sti/render-mode', str(state)))
            env = dict(os.environ, STI_DISPLAY='native', STI_WSL_GPU='1',
                       GALLIUM_DRIVER='d3d12', LIBGL_ALWAYS_SOFTWARE='1')
            result = subprocess.run(
                ['bash', str(script), sys.executable, '-c',
                 'import os,json; print(json.dumps(dict(os.environ)))'],
                env=env, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)

    def test_wsl_gpu_uses_d3d12_without_software_override(self):
        env = self.render_environment('gpu')
        self.assertEqual(env['GALLIUM_DRIVER'], 'd3d12')
        self.assertNotIn('LIBGL_ALWAYS_SOFTWARE', env)

    def test_cpu_fallback_removes_d3d12_override(self):
        env = self.render_environment('cpu')
        self.assertEqual(env['LIBGL_ALWAYS_SOFTWARE'], '1')
        self.assertNotIn('GALLIUM_DRIVER', env)


if __name__ == '__main__':
    unittest.main()
