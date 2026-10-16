"""AI 翻译后修复占位符不匹配的译文"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .replace import _check_placeholders, _is_positional  # noqa: F401
from .utils import (
    AIConfig, RateLimiter, TranslationDict, extract_placeholders,
    load_json, save_json,
)

log = logging.getLogger(__name__)

_MAX_RETRIES = 3

_FIX_PROMPT = """\
你是 Rust 代码翻译修复工具。以下译文的格式占位符与原文不匹配，请修正译文使其占位符与原文完全一致。

规则:
1. 占位符（如 {{name}}, {{}}, {{:?}}, {{0}} 等）必须与原文完全相同，不可翻译
2. 只修正占位符，尽量保留原有翻译内容
3. 直接返回修正后的译文，不要添加任何说明

原文: {original}
原文占位符: {src_ph}
错误译文: {bad_value}
译文占位符: {dst_ph}

请返回修正后的译文:"""


def _ai_fix_one(
    client: object, model: str, limiter: RateLimiter,
    original: str, bad_value: str,
    src_ph: list[str], dst_ph: list[str],
) -> str | None:
    """调用 AI 修复单条译文，重试 _MAX_RETRIES 次"""
    prompt = _FIX_PROMPT.format(
        original=original, bad_value=bad_value,
        src_ph=src_ph, dst_ph=dst_ph,
    )
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            limiter.acquire_sync()
            resp = client.chat.completions.create(  # type: ignore[attr-defined]
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            fixed = (resp.choices[0].message.content or "").strip()
            if not fixed:
                continue
            fixed_ph = extract_placeholders(fixed)
            if _check_placeholders(src_ph, fixed_ph):
                log.info(
                    "修复成功 (第%d次): %r → %r", attempt, original, fixed,
                )
                return fixed
            log.debug(
                "修复后仍不匹配 (第%d次): %s → %s",
                attempt, dst_ph, fixed_ph,
            )
        except Exception as e:
            log.warning("修复请求失败 (第%d次): %s", attempt, e)
    return None


def fix_translation_json(
    trans_path: str, source_root: str, ai_cfg: AIConfig,
) -> tuple[int, int, int]:
    """扫描翻译 JSON，修复占位符不匹配条目，删除不存在的文件条目。

    Returns: (fixed_count, removed_ph_count, removed_file_count)
    """
    from openai import OpenAI

    data: TranslationDict = load_json(trans_path)
    root = Path(source_root)
    client = OpenAI(base_url=ai_cfg.base_url, api_key=ai_cfg.api_key)
    limiter = RateLimiter(ai_cfg.rpm)

    fixed_count = 0
    removed_ph_count = 0
    removed_files: list[str] = []

    for file_path in list(data.keys()):
        # 检查源文件是否存在
        rel = file_path[4:] if file_path.startswith("zed/") else file_path
        if not (root / rel).exists():
            removed_files.append(file_path)
            continue

        entries = data[file_path]
        for original, translated in list(entries.items()):
            if not translated:
                continue
            src_ph = extract_placeholders(original)
            dst_ph = extract_placeholders(translated)
            if _check_placeholders(src_ph, dst_ph):
                continue

            log.info(
                "占位符不匹配: %r → %r (原文=%s, 译文=%s) [%s]",
                original, translated, src_ph, dst_ph, file_path,
            )
            fixed = _ai_fix_one(
                client, ai_cfg.model, limiter,
                original, translated, src_ph, dst_ph,
            )
            if fixed:
                entries[original] = fixed
                fixed_count += 1
            else:
                log.warning("修复失败，删除该条目: %r [%s]", original, file_path)
                del entries[original]
                removed_ph_count += 1

    # 删除不存在文件的条目
    for fp in removed_files:
        del data[fp]

    save_json(data, trans_path)

    if removed_files:
        log.info("已清理 %d 个不存在的文件条目:", len(removed_files))
        for f in removed_files[:20]:
            log.info("  - %s", f)
    log.info(
        "修复完成: 修复 %d 条, 删除不可修复 %d 条, 清理文件 %d 个",
        fixed_count, removed_ph_count, len(removed_files),
    )
    return fixed_count, removed_ph_count, len(removed_files)


def run(args: argparse.Namespace) -> None:
    """CLI 入口"""
    ai_cfg = AIConfig(
        base_url=args.base_url, api_key=args.api_key,
        model=args.model, concurrency=args.concurrency,
    )
    ai_cfg.validate()
    fix_translation_json(args.input, args.source_root, ai_cfg)
