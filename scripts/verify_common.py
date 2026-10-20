#!/usr/bin/env python3
"""核验基础层：状态词表 / URL归一化 / 计数解析 / 作者卡解析 / 结果库(缓存) / 全局限流
被 link_verify.py、link_status.py、headful_verify.py、link_review.py 共用。

设计要点（实测沉淀）：
- 请求数才是稀缺资源：结果库跨批次复用 + 同视频/同达人去重
- 状态词表与 audit_pipeline.build_output 第6列着色约定严格对齐（'失效'开头→红字，'未复核'→橙字）
- 全局限流状态 + 运行锁，杜绝多脚本各自轰炸抖音
"""
import sys
import fcntl, json, os, re, shutil, subprocess, tempfile, time, random

# ---------- 状态词表（勿改 DISPLAY 字符串：交付物着色依赖） ----------
VALID, INVALID, EARLY, PENDING, CAPTCHA, ERR, SKIPPED = (
    'VALID', 'INVALID', 'EARLY', 'PENDING', 'CAPTCHA', 'ERR', 'SKIPPED')
DISPLAY = {
    VALID: '有效',
    INVALID: '失效(跳转推荐流/404)',
    EARLY: '未复核', PENDING: '未复核', CAPTCHA: '未复核', ERR: '未复核', SKIPPED: '未复核',
}
CERTAIN = (VALID, INVALID)          # 可长缓存的确定态
TTL = {                             # 秒；0 = 不缓存（必重试）
    VALID: 7 * 86400,
    INVALID: 30 * 86400,            # 删了不会回来
    SKIPPED: 30 * 86400,
    EARLY: 0, PENDING: 0, CAPTCHA: 0, ERR: 0,
}

CACHE_DIR = os.path.expanduser('~/.cache/yanshou')
DB_PATH = os.path.join(CACHE_DIR, 'verify_db.jsonl')
STATE_PATH = os.path.join(CACHE_DIR, 'rate_state.json')
LOCK_PATH = os.path.join(CACHE_DIR, 'run.lock')

# ---------- 原子写 / 追加写 ----------
def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')
        f.flush(); os.fsync(f.fileno())

# ---------- URL 归一化 ----------
VIDEO_RE = re.compile(r'(?:/video/|/share/video/|modal_id=)(\d{15,25})')
SHORT_RE = re.compile(r'https?://v\.douyin\.com/([\w\-]+)')
UA_IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
             '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')

def norm_url(url):
    """→ (kind, key, canonical)
    kind: 'video'（可直接取id，0请求）/ 'short'（需解析一次）/ 'unsupported'（非抖音）
    key : 'v:<aweme_id>' 或 'u:<shortcode>'
    """
    url = (url or '').strip()
    if not url.startswith('http'):
        return 'unsupported', None, None
    if 'weixin.qq.com' in url or '/sph/' in url:
        return 'unsupported', None, None
    m = VIDEO_RE.search(url)
    if m:
        vid = m.group(1)
        return 'video', f'v:{vid}', f'https://www.douyin.com/video/{vid}'
    m = SHORT_RE.search(url)
    if m:
        return 'short', f'u:{m.group(1)}', url
    if 'douyin.com' in url:
        return 'short', f'u:{abs(hash(url)) % 10**12}', url   # 其它抖音形态：当短链处理
    return 'unsupported', None, None

_last_short_ts = [0.0]
def resolve_short(url, db=None, timeout=20, min_interval=0.6):
    """短链→视频id；结果永久缓存。加最小间隔节流（避免几百条短链在瞬间形成请求突发）"""
    dt = time.time() - _last_short_ts[0]
    if dt < min_interval:
        time.sleep(min_interval - dt)
    _last_short_ts[0] = time.time()
    """短链→视频id（纯HTTP重定向，不启动浏览器；结果永久缓存）"""
    key = f'u:{SHORT_RE.search(url).group(1)}' if SHORT_RE.search(url) else None
    if db and key:
        rec = db.get(key, max_age=30 * 86400)
        if rec and rec.get('video_id'):
            return rec['video_id']
    try:
        p = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{url_effective}', '-L',
                            '--max-time', str(timeout), '-A', UA_IPHONE, url],
                           capture_output=True, text=True)
        m = VIDEO_RE.search(p.stdout or '')
        vid = m.group(1) if m else None
    except Exception:
        vid = None
    if db and key and vid:
        db.put(key, VALID, video_id=vid, note='short-link-resolved')
    return vid

