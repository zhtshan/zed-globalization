# 侧边栏「最近项目」弹窗排序功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ZedG 侧边栏「最近项目」弹窗支持自动排序（最近使用 / 名称升序 / 名称降序）与手动排序（右键上移 / 下移 / 置顶），并持久化排序偏好。

**Architecture:** 排序逻辑写成独立的 Rust 模块 `zedg_recent_sort`（纯函数 + JSON 状态文件），通过本仓库的一个 git patch 文件注入到上游 Zed 源码；CI 在 clone 上游后 `git apply` 该补丁，改动随既有的 `l10n.patch` 分发到全部平台构建。UI 改动集中在 `crates/recent_projects/src/sidebar_recent_projects.rs` 的 delegate：列表排序、每行右键菜单、footer 排序按钮。

**Tech Stack:** Rust（gpui UI 框架）、serde / serde_json（状态文件）、GitHub Actions（CI）、Python 无关（本计划不涉及 Python 代码）。

## Global Constraints

- 上游基线：**zed-industries/zed v1.27.0**（`git clone --depth 1 --branch v1.27.0`）。补丁必须能在该 tag 上干净应用。
- 本仓库（zed-globalization）**不含 Rust 源码**；所有 Rust 改动都通过 `patches/zedg-recent-projects-sort.patch` 交付。
- 上游沙盒路径固定为 `/Users/startiasoft/work/zed-upstream`（在本仓库之外，不进版本库）。
- 本仓库工作目录：`/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization`（git worktree，分支 `workerspace-order`）。
- 不修改上游数据库表结构；排序状态存放在 Zed 配置目录下的独立 JSON 文件。
- 新增 UI 文案必须使用**英文原文硬编码**（由 `zedl10n` 流水线提取翻译），不得直接写中文。
- 上游 `crates/recent_projects/Cargo.toml` 当前**没有** `serde` 依赖，需要新增 `serde.workspace = true`（workspace 已定义 `serde = { version = "1.0", features = ["rc"] }`）。
- 首次 `cargo test -p recent_projects` 需要编译 Zed 全部依赖，耗时很长（30–90 分钟）；后续增量编译快。所有测试命令都必须在沙盒里执行。

---

## 文件结构

**上游沙盒（`/Users/startiasoft/work/zed-upstream`，不进本仓库）**

| 文件 | 责任 |
|---|---|
| `crates/recent_projects/src/zedg_recent_sort.rs`（新增） | 排序模式枚举、排序纯函数、手动移动计算、状态文件读写 |
| `crates/recent_projects/src/recent_projects.rs` | 仅新增 `mod zedg_recent_sort;` 声明 |
| `crates/recent_projects/src/sidebar_recent_projects.rs` | delegate 接线：排序生效、行右键菜单、footer 排序按钮 |
| `crates/recent_projects/Cargo.toml` | 新增 `serde.workspace = true` |

**本仓库（zed-globalization）**

| 文件 | 责任 |
|---|---|
| `patches/zedg-recent-projects-sort.patch`（新增） | 上述 Rust 改动的完整 diff，CI 用 `git apply` 注入上游源码 |
| `.github/workflows/02-build.yml`（修改） | `prepare` job clone 上游后应用补丁 |
| `.github/workflows/01-translate.yml`（修改） | 提取字符串前应用补丁，保证新增文案能被翻译 |
| `docs/feature-patches.md`（新增） | 说明补丁的用途与上游升级后如何重新生成 |

---

### Task 1: 准备上游沙盒

**Files:**
- Create: `/Users/startiasoft/work/zed-upstream`（上游 clone，不在本仓库）
- Read: `docs/superpowers/specs/2026-11-05-recent-projects-sort-design.md`

**Interfaces:**
- Consumes: 无
- Produces: 沙盒仓库 `/Users/startiasoft/work/zed-upstream`，分支 `feature/recent-projects-sort`，基线为 tag `v1.27.0`；后续所有 Rust 任务都在此目录进行。

- [ ] **Step 1: 确认 Rust 工具链可用**

Run:
```bash
rustc --version && cargo --version
```
Expected: 两条命令都打印版本号且无 error。若缺失，安装 stable 工具链：`rustup toolchain install stable`。

- [ ] **Step 2: 克隆上游 v1.27.0**

Run:
```bash
rm -rf /Users/startiasoft/work/zed-upstream
git clone --depth 1 --branch v1.27.0 https://github.com/zed-industries/zed.git /Users/startiasoft/work/zed-upstream
```
Expected: 克隆成功（网络不稳时重试同一命令）。

- [ ] **Step 3: 建立工作分支并确认基线**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && git checkout -b feature/recent-projects-sort && git describe --tags && git log --oneline -1
```
Expected: `v1.27.0` 与一条 v1.27.0 的提交记录。

- [ ] **Step 4: 确认将要改动的三个文件存在**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && ls -l crates/recent_projects/src/recent_projects.rs crates/recent_projects/src/sidebar_recent_projects.rs crates/recent_projects/Cargo.toml
```
Expected: 三个文件都存在。

- [ ] **Step 5: 记录基线（用于后续生成补丁）**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && git rev-parse HEAD
```
Expected: 打印一串 commit SHA，**记下来**，Task 6 生成补丁时用它作 diff 起点。

---

### Task 2: 排序模块 `zedg_recent_sort`（纯逻辑 + 状态文件）

**Files:**
- Create: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/zedg_recent_sort.rs`
- Modify: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/recent_projects.rs:1-6`（加 `mod zedg_recent_sort;`）
- Modify: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/Cargo.toml`（加 `serde.workspace = true`）
- Test: 同文件内 `#[cfg(test)] mod tests`

