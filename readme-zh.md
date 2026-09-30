# Architecture Diagram

[English](README.md) | **简体中文**

**功能版本 0.4.0 · 文档修订 2026-09-30 · 使用范围为本地／私有**

一个独立的 Qoder 绘图 Skill 包，通过统一任务入口生成交互式 HTML、可编辑 DrawIO、PNG 预览和 GIF 动画。包内整合了 Archify 私有快照、DrawIO 工具及只读本地图标解析能力。

本次文档更新不改动渲染代码。该包不依赖原项目或旧技能目录运行，也不会自动跟随上游更新。

## 功能概览

| 图种 | 交互 HTML | DrawIO + PNG | GIF |
| --- | --- | --- | --- |
| 系统架构 architecture | 支持 | 共享 JSON 几何或独立 DrawIO | 拓扑动画 |
| 工作流 workflow | 支持 | 共享 JSON 几何或独立 DrawIO | 拓扑动画 |
| 时序 sequence | 支持 | 仅支持另行创作原生 DrawIO | 不支持 |
| 数据流 dataflow | 支持 | 仅支持另行创作原生 DrawIO | 不支持 |
| 生命周期 lifecycle | 支持 | 仅支持另行创作原生 DrawIO | 不支持 |
| UML／特殊网络图 | 不支持这两种任务类型 | 原生 DrawIO | 不支持 |
| 固定三阶段模板 | 不支持 | 不支持 | 模板动画 |

- **HTML** 内嵌 SVG、交互界面和已解析图标，支持深浅主题、平移缩放、搜索、关系聚焦和预设章节；可导出 PNG、JPEG、WebP、SVG，启用 trace 的 WebM 录制还取决于浏览器支持。
- **DrawIO** 可继续编辑，默认输出 PNG 预览，多页输入逐页导出。
- **共享几何** 复用 architecture／workflow 模型、节点 ID、业务标签、边方向、核心几何与图标绑定；Workflow 还保留泳道、分组和正交路径。不承诺像素完全一致，也不支持手动修改 DrawIO 后自动同步其他格式。
- **拓扑 GIF** 沿实际架构图或工作流的 SVG 路径播放；**模板 GIF** 是独立的固定布局渲染器，不是任意图表的失败回退方案。
- **图标可选**。缺失、歧义或待审核的图标不会使业务节点、标签和关系消失；不安全素材会被拒绝并说明原因。

## 项目结构

```text
architecture-diagram/
├── SKILL.md                 Qoder Agent 执行说明
├── README.md                英文说明
├── readme-zh.md              中文说明
├── manifest.json            版本、来源与逐文件摘要
├── scripts/                 任务入口、环境预检、适配器与打包工具
├── modules/
│   ├── archify/             类型化模型与 HTML 渲染
│   ├── drawio/              DrawIO 校验与模板 GIF 工具
│   └── icons/               包内图标引擎与契约
├── assets/icon-pack/        本地图标目录与 SVG 快照
├── examples/                原生输入与任务示例
├── references/              详细集成契约
├── tests/                   单元测试与真实渲染验收
└── licenses/                许可证据与未解决复核项
```

该目录包含完整实现，运行时不依赖原始仓库。历史图表、验收证据及另行提供的设计原理、功能介绍、使用说明、待优化清单位于 Skill 目录之外，不包含在本 Skill ZIP 中。系统运行时和根目录 `.venv` 也不会打包。

## 环境要求与预检

按所选格式检查依赖；包内 Archify 运行不需要执行 `npm install`。

| 操作 | 所需本地环境 |
| --- | --- |
| 任务入口／图标查询 | Python ≥3.9 |
| HTML 生成 | Python ≥3.9、Node.js ≥18 |
| DrawIO 校验 | Python ≥3.9、Node.js ≥18 |
| DrawIO PNG 预览 | 上述环境及 draw.io Desktop CLI |
| 模板 GIF | Python ≥3.9、Pillow ≥10、可用中文字体 |
| 拓扑 GIF | 模板 GIF 环境及 Node.js ≥18、Chrome／Chromium |
| HTML 自动浏览器验收 | 已有兼容的 Chrome／Chromium |
| 清单校验／ZIP 打包 | Python ≥3.9、POSIX 文件系统 API |

