"""Stylized Gun Pack generator.

Builds 5 stylized weapons x 5 skins in Blender, renders a contact sheet,
and exports every asset as GLB, FBX and OBJ plus a .blend source file.

Run:  python3 build_pack.py            (uses the `bpy` module)
  or: blender -b -P build_pack.py      (inside a Blender install)
"""
import math
import os

import bpy

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXPORT_DIR = os.path.join(OUT, "exports")
RENDER_DIR = os.path.join(OUT, "renders")

CHUNK = 1.6
TURN = math.radians(-28)  # 3/4 view for renders

ROLES = ("body", "accent", "grip", "metal", "glow")

# skin -> role -> (rgb, metallic, roughness, emission strength)
SKINS = {
    "Classic": {
        "body": ((0.06, 0.065, 0.075), 0.3, 0.45, 0),
        "accent": ((0.18, 0.22, 0.28), 0.2, 0.5, 0),
        "grip": ((0.28, 0.12, 0.05), 0.0, 0.6, 0),
        "metal": ((0.12, 0.12, 0.13), 0.9, 0.35, 0),
        "glow": ((1.0, 0.45, 0.08), 0.0, 0.5, 0),
    },
    "Gilded": {
        "body": ((1.0, 0.68, 0.18), 1.0, 0.25, 0),
        "accent": ((0.92, 0.9, 0.86), 0.0, 0.4, 0),
        "grip": ((0.03, 0.03, 0.035), 0.0, 0.5, 0),
        "metal": ((0.7, 0.45, 0.12), 1.0, 0.3, 0),
        "glow": ((1.0, 0.85, 0.3), 0.0, 0.5, 6),
    },
    "Neon": {
        "body": ((0.02, 0.02, 0.03), 0.2, 0.4, 0),
        "accent": ((0.35, 0.05, 0.8), 0.0, 0.4, 0),
        "grip": ((0.08, 0.02, 0.18), 0.0, 0.5, 0),
        "metal": ((0.15, 0.15, 0.18), 0.9, 0.3, 0),
        "glow": ((0.05, 0.85, 1.0), 0.0, 0.5, 8),
    },
    "Inferno": {
        "body": ((0.03, 0.025, 0.025), 0.2, 0.4, 0),
        "accent": ((0.75, 0.04, 0.03), 0.0, 0.4, 0),
        "grip": ((0.25, 0.02, 0.02), 0.0, 0.5, 0),
        "metal": ((0.15, 0.13, 0.13), 0.9, 0.3, 0),
        "glow": ((1.0, 0.35, 0.02), 0.0, 0.5, 10),
    },
    "Frost": {
        "body": ((0.85, 0.9, 0.95), 0.0, 0.3, 0),
        "accent": ((0.2, 0.6, 1.0), 0.0, 0.25, 0),
        "grip": ((0.03, 0.08, 0.25), 0.0, 0.5, 0),
        "metal": ((0.6, 0.65, 0.7), 1.0, 0.25, 0),
        "glow": ((0.5, 0.9, 1.0), 0.0, 0.5, 6),
    },
}


# ---------------------------------------------------------------- materials
def make_materials():
    mats = {}
    for skin, roles in SKINS.items():
        for role, (rgb, metallic, rough, emit) in roles.items():
            m = bpy.data.materials.new(f"M_{skin}_{role}")
            m.use_nodes = True
            p = m.node_tree.nodes["Principled BSDF"]
            p.inputs["Base Color"].default_value = (*rgb, 1)
            p.inputs["Metallic"].default_value = metallic
            p.inputs["Roughness"].default_value = rough
            if emit:
                p.inputs["Emission Color"].default_value = (*rgb, 1)
                p.inputs["Emission Strength"].default_value = emit
            m.diffuse_color = (*rgb, 1)
            mats[(skin, role)] = m
    return mats


# ---------------------------------------------------------------- primitives
PARTS = []
MATS = {}


def _finish(obj, role, bevel):
    obj.data.materials.append(MATS[("Classic", role)])
    if bevel:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = "ANGLE"
    PARTS.append(obj)
    return obj


def box(size, loc, role, rot=(0, 0, 0), bevel=0.004):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc,
                                    rotation=[math.radians(r) for r in rot])
    obj = bpy.context.active_object
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    return _finish(obj, role, bevel)


