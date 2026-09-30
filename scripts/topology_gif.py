"""Compose an immutable topology background and sampled paths into a GIF."""
from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys

FPS = 20
STEP_FRAMES = 12
PAUSE_MS = 400
MAX_NODES = 12
MAX_EDGES = 30
MAX_FRAME_PIXELS = 3_000_000
MAX_PALETTE_PIXELS = 180_000_000
MAX_OUTPUT_BYTES = 40 * 1024 * 1024
MAX_SCENE_BYTES = 32 * 1024 * 1024
BASE_COLORS = 248


class GifError(ValueError):
    pass


@dataclass(frozen=True)
class Budget:
    frames: int
    duration_ms: int
    peak_palette_pixels: int


@dataclass(frozen=True)
class SampledPath:
    points: tuple
    cumulative: tuple

    @property
    def length(self):
        return self.cumulative[-1]


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str
    id: str | None
    path: SampledPath


@dataclass(frozen=True)
class Scene:
    width: int
    height: int
    theme: str
    nodes: tuple
    edges: tuple
    text_boxes: tuple
    budget: Budget


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise GifError(f"{name} must be an integer >= {minimum}")
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GifError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise GifError(f"{name} must be a finite number") from None
    if not math.isfinite(value):
        raise GifError(f"{name} must be a finite number")
    return value


def _text(value, name, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value):
        raise GifError(f"{name} must be {'a nonempty' if nonempty else 'a'} string")
    return value


def calculate_budget(width, height, edge_count):
    _integer(width, "width")
    _integer(height, "height")
    _integer(edge_count, "edge_count")
    if edge_count > MAX_EDGES:
        raise GifError(f"edge_count exceeds {MAX_EDGES}")
    pixels = width * height
    if pixels > MAX_FRAME_PIXELS:
        raise GifError(f"single-frame pixels exceed {MAX_FRAME_PIXELS}")
    if width > 65535 or height > 65535:
        raise GifError("GIF dimensions must not exceed 65535 pixels")
    frames = edge_count * STEP_FRAMES + 2
    palette_pixels = pixels * frames
    if palette_pixels > MAX_PALETTE_PIXELS:
        raise GifError(f"total palette-frame pixels exceed {MAX_PALETTE_PIXELS}")
    return Budget(frames, edge_count * 600 + 2 * PAUSE_MS, palette_pixels)


def frame_durations(edge_count):
    _integer(edge_count, "edge_count")
    if edge_count > MAX_EDGES:
        raise GifError(f"edge_count exceeds {MAX_EDGES}")
    return [PAUSE_MS] + [1000 // FPS] * (edge_count * STEP_FRAMES) + [PAUSE_MS]


def build_path(points, width=None, height=None):
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        raise GifError("path must contain at least two points")
    kept, cumulative = [], []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise GifError("each path point must contain x and y")
        x, y = (_number(v, "path coordinate") for v in point)
        if ((width is not None and not 0 <= x <= width)
                or (height is not None and not 0 <= y <= height)):
            raise GifError("path coordinate is outside the scene")
        point = (x, y)
        if kept and point == kept[-1]:
            continue
        distance = math.hypot(x - kept[-1][0], y - kept[-1][1]) if kept else 0.0
        total = (cumulative[-1] if cumulative else 0.0) + distance
        if not math.isfinite(total):
            raise GifError("path length must be finite")
        kept.append(point)
        cumulative.append(total)
    if len(kept) < 2 or cumulative[-1] <= 0:
        raise GifError("path must have positive arc length")
    return SampledPath(tuple(kept), tuple(cumulative))


def point_at_distance(path, distance):
    distance = _number(distance, "distance")
    distance = max(0.0, min(path.length, distance))
    if distance <= 0:
        return path.points[0]
    if distance >= path.length:
        return path.points[-1]
    right = bisect.bisect_right(path.cumulative, distance)
    left = right - 1
    ratio = ((distance - path.cumulative[left])
             / (path.cumulative[right] - path.cumulative[left]))
    a, b = path.points[left], path.points[right]
    return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)


