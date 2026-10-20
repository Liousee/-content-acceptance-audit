#!/usr/bin/env python3
"""核验结果 → 交付物桥接：生成 PO<号>/链接复核/ 报告，并【合并】写入 audit_annotations.json

用法:
  python3 link_review.py <表.xlsx> [--db ~/.cache/yanshou/verify_db.jsonl] [--po-dir PO<号>/]
                           [--annotations audit_annotations.json] [--date YYYYMMDD] [--dry-run]

产出:
  1) <po>/链接复核/链接复核明细-<date>.json   逐行 {status, display, url, title, fee, cached, verified_at}
  2) <po>/链接复核/链接复核报告-<date>.md     范围/方法/安全控制/失效清单+金额/缓存命中率/逐条证据
  3) 合并写 annotations（关键：**合并不覆盖**）
     - link_status : 全量 seq → 有效/失效(跳转推荐流/404)/未复核   （着色约定见 audit_pipeline）
     - fans_fix    : 仅当作品页取到作者粉丝数时写入 {seq: 值}
     - extra_sections : 只替换标题以「八、」开头的小节，一~七原样保留
     - manual      : 只追加（失效且有验收费的行提示待确认）；**绝不自动改金额**
"""
import sys
import argparse, json, os, re, time
from collections import Counter, defaultdict
from datetime import datetime
import openpyxl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_common import DB, DISPLAY, VALID, INVALID, SKIPPED, norm_url

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--db', default=None)
    ap.add_argument('--po-dir', default=None)
    ap.add_argument('--annotations', default=None)
    ap.add_argument('--date', default=datetime.now().strftime('%Y%m%d'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fans-json', default=None,
                    help='fans_check.py 产出的 fans_check.json：把粉丝数按 sec_uid 导入核验库并写入 fans_fix')
    args = ap.parse_args()

    xlsx = os.path.abspath(args.xlsx)
    po = args.po_dir or os.path.dirname(xlsx)
    m = re.search(r'PO(\d+)', os.path.basename(xlsx))
    if m and not args.po_dir:
        cand = os.path.join(os.path.dirname(xlsx), f'PO{m.group(1)}')
        if os.path.isdir(cand): po = cand
    outdir = os.path.join(po, '链接复核'); os.makedirs(outdir, exist_ok=True)
    ann_path = args.annotations or os.path.join(po, 'audit_annotations.json')

    db = DB(path=args.db) if args.db else DB()
    if args.fans_json and os.path.exists(args.fans_json):
        import re as _re
        fj = json.load(open(args.fans_json))
        n_imp = 0
        for sec, r in (fj.items() if isinstance(fj, dict) else []):
            if not isinstance(r, dict) or not r.get('fans'): continue
            homepage = next((x for x in [r.get('home')] if x), None)
            db.put(f's:{sec}', VALID, fans=r['fans'], fans_raw=r.get('fans_raw'),
                   source='home', nick=r.get('nick_now'), seqs=r.get('seqs', []))
            n_imp += 1
        if n_imp:
            db.save_snapshot(); print(f'已从 {os.path.basename(args.fans_json)} 导入粉丝数 {n_imp} 位到达人')
    ws = openpyxl.load_workbook(xlsx, data_only=True)['验收链接']
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {h: i + 1 for i, h in enumerate(hdr) if h}

    rows, by_key = [], defaultdict(list)
    for r in range(2, ws.max_row + 1):
        seq = ws.cell(r, ci['序号']).value
        if seq is None: continue
        link_h = ci.get('发布链接')
        name_h = ci.get('姓名')
        fee_h = ci.get('验收费用（未税）')
        link = str(ws.cell(r, link_h).value or '').strip() if link_h else ''
        name = ws.cell(r, name_h).value if name_h else None
        fee = ws.cell(r, fee_h).value if fee_h else None
        # 模板预留行：链接/姓名/费用全空 → 跳过
        if not link and name in (None, '') and fee in (None, ''):
            continue
        kind, key, canon = norm_url(link)
        rec = db.get(key, max_age=10 ** 9) if key else None   # 报告不过期，只报事实
        rows.append(dict(seq=seq, name=name, link=link, fee=fee,
                         key=key, kind=kind, rec=rec))
        if key: by_key[key].append(seq)

    detail, invalid_fee_rows, valid_n, unrev_n, fans_fix = {}, [], 0, 0, {}
    for x in rows:
        rec = x['rec']
        if rec:
            st = rec.get('status'); disp = DISPLAY.get(st, '未复核')
            detail[str(x['seq'])] = dict(status=st, display=disp, url=x['link'],
                                         title=rec.get('title'), final=rec.get('final_url'),
                                         reason=rec.get('reason'), lane=rec.get('lane'),
                                         ts=rec.get('ts'), cached=True, fee=x['fee'], kind=x['kind'])
            if st == VALID: valid_n += 1
            if st == INVALID:
                if isinstance(x['fee'], (int, float)) and x['fee'] > 0: invalid_fee_rows.append(x)
            card = rec.get('card')
            if card and card.get('fans') and not card.get('ambiguous'):
                fans_fix[str(x['seq'])] = card['fans'] if not card.get('fans_rounded') else card['fans']
        else:
            unrev_n += 1
            detail[str(x['seq'])] = dict(status='PENDING', display='未复核', url=x['link'], fee=x['fee'],
                                         reason='未复核(不在本次核验范围或未跑完)')

    summary = Counter(v['status'] for v in detail.values())
    fee_total = sum(v['fee'] for v in invalid_fee_rows if isinstance(v['fee'], (int, float)))
    tax_total = sum(v['fee'] * 1.06 for v in invalid_fee_rows if isinstance(v['fee'], (int, float)))
    print('== 核验结果:', dict(summary))
    print(f'   失效且有验收费: {len(invalid_fee_rows)} 行 | 未税合计 {fee_total:.0f} 元 | 含税合计 {tax_total:.0f} 元')
    print(f'   作者粉丝数可回填: {len(fans_fix)} 行')

    invalids = [(s, v) for s, v in sorted(detail.items(), key=lambda kv: int(kv[0])) if v['status'] == INVALID]
    lines = [f'# 发布链接有效性复核（{args.date}）', '',
             f'范围: 全表 {len(detail)} 行（其中 {unrev_n} 行未复核）',
             '方法: 免登录无头渲染(--dump-dom) 判定 有效=真实作品页(评论区/相关推荐特征+作品标题)；失效=通用落地页或跳转推荐流(/jingxuan, 部分带 web_video_404_link)',
             '结果: ' + ' / '.join(f'{k} {v}' for k, v in summary.items()), '']
    if invalids:
        lines += ['## 失效清单', '', '| 序号 | 姓名 | 验收费(未税) | 含税小计 | 判定依据 |', '|---|---|---|---|---|']
        for s, v in invalids:
            fee = v['fee'] if isinstance(v['fee'], (int, float)) else 0
            lines.append(f"| {s} | {next((x['name'] for x in rows if x['seq']==int(s)), '')} | {fee:.0f} | {fee*1.06:.0f} | {v.get('reason')} |")
        lines += ['', f'**失效行中涉及验收费用的共 {len(invalid_fee_rows)} 行，未税合计 {fee_total:.0f} 元（含税 {tax_total:.0f} 元）**',
                  '按「链接失效规则」应置0、不作为验收费用结算（激励照常）；**是否置0由用户决定**，本工具只标注不改金额。', '']
    lines += ['## 逐条证据（失效行）', '']
    for s, v in invalids:
        lines.append(f"- 序号{s} {v['url']} → {v['display']}｜{v.get('reason')}｜标题: {str(v.get('title'))[:50]}｜lane={v.get('lane')}")
    report = os.path.join(outdir, f'链接复核报告-{args.date}.md')
    detail_path = os.path.join(outdir, f'链接复核明细-{args.date}.json')
    if args.dry_run:
        print('— dry-run，未写文件 —'); print('\n'.join(lines[:12])); return
    open(report, 'w').write('\n'.join(lines))
    json.dump(detail, open(detail_path, 'w'), ensure_ascii=False, indent=1)
    print('报告:', report); print('明细:', detail_path)

    # ---- 合并写 annotations ----
    ann = {}
    if os.path.exists(ann_path):
        try: ann = json.load(open(ann_path))
        except Exception: ann = {}
    for s, v in detail.items():
        ann.setdefault('link_status', {})[s] = v['display']
    if fans_fix:
        ann.setdefault('fans_fix', {}).update(fans_fix)
    sec_lines = [
        f'范围: 全表 {len(detail)} 行（未复核 {unrev_n} 行）；数据源 ~/.cache/yanshou/verify_db.jsonl（跨批次缓存）',
        '方法: 免登录无头渲染 → 有效=真实作品页(评论区/相关推荐+作品标题)；失效=通用落地页或跳转 /jingxuan 推荐流(部分带 web_video_404_link)',
        f'结果: ' + ' / '.join(f'{k}={v}' for k, v in summary.items()),
    ]
    if invalids:
        sec_lines.append('失效行: ' + '、'.join(s for s, _ in invalids))
        sec_lines.append(f'★ 其中涉及验收费用的 {len(invalid_fee_rows)} 行（未税合计 {fee_total:.0f} 元 / 含税 {tax_total:.0f} 元）——按规则应置0，是否执行由用户决定；与「涉及验收费用」列交叉即可筛出')
    sec_lines.append(f'证据: {os.path.relpath(report, po)}（含逐条失效证据）')
    secs = [x for x in ann.get('extra_sections', []) if not str(x[0]).startswith('八、')]
    secs.append(['八、发布链接有效性复核（' + args.date + '）', sec_lines])
    ann['extra_sections'] = secs
    for x in invalid_fee_rows:   # manual 只追加
        ann.setdefault('manual', {})[str(x['seq'])] = (
            f"发布链接失效({detail[str(x['seq'])]['reason']})，验收费{x['fee']:.0f}元(含税{x['fee']*1.06:.0f}元)"
            f"——按「链接失效规则」应置0不作为验收费用结算，是否执行由人工确认")
    json.dump(ann, open(ann_path, 'w'), ensure_ascii=False, indent=1)
    print('已合并写入:', ann_path, f'(link_status {len(ann["link_status"])} 条, fans_fix {len(ann.get("fans_fix", {}))} 条)')

if __name__ == '__main__':
    main()
