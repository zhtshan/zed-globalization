# 侧边栏「最近项目」弹窗排序功能 设计文档

日期：2026-11-05
状态：已确认（待实现）

## 1. 背景与目标

目标：ZedG（Zed 汉化版）的侧边栏「最近项目」弹窗（`SidebarRecentProjects`）支持排序，包括自动排序与手动排序。

现状问题：该弹窗的列表顺序完全沿用数据库查询顺序（`ORDER BY timestamp DESC`，即最近使用优先），用户无法改变。

明确范围：只做侧边栏这个弹窗。Zed 里另外两处「最近项目」入口不在本次范围内：

- 项目选择器模态框（`crates/recent_projects/src/recent_projects.rs` 的 `RecentProjectsDelegate`，三段式列表）——不在范围内
- 「当前文件夹」段（`get_open_folders`，已硬编码按名称排序）——不在范围内

## 2. 上游现状（已核实）

核实基线：`zed-industries/zed` v1.27.0（最新稳定版）与 `main` 分支一致；另有预发布 v1.28.0-pre。

| 事项 | 结论 | 依据 |
|---|---|---|
| 弹窗列表顺序来源 | DB 查询 `ORDER BY timestamp DESC` | `crates/workspace/src/persistence.rs:1886-1893` |
| 列表数据传入 | `db.recent_project_workspaces(fs)` → `set_workspaces` | `crates/recent_projects/src/recent_projects.rs:894`、`crates/workspace/src/persistence.rs:2109` |
| 弹窗是否自行排序 | 否。空查询时按 `self.workspaces` 原序输出 | `crates/recent_projects/src/sidebar_recent_projects.rs:163-208` |
| 行渲染 | `ListItem`，支持右键钩子 `on_secondary_mouse_down` | `crates/recent_projects/src/sidebar_recent_projects.rs:293-378`、`crates/ui/src/components/list/list_item.rs:185` |
| footer 挂载点 | `PickerDelegate::render_footer` 已实现（含两个按钮） | `crates/recent_projects/src/sidebar_recent_projects.rs:381-441`、`crates/picker/src/picker.rs:408-424` |
| 选中项菜单 | `PickerDelegate::actions_menu`（可选实现） | `crates/picker/src/picker.rs:417-424` |
| 手动排序先例 | 侧边栏项目组已有「上移/下移」动作 | `crates/workspace/src/multi_workspace.rs:892-930` |
| 持久化位置 | `paths::config_dir()`（与 settings.json 同目录） | `crates/paths/src/paths.rs:18,122` |
| crate 依赖 | `paths` / `serde_json` / `ui` / `picker` 均已存在，无需改 Cargo.toml | `crates/recent_projects/Cargo.toml` |

结论：上游完全无「最近项目排序」能力，需要自行实现。

## 3. 功能规格

### 3.1 排序模式

持久化枚举，默认 `recent`（与现状一致，不改变现有用户体验）：

| 值 | 含义 |
|---|---|
| `recent` | 最近使用优先（沿用 DB 的 timestamp 倒序） |
| `name_asc` | 名称升序（A→Z） |
| `name_desc` | 名称降序（Z→A） |
| `manual` | 手动顺序 |

名称比较：以弹窗中显示的项目名（即 `identity_paths` 派生出的展示名）为准，大小写不敏感。

### 3.2 手动排序交互

- 手动模式下，每行**右键**弹出菜单：`Move Up` / `Move Down` / `Pin to Top`。
- 在非手动模式下执行这三个操作之一时，**自动切换到 `manual` 模式**并落盘，再执行操作。此时 `order` 用**当前显示顺序**初始化（即切换瞬间看到的列表顺序），再执行本次操作。
- 操作立即重排当前列表并写盘，无需重启弹窗。
- `Pin to Top` 将该项移到手动顺序首位。

### 3.3 排序模式切换入口

