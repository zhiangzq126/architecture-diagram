import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "topology_gif.py"
SPEC = importlib.util.spec_from_file_location("topology_gif_under_test", SCRIPT)
gifmod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gifmod
SPEC.loader.exec_module(gifmod)

try:
    import PIL
    from PIL import Image, ImageChops, ImageDraw
    HAVE_PILLOW = int(PIL.__version__.split(".")[0]) >= 10
except ImportError:
    HAVE_PILLOW = False


def scene_data(count=1, theme="light"):
    edges = [
        {"from": "源<&>", "to": "目标", "label": '请求 <>& "中文"\n第二行', "id": "边一",
         "points": [[40, 52], [56, 30], [78, 27], [105, 80], [126, 68], [140, 52]]},
        {"from": "目标", "to": "源<&>", "label": "反向 / response",
         "points": [[140, 52], [125, 18], [76, 18], [40, 52]]},
        {"from": "源<&>", "to": "源<&>", "label": "回环 ↩",
         "points": [[24, 40], [24, 12], [63, 12], [64, 33], [40, 52]]},
    ]
    return {"width": 180, "height": 110, "theme": theme,
            "text_boxes": [{"x": 80, "y": 45, "width": 26, "height": 19}],
            "nodes": [{"id": "源<&>", "label": '入口 & <API> "中文"',
                       "x": 10, "y": 40, "width": 30, "height": 24},
                      {"id": "目标", "label": "数据库 / DB\n存储", "x": 140,
                       "y": 40, "width": 30, "height": 24}],
            "edges": [copy.deepcopy(edges[i % 3]) for i in range(count)]}


def run_cli(*args):
    return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


