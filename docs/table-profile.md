# Table profiles

`audit_pipeline.py` reads the source table **by column position**. A profile is a mapping
from positions to the row dictionary used by the judgement code.

## Profile A — two sheets, 37 columns

Sheets: `验收链接`, `审核规则`.

| Col | Field | Col | Field |
|---|---|---|---|
| 1 | 序号 | 20 | 用户截图收藏量 |
| 2 | 姓名 | 21 | 互动率 |
| 3 | 联系人（可选） | 22 | 完播率 |
| 4 | 车型 | 23 | 作品时长 |
| 5 | 车辆标识（可选） | 24 | 用户上传截图1 |
| 6 | 昵称 | 25 | 用户上传截图2 |
| 7 | 粉丝数量 | 26 | 用户上传截图3 |
| 8 | 主页链接 | 27 | 内容等级 |
| 9 | 任务名称 | 28 | 激励金额（含税） |
| 10 | 子任务ID | 29 | 验收费用（未税） |
| 11 | 任务上线时间 | 30 | 税率 |
| 12 | 发布时间 | 31 | 含税小计 |
| 13 | 是否72HR内发布 | 32 | 审核1 |
| 14 | 发布链接 | 33 | 审核2 |
| 15 | 系统抓取播放量 | 34 | 审核3 |
| 16 | 用户截图阅读量 | 35 | 审核4 |
| 17 | 用户截图点赞量 | 36 | 完播率(截图OCR)（输出追加） |
| 18 | 用户截图评论 | 37 | 人工确认项（输出追加） |
| 19 | 用户截图转发 | | |

The pipeline supports `.xls` (35 data columns, xlrd dates) and `.xlsx` (37 columns).
Generate a blank workbook with:

```bash
python3 scripts/make_template.py --out 验收审核底表模板.xlsx
```

### Field semantics

| Row key | Source | Notes |
|---|---|---|
| `seq` | col 1 | unique row id |
| `name`, `task`, `sub` | cols 2, 9, 10 | display only |
| `fans` | col 7 | compared with measured value |
| `home` | col 8 | homepage URL |
| `online`, `pub` | cols 11, 12 | datetimes |
| `pub_link` | col 14 | short link or video URL |
| `play` | col 15 | system-captured play count |
| `shot` | col 16 | user screenshot reading count |
| `like`, `cmt`, `rep`, `fav` | cols 17-20 | interaction counts |
| `interact` | col 21 | decimal interaction rate |
| `finish_col` | col 22 | unverified completion value |
| `dur` | col 23 | seconds |
| `s1`, `s2`, `s3` | cols 24-26 | URL or local path |
| `inc` | col 28 | incentive amount (gross) |
| `fee` | col 29 | acceptance fee (net) |
| `tax` | col 30 | must be 0.06 |
| `subtotal` | col 31 | must equal `fee × 1.06` |

## Adapting another layout

1. Copy `load_source()` and change the index mapping.
2. Keep the returned row keys identical — the rest of the pipeline depends on them.
3. If one row has multiple judgment blocks (e.g. four pairs of `验收1/验收2`), do not reuse
   `audit_rows()` directly. Write a profile adapter that:
   - expands each block into a judgment unit
   - calls the same tier logic per unit
   - writes the verdicts back to the original columns
4. Reuse the OCR, link, follower, and evidence tools unchanged.

### Multi-sheet adapter pattern

```
load rows
  → for each row, for each judgment block:
        build a unit dict {play, interact, finish, dur, inc, fee, tax, subtotal}
        call audit_tier() / fee lookup
        write verdicts to the block's columns
  → write manual-confirmation items
```

Keep the block-to-column mapping in a config file rather than hardcoding it. The public repo
does not ship a multi-sheet adapter because those schemas are project-specific; the pattern
above is the intended extension point.

## Rule config

```json
{
  "tiers": {
    "base": {"play": 10000, "interact": 0.005, "finish": 5, "label": "..."}
  },
  "incentive_tiers": {"300": "base"},
  "fee_tiers": {"500": "base"}
}
```

- Interaction rate is a decimal (`0.005` = 0.5%).
- Completion rate is a percentage (`5` = 5%).
- `base100` is the special “link opens + duration ≥15 s” tier with no play/interaction/
  completion thresholds.
- Any amount that is not in the maps goes to manual confirmation.