# ---------- 计数解析 ----------
def parse_count(raw):
    """'1.1万'→(11000,True) | '2434'→(2434,False) | '82.8万'→(828000,True) | None→(None,False)
    rounded=True 表示平台万位舍入（值≥10000，对 ≥1000 判定无影响）"""
    if not raw:
        return None, False
    s = str(raw).strip().replace(',', '')
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(万|亿)?', s)
    if not m:
        return None, False
    v = float(m.group(1))
    unit = m.group(2)
    if unit == '万':
        return int(v * 10000), True
    if unit == '亿':
        return int(v * 100000000), True
    return int(v), False

# ---------- 作者卡解析（作品页） ----------
# 作品页是「标签在前、值在后」：粉丝 X 获赞 Y 关注；必须三连做锚，
# 否则会被页面内嵌 JSON 的 "粉丝指数"/"fan_index_info" 误命中。
RE_AUTHOR_TRIPLE = re.compile(
    r'粉丝\s*([\d,]+(?:\.\d+)?\s*[万亿]?)\s*获赞\s*([\d,]+(?:\.\d+)?\s*[万亿]?)\s*关注')
RE_AUTHOR_NICK = re.compile(r'([^\s|]{1,24})\s*\|\s*粉丝\s')      # 昵称|粉丝 (dump-dom 文本化后)

def extract_author_card(text):
    """从作品页纯文本提取作者卡；多命中不一致→ fans=None 且 ambiguous=True"""
    if not text:
        return None
    hits = RE_AUTHOR_TRIPLE.findall(text)
    if not hits:
        return None
    fans_raws = {h[0].strip() for h in hits}
    likes_raws = {h[1].strip() for h in hits}
    out = {'hits': len(hits), 'ambiguous': len(fans_raws) > 1 or len(likes_raws) > 1}
    if out['ambiguous']:
        out.update(fans=None, fans_raw=None, likes=None, likes_raw=None)
    else:
        fr, lr = fans_raws.pop(), likes_raws.pop()
        fv, fround = parse_count(fr); lv, lround = parse_count(lr)
        out.update(fans=fv, fans_raw=fr, fans_rounded=fround, likes=lv, likes_raw=lr, likes_rounded=lround)
    return out

def html_to_text(html):
    t = re.sub(r'<script[^>]*>.*?</script>', ' ', html or '', flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t)

# ---------- 结果库 ----------
class DB:
    """跨批次结果库：JSONL 追加(崩溃安全/天然断点续跑) + 内存索引"""
    def __init__(self, path=None, snapshot=None):
        self.path = path or DB_PATH
        self.snapshot = snapshot or (os.path.splitext(self.path)[0] + '.json')
        self.recs = {}
        self.n_lines = 0
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        self.recs[r['key']] = r
                        self.n_lines += 1
            except Exception:
                pass

    def get(self, key, max_age=None):
        """max_age=None → 用状态默认 TTL；max_age=0 → 只认永不过期的确定态"""
        r = self.recs.get(key)
        if not r:
            return None
        ttl = TTL.get(r.get('status'), 0) if max_age is None else max_age
        if ttl <= 0:
            return None
        if time.time() - r.get('ts', 0) > ttl:
            return None
        return r

    def put(self, key, status, **fields):
        rec = dict(fields, key=key, status=status, ts=time.time())
        hist = list(self.recs.get(key, {}).get('history', []))[-2:]
        hist.append({'status': status, 'ts': rec['ts']})
        rec['history'] = hist
        self.recs[key] = rec
        append_jsonl(self.path, rec)
        self.n_lines += 1
        return rec

    def save_snapshot(self):
        atomic_write_json(self.snapshot, {
            'saved_at': time.time(), 'n': len(self.recs),
            'recs': {k: {kk: vv for kk, vv in v.items() if kk != 'history'} for k, v in self.recs.items()},
        })

    def stats(self):
        from collections import Counter
        return Counter(r.get('status') for r in self.recs.values())

