# 标准流程图符号（Flowchart）

> 经典 BPMN / 流程图符号。用 `shape=mxgraph.flowchart.*`。

| shape ID | 含义 | style 示例 |
|---|---|---|
| `mxgraph.flowchart.terminator` | 开始 / 结束（圆头矩形） | `shape=mxgraph.flowchart.terminator;fillColor=#d5e8d4;strokeColor=#82b366` |
| `mxgraph.flowchart.process` | 处理步骤（矩形） | `shape=mxgraph.flowchart.process;fillColor=#dae8fc;strokeColor=#6c8ebf` |
| `mxgraph.flowchart.decision` | 决策（菱形） | `shape=mxgraph.flowchart.decision;fillColor=#ffe6cc;strokeColor=#d79b00` |
| `mxgraph.flowchart.data` | 输入/输出（平行四边形） | `shape=mxgraph.flowchart.data;fillColor=#f8cecc;strokeColor=#b85450` |
| `mxgraph.flowchart.document` | 文档（带波浪底） | `shape=mxgraph.flowchart.document;fillColor=#e1d5e7;strokeColor=#9673a6` |
| `mxgraph.flowchart.stored_data` | 数据库 / 存储 | `shape=mxgraph.flowchart.stored_data;fillColor=#dae8fc;strokeColor=#6c8ebf` |
| `mxgraph.flowchart.manual_input` | 手工输入 | `shape=mxgraph.flowchart.manual_input;fillColor=#fff2cc;strokeColor=#d6b656` |
| `mxgraph.flowchart.delay` | 延迟 | `shape=mxgraph.flowchart.delay;fillColor=#f5f5f5;strokeColor=#666666` |

## 决策分支边的标签

决策菱形出来的 yes/no 边，给 edge 加 `value="Yes"` / `value="No"` 即可，记得 `html=1`：

```xml
<mxCell id="e_yes" value="Yes" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;endFill=1"
        edge="1" parent="1" source="decision1" target="step2">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```
