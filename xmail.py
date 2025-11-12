# xmail.py
import asyncio
import email as email_stdlib
from email.message import Message
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from bs4 import BeautifulSoup
import aioimaplib
import re

class EmailNotifier:
    """
    异步邮件通知器，使用 aioimaplib 连接到IMAP服务器。
    """

    def __init__(self, host: str, user: str, password: str, logger=None):
        self.host = host
        self.user = user
        self.password = password
        self.logger = logger
        self.mail: Optional[aioimaplib.IMAP4_SSL] = None
        self.text_num: int = 150
        self.uid_regex = re.compile(r'UID\s+(\d+)')
        self.MAX_FETCH_PER_RUN = 20

    @staticmethod
    async def test_connection(host: str, user: str, password: str, logger=None) -> bool:
        test_mail = None
        try:
            test_mail = aioimaplib.IMAP4_SSL(host)
            await test_mail.wait_hello_from_server()
            await test_mail.login(user, password)
            return True
        except Exception as e:
            if logger: logger.error(f"[EmailNotifier] 连接测试失败 {user}: {e}")
            return False
        finally:
            if test_mail:
                try:
                    await test_mail.logout()
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
            self.mail = aioimaplib.IMAP4_SSL(self.host)
            await self.mail.wait_hello_from_server()
            await self.mail.login(self.user, self.password)
            await self.mail.select('inbox')
        except Exception as e:
            self._log(f"连接或登录 {self.user} 失败: {e}", 'error')
            if self.mail and getattr(self.mail, 'is_open', False):
                try: await self.mail.logout()
                except Exception: pass
            self.mail = None
            raise

    def _html_to_text(self, html_content: str) -> str:
        if not html_content: return ""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for s in soup(["script", "style"]):
                s.decompose()
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            text_parts = []
            for line in lines:
                for phrase in line.split("  "):
                    stripped_phrase = phrase.strip()
                    if stripped_phrase:
                        text_parts.append(stripped_phrase)
            return ' '.join(text_parts)
        except Exception:
            return "（HTML内容解析失败）"

    def _decode_header(self, header: str) -> str:
        try:
            decoded_parts = []
            for part, charset in email_stdlib.header.decode_header(header):
                if isinstance(part, bytes):
                    decoded_parts.append(part.decode(charset or 'utf-8', 'ignore'))
                else:
                    decoded_parts.append(str(part))
            return "".join(decoded_parts)
        except Exception:
            return str(header)

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str: return None
        try:
            tz_tuple = email_stdlib.utils.parsedate_tz(date_str)
            if tz_tuple: return datetime.fromtimestamp(email_stdlib.utils.mktime_tz(tz_tuple))
        except Exception: pass
        return None

    def _extract_body(self, msg: Message) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain' and 'attachment' not in str(part.get('Content-Disposition')):
                    try: return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                    except Exception: continue
            for part in msg.walk():
                if part.get_content_type() == 'text/html' and 'attachment' not in str(part.get('Content-Disposition')):
                    try: return self._html_to_text(part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore'))
                    except Exception: continue
        else:
            try:
                text = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='ignore')
                return self._html_to_text(text) if 'html' in msg.get_content_type() else text
            except Exception: pass
        return body

    def _parse_email_message(self, msg: Message) -> Dict[str, Any]:
        subject = self._decode_header(msg['Subject'] or "（无主题）")
        sender = self._decode_header(msg['From'] or "（未知发件人）")
        recipient = self._decode_header(msg['To'] or "（未知收件人）")
        email_date = self._parse_date(msg.get('Date'))
        body = self._extract_body(msg)
        final_body = ' '.join(body.split())
        if len(final_body) > self.text_num: final_body = final_body[:self.text_num] + "..."
        if not final_body: final_body = "（无文本内容）"
        return {"subject": subject, "content": final_body, "date": email_date, "sender": sender, "recipient": recipient}

    async def fetch_new_emails(self, last_known_uid: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            await self._connect()
            if not self.mail:
                return [], last_known_uid

            fetch_last_uid_result = await self.mail.fetch('*', '(UID)')
            if not fetch_last_uid_result.lines:
                return [], last_known_uid

            last_line = fetch_last_uid_result.lines[0].decode()
            match = self.uid_regex.search(last_line)
            if not match:
                return [], last_known_uid
            
            latest_uid_on_server = match.group(1)

            if not last_known_uid:
                self._log(f"首次运行，设定基准UID为 {latest_uid_on_server}。")
                return [], latest_uid_on_server

            try:
                last_uid_int = int(last_known_uid)
                latest_uid_int = int(latest_uid_on_server)
            except (ValueError, TypeError):
                return [], latest_uid_on_server

            if latest_uid_int <= last_uid_int:
                return [], latest_uid_on_server

            num_new_emails = latest_uid_int - last_uid_int
            if num_new_emails > self.MAX_FETCH_PER_RUN:
                self._log(f"检测到大量 ({num_new_emails}) 新邮件，将只获取最近的 {self.MAX_FETCH_PER_RUN} 封。", 'warning')
                start_uid = latest_uid_int - self.MAX_FETCH_PER_RUN + 1
            else:
                start_uid = last_uid_int + 1

            uids_to_fetch = list(range(start_uid, latest_uid_int + 1))
            if num_new_emails > 0:
                 self._log(f"发现 {num_new_emails} 封新邮件。准备获取 UIDs: {uids_to_fetch}")

            new_emails = []
            for uid_to_fetch in uids_to_fetch:
                uid_str = str(uid_to_fetch)
                fetch_result = await self.mail.uid('fetch', uid_str, '(RFC822)')
                
                if len(fetch_result.lines) > 1:
                    msg_bytes = fetch_result.lines[1]
                    msg = email_stdlib.message_from_bytes(msg_bytes)
                    parsed_data = self._parse_email_message(msg)

                    parsed_data['uid'] = uid_str
                    
                    new_emails.append(parsed_data)

            return new_emails, latest_uid_on_server
        except Exception as e:
            self._log(f"获取新邮件时发生严重错误: {e}", 'error')
            if self.mail and getattr(self.mail, 'is_open', False):
                try: await self.mail.logout()
                except Exception: pass
            self.mail = None
            return [], last_known_uid

    def _log(self, message: str, level: str = 'info'):
        if self.logger:
            log_func = getattr(self.logger, level, self.logger.info)
            log_func(f"[EmailNotifier] {message}")
        else:
            print(f"[{level.upper()}] [EmailNotifier] {message}")
