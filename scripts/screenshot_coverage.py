#!/usr/bin/env python3
"""截图类型分类 + 可核验性判定: 输出「缺作品数据详情页」的行(人工确认项候选)
用法: python3 screenshot_coverage.py <验收表.xlsx> [--ocr-cache ~/.cache/yanshou_ocr.json] [--json OUT]

背景: 只有「作品数据详情页」能作为完播率/互动率的核验源；
内容吸引力/观众分析页(有完播率无互动数)、作品浏览页、DOU+投放订单页(仅早期数据)、内容诊断页 均不能核验 → 进人工确认项。
缓存由 audit_pipeline.py 的 OCR 步骤填充(截图URL/本地路径→{txt}), 故先跑流水线再用本工具。
"""
import argparse, json, os, re, openpyxl

FIN = re.compile(r'[完元]播率')
INTER = re.compile(r'点赞量|评论量|收藏量|分享量')
DATA = '作品数据详情'
DOU_KW = ('投放DOU+', '订单视频', '主浏览量', '进入DOU+', '目标完成', '内容诊断已完成', '发布日期：')
CONTENT_KW = ('内容吸引力', '观众分析', '5s完播率', '平均播放时长')

def classify(txt):
    if DATA in txt:
        return '数据页' if INTER.search(txt) else '数据页(无互动字段)'
    if any(k in txt for k in DOU_KW): return 'DOU+/诊断页'
    if any(k in txt for k in CONTENT_KW) and FIN.search(txt): return '内容吸引力/观众分析页'
    if re.search(r'\d+\s*浏览', txt): return '作品浏览页'
    return '其他'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--ocr-cache', default=os.path.expanduser('~/.cache/yanshou_ocr.json'))
    ap.add_argument('--json', default=None)
    ap.add_argument('--no-tier-amounts', default='0',
                    help='不要求完播/互动读数的金额，逗号分隔（如 0,100）')
    args = ap.parse_args()
    cache = json.load(open(args.ocr_cache)) if os.path.exists(args.ocr_cache) else {}
    ws = openpyxl.load_workbook(args.xlsx, data_only=True)['验收链接']
    hdr = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    ci = {h: i + 1 for i, h in enumerate(hdr) if h}
    skip_amounts = {float(x) for x in args.no_tier_amounts.split(',') if x.strip()}
    def needs(v):
        return v is not None and float(v) not in skip_amounts
    out = []
    for r in range(2, ws.max_row + 1):
        seq = ws.cell(r, ci['序号']).value
        if seq is None: continue
        shots = [str(ws.cell(r, ci['用户上传截图%d' % i]).value or '').strip() for i in (1, 2, 3)]
        shots = [s for s in shots if s and s != 'None']
        name = ws.cell(r, ci['姓名']).value if '姓名' in ci else None
        home = ws.cell(r, ci['主页链接']).value if '主页链接' in ci else None
        # 模板预留行：姓名/主页/截图全空 → 跳过
        if name in (None, '') and not home and not shots:
            continue
        txts = []
        for s in shots:
            e = cache.get(s)
            if e and 'txt' in e: txts.append(e['txt'])
        kinds = sorted({classify(t) for t in txts}) or ['无截图/未OCR']
        inc_h = '激励金额（含税）' if '激励金额（含税）' in ci else None
        inc = ws.cell(r, ci[inc_h]).value if inc_h else None
        fee = ws.cell(r, ci['验收费用（未税）']).value if '验收费用（未税）' in ci else None
        need_fin = needs(inc) or needs(fee)
        need_int = need_fin
        out.append(dict(seq=seq, name=name, n_shot=len(shots), kinds=kinds, inc=inc, fee=fee,
                        need_fin=need_fin, need_int=need_int,
                        has_data=any(DATA in t for t in txts),
                        has_finish=any(FIN.search(t) for t in txts),
                        has_inter=any(INTER.search(t) for t in txts)))
    print(f'总行 {len(out)}')
    no_data = [x for x in out if not x['has_data']]
    print(f'\n【一】无「作品数据详情页」证据的行(混源风险, 须字段级比对): {len(no_data)}')
    print('   ', [x['seq'] for x in no_data])
    for title, flag in (('【二】需判档位但截图无完播率读数', 'has_finish'), ('【三】需判档位但截图无互动数据', 'has_inter')):
        bad = [x for x in out if (x['need_fin'] if flag == 'has_finish' else x['need_int']) and not x[flag]]
        print(f'\n{title}: {len(bad)} 行 → 人工确认项候选')
        for x in bad:
            need = [n for n, k in (('完播率', x['has_finish']), ('互动率', x['has_inter'])) if not k]
            print(f"  序号{x['seq']} {x['name']} (激励{x['inc']}/费用{x['fee']}): 截图{x['n_shot']}张 {x['kinds']}")
            print(f"     建议: 截图均为{'、'.join(x['kinds'])}，无作品数据详情页，{'/'.join(need)}未获截图核验，建议人工复核")
    if args.json:
        json.dump(out, open(args.json, 'w'), ensure_ascii=False, indent=1); print('\n明细已写入', args.json)

if __name__ == '__main__':
    main()
