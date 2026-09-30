# Agent 图标调用规范 · v2

这是 arch-icons 上游图标库的调用规范副本，本文链接与环境变量描述原项目；本包仅同步更名，不代表已同步上游代码。architecture-diagram 的实际入口、内置快照与 icons.root 规则以 `icons.md` 为准，不执行本文中的原项目命令。

## 1. 定位、范围与版本

设置 `ARCH_ICONS_ROOT` 为图标库项目根目录，例如：

```bash
export ARCH_ICONS_ROOT="/absolute/path/to/arch-icons"
python3 "$ARCH_ICONS_ROOT/scripts/iconlib.py" resolve "tencent/cos" --json
```

不设置环境变量时，CLI 使用脚本所在项目作为根目录。其他机器需改成本地实际路径；相对路径、`~` 等应先展开成绝对路径。无需启动浏览服务，不依赖固定端口。使用 Python 3.9+，运行时无需第三方包或网络。

公共接口是 CLI 的 `search`、`resolve`、`show`，不是 Python 内部函数、预览页 data.js 或浏览器 API。三者返回 `contract_version: 2`；该版本独立于 `catalog/index.json` 的 `schema_version`。旧字段保留，新增字段允许消费者忽略。改变字段含义、必填字段或选择规则需升级 contract 主版本；消费者仅接受自己支持的版本。未知状态和缺失版本不能当成功处理。

普通消费只读 `catalog`、规范和本次选中的 `assets`。不遍历 SVG 猜产品，也不访问 `sources` 寻找其他版本。

## 2. 输入与查询顺序

| 命令 | 输入 | 输出与用途 |
|---|---|---|
| `search <query> [--provider ID] [--limit N] [--include-pending] --json` | 产品名、别名、关键词或 ID | `{contract_version, query, matches}`；发现候选，默认最多 20 项且不包含待确认产品 |
| `show <product-id> --json` | 精确产品 ID | `{contract_version, product, variants}`；查看身份和变体，包含待确认变体，不授予使用资格 |
| `resolve <query> [--provider ID] [--variant ID] --json` | 优先用已确认的产品 ID，也接受名称/别名 | 成功返回具体可用资产，失败返回状态和卡片降级 |

厂商使用索引中的 ID，例如 aliyun、aws、tencent、huawei、opensource、generic。厂商可扩展，不在消费者中写死白名单。产品 ID 大小写不敏感解析，变体 ID 使用 `show` 返回的精确值。

`matches` 包含产品数据以及 `score`、`variant_count`。分数只是排序依据，不是身份置信度；分数与同分顺序不作为稳定接口。产品/变体完整字段由 [catalog/schema.json](../catalog/schema.json) 定义。

已知产品 ID 时直接 resolve；自然语言先搜索，再依据用户描述、厂商和上下文确认产品。模糊匹配即使只剩一项也不可直接当唯一身份。未限定厂商时先匹配独立组件；没有独立条目时，允许按组件映射复用阿里云 SVG。保留组件名称和部署语义，不把节点改成托管云服务。待审核的独立条目不能绕过审核。

## 3. 组件身份、素材复用与选择优先级

未提供厂商、也未使用精确产品 ID 时：

1. 优先解析独立组件（opensource/generic/status）；若多个独立条目精确匹配，返回 ambiguous。独立条目或默认变体待审核时返回 pending_review，不绕过审核借用其他图标。
2. 独立组件不存在时，依据 [component-mappings.json](../catalog/component-mappings.json) 的明确关联，优先复用其中指定的阿里云产品 SVG。不能用模糊搜索第一项代替关联。当前配置 MySQL、Redis、Apache Kafka、Elasticsearch；后续组件可通过同一文件扩展。
3. 只有其他厂商素材、没有指定的阿里云素材时返回 vendor_only；不任意改用其他厂商。指定的阿里云素材待审核/损坏时如实报告，不静默换图。
4. 显式 `--provider` 或精确产品 ID 优先于组件复用策略，绝不跨越用户指定厂商。仅在已选择产品内应用下面的变体优先级。

