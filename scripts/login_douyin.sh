#!/bin/bash
# 抖音登录窗口（一次性设置，登录态长期复用）
# 用法: bash scripts/login_douyin.sh
# 步骤: ① 弹出的 Chrome 窗口里用抖音 APP 扫码登录 ② 登录成功后【关闭该窗口】
#
# 安全提醒:
#   · 登录态只用于少量、慢速、人工在场的定点复核（≤5 条/批、短会话），不要用于批量自动化。
#   · ~/.cache/yanshou-chrome 内含登录 Cookie，等同于账号凭据——绝不提交、绝不外发。
#   · 登录态失效后重新运行本脚本扫码即可。
PROFILE="${YANSHOU_CHROME_PROFILE:-$HOME/.cache/yanshou-chrome}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mkdir -p "$PROFILE"
echo "登录窗口启动中 (profile: $PROFILE)"
echo "→ 用抖音 APP 扫码登录，登录成功后关闭窗口即可"
"$CHROME_BIN" \
  --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
  --new-window "https://www.douyin.com/" >/dev/null 2>&1
echo "窗口已关闭，登录态已保存到 $PROFILE"