def cyl(radius, length, loc, role, verts=16, bevel=0.002, axis="X"):
    rot = {"X": (0, math.pi / 2, 0), "Y": (math.pi / 2, 0, 0), "Z": (0, 0, 0)}[axis]
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius,
                                        depth=length, location=loc, rotation=rot)
    return _finish(bpy.context.active_object, role, bevel)


def cone(r1, r2, length, loc, role, verts=16):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2,
                                    depth=length, location=loc,
                                    rotation=(0, math.pi / 2, 0))
    return _finish(bpy.context.active_object, role, 0.002)


def ring(major, minor, loc, role):
    """Trigger guard: a torus standing in the XZ plane."""
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                     major_segments=20, minor_segments=8,
                                     location=loc, rotation=(math.pi / 2, 0, 0))
    return _finish(bpy.context.active_object, role, 0)


def side_strips(length, z, x=0.0, half_width=0.02, height=0.005):
    for s in (1, -1):
        box((length, 0.003, height), (x, s * half_width, z), "glow", bevel=0)


# ---------------------------------------------------------------- weapons
def build_pistol():
    box((0.19, 0.032, 0.036), (0.0, 0, 0.066), "body")
    box((0.15, 0.028, 0.022), (-0.01, 0, 0.038), "accent")
    box((0.038, 0.028, 0.10), (-0.065, 0, -0.012), "grip", rot=(0, -14, 0))
    box((0.02, 0.03, 0.012), (-0.08, 0, -0.06), "accent", rot=(0, -14, 0))
    ring(0.017, 0.0035, (-0.016, 0, 0.016), "metal")
    box((0.006, 0.006, 0.018), (-0.018, 0, 0.02), "metal", bevel=0.001)
    cyl(0.008, 0.012, (0.098, 0, 0.07), "metal")
    box((0.01, 0.006, 0.008), (0.085, 0, 0.088), "metal", bevel=0.001)
    box((0.012, 0.02, 0.008), (-0.085, 0, 0.088), "metal", bevel=0.001)
    for i in range(3):
        box((0.004, 0.034, 0.024), (-0.07 + i * 0.01, 0, 0.068), "metal", bevel=0)
    side_strips(0.11, 0.07, x=0.02, half_width=0.0165, height=0.004)


def build_smg():
    box((0.22, 0.042, 0.062), (0, 0, 0.05), "body")
    box((0.17, 0.026, 0.012), (0.0, 0, 0.087), "metal", bevel=0.002)
    box((0.045, 0.032, 0.032), (0.02, 0, 0.108), "accent")
    cyl(0.009, 0.006, (0.045, 0, 0.11), "glow", bevel=0)
    cyl(0.012, 0.11, (0.165, 0, 0.06), "metal")
    cyl(0.018, 0.035, (0.23, 0, 0.06), "metal", verts=8)
    box((0.08, 0.046, 0.03), (0.085, 0, 0.035), "accent")
    box((0.036, 0.034, 0.095), (-0.05, 0, -0.025), "grip", rot=(0, -12, 0))
    box((0.032, 0.026, 0.13), (0.04, 0, -0.045), "accent", rot=(0, -6, 0))
    ring(0.016, 0.0035, (-0.005, 0, 0.0), "metal")
    # skeleton stock
    box((0.13, 0.012, 0.014), (-0.175, 0, 0.065), "metal", bevel=0.002)
    box((0.13, 0.012, 0.014), (-0.17, 0, 0.0), "metal", rot=(0, 14, 0), bevel=0.002)
    box((0.018, 0.04, 0.09), (-0.245, 0, 0.03), "grip")
    side_strips(0.14, 0.055, x=-0.01, half_width=0.0215)


def build_shotgun():
    cyl(0.017, 0.40, (0.17, 0, 0.075), "metal")
    cyl(0.014, 0.30, (0.12, 0, 0.044), "metal")
    cyl(0.021, 0.02, (0.37, 0, 0.075), "accent")
    box((0.13, 0.05, 0.042), (0.14, 0, 0.042), "grip")
    for i in range(4):
        box((0.006, 0.052, 0.044), (0.10 + i * 0.025, 0, 0.042), "accent", bevel=0)
    box((0.15, 0.048, 0.075), (-0.07, 0, 0.06), "body")
    box((0.04, 0.038, 0.09), (-0.13, 0, -0.01), "grip", rot=(0, -18, 0))
    ring(0.017, 0.0035, (-0.075, 0, 0.012), "metal")
    box((0.22, 0.042, 0.065), (-0.25, 0, 0.035), "grip", rot=(0, -6, 0))
    box((0.02, 0.046, 0.085), (-0.365, 0, 0.022), "accent", rot=(0, -6, 0))
    box((0.012, 0.008, 0.012), (0.35, 0, 0.098), "glow", bevel=0)
    side_strips(0.11, 0.07, x=-0.07, half_width=0.0245)


