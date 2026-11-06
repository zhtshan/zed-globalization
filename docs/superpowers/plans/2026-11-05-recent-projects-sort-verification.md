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

未验证：干净环境（联网）下的 `cargo check --locked`，要等 CI 首次构建确认。

## 3. 人工 UI 验证 —— 待人工验证

本次没有可运行的 ZedG 构建产物，以下各项都还没实测：

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

自动化部分（单元测试、补丁应用）通过；UI 行为要等 CI 产出安装包后按第 3 节逐项人工验证。
