#!/usr/bin/env python3
"""达人粉丝数核验工具（DOM法主 + 截图OCR法辅，两法交叉）
用法:
  python3 fans_check.py <验收表.xlsx> [--out DIR] [--workers 3] [--limit N]
                           [--evidence-threshold 1000] [--evidence-dir DIR] [--report]

流程: 主页链接去重(按sec_uid) → 无头Chrome --dump-dom 解析 user-info-fans(精确值,页面不渲染也能取到)
      → 同时 --screenshot 截图 + Vision OCR(视觉存证/交叉验证) → 与表内值比对 → 输出 fans_check.json
特点: 可断点续跑(已成功的不重跑); <evidence-threshold 的达人截图自动复制到 evidence-dir 存证
经验: 3并发约6分钟/80位; DOM与OCR在重合样本上应0不一致;
      表内粉丝数常为旧值, 实测<阈值须改判审核1, 并在留痕/人工确认项写明原值→实测值
"""
import sys
import argparse, os, re, json, shutil, subprocess, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import openpyxl

CHROME = os.environ.get('CHROME_BIN', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
OCR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ocr.swift')
_tl = threading.local()

def wid():
    if not hasattr(_tl, 'w'):
        _tl.w = abs(hash(threading.current_thread().name)) % 997
    return _tl.w

def parse_dom(html):
    d = {}
    for key, label, e2e in (('fans_raw','粉丝','user-info-fans'), ('follow_raw','关注','user-info-follow'),
                            ('like_raw','获赞','user-info-like')):
        m = re.search(e2e + r'"[^>]*>\s*<div[^>]*>' + label + r'</div>\s*<div[^>]*>([^<]+)</div>', html)
        if m: d[key] = m.group(1).strip()
    m = re.search(r'data-e2e="user-name"[^>]*>([^<]+)<', html) or re.search(r'<h1[^>]*>([^<]{1,40})</h1>', html)
    if m: d['nick_now'] = m.group(1).strip()
    return d

def to_num(raw):
    if not raw: return None
    m = re.match(r'^([\d,\.]+)\s*(万)?', raw)
    if not m: return None
    return int(float(m.group(1).replace(',', '')) * (10000 if m.group(2) else 1))

def ocr_fans(png):
    p = subprocess.run(['swift', OCR, png], capture_output=True, text=True, timeout=180)
    joined = '|'.join(l for l in p.stdout.split('\n') if l.strip())
    m = re.search(r'粉丝\s*([\d,]+(?:\.\d+)?)\s*(万)?', joined)
    if not m: return None, None
    val = int(float(m.group(1).replace(',', '')) * (10000 if m.group(2) else 1))
    return m.group(0), val

def _maybe_recycle(prof):
    """每 worker 每 40 条回收一次 profile（甩掉累积 storage）；**不再每条重建**——
    每条重建会让 Chrome 反复付首次初始化开销(约60-90s/条)，是此前的性能瓶颈"""
    n = getattr(_tl, 'n', 0) + 1
    _tl.n = n
    if n % 40 == 0:
        shutil.rmtree(prof, ignore_errors=True)

def render(url, png, prof, budget):
    if os.path.exists(png): os.remove(png)
    _maybe_recycle(prof)
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                    '--window-size=900,1600', f'--user-data-dir={prof}', f'--virtual-time-budget={budget}',
                    f'--screenshot={png}', url], capture_output=True, timeout=budget/1000 + 60)

def dump_dom(url, prof, budget):
    _maybe_recycle(prof)
    p = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox',
                        f'--user-data-dir={prof}', f'--virtual-time-budget={budget}', '--dump-dom', url],
                       capture_output=True, text=True, timeout=budget/1000 + 60)
    return p.stdout or ''