def path_section(path, start, end):
    start = max(0.0, min(path.length, _number(start, "start")))
    end = max(start, min(path.length, _number(end, "end")))
    lo = bisect.bisect_right(path.cumulative, start)
    hi = bisect.bisect_left(path.cumulative, end)
    return (point_at_distance(path, start), *path.points[lo:hi],
            point_at_distance(path, end))


def frame_positions(path):
    return tuple(point_at_distance(path, path.length * i / (STEP_FRAMES - 1))
                 for i in range(STEP_FRAMES))


def validate_scene(data):
    if not isinstance(data, dict):
        raise GifError("scene must be an object")
    raw_nodes, raw_edges = data.get("nodes"), data.get("edges")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= MAX_NODES:
        raise GifError(f"nodes must contain 1..{MAX_NODES} entries")
    if not isinstance(raw_edges, list) or not 1 <= len(raw_edges) <= MAX_EDGES:
        raise GifError(f"edges must contain 1..{MAX_EDGES} entries")
    width, height = data.get("width"), data.get("height")
    budget = calculate_budget(width, height, len(raw_edges))
    theme = data.get("theme")
    if theme not in ("light", "dark"):
        raise GifError("theme must be light or dark")
    nodes, edges, ids = [], [], set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise GifError("node must be an object")
        node_id = _text(raw.get("id"), "node id", True)
        if node_id in ids:
            raise GifError(f"duplicate node id: {node_id}")
        ids.add(node_id)
        label = _text(raw.get("label"), "node label")
        x, y, w, h = (_number(raw.get(k), f"node {k}")
                       for k in ("x", "y", "width", "height"))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise GifError("node bounds must fit inside the scene")
        nodes.append(Node(node_id, label, x, y, w, h))
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise GifError("edge must be an object")
        source = _text(raw.get("from"), "edge from", True)
        target = _text(raw.get("to"), "edge to", True)
        if source not in ids or target not in ids:
            raise GifError("edge references an unknown endpoint")
        label = _text(raw.get("label"), "edge label")
        edge_id = _text(raw["id"], "edge id", True) if "id" in raw else None
        edges.append(Edge(source, target, label, edge_id,
                          build_path(raw.get("points"), width, height)))
    raw_boxes = data.get("text_boxes")
    if not isinstance(raw_boxes, list):
        raise GifError("text_boxes must be an array")
    text_boxes = []
    for box in raw_boxes:
        if not isinstance(box, dict):
            raise GifError("text box must be an object")
        x, y, w, h = (_number(box.get(k), f"text box {k}")
                       for k in ("x", "y", "width", "height"))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise GifError("text box bounds must fit inside the scene")
        text_boxes.append((x, y, w, h))
    return Scene(width, height, theme, tuple(nodes), tuple(edges), tuple(text_boxes), budget)


def _absolute(path, name):
    path = Path(path)
    if not path.is_absolute():
        raise GifError(f"{name} must be an absolute path")
    return path


def load_scene(path):
    path = _absolute(path, "scene")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SCENE_BYTES + 1)
    if len(raw) > MAX_SCENE_BYTES:
        raise GifError(f"scene JSON exceeds {MAX_SCENE_BYTES} bytes")
    return validate_scene(json.loads(raw))


def _pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, GifImagePlugin
        import PIL
    except ImportError:
        raise GifError("Pillow >= 10 is required for GIF encoding") from None
    if int(PIL.__version__.split(".")[0]) < 10:
        raise GifError("Pillow >= 10 is required for GIF encoding")
    return Image, ImageChops, ImageDraw, GifImagePlugin