**Interfaces:**
- Consumes: 无（Task 1 的沙盒）
- Produces: 模块 `crate::zedg_recent_sort`，导出
  - `pub enum SortMode { Recent, NameAsc, NameDesc, Manual }`，`Default = Recent`，带 `pub const ALL: [SortMode; 4]` 与 `pub fn label(self) -> &'static str`
  - `pub struct SortState { pub mode: SortMode, pub order: Vec<String> }`（`Serialize` / `Deserialize`，`mode` 用 `snake_case`）
  - `pub struct SortableEntry { pub key: String, pub name: String, pub index: usize }`
  - `pub enum MoveTarget { Up, Down, PinToTop }`
  - `pub fn project_key_from(path: &str, host: Option<&str>) -> String`
  - `pub fn display_name_from(path: &str) -> String`
  - `pub fn sort_entries(entries: &mut [SortableEntry], state: &SortState)`
  - `pub fn apply_move(display_order: &[String], key: &str, target: MoveTarget) -> Vec<String>`
  - `pub fn state_file_path() -> PathBuf`
  - `pub fn load_state() -> SortState` / `pub fn save_state(state: &SortState)`
  - `pub fn load_state_from(path: &Path) -> SortState` / `pub fn save_state_to(path: &Path, state: &SortState)`

- [ ] **Step 1: 先写失败测试**

创建 `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/zedg_recent_sort.rs`，**只写测试**（实现留空，让编译失败）：

```rust
//! ZedG 扩展：侧边栏「最近项目」弹窗的排序支持。
//!
//! 支持自动排序（最近使用 / 名称升序 / 名称降序）与手动排序（右键上移 / 下移 / 置顶）。
//! 排序偏好保存在 Zed 配置目录下的 `zedg-recent-projects-sort.json`。

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(key: &str, name: &str, index: usize) -> SortableEntry {
        SortableEntry {
            key: key.to_string(),
            name: name.to_string(),
            index,
        }
    }

    fn keys(entries: &[SortableEntry]) -> Vec<&str> {
        entries.iter().map(|e| e.key.as_str()).collect()
    }

    #[test]
    fn default_mode_is_recent() {
        assert_eq!(SortMode::default(), SortMode::Recent);
        assert_eq!(SortState::default().mode, SortMode::Recent);
        assert!(SortState::default().order.is_empty());
    }

    #[test]
    fn sorts_by_name_ascending_case_insensitively() {
        let mut entries = vec![
            entry("b", "beta", 0),
            entry("a", "Alpha", 1),
            entry("c", "Beta", 2),
        ];
        sort_entries(
            &mut entries,
            &SortState {
                mode: SortMode::NameAsc,
                order: vec![],
            },
        );
        assert_eq!(keys(&entries), vec!["a", "b", "c"]);
    }

    #[test]
    fn sorts_by_name_descending() {
        let mut entries = vec![
            entry("a", "Alpha", 0),
            entry("c", "gamma", 1),
            entry("b", "Beta", 2),
        ];
        sort_entries(
            &mut entries,
            &SortState {
                mode: SortMode::NameDesc,
                order: vec![],
            },
        );
        assert_eq!(keys(&entries), vec!["c", "b", "a"]);
    }

    #[test]
    fn recent_mode_keeps_original_order() {
        let mut entries = vec![entry("a", "Alpha", 0), entry("b", "Beta", 1)];
        sort_entries(
            &mut entries,
            &SortState {
                mode: SortMode::Recent,
                order: vec![],
            },
        );
        assert_eq!(keys(&entries), vec!["a", "b"]);
    }

    #[test]
    fn manual_order_wins_and_unknown_items_go_last() {
        let mut entries = vec![
            entry("a", "Alpha", 0),
            entry("b", "Beta", 1),
            entry("c", "Gamma", 2),
        ];
        let state = SortState {
            mode: SortMode::Manual,
            order: vec!["c".to_string(), "a".to_string()],
        };
        sort_entries(&mut entries, &state);
        assert_eq!(keys(&entries), vec!["c", "a", "b"]);
    }

    #[test]
    fn move_up_swaps_with_previous_item() {
        let order = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        assert_eq!(
            apply_move(&order, "c", MoveTarget::Up),
            vec!["a", "c", "b"]
        );
    }

    #[test]
    fn move_down_swaps_with_next_item() {
        let order = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        assert_eq!(
            apply_move(&order, "b", MoveTarget::Down),
            vec!["a", "c", "b"]
        );
    }

    #[test]
    fn pin_to_top_moves_item_first() {
        let order = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        assert_eq!(
            apply_move(&order, "c", MoveTarget::PinToTop),
            vec!["c", "a", "b"]
        );
    }

    #[test]
    fn project_key_includes_remote_host() {
        assert_eq!(project_key_from("/tmp/proj", None), "/tmp/proj");
        assert_eq!(
            project_key_from("/tmp/proj", Some("dev-box")),
            "dev-box::/tmp/proj"
        );
    }

    #[test]
    fn display_name_uses_last_path_segment() {
        assert_eq!(display_name_from("/home/me/work/proj"), "proj");
        assert_eq!(display_name_from("/"), "/");
    }

    #[test]
    fn state_round_trips_through_disk() {
        let dir = std::env::temp_dir().join(format!("zedg-sort-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        let state = SortState {
            mode: SortMode::Manual,
            order: vec!["/a".to_string(), "/b".to_string()],
        };
        save_state_to(&path, &state);
        assert_eq!(load_state_from(&path), state);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn missing_or_broken_state_falls_back_to_default() {
        let dir = std::env::temp_dir().join(format!("zedg-sort-test-broken-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        assert_eq!(load_state_from(&path), SortState::default());
        std::fs::write(&path, "{ not json").unwrap();
        assert_eq!(load_state_from(&path), SortState::default());
        std::fs::remove_dir_all(&dir).ok();
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib zedg_recent_sort 2>&1 | tail -30
```
Expected: 编译错误，提示 `SortMode` / `SortState` / `sort_entries` 等未定义（`cannot find type/function in this scope`）。

