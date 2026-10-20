#!/usr/bin/env python3
"""像素级裁决：对争议行，用 bbox 标签-值邻接法逐张截图读出权威值。

用法:
  python3 adjudicate.py --work-dir ./PO0000000000 12 35 101
  python3 adjudicate.py --work-dir ./PO0000000000 12 35 --field 点赞量 完播率

依赖:
  - 先编译坐标工具: swiftc -O scripts/bbox.swift -o /tmp/bboxbin
  - 工作目录需有 work_rows.json（由审核流水线抽取）
  - 截图字段 `imgs` 可以是 URL 或本地路径

逻辑:
  bbox.swift 输出 token + 归一化坐标（原点左下）→ 找标签 token →
  取同 x 列（±0.06）且 标签y-值y ∈ (0.006, 0.06) 的数值 token 即真值。
  这是「全页 OCR 读错/漏读」时的最终裁决手段。
"""
import argparse
import json
import os
import re
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BBOX = os.environ.get('BBOX_BIN', '/tmp/bboxbin')

FIELDS = {
    '点赞量': 'like', '评论量': 'cmt', '分享量': 'rep', '收藏量': 'fav',
    '播放量': 'play', '完播率': 'finish', '转发量': 'rep',
}


def ensure_bbox():
    if not os.path.exists(BBOX):
        subprocess.run(
            ['swiftc', '-O', os.path.join(SCRIPT_DIR, 'bbox.swift'), '-o', BBOX],
            check=True,
        )


def tokens(img):
    p = subprocess.run([BBOX, img], capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        if '\t' not in line:
            continue
        xy, txt = line.split('\t', 1)
        try:
            x, y, w, h = map(float, xy.split())
        except ValueError:
            continue
        out.append((x, y, w, h, txt.strip()))
    return out


def find_value(toks, label):
    """标签下方同列最近的数值 token。"""
    cands = []
    for x, y, w, h, t in toks:
        if label not in t or len(t) > 12:
            continue
        if label == '完播率' and '5s' in t.lower():
            continue
        for x2, y2, w2, h2, t2 in toks:
            if t2 == t:
                continue
            dx = abs(x2 - x)
            dy = y - y2
            if dx <= 0.06 and 0.006 < dy < 0.06:
                v = re.match(r'^(\d+(?:\.\d+)?)\s*(%|万)?$', t2)
                if v:
                    cands.append((dy, x2, t2, (x, y, t)))
    cands.sort()
    return cands[0] if cands else None


def main():
    ap = argparse.ArgumentParser(description='Pixel-level adjudication via bbox label-value adjacency.')
    ap.add_argument('seqs', nargs='+', type=int, help='序号列表')
    ap.add_argument('--work-dir', default=os.environ.get('YANSHOU_WORK_DIR', os.getcwd()),
                    help='包含 work_rows.json 的工作目录（默认当前目录）')
    ap.add_argument('--img-dir', default=os.environ.get('YANSHOU_IMG_DIR', '/tmp/ys_adj'),
                    help='截图临时目录')
    ap.add_argument('--field', nargs='*', default=None, help='只裁决指定字段，如 点赞量 完播率')
    args = ap.parse_args()

    ensure_bbox()
    rows_path = os.path.join(args.work_dir, 'work_rows.json')
    if not os.path.exists(rows_path):
        raise SystemExit(f'缺少 {rows_path}（先运行审核流水线的 extract 步骤）')
    rows = {x['seq']: x for x in json.load(open(rows_path, encoding='utf-8'))}
    os.makedirs(args.img_dir, exist_ok=True)
    summary = {}

    for seq in args.seqs:
        x = rows.get(seq)
        if not x:
            print(f'===== 序号{seq}: work_rows.json 中不存在，跳过')
            continue
        shot_play = x.get('shot_play', x.get('shot'))
        rate = x.get('rate', x.get('interact'))
        finish = x.get('finish', x.get('finish_use', x.get('finish_col')))
        print(f"===== 序号{seq} {x.get('name', '')} / {x.get('nick', '')}")
        print(
            f"  表内: 播放(截图){float(shot_play or 0):.0f} "
            f"播放(系统){float(x.get('play') or 0):.0f} "
            f"赞{float(x.get('like') or 0):.0f} 评{float(x.get('cmt') or 0):.0f} "
            f"转{float(x.get('rep') or 0):.0f} 藏{float(x.get('fav') or 0):.0f} "
            f"互动率{float(rate or 0) * 100:.3f}% "
            f"完播{float(finish or 0) * 100:.2f}%"
        )
        imgs = x.get('imgs') or [s for s in (x.get('s1'), x.get('s2'), x.get('s3')) if s]
        got = []
        for i, u in enumerate(imgs, 1):
            ext = os.path.splitext(u.split('?')[0])[1] or '.jpg'
            img = os.path.join(args.img_dir, f'seq{seq}_s{i}{ext}')
            if not os.path.exists(img):
                subprocess.run(
                    ['curl', '-s', '-L', '-o', img, '--max-time', '40', u],
                    check=False,
                )
            if not os.path.exists(img) or os.path.getsize(img) < 1000:
                print(f'  截图{i}: 下载失败')
                continue
            toks = tokens(img)
            line = []
            for label, key in FIELDS.items():
                if args.field and label not in args.field:
                    continue
                hit = find_value(toks, label)
                if hit:
                    line.append(f'{label}={hit[2]}')
                    got.append((key, hit[2]))
            print(f'  截图{i}: ' + (' | '.join(line) if line else '(无标签命中)'))

        agg = {}
        for k, v in got:
            m = re.match(r'^(\d+(?:\.\d+)?)(万|%)?$', v)
            if not m:
                continue
            val = float(m.group(1)) * (10000 if m.group(2) == '万' else 1)
            agg.setdefault(k, []).append(val)
        if agg:
            parts = []
            for k, cn in (('like', '赞'), ('cmt', '评'), ('rep', '转'), ('fav', '藏'), ('finish', '完播')):
                if k in agg:
                    parts.append(f'{cn}{sorted(set(agg[k]))}')
            print('  截图汇总: ' + ' '.join(parts))
        summary[seq] = agg

    out = os.path.join(args.work_dir, 'adjudication.json')
    prev = json.load(open(out, encoding='utf-8')) if os.path.exists(out) else {}
    prev.update({str(k): v for k, v in summary.items()})
    json.dump(prev, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('->', out)


if __name__ == '__main__':
    main()