组件标准名及别名：Apache Kafka ← Kafka / Apache Kafka / ApacheKafka；Elasticsearch ← Elasticsearch / ElasticSearch / Elastic Search / ES。ES 按映射查询，不做短字符串子串匹配。映射中的 opensource/* 是请求组件身份，可能尚无同名 catalog 产品；不能将其误当作已入库的独立官方图标。

`search` 返回相关的独立/厂商候选及 `component_name`。没有厂商约束的组件请求应将原组件名称交给 `resolve`，不能先把厂商候选 ID 当作用户选择，否则会绕过独立组件优先级和复用说明。



1. 用户本次明确指定的变体：`resolve <product-id> --variant <variant-id>`。
2. `catalog/preferences.json` 中该产品的用户偏好。
3. 产品的 `default_variant`。

`--variant` 只影响当前调用，不写偏好。变体必须属于该产品，且产品和变体都为 ready、资产为有效 vector SVG；指定错误时回退，不静默换成默认图标。不通过设置并恢复全局偏好来模拟临时选择。

相同 SVG 可被不同产品共享，但产品身份不合并。组件可只复用云产品的 SVG，素材所属产品身份仍保留。同一产品多个配色/样式由变体区分。ready 代表本地审核可用，不代表官方最新版认证。

普通绘图不导入、审核、删除或更改默认值。用户明确要求维护图标库时使用独立维护流程及其权限要求；这些命令不属于本只读消费接口。

## 4. 结果、状态与退出码

机器结构见 [agent-response.schema.json](schemas/agent-response.schema.json)。它校验三类命令的关键消费字段，允许兼容性新增字段；完整索引仍由 catalog schema 校验。使用库内 `library.check_schema` 或支持 Draft 2020-12 的验证器校验。Schema 检查不能替代读取文件后的路径、摘要验证。

示例快照：[成功解析](../examples/agent/resolved.json)、[名称歧义](../examples/agent/ambiguous.json)、[复用阿里云 SVG](../examples/agent/vendor-reuse.json)。成功示例的绝对路径已替换为 `/path/to/arch-icons`，仅说明格式；实际选择应实时调用 resolve，不能把快照当作当前偏好。

成功解析包含：

| 字段 | 含义 |
|---|---|
| `contract_version` / `status` | 必须为 `2` / `resolved` |
| `product_id` / `name` / `provider` / `kind` | 素材所属产品的信息；复用时不能用这些字段推断节点的部署厂商 |
| `display_name` / `requested_component` | 建议节点名称、请求组件的 ID 和标准名；用户已有标签优先保留 |
| `match_type` | exact_product、independent_component 或 vendor_svg_reuse；最后一种明确表示复用厂商 SVG |
| `diagnostic` | code、message、action：原因、用户可读提示和后续动作，成功复用也带来源说明 |
| `variant_id` / `style` / `selection` | 实际选中版本及原因；selection 为 explicit_variant、user_preference 或 catalog_default |
| `asset_path` / `asset_relative_path` | 当前机器绝对路径、相对于图标库的 assets 路径 |
| `sha256` / `representation` | 原始返回 SVG 字节摘要、固定为 vector |
| `theme.preserve_colors` / `theme.dark_backdrop` | 保留颜色、深色背景是否需要浅色底托 |

`optical_scale` 当前只在变体数据中，resolve v2 不返回它；不要假设已存在该字段。

| resolve 状态 | 退出码 | 消费动作 |
|---|---|---|
| `resolved` | 0 | 核对资产后嵌入 |
| `not_found` | 2 | 当前查询范围完全没有相关条目，使用卡片 |
| `vendor_only` | 2 | 只有厂商版本且没有可复用的阿里云映射目标；使用卡片或明确选择厂商 |
| `no_exact_match` | 2 | 存在相关素材但身份尚不确定；不能说“没有素材” |
| `ambiguous` | 2 | 用上下文缩小范围；仍不明确则卡片，影响图意时询问用户 |
| `needs_provider_context` | 2 | 仅有厂商版本且缺少组件映射；补充映射或指定厂商 |
| `pending_review` | 2 | 产品或所选变体待审核，使用卡片，提示浏览页审核 |
| `invalid_variant` | 2 | 指定变体不存在或不属于该产品；卡片并报告 |
| `unusable_variant` | 2 | 所选变体不存在或非矢量；卡片并报告 |
| `asset_integrity_error` | 2 | 文件缺失或哈希不符；卡片并报告 |

Agent 应依据 diagnostic 分别解释“没有素材”“只有厂商版本”“存在歧义”“待审核”，不要统一概括为“没找到”。vendor_svg_reuse 是成功状态，应说明已借用阿里云 SVG，而不是继续使用卡片。

以上失败状态都返回 `fallback: card`，可能含 `candidates`，不返回可消费资产。

search 无匹配仍退出 0、返回空 matches。show 产品不存在，以及命令处理中的运行异常，退出 1，返回 `{contract_version: 2, error: "..."}`。参数解析错误退出 2 并输出用法到 stderr，可能没有 JSON；进程无法启动等情况也不能假定有 JSON。**退出码 2 不一定是正常业务回退，必须同时检查 JSON 和状态。** 示例消费者自己的异常格式不属于 CLI 返回 schema。

## 5. SVG 读取与展示

- 只在退出 0 且版本、status 均正确时读取素材。检查相对路径落在 assets、绝对路径与相对路径指向同一文件；读取后核对 SHA-256，避免查询后文件变化。
- 建议产品图标框 56px，可随图表密度在 48–64px 调整；名称在下方。保留 viewBox 和比例，不拉伸或自动裁剪留白。字号、布局可按用户当前要求调整。
- 保留品牌原色，不统一染色；`dark_backdrop: true` 时在深色背景使用浅色底托。
- 节点类别、连接点、包含关系、连线和用户标签独立于图标。无匹配时保留该节点的卡片表达。
- 交付 HTML、SVG、DrawIO 等文件应嵌入素材，禁止把本机 assets 路径或 localhost URL 留作运行时依赖。
- 可使用独立 SVG data URI，或工具原生嵌入方式。直接内联时，同一素材的多个实例仍会有相同内部 ID；适配器需按节点实例重命名 ID 及其 url()/href 引用，不能只依赖素材的哈希前缀。
- SVG 转 PNG/GIF 是消费端导出步骤，不回写 assets。适配器要处理工具特定编码与 XML 转义，并验证最终图表与导出图片。某工具不支持时保持卡片并说明，不绕过其 schema 或校验。

## 6. 使用记录与复现

建议交付图表时附带 `icons.lock.json`，结构见 [icons-lock.schema.json](schemas/icons-lock.schema.json)。每个使用图标的节点记录 node_id、product_id、variant_id、sha256、asset_relative_path、selection、display_name、match_type，以及存在时的 requested_component；没有图标的节点不列入 icons。node_id 应与图表节点一一对应且不重复。

记录的是嵌入前源 SVG 的哈希；如果适配器重命名内部 ID，嵌入后字节可能不同。资产嵌入保证现有图表不会因偏好变化而改变。重新生成时可用记录中的素材产品和变体重新 resolve，再核对哈希；同时保留记录的 display_name 和 requested_component，不能把素材产品名覆盖到组件标签；如果产品/变体已删除、改为待审核或哈希变化，报告差异，不自动使用历史失效资产。该记录不保证字体、布局算法等整张图表的完全一致。

库不会在查询时自动写使用记录，绘图消费者负责保存。当前 [consume_icon.py](../examples/agent/consume_icon.py) 返回可保存的记录及 data URI，不写文件：

```bash
python3 "$ARCH_ICONS_ROOT/examples/agent/consume_icon.py" tencent/cos --node-id storage
```

该示例演示无 shell 拼接的命令调用、schema/退出码验证、路径检查、字节校验和嵌入准备，不是 archify spec 或 DrawIO XML 生成器。可将其 stdout 保存到临时文件查看，避免在 Agent 上下文中展开大量 SVG/base64。

## 7. 其他 Agent 与技能接入

公共技能位于 [skills/arch-icons/SKILL.md](../skills/arch-icons/SKILL.md)。本机可符号链接到技能发现目录；单独复制技能到其他 Agent 时设置 ARCH_ICONS_ROOT，指向同步后的完整项目。只读入口 `scripts/icon_query.py` 只开放 search、resolve、show。

支持技能的 Agent 在自身的技能目录注册此技能；不支持技能的 Agent 可在其项目指令中引用本文并直接调用 CLI。不要假设复制 reference 后所有 Agent 都会自动发现。可在绘图技能的“产品图标”步骤加入如下接入约定，并实现其渲染适配：

> 需要产品或组件图标时，使用 arch-icons 公共技能或其 CLI。遵循图标库 docs/agent-contract.md，先确认身份再 resolve，仅消费成功返回且校验通过的 SVG；不能可靠解析则使用卡片。将素材通过本绘图工具支持的方式嵌入，完成本工具要求的验证和导出。

库规范是共同选择规则的唯一维护位置，各绘图技能只维护格式适配。当前没有修改 archify、ai-drawio 或提供 MCP：archify 尚需正式本地 SVG 引用/渲染支持；ai-drawio 静态图需编码和布局适配，动图流程需另行接入。远程 Agent 无法访问文件时，后续再提供读取 SVG 内容的只读服务接口，不把本机路径当作远程可用数据。

## v1 → v2 迁移

v2 改变无厂商组件的选择策略，增加 not_found/vendor_only 状态及显示语义、来源诊断。CLI 和公共技能、响应 schema、消费者示例与使用记录同步升级；catalog 数据版本仍为 1。旧消费者若只接受 v1，应更新版本检查及字段处理后再使用，不能仅忽略版本号。旧交付文件不回写；已有 v1 lock 可用于查找历史素材，但需明确确认节点语义后再转换为 v2。