> 注意：首次编译会拉取并编译 Zed 全部依赖，耗时很长（30–90 分钟）。耐心等待，不要中断。

- [ ] **Step 3: 补上模块实现**

把 `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/zedg_recent_sort.rs` 整个替换为下面内容（保留上面的测试模块）：

```rust
//! ZedG 扩展：侧边栏「最近项目」弹窗的排序支持。
//!
//! 支持自动排序（最近使用 / 名称升序 / 名称降序）与手动排序（右键上移 / 下移 / 置顶）。
//! 排序偏好保存在 Zed 配置目录下的 `zedg-recent-projects-sort.json`。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// 排序状态文件名（位于 Zed 配置目录）。
pub const STATE_FILE_NAME: &str = "zedg-recent-projects-sort.json";

/// 排序模式。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortMode {
    /// 最近使用优先（与上游默认行为一致）。
    #[default]
    Recent,
    /// 按名称升序排列。
    NameAsc,
    /// 按名称降序排列。
    NameDesc,
    /// 手动顺序。
    Manual,
}

impl SortMode {
    /// 全部模式，顺序即菜单展示顺序。
    pub const ALL: [SortMode; 4] = [
        SortMode::Recent,
        SortMode::NameAsc,
        SortMode::NameDesc,
        SortMode::Manual,
    ];

    /// 菜单展示文案（会被汉化流程提取翻译）。
    pub fn label(self) -> &'static str {
        match self {
            SortMode::Recent => "Recent",
            SortMode::NameAsc => "Name (A-Z)",
            SortMode::NameDesc => "Name (Z-A)",
            SortMode::Manual => "Manual",
        }
    }
}

/// 排序状态，对应 `zedg-recent-projects-sort.json` 的内容。
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SortState {
    /// 当前排序模式。
    pub mode: SortMode,
    /// 手动顺序，元素是项目 key（见 [`project_key_from`]）。
    #[serde(default)]
    pub order: Vec<String>,
}

/// 参与排序的最小信息。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SortableEntry {
    /// 项目唯一标识。
    pub key: String,
    /// 参与名称排序的显示名。
    pub name: String,
    /// 排序前在原始列表中的下标，用于排序后还原条目。
    pub index: usize,
}

/// 手动调整的目标位置。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MoveTarget {
    /// 上移一位。
    Up,
    /// 下移一位。
    Down,
    /// 置顶。
    PinToTop,
}

/// 由路径与（可选的）远程主机名计算项目 key。
///
/// 本地项目直接用路径；远程项目用 `host::path`，避免不同主机上的同名项目冲突。
pub fn project_key_from(path: &str, host: Option<&str>) -> String {
    match host {
        Some(host) => format!("{host}::{path}"),
        None => path.to_string(),
    }
}

/// 由路径推导用于排序的显示名（最后一级目录名）。
pub fn display_name_from(path: &str) -> String {
    Path::new(path)
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_else(|| path.to_string())
}

/// 名称比较：大小写不敏感。
fn compare_names(a: &str, b: &str) -> std::cmp::Ordering {
    a.to_lowercase().cmp(&b.to_lowercase())
}

/// 按 `state` 对 `entries` 原地排序（稳定排序）。
pub fn sort_entries(entries: &mut [SortableEntry], state: &SortState) {
    match state.mode {
        // 上游数据库已按最近使用时间倒序返回，这里保持原顺序。
        SortMode::Recent => {}
        SortMode::NameAsc => entries.sort_by(|a, b| {
            compare_names(&a.name, &b.name).then_with(|| a.key.cmp(&b.key))
        }),
        SortMode::NameDesc => entries.sort_by(|a, b| {
            compare_names(&b.name, &a.name).then_with(|| a.key.cmp(&b.key))
        }),
        SortMode::Manual => {
            let position = |entry: &SortableEntry| {
                state
                    .order
                    .iter()
                    .position(|key| key == &entry.key)
                    .unwrap_or(usize::MAX)
            };
            entries.sort_by_key(position);
        }
    }
}

/// 在 `display_order` 中把 `key` 移动到指定位置，返回新的完整顺序。
///
/// `display_order` 是当前显示顺序（可能不包含 `key`）。
pub fn apply_move(display_order: &[String], key: &str, target: MoveTarget) -> Vec<String> {
    let mut order: Vec<String> = display_order
        .iter()
        .filter(|k| k.as_str() != key)
        .cloned()
        .collect();

    let Some(index) = display_order.iter().position(|k| k == key) else {
        // key 不在当前顺序里（正常不会出现）：置顶或追加到末尾。
        match target {
            MoveTarget::PinToTop => order.insert(0, key.to_string()),
            _ => order.push(key.to_string()),
        }
        return order;
    };

    match target {
        MoveTarget::Up => order.insert(index.saturating_sub(1).min(order.len()), key.to_string()),
        MoveTarget::Down => order.insert((index + 1).min(order.len()), key.to_string()),
        MoveTarget::PinToTop => order.insert(0, key.to_string()),
    }

    order
}

/// 排序状态文件路径（Zed 配置目录）。
pub fn state_file_path() -> PathBuf {
    paths::config_dir().join(STATE_FILE_NAME)
}

/// 从指定文件读取排序状态；文件不存在或内容损坏时返回默认状态。
pub fn load_state_from(path: &Path) -> SortState {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return SortState::default();
    };
    serde_json::from_str(&contents).unwrap_or_default()
}

/// 读取排序状态。
pub fn load_state() -> SortState {
    load_state_from(&state_file_path())
}

/// 把排序状态写入指定文件；失败时静默忽略，不影响使用。
pub fn save_state_to(path: &Path, state: &SortState) {
    let Ok(contents) = serde_json::to_string_pretty(state) else {
        return;
    };
    let _ = std::fs::write(path, contents);
}

/// 写回排序状态。
pub fn save_state(state: &SortState) {
    save_state_to(&state_file_path(), state);
}
```

