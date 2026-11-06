# 最近项目排序功能 验证记录

验证日期：2026-11-06

## 验证环境

- 上游基线：zed-industries/zed `v1.27.0`（沙盒 `/Users/startiasoft/work/zed-upstream`，基线 commit `e2c760a`）
- 补丁：`patches/zedg-recent-projects-sort.patch`
- 平台：macOS（Darwin 27.0.0）

## 1. 本地全量测试 —— 通过

```bash
cd /Users/startiasoft/work/zed-upstream && cargo test -p recent_projects --lib
```

结果：`49 passed; 0 failed`，其中包括 `zedg_recent_sort` 的 12 个新测试，上游原有测试也全部通过。

## 2. 补丁干净应用 —— 通过

- 在基线 `e2c760a` 的临时 worktree 上执行 `git apply --check`，可以干净应用。
- 应用后 `Cargo.lock` 中 `recent_projects` 的依赖包含 `"serde"`，与 `crates/recent_projects/Cargo.toml` 的 `serde.workspace = true` 一致。
- 补丁共改动 5 个文件，包含 `Cargo.lock`，不含根 `Cargo.toml`（沙盒的本地 `[patch]` 改动没有混进补丁）。

## 2.1 CI 构建 —— 通过

- Run：https://github.com/zhtshan/zed-globalization/actions/runs/42153777795
  （`version=v1.27.0`、`lang=zh-CN`、`arch=macos`、`publish=actions`）
- `prepare`：`git -C zed apply` 打补丁成功，`l10n.patch` 正常生成。
- `build-macos-aarch64` / `build-macos-x86_64` / `build-macos-universal` 全部成功；构建命令是
  `cargo build --release --locked`，确认补丁里的 `Cargo.lock` 改动在干净环境下有效。
- 首次运行（run 42153539094）在 `prepare` 失败：`git apply` 没进 `zed/` 目录执行，已在 `5eb1f3e` 修复。
- Linux / Windows 这次没构建，未验证。

## 3. 人工 UI 验证 —— 待人工验证

可用产物：上面 CI run 的 `zed-macos` artifact。以下各项都还没实测：

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 打开「最近项目」弹窗，footer 出现 `Sort By` 按钮，默认显示 `Recent` | 待人工验证 |
| 2 | 点击 `Sort By`，菜单列出 `Recent` / `Name (A-Z)` / `Name (Z-A)` / `Manual` | 待人工验证 |
| 3 | 选 `Name (A-Z)` 后列表立即按名称升序排列 | 待人工验证 |
| 4 | 关闭再打开弹窗，仍是名称升序 | 待人工验证 |
| 5 | 右键任意一行，出现 `Move Up` / `Move Down` / `Pin to Top` | 待人工验证 |
| 6 | 执行 `Move Up` 后该项上移一位，模式自动切到 `Manual` | 待人工验证 |
| 7 | 关闭再打开，手动顺序保持不变 | 待人工验证 |
| 8 | 输入搜索关键字，结果按相关度排序，不受排序模式影响 | 待人工验证 |
| 9 | `~/Library/Application Support/Zed/zedg-recent-projects-sort.json` 内容为 `{"mode":"manual","order":[...]}` | 待人工验证 |

## 结论

自动化部分（单元测试、补丁应用、macOS CI 构建）都通过了；UI 行为还要用 `zed-macos` 产物按第 3 节逐项人工验证。
