#!/usr/bin/env python3
"""人工链接复核台（有头·登录态）——人工目视，建议每批 ≤5 条、批间 ≥8 秒

与 headful_verify.py 的区别:
  headful_verify = 全自动, 靠 wait=14s 串行等待 + 2 worker + 批间停 240s 保守限速 → 约 1-2 条/分
  本工具        = 人工参与: 一次并行打开 N 个标签(默认5), 人翻标签扫视(Cmd+1..9,0),
                  自动判定(title/url)作辅助, 人回车认可或报告失效序号; 下一批在后台预载 → 无等待
  真人节奏风控风险最低，但仍应低频；不要用它做无人值守的批量自动化。

前置: bash scripts/login_douyin.sh   (一次性扫码登录, 登录态长期复用)
用法: python3 link_console.py <表.xlsx> [--batch 5] [--settle 12] [--min-fee 1] [--all-rows]
                                 [--seqs 1-50,60] [--sheet 验收链接] [--col-link 发布链接]
                                 [--out 链接复核/link_verify_manual.json] [--accept-suspect]

人工输入(每批一次):
  回车           = 认可自动判定(标签页里目视无误)
  7,12          = 把标签7、12 改判为「失效」
  7=ok          = 把标签7 改判为「有效」
  -             = 整批跳过(不写结论)
  q             = 退出并保存
"""
import sys, os, re, json, time, subprocess, urllib.request, urllib.parse, argparse, concurrent.futures
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 允许从任意目录启动
import openpyxl
from cdp_ws import probe_page, WS

