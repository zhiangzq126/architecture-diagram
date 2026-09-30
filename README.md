# Architecture Diagram

**English** | [简体中文](readme-zh.md)

**Implementation version: 0.4.0 · Documentation revision: 2026-09-30 · Distribution: local/private**

A self-contained Qoder Skill package for creating interactive HTML diagrams, editable DrawIO files, PNG previews, and GIF animations. It combines a private Archify snapshot, DrawIO tooling, and read-only local icon resolution behind one task interface.

This documentation update does not change the rendering code. The package is independent of the original projects and older installed Skills; it does not automatically track upstream changes.

## Capabilities

| Diagram type | Interactive HTML | DrawIO + PNG | GIF |
| --- | --- | --- | --- |
| Architecture | Supported | Shared JSON geometry or independent DrawIO | Topology animation |
| Workflow | Supported | Shared JSON geometry or independent DrawIO | Topology animation |
| Sequence | Supported | Independently authored DrawIO only | Not supported |
| Dataflow | Supported | Independently authored DrawIO only | Not supported |
| Lifecycle | Supported | Independently authored DrawIO only | Not supported |
| UML / specialized network diagrams | Not supported as these task types | Native DrawIO | Not supported |
| Fixed three-stage template | Not supported | Not supported | Template animation |

- **HTML** embeds its SVG, viewer, and resolved icons. It supports light/dark themes, pan/zoom, search, relationship focus, and authored story chapters. Exports include PNG, JPEG, WebP, and SVG; trace-enabled WebM depends on the browser's recording support.
- **DrawIO** remains editable. PNG preview is enabled by default, including one preview per page for multipage inputs.
- **Shared geometry** reuses the architecture/workflow model, node IDs, business labels, edge directions, core geometry, and icon bindings. Workflow also retains lanes, groups, and orthogonal routes. It does not promise pixel-identical rendering or synchronization after manual DrawIO edits.
- **Topology GIF** animates the actual architecture/workflow SVG paths. **Template GIF** is a separate fixed-layout renderer, not a fallback for arbitrary diagrams.
- **Icons** are optional. A missing, ambiguous, or pending-review icon leaves the business node, label, and relationships intact. Unsafe assets are rejected and reported.

## Package layout

```text
architecture-diagram/
├── SKILL.md                 Qoder agent instructions
├── README.md                English guide
├── readme-zh.md              Chinese guide
├── manifest.json            Version, provenance, and file hashes
├── scripts/                 Task entry point, preflight, adapters, packaging
├── modules/
│   ├── archify/             Typed models and HTML rendering
│   ├── drawio/              DrawIO validation and template GIF tooling
│   └── icons/               Packaged icon engine and schemas
├── assets/icon-pack/        Local catalog and SVG snapshot
├── examples/                Native inputs and task examples
├── references/              Detailed integration contracts
├── tests/                   Unit and real-render acceptance tests
└── licenses/                License evidence and unresolved review items
```

The complete implementation is this directory. Original repositories are not runtime dependencies. Historical rendered outputs, acceptance evidence, and the separately supplied design/function/usage/backlog documents live outside the Skill directory and are not included in this Skill ZIP. System runtimes and a root `.venv` are not bundled.

## Requirements and preflight

Requirements depend on the requested output. No `npm install` is needed for the packaged Archify runtime.

| Operation | Required local tools |
| --- | --- |
| Task entry point / icon lookup | Python ≥3.9 |
| HTML generation | Python ≥3.9 and Node.js ≥18 |
| DrawIO validation | Python ≥3.9 and Node.js ≥18 |
| DrawIO PNG preview | Above tools plus draw.io Desktop CLI |
| Template GIF | Python ≥3.9, Pillow ≥10, and a usable CJK font |
| Topology GIF | Template GIF requirements plus Node.js ≥18 and Chrome/Chromium |
| Automated HTML browser acceptance | Existing compatible Chrome/Chromium |
| Package verification / ZIP build | Python ≥3.9 and POSIX filesystem APIs |

