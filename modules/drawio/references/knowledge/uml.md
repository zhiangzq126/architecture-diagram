# UML 元素

| shape ID | 含义 | style 示例 |
|---|---|---|
| `swimlane` | 类 / 接口框（带标题栏） | `swimlane;fillColor=#dae8fc;strokeColor=#6c8ebf` |
| `umlLifeline` | 时序图生命线 | `shape=umlLifeline;fillColor=#ffffff;strokeColor=#666666` |
| `umlActor` | Actor / 用户 | `shape=umlActor;fillColor=#dae8fc;strokeColor=#6c8ebf` |
| `ellipse` | 用例（Use Case） | `ellipse;fillColor=#e1d5e7;strokeColor=#9673a6` |
| `umlState` | 状态 | `shape=umlState;fillColor=#ffe6cc;strokeColor=#d79b00` |
| `endState` | 终止状态（实心圆） | `shape=endState;fillColor=#000000;strokeColor=#666666` |

## 类图：在 swimlane 里堆字段/方法

```xml
<mxCell id="C1" value="User" style="swimlane;fontStyle=1;align=center;fillColor=#dae8fc;strokeColor=#6c8ebf"
        vertex="1" parent="1">
  <mxGeometry x="40" y="40" width="180" height="120" as="geometry"/>
</mxCell>
<mxCell id="C1_f1" value="+ id: string" style="text;html=1;align=left;spacingLeft=8"
        vertex="1" parent="C1">
  <mxGeometry y="26" width="180" height="20" as="geometry"/>
</mxCell>
<mxCell id="C1_f2" value="+ login(): void" style="text;html=1;align=left;spacingLeft=8"
        vertex="1" parent="C1">
  <mxGeometry y="46" width="180" height="20" as="geometry"/>
</mxCell>
```

注意 `parent="C1"`——子元素挂在 swimlane 上，并用相对坐标。

## 关系箭头

| 关系 | edge style |
|---|---|
| 依赖（虚线箭头） | `endArrow=open;dashed=1` |
| 关联（实线箭头） | `endArrow=open` |
| 聚合（空心菱形） | `startArrow=diamondThin;startFill=0;startSize=14;endArrow=none` |
| 组合（实心菱形） | `startArrow=diamondThin;startFill=1;startSize=14;endArrow=none` |
| 继承（空心三角） | `endArrow=block;endFill=0` |
