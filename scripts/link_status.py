#!/usr/bin/env python3
"""链接复核（无头 Chrome）：不传 --profile 即免登录模式；传 --profile 则使用登录态。
用法:
  # 免登录批量（推荐，零账号身份；安全速率见 docs/safety.md）:
  python3 link_status.py <验收表.xlsx> [--workers 2] [--delay 5] [--min-fee 1]
  # 登录态（可选；仅少量定点，建议 ≤5 条/批、短会话、人工在场）:
  bash scripts/login_douyin.sh
  python3 link_status.py <验收表.xlsx> --profile ~/.cache/yanshou-chrome --limit 5

判定: 渲染DOM/文本中出现
  异常信号: 已删除/不存在/审核中/违规/私密/该作品无法查看/账号已注销
  正常信号: 播放量|点赞量|作品数据|粉丝 (真实页面才会出现)
输出: 每行 OK / SUSPECT(异常文案) / UNKNOWN(无信号,需人工看截图)
"""
import argparse, json, os, re, subprocess, shutil, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import openpyxl

CHROME = os.environ.get('CHROME_BIN', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
BAD = ('已删除', '不存在', '审核中', '违规', '私密', '无法查看', '已注销', '不见了', '当前内容无法播放')
GOOD = ('播放量', '点赞量', '作品数据', '粉丝')
_tl = threading.local()
def wid():
    if not hasattr(_tl, 'w'): _tl.w = abs(hash(threading.current_thread().name)) % 997
    return _tl.w

_prof = threading.local()
def worker_profile(profile):
    """每个worker复制一份profile(登录态)复用, 避免每条URL复制+避免Chrome profile锁冲突"""
    if not getattr(_prof, 'dir', None):
        d = f'{profile}-w{wid()}'
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(profile, d, symlinks=True, ignore_dangling_symlinks=True)
        _prof.dir = d
    return _prof.dir

def probe(url, profile, delay=0):
    if profile and os.path.isdir(profile) and os.listdir(profile):
        try: prof = worker_profile(profile)
        except Exception as e: return dict(url=url, status='ERR', reason=f'profile copy: {e}')
    else:
        prof = getattr(_prof, 'plain', None)
        if not prof:
            prof = f'/tmp/yanshou-plainprof-w{wid()}'; shutil.rmtree(prof, ignore_errors=True); _prof.plain = prof
    if delay: time.sleep(delay)
    html = ''
    for att in range(2):   # 第1次常因profile首次初始化超时, 第2次即快
        try:
            p = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox',
                                '--no-first-run', '--no-default-browser-check', '--mute-audio',
                                '--blink-settings=imagesEnabled=false',
                                f'--user-data-dir={prof}', '--virtual-time-budget=10000', '--dump-dom', url],
                               capture_output=True, text=True, timeout=100)
            html = p.stdout or ''
            if len(html) > 5000: break
        except subprocess.TimeoutExpired:
            subprocess.run(['pkill', '-f', prof], capture_output=True)
            time.sleep(2)
    if not html or len(html) <= 5000:
        return dict(url=url, status='ERR', reason='timeout/空页面')
    txt = re.sub(r'<[^>]+>', ' ', html)
    txt = re.sub(r'\s+', ' ', txt)
    bad = [k for k in BAD if k in txt]
    good = [k for k in GOOD if k in txt]
    m = re.search(r'<title[^>]*>([^<]{0,90})</title>', html)
    title = m.group(1).strip() if m else None
    if '验证码中间页' in html or ('验证码' in html[:3000] and len(html) < 200000):
        return dict(url=url, status='CAPTCHA', reason='触发验证码中间页(需降低频率/人工)', title=title)
    # 真实作品页特征(实测): 评论区/相关推荐/作者行 会出现; 不存在的作品只有登录墙+通用标题
    rich = [k for k in ('全部评论', '点击加载更多', '相关推荐', '条评论', '发布时间') if k in txt]
    if rich:
        return dict(url=url, status='OK', reason='、'.join(rich[:2]), title=title)
    if bad:
        return dict(url=url, status='SUSPECT', reason='、'.join(bad), title=title, snippet=txt[:150])
    if title and '抖音精选' not in title:
        return dict(url=url, status='OK', reason=f'标题={title[:30]}', title=title)
    return dict(url=url, status='SUSPECT', reason='无内容特征(疑似已删除/不可见/需登录)', title=title)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--profile', default=None, help='已登录的Chrome profile目录; 不传=免登录模式(可核验公开作品可用性)')
    ap.add_argument('--col', default='发布链接')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--out', default=None)
    ap.add_argument('--min-fee', type=float, default=None, help='仅复核 验收费用(未税)>=该值 的行(如 1 = 只查有验收费的行)')
    ap.add_argument('--seqs', default=None, help='仅复核指定序号, 逗号分隔, 如 42,44,45,46')
    ap.add_argument('--dry-run', action='store_true', help='只打印将复核的行, 不实际跑浏览器')
    ap.add_argument('--delay', type=float, default=5, help='每条之间的间隔秒数(默认5，避免风控)')
    args = ap.parse_args()
    args.profile = args.profile or None
    if args.profile and not os.path.isdir(args.profile):
        print(f'❌ profile 不存在: {args.profile} —— 请先按用法①登录'); sys.exit(1)
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
    if args.dry_run:
        print(f'将复核 {len(rows)} 条: {[s for s,_ in rows][:50]}{" ..." if len(rows)>50 else ""}')
        return
    if args.limit: rows = rows[:args.limit]
    print(f'待复核 {len(rows)} 条 {args.col} (profile={args.profile})', flush=True)
    res = {}; t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, u, args.profile, args.delay): (s, u) for s, u in rows}
        for k, f in enumerate(as_completed(futs), 1):
            s, u = futs[f]
            try: r = f.result()
            except Exception as e: r = dict(url=u, status='ERR', reason=str(e))
            res[str(s)] = r
            print(f"[{k}/{len(rows)}] 序号{s} {r['status']} {r.get('reason','')[:40]} ({time.time()-t0:.0f}s)", flush=True)
    from collections import Counter
    print('\n== 汇总:', dict(Counter(r['status'] for r in res.values())))
    for s, r in sorted(res.items(), key=lambda kv: int(kv[0])):
        if r['status'] != 'OK': print(f"   序号{s}: {r['status']} {r.get('reason','')}")
    if args.out:
        json.dump(res, open(args.out, 'w'), ensure_ascii=False, indent=1); print('明细:', args.out)

if __name__ == '__main__':
    main()