已在 macOS 上执行真实渲染，不宣称 Windows／Linux、移动真机或跨浏览器矩阵全部通过。找到浏览器可执行文件不等于通过渲染测试。

先填写项目路径并选择已有 Python。入口通过 `PATH` 寻找 `node`，不读取 `NODE` 变量。若 shell 版本管理器的转发脚本异常，应让当前进程使用已有可执行文件所在目录，不必修改 shell 配置。

```sh
SKILL_DIR="/absolute/path/to/architecture-diagram"
PYTHON="python3"
/bin/sh "$SKILL_DIR/scripts/check-runtime.sh"
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor archify
```

其他分支生成前执行对应预检。

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor drawio
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor gif
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" doctor topology-gif
```

以下可选环境变量只指向**已有本地文件**。

| 变量 | 用途 |
| --- | --- |
| `DRAWIO_BIN` | draw.io Desktop 可执行文件 |
| `ARCHIFY_CHROME` | Chrome／Chromium 可执行文件 |
| `ARCHITECTURE_DIAGRAM_FONT` | 本地中文字体；也识别 `CJK_FONT` |

预检不下载、不安装。缺少必需依赖时，对应分支被阻止。只想查看安装方案时执行下列命令，不会实际安装。

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" install-plan pillow
```

安装是单独的操作，必须先明确同意当前方案的来源、命令和环境影响。统一生成流程不自动安装包管理器、不提权、不遥测、不发布。

## 快速开始：交互 HTML

### 1. 准备独立工作目录

替换占位路径。`WORK_DIR` 的父目录须已存在，且新目录必须在 Skill 目录之外；目录已存在时，`mkdir` 会失败，避免沿用旧产物。

```sh
WORK_DIR="/absolute/path/to/new-diagram-demo"
mkdir "$WORK_DIR"
cp "$SKILL_DIR/examples/service.architecture.json" "$WORK_DIR/service.architecture.json"
```

示例包含接入网关、订单服务和 MySQL 数据库，业务文案为中文。需要其他语言时修改模型标签；`meta.locale` 单独控制交互界面语言。

### 2. 在工作目录保存 `demo.task.json`

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

### 3. 检查路由并生成

```sh
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" route "$WORK_DIR/demo.task.json"
"$PYTHON" "$SKILL_DIR/scripts/diagram.py" run "$WORK_DIR/demo.task.json"
```

打开 `WORK_DIR` 下的 `delivery/demo-html-html/demo-html.html`。`route` 只检查任务并显示后端选择，不生成图表，也不替代完整模型验证；`run` 会再次预检并执行所选目标。

## 同一模型生成 HTML、DrawIO 与 GIF

`doctor archify`、`doctor drawio`、`doctor topology-gif` 均通过后，将下列内容保存为同目录的 `multiformat.task.json`，再用 `route` 和 `run` 执行。

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

工作流需换用 workflow 模型，将任务 `type` 和 GIF `derive` 改为 `workflow`，同时更新输入路径及图标节点 ID。参考 [agent-tool-call.workflow.json](modules/archify/examples/agent-tool-call.workflow.json)；新 Workflow 模型使用 schema version 2。

选择**独立 DrawIO 几何**时，输入原生未压缩 `.drawio`，设置 `geometry: "independent"`，单独创作和验证其布局；不要把 Archify JSON 作为 independent DrawIO 的输入。

选择**模板 GIF**时，将 [05-animated-flow-spec.json](modules/drawio/examples/05-animated-flow-spec.json) 复制到工作目录并命名为 `template.json`，执行 `doctor gif` 后使用下列任务，不填写 `derive` 或 SVG 图标绑定。

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

### 关键任务规则

