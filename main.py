# main.py
import asyncio
import os
import json
import traceback
from typing import List, Optional, Dict, Set
import yaml

from astrbot.core.agent.message import AssistantMessageSegment, UserMessageSegment
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from .xmail import EmailNotifier

def _load_metadata() -> dict:
    try:
        metadata_path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {"version": "v1.0.0"}

_metadata = _load_metadata()

@register(
    _metadata.get("name"),
    _metadata.get("author"),
    _metadata.get("description"),
    _metadata.get("version"),
)
class EmailNarrator(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._targets: Set[str] = set()
        self._notifiers: Dict[str, EmailNotifier] = {}
        self._is_running = False
        self._email_task: Optional[asyncio.Task] = None
        self.data_dir = StarTools.get_data_dir("email_narrator")
        self.state_file = os.path.join(self.data_dir, "narrator_state.json")
        self._last_uids: Dict[str, str] = {}
        self._interval = max(float(self.config.get("interval", 30)), 15.0)
        self._text_num = max(int(self.config.get("text_num", 150)), 20)
        logger.info(f"[{_metadata['name']}] v{_metadata['version']} 插件初始化完成。")

    async def initialize(self):
        """插件异步初始化，恢复状态和播报目标。"""
        self._load_state()
        
        is_fixed_mode = self.config.get("fixed_target", False)

        preconfigured_targets = self.config.get("preconfigured_targets", [])
        if preconfigured_targets:
            self._targets.update(preconfigured_targets)
            logger.info(f"[{_metadata['name']}] 已从配置加载 {len(preconfigured_targets)} 个预设播报目标。")

        if not is_fixed_mode:
            saved_targets = self.config.get("active_targets", [])
            if saved_targets:
                self._targets.update(saved_targets)
                logger.info(f"[{_metadata['name']}] 已恢复 {len(saved_targets)} 个由指令开启的播报目标。")
        
        if is_fixed_mode:
            logger.info(f"[{_metadata['name']}] 插件运行在固定推送目标模式。指令将不可用。")

        if self._targets:
            self._init_notifiers()
            self._start_email_service()
    
    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    self._last_uids = json.load(f)
            except Exception as e:
                logger.error(f"[{_metadata['name']}] 加载状态文件失败: {e}")
    
    def _save_state(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self._last_uids, f, indent=4)
        except Exception as e:
            logger.error(f"[{_metadata['name']}] 保存状态文件失败: {e}")

    def _init_notifiers(self):
        self._notifiers.clear()
        accounts_config = self.config.get("accounts", [])
        for account_str in accounts_config:
            try:
                host, user, password = [part.strip() for part in account_str.split(',')]
                notifier = EmailNotifier(host, user, password, logger)
                notifier.text_num = self._text_num
                self._notifiers[user] = notifier
            except Exception as e:
                logger.error(f"[{_metadata['name']}] 初始化邮箱账号失败: {account_str} -> {e}")

    async def _email_monitor_loop(self):
        logger.info(f"[{_metadata['name']}] 邮件监控服务已启动，监控 {len(self._notifiers)} 个账号。")
        while self._is_running:
            try:
                for user, notifier in self._notifiers.items():
                    last_uid = self._last_uids.get(user)
                    new_emails, latest_uid = await notifier.fetch_new_emails(last_uid)
                    if latest_uid and self._last_uids.get(user) != latest_uid:
                        self._last_uids[user] = latest_uid
                        self._save_state()
                    if new_emails:
                        logger.info(f"[{_metadata['name']}] 邮箱 {user} 收到 {len(new_emails)} 封新邮件，准备播报...")
                        for email_data in new_emails:
                            await self._broadcast_to_targets(user, email_data)
                await asyncio.sleep(self._interval)
            except Exception as e:
                logger.error(f"[{_metadata['name']}] 监控循环发生严重错误: {e}")
                await asyncio.sleep(self._interval * 2)

    async def _broadcast_to_targets(self, email_user: str, email_data: dict):
        if not self._targets: return
        tasks = [
            self._process_and_narrate_email(target_uid, email_user, email_data)
            for target_uid in list(self._targets)
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_and_narrate_email(self, session_id: str, email_user: str, email_data: dict):
        try:
            provider = self.context.get_using_provider(umo=session_id)
            subject = email_data.get("subject", "（无主题）")
            content = email_data.get("content", "（无内容）")
            sender = email_data.get("sender", "（未知）")
            recipient = email_data.get("recipient", "（未知）")

            if not provider:
                logger.warning(f"[{_metadata['name']}] 无法为 {session_id} 找到LLM Provider，将发送原始文本。")
                fallback_msg = f"📧 新邮件通知 ({email_user})\n- 发件人: {sender}\n- 主题: {subject}\n- 内容: {content}"
                await self.context.send_message(session_id, MessageChain([Plain(fallback_msg)]))
                return

            pure_history, system_prompt = [], ""
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(session_id) or \
                      await self.context.conversation_manager.new_conversation(session_id)
            
            conversation = await self.context.conversation_manager.get_conversation(session_id, conv_id)
            if conversation:
                if conversation.history: pure_history = json.loads(conversation.history)
                if persona_id := conversation.persona_id:
                    if persona := await self.context.persona_manager.get_persona(persona_id):
                        system_prompt = persona.system_prompt
            
            if not system_prompt:
                if default_persona := await self.context.persona_manager.get_default_persona_v3(umo=session_id):
                    system_prompt = default_persona["prompt"]

            if not system_prompt:
                logger.error(f"[{_metadata['name']}] 无法加载任何人格，播报任务中止。")
                return

            prompt_template = self.config.get("prompt_template", "")
            final_prompt = prompt_template.replace("{{user}}", email_user)\
                                          .replace("{{subject}}", subject)\
                                          .replace("{{content}}", content)\
                                          .replace("{{sender}}", sender)\
                                          .replace("{{recipient}}", recipient)

            llm_response = await provider.text_chat(prompt=final_prompt, contexts=pure_history, system_prompt=system_prompt)

            if not (llm_response and llm_response.completion_text):
                logger.warning(f"[{_metadata['name']}] LLM调用失败或返回空内容。")
                return
            
            response_text = llm_response.completion_text.strip()
            await self.context.send_message(session_id, MessageChain([Plain(response_text)]))
            await self.context.conversation_manager.add_message_pair(cid=conv_id, user_message=UserMessageSegment(content=final_prompt), assistant_message=AssistantMessageSegment(content=response_text))

        except Exception:
            logger.error(f"[{_metadata['name']}] 处理邮件播报时发生严重错误:\n{traceback.format_exc()}")

    def _start_email_service(self):
        if self._is_running: return
        self._is_running = True
        self._email_task = asyncio.create_task(self._email_monitor_loop())

    async def _stop_email_service(self):
        if not self._is_running: return
        self._is_running = False
        if self._email_task:
            self._email_task.cancel()
            try: await self._email_task
            except asyncio.CancelledError: pass
        logger.info(f"[{_metadata['name']}] 邮件监控服务已停止。")
        
    def _save_active_targets(self):
        if not self.config.get("fixed_target", False):
            preconfigured = set(self.config.get("preconfigured_targets", []))
            active_targets = list(self._targets - preconfigured)
            self.config["active_targets"] = active_targets
            self.config.save_config()

    @filter.command_group("email_narrator", alias={"邮件播报"})
    def cmd_group(self): pass

    @cmd_group.command("on", alias={"开启"})
    async def cmd_on(self, event: AstrMessageEvent):
        if self.config.get("fixed_target", False):
            yield event.plain_result("ℹ️ 当前为固定推送目标模式，无法通过指令开启播报。")
            return

        uid = event.unified_msg_origin
        if uid in self._targets:
            yield event.plain_result("✅ 邮件播报功能已经开启啦！")
            return
            
        self._targets.add(uid)
        self._save_active_targets()
        
        if not self._is_running and len(self._targets) > 0:
            self._init_notifiers()
            self._start_email_service()

        yield event.plain_result(f"✅ 邮件播报功能已开启！")

    @cmd_group.command("off", alias={"关闭"})
    async def cmd_off(self, event: AstrMessageEvent):
        if self.config.get("fixed_target", False):
            yield event.plain_result("ℹ️ 当前为固定推送目标模式，无法通过指令关闭播报。")
            return

        uid = event.unified_msg_origin
        if uid not in self._targets:
            yield event.plain_result("❌ 邮件播报功能本来就是关着的哦。")
            return
            
        self._targets.discard(uid)
        self._save_active_targets()
        
        if not self._targets:
            await self._stop_email_service()
            
        yield event.plain_result("✅ 当前会话的邮件播报已关闭。")

    @cmd_group.command("status", alias={"状态"})
    async def cmd_status(self, event: AstrMessageEvent):
        uid = event.unified_msg_origin
        session_status = "✅ 已开启" if uid in self._targets else "❌ 已关闭"
        service_status = "🟢 运行中" if self._is_running else "🔴 已停止"
        
        status_text = f"""--- 📧 邮件播报员状态 ---\n- 当前会话: {session_status}\n- 监控服务: {service_status}\n- 监控账号数: {len(self._notifiers)} / {len(self.config.get('accounts', []))}\n- 检查间隔: {self._interval} 秒\n- 内容上限: {self._text_num} 字符"""
        
        if self.config.get("fixed_target", False):
            status_text += "\n- 模式: 固定目标模式"
        else:
            status_text += "\n\n使用 `/email_narrator on` 来开启播报。"

        yield event.plain_result(status_text)
        
    @cmd_group.command("check_accounts", alias={"检查账号"})
    async def cmd_check_accounts(self, event: AstrMessageEvent):
        if not event.is_admin():
            yield event.plain_result("❌ 权限不足，此指令仅限管理员使用。")
            return
        accounts_config = self.config.get("accounts", [])
        if not accounts_config:
            yield event.plain_result("ℹ️ 尚未配置任何邮箱账号。")
            return
        yield event.plain_result("正在检查所有邮箱账户的连接状态，请稍候...")
        status_list = []
        total_accounts = len(accounts_config)
        valid_count = 0
        for account_str in accounts_config:
            try:
                host, user, password = [part.strip() for part in account_str.split(',')]
                is_ok = await EmailNotifier.test_connection(host, user, password, logger)
                if is_ok:
                    status_list.append(f"  - {user}: ✅ 连接成功"); valid_count += 1
                else:
                    status_list.append(f"  - {user}: ❌ 连接失败")
            except Exception:
                status_list.append(f"  - {account_str}: ❌ 配置格式错误")
        response_text = f"📧 邮箱账号连接状态 ({valid_count}/{total_accounts} 有效):\n" + "\n".join(status_list)
        yield event.plain_result(response_text)
        
    async def terminate(self):
        await self._stop_email_service()
        logger.info(f"[{_metadata['name']}] 插件已终止。")
