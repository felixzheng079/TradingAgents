#!/usr/bin/env python3
"""Transform TradingAgents terminal-formatted markdown reports into clean, readable markdown."""

import re
from pathlib import Path

try:
    import wcwidth

    def display_width(text: str) -> int:
        return wcwidth.wcswidth(text) or len(text)

    def char_width(ch: str) -> int:
        w = wcwidth.wcwidth(ch)
        return w if w > 0 else 0
except ImportError:
    def display_width(text: str) -> int:
        return len(text)

    def char_width(ch: str) -> int:
        return 1


def clean_line(raw: str) -> str:
    """Strip box borders from a line."""
    s = raw.rstrip("\n\r")

    if s.startswith("│ "):
        s = s[2:]
    elif s.strip() == "│":
        return ""

    s = s.rstrip()
    if s.endswith("│"):
        s = s[:-1].rstrip()

    return s


def try_extract_section(line: str) -> str | None:
    """Try to extract a section name from any line, especially box-border lines."""
    s = line.strip()
    if not s:
        return None

    # If line has box-drawing border chars, extract text from between them
    if any(c in s for c in "╭╮╰╯"):
        # Remove corner/border chars
        cleaned = s
        for c in "╭╮╰╯═":
            cleaned = cleaned.replace(c, "")
        # Extract text parts between ─ characters
        parts = []
        current = ""
        for ch in cleaned:
            if ch == "─":
                if current.strip():
                    parts.append(current.strip())
                    current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())

        result = " ".join(parts).strip()
        if result and len(result) > 2:
            return result
        return None

    return None


def is_dash_hrule(s: str) -> bool:
    """Is this line primarily a horizontal rule (long dashes)?"""
    stripped = s.strip()
    if not stripped:
        return False
    dash_count = stripped.count("─") + stripped.count("-")
    space_count = stripped.count(" ")
    total = len(stripped)
    return dash_count > 30 and (dash_count + space_count) / total > 0.90


def is_table_row_sep(s: str) -> bool:
    """Is this line a table separator? Accepts both ─-only and ─-with-spaces patterns."""
    stripped = s.strip()
    if not stripped:
        return False
    if "─" not in stripped:
        return False
    dash_count = stripped.count("─")
    total = len(stripped)
    return dash_count > 10 and dash_count / total > 0.70


SECTION_MAP = {
    "I. Analyst Team Reports": "## I. Analyst Team Reports",
    "II. Research Team Decision": "## II. Research Team Decision",
    "Market Analyst": "### Market Analyst",
    "Social Analyst": "### Social Analyst",
    "News Analyst": "### News Analyst",
    "Fundamentals Analyst": "### Fundamentals Analyst",
    "Bull Researcher": "### Bull Researcher",
    "Bear Researcher": "### Bear Researcher",
}


def _find_regions_from_sep(sep_line: str) -> list[tuple[int, int]]:
    """Find column regions from a ─ separator line."""
    regions = []
    in_run = False
    start = 0
    for i, ch in enumerate(sep_line):
        if ch == "─" and not in_run:
            start = i
            in_run = True
        elif ch == " " and in_run:
            regions.append((start, i))
            in_run = False
    if in_run:
        regions.append((start, len(sep_line)))
    return regions


def _find_display_regions(header: str, data_rows: list[str], min_gap: int = 2) -> list[tuple[int, int]]:
    """Find column regions in display-width coordinates.
    Uses the header row's gap positions, then adjusts using all data rows."""
    if not header:
        return []

    # Find gaps in header
    gaps = []
    char_idx = 0
    display_pos = 0
    while char_idx < len(header):
        ch = header[char_idx]
        if ch == " ":
            gap_start = display_pos
            while char_idx < len(header) and header[char_idx] == " ":
                display_pos += 1
                char_idx += 1
            gap_width = display_pos - gap_start
            if gap_width >= min_gap:
                gaps.append((gap_start, display_pos))
        else:
            display_pos += char_width(ch)
            char_idx += 1

    if not gaps:
        return []

    total_width = display_pos
    regions = []
    regions.append((0, gaps[0][0]))
    for k in range(len(gaps) - 1):
        regions.append((gaps[k][1], gaps[k + 1][0]))
    regions.append((gaps[-1][1], total_width))
    regions = [(s, e) for s, e in regions if e > s]

    if not regions or not data_rows:
        return regions

    # Adjust region boundaries using data rows:
    # Expand each region to include overflow content from all rows
    adjusted = []
    for s, e in regions:
        max_e = e
        for row in data_rows:
            # Find where this row's content ends within this column region
            row_end = _find_content_end(row, s, e)
            if row_end > max_e:
                max_e = row_end
        adjusted.append((s, max_e))

    # Fix overlaps: each region's end must not exceed next region's start
    for i in range(len(adjusted) - 1):
        if adjusted[i][1] >= adjusted[i + 1][0]:
            adjusted[i] = (adjusted[i][0], adjusted[i + 1][0] - 1)

    return [(s, e) for s, e in adjusted if e > s]


