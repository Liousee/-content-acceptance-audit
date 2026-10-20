#!/usr/bin/env python3
"""链接有效性 + 作者粉丝数 统一核验器（v2）

用法:
  python3 link_verify.py <表.xlsx> [--lane anon|login] [--allow-login]
        [--min-fee 1] [--seqs 42,44,101-110] [--all-rows]
        [--db PATH] [--max-age S] [--refresh] [--dry-run]
        [--workers 2] [--out JSON] [--limit N]

两条 lane（身份隔离）:
  anon  (默认) 免登录无头 --dump-dom：零账号风险；一次加载同时得到「链接有效性 + 作者粉丝数」（作品页作者卡三连）
  login        登录态有头真实窗口 + CDP轮询收敛：最准（失效=跳转 /jingxuan），需 --allow-login；永不 headless

安全: 全局限流(令牌桶+窗口预算+冷却) + 熔断(anon captcha≥2 / login captcha≥1 全停) + 运行锁（同时只允许一个 run）
结果: ~/.cache/yanshou/verify_db.jsonl（跨批次缓存；VALID 7天 / INVALID 30天 / 其余不缓存）
"""
import sys
import argparse, json, os, re, shutil, subprocess, threading, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import openpyxl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_common import (VALID, INVALID, EARLY, PENDING, CAPTCHA, ERR, SKIPPED, DISPLAY,
                           DB, Limiter, RunLock, LANES, norm_url, resolve_short,
                           extract_author_card, html_to_text)

CHROME = os.environ.get('CHROME_BIN', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
LOGIN_PROFILE = os.path.expanduser('~/.cache/yanshou-chrome')
PORT = 9411
BAD_KW = ('已删除', '不存在', '审核中', '违规', '私密', '无法查看', '已注销', '不见了', '当前内容无法播放')
RICH_KW = ('全部评论', '点击加载更多', '相关推荐', '条评论', '发布时间')
GEN_TITLE = ('抖音精选电脑版', '在抖音记录美好生活')   # 通用落地页标题模板（失效作品会落到这类页面）
_tl = threading.local()

def _wid():
    if not hasattr(_tl, 'w'):
        _tl.w = abs(hash(threading.current_thread().name)) % 997
    return _tl.w

def real_title(t):
    if not t: return False
    t = t.strip()
    if t.startswith('http') or t.startswith('douyin.com'): return False
    if t in ('抖音', '抖音-记录美好生活') or any(g in t for g in GEN_TITLE): return False
    return len(t) > 6

# ---------------- anon lane: 免登录无头 --dump-dom ----------------
def anon_probe(url, budget=10000):
    prof = getattr(_tl, 'prof', None)
    if not prof:
        prof = f'/tmp/yanshou-anon-w{_wid()}'
        shutil.rmtree(prof, ignore_errors=True)
        _tl.prof = prof
    html = ''
    for att in range(2):
        try:
            p = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                                '--no-default-browser-check', '--mute-audio', '--blink-settings=imagesEnabled=false',
                                f'--user-data-dir={prof}', f'--virtual-time-budget={budget}',
                                '--dump-dom', url], capture_output=True, text=True, timeout=100)
            html = p.stdout or ''
            if len(html) > 5000: break
        except subprocess.TimeoutExpired:
            subprocess.run(['pkill', '-f', prof], capture_output=True)
            time.sleep(2)
        if att == 0: budget = 25000   # 二级预算（EARLY 重试）
    if len(html) <= 5000:
        return dict(status=ERR, reason='timeout/空页面')
    # 验证码页判定：必须用「验证码中间页」标题（正文里的 'captcha' 子串在正常页JS中大量存在，误报源）
    if '验证码中间页' in html or ('验证码' in html[:3000] and len(html) < 200000):
        return dict(status=CAPTCHA, reason='验证码中间页')
    text = html_to_text(html)
    m = re.search(r'<title[^>]*>([^<]{0,120})</title>', html)
    title = m.group(1).strip() if m else ''
    rich = [k for k in RICH_KW if k in text]
    bad = [k for k in BAD_KW if k in text]
    card = extract_author_card(text)
    asec = re.search(r'/user/(MS4wLjABAAAA[\w\-]{10,})', html)
    if card is not None and asec:
        card['author_sec'] = asec.group(1)
    if rich:
        conf = 'strong' if real_title(title) else 'weak'
        return dict(status=VALID, reason='、'.join(rich[:2]), title=title, card=card, confidence=conf)
    if bad:
        return dict(status=INVALID, reason='、'.join(bad), title=title, markers=bad)
    if any(g in title for g in GEN_TITLE):
        return dict(status=INVALID, reason='通用落地页(无内容特征)', title=title)
    if not real_title(title):
        return dict(status=EARLY, reason='页面未加载完(标题=URL)', title=title)
    return dict(status=PENDING, reason='有标题但无内容特征', title=title)

