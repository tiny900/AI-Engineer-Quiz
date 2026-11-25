# 交付说明：素材检索与结果导出

- Fork：<https://github.com/tiny900/AI-Engineer-Quiz>
- 分支：`feature/search-export`（基于题目版本 `3aa137c`）
- 最终 commit SHA：见回复邮件（SHA 无法写进包含它自己的提交里）

## 1. 运行与验证

需要 Python 3.10+。在仓库根目录执行：

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
export KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false   # PowerShell: $env:KEEPSTACK_AI_ENABLED="false"; $env:KEEPSTACK_OCR_ENABLED="false"

uv run --no-sync python -m keepstack seed        # 原有 24 个演示素材 + 3 个集合
uv run --no-sync python -m keepstack seed-quiz   # 新增：本题场景数据（可重复执行，已存在则跳过）
uv run --no-sync python -m keepstack             # http://localhost:8000  admin / admin

uv run --no-sync python -m pytest -q             # 自动化测试
```

不用 uv 时，也可以按 CI 的方式安装：`pip install -r requirements.txt pytest httpx`。

### 页面上复现业务场景

1. 集合选 **Harbor Campaign**，顶部关键词输入 `harbor`。
2. "Must have tags" 依次输入 `approved`、`campaign`（每个按回车），"Exclude tags" 输入 `internal`。
3. 排序选 "Created: oldest first"，每页 12 条：应显示 **14 条**，分 2 页，第 2 页是 `Harbor batch shot 12` 和 `Harbor ferry 港口渡轮`。
4. 点 **Export CSV**：得到这 14 行，顺序与两页列表一致。

`seed-quiz` 的数据设计（[keepstack/quiz_data.py](keepstack/quiz_data.py)）：

- 应匹配：1 条普通素材、12 条 **created_at 完全相同** 的素材，以及 1 条含中文、逗号、引号、换行的素材。
- 应被排除，每条恰好违反一条规则：带 `internal`、缺少 `campaign`、在其他集合、关键词不匹配、已软删除。

## 2. 实测结果

- `pytest`：**80 passed, 2 skipped**。2 个跳过的是原有的 CLIP / OCR 测试，本机没有对应模型和 Tesseract。
- 新增 [tests/test_search_export.py](tests/test_search_export.py) 共 58 个用例，覆盖：
  - 业务场景，以及每个筛选条件单独起作用；
  - 包含标签逐个都会检查（只有 `approved`、只有 `campaign` 的素材都被排除，与标签顺序无关），多个排除标签中任意一个命中即排除；
  - 标签大小写、重复标签、整名匹配，同一标签既包含又排除时返回空；
  - 含 `/`、`,`、`%`、引号、中文的标签可以添加、筛选和删除；
  - 列表不能通过 `status` 参数返回非 active 素材；超出 SQLite 整数范围的参数返回 422，不再是 500；
  - 有关键词时也遵守排序；
  - 相同 created_at 下用 4 种页大小、2 种排序翻完所有页，无重复、无遗漏，两次翻页顺序一致；
  - 越界 offset，非法 limit / offset / sort 返回 422；
  - 11 种特殊字符输入，以及纯标点输入等价于空白；
  - 导出等于所有分页按顺序拼接，表头、BOM 和 CSV 转义正确，0 条结果仍有表头，未登录返回 401；
  - 修改标题、描述，增删标签（包括只通过标签命中关键词的情况），软删除之后，下一次查询和导出都随之变化。
- **测试有效性检查**：临时把实现改成"包含标签只检查最后一个""排除标签只检查第一个"，或回退本轮的修复，对应用例都会失败。检查完已还原。
- 第一版提交后用 Codex 做了一轮独立审核，确认的问题已在 `600b897`、`93b9365`、`12f02c2` 中修复。
- 浏览器手工验证（本地 Chrome 内核）：
  - 业务场景结果为 14 条，翻页正确，生效条件 chip 可以单独移除或"Clear all"；
  - 导出文件名和内容正确；
  - 人为放慢旧请求时，最终显示的是最新条件的结果；
  - 模拟 500 时显示错误信息和 Retry；
  - 在只匹配渡轮素材的条件下（关键词 `ferry` 加场景的标签条件），在详情抽屉给它加上 `internal` 标签，列表立即从 1 条变成 0 条；删掉这个标签后恢复；
  - 在标签筛选框里键入 `news,2026` 后，它保持为一个完整的标签；
  - 模拟保存失败时显示带原因的错误 toast。

## 3. 修复的已有缺陷

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| 有关键词时强制按 `bm25` 排序 | 违反"有关键词时也遵守所选排序" | 始终使用所选排序 |
| 排序没有唯一的次序键 | created_at 相同时分页会重复或遗漏 | 所有排序末尾加 `a.id` |
| 纯标点关键词生成空的 `MATCH` | 返回 500 | 视为没有关键词 |
| `limit` 没有下限，`offset` 可以为负 | `limit=-1` 在 SQLite 中等于不限条数 | API 层校验，非法时返回 422 |
| `collection_id=0` 被当成"不限" | 边界错误 | 改为判断 `is not None` |
| 前端 `api()` 带 body 时没有设置 method | 浏览器里登录、**详情页加标签**、新建集合、分享都直接失败 | 有 body 时默认用 POST |
| 侧栏统计是全库数字，却显示得像当前匹配数 | 误导用户 | 标题注明 "all assets"，接口返回 `facets_scope: "catalog"` |
| 列表接口接受 `status` 参数 | 可以列出已删除素材，与导出不一致 | 列表固定只返回 active |
| 超出 2^63-1 的 `offset` / `collection_id` | SQLite 绑定溢出，返回 500 | 校验上限，返回 422 |
| 删除标签的路由参数不能包含 `/` | 含 `/` 的标签加得上、删不掉（405） | 路由改为 `{name:path}` |
| 详情抽屉的编辑操作失败时没有反应 | 用户不知道修改没有生效 | 显示带原因的错误 toast |

## 4. 主要取舍

- **列表和导出共用同一个 WHERE / ORDER BY**（`search._filter_sql` 加 `SORTS`），两个接口接受的筛选参数也相同，且都固定只查 active，所以不需要维护两份逻辑。条件全部写成 `EXISTS` / `IN` 子查询，不用 JOIN，所以同一个素材不会重复出现，`COUNT(*)` 也准确。
- **包含标签**的判断是"命中的不同标签数等于要求的个数"；**排除标签**用 `NOT EXISTS ... IN (...)`。同一标签既包含又排除时，两个条件自然互斥，结果为空，不需要特殊分支。标签的规范化方式与入库一致：去掉首尾空白、转小写、去重。
- **分页用 limit / offset，不用 keyset**。题目只要求数据不变时稳定，加上 `id` 作为次序键就足够了，而且上一页 / 下一页和"第几页"实现简单。代价是数据量很大时深分页会变慢，数据变化时可能错位；题目明确不要求跨请求快照。
- **CSV 在内存里生成后一次性返回**，没有用 `StreamingResponse`。原因是项目的 SQLite 连接是线程本地的，而 Starlette 的同步生成器会在线程池里逐块迭代，跨线程使用同一个游标有风险。代价是导出非常大时内存占用高；当前规模（几百到几千条）没有问题。
- **CSV 格式**：UTF-8 带 BOM（Excel 能正确识别中文）、RFC 4180 转义（由标准库 `csv` 处理）、`\r\n` 换行。多个标签按名称排序后用 `|` 连接，例如 `approved|campaign|ferry`。
- **导出鉴权**：沿用 Bearer token。普通 `<a href>` 带不上请求头，所以前端用 `fetch` 拿到 blob 再触发下载，失败时 toast 提示。导出会写审计日志 `asset.export`。
- **前端竞态**：新请求会 `abort` 旧请求，同时用请求序号丢弃晚到的响应。只用其中一种不够：abort 来不及取消已经返回的响应，单靠序号又会浪费请求。
- **没有引入新依赖**。`httpx` 原本就在 `[dev]` 依赖里，只是 CI 漏装了，已补上。CI 也改为在 `feature/**` 分支 push 时运行。
- 语义搜索入口已隐藏，接口保留不动。

## 5. 限制与未完成项

- **CSV 公式注入**：没有处理以 `= + - @` 开头的字段。为了保持数据原样没有自动加前缀；如果导出文件会被 Excel 打开，建议后续加上可选的转义开关。
- 标签名本身含 `|` 时，导出列存在歧义（当前数据里没有这种情况）。
- 语义模式（`mode=semantic`）不支持新的标签和集合筛选，入口已隐藏。
- 前端没有浏览器自动化测试。条件重置、乱序响应、失败重试、下载失败等前端行为只做了上面列出的手工验证；pytest 覆盖的是后端接口的行为。
- 移动端布局只依赖原有样式的自动换行，没有专门适配。

### 发现但不在本题范围内的问题

- `remove_tag` 只重建了 FTS 索引，没有重建 embedding（只影响语义搜索）。
- FastAPI 的 `on_event` 已弃用，会产生 DeprecationWarning。

## 6. AI 工具使用

开发过程中使用了 Claude Code，用于阅读代码、实现、编写测试和在浏览器里验证。所有改动都经过测试和手工核对，提交历史按实际开发顺序保留。
