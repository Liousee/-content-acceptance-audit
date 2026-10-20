#!/usr/bin/env python3
"""从已打开的抖音作品页标签里取出真实媒体地址(视频/图集), 并下载。

用法: python3 dy_media.py <video_id> [--out raw/<id>.mp4]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dy_read import evaluate, call, find_tab  # noqa: E402

JS_RES = r"""
(() => {
  const out = {videos: [], imgs: [], perf: [], dur: null, img_post: false};
  document.querySelectorAll('video').forEach(v => {
    out.videos.push({src: v.src || '', cur: v.currentSrc || '', dur: isFinite(v.duration) ? v.duration : null,
                     w: v.videoWidth, h: v.videoHeight, paused: v.paused});
    if (isFinite(v.duration)) out.dur = v.duration;
  });
  // 图集(图文作品): 大图轮播
  document.querySelectorAll('img').forEach(i => {
    if (i.naturalWidth >= 500 && i.src && !i.src.startsWith('data:')) out.imgs.push(i.src);
  });
  performance.getEntriesByType('resource').forEach(e => {
    if (/douyinvod|\.mp4|media-video|zjcdn|byteimg.*~/.test(e.name)) out.perf.push(e.name);
  });
  return JSON.stringify(out);
})()
"""


def grab(tab_ws):
    v = evaluate(tab_ws, JS_RES)
    return json.loads(v) if isinstance(v, str) else {}


def download(url, out, referer='https://www.douyin.com/'):
    cmd = ['curl', '-sS', '-L', '--max-time', '180',
           '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36',
           '-H', f'Referer: {referer}', '-o', out, url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sz = os.path.getsize(out) if os.path.exists(out) else 0
    return r.returncode, sz, r.stderr[:200]


def main():
    ap = argparse.ArgumentParser(description='Extract and download media from an open Douyin work tab.')
    ap.add_argument('video_id')
    ap.add_argument('--out', default=None, help='下载输出路径')
    a = ap.parse_args()
    vid, out = a.video_id, a.out
    tab = find_tab(vid)
    if not tab:
        print(json.dumps({'error': 'tab not found', 'vid': vid}))
        return
    info = grab(tab['webSocketDebuggerUrl'])
    # 选最佳视频地址
    cands = [v['cur'] or v['src'] for v in info.get('videos', []) if (v['cur'] or v['src']).startswith('http')]
    cands += [u for u in info.get('perf', []) if 'douyinvod' in u]
    url = cands[0] if cands else None
    res = {'vid': vid, 'dur': info.get('dur'), 'n_videos': len(info.get('videos', [])),
           'n_imgs': len(info.get('imgs', [])), 'url': (url or '')[:200]}
    if url and out:
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        rc, sz, err = download(url, out)
        res['download'] = {'rc': rc, 'bytes': sz, 'err': err}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
