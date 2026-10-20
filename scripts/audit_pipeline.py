#!/usr/bin/env python3
"""
yanshou-audit 审核流水线（一次命令跑完整流程）
用法: python3 audit_pipeline.py <验收文件.xlsx|xls>
流程: 预检 → 完播率口径检测 → OCR(缓存+并发) → 三层链接检查 → 判定 → 生成交付物 → 归档PO文件夹 → audit_state.json
规则来源: config/rules.example.json（复制为 config/rules.local.json 后修改，用 --rules 指定）
"""
import argparse, json, os, re, subprocess, sys, time, concurrent.futures, shutil
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import xlrd

# ---------- 配置 ----------
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_SWIFT = os.path.join(SCRIPT_DIR, 'ocr_batch.swift')
CACHE_PATH = os.path.expanduser('~/.cache/yanshou_ocr.json')
CACHE_TTL = 7 * 24 * 3600   # 缓存保留7天
TMP_ROOT = '/tmp/yanshou_audit'
CONCURRENCY = 8   # 实测: 4并发约50张/分 → 8并发约89张/分（带宽拐点，再高无收益）
DL_DELAY = 0.6
OCR_BATCH = 30

# ---------- 规则单一事实源 ----------
# 示例档位（仅用于演示）。请复制 config/rules.example.json 为 config/rules.local.json 后按项目修改。
TIER = {
    'base100': dict(label='基础激励档（链接可打开 + 时长≥15秒）'),
    'base':    dict(play=10000,   interact=0.005, finish=5,  label='基础档（播放≥10000，互动率≥0.5%，完播率≥5%）'),
    'good':    dict(play=50000,   interact=0.005, finish=8,  label='优质档（播放≥50000，互动率≥0.5%，完播率≥8%）'),
    'hot':     dict(play=1000000, interact=0.001, finish=20, label='爆款档（播放≥1000000，互动率≥0.1%，完播率≥20%）'),
}
INC_TIER = {100: 'base100', 300: 'base', 500: 'good', 1000: 'hot'}
FEE_TIER = {500: 'base', 1000: 'good', 1500: 'hot'}


def apply_rules(path=None):
    """从 JSON 覆盖档位配置；不传则保持示例默认值。"""
    global TIER, INC_TIER, FEE_TIER
    if not path:
        return
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    if 'tiers' in cfg:
        TIER = {k: dict(v) for k, v in cfg['tiers'].items()}
    if 'incentive_tiers' in cfg:
        INC_TIER = {int(k): v for k, v in cfg['incentive_tiers'].items()}
    if 'fee_tiers' in cfg:
        FEE_TIER = {int(k): v for k, v in cfg['fee_tiers'].items()}
    for name, t in TIER.items():
        if 'label' not in t:
            raise ValueError(f'规则 {name} 缺少字段 label')
        if name == 'base100':
            continue
        for key in ('play', 'interact', 'finish'):
            if key not in t:
                raise ValueError(f'规则 {name} 缺少字段 {key}')
    for map_name, mapping in (('incentive_tiers', INC_TIER), ('fee_tiers', FEE_TIER)):
        for amount, name in mapping.items():
            if name not in TIER:
                raise ValueError(f'{map_name} 金额 {amount} 引用了不存在的档位 {name}')


def rules_summary():
    """生成留痕里的规则摘要（随配置变化）。"""
    def tier_desc(name):
        # label 由规则文件提供，约定包含档位阈值说明
        return TIER[name]['label']

    groups = {}
    for amount, name in sorted(INC_TIER.items()):
        groups.setdefault(name, []).append(amount)
    a3 = []
    for name, amounts in groups.items():
        amt = '/'.join(str(a) for a in amounts)
        if name == 'base100':
            a3.append(f'{amt}元→{TIER[name]["label"]}')
        else:
            a3.append(f'{amt}元→{tier_desc(name)}')
    a3 = '审核3（激励档位）：' + '；'.join(a3) + '；激励0元=放弃激励，仅审核其他项'

    fee_groups = {}
    for amount, name in sorted(FEE_TIER.items()):
        fee_groups.setdefault(name, []).append(amount)
    a4 = '审核4（验收费用）：' + '；'.join(
        f'{"/".join(str(a) for a in amounts)}元→{tier_desc(name)}'
        for name, amounts in fee_groups.items()
    ) + '；税率须为6%且含税小计=验收费用(未税)×1.06'
    return a3, a4

# ---------- 样式 ----------
HDR_FILL = PatternFill('solid', fgColor='1F4E79')
HDR_FONT = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
BODY_FONT = Font(name='微软雅黑', size=10)
BOLD_FONT = Font(name='微软雅黑', size=10, bold=True)
RED_FONT = Font(name='微软雅黑', size=10, bold=True, color='C00000')
GREEN_FONT = Font(name='微软雅黑', size=10, bold=True, color='008000')
ORANGE_FONT = Font(name='微软雅黑', size=10, bold=True, color='BF8F00')
LINK_FONT = Font(name='微软雅黑', size=9, color='0563C1', underline='single')
ALT_FILL = PatternFill('solid', fgColor='F2F7FB')
INFO_FILL = PatternFill('solid', fgColor='DCE6F1')
MANUAL_FILL = PatternFill('solid', fgColor='FFF2CC')
THIN = Side(style='thin', color='B0B7C3')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
LEFT_AL = Alignment(horizontal='left', vertical='center', wrap_text=True)

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

def vfont(v):
    return RED_FONT if v == '不通过' else (ORANGE_FONT if v == '待复核' else GREEN_FONT)