Real rendering was exercised on macOS. The package does not claim a fully validated Windows/Linux, mobile-device, or cross-browser matrix. Browser executable discovery is not a rendering test.

Set the package path and choose an existing Python interpreter. `node` must be discoverable on `PATH`; the wrapper does not read a `NODE` variable. If a shell version-manager shim is broken, point the current process at the existing executable directory rather than modifying shell configuration.

```sh
SKILL_DIR="/absolute/path/to/architecture-diagram"
PYTHON="python3"
/bin/sh "$SKILL_DIR/scripts/check-runtime.sh"
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor archify
```

For other branches, run the corresponding preflight before generation:

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor drawio
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor gif
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor topology-gif
```

Optional environment variables point to **existing local files**:

| Variable | Purpose |
| --- | --- |
| `DRAWIO_BIN` | draw.io Desktop executable |
| `ARCHIFY_CHROME` | Chrome/Chromium executable |
| `ARCHITECTURE_DIAGRAM_FONT` | Local CJK font file; `CJK_FONT` is also recognized |

Preflight does not download or install anything. Missing required dependencies block the affected branch. To inspect a dependency installation plan without executing it:

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" install-plan pillow
```

Installation is a separate action requiring explicit approval of the current plan, including its source, commands, and environment impact. There is no automatic package-manager installation, privilege escalation, telemetry, or publication in the unified generation workflow.

## Quick start: interactive HTML

### 1. Prepare a separate working directory

Replace the placeholder paths. The parent of `WORK_DIR` must already exist; choose a new directory outside the Skill. `mkdir` intentionally fails if that directory already exists.

```sh
WORK_DIR="/absolute/path/to/new-diagram-demo"
mkdir "$WORK_DIR"
cp "$SKILL_DIR/examples/service.architecture.json" "$WORK_DIR/service.architecture.json"
```

The sample depicts a gateway, an order service, and a MySQL database. Its authored labels are Chinese; change the model's labels for another content language. `meta.locale` controls viewer UI language independently.

### 2. Save `demo.task.json` in that directory

```json
{
  "contract_version": 1,
  "name": "demo-html",
  "type": "architecture",
  "targets": [
    {"format": "html", "source": "service.architecture.json"}
  ],
  "output_dir": "./delivery",
  "theme": "light",
  "quality": "showcase",
  "icons": {
    "bindings": [
      {"node_id": "store", "query": "MySQL"}
    ]
  }
}
```

