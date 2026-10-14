import struct

import pytest

from baymax.artifacts import ArtifactRegistry
from baymax.creation3d.classifier import RequestClassifier
from baymax.creation3d.gltf import export_glb
from baymax.creation3d.modify import Modifier
from baymax.creation3d.planner import Planner, PlanningError, validate_composite_parts
from baymax.creation3d.registry import GeneratorRegistry
from baymax.creation3d.store import ModelStore
from baymax.creation3d.validate import load_spec, parse_glb, validate_glb
from baymax.db import Database

REG = GeneratorRegistry()
CLS = RequestClassifier(REG)
PLAN = Planner(REG)
MOD = Modifier(PLAN)


def build(prompt, detail=None):
    c = CLS.classify(prompt)
    assert c.status == "supported", prompt
    if detail:
        c.detail = detail
    return PLAN.plan(c)


ALL_ALIASES = sorted(REG.aliases())


@pytest.mark.parametrize("alias", ALL_ALIASES)
def test_every_alias_builds_valid_glb_of_its_type(alias):
    spec = build(f"make a {alias}")
    gen, _ = REG.aliases()[alias]
    assert spec.generator == gen.id
    data = export_glb(spec)
    rep = validate_glb(data, gen.required(spec.params), spec.object_type)
    assert rep.ok, (alias, rep.errors)
    assert rep.stats["triangles"] > 0 and rep.stats["vertices"] > 0


@pytest.mark.parametrize("gen", REG.all(), ids=lambda g: g.id)
@pytest.mark.parametrize("detail", [1, 2, 3])
def test_detail_levels(gen, detail):
    alias = next(iter(gen.aliases))
    spec = build(f"a {alias}", detail)
    rep = validate_glb(export_glb(spec), gen.required(spec.params), spec.object_type)
    assert rep.ok, rep.errors


def test_more_detail_means_more_geometry():
    low = validate_glb(export_glb(build("a car", 1))).stats["triangles"]
    high = validate_glb(export_glb(build("a car", 3))).stats["triangles"]
    assert high > low


@pytest.mark.parametrize("prompt", ["a giraffe", "create a 3d model of a toaster", "make a spaceship station orbiting mars?", "model an octopus"])
def test_unsupported_is_not_substituted(prompt):
    c = CLS.classify(prompt)
    if c.status == "supported":  # "spaceship" is a rocket alias: allowed, it really is that object
        assert c.alias in prompt
        return
    assert c.unknown_object
    with pytest.raises(PlanningError):
        PLAN.plan(c)


def test_classifier_extracts_attributes():
    c = CLS.classify("create a detailed blue SUV with black wheels")
    assert c.generator.id == "vehicle.car" and c.preset["style"] == "suv" and c.detail == 3
    spec = PLAN.plan(c)
    assert spec.params["body_color"] == "#1e5bd8" and spec.params["wheel_color"] == "#151515"
    c = CLS.classify("a house with 3 floors and a red roof")
    spec = PLAN.plan(c)
    assert spec.params["floors"] == 3 and spec.params["roof_color"] == "#c62828"
    c = CLS.classify("a hexacopter")
    assert PLAN.plan(c).params["arms"] == 6
    big = PLAN.plan(CLS.classify("a car with big wheels")).params
    normal = PLAN.plan(CLS.classify("a car")).params
    assert big["wheel_radius"] > normal["wheel_radius"] and big["length"] == normal["length"]


def test_object_type_follows_variant():
    assert build("a vase").object_type == "vase"
    assert build("a school bus").object_type == "bus"
    assert build("a cardboard box").object_type == "box"


def test_car_semantics_in_file():
    spec = build("a red sports car")
    rep = validate_glb(export_glb(spec))
    roles = rep.stats["roles"]
    assert roles["wheel"] == 4 and roles["body"] >= 2 and roles["window"] >= 2
    lo, hi = rep.stats["bounds"]["min"], rep.stats["bounds"]["max"]
    assert 3.5 < hi[2] - lo[2] < 5.5 and lo[1] > -0.01  # car-sized, sitting on the ground


def test_modifications():
    spec = build("a car")
    r = MOD.parse(spec, "Make the body red.")
    s2 = MOD.apply(spec, r.operations)
    assert s2.params["body_color"] == "#c62828" and s2.version == spec.version + 1
    r = MOD.parse(s2, "Increase the wheel size.")
    s3 = MOD.apply(s2, r.operations)
    assert s3.params["wheel_radius"] > s2.params["wheel_radius"]
    r = MOD.parse(s3, "Make the model more detailed.")
    assert MOD.apply(s3, r.operations).detail == s3.detail + 1
    r = MOD.parse(s3, "Change the camera angle to the top view")
    assert r.operations == [{"op": "camera", "view": "top"}] and not r.changed
    r = MOD.parse(s3, "Export this as GLB.")
    assert {"op": "export", "format": "glb"} in r.operations
    r = MOD.parse(s3, "make it metallic")
    s4 = MOD.apply(s3, r.operations)
    assert all(m.metallic == 0.9 for m in s4.materials.values())
    r = MOD.parse(s3, "please do the thing with the flux capacitor")
    assert r.clarification


