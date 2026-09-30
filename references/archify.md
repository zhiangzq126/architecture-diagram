# 交互 HTML 模块

使用包内 `modules/archify` 私有引擎；无需安装 npm 开发依赖，运行要求 Node>=18。

1. 按用户语义选择 architecture / workflow / sequence / dataflow / lifecycle
2. 仅读对应 `modules/archify/schemas/<type>.schema.json`、`common.schema.json` 及 `modules/archify/examples/` 中一个同类型 JSON
3. 先创作候选 JSON，最多12个主节点，短分支，保留关系标签；默认 classic、showcase，省略无信息副标题
4. 新 workflow 使用 schema_version=2；其他类型依 schema，不强行扁平化泳道、生命线或边界
5. 未诊断前不要添加 via/channelX/channelY/labelAt；每轮只修正已诊断几何问题
6. 通过统一 task 绑定图标；私有适配在品牌解析阶段加载安全 SVG，使用已有测量和品牌槽位，不后处理 HTML
7. 执行 `diagram.py run`，内部依次 validate 和 deliver；showcase 应有9项 artifact 检查、0 composition error、0 warning
8. 连续两轮未减少诊断则停止并报告，不降级标准、不删除语义标签骗过检查
9. 使用包内 visual-check 收集证据，再用浏览器检查主题、搜索、关系追踪、导出及实际图标

内置 brand ID 仍可使用；远程 brand URL 和对象在本包内拒绝，不静默抓取网络资源。
HTML 包含内嵌图标和交互代码，脱离原项目和图标目录仍可打开。
双格式任务各自提供原生输入；当前不包含共用几何编译器或原生 DrawIO 导出器。
