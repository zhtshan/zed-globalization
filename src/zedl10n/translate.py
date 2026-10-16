"""AI 并发翻译，内置智能过滤"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from pathlib import Path

from .batch import (
    MAX_CONTEXT_CHARS_PER_FILE,
    Unit,
    fit_context,
    pack_units,
    split_batch,
)
from .error_codes import report as report_error
from .prompts import (
    SYSTEM_PROMPT_TEMPLATE,
    XML_FALLBACK_INSTRUCTION,
    build_consistency_fix_prompt,
    build_fix_prompt,
    build_multi_file_prompt,
    build_numbered_instruction,
    validate_placeholders,
)
from .utils import (
    AIConfig,
    ProgressBar,
    RateLimiter,
    TranslationDict,
    build_glossary_section,
    load_json,
    normalize_fullwidth,
    parse_json_response,
    parse_numbered_response,
    parse_xml_response,
    save_json,
)

log = logging.getLogger(__name__)


# 流式保活间隔：无新 chunk 超过此时长视为连接僵死（代理/负载均衡空闲
# 超时的典型表现——非流式大响应经常在 5-10 分钟生成期被中间层掐断），
# 主动放弃并重试，而不是干等到客户端自身超时。
STREAM_IDLE_TIMEOUT = 120.0

# 进程内所有 _call_ai 共用的限速器，由入口按 AIConfig.rpm 设置
_rate_limiter = RateLimiter(0)


def set_rate_limit(rpm: int) -> None:
    """设置全局 RPM 限速（需在事件循环内的首个请求前调用）"""
    global _rate_limiter
    _rate_limiter = RateLimiter(rpm)
    if rpm > 0:
        log.info("AI 请求限速: %d 次/分钟", rpm)


async def _call_ai(
    client: object,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用 AI API（流式），内置网络错误与流空闲超时重试。

    流式的意义不在总耗时（token 量相同生成时间相同），而在连接活性：
    chunk 持续到达证明链路存活，代理不会因"空闲超时"掐断长请求。
    对 max_tokens=65535 的批量翻译批次，这是非流式经常 5-10 分钟断连
    的主要解药。收到 [DONE] 后拼接完整内容，对调用方透明。
    """
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            await _rate_limiter.acquire()
            stream = await client.chat.completions.create(  # type: ignore[attr-defined]
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=65535,
                stream=True,
                extra_body={"thinking": {"type": "disabled"}},
            )
            chunks: list[str] = []
            while True:
                try:
                    # chunk 间隔超过 STREAM_IDLE_TIMEOUT 视为链路僵死
                    raw = await asyncio.wait_for(
                        stream.__anext__(), timeout=STREAM_IDLE_TIMEOUT
                    )
                except StopAsyncIteration:
                    break  # 正常结束
                if getattr(raw, "choices", None):
                    delta = raw.choices[0].delta.content
                    if delta:
                        chunks.append(delta)
            return "".join(chunks).strip()
        except Exception as e:
            last_err = e
            if attempt < 4:
                delay = (2**attempt) * 3 + random.uniform(0, 2)
                report_error(log, e, f"重试 {attempt + 1}/5，等待 {delay:.1f}s")
                await asyncio.sleep(delay)
                continue
            raise
    raise last_err if last_err else RuntimeError("AI 调用失败：未知原因")


