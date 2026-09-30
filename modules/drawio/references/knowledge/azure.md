# Azure 云服务图标

Azure 用的是 SVG 图片路径而不是 `shape=`，格式：`shape=image;image=img/lib/azure2/<分类>/<服务>.svg`

| 路径 | 含义 |
|---|---|
| `img/lib/azure2/compute/Virtual_Machine.svg` | 虚拟机 |
| `img/lib/azure2/compute/Function_Apps.svg` | Azure Functions |
| `img/lib/azure2/compute/Container_Instances.svg` | Container Instances |
| `img/lib/azure2/storage/Storage_Accounts.svg` | Blob / Storage |
| `img/lib/azure2/databases/SQL_Database.svg` | SQL Database |
| `img/lib/azure2/app_services/App_Services.svg` | App Service |
| `img/lib/azure2/networking/Virtual_Networks.svg` | VNet |
| `img/lib/azure2/networking/Load_Balancers.svg` | Load Balancer |
| `img/lib/azure2/networking/Application_Gateways.svg` | App Gateway |
| `img/lib/azure2/containers/Kubernetes_Services.svg` | AKS |

## 用法

```xml
<mxCell id="vm1" value="Web VM"
        style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;verticalLabelPosition=bottom;verticalAlign=top"
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
</mxCell>
```

`verticalLabelPosition=bottom;verticalAlign=top` 让标签出现在图标下方。