def _find_content_end(text: str, region_start: int, region_end: int) -> int:
    """Find the display position where content actually ends within a column region."""
    display_pos = 0
    content_end = region_start
    in_content = False
    for ch in text:
        ch_width = char_width(ch)
        if display_pos >= region_end:
            break
        if display_pos >= region_start:
            if ch != " ":
                content_end = display_pos + ch_width
                in_content = True
        display_pos += ch_width
    return content_end if in_content else region_start


def _extract_by_display(text: str, start: int, end: int) -> str:
    """Extract substring between display-width positions."""
    chars = []
    display_pos = 0
    for ch in text:
        ch_width = char_width(ch)
        if display_pos >= end:
            break
        if display_pos >= start:
            chars.append(ch)
        display_pos += ch_width
    return "".join(chars).strip()


def parse_table(block: list[str]) -> str | None:
    """Parse a fixed-width table block into markdown table."""
    if not len(block) >= 3:
        return None

    # Find separator (any line that is a table row sep)
    sep_idx = None
    for i, line in enumerate(block):
        if is_table_row_sep(line):
            sep_idx = i
            break
    if sep_idx is None or sep_idx < 1:
        return None

    sep_line = block[sep_idx].strip()

    # Try separator-based regions (char positions = display positions for ─ chars)
    char_regions = _find_regions_from_sep(sep_line)

    if len(char_regions) >= 2:
        # Separator has multiple ─ runs with spaces → use char positions as display positions
        regions = char_regions
    else:
        # Separator is pure ─ → use header-based display-width detection with data row adjustment
        header_line = None
        for l in reversed(block[:sep_idx]):
            stripped = l.strip()
            if stripped and not is_dash_hrule(stripped) and not is_table_row_sep(stripped):
                header_line = stripped
                break
        if not header_line:
            return None

        # Collect data rows first to help with column detection
        data_rows_raw = []
        for l in block[sep_idx + 1:]:
            stripped = l.strip()
            if stripped and not is_table_row_sep(stripped) and not is_dash_hrule(stripped):
                data_rows_raw.append(stripped)

        regions = _find_display_regions(header_line, data_rows_raw)

    if len(regions) < 2:
        return None

    # Header: last non-blank before separator
    header_line = None
    for l in reversed(block[:sep_idx]):
        stripped = l.strip()
        if stripped and not is_dash_hrule(stripped) and not is_table_row_sep(stripped):
            header_line = stripped
            break
    if not header_line:
        return None

    # Data rows after separator
    data_lines = []
    for l in block[sep_idx + 1 :]:
        stripped = l.strip()
        if stripped and not is_table_row_sep(stripped) and not is_dash_hrule(stripped):
            data_lines.append(stripped)

    if not data_lines:
        return None

    headers = [_extract_by_display(header_line, s, e) for s, e in regions]
    while headers and headers[-1] == "":
        headers.pop()
        regions.pop()

    if len(headers) < 2:
        return None

    rows = []
    for dl in data_lines:
        cells = [_extract_by_display(dl, s, e) for s, e in regions[: len(headers)]]
        rows.append(cells)

    if not rows:
        return None

    md = []
    md.append("| " + " | ".join(headers) + " |")
    md.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        md.append("| " + " | ".join(padded[:len(headers)]) + " |")

    return "\n".join(md)


def determine_meta(fp: str, content_hint: str = "") -> tuple[str, str, str]:
    """Get ticker, name, date from file path. Falls back to content scan for unknown stocks."""
    name = Path(fp).stem
    date = Path(fp).parent.name
    known = {
        "D-Wave Quantum": ("QBTS", "D-Wave Quantum Inc."),
        "SEALSQ": ("LAES", "SEALSQ Corp"),
        "Sivers Semiconductors": ("SIVE.ST", "Sivers Semiconductors AB"),
    }
    if name in known:
        return known[name][0], known[name][1], date

    # Try to extract ticker from content (pattern: "QBTS" or "LAES" in parentheses)
    ticker = None
    if content_hint:
        m = re.search(r'\b([A-Z]{1,6}(?:\.[A-Z]{1,4})?)\b', content_hint)
        if m:
            ticker = m.group(1)
    return ticker or name[:12], name, date


