import imaplib
import email as email_stdlib
from email.message import Message
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from bs4 import BeautifulSoup

class EmailNotifier:
    """
    同步邮件通知器，用于连接到IMAP服务器以获取新邮件。
    """

    def __init__(self, host: str, user: str, password: str, logger=None):
        self.host = host
        self.user = user
        self.password = password
        self.logger = logger
        self.mail: Optional[imaplib.IMAP4_SSL] = None
        self.text_num: int = 150

    @staticmethod
    def test_connection(host: str, user: str, password: str, logger=None) -> bool:
        """
        测试IMAP连接和登录凭据是否有效。这是一个阻塞操作。
        """
        try:
            # 使用 with 语句确保连接被正确关闭
            with imaplib.IMAP4_SSL(host) as test_mail:
                test_mail.login(user, password)
                test_mail.logout()
            return True
        except Exception as e:
            if logger:
                logger.error(f"[EmailNotifier] 连接测试失败 {user}: {e}")
            return False

    def _connect(self):
        """
        建立并维护到IMAP服务器的连接。
        如果连接或登录失败，则会引发异常。
        """
        # 如果已连接，先检查连接是否仍然有效
        if self.mail:
            try:
                self.mail.noop()
                return
            except (imaplib.IMAP4.abort, imaplib.IMAP4.readonly):
                # 连接已失效，准备重新连接
                self.mail = None
        
        # 建立新连接
        try:
            self.mail = imaplib.IMAP4_SSL(self.host)
            self.mail.login(self.user, self.password)
            self.mail.select('inbox')
        except Exception as e:
            self._log(f"连接或登录 {self.user} 失败: {e}", 'error')
            self.mail = None
            raise

    def _html_to_text(self, html_content: str) -> str:
        """
        将HTML内容转换为纯文本。
        """
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            # 移除脚本和样式标签
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()
            # 获取文本并清理空白
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            return ' '.join(chunk for chunk in chunks if chunk)
        except Exception:
            return "（HTML内容解析失败）"

    def _decode_header(self, header: str) -> str:
        """
        将邮件头（如'Subject'）安全地解码为可读字符串。
        """
        try:
            decoded_parts = email_stdlib.header.decode_header(header)
            header_parts = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    # 如果charset未指定，则默认为utf-8并忽略错误
                    header_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
                else:
                    header_parts.append(str(part))
            return "".join(header_parts)
        except Exception:
            # 出现意外解码错误时的回退方案
            return str(header)

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        将邮件头中的日期字符串解析为datetime对象。
        """
        if not date_str:
            return None
        try:
            date_tuple = email_stdlib.utils.parsedate_tz(date_str)
            if date_tuple:
                return datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))
        except Exception:
            pass
        return None

    def _extract_body(self, msg: Message) -> str:
        """
        从邮件消息对象中提取最合适的文本内容。
        它会优先选择纯文本（text/plain）而不是HTML（text/html）。
        """
        body = ""
        if msg.is_multipart():
            # 首先，遍历寻找纯文本部分
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                if content_type == 'text/plain' and 'attachment' not in content_disposition:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        return body # 找到纯文本后立即返回
                    except Exception:
                        continue
            
            # 如果没有找到纯文本，再寻找HTML部分
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))
                if content_type == 'text/html' and 'attachment' not in content_disposition:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        html_body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        return self._html_to_text(html_body) # 找到HTML后立即返回
                    except Exception:
                        continue
        else:
            # 非多部分（multipart）邮件
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
        return body # 如果未找到任何内容，则返回空字符串

    def _parse_email_message(self, msg: Message) -> Tuple[str, str, Optional[datetime]]:
        """
        将原始的email.message对象解析为其关键组件（主题、正文、日期）。
        这是一个协调函数，调用了其他辅助方法。
        """
        subject = self._decode_header(msg['Subject'] or "（无主题）")
        email_date = self._parse_date(msg.get('Date'))
        
        body = self._extract_body(msg)
        
        # 清理空白字符并截断
        final_body = ' '.join(body.split())
        if len(final_body) > self.text_num:
            final_body = final_body[:self.text_num] + "..."
        
        if not final_body:
            final_body = "（无文本内容）"
            
        return subject, final_body, email_date

    def fetch_new_emails(self, last_known_uid: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        获取所有比 last_known_uid 更新的邮件。这是一个阻塞操作。
        """
        try:
            self._connect()
            if not self.mail:
                return [], last_known_uid

            # 搜索UID大于已知UID的邮件
            if last_known_uid:
                status, data = self.mail.uid('search', None, f'UID {last_known_uid}:*')
                # 搜索结果包含last_known_uid本身，所以我们跳过第一个
                new_uids = data[0].split()[1:]
            else:
                # 如果没有已知的UID（首次运行），获取所有邮件，但只返回最新的UID作为基准，避免消息轰炸
                status, data = self.mail.uid('search', None, 'ALL')
                all_uids = data[0].split()
                if all_uids:
                    latest_uid = all_uids[-1].decode()
                    self._log(f"用户 {self.user} 没有历史UID，设定基准UID为 {latest_uid}。")
                    return [], latest_uid
                return [], None # 邮箱为空

            if status != 'OK' or not new_uids:
                # 如果搜索失败或没有新邮件，获取当前最新的UID以保持状态同步
                status, data = self.mail.uid('search', None, 'ALL')
                all_uids = data[0].split()
                return [], all_uids[-1].decode() if all_uids else last_known_uid

            new_emails = []
            latest_uid_in_batch = new_uids[-1]
            for uid in new_uids:
                status, msg_data = self.mail.uid('fetch', uid, '(RFC822)')
                if status == 'OK':
                    # 解析邮件内容
                    msg = email_stdlib.message_from_bytes(msg_data[0][1])
                    subject, body, date = self._parse_email_message(msg)
                    new_emails.append({"subject": subject, "content": body, "date": date})

            return new_emails, latest_uid_in_batch.decode()
        except Exception as e:
            self._log(f"获取新邮件时出错: {e}", 'error')
            if self.mail:
                try: self.mail.logout()
                except Exception: pass
            self.mail = None
            return [], last_known_uid

    def _log(self, message: str, level: str = 'info'):
        """
        使用提供的logger记录消息，或直接打印到控制台。
        """
        if self.logger:
            getattr(self.logger, level, print)(f"[EmailNotifier] {message}")
        else:
            print(f"[{level.upper()}] [EmailNotifier] {message}")