# ---------- 读取 ----------
def load_source(path):
    """返回 (rows, is_xls)。row标准字段见 dict。"""
    is_xls = path.endswith('.xls')
    rows = []
    if is_xls:
        rb = xlrd.open_workbook(path)
        ws = rb.sheet_by_name('验收链接')
        n = ws.nrows  # xls: range(1, n) 覆盖全部数据行
        def get(r, c): return ws.cell_value(r, c)
        def dt(r, c):
            v = get(r, c)
            return xlrd.xldate_as_datetime(v, rb.datemode) if isinstance(v, float) else v
    else:
        ws = load_workbook(path)['验收链接']
        n = ws.max_row + 1  # xlsx: range(2, n) 覆盖 2..max_row
        def get(r, c): return ws.cell(r, c).value
        def dt(r, c): return get(r, c)
    for r in range(1 if is_xls else 2, n):
        if is_xls:
            if get(r, 0) in (None, ''):
                continue  # 跳过空行(模板预留行)
            seq = r
        else:
            sv = get(r, 1)
            if sv is None or str(sv).strip() == '':
                continue  # 跳过空行(模板预留行)
            seq = int(sv)
        row = dict(
            seq=seq, src_r=r, is_xls=is_xls, name=get(r, 1) if is_xls else get(r, 2),
            nick=get(r, 5) if is_xls else get(r, 6),
            task=get(r, 8) if is_xls else get(r, 9), sub=get(r, 9) if is_xls else get(r, 10),
            fans=get(r, 6) if is_xls else get(r, 7),
            home=str(get(r, 7) if is_xls else get(r, 8) or '').strip(),
            online=dt(r, 10 if is_xls else 11), pub=dt(r, 11 if is_xls else 12),
            pub_link=str(get(r, 13 if is_xls else 14) or '').strip(),
            play=get(r, 14 if is_xls else 15), shot=get(r, 15 if is_xls else 16),
            like=get(r, 16 if is_xls else 17), cmt=get(r, 17 if is_xls else 18),
            rep=get(r, 18 if is_xls else 19), fav=get(r, 19 if is_xls else 20),
            interact=get(r, 20 if is_xls else 21), finish_col=get(r, 21 if is_xls else 22),
            dur=get(r, 22 if is_xls else 23),
            s1=str(get(r, 23 if is_xls else 24) or '').strip(),
            s2=str(get(r, 24 if is_xls else 25) or '').strip(),
            s3=str(get(r, 25 if is_xls else 26) or '').strip(),
            inc=get(r, 27 if is_xls else 28), fee=get(r, 28 if is_xls else 29),
            tax=get(r, 29 if is_xls else 30), subtotal=get(r, 30 if is_xls else 31))
        # 模板预留行：序号已预填、但业务字段全空 → 跳过
        if (row['name'] in (None, '') and row['home'] == '' and row['pub_link'] == ''
                and row['play'] is None and row['inc'] is None and row['fee'] is None):
            continue
        rows.append(row)
    return rows, is_xls

# ---------- 预检 ----------
def precheck(rows):
    issues = []
    interact_mismatch = 0
    for x in rows:
        try:
            calc = (x['like'] + x['cmt'] + x['rep'] + x['fav']) / x['shot']
            if abs(calc - x['interact']) > 0.0005:
                interact_mismatch += 1
        except Exception:
            interact_mismatch += 1
        if x['fee'] and abs((x['subtotal'] or 0) - x['fee'] * 1.06) > 0.01:
            issues.append(f"序号{x['seq']} 含税小计≠未税×1.06")
        if x['tax'] is not None and x['tax'] != 0.06:
            issues.append(f"序号{x['seq']} 税率≠6%")
        if not x['home'].startswith('http'):
            issues.append(f"序号{x['seq']} 主页链接缺失")
        if not x['pub_link'].startswith('http'):
            issues.append(f"序号{x['seq']} 发布链接缺失")
    fake = 0
    for x in rows:
        try:
            f1 = (x['like'] + x['cmt'] + x['rep'] + x['fav']) / x['play'] * 100
            if abs(f1 - x['finish_col']) < 0.01:
                fake += 1
        except Exception:
            pass
    calib = '假列(互动率×100)' if fake > len(rows) * 0.5 else '待OCR判定(可能为真实小数或口径异常)'
    remap_note = None
    if interact_mismatch > len(rows) * 0.5:
        remap_note = (f'互动率公式不一致 {interact_mismatch}/{len(rows)} 行——疑似列错位(如某批次)，'
                      f'请人工确认列映射后再审(可检查: 阅读量/点赞/评论/转发/收藏/互动率 是否右移一列)')
    return issues, interact_mismatch, remap_note, calib, fake

# ---------- OCR ----------
def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                cache = json.load(f)
            now = time.time()
            fresh = {k: v for k, v in cache.items() if isinstance(v, dict) and v.get('ts', 0) > now - CACHE_TTL}
            if len(fresh) != len(cache):
                log(f'缓存清理: 移除过期条目 {len(cache) - len(fresh)} (保留7天)')
            return fresh
        except Exception:
            return {}
    return {}

def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, ensure_ascii=False)

def download(url, out):
    for attempt in range(3):
        p = subprocess.run(['curl', '-s', '-o', out, '-w', '%{http_code}', '--max-time', '40', '-A', UA, url],
                           capture_output=True, text=True)
        if p.stdout.strip() == '200' and os.path.getsize(out) > 1000:
            return True
        time.sleep(3 + attempt * 3)
    return False

