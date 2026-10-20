#!/usr/bin/env python3
"""有头浏览器(登录态)链接复核——决定性判定: 作品可用=停留在 /video/<id> 并显示作品标题;
不可用=自动跳转到 /jingxuan 推荐流(或 generic 标题)。无需CDP库(用 /json/new|list|close HTTP接口)。
用法:
  # 前置: 用户已登录(scripts/login_douyin.sh), 且 Chrome 窗口已关闭
  python3 headful_verify.py <验收表.xlsx> [--profile ~/.cache/yanshou-chrome] [--seqs 42,44] [--min-fee 1]
                               [--out out.json] [--workers 2] [--wait 14] [--keep-open]
原理(实测): 登录态+无头 会被风控拦(验证码中间页); 登录态+有头(真实窗口) 正常且可判定。
说明: 会打开一个真实 Chrome 窗口(勿关闭); 脚本自行开标签/关标签。
安全: 登录态只做少量定点确认(建议 ≤5 条/批、短会话), 不要批量跑。
"""
import argparse, json, os, random, re, shutil, subprocess, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import openpyxl

CHROME = os.environ.get('CHROME_BIN', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
PORT = 9411

def http(path, method='GET'):
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))

def ensure_chrome(profile):
    try:
        http('/json/version'); return None
    except Exception:
        pass
    p = subprocess.Popen([CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={PORT}',
                          '--no-first-run', '--no-default-browser-check', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            http('/json/version'); return p
        except Exception:
            continue
    raise RuntimeError('Chrome 启动失败')

def verify_one(url, wait, delay=0, jitter=0):
    vid = (re.search(r'/video/(\d+)', url) or [None, None])[1]
    if delay:
        time.sleep(delay * (1 + random.uniform(-jitter, jitter)))
    try:
        t = http(f'/json/new?{urllib.parse.quote(url, safe="")}', method='PUT')
    except Exception as e:
        return dict(url=url, status='ERR', reason=f'open tab: {e}')
    tid = t.get('id')
    time.sleep(wait)
    try:
        tabs = http('/json/list')
    except Exception as e:
        return dict(url=url, status='ERR', reason=f'list: {e}')
    tab = next((x for x in tabs if x.get('id') == tid), None)
    if tid:
        try: http(f'/json/close/{tid}')
        except Exception: pass
    if not tab:
        return dict(url=url, status='ERR', reason='tag closed')
    final, title = tab.get('url', ''), (tab.get('title') or '')
    if '验证码' in title or 'captcha' in final:
        return dict(url=url, status='CAPTCHA', reason=title[:40], title=title, final=final)
    if 'jingxuan' in final or 'web_video_404' in final:
        return dict(url=url, status='SUSPECT', reason='跳转推荐流/404(作品不可见)', title=title, final=final)
    if vid and f'/video/{vid}' in final:
        # 标题仍是URL/空 => 页面未加载完, 不可判定为OK
        if title and not title.startswith('douyin.com') and title not in ('', '抖音'):
            return dict(url=url, status='OK', reason=f'作品页-{title[:24]}', title=title, final=final)
        return dict(url=url, status='EARLY', reason='停留作品页但标题未加载(建议加长--wait重测)', title=title, final=final)
    return dict(url=url, status='SUSPECT', reason=f'异常最终页: {final[:60]}', title=title, final=final)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--profile', default=os.path.expanduser('~/.cache/yanshou-chrome'))
    ap.add_argument('--col', default='发布链接')
    ap.add_argument('--seqs', default=None)
    ap.add_argument('--min-fee', type=float, default=None)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--wait', type=float, default=14, help='每个标签打开后等待秒数')
    ap.add_argument('--out', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--delay', type=float, default=12, help='每条之间的基础间隔秒(默认12)')
    ap.add_argument('--jitter', type=float, default=0.5, help='间隔随机抖动比例(默认±50%%, 更像人工)')
    ap.add_argument('--batch-size', type=int, default=5, help='每批条数, 批间停顿(默认5；登录态请勿超5)')
    ap.add_argument('--batch-pause', type=float, default=240, help='批间停顿秒数(默认240=4分钟)')
    ap.add_argument('--max-captcha', type=int, default=1, help='本批出现N条验证码即熔断停止(默认1；登录态建议1)')
    args = ap.parse_args()
    if not os.path.isdir(args.profile):
        print(f'❌ profile 不存在: {args.profile}'); sys.exit(1)
    ws = openpyxl.load_workbook(args.xlsx, data_only=True)['验收链接']
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {h: i + 1 for i, h in enumerate(hdr) if h}
    only = set(int(x) for x in args.seqs.split(',')) if args.seqs else None
    rows = []
    for r in range(2, ws.max_row + 1):
        seq = ws.cell(r, ci['序号']).value
        if seq is None: continue
        if only and seq not in only: continue
        if args.min_fee is not None:
            fee = ws.cell(r, ci.get('验收费用（未税）')).value if ci.get('验收费用（未税）') else None
            if not isinstance(fee, (int, float)) or fee < args.min_fee: continue
        link = str(ws.cell(r, ci[args.col]).value or '').strip()
        if link.startswith('http'): rows.append((seq, link))
    print(f'待复核 {len(rows)} 条 {args.col} (有头+登录态, profile={args.profile})', flush=True)
    if args.dry_run: return
    proc = ensure_chrome(args.profile)
    print(f'Chrome 已就绪 (debug port {PORT}); 请勿关闭弹出的窗口', flush=True)
    res = {}; t0 = time.time(); k = 0; aborted = False
    for bi in range(0, len(rows), args.batch_size):
        chunk = rows[bi:bi + args.batch_size]
        capt = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(verify_one, u, args.wait, args.delay, args.jitter): (s, u) for s, u in chunk}
            for f in as_completed(futs):
                s, u = futs[f]; k += 1
                try: r = f.result()
                except Exception as e: r = dict(url=u, status='ERR', reason=str(e))
                res[str(s)] = r
                if r['status'] == 'CAPTCHA': capt += 1
                print(f"[{k}/{len(rows)}] 序号{s} {r['status']} {r.get('reason','')[:44]} ({time.time()-t0:.0f}s)", flush=True)
        if args.out:   # 每批增量落盘(熔断/中断后部分结果仍可用)
            json.dump(res, open(args.out, 'w'), ensure_ascii=False, indent=1)
        if capt >= args.max_captcha:
            print(f'!! 本批验证码 {capt} 条 ≥ 熔断阈值 {args.max_captcha} —— 立即停止(保护账号), 已保存 {len(res)} 条结果', flush=True)
            aborted = True; break
        if bi + args.batch_size < len(rows):
            print(f'-- 批次停顿 {args.batch_pause:.0f}s (已完成 {len(res)}/{len(rows)}) --', flush=True)
            time.sleep(args.batch_pause)
    from collections import Counter
    print('\n== 汇总:', dict(Counter(r['status'] for r in res.values())))
    for s, r in sorted(res.items(), key=lambda kv: int(kv[0])):
        if r['status'] != 'OK':
            print(f"   序号{s}: {r['status']} {r.get('reason','')} | {str(r.get('title'))[:50]}")
    if args.out:
        json.dump(res, open(args.out, 'w'), ensure_ascii=False, indent=1); print('明细:', args.out)

if __name__ == '__main__':
    main()
