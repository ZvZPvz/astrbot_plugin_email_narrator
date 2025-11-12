# astrbot_plugin_email_narrator

_✨ [astrbot](https://github.com/AstrBotDevs/AstrBot) 智能邮件播报插件 ✨_  
[![License](https://img.shields.io/badge/License-AGPLv3-purple.svg)](https://opensource.org/license/agpl-v3)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-ZvZPvz-blue)](https://github.com/ZvZPvz)

</div>

## ✨ 功能特性

-   **智能播报**：利用 LLM 和人格设定，将冰冷的邮件通知转化为符合机器人性格的自然语言对话。
-   **会话继承**：每次邮件播报都会被记入对话历史，方便用户进行追问等上下文相关的操作。
-   **永不丢信**：采用持久化的 UID 追踪机制，即使机器人离线重启，也能收到所有错过的邮件。
-   **多账户支持**：可同时监控多个不同的邮箱账户。
-   **稳定解析**：使用 `BeautifulSoup` 进行健壮的 HTML 邮件解析，避免乱码和解析失败。
-   **多用户推送**：支持向多个目标（私聊或群聊）同时推送邮件通知。

## 📦 安装

- 在Astrbot插件市场安装或下载仓库zip手动安装
- 需要使在插件配置里设置邮箱帐户
### Gmail配置
1. 开启两步验证
2. 生成应用专用密码
3. 使用应用专用密码连接

## ⚙️ 配置

安装并启用插件后，请在 AstrBot 管理后台 -> 插件市场 -> 已安装 -> Email Narrator 中进行配置。

1.  **邮箱账户列表 (Accounts)**:
    这是最重要的配置。请**每行填写一个账户**，格式为 `imap服务器,邮箱地址,应用密码`。
    ```
    imap.gmail.com,your.email@gmail.com,your_google_app_password
    imap.qq.com,123456@qq.com,your_qq_app_password
    ```
    > ⚠️ **安全警告**: 应用密码虽然方便，但请妥善保管。建议为本插件创建专用的应用密码，并不要在其他地方使用。

2.  **预设的推送目标列表 (Preconfigured Targets)**:
    如果你希望某些会话（如管理员群）总是接收邮件通知，而无需手动开启，可以在这里填写完整的会话ID。
    -   如何获取会话ID？在目标会话中触发机器人，然后查看 AstrBot WebUI → 更多功能 → 会话管理，复制 消息会话来源 即可。格式如：`QQ:GroupMessage:12345678`。

3.  **其他配置**:
    -   **邮件检查间隔**: 轮询邮箱的秒数，建议不要低于10秒。
    -   **邮件内容预览字符上限**: 截取邮件正文的前 N 个字符交给 LLM 处理。
    -   **邮件播报指令模板**: 高级功能，可自定义发送给 LLM 的指令模板。

## ⌨️ 使用说明

### 命令

```plaintext
/email_narrator on
别名: /邮件播报 开启
功能: 在当前会话开启邮件播报功能。

/email_narrator off
别名: /邮件播报 关闭
功能: 在当前会话关闭邮件播报功能。

/email_narrator status
别名: /邮件播报 状态
功能: 查看插件的当前运行状态、监控的账号数量等信息。

/email_narrator check_accounts
别名: /邮件播报 检查账号
功能: (仅限管理员) 测试所有已配置邮箱账号的连接性，并返回状态报告。
```

## 👥 贡献指南

-   🌟 Star 这个项目！（点右上角的星星，感谢支持！）
-   🐛 提交 Issue 报告问题
-   💡 提出新功能建议
-   🔧 提交 Pull Request 改进代码

## # 支持
-   本插件需要处理你的邮件内容才能生成播报。所有处理均在本地完成，不会上传到任何第三方服务器。
-   请务必使用**应用密码 (App Password)** 而非你的主密码来配置邮箱账户，以保障账户安全。
-   本插件灵感和部分参照来源于 [https://github.com/DBJD-CR/astrbot_plugin_proactive_chat](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat) 和 [https://github.com/OlyMarco/EmailNotixion](https://github.com/OlyMarco/EmailNotixion)