def parse_ocr(txt):
    segs = [s.strip() for s in txt.split('|') if s.strip()]
    finish = like = cmt = rep = fav = play = None
    status_bad = set()
    for k, seg in enumerate(segs):
        nxt = segs[k + 1] if k + 1 < len(segs) else ''
        if ('完播' in seg or '元播' in seg) and '5s' not in seg and '5S' not in seg and len(seg) < 10:
            m = re.search(r'(\d+(?:\.\d+)?)\s*%', seg)
            if not m:
                for j in range(k + 1, min(k + 4, len(segs))):
                    m = re.search(r'(\d+(?:\.\d+)?)\s*%', segs[j])
                    if m:
                        break
            if m:
                finish = float(m.group(1))
        elif seg == '播放量':
            m = re.match(r'^(\d+(?:\.\d+)?)\s*万$', nxt)
            if m:
                play = float(m.group(1)) * 10000
            else:
                m2 = re.match(r'^(\d+)$', nxt)
                if m2:
                    play = float(m2.group(1))
        elif seg == '点赞量':
            m = re.match(r'^(\d+)$', nxt)
            if m:
                like = float(m.group(1))
        elif seg == '评论量':
            m = re.match(r'^(\d+)$', nxt)
            if m:
                cmt = float(m.group(1))
        elif seg == '收藏量':
            m = re.match(r'^(\d+)$', nxt)
            if m:
                fav = float(m.group(1))
        elif seg == '分享量':
            m = re.match(r'^(\d+)$', nxt)
            if m:
                rep = float(m.group(1))
        elif '作品状态' in seg and '正常' not in seg and '正常' not in nxt:
            status_bad.add(seg)
        for kw in ('不存在', '已删除', '违规', '下架', '内容被删除'):
            if kw in seg:
                status_bad.add(kw)
    return finish, like, cmt, rep, fav, play, status_bad

def is_shot(s):
    """截图来源: http URL 或本地补图路径"""
    return s.startswith('http') or (len(s) > 4 and os.path.exists(s))

def run_ocr(rows):
    cache = load_cache()
    urls = {}
    for x in rows:
        for s in (x['s1'], x['s2'], x['s3']):
            if is_shot(s):
                urls.setdefault(s, []).append(x['seq'])
    log(f'去重截图URL: {len(urls)}')
    os.makedirs(TMP_ROOT, exist_ok=True)
    results = {}
    idx, to_dl = {}, []
    for url in urls:
        if url in cache:
            results[url] = cache[url]
        else:
            i = len(idx)
            idx[i] = url
            to_dl.append((i, url))
    def dl_one(item):
        i, url = item
        if os.path.exists(url):
            return i, url  # 本地补图: 无需下载,直接OCR
        ext = (url.split('.')[-1].split('?')[0] or 'jpg')
        out = f'{TMP_ROOT}/img{i}.{ext}'
        ok = download(url, out)
        time.sleep(DL_DELAY)
        return i, out if ok else None
    log(f'需下载: {len(to_dl)}, 缓存命中: {len(urls) - len(to_dl)}')
    files = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for i, out in ex.map(dl_one, to_dl):
            if out:
                files[out] = idx[i]
    log(f'下载成功: {len(files)}')
    paths = list(files.keys())
    for b in range(0, len(paths), OCR_BATCH):
        batch = paths[b:b + OCR_BATCH]
        p = subprocess.run(['swift', OCR_SWIFT] + batch, capture_output=True, text=True, timeout=400)
        for line in p.stdout.splitlines():
            if not line.startswith('TXT\t'):
                continue
            _, fpath, txt = line.split('\t', 2)
            url = files.get(fpath)
            if not url:
                continue
            cache[url] = {'txt': txt, 'ts': time.time()}
            results[url] = cache[url]
        log(f'OCR {min(b + OCR_BATCH, len(paths))}/{len(paths)}')
    save_cache(cache)
    for x in rows:
        best, best_score = {}, -1
        for s in (x['s1'], x['s2'], x['s3']):
            if s in results:
                entry = results[s]
                if 'txt' in entry:
                    fin, lk, cm, rp, fv, pl, sb = parse_ocr(entry['txt'])
                    parsed = {'finish': fin, 'like': lk, 'cmt': cm, 'rep': rp, 'fav': fv, 'play': pl,
                              'status_bad': sorted(sb)}
                else:
                    parsed = entry
                # 取解析字段最全的截图(浏览页/无数据页不会覆盖数据页)
                score = sum(1 for k in ('finish', 'like', 'cmt', 'rep', 'fav', 'play') if parsed.get(k) is not None)
                if score > best_score:
                    best, best_score = parsed, score
        x['ocr'] = best
    return rows

# ---------- 链接检查(L1) ----------
LINK_CACHE_PATH = os.path.expanduser('~/.cache/yanshou_links.json')
LINK_TTL = 24 * 3600   # 链接状态缓存24h(同日重跑/同批补丁重跑秒级命中,避免每轮全量curl)

def load_link_cache():
    if os.path.exists(LINK_CACHE_PATH):
        try:
            with open(LINK_CACHE_PATH) as f:
                cache = json.load(f)
            now = time.time()
            return {k: v for k, v in cache.items() if v.get('ts', 0) > now - LINK_TTL}
        except Exception:
            return {}
    return {}

def save_link_cache(cache):
    os.makedirs(os.path.dirname(LINK_CACHE_PATH), exist_ok=True)
    with open(LINK_CACHE_PATH, 'w') as f:
        json.dump(cache, f)

