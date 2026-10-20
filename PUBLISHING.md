# 发布指南（PUBLISHING）

> 这份文档只用于把本目录发布到 GitHub。
>
> **不会修改、也不会替换本地工作目录的 `README.md`。**
>
> - 本地工作目录的 `README.md`：继续作为日常审核工作目录的说明，保持原样。
> - 本目录（公开版）的 `README.md`：面向 GitHub 读者的项目说明。
>
> 两者位于不同目录、是不同文件，互不影响。发布完成后可以删除本文件，或保留作为维护者指南。

---

## 0. 发布前检查

- [x] README 中的仓库地址已同步为当前仓库
- [x] `LICENSE` 版权人已设为 `Liousee`（如需改成真实姓名，发布前修改）
- [ ] 可选：在 `SECURITY.md` 中补充你的安全联系方式
- [ ] 运行测试：

  ```bash
  make test
  ```

- [ ] 运行敏感信息扫描（在公开仓库目录执行）：

  ```bash
  grep -rnE '/Users/|sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{10,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|sessionid|sid_tt' . \
    --exclude-dir=.git --exclude='*.pyc'
  ```

  预期：无输出。

- [ ] 确认以下内容**没有**进入公开目录：
  - 原始表、截图、交付物
  - 登录 profile / Cookie / 缓存
  - 任何真实客户名、批次号、达人姓名、真实金额
  - `.env`、API Key、token

---

## 1. 在 GitHub 创建空仓库

建议：

- 仓库名：`-content-acceptance-audit`
  （注意：当前 GitHub 仓库名首字符是 `-`；如为误操作，可在 Settings 中改名）
- 可见性：Public（或按你的组织要求设为 Private）
- **不要**勾选 “Add a README file”“Add .gitignore”“Choose a license”
  （避免和本地文件冲突）

可选描述：

> Evidence-first content acceptance audit toolkit: screenshot OCR, link/follower verification, styled deliverables, and audit dossiers.

可选 topics：

`claude-code` `audit` `content-review` `ocr` `douyin` `excel` `python` `macos`

---

## 2. 本地初始化并推送

在**公开仓库目录**执行（不要把本地工作目录整个提交）：

```bash
cd <公开仓库目录>

git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin git@github.com:Liousee/-content-acceptance-audit.git
git push -u origin main
```

如果使用 HTTPS：

```bash
git remote add origin https://github.com/Liousee/-content-acceptance-audit.git
```

---

## 3. 推送后

- Settings → Security → 开启 **Private vulnerability reporting**
- Settings → Actions → 确认 workflow 权限符合你的组织策略
- 可选：为 `main` 开启 branch protection
- 打开仓库首页，确认：
  - README 正常渲染
  - CI 通过
  - 没有误提交的表格/截图/缓存

---

## 4. 后续维护

- 日常审核继续在**本地工作目录**进行，不受公开仓库影响。
- 公共版的修改只在公开仓库目录进行；不要把审核数据复制进去。
- 每次更新前：

  ```bash
  make test
  grep -rnE '/Users/|sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{10,}|sessionid|sid_tt' . \
    --exclude-dir=.git --exclude='*.pyc'
  ```

- 更新发布：

  ```bash
  git add .
  git commit -m "Describe the change"
  git push
  ```

---

## 5. 两个 README 的关系

| 文件 | 用途 | 是否发布 |
|---|---|---|
| 本地工作目录的 `README.md` | 日常审核工作目录说明 | 否 |
| 本目录的 `README.md` | GitHub 项目说明（英文） | 是 |
| 本目录的 `README.zh-CN.md` | GitHub 项目说明（中文） | 是 |

本文件（`PUBLISHING.md`）只是发布指南，不含业务数据；可以发布，也可以发布后删除。
