# DrawIO XML 基础规则

> 抽取自 Andraw 项目的 `DEFAULT_SYSTEM_PROMPT`（`app/lib/config-utils.ts`）。
> 本文件是绘制任意 `.drawio` 图所必须遵守的"硬规则"。**违反任何一条都会导致图渲染错误**。

---

## A. XML 结构（必须）

DrawIO 文件以 XML 存储，固定层级如下：

```
mxfile
└── diagram (name, id)
    └── mxGraphModel
        └── root
            ├── mxCell id="0"          ← 系统保留，禁止改/删
            ├── mxCell id="1" parent="0"  ← 系统保留的根 layer，禁止改/删
            ├── mxCell ... vertex="1"   ← 你创建的节点
            └── mxCell ... edge="1"     ← 你创建的连线
```

- **根路径（XPath）**：`/mxfile/diagram/mxGraphModel/root`
- **新元素的 `parent`**：永远写 `parent="1"`
- **坐标系**：原点 (0,0) 在左上角；单位是像素；X 向右、Y 向下

### 一个最小可用的空白文件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="claude-code" agent="claude-code-drawio-skill" version="24.7.17" type="device">
  <diagram name="Page-1" id="page1">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" tooltips="1"
                  connect="1" arrows="1" fold="1" page="1" pageScale="1"
                  pageWidth="827" pageHeight="1169" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <!-- 你的 vertex / edge 写在这里 -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

---

## B. 两种元素

### Vertex（节点 / 形状）

```xml
<mxCell id="node-1" value="Text" style="rounded=1;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf"
        vertex="1" parent="1">
  <mxGeometry x="100" y="50" width="120" height="60" as="geometry"/>
</mxCell>
```

- `vertex="1"` — 标记为节点
- `parent="1"` — 永远挂在 root layer 上
- `<mxGeometry>` — 必须有 `x y width height`，且 `as="geometry"`

### Edge（连线）

```xml
<mxCell id="edge-1" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;endFill=1"
        edge="1" parent="1" source="node-1" target="node-2">
  <mxGeometry relative="1" as="geometry">
    <Array as="points">
      <mxPoint x="240" y="80"/>
      <mxPoint x="240" y="240"/>
    </Array>
  </mxGeometry>
</mxCell>
```

- `edge="1"` — 标记为连线
- `source` / `target` — 两端连接的 vertex id
- `<Array as="points">` — 可选 waypoints，用于路径控制（见 C 节）

---

## C. 连线路由（关键！）

**DrawIO 不会自动布线。** 不给 waypoints 的 edge 经常穿过其他节点或乱跑。

### Manhattan 正交布线策略

1. 计算 source 和 target 的中心点
2. 选一个中间值（midX 或 midY）
3. 加 2–4 个 waypoint 形成 90° 转角
4. 如果直线会穿过其他节点，绕开它们

### 示例：连接 A(中心 160,80) → B(中心 320,240)

```xml
<Array as="points">
  <mxPoint x="240" y="80"/>     <!-- 从 A 水平延伸 -->
  <mxPoint x="240" y="240"/>    <!-- 垂直到 B -->
</Array>
```

### 推荐 edge style

- `edgeStyle=orthogonalEdgeStyle` — 直角折线（推荐用于流程图/架构图）
- `edgeStyle=entityRelationEdgeStyle` — ER 图风格
- `rounded=0` — 直角；`rounded=1` — 圆角
- `endArrow=block` + `endFill=1` — 实心三角箭头（最常用）

---

## D. Style 系统

style 是 `key=value;key=value` 形式的 **分号分隔字符串**。

### 三条最重要的格式规则（违反就报错）

1. **NO 尾分号** ✅ `rounded=1;fillColor=#fff` ❌ `rounded=1;fillColor=#fff;`
2. **自闭合标签前 NO 空格** ✅ `<mxGeometry .../>` ❌ `<mxGeometry ... />`
3. **value 含 HTML 标签时必须配 `html=1`**
   - ✅ `style="html=1;..."` + `value="第一行<br>第二行"`
   - ❌ `style="..."` + `value="第一行<br>第二行"`（HTML 不会渲染）
   - ❌ `value="第一行\n第二行"`（DrawIO 会显示字面 `\n`）
   - 换行用 `<br>` 或 `<div>`，不要用 `\n`

### 常用 style 属性

| 属性 | 作用 | 示例值 |
|---|---|---|
| `fillColor` | 填充色 | `#dae8fc`、`#f8cecc` |
| `strokeColor` | 边框/线色 | `#6c8ebf`、`#b85450` |
| `strokeWidth` | 线宽 | `1`、`2`、`3` |
| `rounded` | 圆角 | `0` / `1` |
| `fontSize` | 字号 | `12`、`14`、`16` |
| `fontColor` | 字色 | `#000000` |
| `shape` | 形状类型 | `rectangle`、`ellipse`、`mxgraph.flowchart.*` 等 |
| `html` | value 是否当 HTML 渲染 | 用 HTML 标签就必须 `html=1` |
| `verticalAlign` | 文本垂直对齐 | `top`、`middle`、`bottom` |
| `align` | 文本水平对齐 | `left`、`center`、`right` |
| `whiteSpace` | 文本换行 | `wrap`（小心使用，会影响 measure）|

### value 里支持的 HTML 标签

`<div>` `<br>` `<b>` `<i>` `<u>` `<font color="..." size="...">`

---

## E. 工作流（推荐执行顺序）

1. **想清布局**：拿张纸/脑子里画一遍。决定网格（如水平 200px、垂直 100px），算好每个节点的 x/y/width/height
2. **先骨架后细节**：先写 mxfile 骨架（A 节的空白模板），再加 vertex，最后加 edge + waypoints
3. **edge 必须显式 waypoints**：除非两节点正好水平/垂直对齐
4. **写完跑 validate**：用 `scripts/validate.mjs` 自检（见 SKILL.md）
5. **可选预览**：把文件拖进 https://app.diagrams.net 看效果，或在用户安装了 Andraw / drawio Desktop 时让其打开

---

## F. 颜色配对惯例（DrawIO 经典色板）

每种颜色都有"浅填充 / 深描边"的配对，直接用就显得专业：

| 用途 | fillColor | strokeColor |
|---|---|---|
| 主流程 / 蓝 | `#dae8fc` | `#6c8ebf` |
| 成功 / 绿 | `#d5e8d4` | `#82b366` |
| 决策 / 黄 | `#fff2cc` | `#d6b656` |
| 错误 / 红 | `#f8cecc` | `#b85450` |
| 文档 / 紫 | `#e1d5e7` | `#9673a6` |
| 警告 / 橙 | `#ffe6cc` | `#d79b00` |
| 容器 / 灰 | `#f5f5f5` | `#666666` |

更多配色见 `references/color-themes.md`。
