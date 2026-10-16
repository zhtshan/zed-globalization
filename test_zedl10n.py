#!/usr/bin/env python3
"""替换保护规则 + AI 响应解析的回归测试

直接运行: python3 test_zedl10n.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from zedl10n.prompts import validate_placeholders  # noqa: E402
from zedl10n.replace import (  # noqa: E402
    _escape_for_rust_source, _find_protected_ranges, _replace_skip_protected,
)
from zedl10n.untranslatable import (  # noqa: E402
    collect_candidates, load_skip_sets, rule_verdict,
)
from zedl10n.utils import parse_json_response  # noqa: E402


def translate(content: str, original: str, translated: str) -> str:
    """模拟 replace_in_source 对单条翻译的处理"""
    protected = _find_protected_ranges(content)
    safe = _escape_for_rust_source(translated)
    new_content, _ = _replace_skip_protected(
        content, f'"{original}"', f'"{safe}"', protected,
    )
    return new_content


def test_raw_string_regex_untouched() -> None:
    """issue #65: 原始字符串里的正则不能被转义写回破坏

    r"fn (.+?)\\(" 按普通字符串写回会变成 r"fn (.+?)\\\\("，
    正则引擎把 \\\\ 当作转义反斜杠，后面的 ( 成为未闭合分组 → 编译 panic。
    """
    src = 'static RE: LazyLock<Regex> =\n' \
          '    LazyLock::new(|| Regex::new(r"fn (.+?)\\(").expect("失败"));'
    out = translate(src, r'fn (.+?)\(', r'fn (.+?)\(')
    assert r'r"fn (.+?)\("' in out, out
    assert r'\\(' not in out, "原始字符串里的反斜杠被加倍了"


def test_raw_hash_string_untouched() -> None:
    """r#"..."# 形式同样受保护"""
    src = 'let re = Regex::new(r#"\\d+\\s*"#).unwrap();'
    out = translate(src, r'\d+\s*', '数字')
    assert r'r#"\d+\s*"#' in out, out


def test_key_context_untouched() -> None:
    """issue #66: key context 是代码标识符，译成中文后 keymap 匹配不上"""
    src = 'dispatch_context.add("Terminal");\n' \
          'key_context.set("mode", "full");'
    out = translate(src, "Terminal", "终端")
    out = translate(out, "full", "完整")
    assert 'dispatch_context.add("Terminal")' in out, out
    assert 'key_context.set("mode", "full")' in out, out


def test_key_context_parse_untouched() -> None:
    """KeyContext::parse("editor mode=full") 同样是标识符表达式"""
    src = 'let ctx = KeyContext::parse("editor mode=full").unwrap();'
    out = translate(src, "editor mode=full", "编辑器 模式=完整")
    assert 'KeyContext::parse("editor mode=full")' in out, out


def test_serialized_item_kind_untouched() -> None:
    """issue #66: 序列化标识符被翻译会破坏工作区持久化"""
    src = 'impl SerializableItem for TerminalView {\n' \
          '    fn serialized_item_kind() -> &\'static str {\n' \
          '        "Terminal"\n' \
          '    }\n}'
    out = translate(src, "Terminal", "终端")
    assert '"Terminal"' in out, out
    assert "终端" not in out, out


def test_ui_text_still_translated() -> None:
    """防过度保护：保护区之外的同名字符串仍要翻译"""
    src = 'dispatch_context.add("Terminal");\n' \
          'let label = "Terminal";'
    out = translate(src, "Terminal", "终端")
    assert 'dispatch_context.add("Terminal")' in out, out
    assert 'let label = "终端";' in out, out


def test_existing_protections_intact() -> None:
    """原有规则不回归：字节串与属性宏仍受保护，#[error] 仍可翻译"""
    src = 'const MAGIC: &[u8] = b"Terminal";\n' \
          '#[serde(rename = "Terminal")]\n' \
          '#[error("Terminal")]'
    out = translate(src, "Terminal", "终端")
    assert 'b"Terminal"' in out, out
    assert '#[serde(rename = "Terminal")]' in out, out
    assert '#[error("终端")]' in out, "用户可见的错误消息应该被翻译"


def test_escape_still_works_for_normal_strings() -> None:
    """普通字符串的转义逻辑保持不变"""
    assert _escape_for_rust_source('行1\n行2') == '行1\\n行2'
    assert _escape_for_rust_source('说"你好"') == '说\\"你好\\"'
    assert _escape_for_rust_source(r'路径\n换行') == r'路径\n换行'


def test_nested_value_dropped() -> None:
    """模型把译文包成对象时必须视同解析失败，交给降级链重试

    放行的话下游 extract_placeholders 会对 dict 调 .replace()，
    异常在 try 之外冒泡，整个 workflow 挂掉。
    """
    raw = '{"Hello": {"translation": "你好"}}'
    assert parse_json_response(raw) == {}, "嵌套对象应被丢弃以触发降级"


def test_partial_nested_keeps_valid() -> None:
    """部分条目异常时保留合法的，异常条目视同未翻译"""
    raw = '{"Hello": "你好", "Bye": {"t": "再见"}, "N": 42, "X": null}'
    assert parse_json_response(raw) == {"Hello": "你好"}


def test_non_object_toplevel() -> None:
    """顶层不是对象时不能当成翻译结果"""
    assert parse_json_response("[1, 2, 3]") == {}
    assert parse_json_response('"just a string"') == {}


def test_normal_json_unaffected() -> None:
    """正常响应与 markdown 包裹的解析行为不变"""
    assert parse_json_response('{"Hello": "你好"}') == {"Hello": "你好"}
    fenced = '```json\n{"Hello": "你好", "Bye": ""}\n```'
    assert parse_json_response(fenced) == {"Hello": "你好", "Bye": ""}


def test_bad_response_no_longer_crashes_validation() -> None:
    """回归 CI 崩溃：解析结果必须能安全喂给占位符校验"""
    raw = '{"a {} b": {"translation": "甲 {} 乙"}, "c {}": "丙 {}"}'
    result = parse_json_response(raw)
    validate_placeholders(result)  # 修复前这里 AttributeError
    assert result == {"c {}": "丙 {}"}


def test_rules_never_claim_ui_text() -> None:
    """禁翻规则必须保守：误判会让界面文案永久得不到翻译

    这几条都曾被早期规则误判——"New:" 被当成 URI scheme，
    "Follow-up"/"Read/Write" 被当成带分隔符的标识符，"AI"/"OK"
    被当成裸标识符。
    """
    ui_text = [
        "New:", "From:", "Debugger:", "Searching:", "Input:", "WSL:",
        "Follow-up", "Read/Write", "AI", "OK", "Save", "Cancel",
        "Failed to open file", "Rename symbol",
    ]
    for s in ui_text:
        assert rule_verdict(s) is None, f"界面文案被误判: {s!r}"


def test_rules_catch_obvious_identifiers() -> None:
    """明确不该翻译的形态必须被规则拦下，否则白白送 AI"""
    expected = {
        "tool_name": "rule:identifier",
        "output_before_kill": "rule:identifier",
        "/tmp/foo": "rule:path",
        "main.rs": "rule:path",
        "theme.mode": "rule:path",
        "https://zed.dev/docs": "rule:uri",
        "mailto:user@example.com": "rule:uri",
        "  ": "rule:punctuation",
        "42": "rule:numeric",
        "100%": "rule:numeric",
        "": "rule:empty_string",
    }
    for s, want in expected.items():
        assert rule_verdict(s) == want, f"{s!r} -> {rule_verdict(s)}, 期望 {want}"


def test_skip_sets_missing_file() -> None:
    """清单缺失不是错误，返回空集让流程照常全量翻译"""
    assert load_skip_sets("") == (set(), set())
    assert load_skip_sets("/nonexistent/do_not_translate.json") == (set(), set())


def test_collect_candidates_only_empty_and_uncovered() -> None:
    """只收集译文为空、且尚未进清单的条目"""
    translations = {
        "a.rs": {"Save": "保存", "tool_name": "", "already": ""},
        "b.rs": {"globally_skipped": ""},
    }
    covered = {("a.rs", "already")}
    covered_global = {"globally_skipped"}
    got = collect_candidates(translations, covered, covered_global)
    assert got == [("a.rs", "tool_name")], got


def test_pack_units_budget_and_duplicates() -> None:
    """合包：小文件合并；同一原文不进同一包；超预算另起一包"""
    from zedl10n.batch import MAX_INPUT_CHARS, pack_units

    units = [
        ("a.rs", {"Open": "", "Close": ""}, "fn a() {}"),
        ("b.rs", {"Save": ""}, "fn b() {}"),
        ("c.rs", {"Open": ""}, "fn c() {}"),  # 与 a.rs 重复
        ("d.rs", {"Big": ""}, "x" * MAX_INPUT_CHARS),  # 单独超预算
    ]
    packs = pack_units(units, "sys")
    files = [[u[0] for u in p] for p in packs]
    assert files == [["a.rs", "b.rs"], ["c.rs"], ["d.rs"]], files


def test_multi_file_prompt_numbering() -> None:
    """多文件 prompt 编号跨文件连续，单文件退化为原 prompt"""
    from zedl10n.prompts import build_multi_file_prompt, build_user_prompt

    one = [("crates/x/src/a.rs", {"Open": ""}, "code")]
    assert build_multi_file_prompt(one) == build_user_prompt(*one[0])
    two = one + [("crates/y/src/b.rs", {"Save": "", "Quit": ""}, "")]
    prompt = build_multi_file_prompt(two)
    assert '1. "Open"' in prompt and '2. "Save"' in prompt and '3. "Quit"' in prompt
    assert '{"Open": "", "Save": "", "Quit": ""}' in prompt


def test_rate_limiter_spacing() -> None:
    """限速器按 60/rpm 秒间隔放行"""
    import asyncio
    import time

    from zedl10n.utils import RateLimiter

    async def go() -> float:
        lim = RateLimiter(600)  # 间隔 0.1s
        t0 = time.monotonic()
        await asyncio.gather(*(lim.acquire() for _ in range(4)))
        return time.monotonic() - t0

    elapsed = asyncio.run(go())
    assert 0.29 <= elapsed < 0.6, elapsed
    assert RateLimiter(0).interval == 0


def test_translate_packs_and_records_empties() -> None:
    """端到端（假 AI）：多文件合并请求、按文件回填、空串写入禁翻清单"""
    import json
    import re
    import tempfile
    import types

    import openai

    from zedl10n.translate import translate_all
    from zedl10n.utils import AIConfig

    requests: list[str] = []

    class FakeStream:
        def __init__(self, text: str) -> None:
            self.chunks = [text]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.chunks:
                raise StopAsyncIteration
            delta = types.SimpleNamespace(content=self.chunks.pop())
            return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])

    async def create(**kw):
        prompt = kw["messages"][1]["content"]
        requests.append(prompt)
        blocks = re.findall(r"```json\n(.*?)\n```", prompt, re.S)
        if not blocks:  # 占位符修正 / 一致性修复请求，原样不改
            return FakeStream("{}")
        payload = json.loads(blocks[-1])
        out = {}
        for k in payload:
            if k.startswith("id_"):
                out[k] = ""  # AI 判定无需翻译
            elif k == "Got {}":
                out[k] = "得到"  # 占位符丢失，校验后被丢弃
            else:
                out[k] = "译" + k
        return FakeStream(json.dumps(out, ensure_ascii=False))

    class FakeClient:
        def __init__(self, **kw) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create),
            )

    real = openai.AsyncOpenAI
    openai.AsyncOpenAI = FakeClient
    try:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            strings = {f"f{i}.rs": [f"Text{i}", f"id_{i}"] for i in range(5)}
            strings["f0.rs"].append("Got {}")
            (d / "s.json").write_text(json.dumps(strings))
            (d / "out.json").write_text(json.dumps({"f1.rs": {"Text1": "旧"}}))
            translate_all(
                str(d / "s.json"), str(d / "out.json"),
                glossary_path=str(d / "none.yaml"),
                ai_cfg=AIConfig(api_key="x", model="m", concurrency=2),
                do_not_translate=str(d / "dnt.json"),
            )
            out = json.loads((d / "out.json").read_text())
            dnt = json.loads((d / "dnt.json").read_text())
    finally:
        openai.AsyncOpenAI = real

    # 其余请求是 "Got {}" 的占位符修正与一致性修复
    packed = [r for r in requests if "以上字符串来自 5 个源文件" in r]
    assert len(packed) == 1, f"5 个小文件应合并为 1 个翻译请求，实际 {len(packed)}"
    assert out["f1.rs"]["Text1"] == "旧", "增量模式不应重翻已有译文"
    assert out["f3.rs"]["Text3"] == "译Text3"
    assert "Text3" not in out["f2.rs"], "译文不应回填到别的文件"
    recorded = {(e["file"], e["original"]) for e in dnt["entries"]}
    assert recorded == {(f"f{i}.rs", f"id_{i}") for i in range(5)}, recorded
    assert all(e["reason"] == "ai:translate-empty" for e in dnt["entries"])


def main() -> int:
    logging.disable(logging.WARNING)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
