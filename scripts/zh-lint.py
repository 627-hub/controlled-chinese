#!/usr/bin/env python3
"""Conservative mechanical checks for agent-facing Chinese text.

This script does not prove that text is unambiguous or that a rewrite is
faithful. It skips fenced code, inline code, URLs, and non-prose lines
(Markdown tables, blockquotes, indented code, timestamp/log lines, raw JSON).
Per-line rules only run on lines that contain CJK characters, so English text
is never flagged. Warnings never affect the exit code; only errors above the
configured baseline do.

Two rules grade multi-action signals:

  multi-action       strong: a semicolon joins clauses in Chinese prose.
  multi-action-soft  weak: ，然后／接着／随后／同时 joins actions.

Three profiles control how much of that noise you want:

  chat  narration and reviews; both multi-action rules off.
  doc   long-form default; strong only, at most one finding per line.
  spec  specifications and step lists; both rules, every semicolon reported.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

CJK = r"\u3400-\u9fff"
CJK_RE = re.compile(rf"[{CJK}]")
# 行首时间戳／日志前缀：2026-10-25 10:00:25、[2026-10-25]、10:00:25
TIMESTAMP_RE = re.compile(
    r"^\[?\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\]?"
    r"|^\[?\d{1,2}:\d{2}(?::\d{2})?\]?"
)
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
JSON_FIELD_RE = re.compile(r'"[^"\n]+"\s*:')
RULE_NAMES = {
    "mixed-brackets",
    "ascii-comma",
    "duplicate-punctuation",
    "multi-action",
    "multi-action-soft",
    "negation-scope",
    "quantity-range",
    "pronoun-reference",
    "terminology",
}

DEFAULT_PROFILE = "doc"
PROFILES = {
    # 聊天与复盘叙述：分号多为并列分隔，不是多动作
    "chat": {"disabled": {"multi-action", "multi-action-soft"}, "dedupe": False},
    # 长文与报告：只报强信号，同一行最多一条
    "doc": {"disabled": {"multi-action-soft"}, "dedupe": True},
    # 规范与步骤：强弱都报，保留每个分号的位置
    "spec": {"disabled": set(), "dedupe": False},
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    column: int
    severity: str
    rule: str
    message: str
    excerpt: str


def _blank_match(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def mask_inline(line: str) -> str:
    """Mask inline code and URLs while preserving columns."""
    line = re.sub(r"`[^`\n]*`", _blank_match, line)
    line = re.sub(r"https?://[^\s<>]+", _blank_match, line)
    return line


def iter_checkable_lines(text: str) -> Iterable[tuple[int, str, str]]:
    """Yield line number, raw line, and masked line outside fenced code."""
    fence_char: str | None = None
    for line_number, raw in enumerate(text.splitlines(), start=1):
        fence = re.match(r"^\s*(`{3,}|~{3,})", raw)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char = marker[0]
            elif marker[0] == fence_char:
                fence_char = None
            continue
        if fence_char is not None:
            continue
        yield line_number, raw, mask_inline(raw)


def is_table_row(raw: str) -> bool:
    stripped = raw.strip()
    if not stripped:
        return False
    if stripped.startswith("|"):
        return True
    # 管道分隔的数据行（行情、CSV 式粘贴）：至少两个竖线
    return stripped.count("|") >= 2


def is_indented_code(raw: str) -> bool:
    """四空格或制表符缩进的块；嵌套列表项不算代码块。"""
    if not (raw.startswith("    ") or raw.startswith("\t")):
        return False
    return LIST_ITEM_RE.match(raw) is None


def is_structured_line(raw: str) -> bool:
    """时间戳／日志行与裸 JSON 行不是散文。"""
    stripped = raw.strip()
    if not stripped:
        return False
    if TIMESTAMP_RE.match(stripped):
        return True
    if stripped[0] in "{[":
        return JSON_FIELD_RE.search(stripped) is not None
    return False


def is_prose_line(raw: str) -> bool:
    """判断一行是否属于需要检查的散文。

    跳过 Markdown 表格、引用块、缩进代码块、时间戳／日志行、裸 JSON。
    """
    stripped = raw.strip()
    if not stripped:
        return False
    if stripped.startswith(">"):
        return False
    if is_table_row(raw):
        return False
    if is_indented_code(raw):
        return False
    return not is_structured_line(raw)


def iter_prose_lines(text: str) -> Iterable[tuple[int, str, str]]:
    """只产出散文行；围栏代码块内的内容仍全部跳过。"""
    for line_number, raw, masked in iter_checkable_lines(text):
        if is_prose_line(raw):
            yield line_number, raw, masked


def make_finding(
    path: str,
    line_number: int,
    column: int,
    severity: str,
    rule: str,
    message: str,
    raw: str,
) -> Finding:
    excerpt = raw.strip()
    if len(excerpt) > 120:
        excerpt = excerpt[:117] + "..."
    return Finding(
        file=path,
        line=line_number,
        column=column,
        severity=severity,
        rule=rule,
        message=message,
        excerpt=excerpt,
    )


def scan_regex(
    findings: list[Finding],
    path: str,
    line_number: int,
    raw: str,
    line: str,
    pattern: str,
    rule: str,
    severity: str,
    message: str,
    flags: int = 0,
) -> None:
    for match in re.finditer(pattern, line, flags):
        findings.append(
            make_finding(
                path,
                line_number,
                match.start() + 1,
                severity,
                rule,
                message,
                raw,
            )
        )


def load_glossary(path: str | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("术语表必须是 JSON 对象。")
    glossary: dict[str, list[str]] = {}
    for canonical, variants in data.items():
        if not isinstance(canonical, str) or not canonical:
            raise ValueError("批准术语必须是非空字符串。")
        if not isinstance(variants, list) or not all(
            isinstance(item, str) and item for item in variants
        ):
            raise ValueError(f"术语 {canonical!r} 的变体必须是非空字符串数组。")
        if canonical in variants:
            raise ValueError(f"术语 {canonical!r} 不应同时出现在自己的变体表中。")
        glossary[canonical] = variants
    return glossary


def run_checks(
    text: str,
    path: str = "<stdin>",
    glossary: dict[str, list[str]] | None = None,
    disabled: set[str] | None = None,
    profile: str = DEFAULT_PROFILE,
) -> list[Finding]:
    if profile not in PROFILES:
        raise ValueError(f"未知 profile: {profile}")
    disabled = set(disabled or set()) | PROFILES[profile]["disabled"]
    glossary = glossary or {}
    findings: list[Finding] = []
    previous_line = ""

    for line_number, raw, line in iter_prose_lines(text):
        # 只检查含中日韩字符的行；英文文本一律跳过，避免半角标点误报
        if not CJK_RE.search(line):
            continue
        if "mixed-brackets" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                r"（[^（）()\n]*\)|\([^（）()\n]*）",
                "mixed-brackets", "error",
                "括号全半角混配；统一括号形式。",
            )
        if "ascii-comma" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                rf"(?<=[{CJK}]),(?=[{CJK}])",
                "ascii-comma", "error",
                "两个汉字之间使用 ASCII 逗号；确认是否应使用中文逗号。",
            )
        if "duplicate-punctuation" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                r"，，|。。|；；|、、|：：",
                "duplicate-punctuation", "error",
                "句读符号连续重复；确认是否为排版错误。",
            )
        if "multi-action" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                r"；|;",
                "multi-action", "warning",
                "分号连接多个信息单元；确认是否需要拆句或列项。",
            )
        if "multi-action-soft" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                r"，\s*(?:然后|接着|随后|同时)",
                "multi-action-soft", "warning",
                "连词连接的动作是弱信号；把这段当作步骤规范时才需要拆句。",
            )
        if "negation-scope" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                r"(?:所有|全部)(?=[^。！？\n]{0,40}都不)",
                "negation-scope", "warning",
                "全部否定与部分否定可能混淆；明确否定范围。",
            )
        if "quantity-range" not in disabled:
            scan_regex(
                findings, path, line_number, raw, line,
                rf"\d+(?:\.\d+)?\s*(?:[A-Za-z%°℃]+|[{CJK}]{{0,4}})?\s*(?:以上|以下|以内|以外)",
                "quantity-range", "warning",
                "数量或时间范围的端点可能不清；确认是否包含端点。",
            )
        if "pronoun-reference" not in disabled:
            if re.search(r"(?:和|与|及|以及)", previous_line):
                scan_regex(
                    findings, path, line_number, raw, line,
                    r"^\s*(?:其|它|他们|它们)",
                    "pronoun-reference", "warning",
                    "前句存在并列对象；确认该指代是否只有一个先行项。",
                )
        if line.strip():
            previous_line = line

    if glossary and "terminology" not in disabled:
        # 术语表是显式配置的可选检查；表格里常列术语，因此不做散文过滤。
        checkable_lines = list(iter_checkable_lines(text))
        full_text = "\n".join(line for _, _, line in checkable_lines)
        for canonical, variants in glossary.items():
            canonical_flags = re.IGNORECASE if canonical.isascii() else 0
            canonical_present = re.search(re.escape(canonical), full_text, canonical_flags) is not None
            for variant in variants:
                flags = re.IGNORECASE if variant.isascii() else 0
                for line_number, raw, line in checkable_lines:
                    for match in re.finditer(re.escape(variant), line, flags):
                        if canonical_present:
                            message = f"术语 {canonical!r} 与变体 {variant!r} 混用；按项目术语表统一。"
                        else:
                            message = f"使用项目术语 {canonical!r}，不要使用变体 {variant!r}。"
                        findings.append(
                            make_finding(
                                path, line_number, match.start() + 1,
                                "warning", "terminology", message, raw,
                            )
                        )

    if PROFILES[profile]["dedupe"]:
        seen: set[tuple[str, int, str]] = set()
        kept: list[Finding] = []
        for finding in findings:
            if finding.rule.startswith("multi-action"):
                key = (finding.file, finding.line, finding.rule)
                if key in seen:
                    continue
                seen.add(key)
            kept.append(finding)
        findings = kept

    findings.sort(key=lambda item: (item.file, item.line, item.column, item.rule))
    return findings


def counts(findings: Iterable[Finding]) -> tuple[int, int]:
    errors = 0
    warnings = 0
    for finding in findings:
        if finding.severity == "error":
            errors += 1
        else:
            warnings += 1
    return errors, warnings


def exit_code(findings: Iterable[Finding], baseline: int = 0) -> int:
    errors, _ = counts(findings)
    return 1 if errors > baseline else 0


def selftest() -> int:
    cases = [
        ("clean", "错误码 E500：服务暂时不可用。\n请稍后重试。", set(), "doc"),
        ("preserve modality", "请求可能已失败。任务尚未完成。", set(), "doc"),
        ("preserve scope", "仅管理员可以删除日志。", set(), "doc"),
        ("mixed Chinese bracket", "请检查配置（见附录A)。", {"mixed-brackets"}, "doc"),
        ("mixed ASCII bracket", "请检查配置(见附录A）。", {"mixed-brackets"}, "doc"),
        ("ASCII comma", "读取配置,启动服务。", {"ascii-comma"}, "doc"),
        ("duplicate punctuation", "服务不可用。。请重试。", {"duplicate-punctuation"}, "doc"),
        ("semicolon", "读取配置；启动服务。", {"multi-action"}, "doc"),
        ("sequential action is soft", "读取配置，然后启动服务。", {"multi-action-soft"}, "spec"),
        ("simultaneous action is soft", "读取配置，同时启动服务。", {"multi-action-soft"}, "spec"),
        ("English semicolon is ignored", "Read config; then start the service.", set(), "doc"),
        ("English comma is ignored", "Read config, start the service.", set(), "doc"),
        ("table row is ignored", "| 读取配置；启动服务 | 通过 |\n| --- | --- |", set(), "doc"),
        ("blockquote is ignored", "> 读取配置；再启动服务。", set(), "doc"),
        ("indented block is ignored", "示例：\n\n    读取配置；启动服务。", set(), "doc"),
        ("log timestamp is ignored", "[2026-10-25 10:00:25] 读取配置；启动服务。", set(), "doc"),
        ("short timestamp is ignored", "10:00:25 读取配置；启动服务。", set(), "doc"),
        ("JSON line is ignored", '{"note": "读取配置；启动服务"}', set(), "doc"),
        ("negation", "所有任务都不要重试。", {"negation-scope"}, "doc"),
        ("range", "超时 30 秒以上的任务会终止。", {"quantity-range"}, "doc"),
        ("pronoun", "将配置写入缓存和数据库。\n它会被删除。", {"pronoun-reference"}, "doc"),
        ("unique reference is clean", "读取配置。它会决定启动方式。", set(), "doc"),
        (
            "code and URL",
            "使用 `配置,测试` 作为标签。\nhttps://example.com/a;b。\n```\n所有任务都不要重试。\n```",
            set(),
            "doc",
        ),
    ]
    passed = 0
    for name, text, expected, profile in cases:
        actual = {finding.rule for finding in run_checks(text, name, profile=profile)}
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected}, got {actual}")
        passed += 1

    many = "第一项；第二项；第三项。"
    assert len(run_checks(many, "doc", profile="doc")) == 1
    assert len(run_checks(many, "spec", profile="spec")) == 2
    passed += 1

    chat = run_checks("读取配置；启动服务。", "chat", profile="chat")
    assert chat == []
    chat_comma = run_checks("读取配置,启动服务。", "chat", profile="chat")
    assert [finding.rule for finding in chat_comma] == ["ascii-comma"]
    passed += 1

    try:
        run_checks("x", profile="nope")
    except ValueError:
        passed += 1
    else:
        raise AssertionError("unknown profile must raise")

    glossary = {"任务": ["作业"]}
    mixed = run_checks("创建任务后，系统返回作业编号。", glossary=glossary)
    assert [finding.rule for finding in mixed] == ["terminology"]
    assert "混用" in mixed[0].message
    passed += 1

    noncanonical = run_checks("系统返回作业编号。", glossary=glossary)
    assert [finding.rule for finding in noncanonical] == ["terminology"]
    assert "不要使用变体" in noncanonical[0].message
    passed += 1

    clean_glossary = run_checks("系统返回任务编号。", glossary=glossary)
    assert clean_glossary == []
    passed += 1

    disabled = run_checks(
        "读取配置，然后启动服务。", disabled={"multi-action-soft"}, profile="spec"
    )
    assert disabled == []
    passed += 1

    strong_only = run_checks(
        "读取配置；启动服务。", disabled={"multi-action-soft"}, profile="spec"
    )
    assert [finding.rule for finding in strong_only] == ["multi-action"]
    passed += 1

    errors = run_checks("读取配置,启动服务。")
    assert exit_code(errors, baseline=0) == 1
    assert exit_code(errors, baseline=1) == 0
    passed += 1

    warnings = run_checks("所有任务都不要重试。")
    assert exit_code(warnings, baseline=0) == 0
    passed += 1

    print(f"selftest: {passed} passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对中文技术文本执行保守的机械检查；不证明文本无歧义。"
    )
    parser.add_argument("files", nargs="*", help="UTF-8 文本或 Markdown 文件；省略时读取 stdin。")
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    parser.add_argument("--baseline", type=int, default=0, help="允许保留的 error 数量，默认 0。")
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE,
        help="文本类型预设：chat 关闭多动作检查；doc 只报强信号且每行一条；"
             "spec 保留强弱全部命中。默认 %(default)s。",
    )
    parser.add_argument(
        "--disable", default="",
        help="逗号分隔的检查规则名，在 profile 之上再关闭指定规则。",
    )
    parser.add_argument("--glossary", help="项目术语表 JSON 路径。")
    parser.add_argument("--selftest", action="store_true", help="运行内置回归测试。")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.baseline < 0:
        parser.error("--baseline 不能小于 0。")

    disabled = {item.strip() for item in args.disable.split(",") if item.strip()}
    unknown = disabled - RULE_NAMES
    if unknown:
        parser.error("未知检查规则：" + ", ".join(sorted(unknown)))

    try:
        glossary = load_glossary(args.glossary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"zh-lint: 无法读取术语表：{exc}", file=sys.stderr)
        return 2

    inputs: list[tuple[str, str]] = []
    if args.files:
        for filename in args.files:
            try:
                inputs.append((filename, Path(filename).read_text(encoding="utf-8-sig")))
            except OSError as exc:
                print(f"zh-lint: 无法读取 {filename}：{exc}", file=sys.stderr)
                return 2
    else:
        inputs.append(("<stdin>", sys.stdin.read()))

    findings: list[Finding] = []
    for path, text in inputs:
        findings.extend(run_checks(text, path, glossary, disabled, args.profile))

    errors, warnings = counts(findings)
    if args.json:
        print(json.dumps({
            "findings": [asdict(finding) for finding in findings],
            "summary": {
                "errors": errors,
                "warnings": warnings,
                "baseline": args.baseline,
                "profile": args.profile,
                "semantics_checked": False,
                "faithfulness_checked": False,
            },
        }, ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(
                f"{finding.file}:{finding.line}:{finding.column}: "
                f"[{finding.severity}] {finding.rule}: {finding.message} "
                f"| {finding.excerpt}"
            )
        print(f"zh-lint: {errors} error(s), {warnings} warning(s).")

    return exit_code(findings, args.baseline)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"selftest failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
