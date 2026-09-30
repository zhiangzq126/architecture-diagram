# 任务契约 v1

JSON 文件路径作为 route/run 的唯一任务参数；相对路径均相对任务文件目录，不相对 shell 工作目录。

```json
{
  "contract_version": 1,
  "name": "orders",
  "type": "workflow",
  "targets": [
    {"format": "html", "source": "orders.workflow.json"},
    {"format": "drawio", "source": "orders.workflow.json", "geometry": "shared", "preview": true},
    {"format": "gif", "source": "orders.workflow.json", "derive": "workflow"}
  ],
  "output_dir": "./delivery",
  "theme": "light",
  "quality": "showcase",
  "icons": {
    "bindings": [
      {"node_id": "database", "query": "MySQL"},
      {"node_id": "cache", "query": "Redis", "provider": "aliyun"}
    ]
  }
}
```

- name：字母开头的 ASCII 文件名前缀，后续允许字母数字、短横线、下划线，最长64
- type：architecture / workflow / sequence / dataflow / lifecycle / uml / network / template
- targets：非空，每个格式只能出现一次，format 为 html / drawio / gif
- geometry：仅 DrawIO 支持，值为 `shared` 或 `independent`；缺省为 `independent`，保持旧任务行为
- `geometry="shared"`：仅匹配 `type=architecture|workflow`；source 必须是对应类型 JSON。HTML 与 DrawIO 可指向同一文件，复用节点 ID、业务标签、边方向和核心几何；Workflow 还复用泳道、分组和正交路由。目标目录额外保留 `model.json` 与 `layout.json`
- `geometry="independent"`：source 必须是未压缩 `.drawio`；若同时交付 HTML，其 JSON 与 DrawIO 各自维护布局，适用于用户明确选择不同排版或原生 DrawIO 特殊能力的场景
- derive：仅 DrawIO 和拓扑 GIF 接受 `"architecture"` 或 `"workflow"`，必须匹配任务 type。DrawIO 中仍是兼容字段，等价于 `geometry="shared"`，不能与 `geometry="independent"` 组合；拓扑 GIF 中必须显式填写，不推断、不静默转换
- preview：仅 DrawIO 支持，默认 true；false 只用于用户明确只需要源文件的情形
- theme：light / dark；HTML 设置初始主题且可切换，GIF 使用该主题；DrawIO 原生样式须在创作时与此一致，不自动重绘既有样式
- quality：默认 showcase，standard 仅接受用户显式要求
- icons.root：可选资源包根目录，包含 catalog/ 和 assets/；缺省使用技能内快照；不从此目录加载或执行代码
- icons.bindings：node_id、query 必填；provider、variant 可选；同一任务 node_id 不得重复，多页 DrawIO 绑定 ID 必须跨页唯一
- 模板 GIF：保持 `type=template`、目标 `format=gif` 且无 derive，source 使用原固定模板规范，仍拒绝 SVG bindings，仅用模板内置符号；无需 Node/Chrome
- 拓扑 GIF：`type=architecture|workflow`，目标 `format=gif`，source 为对应类型 JSON，derive 显式匹配 type；可与 HTML、DrawIO 一起输出。沿现有 `icons.bindings` 安全嵌入图标，缺图标保留节点；GIF 目标一律不得携带 geometry/preview

输入中禁止未声明字段、未知格式、自动路径覆盖；需要修改已交付结果时使用新 name。

输出状态：generated / partial / failed；每个目标独立标记 generated / blocked / failed。
未匹配图标属于可报告回退，不自动导致整图失败；生成成功仍保留 visual_review=pending，必须补实际视觉验收。

每个目标目录保存主产物、source 原生输入、task.json、icons.lock.json 和 receipt.json；HTML 另有 model.json 与 icons.registry.json；同源 DrawIO 另有 model.json、layout.json 与 icons.registry.json。拓扑 GIF 与静态 PNG 底图同目录，另有 model.json、layout.json、scene.json、icons.registry.json 和 base.html，scene 含实际像素坐标路径。注册表用于按相同资源复渲染，不是展示产物的外部依赖；receipt 的 `geometry` 与 `checks.geometry` 明确记录 DrawIO 模式。
源 SVG 摘要与安全序列化 SVG 摘要分开记录；源摘要不等于完整布局可复现保证。

## 拓扑 GIF 限制与验收

- `doctor topology-gif` 要求现有 Python>=3.9、Pillow>=10、CJK 字体、Node>=18 与 Chrome/Chromium；`ARCHIFY_CHROME` 可指定路径。预检仅静态发现 Chrome 路径，实际渲染使用离线临时 profile 与禁止网络的 CSP；绝不安装、遥测或更改权限。
- 仅 2–12 节点、1–30 边；architecture 支持 free/grid，workflow 支持 fixed-v1/readable-v2。底图复用 Archify 原 SVG，保留泳道、分组、标签、图标；沿浏览器实际曲线路径采样，按输入 `edges`/`connections` 数组逐边高亮，讲解顺序不代表实际执行或并发语义。
- 动画段20fps、每边12帧（600ms），首尾各一帧400ms；总帧数 `12 * 边数 + 2`，总时长 `600 * 边数 + 800` 毫秒。GIF 以10ms为延时单位，选20fps而非无法精确表达的60fps。
- 单帧 ≤3M 像素、所有调色板帧合计 ≤180M 像素、GIF ≤40MiB、最小文字投影 ≥8px；每边最多20001采样点，scene JSON ≤32MiB。预检选定的 CJK 字体 ≤64MiB，只加载到临时浏览器内存，不复制到产物；解码失败、资源或可读性超限明确拒绝，不偷降清晰度、不转模板。
- 程序校验实际帧数、时长及帧差，另目检 PNG 和首中末动画帧；生成仅检查不等于目检。Chrome/Chromium 单引擎不声称全浏览器通过。使用步骤见 [GIF 动画模块](gif.md)。

