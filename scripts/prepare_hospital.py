#!/usr/bin/env python3
"""Convert the supplied static GLB hospital to OBJ/MTL and a Fortress SDF world.

No third-party Python dependencies. This is a deliberately limited glTF 2 loader:
embedded buffer/images, triangle meshes, node transforms, base colour materials
and UVs. Unsupported compression, skins, animations or sparse accessors fail
explicitly. PBR effects are approximated by traditional OBJ materials.
"""
import argparse
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import struct

IDENTITY = [[int(r == c) for c in range(4)] for r in range(4)]
FORMATS = {5120: 'b', 5121: 'B', 5122: 'h', 5123: 'H', 5125: 'I', 5126: 'f'}
WIDTHS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def transform(node):
    if 'matrix' in node:
        return [[node['matrix'][c * 4 + r] for c in range(4)] for r in range(4)]
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    scale = node.get('scale', [1, 1, 1])
    result = [
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w, 0],
        [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w, 0],
        [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y, 0],
        [0, 0, 0, 1],
    ]
    for r in range(3):
        for c in range(3):
            result[r][c] *= scale[c]
        result[r][3] = node.get('translation', [0, 0, 0])[r]
    return result


def position(matrix, vertex):
    p = [sum(matrix[r][c] * vertex[c] for c in range(3)) + matrix[r][3] for r in range(3)]
    # glTF Y-up -> Gazebo Z-up, right handed, metre units.
    return (p[0], -p[2], p[1])


def mesh_normals(points, indices):
    """Area-weighted vertex normals and nondegenerate triangles in world axes.

    Fortress's OBJ loader does not manufacture normals. DART requires one for
    every face corner it expands into a vertex, even for static collision meshes.
    """
    accumulated = [[0.0, 0.0, 0.0] for _ in points]
    adjacent = [None] * len(points)
    valid = []
    for offset in range(0, len(indices), 3):
        triangle = indices[offset:offset+3]
        a, b, c = (points[index] for index in triangle)
        u = [b[i]-a[i] for i in range(3)]
        v = [c[i]-a[i] for i in range(3)]
        normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        length = math.hypot(*normal)
        if not math.isfinite(length):
            raise ValueError('Non-finite hospital geometry')
        if length <= 1e-12:
            continue
        valid.extend(triangle)
        for index in triangle:
            for axis in range(3):
                accumulated[index][axis] += normal[axis]
            adjacent[index] = normal
    normals = []
    for index, normal in enumerate(accumulated):
        if math.hypot(*normal) <= 1e-12:
            # Opposite faces can cancel; use an incident face rather than a zero
            # vector. Unused vertices aren't read by Gazebo but still get a unit normal.
            normal = adjacent[index] or (0.0, 0.0, 1.0)
        length = math.hypot(*normal)
        normals.append(tuple(value / length for value in normal))
    return normals, valid


def validate_obj(path):
    """Reject OBJ output that Fortress/DART cannot safely consume."""
    vertices = normals = faces = 0
    normal_lengths = []
    with Path(path).open() as stream:
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == 'v':
                vertices += 1
            elif fields[0] == 'vn':
                normals += 1
                normal_lengths.append(math.hypot(*(float(v) for v in fields[1:])))
            elif fields[0] == 'f':
                if len(fields) != 4:
                    raise ValueError(f'{path}: expected triangle faces')
                for corner in fields[1:]:
                    refs = corner.split('/')
                    if len(refs) != 3 or not refs[2]:
                        raise ValueError(f'{path}: face corner has no normal: {corner}')
                    if not 0 < int(refs[0]) <= vertices or not 0 < int(refs[2]) <= normals:
                        raise ValueError(f'{path}: invalid vertex/normal reference: {corner}')
                faces += 1
    if not faces or normals != vertices or any(not math.isfinite(n) or abs(n-1) > 1e-5 for n in normal_lengths):
        raise ValueError(f'{path}: expected nonempty geometry with one unit normal per vertex')
    return {'vertices': vertices, 'normals': normals, 'triangles': faces}


