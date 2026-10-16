"""扫描翻译结果中的空值，判定哪些「确定无需翻译」并写入禁翻清单。

背景：翻译结果里空字符串承载了两种互斥语义——「AI 调用失败，该重试」
和「这就是个代码标识符，永远不必翻译」。两者分不开，所以增量模式只能
把全部空值重翻一遍（translate.py 的补翻空值分支）。实测 v1.20.1-pre
的 zh-CN.json 里 93993 条中有 62394 条是空值，每轮增量都在重翻它们。

本模块把第二种语义固化到 config/do_not_translate.json：先用保守规则
判定标识符/路径/URI 等确定无疑的，剩余语义模糊的交给 AI。清单一旦写
入，translate.py 会在增量判断时直接跳过，不再重翻。

判定务必保守：漏判只是少省一点 token，误判会让真正的界面文案永久得
不到翻译。因此规则只覆盖「绝无可能是用户可见文案」的形态。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import signal
import sys
from pathlib import Path

from .utils import (
    AIConfig, ProgressBar, TranslationDict, load_json, save_json,
)

log = logging.getLogger(__name__)

# 全局状态：用于信号处理
_shutdown_requested = False
_current_data = None
_current_dnt_path = None


def _signal_handler(signum, frame):
    """处理 SIGTERM/SIGINT 信号，保存当前进度后退出。"""
    global _shutdown_requested, _current_data, _current_dnt_path
    log.warning("收到信号 %d，准备优雅退出", signum)
    _shutdown_requested = True

    # 立即保存当前进度
    if _current_data is not None and _current_dnt_path is not None:
        try:
            save_json(_current_data, _current_dnt_path)
            log.info("已保存当前进度到 %s", _current_dnt_path)
        except Exception as e:
            log.error("保存进度失败: %s", e)

    sys.exit(0)

# ---------------------------------------------------------------------------
# 保守规则：命中即判定为无需翻译，reason 统一加 rule: 前缀以便追溯来源
# ---------------------------------------------------------------------------

# 纯标点/空白（含常见全角标点），翻译它们会破坏语法
_PUNCT = re.compile(r'^[\s\x20-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e…·—–«»“”‘’]+$')

# 只由转义序列组成：\n、\\n、\r\n 等
_ESCAPE_ONLY = re.compile(r'^(?:\\+[nrt0]|\s)+$')

# 只由格式占位符和分隔符组成：{}、{:?}、%s、", " 等
_FMT_ONLY = re.compile(r'^(?:\{[^{}]*\}|%\w|\s|[.,:;/-])+$')

# 数字、版本号、带单位的量：42、0.8、16px、100%
_NUMERIC = re.compile(r'^[+-]?\d+(?:\.\d+)?(?:[a-zA-Z%]{0,3})$')

# base64 / 十六进制长串：图片数据、哈希
_B64HEX = re.compile(r'^[A-Za-z0-9+/=]{40,}$|^[0-9a-fA-F]{16,}$')

# 绝对路径与家目录路径：/tmp/foo、~/.config/zed
_ABS_PATH = re.compile(r'^~?/[A-Za-z0-9_./-]*$')

# 裸文件名：foo.rs、settings.json
_FILE_NAME = re.compile(r'^[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,5}$')

# URI：只认已知 scheme。若放宽成「任意字母串 + 冒号」，界面上的
# "New:"、"Debugger:"、"Input:" 会被整片误判。
_URI = re.compile(
    r'^(?:https?|ftp|file|mailto|data|ssh|git|ws|wss|socks5h?|chrome|zed)'
    r':(?://)?[^\s{}]{2,}$',
    re.I,
)

# 转义换行分隔的小写单词，典型的测试夹具：one\ntwo\nthree\n
_WORDS_NL = re.compile(r'^(?:[a-z0-9_-]+(?:\\+n|\n))+[a-z0-9_-]*$')

# 单个标识符：snake_case、camelCase、CONST_NAME
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# 标识符必须带下划线或数字才由规则拍板。纯字母的词（AI、OK、Save、
# EchoEcho）既可能是类型名也可能是按钮文案，规则无从分辨，交给 AI。
# 这一条把规则覆盖率从 59% 压到 37%，但换来的是误判基本归零——漏判
# 只是少省点 token，误判会让界面文案永久得不到翻译。
_IDENT_STRONG = re.compile(r'[_0-9]')

# 带分隔符的标识符/模块路径：foo.bar、a::b、lsp/rust-analyzer。
# 首段要求全小写，否则 "Follow-up"、"Read/Write" 这类复合词会被误判。
_DOTTED = re.compile(
    r'^[a-z_][a-z0-9_]*(?:[./:-][A-Za-z0-9_]+)+$'
    r'|^[a-z0-9_-]+(?:/[a-z0-9_.-]+)+/?$'
)


def rule_verdict(s: str) -> str | None:
    """用保守规则判定，返回 reason；无法确定时返回 None 交给 AI。"""
    if s == "":
        return "rule:empty_string"
    if _PUNCT.match(s):
        return "rule:punctuation"
    if _ESCAPE_ONLY.match(s):
        return "rule:escape_sequence"
    if _FMT_ONLY.match(s):
        return "rule:format_only"
    if _NUMERIC.match(s):
        return "rule:numeric"
    if _B64HEX.match(s):
        return "rule:binary_data"
    if _ABS_PATH.match(s) or _FILE_NAME.match(s):
        return "rule:path"
    if _URI.match(s) and " " not in s:
        return "rule:uri"
    if _WORDS_NL.match(s):
        return "rule:test_fixture"
    if _IDENT.match(s) and _IDENT_STRONG.search(s):
        return "rule:identifier"
    if _DOTTED.match(s):
        return "rule:identifier"
    return None


# ---------------------------------------------------------------------------
# AI 判定：规则拿不准的交给模型，reason 沿用清单里既有的分类词汇
# ---------------------------------------------------------------------------

_AI_SYSTEM_PROMPT = """你在审查 Zed 代码编辑器源码中提取出的字符串字面量，\
判断每条是否需要翻译成中文。