## 本地验收与版本维护

```sh
python3 -B <skill-dir>/tests/acceptance.py --outdir /existing-parent/new-acceptance
node <skill-dir>/tests/browser_acceptance.mjs --artifact /path/order-platform.html --outdir /existing-parent/new-browser --expect-icons 2 --require-chinese true
node <skill-dir>/tests/browser_acceptance.mjs --artifact /path/order-platform.html --outdir /existing-parent/new-responsive --profile responsive
node <skill-dir>/tests/test_browser_structure.mjs --fixtures /path/acceptance/delivery --outdir /existing-parent/new-structure
python3 -B <skill-dir>/scripts/package.py verify
python3 -B <skill-dir>/scripts/package.py build --output /existing-parent/architecture-diagram.zip
```

浏览器验收默认读取 HTML 同目录的 `model.json`；独立 HTML 必须显式传入 `--model /absolute/path/native.json`。模型须为对应产物的原生 JSON，缺失或无效时失败，不从 DOM 反推预期结构。报告保存模型路径、字节数与 SHA-256；分组成员、时序生命线和图例按模型验证，删除、隐藏或错位的必需元素不得因空集通过。模型显式指定 `meta.legend.mode=hidden` 时允许隐藏图例。

结构正负例须显式传入 `--fixtures` 与 `--outdir`（或 `ARCHIFY_BROWSER_STRUCTURE_FIXTURES` 与 `ARCHIFY_BROWSER_STRUCTURE_OUTDIR`）；普通 `node --test` 会跳过该组，不代表浏览器正负例通过。夹具目录使用 acceptance 生成的五类 HTML 与原生模型。

验收目录必须新建且在技能目录外。acceptance 执行五类 HTML、双页 DrawIO/PNG、深浅 GIF、缺图标和重复图标、收据及输出保护检查，并对每份 HTML 调用浏览器验收，任一失败即返回非零。浏览器脚本仅监听本机临时端口，默认 `--profile desktop` 检查三种桌面尺寸、两种主题、文字可读性、工具栏避让、搜索、关系、章节及导出，并检查移动断点、演示、嵌入和打印模式切换后恢复；结束关闭服务。默认允许英文业务标签，`--require-chinese true` 额外要求中文标签，Viewer UI 始终检查中文。查看实际截图与 PNG 后单独记录视觉目检结论，不将程序通过等同目检。

修改响应式布局时，对五类 HTML 分别追加 `--profile responsive`：覆盖宽度 320–3840 CSS px 的 17 组尺寸与双主题、全部章节的点击/播放/暂停/恢复，并在 360、420、720、768、1024 宽度断点前后各 16px 逐像素往返扫描及高度边界扫描。该模式允许小屏纵向滚动，检查文档无横向溢出、侧栏及文案可访问、桌面属性清理，不替代 desktop 的节点几何、投影字号 ≥6px 与真实 UI 导出检查。有限矩阵不是所有分辨率的穷举；Chrome 视口模拟不是其他浏览器引擎或移动真机测试。其他引擎不可用时明确记录 blocked，不自动安装运行时或开启 Safari 远程自动化权限。长矩阵可追加 `--shard I/N`（例如分别执行 `1/3`、`2/3`、`3/3`），按视口索引与断点扫描分片；每片使用独立新目录，必须核对同一产物的全部 N 份报告及覆盖并集，不能将单片通过当作全矩阵通过。

package 要求 Python ≥3.9 和 POSIX 文件系统 API（macOS/Linux；本次实测 macOS），只校验或创建本地 ZIP，不下载、不安装、不切换版本、不发布；产物不得覆盖已有路径。ZIP 排除根 `.venv` 等运行环境，固定文件顺序、时间戳和权限，采用 ZIP_STORED，同一 manifest 与文件内容可复现相同 ZIP。verify 的 sha256 对应原始 manifest 字节，bytes/files 包含 manifest；build 的 sha256/bytes 对应 ZIP，files 是成员数。清单 SHA-256 只能发现与清单不一致，不能替代可信来源或数字签名。

升级前保留旧版完整目录/ZIP及摘要；将新包解压到技能发现目录之外的新目录，先 verify、doctor 和 acceptance，通过后经用户确认再切换唯一 `architecture-diagram` 入口。旧版移到发现目录之外留作回滚，不把两个版本同时注册；失败时切回保留的旧目录，不覆盖旧目录、不自动搬运 `.venv`。新目录缺依赖仍须展示具体安装计划并征得确认。

manifest 的 `sources` 保留历史复制来源和摘要，`current_source` 记录 arch-icons 更名后的定位；`inventory` 是当前包文件的逐文件摘要。历史交付收据保持原样，新收据使用当前 manifest 版本。许可复核范围见 `licenses/review.json`；完整性通过不表示已取得全包对外分发权。