class GLB:
    def __init__(self, path):
        content = Path(path).read_bytes()
        magic, version, length = struct.unpack_from('<4sII', content)
        if magic != b'glTF' or version != 2 or length != len(content):
            raise ValueError('Expected a valid glTF 2 binary file')
        offset, chunks = 12, {}
        while offset < length:
            size, kind = struct.unpack_from('<II', content, offset)
            chunks[kind] = content[offset + 8:offset + 8 + size]
            offset += 8 + size
        self.data = json.loads(chunks[0x4e4f534a])
        self.binary = chunks[0x004e4942]
        if self.data.get('animations') or self.data.get('skins'):
            raise ValueError('Only static hospital scenes are supported')
        if len(self.data['buffers']) != 1 or 'uri' in self.data['buffers'][0]:
            raise ValueError('Expected one embedded binary buffer')

    def view(self, index):
        view = self.data['bufferViews'][index]
        if view.get('buffer', 0) != 0 or view.get('extensions'):
            raise ValueError('Compressed/external buffer views are unsupported')
        start = view.get('byteOffset', 0)
        return self.binary[start:start + view['byteLength']]

    @lru_cache(maxsize=None)
    def accessor(self, index):
        accessor = self.data['accessors'][index]
        if 'sparse' in accessor or accessor.get('normalized'):
            raise ValueError('Sparse/normalized mesh accessors are unsupported')
        view = self.data['bufferViews'][accessor['bufferView']]
        data = self.view(accessor['bufferView'])
        fmt = '<' + FORMATS[accessor['componentType']] * WIDTHS[accessor['type']]
        stride = view.get('byteStride', struct.calcsize(fmt))
        offset = accessor.get('byteOffset', 0)
        return [struct.unpack_from(fmt, data, offset + i * stride) for i in range(accessor['count'])]

    def primitives(self):
        def visit(index, parent):
            node = self.data['nodes'][index]
            matrix = multiply(parent, transform(node))
            name = node.get('name', f'node_{index}')
            # Remove the roof to make an overhead teaching view possible.
            if name in ('Ceiling', 'WallsTop'):
                return
            if 'mesh' in node:
                for primitive in self.data['meshes'][node['mesh']]['primitives']:
                    if primitive.get('mode', 4) != 4 or primitive.get('extensions'):
                        raise ValueError(f'{name}: only uncompressed triangles are supported')
                    points = [position(matrix, p) for p in self.accessor(primitive['attributes']['POSITION'])]
                    indices = [p[0] for p in self.accessor(primitive['indices'])] if 'indices' in primitive else list(range(len(points)))
                    if len(indices) % 3 or any(i >= len(points) or i < 0 for i in indices):
                        raise ValueError(f'{name}: invalid triangle indices')
                    determinant = (matrix[0][0]*(matrix[1][1]*matrix[2][2]-matrix[1][2]*matrix[2][1])
                                   - matrix[0][1]*(matrix[1][0]*matrix[2][2]-matrix[1][2]*matrix[2][0])
                                   + matrix[0][2]*(matrix[1][0]*matrix[2][1]-matrix[1][1]*matrix[2][0]))
                    if determinant < 0:
                        for start in range(0, len(indices), 3):
                            indices[start+1], indices[start+2] = indices[start+2], indices[start+1]
                    uv_index = primitive['attributes'].get('TEXCOORD_0')
                    uvs = self.accessor(uv_index) if uv_index is not None else None
                    yield name, points, indices, uvs, primitive.get('material', -1)
            for child in node.get('children', []):
                yield from visit(child, matrix)
        scene = self.data['scenes'][self.data.get('scene', 0)]
        for index in scene['nodes']:
            yield from visit(index, IDENTITY)


def write_materials(glb, output):
    lines = ['# Base colours/textures from supplied GLB. PBR shading approximated.']
    for i, material in enumerate([*glb.data.get('materials', []), {}]):
        name = f'mat_{i}' if i < len(glb.data.get('materials', [])) else 'mat_default'
        pbr = material.get('pbrMetallicRoughness', {})
        r, g, b, alpha = pbr.get('baseColorFactor', [0.8, 0.8, 0.8, 1])
        lines += [f'newmtl {name}', f'Kd {r} {g} {b}', 'Ka 0.2 0.2 0.2', 'Ks 0 0 0', f'd {alpha}']
        texture = pbr.get('baseColorTexture')
        if texture:
            if texture.get('texCoord', 0) != 0 or texture.get('extensions'):
                raise ValueError('Only untransformed TEXCOORD_0 base textures are supported')
            image = glb.data['images'][glb.data['textures'][texture['index']]['source']]
            suffix = {'image/png': 'png', 'image/jpeg': 'jpg'}[image['mimeType']]
            filename = f'texture_{i}.{suffix}'
            (output / filename).write_bytes(glb.view(image['bufferView']))
            lines.append(f'map_Kd {filename}')
        lines.append('')
    (output / 'hospital.mtl').write_text('\n'.join(lines))