弹窗 footer 新增一个「排序」按钮（`Label: "Sort By"` + 当前模式名），点击弹出菜单列出四种模式，单选。

不占用键盘快捷键，保持最小改动。

### 3.4 持久化

文件：`config_dir()/zedg-recent-projects-sort.json`

```json
{
  "mode": "manual",
  "order": ["/Users/me/work/proj-a", "/Users/me/work/proj-b"]
}
```

- `mode`：排序模式枚举字符串。
- `order`：手动顺序，元素是项目标识 key，**按手动顺序从前往后**。
- key 取值：
  - 本地项目：`identity_paths.ordered_paths()` 的路径字符串（多路径时取第一个）
  - 远程项目：`"{host}::{path}"`，host 取 `SerializedWorkspaceLocation::Remote(options).display_name()`
- 从 `manual` 切回其它模式时，`order` 保留在文件中，切回 `manual` 时恢复。
- 文件不存在 / 解析失败：按 `recent` 模式处理，不报错、不阻断弹窗。

### 3.5 边界行为

- **搜索中**：有关键字时保持 fuzzy 相关度排序，排序模式不生效（搜索行为不变）。
- **未登记项**：手动模式下不在 `order` 里的项目，追加到列表末尾，内部顺序沿用 DB 的 timestamp 倒序。
- **残留项**：`order` 里指向已删除项目的条目，读取时忽略（惰性清理，不主动写回）。
- **重复项**：同一项目出现多条记录时，以 key 首次出现为准。
- **空列表**：无最近项目时 footer 排序按钮仍可见但只有四种模式可切。

## 4. 实现方案

### 4.1 交付形态

本仓库新增补丁文件 `patches/zedg-recent-projects-sort.patch`（标准 git diff，基线为上游 v1.27.0）。

理由：本仓库不含 Rust 源码，CI 是 clone 上游 tag 后打补丁；补丁以 diff 形式存在可直接 review、可 rebase，`git apply` 失败会直接报错而不是静默失效。

### 4.2 补丁涉及的上游文件

| 文件 | 改动 |
|---|---|
| `crates/recent_projects/src/recent_projects.rs` | 增加 `mod zedg_recent_sort;` 声明 |
| `crates/recent_projects/src/zedg_recent_sort.rs`（新增） | 排序模式枚举、读写 `zedg-recent-projects-sort.json`、排序纯函数、项目 key 计算 |
| `crates/recent_projects/src/sidebar_recent_projects.rs` | 空查询时按模式排序；行右键菜单；footer 排序按钮 |

### 4.3 核心逻辑

新增模块 `zedg_recent_sort` 提供：

- `enum SortMode { Recent, NameAsc, NameDesc, Manual }`，`Default = Recent`，`serde` 序列化用 `snake_case`
- `fn key_for(workspace: &RecentWorkspace) -> String`：按 3.4 规则生成项目标识
- `fn load(fs: &dyn Fs) -> SortState`：读取配置文件，失败时返回默认值
- `fn save(fs: &dyn Fs, state: &SortState)`：原子性要求不高的简单写入
- `fn sort(workspaces: &mut Vec<RecentWorkspace>, state: &SortState)`：按模式原地重排

排序调用点：`SidebarRecentProjectsDelegate::update_matches` 中，空查询分支构造 `filtered_workspaces` **之前**先调用 `sort`。

手动顺序变更流程：右键菜单动作 → 重排 `self.workspaces` → 更新 `SortState.order` → 写盘 → 重新触发 `update_matches` 刷新列表。

### 4.4 状态加载时机

`SidebarRecentProjectsDelegate::set_workspaces` 被调用时（弹窗打开、DB 查询返回后）加载 `SortState` 并排序；`SortMode` 与 `order` 保存在 delegate 上，供右键菜单与 footer 按钮读写。

## 5. CI 集成

### 5.1 构建流水线（`.github/workflows/02-build.yml`）

