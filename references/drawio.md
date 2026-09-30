# 原生 DrawIO 模块

先读 `modules/drawio/references/xml-fundamentals.md`；按需读取 knowledge/general.md、flowchart.md、uml.md、network.md 或厂商图形规则，以及 themes.md / color-themes.md。

- 写未压缩 mxfile，每页保留0/1系统节点；业务节点与关系使用稳定ID
- style 不加尾分号，自闭合标签前不加空格，HTML value 配 html=1
- 使用 CSS px 或 cell fontSize，不用 font size=N 控制字号
- 明确坐标、容器和正交折点，不承诺通用自动布局
- 为拟绑定图标节点预留至少44px右侧空间，节点至少100×48；建议节点200×80、图标28×28
- 适配器只添加 connectable=0 的图标子节点，保留业务节点 value、ID 和边端点；布局缩小时不要裁切图标
- 图标通过 task.icons.bindings 提供，禁止把未验证 data URI 或网络图片放进输入 style
- 现有压缩 DrawIO 或含自定义内嵌图片的输入需先由可信编辑器导出未压缩结构，并把图标转为显式绑定；不要静默丢弃图片
- run 执行 XML 引用与几何检查、上游格式检查，再用本地 DrawIO CLI逐页导出 PNG；任何 warning 都需要修正
- 对每页 PNG 进行实际目检，检查中文、品牌颜色、连接端点和裁切

默认输出 .drawio 和所有页面 PNG，均在同一个目标目录；仅用户明确请求纯源文件时设置 preview=false，并说明未做渲染验证。
主题颜色在原生 XML 创作时确定，不把已有图的颜色强行全局替换。

## 几何模式

DrawIO target 必须按用户意图选择：

- `geometry="shared"`：仅支持 architecture / workflow，source 指向与 HTML 相同的类型化 JSON。architecture 通过 `archify inspect architecture` 获取布局；workflow 通过 `archify validate workflow --layout-json` 获取 fixed-v1 或 readable-v2 布局。发射器复用节点 ID、业务标签、边方向、节点几何；Workflow 还复用泳道、分组和正交路由。输出保留 `model.json` 与 `layout.json` 作为同源证据。
- `geometry="independent"`：source 必须是独立原生 `.drawio`。HTML 与 DrawIO 各自维护布局，适合用户明确要求不同排版、特殊形状或手工编辑结构的场景。

`derive="architecture|workflow"` 是兼容写法，等价于匹配类型的 `geometry="shared"`；不得与 `geometry="independent"` 同时使用。缺省 DrawIO 仍按 `independent` 处理，避免把旧任务静默转换。同源对齐不承诺 HTML 与 DrawIO 像素级一致。派生发射器内置页面留白，PNG 使用 `--border 0` 保留主题画布。
