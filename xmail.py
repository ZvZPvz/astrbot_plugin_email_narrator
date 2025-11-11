import imaplib
import email as email_stdlib
from datetime import datetime
from bs4 import BeautifulSoup

class EmailNotifier:
    """
    同步邮件通知器
    """
    
    def __init__(self, host: str, user: str, password: str, logger=None):
        self.host = host
        self.user = user
        self.password = password
        self.logger = logger
        self.mail = None
        self.text_num = 150

    @staticmethod
    def test_connection(host: str, user: str, password: str, logger=None) -> bool:
        """
        用于测试IMAP连接和登录是否有效。
        这是一个阻塞操作。
        """
        try:
            test_mail = imaplib.IMAP4_SSL(host)
            test_mail.login(user, password)
            test_mail.logout()
            return True
        except Exception as e:
            if logger:
                logger.error(f"[EmailNotifier] 连接测试失败 {user}: {e}")
            return False

    def _connect(self):
        if self.mail:
            try:
                self.mail.noop()
                return
            except (imaplib.IMAP4.abort, imaplib.IMAP4.readonly):
                pass
        try:
            self.mail = imaplib.IMAP4_SSL(self.host)
            self.mail.login(self.user, self.password)
            self.mail.select('inbox')
        except Exception as e:
            self._log(f"连接或登录 {self.user} 失败: {e}", 'error')
            self.mail = None
            raise

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

    def _parse_email_content(self, msg: email_stdlib.message.Message) -> tuple[str, str, datetime | None]:
        subject = "（无主题）"
        if msg['Subject']:
            try:
                decoded_header = email_stdlib.header.decode_header(msg['Subject'])
                header_parts = []
                for part, charset in decoded_header:
                    if isinstance(part, bytes): header_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
                    else: header_parts.append(str(part))
                subject = "".join(header_parts)
            except Exception: subject = str(msg['Subject'])
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    try: body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore'); break
                    except Exception: continue
            if not body:
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        try: html_body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore'); body = self._html_to_text(html_body); break
                        except Exception: continue
        else:
            try:
                content_type = msg.get_content_type(); payload = msg.get_payload(decode=True); text = payload.decode(msg.get_content_charset() or 'utf-8', errors='ignore')
                if 'html' in content_type: body = self._html_to_text(text)
                else: body = text
            except Exception: pass
        email_date = None
        date_str = msg.get('Date')
        if date_str:
            try:
                date_tuple = email_stdlib.utils.parsedate_tz(date_str)
                if date_tuple: email_date = datetime.fromtimestamp(email_stdlib.utils.mktime_tz(date_tuple))
            except Exception: pass
        final_body = ' '.join(body.split())
        if len(final_body) > self.text_num: final_body = final_body[:self.text_num] + "..."
        if not final_body: final_body = "（无文本内容）"
        return subject, final_body, email_date

    def fetch_new_emails(self, last_known_uid: str | None) -> tuple[list[dict], str | None]:
        try:
            self._connect()
            if last_known_uid:
                status, data = self.mail.uid('search', None, f'UID {last_known_uid}:*')
                new_uids = data[0].split()[1:]
            else:
                status, data = self.mail.uid('search', None, 'ALL')
                all_uids = data[0].split()
                if all_uids: latest_uid = all_uids[-1]; return [], latest_uid.decode()
                return [], None
            if status != 'OK' or not new_uids:
                status, data = self.mail.uid('search', None, 'ALL')
                all_uids = data[0].split()
                return [], all_uids[-1].decode() if all_uids else last_known_uid
            new_emails = []
            latest_uid_in_batch = new_uids[-1]
            for uid in new_uids:
                status, msg_data = self.mail.uid('fetch', uid, '(RFC822)')
                if status == 'OK':
                    msg = email_stdlib.message_from_bytes(msg_data[0][1])
                    subject, body, date = self._parse_email_content(msg)
                    new_emails.append({"subject": subject, "content": body, "date": date})
            return new_emails, latest_uid_in_batch.decode()
        except Exception as e:
            self._log(f"获取新邮件时出错: {e}", 'error')
            if self.mail:
                try: self.mail.logout()
                except: pass
            self.mail = None
            return [], last_known_uid

    def _log(self, message, level='info'):
        if self.logger: getattr(self.logger, level)(message)
        else: print(f"[{level.upper()}] {message}")