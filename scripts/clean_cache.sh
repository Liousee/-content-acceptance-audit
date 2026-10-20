#!/bin/bash
# 清理不必要的缓存（保留全部审计产物）
# 用法: bash clean_cache.sh [PO目录...]      # 不传参则只清全局缓存
#
# 清理对象（都是可再生/无审计价值的）：
#   ① PO*/粉丝核验/ 下的 dompr_*/shotpr_*  —— fans_check 的 Chrome 临时 profile（每批可达 500MB+）
#   ② ~/.cache/yanshou-chrome-w*           —— 批量 Chrome worker 的临时 profile
#   ③ /tmp/yanshou_audit /tmp/ys_adj /tmp/ys_bbox /tmp/ys_pin /tmp/domchk_*
#      —— 下载的截图与中间文件（截图证据清单里的 OSS 链接已留底）
#   ④ /tmp/yanshou-anon-cdp-* /tmp/yanshou-anon-w* —— 免登录 lane 的临时 profile
#
# 绝不删除（审计产物，阶段二要复用）：
#   交付物 xlsx / 原始表 / AI审核底档 / 截图证据清单 / audit_state.json /
#   audit_runlog.jsonl / work_rows.json / diffs.json / ocr_results.json /
#   link_results.json / 链接复核/ / 粉丝核验/*.png *.json / 补图核销/
set -u
before=$(du -sh /tmp 2>/dev/null | cut -f1)

echo "── ① PO 夹内的 Chrome 临时 profile"
for d in "$@"; do
  if [ -d "$d/粉丝核验" ]; then
    sz=$(du -ch "$d"/粉丝核验/dompr_* "$d"/粉丝核验/shotpr_* 2>/dev/null | tail -1 | cut -f1)
    n=$(ls -d "$d"/粉丝核验/dompr_* "$d"/粉丝核验/shotpr_* 2>/dev/null | wc -l | tr -d ' ')
    rm -rf "$d"/粉丝核验/dompr_* "$d"/粉丝核验/shotpr_* 2>/dev/null
    echo "   ${d}/粉丝核验: 删除 ${n} 个临时 profile（${sz}）"
  fi
done

echo "── ② ~/.cache 下的 worker 临时 profile"
sz=$(du -ch ~/.cache/yanshou-chrome-w* 2>/dev/null | tail -1 | cut -f1)
n=$(ls -d ~/.cache/yanshou-chrome-w* 2>/dev/null | wc -l | tr -d ' ')
rm -rf ~/.cache/yanshou-chrome-w* 2>/dev/null
echo "   删除 ${n} 个（${sz}）  ※ 登录 profile ~/.cache/yanshou-chrome 保留不动"

echo "── ③ /tmp 下的截图与中间文件"
for p in /tmp/yanshou_audit /tmp/ys_adj /tmp/ys_bbox /tmp/ys_pin /tmp/yanshou_fans; do
  [ -e "$p" ] && { sz=$(du -sh "$p" 2>/dev/null | cut -f1); rm -rf "$p"; echo "   删除 ${p}（${sz}）"; }
done
rm -rf /tmp/domchk_* /tmp/yanshou-anon-cdp-* /tmp/yanshou-anon-w* /tmp/ys_dist.html 2>/dev/null || true

echo "── 完成。审计产物一律保留。"
