# 共享图标模块

默认使用 arch-icons（原 icon-personal）的 `assets/icon-pack` 独立本地快照；仅查询包内 `modules/icons/engine` 代码，不执行外部资源包中的程序。更名不触发自动同步，外部数据仅通过任务的 `icons.root` 显式选择，不依赖源项目环境变量。

- 先 search 确认身份，再 resolve；禁止取搜索第一项冒充确定匹配
- 显式请求变体 > 资源包用户偏好 > catalog default
- 优先独立组件；仅按已有明确映射借用厂商 SVG，provider 不等于部署位置
- task.icons.bindings 以 node_id 绑定，不覆盖业务原标签和关系
- 未匹配、歧义、待审核等状态逐项保留，使用普通节点继续出图
- 路径、摘要、响应契约或 SVG 安全校验失败时拒绝素材，明确报告，不伪装 resolved
- 检查 assets 边界、绝对/相对路径一致、原始 SHA256，使用 SVG 允许列表拒绝脚本、事件、DTD、foreignObject 和外部资源
- 安全序列化后记录新的哈希，不用它替代源文件哈希；原素材不修改
- HTML 和 DrawIO 用内嵌 SVG 图片隔离内部 ID，保留颜色，按主题提示添加浅底托
- 不对整个图标快照承诺统一再分发许可；本包来源见 manifest 与 licenses

公开命令限 search / show / resolve / bind；维护、导入、偏好修改和审核不属于本 Skill。
更完整的上游身份及失败契约见 `icon-contract-upstream.md`，其中旧路径和维护命令仅作背景，不照搬执行。
