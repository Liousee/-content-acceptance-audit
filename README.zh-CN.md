# content-acceptance-audit

**达人内容验收审核工具链：证据优先、人工兜底。**

**Claude Code skill 名称：** `yanshou-audit`

[English](README.md) · [Skill 方法论](SKILL.md) · [安全红线](docs/safety.md)

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

把一张验收表变成可审计的交付物：判定列回填、截图 OCR 交叉核验、链接与粉丝实测、
金额勾稽、证据包与底档。既可作为 [Claude Code](https://claude.com/claude-code) skill
（`SKILL.md`）使用，也可作为独立的 Python/Swift 命令行工具使用。

---

## ⚠️ 先读这一段

本工具会接触**真实平台账号**和**与钱相关的判定**：

- **绝不提交或外发**：登录 profile、Cookie、API Key、原始表、截图、交付物。
  `.gitignore` 已拦截常见路径，但你仍需自己负责。
- **登录态自动化仅限 ≤5 条/批、短会话、慢速、人工在场**；批量初筛一律走免登录 lane。
- **一见真实验证码立即停止**，冷却 ≥30 分钟。
- **不擅自改金额**：发现失效/存疑只标注，出金额影响清单，由业务确认。
- 遵守平台服务条款与当地法律。本项目不提供绕过验证码、规避风控或未授权访问的能力。

完整红线见 [docs/safety.md](docs/safety.md)。

---

## 能力

| 能力 | 产出 |
|---|---|
| **整表审核流水线** | 预检、完播率口径检测、OCR、链接检查、判定、美化工作簿 |
| **截图 OCR 复核** | Vision OCR + 缓存 + 多截图交叉 + bbox 像素级裁决 |
| **链接有效性** | 三层检查（URL 规律 → 截图状态 → 人工抽查）+ 免登录/登录两条 lane |
| **人工复核台** | 小批量开标签由人目视，人工节奏，不盲跑自动化 |
| **粉丝核验** | DOM 主 + 截图 OCR 辅，按核验日实测值判定，证据归档 |
| **证据链** | `audit_state.json`、`work_rows.json`、运行日志（可选）、截图证据清单、SHA256 底档、复现命令 |
| **两阶段审核** | 阶段一激励审核；阶段二 30/60 天后核销复核 |

---

## 环境要求

| 组件 | 用途 | 说明 |
|---|---|---|
| Python 3.9+ | 全部 | `openpyxl`、`xlrd`、`Pillow` |
| Google Chrome | 链接/粉丝核验 | macOS 默认路径；可用 `CHROME_BIN` 覆盖 |
| macOS + Xcode 命令行工具 | OCR、bbox、抽帧 | Swift + Vision/AVFoundation；Linux 只能跑表格/链接逻辑 |
| 抖音账号 | 仅登录态定点复核 | 用自己的账号，绝不共享 profile |

```bash
python3 -m pip install -r requirements.txt
```

---

## 安装

### 作为 Claude Code skill

```bash
git clone https://github.com/Liousee/-content-acceptance-audit.git ~/.claude/skills/yanshou-audit
```

> 脚本用 `python3` 启动。请在该解释器里安装 `requirements.txt` 的依赖
> （启动 Claude Code 前先激活 venv，或使用 `pip --user` 安装）。

### 独立使用

```bash
git clone https://github.com/Liousee/-content-acceptance-audit.git yanshou-audit
cd yanshou-audit
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 scripts/make_template.py --out 验收审核底表模板.xlsx
```

---

## 快速开始

### 1. 生成空白底表

```bash
python3 scripts/make_template.py --out 验收审核底表模板.xlsx
```

填写 `验收链接` sheet（一行一条作品），再把项目规则写入
`config/rules.local.json`（从 `config/rules.example.json` 复制）。

### 2. 跑流水线

```bash
python3 scripts/audit_pipeline.py 验收审核底表模板.xlsx \
  --rules config/rules.local.json
```

输出：`<原名>-AI审核结果-YYYYMMDD.xlsx` + `audit_state.json`。

### 3. 链接核验（先免登录）

```bash
# 批量初筛，不带账号身份
python3 scripts/link_verify.py <表.xlsx> --lane anon --min-fee 1

# 结果并入交付物
python3 scripts/link_review.py <表.xlsx>
```

需要对少量存疑行做决定性确认时，用人工复核台（登录态）：

```bash
bash scripts/login_douyin.sh          # 一次性扫码登录
python3 scripts/link_console.py <表.xlsx> --batch 5 --min-fee 1 --pause 8
```

### 4. 粉丝核验

```bash
python3 scripts/fans_check.py <表.xlsx> --evidence-dir ./粉丝核验 --report
```

### 5. 贴门槛值钉死

```bash
swiftc -O scripts/bbox.swift -o /tmp/bboxbin
python3 scripts/pin_value.py <表.xlsx> 完播率 12 35 101
```

### 6. 裁决、底档与清理

```bash
python3 scripts/adjudicate.py --work-dir ./PO0000000000 12 35
python3 scripts/evidence.py --work-dir ./PO0000000000
python3 scripts/dossier.py --work-dir ./PO0000000000 --rules config/rules.local.json
bash scripts/clean_cache.sh ./PO0000000000
```

---

## 目录结构

```
.
├── SKILL.md                    # Claude Code skill 方法论
├── README.md / README.zh-CN.md
├── config/
│   └── rules.example.json      # 档位配置模板
├── docs/
│   ├── architecture.md         # 流水线、缓存、状态文件
│   ├── safety.md               # 账号/限流红线
│   ├── data-verification.md    # OCR、bbox、已知错值模式
│   ├── deliverable-spec.md     # 工作簿/证据/底档规范
│   └── table-profile.md        # Profile A 列结构 + 适配指南
├── examples/
│   └── annotations.example.json
├── scripts/                    # 28 个可复用工具
├── tests/
└── .github/workflows/ci.yml
```

---

## 配置

`config/rules.example.json` 是**虚构示例**。复制为 `config/rules.local.json`
（已被 gitignore）后修改：

- `tiers`：命名档位的阈值（`play`、`interact`、`finish`、`label`）
- `incentive_tiers`：激励金额 → 档位
- `fee_tiers`：验收费用 → 档位

互动率用小数（`0.005` = 0.5%），完播率用百分数（`5` = 5%）。

Profile A 的列结构见 [docs/table-profile.md](docs/table-profile.md)。流水线按列位置读取；
源表不同则改 `load_source()` 或写 profile adapter。

---

## 有意不包含的内容

- **客户/品牌名、批次号、达人姓名、真实金额与档位**——已移除
- **历史交付物、原始表、截图、Cookie**——绝不包含
- **多 sheet、8 列验收表型的 adapter**——公共版只带两 sheet Profile A；多 sheet 结构因项目而异，
  适配方法见 `docs/table-profile.md`
- **平台稳定性保证**——抖音反爬行为会变，自动化是尽力而为，人工复核是最终依据

---

## 测试

```bash
python3 -m compileall scripts tests
python3 -m unittest discover -s tests -v
```

CI 在 Ubuntu 上跑同样的检查（不跑 macOS 专属 OCR）。

---

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。请勿在 issue/PR 中附带真实批次数据、凭据或客户信息。

## 许可证

MIT，见 [LICENSE](LICENSE)。

## 免责声明

与抖音/字节跳动及文中提及的任何平台无关联。使用风险自负，请遵守平台条款、合同与法律。
作者不对账号受限、数据丢失或据此做出的资金决策负责。