CHROME = os.environ.get('CHROME_BIN', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
DEF_PROFILE = os.path.expanduser('~/.cache/yanshou-chrome')


def http(path, method='GET', port=9411, timeout=15):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def _profile_in_use(profile):
    """已有同 profile 的 Chrome 但没开调试端口 -> 再启动也不会监听端口(经典坑), 需先关闭"""
    try:
        out = subprocess.run(['ps', '-eo', 'pid,command'], capture_output=True, text=True).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        if profile in line and 'Google Chrome' in line and 'crashpad' not in line and '--type=' not in line:
            m = re.match(r'\s*(\d+)', line)
            if m:
                pids.append(int(m.group(1)))
    return pids


def ensure_chrome(profile, port, restart=False):
    try:
        http('/json/version', port=port)
        return None
    except Exception:
        pass
    pids = _profile_in_use(profile)
    if pids:
        if not restart:
            print(f'❌ 检测到该登录 profile 的 Chrome 正在运行(pid {pids})但未开启调试端口 {port}。')
            print('   请先关闭该 Chrome 窗口后重跑，或加 --restart 由本工具重启它。')
            sys.exit(1)
        for pid in pids:
            subprocess.run(['kill', str(pid)], capture_output=True)
        time.sleep(2)
        print(f'已关闭旧 Chrome (pid {pids})')
    if not os.path.isdir(profile):
        print(f'❌ 登录 profile 不存在: {profile}\n   先执行: bash {os.path.dirname(os.path.abspath(__file__))}/login_douyin.sh')
        sys.exit(1)
    p = subprocess.Popen([CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={port}',
                          '--no-first-run', '--no-default-browser-check', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            http('/json/version', port=port)
            return p
        except Exception:
            continue
    raise RuntimeError('Chrome 启动失败')


def check_login(port):
    """CDP Network.getAllCookies 权威判定登录态(比页面文案可靠)"""
    tabs = list_tabs(port) or [t for t in _raw_tabs(port) if t.get('type') == 'page']
    if not tabs:
        return None, '无可用标签'
    try:
        w = WS(tabs[0]['webSocketDebuggerUrl'], timeout=15)
        w.send_text(json.dumps({'id': 7, 'method': 'Network.getAllCookies'}))
        ck = None
        for _ in range(40):
            m = json.loads(w.recv_text())
            if m.get('id') == 7:
                ck = m.get('result', {}).get('cookies', [])
                break
        w.close()
    except Exception as e:
        return None, f'cookie 读取失败 {type(e).__name__}'
    names = {c['name'] for c in (ck or []) if 'douyin' in c.get('domain', '')}
    ok = {'sessionid', 'sid_tt'} <= names
    return ok, ('登录态有效' if ok else '缺少 sessionid/sid_tt —— 登录态已失效，请重跑 login_douyin.sh 扫码')


def _raw_tabs(port):
    try:
        return http('/json/list', port=port)
    except Exception:
        return []


def activate_chrome():
    subprocess.run(['osascript', '-e', 'tell application "Google Chrome" to activate'],
                   capture_output=True)


def list_tabs(port):
    try:
        tabs = http('/json/list', port=port)
    except Exception:
        return []
    return [t for t in tabs if t.get('type') == 'page']


def open_tab(url, port):
    try:
        return http(f'/json/new?{urllib.parse.quote(url, safe="")}', method='PUT', port=port)
    except Exception as e:
        print(f'   ! 开标签失败 {url[:50]}: {e}')
        return None


def close_tab(tid, port):
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/json/close/{tid}', timeout=10).read()
    except Exception:
        pass


def verdict(vid, final, title, link=None):
    """vid 参数仅为兼容保留; 判定以**最终URL**为准(短链里没有视频ID)"""
    if '验证码' in title or 'captcha' in final or '验证码' in final:
        return 'CAPTCHA', '出现验证码/风控页（建议暂停数分钟）'
    if not final or 'v.douyin.com' in final or (link and final.rstrip('/') == link.rstrip('/')):
        return 'EARLY', '短链尚未跳转(加载中)'
    if 'jingxuan' in final or 'web_video_404' in final:
        return 'SUSPECT', '跳转推荐流/404 —— 作品不可见'
    if re.search(r'/video/\d+', final):
        t = (title or '').strip()
        # 移动分享壳页: 标题就是 URL 本身(iesdouyin.com/share/video/xxx) —— 页面没渲染, 不能当有效
        if re.search(r'iesdouyin\.com|/share/video/|^https?://', t):
            return 'BLOCKED', '移动分享壳页/未渲染(疑似风控)'
        if t and not t.startswith('douyin.com') and t not in ('', '抖音'):
            return 'OK', f'作品页·{t[:26]}'
        return 'EARLY', '作品页已打开但标题未渲染(再等一会)'
    if 'passport' in final or 'login' in final:
        return 'CAPTCHA', '被要求登录(登录态可能已失效, 重跑 login_douyin.sh)'
    return 'SUSPECT', f'异常最终页 {final[:50]}'


BAD_TEXT = ('该作品不存在', '作品不存在', '内容不存在', '作品已删除', '内容已删除',
            '视频不存在', '已下架', '无法查看', '页面不存在')


SHELL_MARK = ('Please wait', 'please wait', '视频数据加载中', 'ratelimit triggered', '验证码中间页')


def text_verdict(txt):
    """从页面正文判定(登录态下最可靠)"""
    if not txt:
        return None
    if '验证码' in txt[:200] or '验证码中间页' in txt:
        return 'CAPTCHA', '出现验证码/风控页'
    if any(k in txt for k in SHELL_MARK) and len(txt) < 250:
        mk = next(k for k in SHELL_MARK if k in txt)
        return 'BLOCKED', f'壳页/风控(「{mk}」)'
    for kw in BAD_TEXT:
        if kw in txt:
            return 'SUSPECT', f'页面提示「{kw}」'
    if any(k in txt for k in ('全部评论', '留下你的精彩评论', '条评论', '发布时间：', '发布时间:')):
        return 'OK', '作品页(含互动/评论/发布时间)'
    return None


def probe_tab(tab, link, tries=2, timeout=20, allow_weak=False):
    """读取页面正文 -> (status, reason, excerpt, readyState)"""
    ws = tab.get('webSocketDebuggerUrl')
    if not ws:
        return None, None, None, None, False
    info = None
    for k in range(tries):
        info = probe_page(ws, timeout=timeout)
        if info:
            break
        time.sleep(0.8)
    if not info:
        return None, None, None, None, False
    txt = (info.get('data') or info.get('txt') or info.get('text') or '').strip()
    m = re.search(r'(\d+)\s+(\d+)\s+收藏\s+(\d+)', txt)
    stat, why = text_verdict(txt) or (None, None)
    firm = stat is not None
    # 兜底(**仅当 allow_weak**: 失效作品会在 /video 停留十几秒后才跳推荐流, 过早兜底会误判为有效)
    if allow_weak and stat is None and info.get('ready') == 'complete' \
            and re.search(r'/video/\d+', info.get('url', '')) and len(txt) > 150:
        stat, why, firm = 'OK', '作品页(已渲染,未读到评论特征)', False
    title = (info.get('t') or info.get('h1') or info.get('desc') or '').strip()
    title = re.sub(r'\s*-\s*抖音\s*$', '', title).strip()
    if title in ('', '抖音'):
        title = txt[:64]
    ex = (f'赞{m.group(1)} 评{m.group(2)} 藏{m.group(3)} | ' if m else '') + title[:64]
    return stat, why, ex, info.get('ready'), firm


def _probe_all(cur, tabs, workers=8, allow_weak=False):
    """并行探查整批标签 -> {i: (st, why, ex, ready)}"""
    out = {}

    def job(i, it):
        t = tabs.get(it['tid']) or {}
        return i, probe_tab(t, it['link'], allow_weak=allow_weak)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, max(1, len(cur)))) as pool:
        futs = [pool.submit(job, i, it) for i, it in enumerate(cur, 1)]
        for f in concurrent.futures.as_completed(futs):
            try:
                i, v = f.result()
                out[i] = v
            except Exception:
                pass
    return out


def collect_batch(cur, port, timeout, interval=1.5):
    """轮询直到整批都「已渲染」(页面正文可读且 readyState=complete) 或超时。
    比固定 settle 快得多: 全部就绪即返回, 通常 5-12 秒。"""
    t0 = time.time()
    last = {}
    while True:
        tabs = {t['id']: t for t in list_tabs(port)}
        el = time.time() - t0
        last = _probe_all(cur, tabs, allow_weak=(el > 25))   # 25s 后才允许弱判定
        resolved = 0
        for i, it in enumerate(cur, 1):
            st, why, ex, ready, firm = (last.get(i) or (None, None, None, None, False))
            t = tabs.get(it['tid']) or {}
            url = t.get('url', '') or ''
            hst, _ = verdict(it['vid'], url, t.get('title') or '', it['link'])
            # 短链跳转页自身也会 readyState=complete, 必须已落到 /video/ 才算就绪
            on_video = bool(re.search(r'/video/\d+', url))
            if (st is not None and firm) or hst in ('OK', 'SUSPECT', 'CAPTCHA'):
                resolved += 1
            elif st is not None and time.time() - t0 > 25:
                resolved += 1        # 25s 后接受弱判定(渲染完成但无评论特征的少数页面)
        if resolved >= len(cur) or time.time() - t0 >= timeout:
            return last, tabs, time.time() - t0
        time.sleep(interval)


def parse_corrections(line, n):
    """-> (mode, set_of_idx)  mode: accept|override|skip"""
    s = line.strip()
    if s == '':
        return 'accept', set()
    if s == '-':
        return 'skip', set()
    bad, good = set(), set()
    for part in re.split(r'[,，\s]+', s):
        if not part:
            continue
        if '=' in part:
            a, b = part.split('=', 1)
            if a.strip().isdigit() and b.strip().lower() in ('ok', 'ok了', '有效', 'y'):
                good.add(int(a))
            continue
        if part.isdigit():
            bad.add(int(part))
    return 'override', (bad, good)


def load_rows(xlsx, sheet, col_link, col_seq, col_fee, seqs, min_fee, all_rows):
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb[sheet]
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {str(h).strip(): i + 1 for i, h in enumerate(hdr) if h}
    for need in (col_seq, col_link):
        if need not in ci:
            print(f'❌ 表里找不到列「{need}」，现有列: {list(ci)[:20]}')
            sys.exit(1)
    out = []
    for r in range(2, ws.max_row + 1):
        seq = ws.cell(r, ci[col_seq]).value
        if seq is None:
            continue
        if seqs and int(seq) not in seqs:
            continue
        fee = ws.cell(r, ci[col_fee]).value if col_fee in ci else None
        if not all_rows and min_fee is not None:
            if not isinstance(fee, (int, float)) or fee < min_fee:
                continue
        link = str(ws.cell(r, ci[col_link]).value or '').strip()
        if not link.startswith('http'):
            continue
        out.append(dict(seq=int(seq), link=link, fee=fee))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='验收链接')
    ap.add_argument('--col-link', default='发布链接')
    ap.add_argument('--col-seq', default='序号')
    ap.add_argument('--col-fee', default='验收费用（未税）')
    ap.add_argument('--batch', type=int, default=5, help='每批标签数(默认5；登录态请勿超5)')
    ap.add_argument('--settle', type=float, default=12, help='单批最长等待秒(默认12, 加载完会提前进入)')
    ap.add_argument('--min-fee', type=float, default=1, help='只复核验收费用≥该值的行(默认1)')
    ap.add_argument('--all-rows', action='store_true', help='不按费用过滤, 全部复核')
    ap.add_argument('--seqs', default=None, help='如 1-50,60')
    ap.add_argument('--out', default='链接复核/link_verify_manual.json')
    ap.add_argument('--profile', default=DEF_PROFILE)
    ap.add_argument('--port', type=int, default=9411)
    ap.add_argument('--pause', type=float, default=8, help='批间停顿秒(默认8, 降低风控概率)')
    ap.add_argument('--restart', action='store_true', help='若该 profile 的 Chrome 已在运行但无调试端口, 先关闭它')
    ap.add_argument('--accept-suspect', action='store_true', help='自动判 SUSPECT 的直接记失效, 不询问(全自动兜底)')
    args = ap.parse_args()

    seqs = None
    if args.seqs:
        seqs = set()
        for part in args.seqs.split(','):
            part = part.strip()
            if '-' in part:
                a, b = part.split('-')
                seqs.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                seqs.add(int(part))

    rows = load_rows(args.xlsx, args.sheet, args.col_link, args.col_seq, args.col_fee,
                     seqs, args.min_fee, args.all_rows)
    if not rows:
        print('没有待复核链接'); return
    print(f'待复核 {len(rows)} 条 | 每批 {args.batch} 个标签 | profile={args.profile}')

    ensure_chrome(args.profile, args.port, args.restart)
    for t in list_tabs(args.port):
        close_tab(t['id'], args.port)          # 清空旧标签, 保证批次占用标签位 1..N
    activate_chrome()
    open_tab('https://www.douyin.com/', args.port)     # 建一个标签用于登录态体检
    time.sleep(3)
    ok, msg = check_login(args.port)
    if ok is True:
        print(f'✅ {msg}')
    elif ok is False:
        print(f'❌ {msg}')
        sys.exit(1)
    print('Chrome 已就绪(请勿关闭该窗口)。标签位 1..N 对应本批顺序。\n')

    out = {}
    if os.path.exists(args.out):
        try:
            out = json.load(open(args.out))
            print(f'(续跑: 已有 {len(out)} 条结论)')
        except Exception:
            out = {}
    done = {int(k) for k, v in out.items() if v.get('status') in ('OK', 'SUSPECT', 'CAPTCHA')}

    def open_batch(chunk):
        b = []
        for it in chunk:
            vid = (re.search(r'/video/(\d+)', it['link']) or [None, None])[1]
            t = open_tab(it['link'], args.port)
            if t and t.get('id'):
                b.append(dict(**it, tid=t['id'], vid=vid))
        return b

    todo = [r for r in rows if r['seq'] not in done]
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    if not batches:
        print('全部已有结论'); return

    t_start = time.time()
    n_reviewed = 0
    cur = open_batch(batches[0])
    pref = None

    for bi, chunk in enumerate(batches):
        probes, tabs, wait = collect_batch(cur, args.port, args.settle)
        print(f'━━━ 批次 {bi+1}/{len(batches)}  (就绪 {wait:.1f}s)  标签 1..{len(cur)} 依次对应:')
        auto = {}
        for i, it in enumerate(cur, 1):
            t = tabs.get(it['tid']) or {}
            st, why = verdict(it['vid'], t.get('url', ''), t.get('title') or '', it['link'])
            st2, why2, ex, ready, firm = (probes.get(i) or (None, None, None, None, False))
            if st2:
                st, why = st2, why2
            elif st == 'EARLY':
                why += '（页面正文未读到，请目视）'
            auto[i] = dict(st=st, why=why, it=it, ex=ex or '')
            flag = {'OK': '✅', 'SUSPECT': '❌', 'CAPTCHA': '⚠️', 'BLOCKED': '🚫', 'EARLY': '⏳'}.get(st, '?')
            print(f'  {flag} 标签{i:>2} | 序号{it["seq"]:<4} | {why}')
            if ex:
                print(f'          └ {ex}')
        if bi + 1 < len(batches):                      # 人审本批时后台预载下一批
            pref = open_batch(batches[bi + 1])
        print('  目视确认后：回车=认可 | 如 "3,7"=标签3/7失效 | "3=ok"=标签3有效 | "-"=跳过 | "q"=退出保存')
        try:
            line = input('  > ')
        except (EOFError, KeyboardInterrupt):
            line = 'q'
        if line.strip().lower() == 'q':
            for it in cur + (pref or []):
                close_tab(it['tid'], args.port)
            break
        mode, payload = parse_corrections(line, len(cur))
        blocked = 0
        for i, a in auto.items():
            st, why, it = a['st'], a['why'], a['it']
            if st in ('BLOCKED', 'CAPTCHA'):
                blocked += 1
                continue            # 壳页/验证码 -> 不写结论, 下次重跑
            if mode == 'skip':
                continue
            if mode == 'override':
                bad, good = payload
                if i in bad:
                    st, why = 'SUSPECT', '人工目视判定失效'
                elif i in good:
                    st, why = 'OK', '人工目视判定有效'
                elif args.accept_suspect and st == 'SUSPECT':
                    why += '（未人工改判，按自动判定）'
            elif args.accept_suspect and st == 'SUSPECT':
                why += '（未人工改判，按自动判定）'
            out[str(it['seq'])] = dict(seq=it['seq'], link=it['link'],
                                       status=st, reason=why, lane='human',
                                       ts=time.strftime('%Y-%m-%d %H:%M:%S'),
                                       fee=it['fee'], excerpt=a.get('ex', ''))
        json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)
        if blocked:
            print(f'  ⚠️ 本批 {blocked}/{len(cur)} 条为壳页/验证码页，未记录结论（请间隔后重跑）')
        if blocked >= 2:
            print(f'  🛑 本批出现 {blocked} 条风控页 —— 立即熔断停止（账号已被限流，至少冷却 30 分钟；'
                  f'且批量本就不该用登录态，见 SKILL.md 风控红线）')
            for it in cur + (pref or []):
                close_tab(it['tid'], args.port)
            break
        n_reviewed += len(cur)
        el = time.time() - t_start
        print(f'  -- 已复核 {n_reviewed}/{len(todo)}  用时 {el:.0f}s  速率 {n_reviewed/el*60:.1f} 条/分 --\n')
        for it in cur:
            close_tab(it['tid'], args.port)
        cur = pref
        pref = None
        time.sleep(args.pause)

    from collections import Counter
    c = Counter(v['status'] for v in out.values())
    el = time.time() - t_start
    print(f'== 完成 {len(out)} 条, 用时 {el:.0f}s, 平均 {len(out)/max(el,1)*60:.1f} 条/分')
    print('   结论:', dict(c))
    for k, v in sorted(out.items(), key=lambda kv: int(kv[0])):
        if v['status'] != 'OK':
            print(f"   序号{k}: {v['status']} {v['reason'][:50]}")
    print('明细:', args.out)


if __name__ == '__main__':
    main()
