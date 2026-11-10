"""Explain a simulator exit caused by the graphics stack.

Usage: diagnose_render.py LOG_FILE...

Prints one line starting with "STI graphics failure (<renderer>)" when a known
graphics signature is found, so the launcher can retry GPU failures on CPU and
students see a cause instead of windows silently closing. Prints nothing and
exits 0 otherwise. Standard library only.
"""
from pathlib import Path
import re
import sys

MARKER = 'STI graphics failure'
# Ordered: the first matching signature is the most specific explanation.
SIGNATURES = (
    (re.compile(r'D3D12: Removing Device'),
     'the Windows GPU driver reset the Direct3D 12 device'),
    (re.compile(r'Ogre::UnimplementedException|GL3PlusTextureGpu::copyTo'),
     "the OpenGL driver lacks a feature Gazebo's renderer needs"),
    (re.compile(r'Failed to create OpenGL context|QGLXContext: Failed|Unable to create the rendering window'),
     'an application could not create an OpenGL context'),
    (re.compile(r'OGRE EXCEPTION'),
     'the 3D renderer (Ogre) raised an error'),
)


def diagnose(text, renderer):
    for pattern, cause in SIGNATURES:
        match = pattern.search(text)
        if match:
            if renderer == 'gpu':
                advice = 'Retry with: python3 sim.py up --mode cpu'
            else:
                advice = 'Software rendering also failed; send the .sti-diagnostics folder to course staff'
            return f'{MARKER} ({renderer}): {cause} [{match.group(0)}]. {advice}'
    return None


def main(paths):
    try:
        renderer = Path('/tmp/sti/render-mode').read_text().strip() or 'unknown'
    except OSError:
        renderer = 'unknown'
    text = ''.join(Path(p).read_text(errors='replace') for p in paths if Path(p).exists())
    result = diagnose(text, renderer)
    if result:
        print(result)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