# ---------------- login lane: 有头 + CDP 轮询收敛 ----------------
def _http(path, method='GET', timeout=15):
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))

def ensure_login_chrome(profile):
    try:
        _http('/json/version'); return None
    except Exception:
        pass
    p = subprocess.Popen([CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={PORT}',
                          '--no-first-run', '--no-default-browser-check', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(1)
        try:
            _http('/json/version'); return p
        except Exception:
            continue
    raise RuntimeError('登录态 Chrome 启动失败')

def login_verify(url, cap=45, poll=1.5):
    """完成即走：每 poll 秒读一次 /json/list，收敛立即返回"""
    vid = (re.search(r'/video/(\d+)', url) or [None, None])[1]
    try:
        tab = _http(f'/json/new?{urllib.parse.quote(url, safe="")}', method='PUT')
    except Exception as e:
        return dict(status=ERR, reason=f'open tab: {e}')
    tid = tab.get('id'); t0 = time.time(); last = {}
    try:
        while time.time() - t0 < cap:
            time.sleep(poll)
            try:
                tabs = _http('/json/list')
            except Exception:
                continue
            t = next((x for x in tabs if x.get('id') == tid), None)
            if not t:
                return dict(status=ERR, reason='tab closed')
            final, title = t.get('url', ''), (t.get('title') or '')
            last = dict(final=final, title=title)
            if '验证码' in title or 'captcha' in final:
                return dict(status=CAPTCHA, reason='验证码中间页', **last)
            if 'jingxuan' in final or 'web_video_404' in final:
                return dict(status=INVALID, reason='跳转推荐流/404(作品不可见)', **last, ms=int((time.time()-t0)*1000))
            if vid and f'/video/{vid}' in final and real_title(title):
                return dict(status=VALID, reason=f'作品页-{title[:24]}', **last, confidence='strong',
                            ms=int((time.time()-t0)*1000))
        if last.get('final', '').find('/video/') >= 0:
            return dict(status=EARLY, reason='停留作品页但标题未加载', **last, ms=int((time.time()-t0)*1000))
        return dict(status=PENDING, reason=f'未收敛: {str(last.get("final"))[:50]}', **last)
    finally:
        try: _http(f'/json/close/{tid}')
        except Exception: pass

# ---------------- anon lane (fast): 常驻无头 Chrome + CDP 轮询 ----------------
ANON_PORT = 9412

def _http2(path, method='GET', timeout=15):
    req = urllib.request.Request(f'http://127.0.0.1:{ANON_PORT}{path}', method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))

def ensure_anon_chrome(profile=None):
    """常驻无头 Chrome（复用进程：消除每条 item 的 Chrome 启动开销，实测 33s/条 → 目标 <12s/条）"""
    try:
        _http2('/json/version'); return None
    except Exception:
        pass
    prof = profile or f'/tmp/yanshou-anon-cdp-{os.getpid()}'
    shutil.rmtree(prof, ignore_errors=True)
    p = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                          '--no-default-browser-check', '--mute-audio', '--hide-scrollbars',
                          '--blink-settings=imagesEnabled=false',
                          f'--user-data-dir={prof}', f'--remote-debugging-port={ANON_PORT}', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(1)
        try:
            _http2('/json/version'); return p
        except Exception:
            continue
    raise RuntimeError('无头 Chrome 启动失败')

def cdp_probe_anon(url, cap=40, poll=1.2):
    """免登录 lane 的收敛即走判定（仅凭 URL+title）：
    失效=跳转 /jingxuan|web_video_404 或 标题=通用落地页(抖音精选电脑版)；有效=停留作品页+真实标题"""
    vid = (re.search(r'/video/(\d+)', url) or [None, None])[1]
    try:
        tab = _http2(f'/json/new?{urllib.parse.quote(url, safe="")}', method='PUT')
    except Exception as e:
        return dict(status=ERR, reason=f'open tab: {e}')
    tid = tab.get('id'); t0 = time.time(); last = {}
    try:
        while time.time() - t0 < cap:
            time.sleep(poll)
            try:
                tabs = _http2('/json/list')
            except Exception:
                continue
            t = next((x for x in tabs if x.get('id') == tid), None)
            if not t:
                return dict(status=ERR, reason='tab closed')
            final, title = t.get('url', ''), (t.get('title') or '')
            last = dict(final=final, title=title)
            if '验证码' in title:
                return dict(status=CAPTCHA, reason='验证码中间页', **last)
            if 'jingxuan' in final or 'web_video_404' in final:
                return dict(status=INVALID, reason='跳转推荐流/404', **last, ms=int((time.time()-t0)*1000))
            if any(g in title for g in GEN_TITLE):
                return dict(status=INVALID, reason='通用落地页(无内容特征)', **last, ms=int((time.time()-t0)*1000))
            if vid and f'/video/{vid}' in final and real_title(title):
                return dict(status=VALID, reason=f'作品页-{title[:24]}', **last, confidence='strong',
                            ms=int((time.time()-t0)*1000))
        if 'jingxuan' in last.get('final', ''):
            return dict(status=INVALID, reason='跳转推荐流(超时兜底)', **last)
        return dict(status=EARLY, reason='未收敛(标题未加载)', **last, ms=int((time.time()-t0)*1000))
    finally:
        try: _http2(f'/json/close/{tid}')
        except Exception: pass

# ---------------- 计划与执行 ----------------
def collect_rows(xlsx, seqs=None, min_fee=None, all_rows=False, col='发布链接'):
    ws = openpyxl.load_workbook(xlsx, data_only=True)['验收链接']
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {h: i + 1 for i, h in enumerate(hdr) if h}
    rows = []
    for r in range(2, ws.max_row + 1):
        seq = ws.cell(r, ci['序号']).value
        if seq is None: continue
        if seqs and seq not in seqs: continue
        fee = ws.cell(r, ci.get('验收费用（未税）')).value if ci.get('验收费用（未税）') else None
        if min_fee is not None and not (isinstance(fee, (int, float)) and fee >= min_fee): continue
        link = str(ws.cell(r, ci[col]).value or '').strip()
        rows.append(dict(seq=seq, link=link, fee=fee, name=ws.cell(r, ci['姓名']).value,
                         home=str(ws.cell(r, ci.get('主页链接')).value or '').strip() if ci.get('主页链接') else ''))
    return rows

def cleanup_stale_chrome():
    """清理本项目残留的无头 Chrome（中止/超时留下会抢占资源、拖慢甚至卡死新 run）"""
    n = 0
    try:
        out = subprocess.run(['ps', '-eo', 'pid,command'], capture_output=True, text=True).stdout
    except Exception:
        return 0
    for line in out.splitlines():
        if ('yanshou-anon-w' in line or 'yanshou-plainprof' in line) and 'Chrome' in line:
            m = re.match(r'\s*(\d+)', line)
            if m:
                try:
                    os.kill(int(m.group(1)), 9); n += 1
                except Exception:
                    pass
    if n:
        time.sleep(1)
    return n

def parse_seqs(s):
    if not s: return None
    out = set()
    for part in s.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-'); out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--lane', default='anon', choices=['anon', 'login'])
    ap.add_argument('--allow-login', action='store_true')
    ap.add_argument('--min-fee', type=float, default=None)
    ap.add_argument('--seqs', default=None)
    ap.add_argument('--all-rows', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=0, help='默认取 lane 配置')
    ap.add_argument('--db', default=None)
    ap.add_argument('--max-age', type=float, default=None, help='覆盖缓存TTL(秒)')
    ap.add_argument('--refresh', action='store_true', help='忽略缓存全部重查')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--out', default=None)
    ap.add_argument('--engine', default=None, choices=['cdp', 'dump'],
                    help='anon lane 引擎: cdp=常驻Chrome+轮询(快,默认) / dump=每条起进程(遗留)')
    args = ap.parse_args()

    if args.lane == 'login' and not args.allow_login:
        print('❌ 登录态 lane 需显式 --allow-login（会使用你的账号；红线见 SKILL.md「风控与账号安全红线」）')
        sys.exit(1)

    db = DB(path=args.db) if args.db else DB()
    lim = Limiter(args.lane)
    tabs, spacing = LANES[args.lane][0], LANES[args.lane][1]
    workers = args.workers or tabs

    stale = cleanup_stale_chrome()
    if stale: print(f'已清理残留无头Chrome进程 {stale} 个', flush=True)
    rows = collect_rows(args.xlsx, parse_seqs(args.seqs), args.min_fee, args.all_rows)
    if args.limit: rows = rows[:args.limit]
    # 归一化 + 去重
    jobs = {}          # key -> {url, seqs[], kind}
    unsupported = []
    for r in rows:
        kind, key, canon = norm_url(r['link'])
        if kind == 'unsupported':
            unsupported.append(r['seq']); continue
        if kind == 'short':
            vid = resolve_short(r['link'], db=db)
            if vid:
                kind, key, canon = 'video', f'v:{vid}', f'https://www.douyin.com/video/{vid}'
            else:
                unsupported.append(r['seq']); continue
        j = jobs.setdefault(key, dict(url=canon, seqs=[], kind=kind))
        j['seqs'].append(r['seq'])
    # 缓存决策
    todo, hits = [], 0
    for key, j in jobs.items():
        rec = None if args.refresh else db.get(key, max_age=args.max_age)
        if rec and rec.get('status') in (VALID, INVALID, SKIPPED):
            j['status'] = rec['status']; j['reason'] = rec.get('reason', '缓存'); j['cached'] = True
            hits += 1
        else:
            todo.append(key)
    print(f'行 {len(rows)} | 唯一视频 {len(jobs)} | 缓存命中 {hits} | 需请求 {len(todo)} | '
          f'非抖音平台 {len(unsupported)} | lane={args.lane} workers={workers} 间隔={spacing}s±', flush=True)
    if args.dry_run:
        print('— dry-run 结束（未发请求）—'); return
    for s in unsupported:
        db.put(f'v:unsupported:{s}', SKIPPED, reason='非抖音平台(视频号等)')
        jobs[f'v:unsupported:{s}'] = dict(status=SKIPPED, reason='非抖音平台', seqs=[s], cached=False)

    if todo:
        rem = lim.remaining_cooldown()
        if rem > 0:
            print(f'⛔ 全局冷却中，剩余 {int(rem/60)} 分钟 —— 本次不执行（可稍后重跑，缓存已保留）'); return
        if args.lane == 'login':
            proc = ensure_login_chrome(LOGIN_PROFILE)
            print(f'登录态 Chrome 就绪(port {PORT})；请勿关闭弹出的窗口')
        else:
            proc = None
        lock = RunLock()
        with lock:
            captcha_n = 0; t0 = time.time(); done = 0
            stop = threading.Event()
            use_cdp = (args.lane == 'anon' and (args.engine or 'cdp') == 'cdp')
            if use_cdp:
                proc2 = ensure_anon_chrome()
                print(f'常驻无头 Chrome 就绪(port {ANON_PORT}) —— 轮询收敛模式', flush=True)
            def work(key):
                if stop.is_set(): return key, None
                lim.wait()
                j = jobs[key]
                if args.lane == 'login':
                    res = login_verify(j['url'])
                elif use_cdp:
                    res = cdp_probe_anon(j['url'])
                else:
                    res = anon_probe(j['url'])
                return key, res
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(work, k): k for k in todo}
                for f in as_completed(futs):
                    key, res = f.result()
                    if res is None: continue
                    done += 1
                    j = jobs[key]
                    db.put(key, res['status'], reason=res.get('reason'), title=res.get('title'),
                           final_url=res.get('final'), lane=args.lane, card=res.get('card'),
                           confidence=res.get('confidence'), seqs=j['seqs'])
                    j.update(status=res['status'], reason=res.get('reason'))
                    if res.get('card'): j['card'] = res['card']
                    print(f"[{done}/{len(todo)}] 序号{j['seqs'][0]}{'+' if len(j['seqs'])>1 else ''} "
                          f"{res['status']} {str(res.get('reason'))[:40]} ({time.time()-t0:.0f}s)", flush=True)
                    if res['status'] == CAPTCHA:
                        captcha_n += 1
                        cd = lim.on_captcha(url=j['url'], seq=j['seqs'][0])
                        if captcha_n >= LANES[args.lane][6]:
                            print(f'!! 验证码 {captcha_n} 条 ≥ 熔断阈值 —— 立即停止(保护账号)，已冷却 {cd//60} 分钟', flush=True)
                            stop.set(); break
            db.save_snapshot()
            if args.lane == 'login' and proc:
                print('提示: 登录态 Chrome 窗口仍开着，可自行关闭')

    # 作者粉丝数归档: s:<sec_uid>（供 audit_pipeline --verify-db 覆盖 x['fans']）
    home_sec = {}
    for r in rows:
        m2 = re.search(r'sec_uid=([\w\-]+)', r.get('home') or '')
        if m2: home_sec[r['seq']] = m2.group(1)
    n_fans = n_mismatch = 0
    for key, j in jobs.items():
        card = j.get('card') or {}
        asec = card.get('author_sec'); fans = card.get('fans')
        if not (asec and fans): continue
        for seq in j.get('seqs', []):
            hs = home_sec.get(seq)
            ident = 'mismatch' if (hs and hs != asec) else 'ok'
            if ident == 'mismatch': n_mismatch += 1
            else: n_fans += 1
            db.put(f's:{asec}', VALID, fans=fans, fans_raw=card.get('fans_raw'),
                   rounded=card.get('fans_rounded'), source='video', src_video=key,
                   identity=ident, seqs=[seq])
    if n_fans or n_mismatch:
        print(f'作者粉丝数归档: {n_fans} 条(sec_uid与表内主页一致)' +
              (f' | 身份不匹配 {n_mismatch} 条(已标 identity=mismatch, 不应用)' if n_mismatch else ''), flush=True)
        db.save_snapshot()

    # 汇总
    res_by_seq = {}
    for key, j in jobs.items():
        for s in j.get('seqs', []):
            res_by_seq[s] = dict(key=key, url=j['url'], status=j.get('status', PENDING),
                                 reason=j.get('reason'), display=DISPLAY.get(j.get('status'), '未复核'),
                                 card=j.get('card'), cached=j.get('cached', False), lane=args.lane)
    print('\n== 汇总:', dict(Counter(v['status'] for v in res_by_seq.values())))
    print('   缓存命中:', sum(1 for v in res_by_seq.values() if v['cached']),
          '| 验证码事件(累计):', lim.captcha_count())
    for s, v in sorted(res_by_seq.items()):
        if v['status'] not in (VALID, SKIPPED):
            print(f"   序号{s}: {v['status']} {str(v.get('reason'))[:50]}")
    if args.out:
        json.dump({str(k): v for k, v in res_by_seq.items()}, open(args.out, 'w'), ensure_ascii=False, indent=1)
        print('明细:', args.out)

if __name__ == '__main__':
    main()
