import email as email_stdlib
from email.message import Message
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from bs4 import BeautifulSoup
from aioimaplib import aioimaplib
import re

# 正则表达式用于从 FETCH 响应中解析出 UID
UID_PATTERN = re.compile(r'UID\s+(\d+)')

class EmailNotifier:
    """
    原生异步邮件通知器，基于 aioimaplib 实现。
    """

    def __init__(self, host: str, user: str, password: str, logger=None):
        self.host = host
        self.user = user
        self.password = password
        self.logger = logger
        self.mail: Optional[aioimaplib.IMAP4_SSL] = None
        self.text_num: int = 150

    @staticmethod
    async def test_connection(host: str, user: str, password: str, logger=None) -> bool:
        client = None
        try:
            client = aioimaplib.IMAP4_SSL(host=host)
            await client.wait_hello_from_server()
            response = await client.login(user, password)
            if response.result != "OK":
                raise RuntimeError(f"Login failed: {response.result}")
            return True
        except Exception as e:
            if logger:
                logger.error(f"[EmailNotifier] 异步连接测试失败 {user}: {e}")
            return False
        finally:
            if client:
                try:
                    await client.logout()
                except Exception:
                    pass

    async def _connect(self):
        if self.mail:
            try:
                await self.mail.noop()
                return
            except Exception:
                self.mail = None
        try:
            self.mail = aioimaplib.IMAP4_SSL(host=self.host)
            await self.mail.wait_hello_from_server()
            response = await self.mail.login(self.user, self.password)
            if response.result != "OK":
                raise RuntimeError(f"Login failed: {response.result}")
            await self.mail.select('inbox')
        except Exception as e:
            self._log(f"异步连接或登录 {self.user} 失败: {e}", 'error')
            self.mail = None
            raise

    async def disconnect(self):
        if self.mail:
            try:
                await self.mail.logout()
            except Exception:
                pass
            finally:
                self.mail = None

    async def fetch_new_emails(self, last_known_uid: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            await self._connect()
            if not self.mail:
                return [], last_known_uid

            # 1. 搜索所有邮件，获取它们的消息序号和最新的 UID
            status, response = await self.mail.search('ALL')
            if status != 'OK' or not response or not response[0]:
                return [], last_known_uid # 邮箱为空
            
            all_msg_nums = response[0].split()
            if not all_msg_nums:
                return [], last_known_uid

            # 为了获取最新的UID，我们需要fetch最后一个消息的UID
            fetch_last_uid_resp = await self.mail.fetch(all_msg_nums[-1], '(UID)')
            if fetch_last_uid_resp.result != 'OK' or not fetch_last_uid_resp.lines:
                return [], last_known_uid # 获取失败

            uid_match = UID_PATTERN.search(fetch_last_uid_resp.lines[0].decode())
            if not uid_match:
                return [], last_known_uid # 解析失败
            
            current_latest_uid = uid_match.group(1)

            # 如果是首次运行，只更新UID，不发送邮件
            if not last_known_uid:
                self._log(f"用户 {self.user} 没有历史UID，设定基准UID为 {current_latest_uid}。")
                return [], current_latest_uid

            # 如果最新的UID不比我们已知的UID大，说明没有新邮件
            if int(current_latest_uid) <= int(last_known_uid):
                return [], current_latest_uid

            # 2. 现在我们知道有新邮件了，我们按UID范围进行搜索
            search_criterion = f'(UID {last_known_uid}:*)'
            status, response = await self.mail.search(search_criterion)
            if status != 'OK' or not response or not response[0]:
                 return [], current_latest_uid # 理论上不应该发生，但作为保护

            new_msg_nums = response[0].split()
            
            new_emails = []
            if new_msg_nums:
                for msg_num in new_msg_nums:
                    # 3. 使用消息序号来获取邮件内容和UID
                    fetch_response = await self.mail.fetch(msg_num, '(UID RFC822)')
                    
                    if fetch_response.result == 'OK' and len(fetch_response.lines) > 1:
                        # 检查我们获取到的邮件的UID是否真的是新的，防止任何会话状态问题
                        header_line = fetch_response.lines[0].decode()
                        uid_match_inner = UID_PATTERN.search(header_line)
                        if uid_match_inner and int(uid_match_inner.group(1)) > int(last_known_uid):
                            msg_data = fetch_response.lines[1]
                            msg = email_stdlib.message_from_bytes(msg_data)
                            subject, body, date = self._parse_email_message(msg)
                            new_emails.append({"subject": subject, "content": body, "date": date})
                    else:
                        self._log(f"获取消息号 {msg_num.decode()} 的邮件内容失败: {fetch_response}", 'warning')

            return new_emails, current_latest_uid
        except Exception as e:
            self._log(f"异步获取新邮件时出错: {e}", 'error')
            await self.disconnect()
            return [], last_known_uid

    def _parse_email_message(self, msg: Message) -> Tuple[str, str, Optional[datetime]]:
        subject = self._decode_header(msg['Subject'] or "（无主题）")
        email_date = self._parse_date(msg.get('Date'))
        body = self._extract_body(msg)
        final_body = ' '.join(body.split())
        if len(final_body) > self.text_num:
            final_body = final_body[:self.text_num] + "..."
        if not final_body:
            final_body = "（无文本内容）"
        return subject, final_body, email_date
    def _html_to_text(self, html_content: str) -> str:
        if not html_content: return ""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for script_or_style in soup(["script", "style"]): script_or_style.decompose()
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            return ' '.join(chunk for chunk in chunks if chunk)
        except Exception: return "（HTML内容解析失败）"
    def _decode_header(self, header: str) -> str:
        try:
            decoded_parts = email_stdlib.header.decode_header(header)
            header_parts = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    header_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
                else: header_parts.append(str(part))
            return "".join(header_parts)
        except Exception: return str(header)
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str: return None
        try:
            date_tuple = email_stdlib.utils.parsedate_tz(date_str)
            if date_tuple: return datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))
        except Exception: return None
    def _extract_body(self, msg: Message) -> str:
        if msg.is_multipart():
            plain_text_body = None
            html_body = None
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                if 'attachment' in content_disposition:
                    continue
                if content_type == 'text/plain':
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        plain_text_body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        break
                    except Exception:
                        continue
                elif content_type == 'text/html':
                    if html_body is None:
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            html_body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        except Exception:
                            continue
            if plain_text_body:
                return plain_text_body
            elif html_body:
                return self._html_to_text(html_body)
            else:
                return ""
        else:
            try:
                content_type = msg.get_content_type()
                charset = msg.get_content_charset() or 'utf-8'
                payload = msg.get_payload(decode=True)
                text = payload.decode(charset, errors='ignore')
                if 'html' in content_type:
                    return self._html_to_text(text)
                else:
                    return text
            except Exception:
                return ""
        return ""
    def _log(self, message: str, level: str = 'info'):
        if self.logger:
            getattr(self.logger, level, print)(f"[EmailNotifier] {message}")
        else:
            print(f"[{level.upper()}] [EmailNotifier] {message}")