# ---------- 全局限流（跨脚本/跨批次共享） ----------
LANES = {
    # lane: (并发标签, 基础间隔秒, 抖动比例, 窗口预算条数, 窗口秒, 冷却秒, 熔断阈值)
    # 保守默认：anon 3 并发 / 8s 间隔 / 200 条每 30 分钟；login 仅少量定点（10 条/30 分钟）。
    # 需要提速时请先阅读 docs/safety.md，并自行承担账号风险。
    'anon':  (3, 8, 0.40, 200, 1800, 25 * 60, 2),
    'login': (2, 12, 0.50, 10, 1800, 60 * 60, 1),
}

class Limiter:
    """令牌桶 + 窗口预算 + 冷却；状态跨进程共享（rate_state.json + flock）"""
    def __init__(self, lane='anon', state_path=None):
        if lane not in LANES:
            raise ValueError(f'unknown lane {lane}')
        self.lane = lane
        self.tabs, self.spacing, self.jitter, self.budget, self.window, self.cooldown, self.max_captcha = LANES[lane]
        self.state_path = state_path or STATE_PATH
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)

    def _load(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, st):
        atomic_write_json(self.state_path, st)

    def remaining_cooldown(self):
        st = self._load()
        return max(0, st.get('blocked_until', 0) - time.time())

    def wait(self):
        """sleep 到下一次允许请求；窗口预算用尽则进入冷却"""
        rem = self.remaining_cooldown()
        if rem > 0:
            raise RuntimeError(f'全局冷却中，剩余 {int(rem/60)} 分钟（此前触发过验证码）')
        st = self._load()
        lane = st.setdefault('lanes', {}).setdefault(self.lane, {'count': 0, 'window_start': time.time()})
        now = time.time()
        if now - lane.get('window_start', 0) > self.window:
            lane['count'] = 0; lane['window_start'] = now
        if lane['count'] >= self.budget:
            # 预算用尽：默认"等到窗口重置再继续"（用户要求一次跑完）；--no-wait-budget 时抛错退出
            reset_in = max(30, self.window - (now - lane.get('window_start', 0)))
            if os.environ.get('YANSHOU_NO_WAIT_BUDGET'):
                raise RuntimeError(f'本窗口预算 {self.budget} 条用尽（YANSHOU_NO_WAIT_BUDGET=1 故退出）')
            print(f'-- 本窗口预算 {self.budget} 条用尽，等待 {reset_in/60:.1f} 分钟窗口重置后继续 --', flush=True)
            time.sleep(reset_in)
            lane['count'] = 0; lane['window_start'] = time.time()
            self._save(st)
        lane['count'] += 1
        self._save(st)
        time.sleep(self.spacing * (1 + random.uniform(-self.jitter, self.jitter)))

    def on_captcha(self, url='', seq=None, note=''):
        """被拦：登录态全停；免登录也全 lane 冷却（IP 已热）"""
        st = self._load()
        now = time.time()
        base = self.cooldown
        prev = st.get('captcha_events', [])
        if any(now - e.get('ts', 0) < 3600 for e in prev):   # 1 小时内二次熔断 → 冷却翻倍
            base = min(base * 2, 4 * 3600)
        st['blocked_until'] = now + base
        st['cooldown_until'] = now + base
        ev = list(prev)[-49:] + [{'ts': now, 'lane': self.lane, 'url': url, 'seq': seq, 'note': note}]
        st['captcha_events'] = ev
        self._save(st)
        return base

    def captcha_count(self):
        return len(self._load().get('captcha_events', []))

class RunLock:
    """同一时刻只允许一个核验 run（防多脚本各自轰炸）"""
    def __init__(self, path=None):
        self.path = path or LOCK_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, 'w')
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('已有另一个核验任务在运行（见 ~/.cache/yanshou/run.lock）；请等待其结束')
        self.fh.write(f'pid={os.getpid()} ts={time.time()}\n'); self.fh.flush()
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN); self.fh.close()
        except Exception:
            pass

def purge_worker_profiles(base=None, keep=()):
    """清理历史遗留的 *-w<id> profile 副本（Chrome 会占空间）"""
    base = base or os.path.expanduser('~/.cache')
    removed = []
    for name in os.listdir(base):
        if re.match(r'^yanshou-chrome-w\d+$', name) and name not in keep:
            p = os.path.join(base, name)
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
    return removed
