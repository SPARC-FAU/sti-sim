"""Geometry conversion and integration checks that don't require ROS or Docker."""
import importlib.util
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_hospital', ROOT / 'scripts/prepare_hospital.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def fixture(path, *, mode=4, scale=None):
    # Four vertices form a floor and a vertical wall in glTF Y-up coordinates.
    binary = struct.pack('<12f6H', 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 1, 0, 0, 1, 2, 0, 3, 2)
    data = {
        'asset': {'version': '2.0'},
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 48},
                        {'buffer': 0, 'byteOffset': 48, 'byteLength': 6},
                        {'buffer': 0, 'byteOffset': 54, 'byteLength': 6}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                      {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'},
                      {'bufferView': 2, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'mode': mode}]},
                   {'primitives': [{'attributes': {'POSITION': 0}, 'indices': 2}]}],
        'nodes': [{'name': 'Floor', 'mesh': 0}, {'name': 'Wall', 'mesh': 1},
                  {'name': 'Ceiling', 'mesh': 0}],
        'scenes': [{'nodes': [0, 1, 2]}], 'scene': 0,
    }
    if scale:
        data['nodes'][1]['scale'] = scale
    metadata = json.dumps(data).encode()
    metadata += b' ' * (-len(metadata) % 4)
    binary += b'\0' * (-len(binary) % 4)
    content = struct.pack('<II', len(metadata), 0x4e4f534a) + metadata
    content += struct.pack('<II', len(binary), 0x004e4942) + binary
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 12+len(content)) + content)


class ConversionTests(unittest.TestCase):
    def test_axis_conversion_and_parent_transform(self):
        matrix = converter.multiply(converter.transform({'translation': [10, 20, 30]}),
                                    converter.transform({'translation': [1, 2, 3], 'scale': [2, 2, 2]}))
        self.assertEqual(converter.position(matrix, [1, 2, 3]), (13, -39, 26))

    def test_floor_normalization_roof_removal_and_collision_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.glb'
            fixture(source)
            output = Path(directory) / 'output'
            report = converter.convert(source, output)
            self.assertEqual(report['source_origin_z_up'], [1, -1, 0])
            self.assertNotIn('Ceiling', report['bounds'])
            self.assertEqual(report['triangles'], 2)
            visual_faces = [s for s in (output/'hospital.obj').read_text().splitlines() if s.startswith('f ')]
            collision_faces = [s for s in (output/'hospital_collision.obj').read_text().splitlines() if s.startswith('f ')]
            self.assertEqual(len(visual_faces), 2)
            self.assertEqual(len(collision_faces), 1)
            self.assertIn('mat_default', (output/'hospital.mtl').read_text())

    def test_non_triangle_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.glb'
            fixture(source, mode=1)
            with self.assertRaisesRegex(ValueError, 'triangles'):
                list(converter.GLB(source).primitives())

    def test_exported_collision_and_visual_faces_have_unit_normals(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.glb'
            fixture(source)
            output = Path(directory) / 'output'
            report = converter.convert(source, output)
            for name in ('hospital.obj', 'hospital_collision.obj'):
                check = converter.validate_obj(output/name)
                self.assertEqual(check['normals'], check['vertices'])
                self.assertEqual(report['mesh_checks'][name], check)
                for line in (output/name).read_text().splitlines():
                    if line.startswith('f '):
                        self.assertTrue(all(len(c.split('/')) == 3 and c.split('/')[2]
                                            for c in line.split()[1:]))

    def test_regression_missing_normals_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'old_collision.obj'
            path.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            with self.assertRaisesRegex(ValueError, 'no normal'):
                converter.validate_obj(path)

    def test_degenerate_faces_do_not_produce_zero_normals(self):
        normals, indices = converter.mesh_normals([(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0)],
                                                  [0, 1, 2, 0, 1, 3])
        self.assertEqual(indices, [0, 1, 3])
        for normal in normals:
            self.assertAlmostEqual(math.hypot(*normal), 1)

    def test_complete_hospital_exports_valid_dart_normals(self):
        with tempfile.TemporaryDirectory() as directory:
            report = converter.convert(ROOT/'worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb', directory)
            for name, check in report['mesh_checks'].items():
                self.assertEqual(check['normals'], check['vertices'], name)
                self.assertGreater(check['triangles'], 300000)

    def test_physics_smoke_test_rejects_crashes_even_with_zero_exit_status(self):
        spec = importlib.util.spec_from_file_location('validate_world', ROOT/'scripts/validate_world.py')
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        result = smoke.subprocess.CompletedProcess(['ign'], 0,
            'World [hospital] initialized\n[Err] missing normals\nStack trace\nSegmentation fault', '')
        with patch.object(smoke.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'physics smoke test failed'):
                smoke.validate('/tmp/hospital.sdf')

    def test_mirrored_nodes_reverse_winding(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.glb'
            fixture(source, scale=[-1, 1, 1])
            parts = list(converter.GLB(source).primitives())
            self.assertEqual(parts[1][2], [0, 2, 3])

    def test_hospital_floor_dimensions_and_spawn_clearance(self):
        source = ROOT / 'worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb'
        parts = list(converter.GLB(source).primitives())
        floor = [points[i] for name, points, indices, *_ in parts if name == 'Floor' for i in indices]
        low = [min(p[i] for p in floor) for i in range(3)]
        high = [max(p[i] for p in floor) for i in range(3)]
        origin = [(low[0]+high[0])/2, (low[1]+high[1])/2, high[2]]
        self.assertAlmostEqual(high[0]-low[0], 16, places=3)
        self.assertAlmostEqual(high[1]-low[1], 13, places=3)
        # Check horizontal cross-sections against the spawn footprint. This catches
        # centring/axis mistakes that put the robot inside a wall or equipment.
        point = (1.0, 0.0)
        clearance = float('inf')
        for name, points, indices, *_ in parts:
            if name == 'Floor':
                continue
            points = [tuple(p[i]-origin[i] for i in range(3)) for p in points]
            for start in range(0, len(indices), 3):
                triangle = [points[i] for i in indices[start:start+3]]
                for height in (0.2, 0.8, 1.4):
                    intersections = []
                    for a, b in zip(triangle, triangle[1:]+triangle[:1]):
                        if (a[2] > height) != (b[2] > height):
                            t = (height-a[2])/(b[2]-a[2])
                            intersections.append(tuple(a[i]+t*(b[i]-a[i]) for i in range(2)))
                    if len(intersections) != 2:
                        continue
                    a, b = intersections
                    delta = (b[0]-a[0], b[1]-a[1])
                    length = delta[0]**2+delta[1]**2
                    t = max(0, min(1, sum((point[i]-a[i])*delta[i] for i in range(2))/length)) if length else 0
                    clearance = min(clearance, math.dist(point, tuple(a[i]+t*delta[i] for i in range(2))))
        self.assertGreater(clearance, 0.6, f'Spawn has only {clearance:.3f} m clearance')

    def test_hospital_world_has_no_embedded_dummy_robot(self):
        world = ET.parse(ROOT/'worlds/hospital.sdf')
        self.assertEqual(world.find('world').get('name'), 'hospital')
        models = world.findall('.//model')
        self.assertEqual([m.get('name') for m in models], ['hospital_arena'])
        self.assertEqual(world.findtext('.//collision[@name="floor"]/geometry/plane/size'), '16 13')


if __name__ == '__main__':
    unittest.main()
