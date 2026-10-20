#!/usr/bin/env python3
"""生成 AI 审核底档（Markdown + HTML），用于事后审计/复核。

用法:
  python3 scripts/dossier.py --work-dir ./PO0000000000
  python3 scripts/dossier.py --work-dir ./PO0000000000 --rules config/rules.local.json

输入:
  <work-dir>/audit_state.json
  <work-dir>/work_rows.json
  <work-dir>/audit_runlog.jsonl     （可选）
  <work-dir>/截图证据清单.xlsx        （可选）

输出:
  <work-dir>/AI审核底档-<date>.md / .html
"""
import argparse
import hashlib
import html as html_lib
import json
import os
import platform
import sys
from collections import Counter
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from audit_pipeline import apply_rules, rules_summary  # noqa: E402


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def safe_int(v):
    return int(v) if isinstance(v, (int, float)) else 0


def find_output(work_dir):
    cands = [os.path.join(work_dir, f) for f in os.listdir(work_dir)
             if f.endswith('.xlsx') and 'AI审核结果' in f]
    return max(cands, key=os.path.getmtime) if cands else None


def md_to_html(md):
    out, in_code, in_table, in_list = [], False, False, False
    for line in md.split('\n'):
        s = line.rstrip()

        def close_list():
            nonlocal in_list
            if in_list:
                out.append('</ul>')
                in_list = False

        if s.startswith('```'):
            close_list()
            if in_code:
                out.append('</pre>')
                in_code = False
            else:
                out.append('<pre>')
                in_code = True
            continue
        if in_code:
            out.append(html_lib.escape(line))
            continue
        if s.startswith('|'):
            close_list()
            cells = [c.strip() for c in s.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells):
                continue
            if not in_table:
                out.append('<table>')
                in_table = True
                tag = 'th'
            else:
                tag = 'td'
            out.append('<tr>' + ''.join(
                f'<{tag}>{html_lib.escape(c)}</{tag}>' for c in cells) + '</tr>')
            continue
        if in_table:
            out.append('</table>')
            in_table = False
        if s.startswith('#'):
            close_list()
            level = len(s) - len(s.lstrip('#'))
            out.append(f'<h{level}>{html_lib.escape(s[level:].strip())}</h{level}>')
        elif s.startswith(('- ', '* ')):
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append(f'<li>{html_lib.escape(s[2:])}</li>')
        elif s.strip() == '':
            close_list()
            out.append('')
        else:
            close_list()
            out.append(f'<p>{html_lib.escape(s)}</p>')
    close_list()
    if in_code:
        out.append('</pre>')
    if in_table:
        out.append('</table>')
    return '\n'.join(out)