- `contract_version: 1` 是任务契约版本，不是包版本。
- 相对路径以**任务文件所在目录**为基准，不以 shell 工作目录为基准。
- 每个任务的每种输出格式只能出现一次；目标目录名为 `<name>-html`、`<name>-drawio`、`<name>-gif`。
- 不覆盖已有目标目录。修改后使用新 `name` 或新输出目录。
- 输出不得写入安装目录；模型和任务文件放在工作目录。
- `geometry` 和 `preview` 仅用于 DrawIO。DrawIO 默认 `independent`，共享几何须显式选择。
- 拓扑 GIF 必须显式填写与任务匹配的 `derive: "architecture"` 或 `"workflow"`；GIF 不接受 `geometry`、`preview`。
- DrawIO 默认 `preview: true`；只有用户明确只需源文件时才关闭，不用它绕过缺少渲染器的问题。
- 建议显式设置 `theme`。HTML／共享 DrawIO／拓扑 GIF 默认浅色，模板 GIF 默认深色；独立 DrawIO 保留输入文件原样式。
- 用户未明确要求其他标准时，保留 `quality: "showcase"`；不为通过布局检查删除有意义的边标签。

完整字段见[任务契约](references/task-contract.md)。

## 本地图标

```sh
"$PYTHON" "$SKILL_DIR/scripts/icons.py" search "MySQL"
"$PYTHON" "$SKILL_DIR/scripts/icons.py" resolve "MySQL"
```

在 `icons.bindings` 中，将产品查询词绑定到模型的稳定 `node_id`。可选 `provider` 约束素材提供方，**不代表**系统实际部署厂商。优先级为显式 `variant`、资源包偏好、默认值。

默认使用包内目录；也可通过 `icons.root` 显式选择包含 `catalog/`、`assets/` 的只读数据包，代码仍只从本 Skill 加载。素材使用前检查路径、摘要和 SVG 安全性；不要手工拼接图片 data URI，也不要为了匹配图标改写业务节点名称。

详见[图标规则](references/icons.md)。`resolved`、`ready` 状态不代表获得对外分发许可。

## 拓扑 GIF 限制

| 项目 | 当前行为 |
| --- | --- |
| 模型 | Architecture free/grid；Workflow fixed-v1/readable-v2 |
| 规模 | 2–12 个节点、1–30 条边 |
| 播放顺序 | 输入 `connections`／`edges` 数组顺序 |
| 动画段 | 20 fps；每边 12 帧、600 ms |
| 首尾静态帧 | 各 400 ms |
| 总帧数／时长 | `12 × 边数 + 2`／`600 × 边数 + 800` ms |
| 资源上限 | 单帧 ≤300 万像素；总调色板帧 ≤1.8 亿像素；GIF ≤40 MiB |
| 可读性 | 最小文字投影 ≥8 px |
| 快照上限 | 每边 ≤20,001 个采样点；scene JSON ≤32 MiB；本地字体 ≤64 MiB |

播放用于讲解，不代表实际执行、因果关系或并发行为。底图保留标签、图标、边界、泳道和分组；中文字体只在渲染时临时加载，不复制到交付文件。资源或可读性超限会明确失败，不降低清晰度、不自动改用固定模板。

## 输出与验收

每个目标保留主产物、原生输入、任务快照、图标锁定记录及收据。共享 DrawIO 增加 `model.json`、`layout.json`；拓扑 GIF 还保留 `base.html`、`scene.json` 和静态 PNG。HTML 及同源渲染路径保留内嵌资源注册表，便于复渲染；展示文件不依赖原图标库路径。

整体状态为 `generated`、`partial` 或 `failed`；单目标状态为 `generated`、`blocked` 或 `failed`。部分失败仍返回非零退出码。

**`generated` 只表示生成与程序检查通过，不代表视觉验收通过。** 自动收据保留 `visual_review: pending`，视觉证据另外记录。