def prepare_base(path, scene):
    Image, _, ImageDraw, _ = _pillow()
    with Image.open(path) as source:
        if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
            raise GifError("base must be a static PNG")
        if source.size != (scene.width, scene.height):
            raise GifError("base PNG dimensions do not match the scene")
        source.load()
        if "A" in source.getbands() or "transparency" in source.info:
            rgba = source.convert("RGBA")
            rgb = Image.new("RGB", source.size, "white" if scene.theme == "light" else "#111827")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            rgba.close()
        else:
            rgb = source.convert("RGB")
    safe = Image.new("L", rgb.size, 255)
    draw = ImageDraw.Draw(safe)
    for node in scene.nodes:
        draw.rectangle((math.floor(node.x), math.floor(node.y),
                        math.ceil(node.x + node.width), math.ceil(node.y + node.height)), fill=0)
    for x, y, w, h in scene.text_boxes:
        draw.rectangle((math.floor(x) - 1, math.floor(y) - 1,
                        math.ceil(x + w) + 1, math.ceil(y + h) + 1), fill=0)
    base = rgb.quantize(colors=BASE_COLORS, dither=Image.Dither.NONE)
    rgb.close()
    colors = ([(8, 145, 178), (13, 148, 136), (2, 132, 199), (14, 165, 163),
               (0, 119, 255), (0, 155, 205), (0, 137, 235), (0, 173, 180)]
              if scene.theme == "light" else
              [(103, 232, 249), (45, 212, 191), (56, 189, 248), (34, 211, 238),
               (210, 255, 255), (165, 243, 252), (153, 246, 228), (186, 230, 253)])
    palette = (base.getpalette()[:BASE_COLORS * 3] + [0] * (BASE_COLORS * 3))[:BASE_COLORS * 3]
    base.putpalette(palette + [channel for color in colors for channel in color])
    return base, safe


def render_frames(scene, base, safe):
    _, ImageChops, ImageDraw, _ = _pillow()
    unsafe = ImageChops.invert(safe)
    lookup = {node.id: node for node in scene.nodes}
    frames = [base]
    try:
        for edge in scene.edges:
            for step, position in enumerate(frame_positions(edge.path)):
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                border_color = BASE_COLORS + step % 4
                head_color = BASE_COLORS + 4 + step % 4
                for node_id in {edge.source, edge.target}:
                    node = lookup[node_id]
                    draw.rectangle((node.x - 3, node.y - 3,
                                    node.x + node.width + 3, node.y + node.height + 3),
                                   outline=border_color, width=1)
                distance = edge.path.length * step / (STEP_FRAMES - 1)
                trail = path_section(edge.path, distance - min(16, edge.path.length * 0.18), distance)
                draw.line(trail, fill=border_color, width=5)
                x, y = position
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=head_color)
                frame.paste(base, (0, 0), unsafe)
                frames.append(frame)
        frames.append(base)
        return frames
    except BaseException:
        for frame in frames[1:]:
            frame.close()
        raise
    finally:
        unsafe.close()


class _LimitedWriter:
    def __init__(self, stream):
        self.stream = stream
        self.written = 0

    def write(self, data):
        if self.written + len(data) > MAX_OUTPUT_BYTES:
            raise GifError(f"GIF exceeds {MAX_OUTPUT_BYTES} bytes (40 MiB limit)")
        result = self.stream.write(data)
        self.written += result
        return result


def encode_gif(stream, frames, durations):
    _, ImageChops, _, GifImagePlugin = _pillow()
    writer = _LimitedWriter(stream)
    header, _ = GifImagePlugin.getheader(frames[0], info={"loop": 0, "optimize": False})
    for block in header:
        writer.write(block)
    previous = None
    for frame, duration in zip(frames, durations):
        box = ImageChops.difference(previous, frame).getbbox() if previous is not None else None
        if previous is not None and box is None:
            raise GifError("animation contains identical adjacent frames")
        region = frame.crop(box) if box else frame
        try:
            # Explicit image blocks avoid Pillow save_all's identical-frame merging.
            for block in GifImagePlugin.getdata(region, offset=box[:2] if box else (0, 0),
                                               duration=duration, disposal=1,
                                               include_color_table=False, optimize=False):
                writer.write(block)
        finally:
            if region is not frame:
                region.close()
        previous = frame
    writer.write(b";")
    stream.flush()