def build_sniper():
    box((0.20, 0.042, 0.058), (0, 0, 0.06), "body")
    box((0.17, 0.046, 0.046), (0.18, 0, 0.06), "accent")
    cyl(0.011, 0.30, (0.31, 0, 0.068), "metal")
    box((0.05, 0.034, 0.026), (0.47, 0, 0.068), "metal", bevel=0.003)
    # scope
    cyl(0.016, 0.20, (0.02, 0, 0.13), "metal")
    cyl(0.024, 0.05, (0.13, 0, 0.13), "accent")
    cyl(0.021, 0.04, (-0.09, 0, 0.13), "accent")
    cyl(0.02, 0.004, (0.157, 0, 0.13), "glow", bevel=0)
    cyl(0.017, 0.004, (-0.111, 0, 0.13), "glow", bevel=0)
    cyl(0.009, 0.03, (0.02, 0, 0.152), "metal", axis="Z")
    for x in (-0.03, 0.07):
        box((0.014, 0.03, 0.036), (x, 0, 0.1), "metal", bevel=0.002)
    box((0.04, 0.036, 0.1), (-0.06, 0, -0.01), "grip", rot=(0, -16, 0))
    box((0.03, 0.028, 0.07), (0.04, 0, 0.0), "accent")
    ring(0.017, 0.0035, (-0.02, 0, 0.012), "metal")
    box((0.27, 0.044, 0.085), (-0.24, 0, 0.04), "grip", rot=(0, -4, 0))
    box((0.12, 0.046, 0.02), (-0.24, 0, 0.09), "accent", rot=(0, -4, 0))
    box((0.02, 0.05, 0.1), (-0.38, 0, 0.03), "accent", rot=(0, -4, 0))
    side_strips(0.13, 0.06, x=0.18, half_width=0.0235)


def build_launcher():
    cyl(0.045, 0.56, (0, 0, 0.1), "body", verts=20)
    cyl(0.052, 0.05, (0.27, 0, 0.1), "accent", verts=20)
    cyl(0.052, 0.04, (-0.27, 0, 0.1), "accent", verts=20)
    cyl(0.035, 0.004, (-0.292, 0, 0.1), "glow", bevel=0)
    for x in (-0.12, 0.0, 0.12):
        cyl(0.047, 0.012, (x, 0, 0.1), "glow", verts=20, bevel=0)
    cone(0.04, 0.0, 0.11, (0.35, 0, 0.1), "accent", verts=20)
    cyl(0.04, 0.03, (0.3, 0, 0.1), "metal", verts=20)
    box((0.04, 0.036, 0.1), (-0.05, 0, 0.01), "grip", rot=(0, -12, 0))
    box((0.035, 0.034, 0.085), (0.12, 0, 0.02), "grip", rot=(0, -6, 0))
    ring(0.017, 0.0035, (-0.01, 0, 0.03), "metal")
    box((0.06, 0.022, 0.035), (0.05, 0, 0.16), "metal", bevel=0.003)
    box((0.012, 0.024, 0.024), (0.08, 0, 0.16), "glow", bevel=0)
    box((0.16, 0.05, 0.02), (-0.15, 0, 0.045), "accent")


WEAPONS = {
    "Pistol": build_pistol,
    "SMG": build_smg,
    "Shotgun": build_shotgun,
    "Sniper": build_sniper,
    "Launcher": build_launcher,
}


# ---------------------------------------------------------------- assembly
def assemble(name):
    """Apply modifiers, join PARTS into one mesh, smooth and unwrap it."""
    bpy.ops.object.select_all(action="DESELECT")
    for p in PARTS:
        p.select_set(True)
    bpy.context.view_layer.objects.active = PARTS[0]
    bpy.ops.object.convert(target="MESH")
    bpy.ops.object.join()
    obj = bpy.context.active_object
    # chunky "toy" proportions: thicker side-to-side than a real firearm
    obj.scale = (1, CHUNK, 1)
    bpy.ops.object.transform_apply(scale=True)
    obj.name = obj.data.name = name
    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(35))
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.location = (0, 0, 0)
    PARTS.clear()
    return obj


