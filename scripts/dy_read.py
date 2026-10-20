#!/usr/bin/env python3
"""打开抖音作品页(登录态 Chrome, CDP) 并抓取页面结构化信息。

用法:
  python3 dy_read.py <url> [--wait 12] [--out raw/xxx.json]
输出: JSON -> {url, title, desc, author, text, comments, video_src, ts}
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_ws import WS  # noqa: E402

PORT = int(os.environ.get('DY_CDP_PORT', '9411'))

JS = r"""
(() => {
  const q = (s) => document.querySelector(s);
  const txt = (el) => el ? (el.innerText || '').trim() : '';
  // 文案: 抖音作品页的描述节点候选
  let desc = '';
  for (const s of ['[data-e2e="video-desc"]', '.video-info-detail', 'h1']) {
    const el = q(s);
    if (el && txt(el)) { desc = txt(el); break; }
  }
  // 作者
  let author = '';
  const au = q('[data-e2e="video-author-name"]') || q('a[href^="/user/"]');
  if (au) author = txt(au);
  // 互动数据: 找带 data-e2e 的计数
  const stats = {};
  document.querySelectorAll('[data-e2e^="video-"]').forEach((el) => {
    const k = el.getAttribute('data-e2e');
    const v = txt(el);
    if (k && v && v.length < 30) stats[k] = v;
  });
  // 评论
  const comments = [];
  document.querySelectorAll('[data-e2e="comment-item"]').forEach((el) => {
    const t = txt(el).replace(/\s+/g, ' ');
    if (t) comments.push(t.slice(0, 300));
  });
  // 视频源
  const v = q('video');
  const vsrc = v ? (v.currentSrc || v.src || '') : '';
  const vdur = v && isFinite(v.duration) ? Math.round(v.duration) : null;
  return JSON.stringify({
    title: document.title,
    url: location.href,
    ready: document.readyState,
    meta_desc: (q('meta[name=description]') || {}).content || '',
    desc, author, stats, n_comments: comments.length, comments: comments.slice(0, 40),
    video_src: vsrc.slice(0, 300), video_dur: vdur,
    body_head: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 1200)
  });
})()
"""


def evaluate(ws_url, expr, timeout=30):
    w = WS(ws_url, timeout=timeout)
    try:
        w.send_text(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': expr, 'returnByValue': True,
                                           'awaitPromise': True}}))
        for _ in range(200):
            msg = json.loads(w.recv_text())
            if msg.get('id') == 1:
                r = msg.get('result', {})
                if 'exceptionDetails' in r:
                    return {'_err': str(r['exceptionDetails'])[:300]}
                return r.get('result', {}).get('value')
    finally:
        w.close()
    return None


def call(ws_url, method, params=None, timeout=30):
    w = WS(ws_url, timeout=timeout)
    try:
        w.send_text(json.dumps({'id': 1, 'method': method, 'params': params or {}}))
        for _ in range(200):
            msg = json.loads(w.recv_text())
            if msg.get('id') == 1:
                return msg.get('result')
    finally:
        w.close()
    return None


def find_tab(url_sub, port=PORT):
    import urllib.request
    d = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5))
    for t in d:
        if t.get('type') == 'page' and url_sub in (t.get('url') or ''):
            return t
    return None


def open_url(url, port=PORT):
    """在登录态窗口新开标签并导航, 返回 tab dict"""
    import urllib.request
    req = urllib.request.Request(f'http://127.0.0.1:{port}/json/new?about:blank', method='PUT')
    t = json.load(urllib.request.urlopen(req, timeout=10))
    call(t['webSocketDebuggerUrl'], 'Page.navigate', {'url': url})
    return t


def wait_loaded(ws_url, need=('video',), timeout=45):
    """轮询等待页面出现关键元素"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        v = evaluate(ws_url, JS)
        if isinstance(v, str):
            try:
                last = json.loads(v)
            except Exception:
                last = None
        if last and last.get('ready') == 'complete' and last.get('title'):
            body = last.get('body_head', '')
            if last.get('video_src') or '验证码' in body or '请登录' in body:
                return last
        time.sleep(1.5)
    return last


def main():
    ap = argparse.ArgumentParser(description='Read a Douyin work page via CDP.')
    ap.add_argument('url')
    ap.add_argument('--wait', type=int, default=45, help='最长等待秒数（默认45）')
    ap.add_argument('--out', default=None, help='JSON 输出路径')
    a = ap.parse_args()
    url, wait, out = a.url, a.wait, a.out
    t = open_url(url)
    ws_url = t['webSocketDebuggerUrl']
    data = wait_loaded(ws_url, timeout=wait)
    res = {'probe_url': url, 'tab_ws': ws_url, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'page': data}
    s = json.dumps(res, ensure_ascii=False, indent=1)
    if out:
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        open(out, 'w', encoding='utf-8').write(s)
        print(f'written {out}')
    print(s[:4000])


if __name__ == '__main__':
    main()