def _digest(image):
    rgb = image.convert("RGB")
    try:
        return hashlib.sha256(rgb.tobytes()).digest()
    finally:
        rgb.close()


def verify_gif(stream, scene, base):
    Image, _, _, _ = _pillow()
    stream.seek(0, os.SEEK_END)
    if stream.tell() > MAX_OUTPUT_BYTES:
        raise GifError("encoded GIF exceeds 40 MiB")
    stream.seek(0)
    expected = frame_durations(len(scene.edges))
    baseline = _digest(base)
    hashes, durations = [], []
    with Image.open(stream) as gif:
        if gif.format != "GIF" or gif.size != (scene.width, scene.height):
            raise GifError("encoded GIF dimensions or format are incorrect")
        if gif.n_frames != scene.budget.frames:
            raise GifError("encoded GIF physical frame count is incorrect")
        if gif.info.get("loop") != 0:
            raise GifError("encoded GIF loop must be 0 (infinite)")
        for index, duration in enumerate(expected):
            gif.seek(index)
            actual = gif.info.get("duration")
            if actual != duration:
                raise GifError(f"encoded GIF frame {index} duration is incorrect")
            durations.append(actual)
            hashes.append(_digest(gif))
    if hashes[0] != baseline or hashes[-1] != baseline:
        raise GifError("encoded GIF pauses must show the unmodified base")
    if any(a == b for a, b in zip(hashes, hashes[1:])):
        raise GifError("encoded GIF contains identical adjacent frames")
    for edge_index in range(len(scene.edges)):
        segment = hashes[1 + edge_index * STEP_FRAMES:1 + (edge_index + 1) * STEP_FRAMES]
        if baseline in segment or len({segment[0], segment[STEP_FRAMES // 2], segment[-1]}) != 3:
            raise GifError(f"edge {edge_index} has no distinct first/middle/last animation")
    if sum(durations) != scene.budget.duration_ms:
        raise GifError("encoded GIF total duration is incorrect")
    return {"ok": True, "width": scene.width, "height": scene.height,
            "frames": len(hashes), "duration_ms": sum(durations), "fps": FPS,
            "step_frames": STEP_FRAMES, "pause_ms": PAUSE_MS,
            "edge_count": len(scene.edges), "changed": True,
            "peak_palette_pixels": scene.budget.peak_palette_pixels}


def _remove_owned(path, identity):
    try:
        stat = path.lstat()
        if (stat.st_dev, stat.st_ino) == identity:
            path.unlink()
    except OSError:
        pass


def compose(scene_path, base_path, output_path):
    scene_path = _absolute(scene_path, "scene")
    base_path = _absolute(base_path, "base")
    output_path = _absolute(output_path, "output")
    if os.path.lexists(output_path):
        raise GifError("output already exists; refusing to overwrite")
    scene = load_scene(scene_path)
    base, safe = prepare_base(base_path, scene)
    frames, identity = [], None
    try:
        frames = render_frames(scene, base, safe)
        with output_path.open("xb+") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            encode_gif(stream, frames, frame_durations(len(scene.edges)))
            result = verify_gif(stream, scene, base)
            stat = output_path.lstat()
            if (stat.st_dev, stat.st_ino) != identity:
                raise GifError("output path changed during encoding")
        return result
    except BaseException:
        if identity is not None:
            _remove_owned(output_path, identity)
        raise
    finally:
        for frame in frames[1:-1]:
            frame.close()
        base.close()
        safe.close()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise GifError(message)


def main(argv=None):
    try:
        parser = _Parser(add_help=False)
        parser.add_argument("--scene", required=True)
        parser.add_argument("--base", required=True)
        parser.add_argument("--output", required=True)
        args = parser.parse_args(argv)
        result = compose(args.scene, args.base, args.output)
        status = 0
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or type(exc).__name__}
        status = 1
    except KeyboardInterrupt:
        result = {"ok": False, "error": "interrupted"}
        status = 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    sys.exit(main())