def reskin(obj, skin):
    for slot in obj.material_slots:
        role = slot.material.name.rsplit("_", 1)[1]
        slot.material = MATS[(skin, role)]


def export(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    saved = obj.location.copy()
    obj.location = (0, 0, 0)
    for sub in ("glb", "fbx", "obj"):
        os.makedirs(os.path.join(EXPORT_DIR, sub), exist_ok=True)
    stem = os.path.join(EXPORT_DIR, "{}", obj.name)
    bpy.ops.export_scene.gltf(filepath=stem.format("glb") + ".glb",
                              use_selection=True, export_format="GLB")
    bpy.ops.export_scene.fbx(filepath=stem.format("fbx") + ".fbx",
                             use_selection=True, apply_scale_options="FBX_SCALE_ALL",
                             path_mode="COPY", embed_textures=True)
    bpy.ops.wm.obj_export(filepath=stem.format("obj") + ".obj",
                          export_selected_objects=True, export_materials=True)
    obj.location = saved


# ---------------------------------------------------------------- render
def setup_render(width, height, ortho_scale, center):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.012, 0.014, 0.022, 1)
    bg.inputs["Strength"].default_value = 1.0

    cx, cz = center
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho_scale
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (cx, -6, cz + 1.2)
    cam.rotation_euler = (math.radians(79), 0, 0)
    scene.camera = cam

    for name, loc, rot, energy, size in (
        ("Key", (cx - 2, -4, cz + 4), (50, 0, -30), 900, 4),
        ("Fill", (cx + 3, -3, cz), (90, 0, 45), 300, 5),
        ("Rim", (cx, 4, cz + 3), (-60, 0, 0), 600, 5),
    ):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.size = size
        lo = bpy.data.objects.new(name, ld)
        lo.location = loc
        lo.rotation_euler = [math.radians(r) for r in rot]
        scene.collection.objects.link(lo)


def render(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_thumbnails(assets, center):
    """One square thumbnail per asset, framed on the grid centre."""
    scene = bpy.context.scene
    cam = scene.camera
    scene.render.resolution_x = scene.render.resolution_y = 800
    scene.cycles.samples = 32
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 90
    thumbs = os.path.join(RENDER_DIR, "thumbnails")
    os.makedirs(thumbs, exist_ok=True)
    homes = {o.name: o.location.copy() for o in assets}
    for obj in assets:
        for other in assets:
            other.hide_render = other is not obj
        obj.location = (center[0], 0, center[1])
        cam.data.ortho_scale = max(obj.dimensions.x, obj.dimensions.z) * 1.25
        render(os.path.join(thumbs, obj.name + ".jpg"))
        obj.location = homes[obj.name]
    for obj in assets:
        obj.hide_render = False


# ---------------------------------------------------------------- main
def main():
    global MATS
    bpy.ops.wm.read_factory_settings(use_empty=True)
    os.makedirs(RENDER_DIR, exist_ok=True)
    MATS = make_materials()

    col_gap, row_gap = 1.0, 0.45
    assets = []
    for row, (wname, builder) in enumerate(WEAPONS.items()):
        builder()
        base = assemble(f"SGP_{wname}_Classic")
        for col, skin in enumerate(SKINS):
            if skin == "Classic":
                obj = base
            else:
                obj = base.copy()
                obj.data = base.data.copy()
                obj.name = obj.data.name = f"SGP_{wname}_{skin}"
                bpy.context.scene.collection.objects.link(obj)
                reskin(obj, skin)
            obj.location = (col * col_gap, 0, -row * row_gap)
            assets.append(obj)

    for obj in assets:
        export(obj)

    n_cols, n_rows = len(SKINS), len(WEAPONS)
    center = ((n_cols - 1) * col_gap / 2, -(n_rows - 1) * row_gap / 2)
    for obj in assets:
        obj.rotation_euler.z = TURN
    setup_render(2400, 1350, 5.6, center)
    render(os.path.join(RENDER_DIR, "contact_sheet.png"))
    render_thumbnails(assets, center)
    for obj in assets:
        obj.rotation_euler.z = 0

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "stylized_gun_pack.blend"),
                                compress=True)
    for obj in assets:
        tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
        print(f"{obj.name:28s} tris={tris}")


if __name__ == "__main__":
    main()