- HTML：打开实际文件，操作主题、搜索、关系聚焦和导出，检查 1440×900、1600×1000、1920×1080 三种视口。
- DrawIO：逐页查看导出的 PNG，检查文字、图标、端点、遮挡和裁切；只交付源文件时明确说明未验证渲染。
- GIF：除帧数、时长和帧差检查外，查看 PNG 以及前段、中段、后段的代表动画帧。

例如，使用已有 Chrome／Chromium 收集 HTML 浏览器证据。

```sh
node "$SKILL_DIR/modules/archify/bin/archify.mjs" visual-check "$WORK_DIR/delivery/demo-html-html/demo-html.html" --json
```

原 0.4.0 实现基线记录了 Python 256 项通过、Node 114 项通过且 2 项明确跳过、另行执行的真实 Chrome 拓扑专项 3 项通过，以及真实渲染验收 17/17 通过。独立拓扑目检覆盖六份最终 PNG 和 54 个 GIF 抽样帧。这些属于历史限定范围的结果，不代表本次文档重打包重新执行了全量渲染、逐帧穷举或跨浏览器认证。

需要新一轮完整验收时，先检查所有所需环境，再使用新输出目录。

```sh
"$PYTHON" -B "$SKILL_DIR/tests/acceptance.py" --outdir "$WORK_DIR/acceptance-new"
```

桌面、响应式和结构测试的详细方法见[任务契约](references/task-contract.md)。不覆盖历史证据，不把旧结果改称新一轮验收。

## 本地校验与备份

```sh
"$PYTHON" -B "$SKILL_DIR/scripts/package.py" verify
"$PYTHON" -B "$SKILL_DIR/scripts/package.py" build --output "$WORK_DIR/architecture-diagram-backup.zip"
```

ZIP 包含全部登记的 Skill 文件及中英文 README，固定成员顺序、时间戳和权限，并采用 `ZIP_STORED` 保证可复现。输出必须使用绝对路径，位于 Skill 之外，且不能已经存在。

`verify` 返回 manifest 原始字节的 SHA-256，`build` 返回 ZIP 的 SHA-256。完整性校验不等于来源可信、数字签名或取得分发许可。本次文档修订会改变 manifest 和 ZIP 摘要，但功能版本仍为 0.4.0；旧包及旧收据保持独立。

迁移时，先解压到技能发现目录之外的新目录，执行 `verify`、对应 `doctor` 和验收，明确获准后再切换当前 Skill；不覆盖旧安装，不搬运旧 `.venv`。在 Qoder 注册后可调用 `/architecture-diagram`，例如：

> 为接入网关、订单服务和 MySQL 数据库画架构图，同时交付 HTML、共享几何 DrawIO 与 PNG、拓扑 GIF。先检查本地环境，不安装任何依赖。

## 当前不支持的能力与许可边界

尚未实现任务 `init`、可调 GIF 速度或指定播放路径、自动拆分大图、sequence／dataflow／lifecycle 同源 DrawIO 与 GIF、任意 DrawIO 转动画、双向同步，也未完成非 Chrome／移动端全矩阵验收。不能把计划中的功能写成当前可用命令或任务字段。

当前是**本地／私有包**，并非已获准公开分发的版本。[licenses/review.json](licenses/review.json) 中 `redistribution_cleared` 仍为 `false`。Archify 自有代码有 [MIT 许可证据](modules/archify/LICENSE)，但字体、品牌、stencil、复制的 DrawIO 材料及 arch-icons 代码／素材各有条件或未解决授权。须保留[第三方声明](modules/archify/THIRD_PARTY_NOTICES.md)和包内全部许可证据，不为整包推定统一授权。

## 延伸阅读

- [SKILL.md](SKILL.md) — Agent 执行流程与边界
- [任务契约](references/task-contract.md) — 字段、输出记录、验收与打包
- [DrawIO 工作流](references/drawio.md) — 原生编辑与预览
- [GIF 工作流](references/gif.md) — 模板与拓扑动画
- [图标规则](references/icons.md) — 匹配、回退与安全消费
