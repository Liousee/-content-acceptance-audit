#!/usr/bin/env python3
"""Token 用量与费用统计（示例：DeepSeek 定价）

用法:
  python3 token_usage.py                # 当前项目最新会话：本次任务 + 会话累计
  python3 token_usage.py --last-task    # 只报本次任务（任务结束时用，最省输出）
  python3 token_usage.py --session <jsonl> [--model <PRICES键>] [--json] [--no-log]

数据源: ~/.claude/projects/<项目slug>/<sessionId>.jsonl
  · assistant 条目的 message.usage 含 input/output/cache_read/cache_creation tokens
  · 任务边界: type=='user' 且 turnOrigin=='human'
  · 时间戳为 UTC；计费按每条调用自己的时刻判定 高峰/空闲（北京时间 UTC+8）

定价（元/百万 tokens，2026-10-10 取自 api-docs.deepseek.com/zh-cn/quick_start/pricing/）
  高峰 = 周一至周五 09:00-12:00 / 14:00-18:00（北京时间，法定节假日除外，脚本不做节假日判断）
  空闲 = 其余时段（含周末全天）= 高峰的一半
"""
import sys
import argparse, glob, json, os, re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ---------- 定价（元/百万 tokens）----------
# 示例值：DeepSeek 公开定价。换成你的服务商价格即可。
PRICES = {
    'flash': {'off': {'miss': 1.0, 'hit': 0.02, 'out': 4.0},
              'peak': {'miss': 2.0, 'hit': 0.04, 'out': 8.0}},
    'pro':   {'off': {'miss': 4.5, 'hit': 0.15, 'out': 13.5},
              'peak': {'miss': 9.0, 'hit': 0.30, 'out': 27.0}},
}
CN = timezone(timedelta(hours=8))
LOG_PATH = os.path.expanduser('~/.cache/yanshou/token_log.jsonl')


def is_peak(dt_utc):
    """北京时间周一至周五 9-12、14-18 点为高峰（不含法定节假日判断）"""
    t = dt_utc.astimezone(CN)
    if t.weekday() >= 5:
        return False
    h = t.hour + t.minute / 60
    return (9 <= h < 12) or (14 <= h < 18)


def find_session(explicit=None):
    if explicit:
        return explicit
    base = os.path.expanduser('~/.claude/projects')
    cands = []
    for d in glob.glob(os.path.join(base, '*')):
        cands += glob.glob(os.path.join(d, '*.jsonl'))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def parse(path):
    """→ (calls[(ts, usage)], last_human_idx)  按 message.id 去重"""
    calls = []
    seq = []              # 顺序记录 (kind, idx_in_calls)
    seen = set()
    last_human = -1
    with open(path, encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get('type')
            if t == 'user':
                if d.get('turnOrigin') == 'human':
                    last_human = len(calls)
                continue
            if t != 'assistant':
                continue
            m = d.get('message') or {}
            u = m.get('usage')
            if not isinstance(u, dict):
                continue
            key = m.get('id') or d.get('uuid')
            if key in seen:
                continue
            seen.add(key)
            ts = None
            if d.get('timestamp'):
                try:
                    ts = datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00'))
                except Exception:
                    ts = None
            calls.append({'ts': ts, 'miss': (u.get('input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0),
                          'hit': u.get('cache_read_input_tokens') or 0,
                          'out': u.get('output_tokens') or 0})
    return calls, last_human


def cost_of(calls, model, now_peak=None):
    """按每条调用时刻分档计价（无时间戳的按 now_peak/默认空闲）"""
    p = PRICES[model]
    tot = defaultdict(float)
    for c in calls:
        peak = is_peak(c['ts']) if c['ts'] else bool(now_peak)
        rates = p['peak'] if peak else p['off']
        tot['miss'] += c['miss']; tot['hit'] += c['hit']; tot['out'] += c['out']
        tot['cost'] += (c['miss'] * rates['miss'] + c['hit'] * rates['hit'] + c['out'] * rates['out']) / 1_000_000
        tot['peak_calls' if peak else 'off_calls'] += 1
    return tot


def fmt(n):
    if n >= 1e9: return f'{n/1e9:.2f}B'
    if n >= 1e6: return f'{n/1e6:.2f}M'
    if n >= 1e3: return f'{n/1e3:.1f}K'
    return str(int(n))


def describe(t, label):
    tokens = int(t['miss'] + t['hit'] + t['out'])
    return (f"{label}：tokens {tokens:,}（未命中 {fmt(t['miss'])} / 缓存命中 {fmt(t['hit'])} / 输出 {fmt(t['out'])}）"
            f" ≈ {t['cost']:.3f} 元")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', default=None)
    ap.add_argument('--model', default='flash', choices=sorted(PRICES))
    ap.add_argument('--last-task', action='store_true', help='只报本次任务')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-log', action='store_true', help='不写 token_log.jsonl')
    a = ap.parse_args()

    path = find_session(a.session)
    if not path or not os.path.exists(path):
        print('未找到会话转录文件（可用 --session 指定）'); return
    calls, last_human = parse(path)
    if not calls:
        print('会话中未发现 usage 数据（转录格式可能已变更）'); return

    task_calls = calls[last_human:] if last_human >= 0 else calls
    sess = cost_of(calls, a.model)
    task = cost_of(task_calls, a.model)
    out = {
        'session_file': path,
        'model': a.model,
        'task': {k: task[k] for k in ('miss', 'hit', 'out', 'cost')},
        'task_calls': len(task_calls),
        'session': {k: sess[k] for k in ('miss', 'hit', 'out', 'cost')},
        'session_calls': len(calls),
        'peak_calls': sess.get('peak_calls', 0), 'off_calls': sess.get('off_calls', 0),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(describe(task, f'本次任务（{len(task_calls)} 次调用）'))
        if not a.last_task:
            print(describe(sess, f'本会话累计（{len(calls)} 次调用，高峰 {out["peak_calls"]} / 空闲 {out["off_calls"]}）'))
    if not a.no_log:
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, 'a') as f:
                f.write(json.dumps({'ts': datetime.now(CN).isoformat(timespec='seconds'),
                                    'session': os.path.basename(path), 'model': a.model,
                                    'task_tokens': task['miss'] + task['hit'] + task['out'],
                                    'task_cost': round(task['cost'], 4),
                                    'session_tokens': sess['miss'] + sess['hit'] + sess['out'],
                                    'session_cost': round(sess['cost'], 4)}, ensure_ascii=False) + '\n')
        except Exception:
            pass


if __name__ == '__main__':
    main()