CSS = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:980px;margin:32px auto;padding:0 20px;color:#1a1a1a;line-height:1.75}
h1{font-size:24px;color:#1F4E79;border-bottom:3px solid #1F4E79;padding-bottom:10px}
h2{font-size:17px;color:#1F4E79;background:#DCE6F1;padding:8px 12px;border-radius:4px;margin-top:28px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
th{background:#1F4E79;color:#fff;padding:8px 10px;text-align:left}
td{border:1px solid #B0B7C3;padding:7px 10px;vertical-align:top}
tr:nth-child(even) td{background:#F2F7FB}
code{background:#f4f4f4;padding:2px 6px;border-radius:3px;font-size:12px}
pre{background:#f7f9fc;border-left:4px solid #1F4E79;padding:12px;overflow-x:auto;font-size:12px}
li{margin:4px 0 4px 20px}
"""


def build(args):
    work_dir = os.path.abspath(args.work_dir)
    state = load_json(os.path.join(work_dir, 'audit_state.json'), {}) or {}
    rows = load_json(os.path.join(work_dir, 'work_rows.json'), []) or []
    out_xlsx = find_output(work_dir)

    source = args.source or state.get('source') or ''
    source_path = source if os.path.isabs(source) else os.path.join(work_dir, source)
    source_exists = bool(source) and os.path.exists(source_path)

    rule_ver = state.get('rule_ver', '-')
    rule_lines = []
    if args.rules:
        apply_rules(args.rules)
        a3, a4 = rules_summary()
        rule_lines = [a3, a4]
    else:
        rule_lines = ['未提供规则文件；如需完整规则摘要，请用 --rules 指定。']

    counts = {k: Counter(x.get(k) for x in rows) for k in ('a1', 'a2', 'a3', 'a4')}
    inc_sum = sum(safe_int(x.get('inc')) for x in rows)
    fee_sum = sum(safe_int(x.get('fee')) for x in rows)
    subtotal_sum = sum(safe_int(x.get('subtotal')) for x in rows)
    manual = state.get('manual') or {}
    issues = state.get('precheck_issues') or []

    runlog_path = os.path.join(work_dir, 'audit_runlog.jsonl')
    runlog_lines = []
    if os.path.exists(runlog_path):
        with open(runlog_path, encoding='utf-8', errors='ignore') as f:
            runlog_lines = [ln.rstrip() for ln in f if ln.strip()][-20:]

    artifacts = []
    for p in [source_path, out_xlsx,
              os.path.join(work_dir, '截图证据清单.xlsx'),
              os.path.join(work_dir, 'audit_state.json'),
              os.path.join(work_dir, 'work_rows.json'),
              runlog_path]:
        if p and os.path.isfile(p):
            artifacts.append((os.path.basename(p), sz(p), sha256(p)))

    stamp = args.date or datetime.now().strftime('%Y%m%d')
    title = args.title or '内容验收审核底档'
    md = []
    md.append(f'# {title}')
    md.append('')
    md.append('## 一、任务标识')
    md.append('')
    md.append('| 项目 | 内容 |')
    md.append('|---|---|')
    md.append(f'| 源文件 | {os.path.basename(source_path) if source else "（未记录）"} |')
    md.append(f'| 工作目录 | {work_dir} |')
    md.append(f'| 数据行 | {len(rows)} |')
    md.append(f'| 规则版本 | {rule_ver} |')
    md.append(f'| 审核时间 | {state.get("audited_at", "-")} |')
    md.append('')
    md.append('## 二、执行信息')
    md.append('')
    md.append(f'- 生成时间：{datetime.now().isoformat(timespec="seconds")}')
    md.append(f'- 运行平台：{platform.platform()}')
    md.append(f'- Python：{platform.python_version()}')
    md.append('- 判定方式：表格规则 + 截图 OCR 交叉核验；链接有效性独立复核'
              '（结果经 `link_review.py` / `--annotations` 注入）')
    md.append('')
    md.append('## 三、输入文件 SHA256')
    md.append('')
    md.append('| 文件 | SHA256 |')
    md.append('|---|---|')
    if source_exists:
        md.append(f'| {os.path.basename(source_path)} | `{sha256(source_path)}` |')
    else:
        md.append('| （未找到源文件） | - |')
    md.append('')
    md.append('## 四、规则依据')
    md.append('')
    for line in rule_lines:
        md.append(f'- {line}')
    md.append('')
    md.append('## 五、执行步骤记录')
    md.append('')
    if runlog_lines:
        md.append('```')
        md.extend(runlog_lines)
        md.append('```')
    else:
        md.append('- 未找到 `audit_runlog.jsonl`；以下为状态文件记录。')
        md.append(f'- 完播率口径：{state.get("calib", "-")}')
        md.append(f'- 预检问题数：{len(issues)}')
    md.append('')
    md.append('## 六、判定方法与口径')
    md.append('')
    md.append('- 审核1：主页链接可达 且 粉丝数 ≥ 阈值（以核验日实测值为准）')
    md.append('- 审核2：发布时间晚于上线时间 且 ≤72 小时 且 时长 ≥15 秒')
    md.append('- 审核3/4：按规则文件的档位阈值判定；0 元与缺数据按规则单独处理')
    md.append(f'- 本批完播率取值口径：{state.get("calib", "-")}')
    md.append('- 截图 OCR 与 bbox 裁决用于贴门槛值和全页 OCR 失真的复核')
    md.append('- 链接有效性只写“可达/失效”，不把 HTTP 200 当作内容可打开')
    md.append('')
    md.append('## 七、结果摘要与金额勾稽')
    md.append('')
    md.append('| 判定列 | 结果统计 |')
    md.append('|---|---|')
    for k, label in (('a1', '审核1'), ('a2', '审核2'), ('a3', '审核3'), ('a4', '审核4')):
        md.append(f'| {label} | {dict(counts[k])} |')
    md.append('')
    md.append('| 金额项 | 合计（元） |')
    md.append('|---|---:|')
    md.append(f'| 激励（含税） | {inc_sum:,} |')
    md.append(f'| 验收费用（未税） | {fee_sum:,} |')
    md.append(f'| 含税小计 | {subtotal_sum:,} |')
    md.append('')
    md.append('## 八、异常与人工确认项')
    md.append('')
    if issues:
        md.append('**预检问题**')
        md.append('')
        for it in issues:
            md.append(f'- {it}')
        md.append('')
    if manual:
        md.append('**人工确认项**')
        md.append('')
        md.append('| 序号 | 需确认内容 |')
        md.append('|---|---|')
        for seq in sorted(manual, key=lambda s: int(s) if str(s).isdigit() else 0):
            md.append(f'| {seq} | {manual[seq]} |')
    else:
        md.append('- 无人工确认项。')
    md.append('')
    md.append('## 九、复现命令')
    md.append('')
    md.append('```bash')
    md.append(f'python3 scripts/audit_pipeline.py "{source_path if source else "<源表.xlsx>"}"'
              + (f' --rules "{args.rules}"' if args.rules else ''))
    md.append(f'python3 scripts/evidence.py --work-dir "{work_dir}"')
    md.append(f'python3 scripts/dossier.py --work-dir "{work_dir}"'
              + (f' --rules "{args.rules}"' if args.rules else ''))
    md.append('```')
    md.append('')
    md.append('## 十、产物清单与 SHA256')
    md.append('')
    md.append('| 文件 | 大小（字节） | SHA256 |')
    md.append('|---|---:|---|')
    for name, size, digest in artifacts:
        md.append(f'| {name} | {size:,} | `{digest}` |')
    md.append('')

    md_text = '\n'.join(md)
    md_path = os.path.join(args.out_dir or work_dir, f'AI审核底档-{stamp}.md')
    html_path = os.path.join(args.out_dir or work_dir, f'AI审核底档-{stamp}.html')
    os.makedirs(os.path.dirname(os.path.abspath(md_path)), exist_ok=True)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_text)
    html_text = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                 f'<title>{html_lib.escape(title)}</title><style>{CSS}</style></head>'
                 f'<body>{md_to_html(md_text)}</body></html>')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_text)
    return md_path, html_path


def sz(path):
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser(description='Generate an audit dossier (.md + .html).')
    ap.add_argument('--work-dir', default=os.getcwd())
    ap.add_argument('--source', default=None, help='源表路径（默认从 audit_state.json 读取）')
    ap.add_argument('--rules', default=None, help='规则 JSON（用于规则摘要）')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--date', default=None, help='日期戳 YYYYMMDD（默认今天）')
    ap.add_argument('--title', default=None)
    args = ap.parse_args()
    md, htm = build(args)
    print(f'-> {md}')
    print(f'-> {htm}')


if __name__ == '__main__':
    main()