### 3. Check routing and generate

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" route "$WORK_DIR/demo.task.json"
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" run "$WORK_DIR/demo.task.json"
```

Open `delivery/demo-html-html/demo-html.html` under `WORK_DIR`. `route` checks the task and shows backend selection; it does not render or replace full model validation. `run` repeats preflight and executes the selected targets.

## Generate HTML, DrawIO, and GIF from one model

After `doctor archify`, `doctor drawio`, and `doctor topology-gif` pass, save this as `multiformat.task.json` beside the same model and run it with `route` and `run`:

```json
{
  "contract_version": 1,
  "name": "demo-multiformat",
  "type": "architecture",
  "targets": [
    {"format": "html", "source": "service.architecture.json"},
    {"format": "drawio", "source": "service.architecture.json", "geometry": "shared", "preview": true},
    {"format": "gif", "source": "service.architecture.json", "derive": "architecture"}
  ],
  "output_dir": "./delivery",
  "theme": "light",
  "quality": "showcase",
  "icons": {
    "bindings": [
      {"node_id": "store", "query": "MySQL"}
    ]
  }
}
```

For workflow, use a workflow model, set task `type` and GIF `derive` to `workflow`, and update the source and icon node IDs. An example is [agent-tool-call.workflow.json](modules/archify/examples/agent-tool-call.workflow.json). New workflow models should use schema version 2.

For **independent DrawIO geometry**, provide a native uncompressed `.drawio` source and set `geometry` to `independent`. Author and validate that file separately. Do not point an independent DrawIO target at an Archify JSON model.

For **template GIF**, copy [05-animated-flow-spec.json](modules/drawio/examples/05-animated-flow-spec.json) into the working directory as `template.json`. Run `doctor gif`, then use this task without `derive` or SVG icon bindings:

```json
{
  "contract_version": 1,
  "name": "demo-template",
  "type": "template",
  "targets": [
    {"format": "gif", "source": "template.json"}
  ],
  "output_dir": "./delivery",
  "theme": "dark",
  "quality": "showcase"
}
```

### Task rules that matter

- `contract_version: 1` is the task contract version, not the package version.
- Relative paths resolve against the **task file's directory**, not the shell's working directory.
- A task may contain each output format only once. Target directories are named `<name>-html`, `<name>-drawio`, and `<name>-gif`.
- Existing target directories are never overwritten. Use a new `name` or a new output directory for another revision.
- Output must remain outside the installed Skill. Keep models and task files in your working directory.
- `geometry` and `preview` are DrawIO-only fields. DrawIO defaults to `independent`; shared geometry must be explicit.
- Topology GIF requires an explicit `derive` matching `architecture` or `workflow`. GIF targets do not accept `geometry` or `preview`.
- DrawIO's default `preview: true` should be disabled only when the user explicitly wants source-only output, not to bypass a missing renderer.
- Set `theme` explicitly for predictable results. HTML/shared DrawIO/topology GIF default to light; template GIF defaults to dark. An existing independent DrawIO file retains its authored styles.
- Keep `quality: "showcase"` unless the user explicitly requests otherwise. Do not remove meaningful edge labels to pass layout checks.

See the complete [task contract](references/task-contract.md).

## Local icons

```sh
"$PYTHON" "$SKILL_DIR/scripts/icons.py" search "MySQL"
"$PYTHON" "$SKILL_DIR/scripts/icons.py" resolve "MySQL"
```

Bind a product query to a stable model `node_id` in `icons.bindings`. Optional `provider` constrains the artwork provider; it does **not** identify the actual deployment vendor. Explicit `variant` wins over package preferences, which win over defaults.

The packaged catalog is used by default. An explicit `icons.root` may select a read-only data pack containing `catalog/` and `assets/`; executable code is still loaded only from this Skill. Path, hash, and SVG checks protect consumption. Do not hand-build image data URIs or rename business nodes to match artwork.

See [icon rules](references/icons.md). An icon marked `resolved` or `ready` is not a license clearance.

## Topology GIF limits

| Constraint | Current behavior |
| --- | --- |
| Models | Architecture free/grid; workflow fixed-v1/readable-v2 |
| Size | 2–12 nodes and 1–30 edges |
| Playback order | Input `connections` / `edges` array order |
| Animation | 20 fps during motion; 12 frames / 600 ms per edge |
| First and last frame | 400 ms each |
| Total frames / duration | `12 × edges + 2` / `600 × edges + 800` ms |
| Resource limits | ≤3 million pixels per frame; ≤180 million palette-frame pixels total; GIF ≤40 MiB |
| Readability | Minimum projected text size ≥8 px |
| Snapshot limits | ≤20,001 path samples per edge; scene JSON ≤32 MiB; local font ≤64 MiB |

Playback is explanatory, not a claim about runtime execution, causality, or concurrency. The base image retains labels, icons, boundaries, lanes, and groups. The local CJK font is loaded temporarily for rendering, not copied into the delivered files. Over-budget or unreadable output fails rather than silently reducing clarity or switching to the fixed template.

## Outputs and acceptance

Each target retains its artifact, native input, task snapshot, icon lock, and receipt. Shared DrawIO adds `model.json` and `layout.json`; topology GIF also includes `base.html`, `scene.json`, and the static PNG. HTML and shared rendering paths retain the embedded-resource registry for reproducibility. Display artifacts do not depend on the original icon-library path.

The overall result is `generated`, `partial`, or `failed`. Per-target results are `generated`, `blocked`, or `failed`. Partial failure returns a nonzero exit status.

**`generated` means generation and programmatic checks passed; it does not mean visual review passed.** Automated receipts intentionally retain `visual_review: pending` until separate evidence is recorded.

- HTML: open the actual file, test themes, search, relationship focus, and exports; inspect 1440×900, 1600×1000, and 1920×1080 viewports.
- DrawIO: inspect every exported PNG page for text, icons, endpoints, overlap, and cropping. Source-only delivery must disclose that rendering was not checked.
- GIF: inspect the PNG and representative early/middle/late animation frames, in addition to frame-count, timing, and frame-difference checks.

For example, collect HTML browser evidence with an existing Chrome/Chromium:

```sh
node "$SKILL_DIR/modules/archify/bin/archify.mjs" visual-check "$WORK_DIR/delivery/demo-html-html/demo-html.html" --json
```

The original 0.4.0 implementation baseline recorded 256 passing Python tests, 114 passing Node tests with two explicit skips, three separately passing real-Chrome topology tests, and 17/17 real-render acceptance cases. Separate topology visual evidence covered six final PNGs and 54 sampled GIF frames. These are historical, scoped results—not a new full rendering run for this documentation-only repack, exhaustive frame inspection, or cross-browser certification.

For a fresh full acceptance run, choose a new output directory and first check all required runtimes:

```sh
"$PYTHON" -B "$SKILL_DIR/tests/acceptance.py" --outdir "$WORK_DIR/acceptance-new"
```

Detailed desktop/responsive and structural test procedures are in the [task contract](references/task-contract.md). Never overwrite earlier evidence or relabel old results as a new run.

## Verify and back up locally

```sh
"$PYTHON" -B "$SKILL_DIR/scripts/package.py" verify
"$PYTHON" -B "$SKILL_DIR/scripts/package.py" build --output "$WORK_DIR/architecture-diagram-backup.zip"
```

The ZIP includes the complete registered Skill files, including both READMEs. It uses fixed member order, timestamps, and permissions with `ZIP_STORED` for reproducibility. The output path must be absolute, outside the Skill, and not already exist.

`verify` reports the SHA-256 of the manifest bytes; `build` reports the SHA-256 of the ZIP. Integrity verification does not establish a trusted origin, a digital signature, or redistribution rights. The documentation revision changes the manifest and ZIP hashes while leaving implementation version 0.4.0 unchanged; older packages and receipts remain distinct.

To relocate a copy, extract into a new directory outside Skill discovery, run `verify`, the relevant `doctor` checks, and acceptance before any explicitly approved switch of the active Skill. Do not overwrite the old installation or copy its `.venv`. Once registered in Qoder, invoke `/architecture-diagram`, for example:

> Create an architecture diagram for a gateway, order service, and MySQL database. Deliver HTML, shared-geometry DrawIO with PNG, and a topology GIF. Check local dependencies first; do not install anything.

## Current exclusions and license boundary

Not implemented: task `init`, configurable GIF speed or selected playback paths, automatic large-diagram partitioning, shared DrawIO/GIF for sequence/dataflow/lifecycle, arbitrary DrawIO-to-animation conversion, bidirectional synchronization, or a fully validated non-Chrome/mobile matrix. Planned work must not be presented as a current command or task field.

This is a **local/private package**, not a cleared public release. Read [licenses/review.json](licenses/review.json): `redistribution_cleared` is currently `false`. Archify's own code has [MIT evidence](modules/archify/LICENSE), while fonts, brands, stencils, copied DrawIO material, and arch-icons code/assets have separate conditions or unresolved permissions. Preserve the [third-party notices](modules/archify/THIRD_PARTY_NOTICES.md) and all packaged license evidence. No blanket license is asserted for the combined package.

## Further reading

- [SKILL.md](SKILL.md) — agent workflow and boundaries
- [Task contract](references/task-contract.md) — fields, output records, acceptance, and packaging
- [DrawIO workflow](references/drawio.md) — native editing and preview
- [GIF workflow](references/gif.md) — template and topology rendering
- [Icon rules](references/icons.md) — matching, fallback, and safe consumption