async def _fetch_translation(
    client: object,
    model: str,
    file_path: str,
    strings: dict[str, str],
    user_prompt: str,
    system_prompt: str,
) -> dict[str, str]:
    """通过 JSON → XML(CDATA) → 编号格式 三级降级获取翻译结果"""
    # 第一级: JSON 格式重试 3 次
    for attempt in range(3):
        try:
            raw = await _call_ai(client, model, system_prompt, user_prompt)
            result = parse_json_response(raw)
            if result:
                return result
        except Exception as e:
            report_error(log, e, f"翻译失败 {file_path}（JSON 级）")
            return {}

    # 第二级: XML(CDATA) 格式重试 3 次
    xml_prompt = user_prompt + XML_FALLBACK_INSTRUCTION
    for attempt in range(3):
        try:
            raw = await _call_ai(client, model, system_prompt, xml_prompt)
            result = parse_xml_response(raw)
            if result:
                return result
            log.debug("XML 解析重试 (%d/3): %s", attempt + 1, file_path)
        except Exception as e:
            report_error(log, e, f"翻译失败 {file_path}（XML 级）")
            return {}

    # 第三级: 编号格式重试 3 次
    keys = list(strings.keys())
    numbered_prompt = user_prompt + build_numbered_instruction(len(keys))
    for attempt in range(3):
        try:
            raw = await _call_ai(client, model, system_prompt, numbered_prompt)
            result = parse_numbered_response(raw, keys)
            if result:
                return result
            log.debug("编号格式解析重试 (%d/3): %s", attempt + 1, file_path)
        except Exception as e:
            report_error(log, e, f"翻译失败 {file_path}（编号级）")
            return {}

    log.warning(
        "[FAILED] JSON+XML+编号 均失败: %s (%d 条)",
        file_path,
        len(strings),
    )
    return {}


def _pack_label(units: list[Unit]) -> str:
    """日志里标识一个请求包"""
    if len(units) == 1:
        return units[0][0]
    return f"{units[0][0]} 等 {len(units)} 个文件"


async def _translate_batch(
    client: object,
    model: str,
    units: list[Unit],
    system_prompt: str,
) -> tuple[dict[str, str], set[str]]:
    """翻译一个请求包（一或多个文件的字符串），含占位符校验和自动重试。

    返回 (译文, AI 明确返回空串的原文集合)。后者要在占位符校验前取，
    校验失败被丢弃成空串的条目不算「无需翻译」。
    """
    file_path = _pack_label(units)
    strings: dict[str, str] = {}
    for _, unit_strings, _ in units:
        strings.update(unit_strings)
    result = await _fetch_translation(
        client, model, file_path, strings,
        build_multi_file_prompt(units), system_prompt,
    )
    if not result:
        return result, set()
    ai_empty = {s for s in strings if s in result and not str(result[s]).strip()}

    # 占位符校验 + 重试（最多 2 次）
    for retry in range(2):
        errors = validate_placeholders(result)
        if not errors:
            return result, ai_empty
        log.debug(
            "占位符不匹配 %d 条，重试修正 (%d/2): %s",
            len(errors), retry + 1, file_path,
        )
        fix_prompt = build_fix_prompt(errors, result)
        try:
            raw = await _call_ai(client, model, system_prompt, fix_prompt)
            fixed = parse_json_response(raw)
        except Exception as e:
            report_error(log, e, f"占位符修正请求失败 {file_path} [ZL1005]")
            break
        if fixed:
            for key, val in fixed.items():
                if key in result:
                    result[key] = val

    # 最终校验：仍有问题的条目丢弃为空字符串
    final_errors = validate_placeholders(result)
    for original, (src_ph, dst_ph) in final_errors.items():
        log.warning(
            "占位符校验失败，丢弃译文: %r (原文占位符=%s, 译文占位符=%s) [%s]",
            original, src_ph, dst_ph, file_path,
        )
        result[original] = ""

    return result, ai_empty


def _read_source_file(file_path: str, source_root: str) -> str:
    """读取源文件内容，找不到则返回空字符串"""
    if not source_root:
        return ""
    root = Path(source_root)
    candidates = [root / file_path]
    if file_path.startswith("zed/"):
        candidates.append(root / file_path[4:])
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                return ""
    return ""