def _anchor(text: str) -> str:
    """Generate GitHub-style anchor from heading text."""
    anchor = text.lower()
    anchor = re.sub(r'[^\w\s-]', '', anchor)
    anchor = re.sub(r'\s+', '-', anchor)
    return anchor


def build_toc(lines: list[str]) -> list[str]:
    """Build a table of contents from markdown headings in the output lines."""
    toc = ["## Contents", ""]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("## Contents"):
            text = stripped[3:].strip()
            anchor = _anchor(text)
            toc.append(f"- [{text}](#{anchor})")
        elif stripped.startswith("### "):
            text = stripped[4:].strip()
            anchor = _anchor(text)
            toc.append(f"  - [{text}](#{anchor})")
    toc.append("")
    return toc


def transform_file(input_path: str, output_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        raw = f.readlines()

    ticker, full_name, date_str = determine_meta(input_path)

    # Skip existing YAML frontmatter and TOC if present (for re-runs)
    start_idx = 0
    if raw and raw[0].strip() == "---":
        start_idx = 1
        for i in range(1, len(raw)):
            if raw[i].strip() == "---":
                start_idx = i + 1
                break
    # Skip previously-generated TOC
    while start_idx < len(raw) and raw[start_idx].strip() in ("", "## Contents"):
        if raw[start_idx].strip() == "## Contents":
            start_idx += 1
            while start_idx < len(raw) and (
                raw[start_idx].strip().startswith("- ") or raw[start_idx].strip() == ""
            ):
                start_idx += 1
        else:
            start_idx += 1
    # Skip old frontmatter block that may be embedded in content from prior runs
    if start_idx < len(raw) and raw[start_idx].strip().startswith("ticker:"):
        while start_idx < len(raw) and raw[start_idx].strip() != "---":
            start_idx += 1
        if start_idx < len(raw):
            start_idx += 1  # skip the closing ---
        while start_idx < len(raw) and raw[start_idx].strip() == "":
            start_idx += 1
    raw = raw[start_idx:]

    # Clean all lines, preserve blank lines
    cleaned = []
    for line in raw:
        stripped = line.strip()

        # Skip banner
        if "Complete Analysis Report" in stripped and any(c in stripped for c in "─═"):
            continue

        # Check for section-in-border-line (e.g. ╭── Market Analyst ──╮)
        section = try_extract_section(stripped)
        if section:
            heading = SECTION_MAP.get(section)
            if heading:
                cleaned.append({"type": "section", "text": heading})
                continue

        # Clean and classify
        cl = clean_line(line)
        if cl is None:
            continue

        st = cl.strip()

        if not st:
            cleaned.append({"type": "blank"})
            continue

        # Check if the cleaned line is itself a heading
        for key, heading in SECTION_MAP.items():
            if st.startswith(key):
                cleaned.append({"type": "section", "text": heading})
                break
        else:
            if is_table_row_sep(st):
                cleaned.append({"type": "table_sep", "text": st})
            elif is_dash_hrule(st):
                cleaned.append({"type": "hrule", "text": st})
            elif st.startswith("▌"):
                cleaned.append({"type": "blockquote", "text": st[1:].strip()})
            elif re.match(r'FINAL\s+TRANSACTION\s+PROPOSAL:\s*(.+)', st):
                m = re.match(r'FINAL\s+TRANSACTION\s+PROPOSAL:\s*(.+)', st)
                cleaned.append({"type": "proposal", "text": m.group(1).strip()})
            else:
                cleaned.append({"type": "text", "text": cl})

    # Find table blocks (using table_sep or hrule markers as possible separators)
    table_blocks = []
    i = 0
    while i < len(cleaned):
        item = cleaned[i]
        if item["type"] in ("table_sep", "hrule") and "─" in item.get("text", ""):
            sep_text = item.get("text", "")

            # Find header: last text item before separator (scan backwards, skip blanks)
            header_idx = i - 1
            while header_idx >= 0 and cleaned[header_idx]["type"] in ("blank",):
                header_idx -= 1
            if header_idx < 0 or cleaned[header_idx]["type"] != "text":
                i += 1
                continue
            header_text = cleaned[header_idx]["text"]

            # Find data rows: consecutive text items after separator (skip blanks)
            data_idx = i + 1
            while data_idx < len(cleaned) and cleaned[data_idx]["type"] == "blank":
                data_idx += 1
            data_rows = []
            while data_idx < len(cleaned) and cleaned[data_idx]["type"] == "text":
                data_rows.append(cleaned[data_idx]["text"])
                data_idx += 1

            if len(data_rows) < 1:
                i += 1
                continue

            # Wrap fixed-width table in code block for monospace rendering
            table_lines = [header_text.strip(), sep_text.strip()] + [r.strip() for r in data_rows]
            table_code = "```\n" + "\n".join(table_lines) + "\n```"
            table_blocks.append((i, data_idx, header_idx, table_code))
            i = data_idx
        else:
            i += 1

    # Build output, skipping table regions
    out = []
    table_skip = {}  # {index: (end, markdown)}
    for s, e, h, md in table_blocks:
        table_skip[h] = (e, md)  # skip from header to end of data

    last_type = None  # track for spacing control

    i = 0
    while i < len(cleaned):
        # Check if this index starts a table
        if i in table_skip:
            end, md = table_skip[i]
            out.append("")
            out.append(md)
            out.append("")
            i = end
            last_type = "table"
            continue

        item = cleaned[i]
        t = item["type"]

        if t == "blank":
            out.append("")
            last_type = "blank"
        elif t == "section":
            out.append("")
            out.append(item["text"])
            out.append("")
            last_type = "section"
        elif t == "hrule":
            # Suppress hrules that appear between closely spaced content sections
            if last_type not in ("hrule", "section"):
                out.append("---")
                last_type = "hrule"
        elif t == "proposal":
            out.append("")
            out.append(f"> **Final Proposal: {item['text']}**")
            out.append("")
            last_type = "proposal"
        elif t == "blockquote":
            out.append(f"> {item['text']}")
            last_type = "blockquote"
        elif t == "text":
            # Don't add blank separator for consecutive text items
            if last_type not in ("text", "blockquote"):
                out.append("")
            out.append(item["text"])
            last_type = "text"
        elif t == "table_sep":
            # Skip isolated table separators (already handled by table blocks)
            pass

        i += 1

    # Collapse blank lines: max 1 consecutive blank, strip leading spaces from text
    final = []
    blank_count = 0
    for line in out:
        s = line.strip()
        if s == "":
            blank_count += 1
            if blank_count <= 1:
                final.append("")
        elif s == "---":
            # Suppress --- that appears right after frontmatter or between headings
            if not final:
                continue
            if final[-1].strip() == "---":
                continue
            # Check if preceded by heading
            prev_idx = len(final) - 1
            while prev_idx >= 0 and final[prev_idx].strip() == "":
                prev_idx -= 1
            if prev_idx >= 0 and final[prev_idx].strip().startswith("#"):
                continue
            final.append("---")
            blank_count = 0
        else:
            # Strip leading whitespace from content that came from right-padded box format
            if not s.startswith("#") and not s.startswith(">") and not s.startswith("|"):
                line = line.lstrip()
            blank_count = 0
            final.append(line)

    # Remove leading blanks
    while final and final[0].strip() == "":
        final.pop(0)

    # Remove trailing blanks
    while final and final[-1].strip() == "":
        final.pop()

    # Remove --- at the very start
    if final and final[0].strip() == "---":
        final.pop(0)
        while final and final[0].strip() == "":
            final.pop(0)

    # Build frontmatter + TOC + content
    toc = build_toc(final)
    result = [
        "---",
        f'ticker: "{ticker}"',
        f'name: "{full_name}"',
        f'date: "{date_str}"',
        "---",
        "",
    ]
    result.extend(toc)
    result.extend(final)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(result))
        f.write("\n")


def main():
    import sys

    if len(sys.argv) > 1:
        # Process specific paths passed as arguments
        for arg in sys.argv[1:]:
            p = Path(arg)
            if p.is_dir():
                for md_file in sorted(p.glob("*.md")):
                    print(f"Processing: {md_file}")
                    transform_file(str(md_file), str(md_file))
            elif p.is_file() and p.suffix == ".md":
                print(f"Processing: {p}")
                transform_file(str(p), str(p))
            else:
                print(f"Skipping: {arg} (not a .md file or directory)")
    else:
        # Default: process all reports in the reports directory
        reports_dir = Path(__file__).parent.parent / "reports"
        if not reports_dir.exists():
            print(f"Reports directory not found: {reports_dir}")
            sys.exit(1)
        for subdir in sorted(reports_dir.iterdir()):
            if not subdir.is_dir():
                continue
            for md_file in sorted(subdir.glob("*.md")):
                print(f"Processing: {md_file.relative_to(reports_dir)}")
                transform_file(str(md_file), str(md_file))


if __name__ == "__main__":
    main()