def check_links(rows, verify_db=None):
    """L1 链接结构检查（改造：去掉批量 curl 突发）
    - 主页/发布链接的"可达性"不再用 curl 判定：抖音对所有无登录态请求统一返回反爬壳页(HTTP 200)，
      200 无意义且会形成请求突发。真实可用性由 link_verify.py 的渲染核验承担（结果经 --verify-db 注入）
    - 发布链接仅做 19 位 video id 正则校验（0 请求）；4 条 v.douyin.com 短链用 resolve_short 解析（缓存后不再请求）
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from verify_common import norm_url, resolve_short
    n_video = n_short = n_unsup = 0
    for x in rows:
        x['home_ok'] = bool(str(x['home']).startswith('http'))
        kind, key, canon = norm_url(x['pub_link'])
        if kind == 'video':
            x['pub_ok'] = True; x['pub_pat'] = True; n_video += 1
        elif kind == 'short':
            vid = resolve_short(x['pub_link'], db=verify_db) if verify_db else None
            x['pub_ok'] = True; x['pub_pat'] = bool(vid); n_short += 1
        else:
            x['pub_ok'] = False; x['pub_pat'] = False; n_unsup += 1
    log(f'链接结构检查(0请求): 视频直链{n_video} 短链{n_short} 非抖音{n_unsup}; 主页格式缺失{sum(1 for x in rows if not x["home_ok"])}')
    return rows

# ---------- 判定 ----------
def check_tier(play, interact, finish, tier):
    msgs = []
    if play is None:
        msgs.append('无播放量数据')
    elif play < tier['play']:
        msgs.append(f'播放量{play:,.0f}<{tier["play"]:,}')
    if interact is None:
        msgs.append('无互动率数据')
    elif interact < tier['interact']:
        msgs.append(f'互动率{interact:.4f}<{tier["interact"]}')
    if finish is None:
        msgs.append('无完播率(截图缺失/无法OCR)')
    elif finish < tier['finish']:
        msgs.append(f'完播率{finish}<{tier["finish"]}')
    return msgs

def audit_tier(play, interact, finish, tier):
    """返回 (verdict, reason)：缺数据→待复核，数据不达标→不通过"""
    missing = []
    if play is None:
        missing.append('播放量')
    if interact is None:
        missing.append('互动率')
    if finish is None:
        missing.append('完播率')
    if missing:
        return '待复核', f'缺数据({"/".join(missing)})无法核验,{tier["label"]}'
    msgs = check_tier(play, interact, finish, tier)
    return ('通过' if not msgs else '不通过'), (';'.join(msgs) if msgs else '达标') + f';{tier["label"]}'

def calibrate_finish(rows, fake_col):
    """OCR后判定完播率取值口径: 'col100'(列值×100) / 'ocr'(截图值)"""
    if fake_col:
        return 'ocr'  # 假列(互动率×100)不可用
    match = both = 0
    for x in rows:
        ov = x['ocr'].get('finish')
        cv = x['finish_col']
        if ov is not None and cv is not None:
            both += 1
            if abs(cv * 100 - ov) < 1.0:
                match += 1
    if both and match / both > 0.7:
        return 'col100'  # 真列(小数形式真实完播率)
    return 'ocr'  # 口径异常,以截图为准

def audit_rows(rows, finish_mode):
    for x in rows:
        fans, play, interact, dur = x['fans'], x['play'], x['interact'], x['dur']
        inc, fee = x['inc'], x['fee']
        if finish_mode == 'col100':
            finish = round(x['finish_col'] * 100, 2) if x['finish_col'] is not None else None
            if finish is None and x['ocr'].get('finish'):
                finish = x['ocr']['finish']
        else:
            finish = x['ocr'].get('finish')
        x['finish_use'] = finish
        if fans is None:
            a1 = '待复核'
        else:
            a1 = '通过' if fans >= 1000 and x['home_ok'] else '不通过'
        online, pub = x['online'], x['pub']
        if online is None or pub is None:
            a2, a2n = '待复核', '缺发布时间/上线时间，无法判定'
            x['hours'] = None
        else:
            try:
                hours = (pub - online).total_seconds() / 3600
            except Exception:
                hours = (pub - online) * 24 if isinstance(pub, (int, float)) else 0
            x['hours'] = round(hours, 2)
            if dur is None:
                a2, a2n = '待复核', '缺作品时长，无法判定'
            else:
                ok2 = pub > online and hours <= 72 and dur >= 15
                a2 = '通过' if ok2 else '不通过'
                a2n = (f'发布间隔{hours:.1f}h，时长{dur}秒' if ok2
                       else f'发布间隔{hours:.1f}h/时长{dur}秒，不满足')
        if inc is None:
            a3, a3n = '待复核', '缺激励档位数据(激励金额（含税）未填)，无法判定'
        else:
            inc_key = int(inc)
            if inc_key == 0:
                a3, a3n = '通过', '无激励(放弃激励)，不适用档位审核'
            elif inc_key in INC_TIER:
                tn = INC_TIER[inc_key]
                if tn == 'base100':
                    ok = x['home_ok'] and x['pub_ok'] and dur is not None and dur >= 15
                    a3 = '通过' if ok else '不通过'
                    a3n = (f'{TIER[tn]["label"]}: 链接可打开✓ 时长{dur}秒≥15✓'
                           if ok else f'{TIER[tn]["label"]}要求未满足')
                else:
                    a3, reason = audit_tier(play, interact, finish, TIER[tn])
                    a3n = reason + f';激励{inc_key}元对应{TIER[tn]["label"]}'
            else:
                a3, a3n = '不通过', f'激励{inc_key}元无对应档位'
        if fee is None:
            a4, a4n = '待复核', '缺验收费用数据(未税未填)，无法判定'
        else:
            fee_key = int(fee)
            if fee_key == 0:
                a4, a4n = '通过', '验收费0元,无费用档位需核;税率/含税小计已核'
            elif fee_key not in FEE_TIER:
                a4, a4n = '不通过', f'验收费{fee_key}元无对应档位'
            else:
                tn = FEE_TIER[fee_key]
                a4, reason = audit_tier(play, interact, finish, TIER[tn])
                a4n = reason + f';验收费{fee_key}元对应{TIER[tn]["label"]}'
        tax_msgs = []
        if x['tax'] is not None and x['tax'] != 0.06:
            tax_msgs.append(f'税率{x["tax"]}≠6%')
        if fee and abs((x['subtotal'] or 0) - fee * 1.06) > 0.01:
            tax_msgs.append(f'含税小计{x["subtotal"]}≠{fee}*1.06')
        if tax_msgs:
            a4, a4n = '不通过', a4n + ';' + ';'.join(tax_msgs)
        x.update(a1=a1, a2=a2, a3=a3, a4=a4, a2n=a2n, a3n=a3n, a4n=a4n)
    return rows

# ---------- 生成输出 ----------
def build_output(src_path, rows, is_xls, out_path, manual, rule_ver, calib, extra_notes=None, extra_sections=None, link_status=None, annotations=None):
    extra_notes = extra_notes or {}
    link_status = link_status or {}
    wb = Workbook()
    ws = wb.active
    ws.title = '验收链接'
    # 源表列: xls=35列(1-35: 序号..审核4), xlsx=37列(1-37: 序号..人工确认项)
    if is_xls:
        rb_src = xlrd.open_workbook(src_path)
        sh = rb_src.sheet_by_name('验收链接')
        NC_SRC = sh.ncols
        headers = [sh.cell_value(0, c) for c in range(sh.ncols)]
        def getv(x, c0):
            return rb_src.sheet_by_name('验收链接').cell_value(x['src_r'], c0)
    else:
        src_wb = load_workbook(src_path)
        sh = src_wb['验收链接']
        NC_SRC = sh.max_column
        headers = [sh.cell(1, c).value for c in range(1, NC_SRC + 1)]
        def getv(x, c0):
            return src_wb['验收链接'].cell(x['src_r'], c0 + 1).value
    # 追加列(按源表是否已有OCR/人工确认列)
    add = []
    if NC_SRC < 36:
        add += ['完播率(截图OCR)', '人工确认项']
    if NC_SRC < 38:
        add.append('OCR互动率')
    headers = headers + add
    NC = len(headers)
    OCR_COL = NC_SRC + 1 + add.index('完播率(截图OCR)') if '完播率(截图OCR)' in add else (36 if NC_SRC >= 36 else None)
    MAN_COL = NC_SRC + 1 + add.index('人工确认项') if '人工确认项' in add else (37 if NC_SRC >= 37 else None)
    OCRINT_COL = NC
    VERD_COLS = (32, 33, 34, 35) if NC_SRC >= 35 else (31, 32, 33, 34)
    for c, h in enumerate(headers, 1):
        cell = ws.cell(1, c, h)
        cell.font, cell.fill, cell.alignment, cell.border = HDR_FONT, HDR_FILL, CENTER, BORDER
    ws.row_dimensions[1].height = 28
    CENTER_COLS = set(range(1, NC + 1)) - {2, 5, 6, 9, 14, 37, 38, 39, 40}
    for i, x in enumerate(rows):
        r = i + 2
        manual_txt = manual.get(x['seq'], '')
        for c in range(1, NC + 1):
            if c in VERD_COLS:
                v = x[f'a{c - VERD_COLS[0] + 1}']
            elif c == OCR_COL:
                v = f'{x["finish_use"]}%' if x['finish_use'] is not None else '无'
            elif c == MAN_COL:
                v = manual_txt
            elif c == OCRINT_COL:
                ocr = x.get('ocr') or {}
                if ocr.get('like') is not None and ocr.get('play'):
                    # 评论/转发/收藏为0时页面可能不显示数值, 缺失按0处理
                    oi = (ocr['like'] + (ocr['cmt'] or 0) + (ocr['rep'] or 0) + (ocr['fav'] or 0)) / ocr['play']
                    v = f'{oi*100:.2f}%(赞{int(ocr["like"])}/评{int(ocr["cmt"] or 0)}/转{int(ocr["rep"] or 0)}/藏{int(ocr["fav"] or 0)})'
                else:
                    v = '未解析(截图无互动数据)'
            elif c <= NC_SRC:
                v = getv(x, c - 1)
            else:
                v = None
            cell = ws.cell(r, c)
            if c == 1:
                v = x['seq']
            elif c == 3 and isinstance(v, float):
                v = int(v)
            cell.value = v
            cell.border = BORDER
            cell.font = BODY_FONT
            if c in VERD_COLS:
                cell.alignment = CENTER
                cell.font = vfont(v)
            elif c == OCR_COL:
                cell.alignment = CENTER
                cell.font = ORANGE_FONT if str(v) == '无' else BODY_FONT
            elif c == MAN_COL:
                cell.alignment = LEFT_AL
                if v:
                    cell.font = ORANGE_FONT
                    cell.fill = MANUAL_FILL
            elif c == OCRINT_COL:
                cell.alignment = LEFT_AL
                cell.font = ORANGE_FONT if '未解析' in str(v) else BODY_FONT
            elif c in (11, 12):
                cell.alignment = CENTER
                if isinstance(v, datetime):
                    cell.number_format = 'yyyy-mm-dd hh:mm'
            elif c in CENTER_COLS:
                cell.alignment = CENTER
            else:
                cell.alignment = LEFT_AL
            if i % 2 == 1 and not (c == MAN_COL and v):
                cell.fill = ALT_FILL
            if c in (8, 14, 24, 25, 26) and isinstance(v, str) and v.startswith('http'):
                cell.hyperlink = v
                cell.font = LINK_FONT
        ws.row_dimensions[r].height = 22
    widths = [6, 10, 14, 10, 17, 18, 10, 42, 30, 10, 17, 17, 11, 26, 13, 13, 12, 11, 11, 12, 9, 9, 10, 36, 36, 36, 13, 12, 13, 8, 12, 9, 9, 9, 9, 16, 42, 30]
    for c, w in enumerate(widths[:NC], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{get_column_letter(NC)}1'

    # 审核规则
    ws2 = wb.create_sheet('审核规则')
    if is_xls:
        sr = xlrd.open_workbook(src_path).sheet_by_name('审核规则')
        for r in range(sr.nrows):
            for c in range(sr.ncols):
                v = sr.cell_value(r, c)
                if v != '':
                    cell = ws2.cell(r + 1, c + 1, v)
                    cell.font = BODY_FONT
                    cell.alignment = LEFT_AL
                    cell.border = BORDER
    else:
        sr = load_workbook(src_path)['审核规则']
        for r in range(1, sr.max_row + 1):
            for c in range(1, sr.max_column + 1):
                v = sr.cell(r, c).value
                if v is not None and v != '':
                    cell = ws2.cell(r, c, v)
                    cell.font = BODY_FONT
                    cell.alignment = LEFT_AL
                    cell.border = BORDER
    ws2.column_dimensions['A'].width = 16
    ws2.column_dimensions['B'].width = 100
    for r in range(1, ws2.max_row + 1):
        ws2.row_dimensions[r].height = 30
    for c in (1, 2):
        ws2.cell(1, c).font = HDR_FONT
        ws2.cell(1, c).fill = HDR_FILL
        ws2.cell(1, c).alignment = CENTER

    # 留痕
    ws3 = wb.create_sheet('AI审核留痕')
    NCOL = 16
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ws3.merge_cells(start_row=1, start_column=1, end_row=1, end_column=NCOL)
    t = ws3.cell(1, 1, 'AI审核留痕证明')
    t.font = Font(name='微软雅黑', size=16, bold=True, color='1F4E79')
    t.fill = INFO_FILL
    t.alignment = CENTER
    ws3.row_dimensions[1].height = 36
    for c in range(1, NCOL + 1):
        ws3.cell(1, c).border = BORDER
    tool_txt = 'Claude Code + yanshou-audit pipeline (macOS Vision OCR)'
    if (annotations or {}).get('cost_note'):
        tool_txt += f"｜{annotations['cost_note']}"
    info = [('审核时间：', now), ('审核工具：', tool_txt),
            ('源文件：', src_path), ('规则版本：', rule_ver)]
    for i, (k, v) in enumerate(info):
        r = 2 + i
        kc = ws3.cell(r, 1, k)
        kc.font, kc.fill, kc.border = BOLD_FONT, INFO_FILL, BORDER
        kc.alignment = CENTER
        ws3.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOL)
        vc = ws3.cell(r, 2, v)
        vc.font, vc.border, vc.alignment = BODY_FONT, BORDER, LEFT_AL
        for c in range(2, NCOL + 1):
            ws3.cell(r, c).border = BORDER
        ws3.row_dimensions[r].height = 20
    row = 7
    def section(title, items):
        nonlocal row
        ws3.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOL)
        t = ws3.cell(row, 1, title)
        t.font, t.fill, t.alignment = HDR_FONT, HDR_FILL, LEFT_AL
        for c in range(1, NCOL + 1):
            ws3.cell(row, c).border = BORDER
        ws3.row_dimensions[row].height = 24
        row += 1
        for it in items:
            ws3.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOL)
            cell = ws3.cell(row, 1, it)
            cell.font, cell.alignment = BODY_FONT, LEFT_AL
            for c in range(1, NCOL + 1):
                ws3.cell(row, c).border = BORDER
            est = max(1, (len(it) + 100) // 100)
            ws3.row_dimensions[row].height = max(20, est * 20 + 6)
            row += 1
        row += 1
    a3_text, a4_text = rules_summary()
    sections = [
        ('一、审核规则（规则文件）', [
            '审核1：主页链接可正常打开 且 粉丝数量≥1000',
            '审核2：发布时间晚于任务上线时间 且 间隔≤72小时 且 作品时长≥15秒',
            a3_text,
            a4_text,
            f'[口径] 完播率列口径: {calib}；互动率用表内列(预检公式复核)；播放量以系统抓取列为准；三层链接检查(L1 URL规律/L2截图状态/L3人工抽查)',
        ]),
        ('二、数据验证记录', [
            '互动率公式/含税小计/税率/72h/时长 预检记录见控制台与audit_state.json',
            'OCR互动率采样规则：互动率<1%行全量OCR复核，其余行随机抽20-30%（本批实际覆盖见OCR互动率列）',
        ]),
        ('三、审核结果汇总', [
            f'审核1-4判定结果见验收链接sheet；人工确认项{len(manual)}行（见"人工确认项"列）',
            f'规则版本: {rule_ver}；audit_state.json 已存档于本PO文件夹',
        ]),
    ]
    for t, items in (extra_sections or []):
        sections.append((t, items))
    for title, items in sections:
        section(title, items)
    row += 1
    hdr3 = ['序号', '姓名', '子任务ID', '任务名称', '发布链接', '链接有效性', '涉及验收费用', '播放量', '互动率', '完播率(截图OCR)', '审核1', '审核2', '审核3', '审核4', '人工确认项', '备注']
    for c, h in enumerate(hdr3, 1):
        cell = ws3.cell(row, c, h)
        cell.font, cell.fill, cell.alignment, cell.border = HDR_FONT, HDR_FILL, CENTER, BORDER
    ws3.row_dimensions[row].height = 26
    row += 1
    for x in rows:
        notes = ' | '.join(n for n in (x.get('a2n'), x['a3n'], x['a4n']) if n)
        if extra_notes.get(x['seq']):
            notes = (notes + ' | ' + extra_notes[x['seq']]) if notes else extra_notes[x['seq']]
        finish_txt = f'{x["finish_use"]}%' if x['finish_use'] is not None else '-'
        lk = link_status.get(str(x['seq']), '未复核')
        fee_v = x.get('fee')
        fee_txt = f'是({int(fee_v)}元)' if isinstance(fee_v, (int, float)) and fee_v > 0 else ('否' if fee_v == 0 else '缺数据')
        vals = [x['seq'], x['name'], x['sub'], x['task'], x['pub_link'], lk, fee_txt,
                x['play'], round(x['interact'], 4) if x['interact'] is not None else None, finish_txt,
                x['a1'], x['a2'], x['a3'], x['a4'], manual.get(x['seq'], ''), notes]
        for c, v in enumerate(vals, 1):
            cell = ws3.cell(row, c, v)
            cell.border = BORDER
            cell.font = BODY_FONT
            cell.alignment = CENTER if c not in (4, 5, 15, 16) else LEFT_AL
            if c in (11, 12, 13, 14):
                cell.font = vfont(v)
            if c == 6 and isinstance(v, str) and v.startswith('失效'):
                cell.font = RED_FONT
            elif c == 6 and v == '未复核':
                cell.font = ORANGE_FONT
            if c == 15 and v:
                cell.font = ORANGE_FONT
                cell.fill = MANUAL_FILL
            if (row - 2) % 2 == 1:
                cell.fill = ALT_FILL
        if isinstance(vals[4], str) and vals[4].startswith('http'):
            ws3.cell(row, 5).hyperlink = vals[4]
            ws3.cell(row, 5).font = LINK_FONT
        ws3.row_dimensions[row].height = 20
        row += 1
    for c, w in enumerate([6, 10, 10, 32, 30, 18, 13, 10, 9, 14, 8, 8, 8, 8, 42, 60], 1):
        ws3.column_dimensions[get_column_letter(c)].width = w
    wb.save(out_path)
    return out_path

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--rule-ver', default='v1.0')
    ap.add_argument('--rules', default=None, help='档位规则 JSON（见 config/rules.example.json）')
    ap.add_argument('--verify-db', default=None, help='核验结果库(verify_db.jsonl)：注入 链接有效性 + 覆盖粉丝数')
    ap.add_argument('--annotations', default=None,
                    help='JSON: {"manual":{seq:文本},"notes":{seq:文本},"extra_sections":[[标题,[行...]],...]}')
    args = ap.parse_args()
    apply_rules(args.rules)
    src = os.path.abspath(args.file)
    if not os.path.exists(src):
        log(f'文件不存在: {src}')
        sys.exit(1)
    m = re.search(r'PO(\d+)', os.path.basename(src))
    base_dir = os.path.dirname(src)
    po_name = f'PO{m.group(1)}' if m else None
    if po_name and os.path.basename(base_dir) == po_name:
        po_dir = base_dir  # 源文件已在PO文件夹内
    elif po_name:
        po_dir = os.path.join(base_dir, po_name)
    else:
        po_dir = base_dir
    os.makedirs(po_dir, exist_ok=True)
    log(f'=== 审核开始: {os.path.basename(src)} ===')
    rows, is_xls = load_source(src)
    log(f'数据行: {len(rows)}')
    issues, im, remap_note, calib, fake = precheck(rows)
    # 同类警告汇总输出(如整列缺失126条→1条),避免日志刷屏
    from collections import Counter as _C
    grouped = _C()
    seq_by_kind = {}
    for it in issues:
        m = re.match(r'^序号(\d+) (.*)$', it)
        kind = m.group(2) if m else it
        grouped[kind] += 1
        seq_by_kind.setdefault(kind, []).append(int(m.group(1)) if m else 0)
    for kind, n in grouped.items():
        seqs = sorted(seq_by_kind[kind])
        if n > 5:
            log(f'⚠️ 预检: {kind} ×{n}行(序号{seqs[0]}~{seqs[-1]})')
        else:
            for s in seqs:
                log(f'⚠️ 预检: 序号{s} {kind}')
    if remap_note:
        log(f'⚠️ {remap_note}')
        sys.exit(2)
    log(f'完播率列口径: {calib} (互动率×100吻合{fake}/{len(rows)})')
    rows = run_ocr(rows)
    finish_mode = calibrate_finish(rows, fake > len(rows) * 0.5)
    if calib.startswith('待OCR'):
        calib = f"真实小数列(列值×100≈OCR, {finish_mode == 'col100'})" if finish_mode == 'col100' else '口径异常(以截图OCR为准)'
    log(f'完播率取值口径: {finish_mode} ({calib})')
    vdb = None
    if args.verify_db and os.path.exists(args.verify_db):
        from verify_common import DB as _VDB
        vdb = _VDB(path=args.verify_db)
        log(f'核验库: {args.verify_db} ({len(vdb.recs)} 条记录)')
    rows = check_links(rows, verify_db=vdb)
    if vdb:   # 用核验库覆盖粉丝数（免改源表）与注入链接有效性
        n_fans = 0
        for x in rows:
            m = re.search(r'sec_uid=([\w\-]+)', x['home'])
            if not m: continue
            rec = vdb.get(f's:{m.group(1)}', max_age=10**9)
            if rec and rec.get('fans'):
                x['fans'] = rec['fans']; n_fans += 1
        log(f'粉丝数按核验库覆盖: {n_fans} 行')
    rows = audit_rows(rows, finish_mode)
    from collections import Counter
    for k in ('a1', 'a2', 'a3', 'a4'):
        log(f'{k}: ' + ' '.join(f'{kk}={v}' for kk, v in Counter(x[k] for x in rows).items()))
    manual = {}
    def add_manual(seq, note):
        if seq in manual:
            manual[seq] = manual[seq] + '；' + note
        else:
            manual[seq] = note
    for x in rows:
        if not x['pub_link'].startswith('http'):
            add_manual(x['seq'], '发布链接缺失，需人工补充')
        elif not x['pub_pat']:
            add_manual(x['seq'], '发布链接URL格式异常(非douyin.com/video/数字)，需人工核验')
        if not x['home'].startswith('http'):
            add_manual(x['seq'], '主页链接缺失，需人工补充(审核1可达性无法核验)')
        if x['fans'] is None:
            add_manual(x['seq'], '粉丝数量缺失，需补充后判定审核1(主页粉丝≥1000)')
        if x['inc'] is None:
            add_manual(x['seq'], '激励金额（含税）缺失，需补充后判定激励档位(审核3)')
        if x['fee'] is None:
            add_manual(x['seq'], '验收费用(未税)缺失，需补充后判定费用档位(审核4)')
        if not any(is_shot(s) for s in (x['s1'], x['s2'], x['s3'])) and int(x['inc'] or 0) != 100:
            add_manual(x['seq'], '截图1/2/3均缺失，需人工补充截图')
        if x['finish_use'] is None and not x['ocr'].get('finish'):
            add_manual(x['seq'], '截图无完播率，需人工查看视频后台')
        inc_key = int(x['inc']) if x['inc'] is not None else 0
        if inc_key not in INC_TIER and inc_key != 0:
            add_manual(x['seq'], f'激励{inc_key}元无对应档位，需人工确认(是否无激励任务)')
    anno = {}
    if args.annotations and os.path.exists(args.annotations):
        with open(args.annotations) as f:
            anno = json.load(f)
    for s, txt in (anno.get('manual') or {}).items():
        add_manual(int(s), txt)
    extra_notes = {int(k): v for k, v in (anno.get('notes') or anno.get('finish_fix_note') or {}).items()}
    extra_sections = anno.get('extra_sections') or []
    link_status = anno.get('link_status') or {}
    if vdb:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from verify_common import norm_url as _nu, DISPLAY as _DISP
        auto = 0
        for x in rows:
            _kind, _key, _ = _nu(x['pub_link'])
            if not _key: continue
            _rec = vdb.get(_key, max_age=10 ** 9)
            if _rec and _rec.get('status'):
                link_status[str(x['seq'])] = _DISP.get(_rec['status'], '未复核'); auto += 1
        if auto:
            log(f'链接有效性列: 核验库注入 {auto} 行')
    for x in rows:
        x['link_status'] = link_status.get(str(x['seq']), '未复核')
    base = re.sub(r'\(\d+\)', '', os.path.splitext(os.path.basename(src))[0])
    out_name = f'{base}-AI审核结果-{datetime.now().strftime("%Y%m%d")}.xlsx'
    out_path = os.path.join(po_dir, out_name)
    build_output(src, rows, is_xls, out_path, manual, args.rule_ver, calib, extra_notes, extra_sections, link_status, anno)
    log(f'已生成: {out_path}')
    state = {
        'po': m.group(1) if m else None, 'source': os.path.basename(src), 'output': out_name,
        'rule_ver': args.rule_ver, 'audited_at': datetime.now().isoformat(),
        'calib': calib, 'precheck_issues': issues,
        'rows': [{k: x[k] for k in ('seq', 'name', 'a1', 'a2', 'a3', 'a4', 'a3n', 'a4n', 'finish_use', 'interact', 'inc', 'fee', 'link_status')} for x in rows],
        'manual': manual,
    }
    with open(os.path.join(po_dir, 'audit_state.json'), 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(po_dir, 'work_rows.json'), 'w') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    log(f'audit_state.json / work_rows.json 已写入 {po_dir}')
    if os.path.dirname(src) != po_dir:
        shutil.move(src, os.path.join(po_dir, os.path.basename(src)))
        log(f'源文件已归档到 {po_dir}')
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    log('=== 完成 ===')

if __name__ == '__main__':
    main()