判定为「无需翻译」的，返回一个分类标识；需要翻译的，返回空字符串。

可用分类：
- api_identifier: 协议/API 字段名、命令名、语言与模型标识符
- serialization: 序列化标签、持久化类型名、枚举判别值
- match_arm: 用于 match/switch 分支比较的字面量
- string_comparison: 参与 == 或 contains 比较的字面量
- test_assertion: 单元测试里的断言消息与测试夹具数据
- code_snippet: 代码片段、SQL、shell 命令、正则、配置模板
- format_spec: 编译器/链接器参数、格式说明符、颜色码
- telemetry: 埋点事件名与属性名

判定原则（重要）：
1. 只有确信该字符串永远不会出现在用户界面上时，才给出分类。
2. 拿不准就返回空字符串——漏判只是少省一点开销，误判会让真正的界面\
文案永远得不到翻译。
3. 错误提示、按钮文案、菜单项、日志中面向用户的说明，一律返回空字符串。

只输出 JSON 对象，key 为原文，value 为分类标识或空字符串，不要任何解释。"""

# 单批送审的字符预算，判定任务比翻译轻，可以放得比翻译批次大
_BATCH_CHARS = 100000


def _build_batches(
    items: list[tuple[str, str]],
) -> list[tuple[str, list[str]]]:
    """按文件分组并切分批次，返回 [(file_path, [原文...]), ...]。

    带上文件路径是因为同一个字符串在测试文件和界面文件里的性质完全不同。
    """
    by_file: dict[str, list[str]] = {}
    for file_path, s in items:
        by_file.setdefault(file_path, []).append(s)

    batches: list[tuple[str, list[str]]] = []
    for file_path, strings in by_file.items():
        cur: list[str] = []
        size = 0
        for s in strings:
            if cur and size + len(s) > _BATCH_CHARS:
                batches.append((file_path, cur))
                cur, size = [], 0
            cur.append(s)
            size += len(s)
        if cur:
            batches.append((file_path, cur))
    return batches


async def _judge_batch(
    client: object, model: str, file_path: str, strings: list[str],
) -> dict[str, str]:
    """判定一批字符串，返回 {原文: 分类}，只含判定为无需翻译的条目。"""
    from .translate import _call_ai
    from .utils import parse_json_response

    payload = json.dumps({s: "" for s in strings}, ensure_ascii=False)
    user_prompt = f"文件: {file_path}\n\n待判定:\n```json\n{payload}\n```"
    try:
        raw = await _call_ai(client, model, _AI_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        from .error_codes import report as report_error
        report_error(log, e, f"判定请求失败 {file_path}")
        return {}

    result = parse_json_response(raw)
    known = set(strings)
    return {
        s: reason for s, reason in result.items()
        if s in known and reason.strip()
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _load_registry(dnt_path: str) -> tuple[dict, set[tuple[str, str]], set[str]]:
    """加载现有禁翻清单，返回 (原始数据, 文件级已覆盖, 全局已覆盖)。"""
    p = Path(dnt_path)
    if not p.exists():
        log.info("禁翻清单不存在，将新建: %s", dnt_path)
        data = {
            "description": "Strings that must never be translated - they are"
                           " used as program identifiers, not user-facing text",
            "global_entries": [],
            "entries": [],
        }
    else:
        data = load_json(dnt_path)
    data.setdefault("entries", [])
    data.setdefault("global_entries", [])
    covered = {(e["file"], e["original"]) for e in data["entries"]}
    covered_global = {e["original"] for e in data["global_entries"]}
    log.info(
        "现有清单: %d 条文件级 + %d 条全局",
        len(covered), len(covered_global),
    )
    return data, covered, covered_global


def load_skip_sets(path: str) -> tuple[set[tuple[str, str]], set[str]]:
    """加载禁翻清单供翻译阶段跳过用，返回 (文件级集合, 全局集合)。

    清单缺失不是错误——首次运行或未接入该流程时返回空集，照常全量翻译。
    """
    if not path or not Path(path).exists():
        return set(), set()
    try:
        data = load_json(path)
    except Exception as e:
        log.warning("禁翻清单读取失败，忽略: %s (%s)", path, e)
        return set(), set()
    covered = {
        (e["file"], e["original"])
        for e in data.get("entries", [])
        if "file" in e and "original" in e
    }
    covered_global = {
        e["original"] for e in data.get("global_entries", []) if "original" in e
    }
    return covered, covered_global


def record_translate_empties(
    dnt_path: str, items: list[tuple[str, str]],
) -> int:
    """把翻译阶段 AI 明确返回空串的条目写入清单，返回新增条数。

    翻译请求已附带源码上下文，空串本身就是一次判定；不沉淀下来的话，
    这些条目要等每周的 05 流程才进清单，期间每轮增量都会重问一遍
    （实测某轮 3016 条待翻里 3005 条属于这种情况）。
    """
    if not dnt_path or not items:
        return 0
    data, covered, covered_global = _load_registry(dnt_path)
    added = 0
    for file_path, original in items:
        if original in covered_global or (file_path, original) in covered:
            continue
        covered.add((file_path, original))
        data["entries"].append(
            {"file": file_path, "original": original, "reason": "ai:translate-empty"},
        )
        added += 1
    if added:
        data["entries"].sort(key=lambda e: (e["file"], e["original"]))
        save_json(data, dnt_path)
    return added


def collect_candidates(
    translations: TranslationDict,
    covered: set[tuple[str, str]],
    covered_global: set[str],
) -> list[tuple[str, str]]:
    """收集译文为空且尚未进入清单的条目。"""
    out: list[tuple[str, str]] = []
    for file_path, pairs in translations.items():
        for original, translated in pairs.items():
            if str(translated).strip():
                continue  # 已有译文，与本模块无关
            if original in covered_global or (file_path, original) in covered:
                continue  # 已在清单里
            out.append((file_path, original))
    return out


async def _judge_all(
    items: list[tuple[str, str]],
    ai_cfg: AIConfig,
    data: dict,
    dnt_path: str,
) -> int:
    """并发调用 AI 判定，每完成一批立即追加到 data["entries"] 并保存进度，返回判定总数。"""
    from openai import AsyncOpenAI

    from .translate import set_rate_limit

    client = AsyncOpenAI(base_url=ai_cfg.base_url, api_key=ai_cfg.api_key)
    semaphore = asyncio.Semaphore(ai_cfg.concurrency)
    set_rate_limit(ai_cfg.rpm)
    batches = _build_batches(items)
    log.info("AI 判定: %d 条，拆分为 %d 批", len(items), len(batches))

    async def one(fp: str, strings: list[str]) -> tuple[str, dict[str, str]]:
        # 检查是否收到关闭信号
        if _shutdown_requested:
            log.info("收到关闭信号，停止处理新批次")
            return fp, {}
        async with semaphore:
            return fp, await _judge_batch(client, ai_cfg.model, fp, strings)

    tasks = [asyncio.create_task(one(fp, ss)) for fp, ss in batches]
    judged_count = 0
    pbar = ProgressBar(len(tasks), desc="判定")
    for coro in asyncio.as_completed(tasks):
        if _shutdown_requested:
            log.info("收到关闭信号，取消剩余任务")
            for task in tasks:
                if not task.done():
                    task.cancel()
            break
        fp, judged = await coro
        # 立即追加到 data 并保存，确保每批完成后进度都被持久化
        for s, reason in judged.items():
            entry = {"file": fp, "original": s, "reason": f"ai:{reason}"}
            data["entries"].append(entry)
            judged_count += 1
        if judged:
            save_json(data, dnt_path)
        pbar.update(extra=f"已判定 {judged_count}")
    pbar.finish()
    return judged_count


def mark_untranslatable(
    trans_path: str,
    dnt_path: str,
    ai_cfg: AIConfig | None = None,
    use_ai: bool = True,
    limit: int = 0,
) -> dict[str, int]:
    """扫描翻译文件中的空值并扩充禁翻清单，返回统计。"""
    global _current_data, _current_dnt_path

    # 注册信号处理器
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    translations: TranslationDict = load_json(trans_path)
    data, covered, covered_global = _load_registry(dnt_path)

    # 设置全局变量，以便信号处理器可以保存进度
    _current_data = data
    _current_dnt_path = dnt_path

    candidates = collect_candidates(translations, covered, covered_global)
    log.info("待判定空值条目: %d", len(candidates))
    if not candidates:
        return {"candidates": 0, "by_rule": 0, "by_ai": 0, "added": 0}

    # 实时追加到 data["entries"]，确保信号处理器能保存增量进度
    added_count = 0
    undecided: list[tuple[str, str]] = []
    for file_path, original in candidates:
        if _shutdown_requested:
            log.warning("收到关闭信号，停止规则判定")
            break
        reason = rule_verdict(original)
        if reason:
            entry = {"file": file_path, "original": original, "reason": reason}
            data["entries"].append(entry)
            added_count += 1
        else:
            undecided.append((file_path, original))
    by_rule = added_count
    log.info("规则判定 %d 条，剩余 %d 条待 AI 判定", by_rule, len(undecided))

    by_ai = 0
    if use_ai and undecided and not _shutdown_requested:
        if limit > 0 and len(undecided) > limit:
            log.info("本次只处理前 %d 条（--limit）", limit)
            undecided = undecided[:limit]
        if ai_cfg is None:
            ai_cfg = AIConfig()
        # _judge_all 现在直接追加到 data 并返回判定总数
        by_ai = asyncio.run(_judge_all(undecided, ai_cfg, data, dnt_path))
        added_count += by_ai

    # 最终排序并保存
    data["entries"].sort(key=lambda e: (e["file"], e["original"]))
    save_json(data, dnt_path)

    # 清除全局变量
    _current_data = None
    _current_dnt_path = None

    if _shutdown_requested:
        log.warning("由于收到关闭信号，提前保存了 %d 条新增条目", added_count)
    else:
        log.info(
            "清单已更新: 新增 %d 条（规则 %d + AI %d），总计 %d 条",
            added_count, by_rule, by_ai, len(data["entries"]),
        )
    return {
        "candidates": len(candidates),
        "by_rule": by_rule,
        "by_ai": by_ai,
        "added": added_count,
    }


def run(args: argparse.Namespace) -> None:
    """CLI 入口"""
    ai_cfg = AIConfig(
        base_url=getattr(args, "base_url", ""),
        api_key=getattr(args, "api_key", ""),
        model=getattr(args, "model", ""),
        concurrency=getattr(args, "concurrency", 10),
    )
    stats = mark_untranslatable(
        args.input,
        args.do_not_translate,
        ai_cfg=ai_cfg,
        use_ai=not args.rules_only,
        limit=args.limit,
    )
    log.info(
        "完成: 候选 %d, 规则 %d, AI %d, 新增 %d",
        stats["candidates"], stats["by_rule"], stats["by_ai"], stats["added"],
    )
