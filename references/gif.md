# GIF 动画模块

按任务显式选择模板或拓扑分支，不读取任意 DrawIO，不静默转换或回退模板。

## 固定模板（兼容原任务）

1. 读取 `modules/drawio/references/animated-spec-format.md` 与 `modules/drawio/examples/05-animated-flow-spec.json`
2. 提炼最多4个输入、3张核心卡片、决策点和左中右面板，文字不能塞满画布
3. 使用内置 folder/file/scan/shield/db/hash/package/alert/clock/wrench 符号；模板仍拒绝 SVG `icons.bindings`
4. 先 `doctor gif`，要求 Python>=3.9、Pillow>=10 和实际可用字体；无需 Node 或 Chrome，可通过 `ARCHITECTURE_DIAGRAM_FONT` 指定本地字体
5. 创建 `type=template`、目标 `format=gif` 且不含 `derive` 的任务，theme 为用户指定值，未指定默认 dark
6. run 调用包内 `render_animated_diagram.py`，强制 `--verify --check`，输出 GIF 与 PNG
7. 检查程序报告与实际 PNG、首中末动画帧和帧差；非零退出码不得交付为成功

Pillow 安装须先获具体计划许可，优先使用本技能 `.venv`，不改全局 Python；用户拒绝安装时保留其他可运行分支。

## Architecture / Workflow 拓扑 GIF

- 使用 `type=architecture|workflow` 与对应类型 JSON；目标形如 `{"format":"gif","source":"typed.json","derive":"architecture"}`，workflow 须改为 `derive="workflow"`。`derive` 必须显式且与 type 一致；GIF 目标不接受 `geometry` 或 `preview`，不从 source 猜测类型。
- 可与 HTML、DrawIO 同一任务输出；同源时指向同一类型化 JSON，DrawIO 仍须使用 `geometry="shared"` 或其兼容 derive，其他格式规则不变。
- 沿用 `icons.bindings` 的安全解析、嵌入与锁定；未命中图标保留业务节点、标签和连线，安全失败拒绝素材并报告，不删除节点。
- 范围为 2–12 个节点、1–30 条边；architecture 支持 free/grid，workflow 支持 fixed-v1/readable-v2；其他类型和布局明确拒绝。

### 环境与离线边界

先运行 `python3 "<skill-dir>/scripts/diagram.py" doctor topology-gif`：要求现有 Python>=3.9、Pillow>=10、可用 CJK 字体、Node>=18 和 Chrome/Chromium。可用 `ARCHITECTURE_DIAGRAM_FONT` 指定本地字体，`ARCHIFY_CHROME` 指定浏览器路径。预检仅静态发现 Chrome 路径，不启动浏览器，也不代表实际渲染通过；缺必需项则阻断本分支。

渲染使用离线浏览器、临时 profile 与禁止网络的 CSP；将预检选定的 CJK 字体（≤64MiB）只加载到临时浏览器内存，不复制或嵌入交付文件，Chrome 无法解码则失败。预检和渲染绝不安装、遥测或更改权限，不为通过检查放宽安全边界。

### 画面、时序与上限

- 底图复用 Archify 生成的原 SVG，保留泳道、分组、标签和图标；沿浏览器实际曲线路径采样，按输入 `edges`/`connections` 数组顺序逐边高亮。此顺序仅用于讲解，不代表真实执行顺序或并发语义。
- 动画段 20fps，每边12帧（每帧50ms，共600ms）；首尾各一帧静止400ms。总帧数 `12 * 边数 + 2`，总时长 `600 * 边数 + 800` 毫秒。GIF 延时单位为10ms，因此选20fps而非无法精确表达的60fps。
- 单帧 ≤3M 像素，所有调色板帧合计 ≤180M 像素，GIF ≤40MiB；最小文字投影 ≥8px。资源或可读性超限时明确拒绝，不偷降清晰度或转为模板。
- 每条边最多采样20001点，scene JSON ≤32MiB；文字保护使用实际文本框和节点边界，有色泳道仍可显示动画。
- 静态 PNG 底图与 GIF 同目录，另存 `model.json`、`layout.json`、`scene.json`、`icons.registry.json`、`base.html`、source 原生输入、`task.json`、`icons.lock.json`、`receipt.json`；`scene.json` 含实际像素坐标路径，便于核对动画与底图。

### 验收

程序校验实际帧数、时长和帧差；另查看实际 PNG 及首、中、末动画帧，核对标签、图标、曲线路径、泳道/分组、可读性与裁切。`status=generated` 仅表示生成及程序检查通过，不等于目检；未目检保持 `visual_review=pending`。仅使用 Chrome/Chromium 单引擎，不声称全浏览器通过。