async def _ai_fix_consistency(
    client: object,
    model: str,
    system_prompt: str,
    result: TranslationDict,
    glossary_path: str,
) -> tuple[TranslationDict, list[str]]:
    """用 AI 修复一致性问题，返回 (修复后结果, 修复日志)"""
    from .consistency import build_issues_for_ai, check_consistency

    issues = check_consistency(result, glossary_path)
    if not issues:
        return result, []

    log.info("一致性检查发现 %d 个问题，调用 AI 修复", len(issues))
    incon, glossary_v, keep_v = build_issues_for_ai(issues, result)
    if not incon and not glossary_v and not keep_v:
        return result, []

    user_prompt = build_consistency_fix_prompt(incon, glossary_v, keep_v)

    fix_log: list[str] = []
    try:
        raw = await _call_ai(client, model, system_prompt, user_prompt)
        fixed = parse_json_response(raw)
    except Exception as e:
        report_error(log, e, "AI 一致性修复请求失败")
        return result, fix_log

    if not fixed:
        log.warning("AI 一致性修复返回为空")
        return result, fix_log

    # 将 AI 修正结果应用到所有文件
    for original, new_translation in fixed.items():
        if not new_translation:
            continue
        applied = 0
        for pairs in result.values():
            if original in pairs and pairs[original]:
                if pairs[original] != new_translation:
                    pairs[original] = new_translation
                    applied += 1
        if applied:
            fix_log.append(
                f'AI 修复: "{original}" → "{new_translation}" '
                f"(更新 {applied} 处)",
            )

    return result, fix_log