class PureTests(unittest.TestCase):
    def test_unicode_and_special_labels_are_not_modified(self):
        raw = scene_data(3)
        before = copy.deepcopy(raw)
        parsed = gifmod.validate_scene(raw)
        self.assertEqual(raw, before)
        self.assertEqual([n.label for n in parsed.nodes], [n["label"] for n in raw["nodes"]])
        self.assertEqual([e.label for e in parsed.edges], [e["label"] for e in raw["edges"]])
        self.assertEqual(parsed.edges[0].id, "边一")
        self.assertIsNone(parsed.edges[1].id)
        self.assertEqual(parsed.edges[2].source, parsed.edges[2].target)

    def test_budgets_and_pause_frames(self):
        for count in (1, 2, 5, 30):
            budget = gifmod.calculate_budget(100, 80, count)
            durations = gifmod.frame_durations(count)
            self.assertEqual(budget.frames, count * 12 + 2)
            self.assertEqual(budget.duration_ms, count * 600 + 800)
            self.assertEqual(budget.peak_palette_pixels, 100 * 80 * budget.frames)
            self.assertEqual(durations, [400] + [50] * (count * 12) + [400])
            self.assertEqual(sum(durations), budget.duration_ms)
        self.assertEqual(gifmod.calculate_budget(3000, 1000, 4).peak_palette_pixels, 150_000_000)
        self.assertEqual(gifmod.calculate_budget(600, 4800, 5).peak_palette_pixels, 178_560_000)

    def test_budget_limits(self):
        for args, error in [((3001, 1000, 1), "single-frame"),
                            ((600, 4900, 5), "palette-frame"),
                            ((500, 1000, 30), "palette-frame"),
                            ((1, 1, 31), "edge_count"),
                            ((1, 1, 0), "edge_count"),
                            ((65536, 1, 1), "65535"),
                            ((True, 2, 1), "width"),
                            ((10.0, 2, 1), "width"),
                            ((2, 0, 1), "height")]:
            with self.subTest(args=args), self.assertRaisesRegex(gifmod.GifError, error):
                gifmod.calculate_budget(*args)
        for count in (0, 31, True):
            with self.assertRaises(gifmod.GifError):
                gifmod.frame_durations(count)

    def test_arc_length_not_sample_index(self):
        path = gifmod.build_path([[0, 0], [1, 0], [1, 0], [1, 10]])
        self.assertEqual(path.length, 11)
        positions = gifmod.frame_positions(path)
        self.assertEqual(len(positions), 12)
        self.assertEqual(positions[1], (1, 0))
        self.assertEqual(positions[6], (1, 5))
        self.assertEqual(positions[-1], (1, 10))
        self.assertEqual(gifmod.point_at_distance(path, -10), (0, 0))
        self.assertEqual(gifmod.point_at_distance(path, 100), (1, 10))
        self.assertEqual(gifmod.path_section(path, 0.5, 3), ((0.5, 0), (1, 0), (1, 2)))

    def test_bends_loops_and_reverse_paths(self):
        points = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        path = gifmod.build_path(points)
        reverse = gifmod.build_path(points[::-1])
        self.assertEqual(path.length, 40)
        for distance, expected in [(0, (0, 0)), (5, (5, 0)), (15, (10, 5)),
                                   (25, (5, 10)), (35, (0, 5)), (40, (0, 0))]:
            self.assertEqual(gifmod.point_at_distance(path, distance), expected)
            self.assertEqual(gifmod.point_at_distance(reverse, 40 - distance), expected)
        self.assertEqual(gifmod.path_section(path, 8, 22), ((8, 0), (10, 0), (10, 10), (8, 10)))
        diagonal = gifmod.build_path([[1, 2], [4, 6]])
        self.assertEqual(diagonal.length, 5)
        self.assertEqual(gifmod.point_at_distance(diagonal, 2.5), (2.5, 4))

    def test_sampled_curve_follows_all_points(self):
        points = [(50 + 30 * math.cos(i / 100), 50 + 30 * math.sin(i / 100))
                  for i in range(201)]
        path = gifmod.build_path(points)
        for index in (0, 11, 79, 147, 200):
            actual = gifmod.point_at_distance(path, path.cumulative[index])
            self.assertAlmostEqual(actual[0], points[index][0])
            self.assertAlmostEqual(actual[1], points[index][1])
        for x, y in gifmod.frame_positions(path):
            self.assertAlmostEqual(math.hypot(x - 50, y - 50), 30, places=3)

    def test_invalid_paths(self):
        for points in ([], [[1, 1]], [[1, 1], [1, 1]], [[1], [2, 3]],
                       [[0, 0], [float("nan"), 2]], [[0, 0], [2, float("inf")]],
                       [[0, 0], [False, 2]], [[0, 0], ["2", 3]],
                       [[0, 0], [10 ** 1000, 2]]):
            with self.subTest(points=str(points)[:80]), self.assertRaises(gifmod.GifError):
                gifmod.build_path(points)
        with self.assertRaisesRegex(gifmod.GifError, "outside"):
            gifmod.build_path([[0, 0], [11, 1]], 10, 10)
        with self.assertRaises(gifmod.GifError):
            gifmod.point_at_distance(gifmod.build_path([[0, 0], [1, 1]]), float("nan"))

    def test_invalid_scenes(self):
        mutations = [
            lambda s: s.update(width=-1), lambda s: s.update(height=1.5),
            lambda s: s.update(theme="auto"), lambda s: s.update(nodes=[]),
            lambda s: s.update(edges=[]), lambda s: s.update(edges=scene_data(31)["edges"]),
            lambda s: s["nodes"].extend([copy.deepcopy(s["nodes"][0])] * 11),
            lambda s: s["nodes"][1].update(id=s["nodes"][0]["id"]),
            lambda s: s["nodes"][0].update(x=float("nan")),
            lambda s: s["nodes"][0].update(height=float("inf")),
            lambda s: s["nodes"][0].update(width=0),
            lambda s: s["nodes"][0].update(x=179),
            lambda s: s["nodes"][0].update(label=123),
            lambda s: s["edges"][0].update(to="missing"),
            lambda s: s["edges"][0].update(points=[]),
            lambda s: s["edges"][0].update(points=[[0, 0], [181, 40]]),
            lambda s: s["edges"][0].update(points=[[0, 0], [float("nan"), 40]]),
            lambda s: s["edges"][0].update(label=None),
            lambda s: s["edges"][0].update(id=False),
            lambda s: s.pop("text_boxes"),
            lambda s: s.update(text_boxes=None),
            lambda s: s.update(text_boxes=[[]]),
            lambda s: s["text_boxes"][0].update(x=-1),
            lambda s: s["text_boxes"][0].update(width=200),
            lambda s: s["text_boxes"][0].update(height=float("nan")),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                data = scene_data()
                mutate(data)
                with self.assertRaises(gifmod.GifError):
                    gifmod.validate_scene(data)
        for data in (None, [], "scene"):
            with self.assertRaises(gifmod.GifError):
                gifmod.validate_scene(data)

    def test_import_and_pure_functions_without_pillow(self):
        code = '''import builtins, json, runpy, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "PIL" or name.startswith("PIL."):
        raise ImportError("Pillow intentionally unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
ns = runpy.run_path(sys.argv[1])
assert ns["calculate_budget"](20, 20, 1).frames == 14
assert ns["build_path"]([[0, 0], [3, 4]]).length == 5
try:
    ns["_pillow"]()
except ns["GifError"] as exc:
    assert "Pillow" in str(exc)
else:
    raise AssertionError("missing Pillow was not detected")
print(json.dumps({"ok": True}))
'''
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(SCRIPT)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True})

    def test_cli_errors_are_single_line_json_without_tracebacks(self):
        for args in ([], ["--help"], ["--scene", "relative.json", "--base", "/missing.png",
                                     "--output", "/missing.gif"]):
            with self.subTest(args=args):
                result = run_cli(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertEqual(result.stderr, "")

    def test_scene_file_size_and_json_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            path.write_text(json.dumps(scene_data(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(gifmod.load_scene(path).nodes[0].id, "源<&>")
            with mock.patch.object(gifmod, "MAX_SCENE_BYTES", 10):
                with self.assertRaisesRegex(gifmod.GifError, "JSON exceeds"):
                    gifmod.load_scene(path)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                gifmod.load_scene(path)


@unittest.skipUnless(HAVE_PILLOW, "Pillow >= 10 unavailable")
class EncodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.scene_path = self.directory / "scene.json"
        self.base_path = self.directory / "base.png"
        self.output_path = self.directory / "output.gif"

    def fixture(self, count=1, theme="light"):
        raw = scene_data(count, theme)
        background = (255, 255, 255) if theme == "light" else (17, 24, 39)
        foreground = (30, 45, 60) if theme == "light" else (230, 240, 250)
        image = Image.new("RGB", (raw["width"], raw["height"]), background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 3, 176, 106), fill=(224, 236, 248) if theme == "light" else (32, 49, 68))
        for edge in raw["edges"]:
            draw.line([tuple(p) for p in edge["points"]], fill=foreground, width=1)
        for node in raw["nodes"]:
            x, y = node["x"], node["y"]
            draw.rectangle((x, y, x + node["width"], y + node["height"]),
                           fill=(210, 225, 232), outline=foreground)
            draw.rectangle((x + 3, y + 4, x + 8, y + 15), fill=(20, 110, 190))
            draw.text((x + 11, y + 6), "A&", fill=foreground)
        draw.rectangle((80, 45, 106, 64), fill=background)
        draw.text((82, 47), "<&>", fill=foreground)
        image.save(self.base_path)
        image.close()
        self.scene_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return gifmod.validate_scene(raw)

    def decode(self, path):
        with Image.open(path) as image:
            result = []
            durations = []
            for index in range(image.n_frames):
                image.seek(index)
                result.append(image.convert("RGB"))
                durations.append(image.info["duration"])
            return result, durations

    def test_real_single_and_multiple_edge_gifs_in_both_themes(self):
        for count, theme in ((1, "light"), (3, "light"), (1, "dark"), (3, "dark"), (30, "light")):
            with self.subTest(count=count, theme=theme):
                scene = self.fixture(count, theme)
                result = gifmod.compose(self.scene_path, self.base_path, self.output_path)
                self.assertEqual(result, {"ok": True, "width": 180, "height": 110,
                                         "frames": count * 12 + 2, "duration_ms": count * 600 + 800,
                                         "fps": 20, "step_frames": 12, "pause_ms": 400,
                                         "edge_count": count, "changed": True,
                                         "peak_palette_pixels": 180 * 110 * (count * 12 + 2)})
                frames, durations = self.decode(self.output_path)
                base, safe = gifmod.prepare_base(self.base_path, scene)
                expected = base.convert("RGB")
                unsafe = ImageChops.invert(safe)
                try:
                    self.assertEqual(durations, [400] + [50] * count * 12 + [400])
                    self.assertEqual(frames[0].tobytes(), expected.tobytes())
                    self.assertEqual(frames[-1].tobytes(), expected.tobytes())
                    for frame in frames:
                        difference = ImageChops.difference(frame, expected)
                        for channel in difference.split():
                            self.assertIsNone(ImageChops.multiply(channel, unsafe).getbbox())
                    for index in range(count):
                        segment = frames[1 + index * 12:1 + (index + 1) * 12]
                        self.assertEqual(len({f.tobytes() for f in (segment[0], segment[6], segment[-1])}), 3)
                        self.assertTrue(all(ImageChops.difference(f, expected).getbbox() for f in segment))
                        self.assertTrue(all(a.tobytes() != b.tobytes() for a, b in zip(segment, segment[1:])))
                    with Image.open(self.output_path) as gif:
                        self.assertEqual(gif.info["loop"], 0)
                    self.assertLessEqual(self.output_path.stat().st_size, gifmod.MAX_OUTPUT_BYTES)
                finally:
                    for frame in frames:
                        frame.close()
                    base.close()
                    safe.close()
                    expected.close()
                    unsafe.close()
                    self.output_path.unlink()

    def test_uniform_palette_and_only_current_endpoint_outlines(self):
        scene = self.fixture(3)
        base, safe = gifmod.prepare_base(self.base_path, scene)
        frames = gifmod.render_frames(scene, base, safe)
        try:
            self.assertEqual(len(frames), 38)
            self.assertTrue(all(frame.mode == "P" for frame in frames))
            self.assertTrue(all(frame.getpalette() == base.getpalette() for frame in frames))
            self.assertLess(base.getextrema()[1], gifmod.BASE_COLORS)
            self.assertEqual(frames[1].getpixel((7, 43)), gifmod.BASE_COLORS)
            self.assertEqual(frames[1].getpixel((173, 43)), gifmod.BASE_COLORS)
            self.assertEqual(frames[25].getpixel((7, 43)), gifmod.BASE_COLORS)
            self.assertEqual(frames[25].getpixel((173, 43)), base.getpixel((173, 43)))
        finally:
            for frame in frames[:-1]:
                frame.close()
            safe.close()

    def test_short_and_closed_paths_keep_physical_frames(self):
        for points in ([[40, 52], [40.01, 52.01]],
                       [[40, 40], [40, 15], [65, 15], [65, 40], [40, 40]]):
            with self.subTest(points=points):
                self.fixture()
                raw = scene_data()
                raw["edges"][0].update(to="源<&>", points=points)
                self.scene_path.write_text(json.dumps(raw), encoding="utf-8")
                result = gifmod.compose(self.scene_path, self.base_path, self.output_path)
                self.assertEqual(result["frames"], 14)
                self.assertEqual(result["duration_ms"], 1400)
                self.output_path.unlink()

    def test_dot_tracks_curve_independently_of_node_outlines(self):
        scene = self.fixture()
        base, safe = gifmod.prepare_base(self.base_path, scene)
        frames = gifmod.render_frames(scene, base, safe)
        try:
            positions = gifmod.frame_positions(scene.edges[0].path)
            for step in (3, 4, 7, 8):
                x, y = positions[step]
                crop = frames[1 + step].crop((int(x) - 5, int(y) - 5, int(x) + 6, int(y) + 6))
                self.assertIn(gifmod.BASE_COLORS + 4 + step % 4,
                              [color for _, color in crop.getcolors(121)])
                crop.close()
        finally:
            for frame in frames[:-1]:
                frame.close()
            safe.close()

    def test_unpaintable_scene_is_rejected_and_cleaned(self):
        self.fixture()
        raw = scene_data()
        raw["nodes"] = [{"id": "all", "label": "覆盖全图", "x": 0, "y": 0,
                         "width": 180, "height": 110}]
        raw["edges"][0].update({"from": "all", "to": "all"})
        self.scene_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(gifmod.GifError, "identical adjacent frames"):
            gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertFalse(self.output_path.exists())

    def test_success_cli_contract(self):
        self.fixture(2)
        result = run_cli("--scene", self.scene_path, "--base", self.base_path, "--output", self.output_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout)["frames"], 26)
        self.assertTrue(self.output_path.exists())

    def test_wrong_base_dimensions_and_format(self):
        self.fixture()
        for format_name, size in (("PNG", (181, 110)), ("JPEG", (180, 110))):
            with self.subTest(format=format_name):
                image = Image.new("RGB", size, "white")
                image.save(self.base_path, format=format_name)
                image.close()
                result = run_cli("--scene", self.scene_path, "--base", self.base_path,
                                 "--output", self.output_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertEqual(result.stderr, "")
                self.assertFalse(self.output_path.exists())

    def test_reject_overwrite_and_dangling_symlink(self):
        self.fixture()
        self.output_path.write_bytes(b"keep existing output")
        with self.assertRaisesRegex(gifmod.GifError, "overwrite"):
            gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertEqual(self.output_path.read_bytes(), b"keep existing output")
        self.output_path.unlink()
        self.output_path.symlink_to(self.directory / "nonexistent")
        with self.assertRaisesRegex(gifmod.GifError, "overwrite"):
            gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertTrue(self.output_path.is_symlink())

    def test_exclusive_creation_race_does_not_remove_other_file(self):
        self.fixture()
        render = gifmod.render_frames
        def raced(*args):
            frames = render(*args)
            self.output_path.write_bytes(b"created by another process")
            return frames
        with mock.patch.object(gifmod, "render_frames", side_effect=raced):
            with self.assertRaises(FileExistsError):
                gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertEqual(self.output_path.read_bytes(), b"created by another process")

    def test_partial_save_failure_cleans_only_own_output(self):
        self.fixture()
        def failed(stream, frames, durations):
            stream.write(b"GIF89a partial")
            raise OSError("simulated save failure")
        with mock.patch.object(gifmod, "encode_gif", side_effect=failed):
            with self.assertRaisesRegex(OSError, "simulated"):
                gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertFalse(self.output_path.exists())

    def test_replaced_output_survives_failure_cleanup(self):
        self.fixture()
        def replaced(stream, frames, durations):
            self.output_path.unlink()
            self.output_path.write_bytes(b"replacement belongs to someone else")
            raise OSError("simulated replacement")
        with mock.patch.object(gifmod, "encode_gif", side_effect=replaced):
            with self.assertRaises(OSError):
                gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertEqual(self.output_path.read_bytes(), b"replacement belongs to someone else")

    def test_size_limit_and_failed_verification_clean_output(self):
        self.fixture()
        with mock.patch.object(gifmod, "MAX_OUTPUT_BYTES", 100):
            with self.assertRaisesRegex(gifmod.GifError, "40 MiB"):
                gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertFalse(self.output_path.exists())
        with mock.patch.object(gifmod, "verify_gif", side_effect=gifmod.GifError("verification failed")):
            with self.assertRaisesRegex(gifmod.GifError, "verification failed"):
                gifmod.compose(self.scene_path, self.base_path, self.output_path)
        self.assertFalse(self.output_path.exists())

    def test_verifier_rejects_merged_frames_wrong_duration_loop_and_dimensions(self):
        scene = self.fixture()
        base, safe = gifmod.prepare_base(self.base_path, scene)
        frames = gifmod.render_frames(scene, base, safe)
        try:
            for corruption in ("frames", "duration", "loop", "dimensions", "segment"):
                with self.subTest(corruption=corruption):
                    stream = io.BytesIO()
                    current = list(frames)
                    durations = gifmod.frame_durations(1)
                    if corruption == "frames":
                        current = [frames[0], *frames[2:]]
                        durations = [400, *durations[2:]]
                    elif corruption == "duration":
                        durations[3] = 100
                    elif corruption == "segment":
                        current[7] = current[1]
                    gifmod.encode_gif(stream, current, durations)
                    if corruption in ("loop", "dimensions"):
                        payload = bytearray(stream.getvalue())
                        if corruption == "loop":
                            offset = payload.index(b"NETSCAPE2.0") + 13
                            payload[offset:offset + 2] = b"\x01\x00"
                        else:
                            payload[6:8] = (181).to_bytes(2, "little")
                        stream = io.BytesIO(payload)
                    with self.assertRaises(gifmod.GifError):
                        gifmod.verify_gif(stream, scene, base)
        finally:
            for frame in frames[:-1]:
                frame.close()
            safe.close()


if __name__ == "__main__":
    unittest.main()