在 `prepare` job 的「克隆 Zed 源码并检出 Tag」之后、「应用 ZedG 品牌重命名」之前新增一步：

```yaml
- name: 应用最近项目排序补丁
  run: git apply patches/zedg-recent-projects-sort.patch
```

位置说明：`prepare` job 末尾会把改动整体导出为 `l10n.patch`（`git add -A && git diff --cached --binary`），各平台 job 只 clone + `git apply l10n.patch`，因此补丁只需在 `prepare` 打一次即可分发到 Linux / Windows / macOS 全部架构。

### 5.2 翻译流水线（`.github/workflows/01-translate.yml`）

补丁新增的 UI 字符串（`Sort By`、`Move Up`、`Move Down`、`Pin to Top` 等）若不处理，会在界面上显示英文。原因是 `01-translate.yml` clone 的是**未打补丁的**上游源码做字符串提取。

修法：在 `01-translate.yml` 的「克隆 Zed 源码」之后，同样先应用补丁再提取：

```yaml
- name: 应用最近项目排序补丁
  run: git apply patches/zedg-recent-projects-sort.patch || echo "补丁未应用，跳过（新增字符串可能未翻译）"
```

容错说明：该 workflow 在取不到指定 tag 时会 fallback 到 clone 最新 master（此时补丁可能不适用），因此此处不阻断流水线，仅告警。

## 6. 汉化处理

- 新增字符串走既有 `zedl10n` 流程：提取 → AI 翻译 → 写入 `i18n/<version>/<lang>.json`。
- 支持语言沿用现有 4 种：`zh-CN` / `zh-TW` / `ja` / `ko`。
- 若某语言缺少新 key，`zedl10n replace` 保留英文原文（既有降级行为），不影响构建。

## 7. 验证方案

1. **补丁可应用**：本地 clone 上游 v1.27.0（`git clone --depth 1 --branch v1.27.0`），`git apply patches/zedg-recent-projects-sort.patch` 成功。
2. **编译通过**：`cargo check -p recent_projects`（需完整 Rust 工具链与 workspace 依赖，首次较慢）。
3. **CI 回归**：`02-build.yml` 的 `prepare` job 已含 `cargo check` 步骤，补丁引入的编译错误会在构建阶段暴露。
4. **行为验证**（人工，需编译可运行的 ZedG）：
   - 打开侧边栏最近项目弹窗，footer 出现「排序」按钮
   - 切换「名称升序」，列表顺序变化并重启后保持
   - 右键某项 → 上移 / 下移 / 置顶，顺序变化并重启后保持
   - 输入搜索关键字时顺序仍按相关度
5. **翻译验证**：构建产物的弹窗内按钮文案为中文而非英文。

## 8. 风险与回滚

| 风险 | 表现 | 应对 |
|---|---|---|
| 上游升级改了同名文件 | `git apply` 失败 → CI 立即失败 | 重新生成补丁（rebase 到新 tag）；临时回滚可把 apply 步骤改为 `|| true` 跳过功能 |
| `01-translate.yml` fallback 到 master | 补丁不适用 → 新字符串未翻译 | 已用 `|| echo` 容错；补一次翻译即可 |
| 排序状态文件损坏 | JSON 解析失败 | 加载时回退到 `recent`，不阻断弹窗 |
| 手动顺序与实际项目对不上 | 列表出现重复/缺失 | 排序时以 key 匹配，匹配不上的项目追加到末尾 |

回滚方式：删除 `patches/zedg-recent-projects-sort.patch` 并移除两处 CI 步骤即可，不影响既有功能。

## 9. 明确不做的事

- 不改项目选择器模态框的「Recent Projects」段
- 不改侧边栏项目组（多工作区）的顺序逻辑
- 不加拖拽重排（本次只做右键菜单）
- 不加设置 UI 页面（排序模式只在弹窗内切换）
- 不改数据库表结构（用独立 JSON 文件持久化）