- [ ] **Step 4: 挂载模块声明**

编辑 `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/recent_projects.rs` 顶部，把

```rust
mod dev_container_suggest;
pub mod disconnected_overlay;
mod remote_connections;
mod remote_servers;
pub mod sidebar_recent_projects;
mod ssh_config;
```

改为

```rust
mod dev_container_suggest;
pub mod disconnected_overlay;
mod remote_connections;
mod remote_servers;
pub mod sidebar_recent_projects;
mod ssh_config;
mod zedg_recent_sort;
```

- [ ] **Step 5: 补 `serde` 依赖**

编辑 `/Users/startiasoft/work/zed-upstream/crates/recent_projects/Cargo.toml`，把

```toml
semver.workspace = true
serde_json.workspace = true
```

改为

```toml
semver.workspace = true
serde.workspace = true
serde_json.workspace = true
```

- [ ] **Step 6: 运行测试确认通过**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib zedg_recent_sort 2>&1 | tail -30
```
Expected: 12 个测试全部 `ok`（`test result: ok. 12 passed; 0 failed`）。

若报 `unused import` 之类警告，确认不是错误即可（警告不阻塞）。

- [ ] **Step 7: 在沙盒提交**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && git add -A && git commit -m "feat(recent-projects): 新增 ZedG 排序模块 zedg_recent_sort"
```
Expected: 提交成功，涉及 3 个文件。

---

### Task 3: delegate 接线——让排序真正生效

**Files:**
- Modify: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/sidebar_recent_projects.rs`
  - `SidebarRecentProjectsDelegate` 结构体（加 `sort_state` 字段）
  - `SidebarRecentProjects::popover`（构造时加载状态）
  - `SidebarRecentProjectsDelegate::set_workspaces`（加载后排序）
  - `SidebarRecentProjectsDelegate::update_matches`（重新排序）
  - 新增 `impl` 方法：`key_for`、`name_for`、`apply_sort`、`current_display_order`

**Interfaces:**
- Consumes: Task 2 的 `crate::zedg_recent_sort`（`SortMode` / `SortState` / `SortableEntry` / `sort_entries` / `project_key_from` / `display_name_from`）
- Produces: delegate 上新增
  - `fn key_for(&self, workspace: &RecentWorkspace) -> String`
  - `fn name_for(&self, workspace: &RecentWorkspace) -> String`
  - `fn apply_sort(&mut self)`
  - `fn current_display_order(&self) -> Vec<String>`
  - `pub fn set_sort_mode(&mut self, mode: SortMode)`
  - `pub fn move_recent_project(&mut self, key: &str, target: MoveTarget)`
  - 字段 `sort_state: SortState`

- [ ] **Step 1: 加 import**

编辑 `crates/recent_projects/src/sidebar_recent_projects.rs`，把

```rust
use crate::{highlights_for_path, icon_for_remote_connection, open_remote_project};
```

改为

```rust
use crate::{
    highlights_for_path, icon_for_remote_connection, open_remote_project,
    zedg_recent_sort::{self, MoveTarget, SortMode, SortState, SortableEntry},
};
```

- [ ] **Step 2: 给结构体加字段**

在同一文件里，把

```rust
pub struct SidebarRecentProjectsDelegate {
    workspace: WeakEntity<Workspace>,
    window_project_groups: Vec<ProjectGroupKey>,
    workspaces: Vec<RecentWorkspace>,
    filtered_workspaces: Vec<StringMatch>,
    selected_index: usize,
    has_any_non_local_projects: bool,
    focus_handle: FocusHandle,
}
```

改为

```rust
pub struct SidebarRecentProjectsDelegate {
    workspace: WeakEntity<Workspace>,
    window_project_groups: Vec<ProjectGroupKey>,
    workspaces: Vec<RecentWorkspace>,
    filtered_workspaces: Vec<StringMatch>,
    selected_index: usize,
    has_any_non_local_projects: bool,
    focus_handle: FocusHandle,
    /// ZedG：最近项目排序状态。
    sort_state: SortState,
}
```

- [ ] **Step 3: 构造时加载状态**

在 `SidebarRecentProjects::popover` 里，把 delegate 的构造改为

```rust
            let delegate = SidebarRecentProjectsDelegate {
                workspace,
                window_project_groups,
                workspaces: Vec::new(),
                filtered_workspaces: Vec::new(),
                selected_index: 0,
                has_any_non_local_projects: false,
                focus_handle: cx.focus_handle(),
                sort_state: zedg_recent_sort::load_state(),
            };
```

- [ ] **Step 4: 替换 `set_workspaces`**

把

```rust
    pub fn set_workspaces(&mut self, workspaces: Vec<RecentWorkspace>) {
        self.has_any_non_local_projects = workspaces
            .iter()
            .any(|workspace| !matches!(workspace.location, SerializedWorkspaceLocation::Local));
        self.workspaces = workspaces;
    }
```

改为

```rust
    pub fn set_workspaces(&mut self, workspaces: Vec<RecentWorkspace>) {
        self.has_any_non_local_projects = workspaces
            .iter()
            .any(|workspace| !matches!(workspace.location, SerializedWorkspaceLocation::Local));
        self.workspaces = workspaces;
        self.apply_sort();
    }