async def _translate_async(
    all_strings: TranslationDict,
    existing: TranslationDict,
    mode: str,
    lang: str,
    glossary_path: str,
    ai_cfg: AIConfig,
    source_root: str = "",
    skip_sets: tuple[set[tuple[str, str]], set[str]] | None = None,
) -> tuple[TranslationDict, list[tuple[str, str]]]:
    """异步并发翻译，返回 (译文, AI 判定无需翻译的 (文件, 原文) 列表)"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=ai_cfg.base_url, api_key=ai_cfg.api_key)
    semaphore = asyncio.Semaphore(ai_cfg.concurrency)
    set_rate_limit(ai_cfg.rpm)

    glossary_section = build_glossary_section(glossary_path)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        lang=lang,
        glossary_section=glossary_section,
    )

    result: TranslationDict = {fp: dict(v) for fp, v in existing.items()}
    units: list[Unit] = []
    skip_covered, skip_global = skip_sets or (set(), set())
    skipped = 0

    for file_path, strings in all_strings.items():
        to_translate: dict[str, str] = {}
        if file_path not in result:
            result[file_path] = {}
        for s in strings:
            # 已判定「确定无需翻译」的条目：空值不再被当成待补翻的缺口，
            # 否则每轮增量都要把几万条代码标识符重新问一遍 AI。
            if s in skip_global or (file_path, s) in skip_covered:
                skipped += 1
                continue
            if mode == "full" or s not in result.get(file_path, {}):
                to_translate[s] = ""
            elif mode == "incremental" and not str(result[file_path].get(s, "")).strip():
                # 增量模式也要补翻空值：AI 对颜色码、标识符风格的字符串偶尔
                # 返回空串，而 "s in result" 让空值永远跳过重翻，缺口只会
                # 越积越多（i18n/zh-CN.json 累积了 4 万+ 空值）。
                to_translate[s] = ""
        if not to_translate:
            continue
        snippet = fit_context(
            _read_source_file(file_path, source_root),
            to_translate, MAX_CONTEXT_CHARS_PER_FILE,
        )
        # 字符串本身就超预算的大文件先按文件拆分，再参与合包
        batches, snippet = split_batch(
            to_translate, system_prompt, file_path, snippet,
        )
        units.extend((file_path, batch, snippet) for batch in batches)

    packs = pack_units(units, system_prompt)

    async def do_pack(
        pack: list[Unit],
    ) -> tuple[list[Unit], tuple[dict[str, str], set[str]]]:
        async with semaphore:
            return pack, await _translate_batch(
                client, ai_cfg.model, pack, system_prompt,
            )

    tasks = [asyncio.create_task(do_pack(p)) for p in packs]
    total = len(tasks)
    if skipped:
        log.info("按禁翻清单跳过 %d 条", skipped)
    log.info(
        "共 %d 个文件片段，合并为 %d 个请求，并发数 %d",
        len(units), total, ai_cfg.concurrency,
    )
    pbar = ProgressBar(total, desc="翻译")

    fail_count = 0
    empties: list[tuple[str, str]] = []
    for coro in asyncio.as_completed(tasks):
        pack, (translations, ai_empty) = await coro
        if not translations:
            fail_count += 1
        # 按文件回填：包内同一原文只属于一个文件（pack_units 保证）
        for fp, unit_strings, _ in pack:
            per_file = {
                s: translations[s] for s in unit_strings if s in translations
            }
            if per_file:
                result.setdefault(fp, {}).update(per_file)
            empties.extend((fp, s) for s in unit_strings if s in ai_empty)
        pbar.update(extra=f"失败 {fail_count}")
    pbar.finish()

    # AI 一致性修复（最多 2 轮）
    for fix_round in range(2):
        result, ai_fix_log = await _ai_fix_consistency(
            client, ai_cfg.model, system_prompt, result, glossary_path,
        )
        for msg in ai_fix_log:
            log.info("一致性修复 (第 %d 轮): %s", fix_round + 1, msg)
        if not ai_fix_log:
            break

    return result, empties


def translate_all(
    strings_path: str,
    output_path: str,
    context_path: str = "",
    glossary_path: str = "config/glossary.yaml",
    mode: str = "incremental",
    lang: str = "zh-CN",
    ai_cfg: AIConfig | None = None,
    source_root: str = "",
    do_not_translate: str = "",
) -> None:
    """同步入口"""
    if ai_cfg is None:
        ai_cfg = AIConfig()
    ai_cfg.validate()

    all_strings: TranslationDict = load_json(strings_path)
    existing = load_json(output_path) if Path(output_path).exists() else {}

    from .untranslatable import load_skip_sets

    skip_sets = load_skip_sets(do_not_translate)
    if skip_sets[0] or skip_sets[1]:
        log.info(
            "已加载禁翻清单: %d 条文件级 + %d 条全局",
            len(skip_sets[0]), len(skip_sets[1]),
        )

    result, empties = asyncio.run(
        _translate_async(
            all_strings, existing, mode, lang,
            glossary_path, ai_cfg, source_root, skip_sets,
        )
    )
    # 全角 ASCII 符号统一转半角，避免破坏 Rust 源码语法
    for fp in result:
        for s, t in result[fp].items():
            if t:
                result[fp][s] = normalize_fullwidth(t)

    # 规则兜底：AI 修复后仍有问题的，用规则强制统一
    from .consistency import fix_consistency

    result, fix_log = fix_consistency(result, glossary_path)
    for msg in fix_log:
        log.info("规则兜底修复: %s", msg)

    save_json(result, output_path)
    log.info("翻译结果已保存: %s", output_path)

    # 最终仍为空的才记入禁翻清单（一致性修复可能补上了译文）
    from .untranslatable import record_translate_empties

    still_empty = [
        (fp, s) for fp, s in empties
        if not str(result.get(fp, {}).get(s, "")).strip()
    ]
    added = record_translate_empties(do_not_translate, still_empty)
    if added:
        log.info("AI 返回空串的 %d 条已写入禁翻清单: %s", added, do_not_translate)


def run(args: argparse.Namespace) -> None:
    """CLI 入口"""
    ai_cfg = AIConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        concurrency=args.concurrency,
    )
    translate_all(
        args.input,
        args.output,
        args.context,
        args.glossary,
        args.mode,
        args.lang,
        ai_cfg,
        source_root=getattr(args, "source_root", ""),
        do_not_translate=getattr(args, "do_not_translate", ""),
    )