def worker(it, out, budget):
    sec = it['sec']
    dom_prof, shot_prof = os.path.join(out, f'dompr_w{wid()}'), os.path.join(out, f'shotpr_w{wid()}')
    png = os.path.join(out, f'{sec[:24]}.png')
    url = f'https://www.douyin.com/user/{sec}'
    r = dict(it, ok=False)
    for att in range(3):
        try:
            d = parse_dom(dump_dom(url, dom_prof, budget))
            if d.get('fans_raw'):
                r.update(d, dom=to_num(d['fans_raw']), ok=True, attempt=att + 1)
                break
        except subprocess.TimeoutExpired:
            subprocess.run(['pkill', '-f', dom_prof], capture_output=True)
        except Exception:
            pass
        time.sleep(2 + att * 3)
    try:
        render(url, png, shot_prof, budget)
        if os.path.exists(png) and os.path.getsize(png) > 20000:
            raw, val = ocr_fans(png)
            r['ocr_raw'], r['ocr'] = raw, val
            r['png'] = png
    except Exception:
        pass
    if not r['ok'] and r.get('ocr') is not None:
        r['ok'] = True; r['fans'] = r['ocr']
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--out', default='/tmp/yanshou_fans')
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--limit', type=int, default=0, help='仅跑前N位(测试用)')
    ap.add_argument('--budget', type=int, default=15000, help='Chrome virtual-time-budget(ms)')
    ap.add_argument('--evidence-threshold', type=int, default=1000)
    ap.add_argument('--evidence-dir', default=None, help='实测<阈值达人的截图复制到该目录')
    ap.add_argument('--report', action='store_true', help='打印留痕格式的核验记录')
    args = ap.parse_args()

    out = args.out; os.makedirs(out, exist_ok=True)
    res_path = os.path.join(out, 'fans_check.json')
    ws = openpyxl.load_workbook(args.xlsx, data_only=True)['验收链接']
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {h: i + 1 for i, h in enumerate(hdr) if h}
    c_link, c_seq, c_name, c_nick, c_fans = ci.get('主页链接'), ci.get('序号'), ci.get('姓名'), ci.get('昵称'), ci.get('粉丝数量')
    home = defaultdict(list)
    for r in range(2, ws.max_row + 1):
        link = str(ws.cell(r, c_link).value or '').strip()
        if link: home[link].append((ws.cell(r, c_seq).value, ws.cell(r, c_name).value, ws.cell(r, c_nick).value, ws.cell(r, c_fans).value))
    items = []
    for link, rows in home.items():
        m = re.search(r'sec_uid=([\w\-]+)', link)
        if m: items.append(dict(sec=m.group(1), seqs=[x[0] for x in rows], name=rows[0][1], nick=rows[0][2], table_fans=rows[0][3]))
    items.sort(key=lambda x: x['seqs'][0])
    if args.limit: items = items[:args.limit]
    done = {}
    if os.path.exists(res_path):
        try: done = json.load(open(res_path))
        except Exception: done = {}
    todo = [it for it in items if it['sec'] not in done or not done[it['sec']].get('ok')]
    print(f'达人 {len(items)} | 已完成 {sum(1 for v in done.values() if v.get("ok"))} | 待跑 {len(todo)}', flush=True)
    results = dict(done); t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, it, out, args.budget): it for it in todo}
        for k, f in enumerate(as_completed(futs), 1):
            it = futs[f]
            try: r = f.result()
            except Exception as e: r = dict(it, ok=False, reason=f'fatal:{type(e).__name__}')
            results[r['sec']] = r
            json.dump(results, open(res_path, 'w'), ensure_ascii=False, indent=1)
            print(f"[{k}/{len(todo)}] 序号{r['seqs'][0]} {r['name']}: DOM={r.get('fans_raw')} OCR={r.get('ocr_raw')} ok={r['ok']} ({time.time()-t0:.0f}s)", flush=True)

    ok = [r for r in results.values() if r.get('ok')]
    both = [r for r in ok if r.get('dom') is not None and r.get('ocr') is not None]
    conflict = [r for r in both if r['dom'] != r['ocr']]
    low = [r for r in ok if (r.get('dom') or r.get('ocr') or 10**9) < args.evidence_threshold]
    print(f"\n== 汇总: 成功 {len(ok)}/{len(items)} | 两法重合 {len(both)} 不一致 {len(conflict)} | 实测<{args.evidence_threshold}: {len(low)}")
    for r in sorted(low, key=lambda x: x['seqs'][0]):
        print(f"   ⚠ 序号{r['seqs'][0]}-{r['seqs'][-1]} {r['name']}/{r['nick']}: 表内{r['table_fans']} → 实测{r.get('fans_raw') or r.get('ocr_raw')}")
    for r in sorted(ok, key=lambda x: x['seqs'][0]):
        if r['table_fans'] is None or r.get('dom') is None: continue
        t, f = r['table_fans'], r['dom']
        if (f < 10000 and abs(f - t) > 2) or (f >= 10000 and not (f * 0.95 <= t <= f * 1.05)):
            pass  # 明细在 --report
    if args.evidence_dir:
        os.makedirs(args.evidence_dir, exist_ok=True)
        for r in low:
            png = r.get('png') or os.path.join(out, f"{r['sec'][:24]}.png")
            if os.path.exists(png):
                dst = os.path.join(args.evidence_dir, f"序号{r['seqs'][0]}-{r['name']}-{r['nick']}-粉丝{r.get('dom') or r.get('ocr')}.png")
                shutil.copy2(png, dst); print('存证:', dst)
    if args.report:
        print('\n== 留痕格式记录 ==')
        recs = sorted(ok, key=lambda x: x['seqs'][0])
        pairs = []
        for r in recs:
            mark = ' ⚠<1000' if (r.get('dom') or r.get('ocr') or 10**9) < args.evidence_threshold else ''
            pairs.append(f"序号{r['seqs'][0]}-{r['seqs'][-1]} {r['name']}: {r['table_fans']}→{r.get('fans_raw') or r.get('ocr_raw')}{mark}")
        for i in range(0, len(pairs), 2):
            print(' | '.join(pairs[i:i+2]))
    print(f'\n结果JSON: {res_path}')

if __name__ == '__main__':
    main()