```

- [ ] **Step 5: 新增排序相关方法**

在 `impl SidebarRecentProjectsDelegate {` 块内（`set_workspaces` 之后）追加：

```rust
    /// ZedG：项目的唯一标识（本地用路径，远程用 `host::path`）。
    fn key_for(&self, workspace: &RecentWorkspace) -> String {
        let path = workspace
            .identity_paths
            .ordered_paths()
            .next()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let host = match &workspace.location {
            SerializedWorkspaceLocation::Remote(options) => Some(options.display_name().to_string()),
            SerializedWorkspaceLocation::Local => None,
        };
        zedg_recent_sort::project_key_from(&path, host.as_deref())
    }

    /// ZedG：用于名称排序的显示名。
    fn name_for(&self, workspace: &RecentWorkspace) -> String {
        let path = workspace
            .identity_paths
            .ordered_paths()
            .next()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        zedg_recent_sort::display_name_from(&path)
    }

    /// ZedG：按当前排序模式重排列表。
    fn apply_sort(&mut self) {
        let original = std::mem::take(&mut self.workspaces);
        let mut entries: Vec<SortableEntry> = original
            .iter()
            .enumerate()
            .map(|(index, workspace)| SortableEntry {
                key: self.key_for(workspace),
                name: self.name_for(workspace),
                index,
            })
            .collect();
        zedg_recent_sort::sort_entries(&mut entries, &self.sort_state);
        self.workspaces = entries
            .iter()
            .map(|entry| original[entry.index].clone())
            .collect();
    }

    /// ZedG：当前显示顺序对应的 key 列表。
    fn current_display_order(&self) -> Vec<String> {
        self.workspaces.iter().map(|ws| self.key_for(ws)).collect()
    }

    /// ZedG：切换排序模式并持久化。切到手动模式时以当前显示顺序作为初始顺序。
    pub fn set_sort_mode(&mut self, mode: SortMode) {
        let mut state = self.sort_state.clone();
        state.mode = mode;
        if mode == SortMode::Manual {
            state.order = self.current_display_order();
        }
        zedg_recent_sort::save_state(&state);
        self.sort_state = state;
        self.apply_sort();
    }

    /// ZedG：手动调整某个项目的位置并持久化。
    pub fn move_recent_project(&mut self, key: &str, target: MoveTarget) {
        let display_order = self.current_display_order();
        let mut state = self.sort_state.clone();
        state.mode = SortMode::Manual;
        state.order = zedg_recent_sort::apply_move(&display_order, key, target);
        zedg_recent_sort::save_state(&state);
        self.sort_state = state;
        self.apply_sort();
    }
```

- [ ] **Step 6: 在 `update_matches` 开头重新排序**

在 `fn update_matches(&mut self, query: String, _: &mut Window, cx: &mut Context<Picker<Self>>) -> Task<()> {` 的**第一行**插入

```rust
        self.apply_sort();
```

（即插在 `let query = query.trim_start();` 之前。）

- [ ] **Step 7: 编译检查**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo check -p recent_projects 2>&1 | tail -30
```
Expected: `Finished dev profile` 或 `Checking recent_projects`，无 error。

若报 `cannot borrow self as mutable` 之类错误，说明 `apply_sort` 里的借用顺序有问题：把 `let original = std::mem::take(&mut self.workspaces);` 之后的 `self.key_for(workspace)` 调用改为先算好 entries 再赋值（当前写法已经是先算 entries 再赋值，若仍报错则把 `key_for`/`name_for` 改成不借用 `self` 的自由函数）。

- [ ] **Step 8: 回归测试**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib zedg_recent_sort 2>&1 | tail -10
```
Expected: 仍是 12 passed。

- [ ] **Step 9: 提交**

```bash
cd /Users/startiasoft/work/zed-upstream && git add -A && git commit -m "feat(recent-projects): 按排序状态重排侧边栏最近项目"
```

---

### Task 4: 行右键菜单（上移 / 下移 / 置顶）

**Files:**
- Modify: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/sidebar_recent_projects.rs`（`render_match`）

**Interfaces:**
- Consumes: Task 3 的 `SidebarRecentProjectsDelegate::move_recent_project`、`key_for`；`ui::right_click_menu`、`ui::ContextMenu`
- Produces: 每行右键弹出含 `Move Up` / `Move Down` / `Pin to Top` 的菜单；操作后立即刷新列表并持久化。

- [ ] **Step 1: 确认 `ui` 导入**

文件顶部已有 `use ui::{ButtonLike, KeyBinding, ListItem, ListItemSpacing, Tooltip, prelude::*};`，改为

```rust
use ui::{
    ButtonLike, ContextMenu, KeyBinding, ListItem, ListItemSpacing, Tooltip, prelude::*,
    right_click_menu,
};
```

- [ ] **Step 2: 在 `render_match` 中包裹右键菜单**

在 `render_match` 里，把结尾的

```rust
        Some(
            ListItem::new(ix)
                .toggle_state(selected)
                .inset(true)
                .spacing(ListItemSpacing::Sparse)
                .child(
                    h_flex()
                        .w_full()
                        .min_w_0()
                        .gap_3()
                        .flex_grow_1()
                        .when(self.has_any_non_local_projects, |this| {
                            this.child(Icon::new(icon).color(Color::Muted))
                        })
                        .child(highlighted_match.render(window, cx)),
                )
                .tooltip(move |_, cx| {
                    Tooltip::with_meta(
                        "Open Project in This Window",
                        None,
                        tooltip_path.clone(),
                        cx,
                    )
                })
                .into_any_element(),
        )
```

改为

```rust
        let picker = cx.entity();
        let key = self.key_for(workspace);

        let item = ListItem::new(ix)
            .toggle_state(selected)
            .inset(true)
            .spacing(ListItemSpacing::Sparse)
            .child(
                h_flex()
                    .w_full()
                    .min_w_0()
                    .gap_3()
                    .flex_grow_1()
                    .when(self.has_any_non_local_projects, |this| {
                        this.child(Icon::new(icon).color(Color::Muted))
                    })
                    .child(highlighted_match.render(window, cx)),
            )
            .tooltip(move |_, cx| {
                Tooltip::with_meta(
                    "Open Project in This Window",
                    None,
                    tooltip_path.clone(),
                    cx,
                )
            });

        Some(
            right_click_menu(SharedString::from(format!("recent-project-row-{ix}")))
                .menu(move |window, cx| {
                    ContextMenu::build(window, cx, move |mut menu, _, _| {
                        menu.entry("Move Up", None, {
                            let picker = picker.clone();
                            let key = key.clone();
                            move |window, cx| {
                                picker.update(cx, |picker, cx| {
                                    picker.delegate.move_recent_project(&key, MoveTarget::Up);
                                    let query = picker.query(cx);
                                    picker.update_matches(query, window, cx);
                                });
                            }
                        })
                        .entry("Move Down", None, {
                            let picker = picker.clone();
                            let key = key.clone();
                            move |window, cx| {
                                picker.update(cx, |picker, cx| {
                                    picker.delegate
                                        .move_recent_project(&key, MoveTarget::Down);
                                    let query = picker.query(cx);
                                    picker.update_matches(query, window, cx);
                                });
                            }
                        })
                        .entry("Pin to Top", None, {
                            let picker = picker.clone();
                            let key = key.clone();
                            move |window, cx| {
                                picker.update(cx, |picker, cx| {
                                    picker
                                        .delegate
                                        .move_recent_project(&key, MoveTarget::PinToTop);
                                    let query = picker.query(cx);
                                    picker.update_matches(query, window, cx);
                                });
                            }
                        })
                    })
                })
                .trigger(move |_, _, _| item.into_any_element())
                .into_any_element(),
        )
```

- [ ] **Step 3: 编译检查**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo check -p recent_projects 2>&1 | tail -40
```
Expected: 无 error。

常见错误与修法：
- `cannot borrow window as mutable more than once`：说明 `ContextMenu::build` 的 `window` 参数与闭包捕获冲突 → 确认菜单条目 handler 用的是 handler 自己的 `window` 参数（形如 `move |window, cx| { ... }`），不要捕获外层 `window`。
- `no method named query found`：说明 `picker.query(cx)` 的 `cx` 类型不对 → 改为 `picker.query(cx)` 且 `cx` 为 `Context<Picker<...>>`（`update` 闭包第二个参数）。
- `use of moved value key`：三个菜单条目都要各自 clone 一份 key → 把 `let key = self.key_for(workspace);` 改成在每个 handler 内部 `let key = key.clone();`（当前写法通过 `{ let picker = picker.clone(); move |...| {...} }` 块捕获，需要给 key 也各 clone 一份：`let key = key.clone();` 在每个块内）。

- [ ] **Step 4: 提交**

```bash
cd /Users/startiasoft/work/zed-upstream && git add -A && git commit -m "feat(recent-projects): 最近项目行右键菜单支持上移/下移/置顶"
```

---

### Task 5: footer 排序按钮（切换排序模式）

**Files:**
- Modify: `/Users/startiasoft/work/zed-upstream/crates/recent_projects/src/sidebar_recent_projects.rs`（`render_footer`）

**Interfaces:**
- Consumes: Task 3 的 `SidebarRecentProjectsDelegate::set_sort_mode`、`sort_state`
- Produces: footer 新增一个下拉按钮，显示当前排序模式，菜单含四种模式。

- [ ] **Step 1: 引入 `DropdownMenu`**

把 Task 4 之后的 `use ui::{...}` 改为

```rust
use ui::{
    ButtonLike, ContextMenu, DropdownMenu, KeyBinding, ListItem, ListItemSpacing, Tooltip,
    prelude::*, right_click_menu,
};
```

- [ ] **Step 2: 在 footer 中加排序按钮**

在 `render_footer` 里，把开头

```rust
    fn render_footer(&self, _: &mut Window, cx: &mut Context<Picker<Self>>) -> Option<AnyElement> {
        let focus_handle = self.focus_handle.clone();
```

改为

```rust
    fn render_footer(&self, window: &mut Window, cx: &mut Context<Picker<Self>>) -> Option<AnyElement> {
        let focus_handle = self.focus_handle.clone();

        let picker = cx.entity();
        let current_mode = self.sort_state.mode;
        let sort_menu = ContextMenu::build(window, cx, move |mut menu, _, _| {
            for mode in SortMode::ALL {
                let picker = picker.clone();
                menu = menu.toggleable_entry(
                    mode.label(),
                    mode == current_mode,
                    IconPosition::Start,
                    None,
                    move |window, cx| {
                        picker.update(cx, |picker, cx| {
                            picker.delegate.set_sort_mode(mode);
                            let query = picker.query(cx);
                            picker.update_matches(query, window, cx);
                        });
                    },
                );
            }
            menu
        });
        let sort_button = DropdownMenu::new_with_element(
            SharedString::from("recent-projects-sort"),
            h_flex()
                .w_full()
                .gap_1()
                .justify_between()
                .child(Label::new("Sort By"))
                .child(Label::new(current_mode.label()))
                .into_any_element(),
            sort_menu,
        );
```

并把返回的 `v_flex()` 里的第一个子元素（`.child({ let open_action = ... })`）之前插入

```rust
                .child(sort_button)
```

即最终形如：

```rust
        Some(
            v_flex()
                .p_1p5()
                .flex_1()
                .gap_1()
                .border_t_1()
                .border_color(cx.theme().colors().border_variant)
                .child(sort_button)
                .child({
                    let open_action = workspace::Open {
                        create_new_window: Some(false),
                    };
                    ...（原有内容不变）
                })
                ...
```

- [ ] **Step 3: 编译检查**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo check -p recent_projects 2>&1 | tail -40
```
Expected: 无 error。

常见错误与修法：
- `IconPosition` 未导入 → 加上 `use ui::IconPosition;`（`ui::prelude::*` 已包含 `IconPosition`，若仍报错则显式导入）。
- `Label::new(current_mode.label())` 类型不匹配 → `label()` 返回 `&'static str`，`Label::new` 接受 `impl Into<SharedString>`，`&str` 可转换 ✓。

- [ ] **Step 4: 回归测试 + 提交**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib zedg_recent_sort 2>&1 | tail -8 && git add -A && git commit -m "feat(recent-projects): 最近项目弹窗 footer 增加排序模式切换"
```
Expected: 12 passed，提交成功。

---

### Task 6: 生成补丁文件并验证

**Files:**
- Create: `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch`

**Interfaces:**
- Consumes: Task 2–5 在沙盒里的全部提交
- Produces: 本仓库的 `patches/zedg-recent-projects-sort.patch`，可被 `git apply` 到干净的 v1.27.0 源码树。

- [ ] **Step 1: 在沙盒生成 diff**

Run（把 `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization` 替换为 `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization`）:
```bash
cd /Users/startiasoft/work/zed-upstream && mkdir -p /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches && git diff v1.27.0..HEAD > /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch && wc -l /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch
```
Expected: 输出一个几百行的行数。

- [ ] **Step 2: 检查补丁内容包含三个文件**

Run:
```bash
grep '^diff --git' /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch
```
Expected: 恰好四行，分别对应：
```
diff --git a/crates/recent_projects/Cargo.toml b/crates/recent_projects/Cargo.toml
diff --git a/crates/recent_projects/src/recent_projects.rs b/crates/recent_projects/src/recent_projects.rs
diff --git a/crates/recent_projects/src/sidebar_recent_projects.rs b/crates/recent_projects/src/sidebar_recent_projects.rs
diff --git a/crates/recent_projects/src/zedg_recent_sort.rs b/crates/recent_projects/src/zedg_recent_sort.rs
```

- [ ] **Step 3: 在干净 clone 上验证补丁可应用**

Run:
```bash
rm -rf /tmp/zedg-patch-check && git clone --depth 1 --branch v1.27.0 https://github.com/zed-industries/zed.git /tmp/zedg-patch-check && cd /tmp/zedg-patch-check && git apply --check --verbose /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch && echo APPLY_OK
```
Expected: 输出 `APPLY_OK`。若失败，按报错信息回到沙盒修正后重新生成补丁（回到 Step 1）。

- [ ] **Step 4: 在应用补丁后的干净 clone 上编译检查**

Run:
```bash
cd /tmp/zedg-patch-check && git apply /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/patches/zedg-recent-projects-sort.patch && cargo check -p recent_projects 2>&1 | tail -20
```
Expected: 无 error（首次编译慢，耐心等待）。

- [ ] **Step 5: 在本仓库提交补丁**

Run:
```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && git add patches/zedg-recent-projects-sort.patch && git commit -m "feat(patch): 最近项目弹窗排序功能补丁（上游 v1.27.0 基线）"
```
Expected: 提交成功。

---

### Task 7: CI 集成

**Files:**
- Modify: `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/.github/workflows/02-build.yml`（`prepare` job）
- Modify: `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/.github/workflows/01-translate.yml`（克隆上游之后）

**Interfaces:**
- Consumes: Task 6 的 `patches/zedg-recent-projects-sort.patch`
- Produces: 构建流水线在 `prepare` job 打补丁；翻译流水线在提取字符串前打补丁。

- [ ] **Step 1: 构建流水线应用补丁**

编辑 `.github/workflows/02-build.yml`，在 `prepare` job 的

```yaml
      - name: 克隆 Zed 源码并检出 Tag
        run: |
          git clone --depth 1 --branch $BUILD_VERSION ${{ env.ZED_REPO }} zed
```

之后插入：

```yaml
      - name: 应用 ZedG 功能补丁（最近项目排序）
        run: git apply patches/zedg-recent-projects-sort.patch
```

> 说明：`prepare` job 末尾会把 `zed/` 目录的全部改动导出成 `l10n.patch`，各平台 job 只 clone + `git apply l10n.patch`，因此补丁只需在这里打一次。

- [ ] **Step 2: 翻译流水线应用补丁**

编辑 `.github/workflows/01-translate.yml`，在「克隆 Zed 源码」这一步的 `run` 脚本末尾（`fi` 之后）追加：

```bash

          # 应用 ZedG 功能补丁，确保新增界面文案能被提取翻译
          if git -C zed apply ../patches/zedg-recent-projects-sort.patch; then
            echo "ZedG 功能补丁应用成功"
          else
            echo "::warning::ZedG 功能补丁应用失败，新增文案可能未被翻译"
          fi
```

> 说明：该步骤在克隆失败回退到 master 时可能无法应用，因此不阻断流水线，只告警。

- [ ] **Step 3: 校验 YAML 语法**

Run:
```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/02-build.yml','.github/workflows/01-translate.yml']]; print('YAML_OK')"
```
Expected: 输出 `YAML_OK`。

若环境没有 PyYAML，改用 `python3 -m pip install pyyaml` 后重试，或用 `ruby -ryaml -e "YAML.load_file('.github/workflows/02-build.yml'); puts 'YAML_OK'"`。

- [ ] **Step 4: 核对插入位置**

Run:
```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && grep -n "应用 ZedG 功能补丁" -B 4 .github/workflows/02-build.yml .github/workflows/01-translate.yml
```
Expected: 两处都能看到，且 02-build 的那处在「克隆 Zed 源码并检出 Tag」之后、「应用 ZedG 品牌重命名」之前；01-translate 的那处在「克隆 Zed 源码」步骤内部。

- [ ] **Step 5: 提交**

```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && git add .github/workflows/02-build.yml .github/workflows/01-translate.yml && git commit -m "ci: 构建与翻译流水线应用最近项目排序补丁"
```

---

### Task 8: 补丁维护文档

**Files:**
- Create: `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/docs/feature-patches.md`

**Interfaces:**
- Consumes: Task 6/7 的补丁与流水线改动
- Produces: 维护者文档，说明补丁用途、基线版本、上游升级后的重新生成流程。

- [ ] **Step 1: 写文档**

创建 `/Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization/docs/feature-patches.md`：

````markdown
# ZedG 功能补丁

本仓库不包含 Zed 的 Rust 源码。构建时 CI 会克隆上游 Zed 源码，再应用本目录下的补丁文件，
把 ZedG 特有的功能与品牌改动一起打进最终产物。

补丁在 `prepare` job 中应用一次，产物以 `l10n.patch` 的形式分发给各平台构建 job。

## 现有补丁

| 补丁 | 基线版本 | 功能 |
|---|---|---|
| `zedg-recent-projects-sort.patch` | 上游 `v1.27.0` | 侧边栏「最近项目」弹窗的排序（自动排序 + 右键手动排序） |

## 应用位置

- `.github/workflows/02-build.yml`：`prepare` job 克隆上游后执行 `git apply patches/<name>.patch`
- `.github/workflows/01-translate.yml`：提取界面文案前同样打补丁，保证新增文案能被翻译

## 上游升级后如何重新生成补丁

1. 在沙盒里检出新的上游 tag：

   ```bash
   rm -rf /Users/startiasoft/work/zed-upstream
   git clone --depth 1 --branch v1.28.0 https://github.com/zed-industries/zed.git /Users/startiasoft/work/zed-upstream
   cd /Users/startiasoft/work/zed-upstream && git checkout -b feature/recent-projects-sort
   ```

2. 把旧补丁应用上去，冲突处按上游新代码手工调整：

   ```bash
   git apply /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   ```

3. 重新编译并测试：

   ```bash
   cargo test -p recent_projects --lib zedg_recent_sort
   cargo check -p recent_projects
   ```

4. 重新生成补丁并替换仓库里的文件：

   ```bash
   git diff v1.28.0..HEAD > /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   ```

5. 在干净 clone 上验证：

   ```bash
   rm -rf /tmp/zedg-patch-check
   git clone --depth 1 --branch v1.28.0 https://github.com/zed-industries/zed.git /tmp/zedg-patch-check
   cd /tmp/zedg-patch-check && git apply --check /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   ```

补丁失效时 CI 会在 `git apply` 步骤直接失败，不会静默产出缺少功能的安装包。
````

- [ ] **Step 2: 提交**

```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && git add docs/feature-patches.md && git commit -m "docs: 补充 ZedG 功能补丁的维护说明"
```

---

### Task 9: 端到端验证清单

**Files:**
- 无代码改动，仅验证。

**Interfaces:**
- Consumes: Task 2–8 的全部产物
- Produces: 一份验证结论（通过 / 未通过 + 实际观察）。

- [ ] **Step 1: 本地全量校验**

Run:
```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib 2>&1 | tail -15
```
Expected: 全部测试通过（含上游已有测试）。

- [ ] **Step 2: 确认补丁可重复应用且干净**

Run:
```bash
cd /Users/startiasoft/work/worktrees/zed-globalization/sturdy-rook/zed-globalization && git status --porcelain && git log --oneline -5
```
Expected: 工作区干净，能看到本计划的各个提交。

- [ ] **Step 3: 人工验证（需要能运行的 ZedG 构建）**

按顺序检查并记录结果：

1. 打开侧边栏的「最近项目」弹窗 → footer 出现「Sort By」按钮，显示当前模式（默认 `Recent`）
2. 点击「Sort By」→ 菜单列出 `Recent` / `Name (A-Z)` / `Name (Z-A)` / `Manual` 四项
3. 选 `Name (A-Z)` → 列表立即按名称升序
4. 关闭并重新打开弹窗 → 仍是名称升序（状态已持久化）
5. 右键任意一行 → 出现 `Move Up` / `Move Down` / `Pin to Top`
6. 执行 `Move Up` → 该项上移一位，且模式自动切到 `Manual`
7. 关闭重开 → 手动顺序保持
8. 在搜索框输入关键字 → 结果仍按搜索相关度排序，不受排序模式影响
9. 检查配置文件 `~/.config/zed/zedg-recent-projects-sort.json`（macOS 为 `~/Library/Application Support/Zed/zedg-recent-projects-sort.json`）内容为 `{"mode":"manual","order":[...]}`

- [ ] **Step 4: 记录验证结论**

在本仓库新增 `docs/superpowers/plans/2026-11-05-recent-projects-sort-verification.md`，写明：验证环境（上游版本）、第 3 步各项的实测结果、以及未通过项的说明。没有构建产物时，把第 3 步标注为「待人工验证」，不要写成已通过。

---

## 附：关键上游 API 速查（已核实）

| API | 位置 |
|---|---|
| `Picker::delegate` 为 `pub` 字段，可在 `picker.update` 中访问 | `crates/picker/src/picker.rs:128` |
| `Picker::update_matches(&mut self, query: String, window, cx)` | `crates/picker/src/picker.rs:1230` |
| `Picker::query(&self, cx: &App) -> String` | `crates/picker/src/picker.rs:1315` |
| `Context<T>` 实现 `Deref/DerefMut<Target = App>` | `crates/gpui/src/app/context.rs:25,33` |
| `Context<T>::entity() -> Entity<T>` | `crates/gpui/src/app/context.rs:50` |
| `ui::right_click_menu(id).menu(...).trigger(el)` | `crates/ui/src/components/right_click_menu.rs` |
| `ui::ContextMenu::build(window, cx, f)` / `menu.entry(label, None, handler)` | `crates/ui/src/components/context_menu.rs:288,501` |
| `ui::DropdownMenu::new_with_element(id, label, menu)` | `crates/ui/src/components/dropdown_menu.rs:69` |
| `PathList::ordered_paths() -> impl Iterator<Item = &PathBuf>` | `crates/util/src/path_list.rs:94` |
| `paths::config_dir() -> &'static PathBuf` | `crates/paths/src/paths.rs:122` |
| `SerializedWorkspaceLocation::{Local, Remote(RemoteConnectionOptions)}` | `crates/workspace/src/persistence/model.rs:43` |
| 最近项目 DB 顺序 `ORDER BY timestamp DESC` | `crates/workspace/src/persistence.rs:1892` |
