# 通用形状（General）

> 基础形状（矩形、椭圆、菱形等），任何图都会用到。
> **提醒**：value 里用 HTML 标签（`<br>` `<b>` `<div>` 等）时，style 必须加 `html=1`。

| shape ID | 含义 | style 示例 |
|---|---|---|
| `rectangle` | 普通方框 / 标签 / 分组框 | `rounded=1;html=1;fillColor=#f5f5f5;strokeColor=#666666` |
| `ellipse` | 圆 / 椭圆 | `ellipse;html=1;fillColor=#ffffff;strokeColor=#666666` |
| `rhombus` | 决策菱形 | `rhombus;html=1;fillColor=#ffe6cc;strokeColor=#d79b00` |
| `triangle` | 三角 | `triangle;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf` |
| `text` | 纯文本标签 | `text;html=1;align=center;verticalAlign=middle` |
| `line` | 直线 | `line;strokeColor=#666666;strokeWidth=2` |
| `image` | 自定义图标/图片 | `shape=image;image=data:image/svg+xml,...` |
| `swimlane` | 泳道 / 容器 | `swimlane;html=1;fillColor=#f5f5f5;strokeColor=#666666` |
| `note` | 便利贴 | `shape=note;html=1;fillColor=#fff2cc;strokeColor=#d6b656` |
| `curlyBracket` | 花括号（右括号加 `flipH=1`） | `shape=curlyBracket;fillColor=none;strokeColor=#666666` |

## 默认矩形最佳实践

```xml
<mxCell id="n1" value="Step 1" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf"
        vertex="1" parent="1">
  <mxGeometry x="40" y="40" width="120" height="60" as="geometry"/>
</mxCell>
```
