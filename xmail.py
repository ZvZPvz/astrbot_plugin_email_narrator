import email as email_stdlib
from email.message import Message
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from bs4 import BeautifulSoup
from aioimaplib import aioimaplib

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
        """
        异步建立并维护到IMAP服务器的连接。
        """
        if self.mail:
            try:
                await self.mail.noop()
                return
            except Exception:
                # noop 失败意味着连接已断开
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
        """安全地断开连接。"""
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

            search_criterion = f'UID {last_known_uid}:*' if last_known_uid else 'ALL'
            status, response = await self.mail.uid_search(search_criterion)
            
            if status != 'OK' or not response or not response[0]:
                 return [], last_known_uid

            uids_str = response[0].split()
            
            if not last_known_uid:
                if uids_str:
                    latest_uid = uids_str[-1].decode()
                    self._log(f"用户 {self.user} 没有历史UID，设定基准UID为 {latest_uid}。")
                    return [], latest_uid
                return [], None

            new_uids = [uid for uid in uids_str if uid.decode() != last_known_uid]
            
            if not new_uids:
                status_all, resp_all = await self.mail.uid_search('ALL')
                if status_all == 'OK' and resp_all and resp_all[0]:
                    all_uids = resp_all[0].split()
                    return [], all_uids[-1].decode() if all_uids else last_known_uid
                return [], last_known_uid

            new_emails = []
            latest_uid_in_batch = new_uids[-1]
            for uid in new_uids:
                fetch_response = await self.mail.uid_fetch(uid, '(RFC822)')
                if fetch_response.result == 'OK':
                    msg_data = fetch_response.lines[1]
                    msg = email_stdlib.message_from_bytes(msg_data)
                    subject, body, date = self._parse_email_message(msg)
                    new_emails.append({"subject": subject, "content": body, "date": date})

            return new_emails, latest_uid_in_batch.decode()
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
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                if content_type == 'text/plain' and 'attachment' not in content_disposition:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        return body
                    except Exception:
                        continue
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                if content_type == 'text/html' and 'attachment' not in content_disposition:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        html_body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        return self._html_to_text(html_body)
                    except Exception:
                        continue
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
                pass
        return body
    def _log(self, message: str, level: str = 'info'):
        if self.logger:
            getattr(self.logger, level, print)(f"[EmailNotifier] {message}")
        else:
            print(f"[{level.upper()}] [EmailNotifier] {message}")
