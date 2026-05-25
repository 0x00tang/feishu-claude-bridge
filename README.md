# 飞书 × Claude Code 桥接

在 **飞书 / Lark** 里和机器人聊天，由本机 **Claude Code** 执行开发、设计、文档、汇报与项目检查等任务，并保留按会话分组的聊天记录。

## 架构

```
Lark 消息 → WebSocket 长连接 → 本桥接服务 → claude -p（非交互）
                ↑                              ↓
           3 秒内先回复「处理中」          JSON 结果 + session_id
                ↓                              ↓
           后台线程完成后推送完整回复    data/sessions.json 持久化
```

- **会话延续**：每个 Lark `chat_id` 对应一个 Claude `session_id`，多轮对话自动 `--resume`
- **聊天记录**：保存在 `data/sessions.json`，可用 `/history` 查看
- **无需公网服务器**：Lark「长连接」模式，本机联网即可

## 前置条件

1. 已安装并登录 [Claude Code](https://code.claude.com)（`claude` 命令可用）
2. Python 3.9+
3. Lark **企业自建应用**（Custom App；长连接仅支持自建应用）

## 一、在 Lark 开放平台创建应用

1. 打开 **[Lark Developer](https://open.larksuite.com/app)**（不是 open.feishu.cn）
2. 创建 **Custom App**（企业自建应用）
3. 在 **Credentials** 页复制 **App ID**、**App Secret**
4. **Permissions & Scopes**（至少开通）：
   - 读取、发送消息相关 IM 权限
   - `im:message:send_as_bot` — Send messages as bot
   - 接收用户发给机器人的单聊消息（Receive messages sent to the bot）
5. **Events** → 添加 `im.message.receive_v1`（Receive message v1.0）
6. **先不要保存**订阅方式，等本地 `python main.py` 跑起来后再选 **Long connection**

7. **Bot** → 启用 Bot 能力
8. **Version management** → 创建版本并 **Install to workspace**

## 二、本地配置

```bash
cd ~/Desktop/WorkSpace/feishu-claude-bridge
cp .env.example .env
```

编辑 `.env`（国际版默认已指向 Lark）：

```env
LARK_APP_ID=cli_你的AppID
LARK_APP_SECRET=你的AppSecret
LARK_DOMAIN=https://open.larksuite.com
```

可选：仅允许你自己使用（在事件日志或测试消息里可看到 `open_id`）：

```env
ALLOWED_OPEN_IDS=ou_xxxxxxxx
```

## 三、启动

```bash
cd ~/Desktop/WorkSpace/feishu-claude-bridge
source .venv/bin/activate
python main.py
```

**首次使用 Claude Code** 需在本机终端登录：

```bash
claude
# 在交互界面输入 /login 完成 OAuth
```

看到 `connected to wss://...` 后：

1. 回到 [open.larksuite.com](https://open.larksuite.com/app) → **Events** → 订阅方式选 **Long connection** → Save  
   （必须本地进程在线才能保存成功）
2. 在 Lark 客户端里搜索你的 Bot，发一条消息测试

## 四、使用说明

| 操作 | 说明 |
|------|------|
| 直接发文字 | Claude Code 在当前工作目录执行任务 |
| `/开发 <任务>` | 开发/改代码（先读代码再改） |
| `/设计 <需求>` | 输出设计草案 |
| `/文档 <要求>` | 编写或完善项目文档 |
| `/汇报 <主题>` | 生成工作汇报 |
| `/检查` | 项目完整性检查（结构、配置、测试、风险） |
| `/cwd /path/to/repo` | 本会话改用指定目录 |
| `/new` | 清空 Claude 会话绑定，开始新对话 |
| `/history` | 查看最近聊天记录 |
| `/help` | 帮助 |

## 五、后台常驻（macOS）

```bash
cat > ~/Library/LaunchAgents/com.echo.lark-claude.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.echo.lark-claude</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/echo/Desktop/WorkSpace/feishu-claude-bridge/.venv/bin/python</string>
    <string>/Users/echo/Desktop/WorkSpace/feishu-claude-bridge/main.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/echo/Desktop/WorkSpace/feishu-claude-bridge</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/lark-claude.log</string>
  <key>StandardErrorPath</key><string>/tmp/lark-claude.err</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.echo.lark-claude.plist
```

## 国内飞书用户

若改用国内飞书，在 `.env` 中设置：

```env
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_DOMAIN=https://open.feishu.cn
```

并在 [open.feishu.cn](https://open.feishu.cn/app) 创建应用。

## 安全提示

- Claude Code 能读写指定目录并执行 shell，建议设置 `ALLOWED_OPEN_IDS`
- 不要把 `.env` 提交到 git

## 故障排查

| 现象 | 处理 |
|------|------|
| Long connection 保存失败 | 先 `python main.py`，确认终端有 `connected to wss://` 再保存 |
| 收不到消息 | 确认 App 已 Install、Bot 已加入会话、权限已审批 |
| 连错开放平台 | 国际版必须用 **open.larksuite.com**，不要用 open.feishu.cn |
| Claude 报错 | 终端运行 `claude`，执行 `/login` 后再试 |