def test_count_and_pose_modifications():
    drone = build("a drone")
    s = MOD.apply(drone, MOD.parse(drone, "use 6 arms").operations)
    assert s.params["arms"] == 6 and s.roles()["arm"] == 6
    house = build("a house")
    s = MOD.apply(house, MOD.parse(house, "add a floor").operations)
    assert s.params["floors"] == house.params["floors"] + 1
    arm = build("a robotic arm")
    s = MOD.apply(arm, MOD.parse(arm, "raise the arm and open the gripper").operations)
    assert s.params["shoulder_angle"] < arm.params["shoulder_angle"] and s.params["gripper_open"] == 1.0
    sofa = build("a sofa")
    s = MOD.apply(sofa, MOD.parse(sofa, "make it taller").operations)
    assert s.params["seat_height"] > sofa.params["seat_height"]


def test_store_versions_undo_export_reload(tmp_path):
    reg = ArtifactRegistry(Database(tmp_path / "db"), tmp_path)
    store = ModelStore(tmp_path / "artifacts", reg, PLAN)
    saved = store.create(build("a wooden chair"))
    assert saved.report.ok and saved.path.exists()
    aid = saved.artifact.id
    s2 = MOD.apply(saved.spec, MOD.parse(saved.spec, "make the seat blue").operations)
    store.save_modified(aid, s2)
    assert store.versions(aid) == [1, 2]
    loaded = store.load(aid)  # reopened from model.glb
    assert loaded.spec.params["seat_color"] == "#1e5bd8" and loaded.report.ok
    undone = store.undo(aid)
    assert undone.spec.version == 1 and store.load(aid).spec.params["seat_color"] != "#1e5bd8"
    with pytest.raises(ValueError):
        store.undo(aid)
    path, rep = store.export(aid)
    assert rep.ok and path.exists() and load_spec(path.read_bytes()).object_type == "chair"


def test_validator_detects_corruption():
    data = bytearray(export_glb(build("a bottle")))
    assert not validate_glb(b"nope" + bytes(data[4:])).ok
    assert not validate_glb(bytes(data[:-10])).ok  # truncated
    gltf, _ = parse_glb(bytes(data))
    # corrupt an index: write a huge value into the first index of the first mesh
    acc = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["indices"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    json_len = struct.unpack_from("<I", data, 12)[0]
    bin_start = 12 + 8 + json_len + 8
    fmt = "<H" if acc["componentType"] == 5123 else "<I"
    struct.pack_into(fmt, data, bin_start + view["byteOffset"], 60000)
    rep = validate_glb(bytes(data))
    assert not rep.ok and any("index out of range" in e for e in rep.errors)


def test_validator_detects_wrong_object_and_missing_parts():
    data = export_glb(build("a chair"))
    assert not validate_glb(data, object_type="car").ok
    rep = validate_glb(data, required_roles={"wheel": 4})
    assert not rep.ok and "wheel" in rep.errors[0]


def test_composite_validation_rejects_bad_parts():
    with pytest.raises(PlanningError):
        validate_composite_parts([{"primitive": "nuke", "size": [1, 1, 1]}])
    with pytest.raises(PlanningError):
        validate_composite_parts([{"primitive": "box", "size": [1, 1]}])
    with pytest.raises(PlanningError):
        validate_composite_parts([{"primitive": "box", "size": [1, 1, 1]}] * 100)
    parts = validate_composite_parts([{"name": "Body", "primitive": "sphere", "size": [1, 1, 1], "position": [0, 1, 0], "color": "red"},
                                      {"name": "leg", "primitive": "cylinder", "size": [0.1, 1e9, 0.1]}])
    assert parts[1]["size"][1] == 100  # clamped
    spec = PLAN.plan_composite("a giraffe", "Giraffe", [{"name": "neck", "primitive": "capsule", "size": [0.3, 2, 0.3], "position": [0, 2, 0]},
                                                         {"name": "body", "primitive": "rounded_box", "size": [1, 1, 2], "position": [0, 1.5, 0]}], "giraffe")
    assert spec.approximation and "approximation" in spec.title
    assert validate_glb(export_glb(spec), PLAN.generator_for(spec).required(spec.params), "giraffe").ok


def test_compound_instruction_targets_each_part():
    spec = build("a red sports car")
    r = MOD.parse(spec, "make the body blue and increase the wheel size")
    s2 = MOD.apply(spec, r.operations)
    assert s2.params["body_color"] == "#1e5bd8"
    assert s2.params["wheel_color"] == spec.params["wheel_color"]  # wheels keep their colour
    assert s2.params["wheel_radius"] > spec.params["wheel_radius"]
    r = MOD.parse(spec, "paint the roof green, then frobnicate the flux")
    assert r.operations and "Skipped" in r.message
