# Animated Diagram GIF Spec Format

Use this reference when creating or editing a spec for `scripts/render_animated_diagram.py`.

## Layout Model

The renderer is optimized for a premium hand-drawn architecture/process diagram (dark or light theme):

1. Top title: `title.prefix` plus highlighted `title.highlight`
2. Top input box: four compact input sources
3. Middle core: three major process cards, a decision diamond, and an output card
4. Bottom left panel: source/context cards
5. Bottom center panel: internal storage or processing layers
6. Bottom right panel: final package/output cards
7. Top right brand slot: dotted mark plus `signature`

Keep the copy short. The renderer uses fixed art-directed positions and applies
basic text fitting in compact regions, but short labels still produce the best
visual hierarchy.

## Recommended Copy Length

- `title.prefix`: 2 to 4 words
- `title.highlight`: 1 to 3 words
- Input labels: 1 word
- Core card title: 1 to 2 words
- Core card body: 2 lines, each under 22 characters
- Panel card title: 1 to 3 words
- Panel card body: 1 to 2 short lines
- Signature: short handle, such as `@author`

## Text Fitting

The renderer automatically fits text in compact labels and cards by wrapping
lines and reducing font size. When a label is still too tight, it may use a
smaller emergency size to preserve the full text. This is intended as a safety
net for labels, not as a replacement for concise copy.

Text fitting is applied to:

- Input labels
- Core card titles and bodies
- Decision diamond text
- Bottom panel cards
- Output and package labels

Manual line breaks in the spec are preserved. English text wraps on spaces,
while CJK text can wrap between characters when needed.

## Icons

Supported icon keys:

- `folder` — folder with tab
- `file` — document with text lines
- `scan` — magnifying glass
- `shield` — shield badge with checkmark
- `db` — database cylinder
- `hash` — hash/pound sign
- `package` — 3D hexagonal box
- `alert` — warning triangle with exclamation
- `clock` — clock face with hands
- `wrench` — wrench/tool

Use simple icons unless the user explicitly provides audited assets. Avoid remote icon libraries by default.

## Themes

The renderer supports two themes via `--theme` CLI flag:

- `dark` (default): black canvas, white text, neon accent colors — best for presentations and dark-mode contexts
- `light`: warm off-white canvas (#f7f5f2), dark text, muted accent fills — best for documents, printed materials, and light-mode contexts

Theme selection does not affect the spec JSON. The same spec produces correct output in either theme.

## Quality Bar

The output should include:

- `.png` static preview
- `.gif` animated version

Verify:

- GIF dimensions match the requested canvas
- GIF has the requested frame count and FPS
- Frame-diff shows real motion

## Common Command

```bash
python scripts/render_animated_diagram.py \
  --spec assets/default-spec.json \
  --outdir /tmp/diagram-output \
  --basename memory-pack \
  --theme dark \
  --verify
```
