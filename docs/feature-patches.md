# ZedG 功能补丁

本仓库不包含 Zed 的 Rust 源码。构建时 CI 会克隆上游 Zed 源码，再应用 `patches/` 下的补丁文件，
把 ZedG 特有的功能与品牌改动一起打进最终产物。

补丁在 `prepare` job 中应用一次，产物以 `l10n.patch` 的形式分发给各平台构建 job。

## 现有补丁

| 补丁 | 基线版本 | 功能 |
|---|---|---|
| `zedg-recent-projects-sort.patch` | 上游 `v1.28.0-pre`（兼容 `v1.27.0`） | 侧边栏「最近项目」弹窗的排序（自动排序 + 右键手动排序）；标题栏项目弹窗「This Window」项目悬停时的置顶/上移/下移按钮 |

该补丁触及 5 个文件（可用下面的命令核对）：

```bash
grep '^diff --git' patches/*.patch
# 应列出：
# a/Cargo.lock
# a/crates/recent_projects/Cargo.toml
# a/crates/recent_projects/src/recent_projects.rs
# a/crates/recent_projects/src/sidebar_recent_projects.rs
# a/crates/recent_projects/src/zedg_recent_sort.rs（新增文件）
```

## 应用位置

- `.github/workflows/02-build.yml`：`prepare` job 克隆上游后执行 `git -C zed apply "$GITHUB_WORKSPACE/patches/<name>.patch"`，
  改动随后随 `l10n.patch` 分发到各平台构建 job。
- `.github/workflows/01-translate.yml`：「克隆 Zed 源码」步骤中在提取界面文案前同样打补丁，
  保证补丁新增的界面文案能被提取并翻译。
  另外，增量提取模式下上游的 git diff 不会包含补丁触及的文件（基线版本里没有这些改动），
  所以提取步骤会把补丁触及的 `.rs` 文件合并进待提取列表（见 workflow 中「合并 ZedG 功能补丁触及的文件」部分）。

## `Cargo.lock` 与 `--locked` 的约束（重要）

CI 的多个平台构建 job 使用 `cargo build --locked`，即 Cargo.lock 必须与 `Cargo.toml` 完全一致。
本补丁为 `recent_projects` crate 新增了 `serde` 依赖，因此补丁**必须同时包含 `Cargo.lock` 的变更**。

重新生成补丁时若漏掉 `Cargo.lock`，构建会在依赖解析阶段直接失败。生成后务必用上面的
`grep '^diff --git'` 命令核对，确认列表里有 `a/Cargo.lock`。

补丁里的 `Cargo.lock` 变更应当只有一处：`recent_projects` 的依赖列表新增 `"serde"`。
若沙盒为离线编译在根 `Cargo.toml` 末尾加过 `[patch."https://github.com/..."]`（把 git 依赖指向
本地路径），`Cargo.lock` 会随之删除这些依赖的 `source` 行。这类本地构建改动**不能进入补丁**，
否则 CI 会去找不存在的本地路径。生成补丁前先撤掉它们（`git checkout -- Cargo.toml` 后重新
`cargo check` 刷新 `Cargo.lock`），或生成后手工删掉对应 hunk，并检查根 `Cargo.toml` 不在补丁里。

## 上游升级后如何重新生成补丁

1. 在沙盒里检出新的上游 tag，并记录基线 commit（后续生成 diff 要用）：

   ```bash
   rm -rf /tmp/zed-upstream
   git clone --depth 1 --branch v1.28.0 https://github.com/zed-industries/zed.git /tmp/zed-upstream
   cd /tmp/zed-upstream && git checkout -b feature/recent-projects-sort
   BASE=$(git rev-parse HEAD)   # 深度克隆下没有 v1.28.0 这个 ref，必须先记下 commit
   ```

2. 把旧补丁应用上去，冲突处按上游新代码手工调整：

   ```bash
   git apply /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   ```

3. 重新编译并测试：

   ```bash
   cargo test -p recent_projects --lib zedg_recent_sort
   cargo check -p recent_projects
   cargo check -p recent_projects --locked   # 确认 Cargo.lock 仍与 Cargo.toml 一致
   ```

   注意：手工调整若新增/删除了依赖，需运行一次 `cargo check`（不带 `--locked`）让 Cargo.lock
   更新到位，再进入下一步。

4. 重新生成补丁并替换仓库里的文件：

   ```bash
   git diff "$BASE" > /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   ```

5. 在干净 clone 上验证补丁能干净应用、且包含全部 5 个文件：

   ```bash
   rm -rf /tmp/zedg-patch-check
   git clone --depth 1 --branch v1.28.0 https://github.com/zed-industries/zed.git /tmp/zedg-patch-check
   git -C /tmp/zedg-patch-check apply --check /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch
   grep '^diff --git' /path/to/zed-globalization/patches/zedg-recent-projects-sort.patch   # 应为 5 个文件，含 Cargo.lock
   ```

补丁失效时 CI 会在 `git apply` 步骤直接失败，不会静默产出缺少功能的安装包。
