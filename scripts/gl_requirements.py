"""Decide whether a glxinfo report can run Gazebo's ogre2 renderer.

Usage: gl_requirements.py [--hardware] GLXINFO_OUTPUT_FILE

Exit 0 and print a summary when usable; exit 1 with the reason otherwise.
Ogre-Next's GL3Plus backend copies textures with glCopyImageSubData, which needs
OpenGL 4.3 or GL_ARB_copy_image. Drivers without either abort Gazebo at startup
(for example older Mesa on WSL's Direct3D 12 driver), so a renderer that merely
exists isn't sufficient. Standard library only.
"""
import re
import sys

SOFTWARE = re.compile(r'llvmpipe|softpipe|lavapipe|software rasterizer|Microsoft Basic Render', re.I)
MINIMUM = (4, 3)
EXTENSION = 'GL_ARB_copy_image'


def evaluate(report, require_hardware=False):
    """Return (usable, explanation) for full glxinfo output."""
    renderer = re.search(r'OpenGL renderer string:\s*(.+)', report)
    if not renderer:
        return False, 'no OpenGL context could be created'
    name = renderer.group(1).strip()
    if require_hardware and SOFTWARE.search(name):
        return False, f'{name} is a software renderer'
    # Ogre requests a core profile; compatibility is the fallback some drivers offer.
    version = (re.search(r'OpenGL core profile version string:\s*(\d+)\.(\d+)', report)
               or re.search(r'OpenGL version string:\s*(\d+)\.(\d+)', report))
    if not version:
        return False, f'{name} reported no OpenGL version'
    found = (int(version.group(1)), int(version.group(2)))
    summary = f'{name}, OpenGL {found[0]}.{found[1]}'
    if found >= MINIMUM:
        return True, summary
    if re.search(rf'\b{EXTENSION}\b', report):
        return True, f'{summary} with {EXTENSION}'
    return False, (f'{summary} lacks {EXTENSION}; Gazebo needs OpenGL '
                   f'{MINIMUM[0]}.{MINIMUM[1]} or that extension')


def main(arguments):
    hardware = '--hardware' in arguments
    paths = [argument for argument in arguments if argument != '--hardware']
    if len(paths) != 1:
        raise SystemExit(__doc__)
    with open(paths[0], encoding='utf-8', errors='replace') as stream:
        usable, explanation = evaluate(stream.read(), hardware)
    print(explanation)
    return 0 if usable else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