def convert(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    glb = GLB(source)
    primitives = list(glb.primitives())
    floor = [points[i] for name, points, indices, *_ in primitives if name == 'Floor' for i in indices]
    if not floor:
        raise ValueError('Named Floor geometry is required to establish origin/height')
    minimum = [min(p[i] for p in floor) for i in range(3)]
    maximum = [max(p[i] for p in floor) for i in range(3)]
    # Place the centre of the floor at x=y=0 and its top at z=0.
    origin = [(minimum[0] + maximum[0])/2, (minimum[1] + maximum[1])/2, maximum[2]]
    write_materials(glb, output)
    vertex_offset, uv_offset, triangles, dropped = 1, 1, 0, 0
    bounds = {}
    with (output / 'hospital.obj').open('w') as obj, (output / 'hospital_collision.obj').open('w') as collision:
        obj.write('# Metres; Z-up; floor centred at origin\nmtllib hospital.mtl\n')
        collision.write('# Static collision geometry with explicit normals\nmtllib hospital.mtl\nusemtl mat_default\n')
        for number, (name, points, indices, uvs, material) in enumerate(primitives):
            shifted = [tuple(p[i] - origin[i] for i in range(3)) for p in points]
            normals, valid_indices = mesh_normals(shifted, indices)
            dropped += (len(indices) - len(valid_indices)) // 3
            indices = valid_indices
            bounds.setdefault(name, []).append([[min(p[i] for p in shifted) for i in range(3)],
                                                [max(p[i] for p in shifted) for i in range(3)]])
            obj.write(f'o part_{number}\n')
            for p in shifted:
                vertex = 'v ' + ' '.join(f'{v:.10g}' for v in p) + '\n'
                obj.write(vertex)
                collision.write(vertex)
            for normal in normals:
                record = 'vn ' + ' '.join(f'{v:.9g}' for v in normal) + '\n'
                obj.write(record)
                collision.write(record)
            if uvs:
                for u, v in uvs:
                    obj.write(f'vt {u:.7g} {1-v:.7g}\n')
            obj.write(f'usemtl mat_{material}\n' if material >= 0 else 'usemtl mat_default\n')
            for start in range(0, len(indices), 3):
                triangle = indices[start:start+3]
                # Reflective node scales reverse winding; preserve outward faces.
                # Y-up -> Z-up conversion itself is a rotation, not a reflection.
                face = [f'{vertex_offset+i}/{uv_offset+i}/{vertex_offset+i}' if uvs
                        else f'{vertex_offset+i}//{vertex_offset+i}' for i in triangle]
                obj.write('f ' + ' '.join(face) + '\n')
                if name != 'Floor':
                    collision.write('f ' + ' '.join(f'{vertex_offset+i}//{vertex_offset+i}' for i in triangle) + '\n')
                triangles += 1
            vertex_offset += len(points)
            if uvs:
                uv_offset += len(uvs)
    mesh_checks = {name: validate_obj(output / name) for name in ('hospital.obj', 'hospital_collision.obj')}
    report = {'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
              'source_origin_z_up': origin, 'floor_size_m': [maximum[i]-minimum[i] for i in range(2)],
              'triangles': triangles, 'dropped_degenerate_triangles': dropped,
              'mesh_checks': mesh_checks, 'bounds': bounds,
              'limitations': ['Static environment, including doors and equipment',
                              'Ceiling and WallsTop omitted for overhead viewing',
                              'PBR materials approximated by OBJ/MTL base colours/textures']}
    (output / 'conversion.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = convert(args.source, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != 'bounds'}, indent=2))
