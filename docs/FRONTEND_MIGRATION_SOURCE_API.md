# 原文页接口

新阅读器使用当前站点的身份 Cookie 读取个人书架中的 PDF。所有页码均为 **PDF 物理页码，从 1 开始**；课程引用中的 `page_number` 可直接用于请求。扫描图上的印刷页码可能与 PDF 页码不同，因此接口只有在有可靠数据时才会填写 `printed_page_number`，目前返回 `null`。

## 页元数据

`GET /api/books/{book_id}/pages/{page_number}` 返回 JSON，例如：

```json
{
  "book_id": "example-book",
  "page_number": 3,
  "page_count": 24,
  "text": "这一页识别出的正文……",
  "image_url": "/api/books/example-book/pages/3/image",
  "printed_page_number": null
}
```

`page_count` 来自源 PDF。`text` 优先采用该 PDF 对应的归一化 OCR 页文本，缺失时使用 PDF 内嵌文本；扫描页没有可用文本时为 `null`。它是原文展示资料，不保证等于课程生成后的改写内容。`image_url` 为相对路径，不包含文件系统路径，阅读器应携带同一身份 Cookie 访问。

## 页图

`GET /api/books/{book_id}/pages/{page_number}/image` 返回真实源 PDF 页的 `image/png`。服务按请求渲染单页并将近期页图缓存在后端进程内，源文件大小或修改时间变化后使用新的缓存键；不预生成整本书。页元数据和页图均重新校验权限，响应带 `Cache-Control: private, no-store`，因此切换账号、移出书架后不会从浏览器缓存继续取得页图。

个人书架里的 canonical 教材始终从 canonical PDF 取页；别名上传 ID 不会使一个拥有者读到另一账号上传的副本。正在处理但尚未领取的上传只允许已绑定的上传者访问，且仍须等到结构解析完成。

| 状态码 | 情况 | 阅读器处理 |
| --- | --- | --- |
| 401 | 登录 Cookie 已过期 | 引导重新登录后恢复当前页 |
| 403 | 当前身份没有该书的书架或上传归属 | 提示先加入书架或切回正确账号 |
| 409 | PDF 尚未完成结构解析 | 显示处理中并允许稍后重试 |
| 404 | 原始 PDF 文件缺失 | 显示原文不可用，允许重新上传或联系管理员 |
| 422 | 页码小于 1、超过 PDF 页数、PDF 已加密或损坏 | 保留当前阅读位置并显示具体错误 |
| 503 | 后端未安装 `pymupdf`（`pdf` 可选依赖） | 显示页图服务暂不可用，不用演示图替代 |

上传、领取、登录和访客身份沿用现有接口；前端无需传入或缓存本地 PDF 路径。移出书架不会删除 PDF 或学习记录。
