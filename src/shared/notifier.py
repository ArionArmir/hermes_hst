"""
Sistema di notifiche Telegram/Email per il Trading Engine
"""
import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from loguru import logger

class Notifier:
    def __init__(self):
        # Telegram
        self.telegram_token = os.getenv('TELEGRAM_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        # Email
        self.email_enabled = os.getenv('EMAIL_ENABLED', 'false').lower() == 'true'
        self.email_sender = os.getenv('EMAIL_SENDER')
        self.email_password = os.getenv('EMAIL_PASSWORD')
        self.email_recipient = os.getenv('EMAIL_RECIPIENT')
        self.smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))

    def send_telegram(self, message: str) -> bool:
        """Invia un messaggio Telegram"""
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("⚠️ Telegram non configurato")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                logger.debug("✅ Telegram inviato")
                return True
            else:
                logger.error(f"❌ Telegram error: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Errore Telegram: {e}")
            return False

    def send_email(self, subject: str, body: str) -> bool:
        """Invia un'email"""
        if not self.email_enabled:
            return False
        if not all([self.email_sender, self.email_password, self.email_recipient]):
            logger.warning("⚠️ Email non configurata")
            return False
        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_sender
            msg['To'] = self.email_recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_sender, self.email_password)
            server.send_message(msg)
            server.quit()
            logger.debug("✅ Email inviata")
            return True
        except Exception as e:
            logger.error(f"❌ Errore email: {e}")
            return False

    def notify_position_opened(self, symbol: str, side: str, entry: float, quantity: float, sl: float, tp: float):
        """Notifica apertura posizione"""
        msg = f"""
🔔 <b>POSIZIONE APERTA</b>
📊 {symbol} {side.upper()}
💰 Entry: {entry:.2f} USDT
📦 Qty: {quantity:.4f}
📉 Stop Loss: {sl:.2f}
📈 Take Profit: {tp:.2f}
        """
        self.send_telegram(msg)
        self.send_email(f"[Hermes] Posizione Aperta {symbol}", msg)

    def notify_position_closed(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float, reason: str):
        """Notifica chiusura posizione"""
        emoji = "✅" if pnl > 0 else "❌"
        msg = f"""
{emoji} <b>POSIZIONE CHIUSA</b>
📊 {symbol} {side.upper()}
💰 Entry: {entry:.2f}
🚪 Exit: {exit_price:.2f}
📈 PnL: {pnl:.2f} USDT
📌 Motivo: {reason}
        """
        self.send_telegram(msg)
        self.send_email(f"[Hermes] Posizione Chiusa {symbol} - {pnl:.2f} USDT", msg)

    def notify_signal(self, symbol: str, action: str, confidence: float):
        """Notifica segnale ricevuto (opzionale)"""
        # solo se confidenza alta
        if confidence > 0.8:
            msg = f"""
📊 <b>SEGNALE RICEVUTO</b>
{symbol} → {action.upper()}
Confidenza: {confidence:.2%}
            """
            self.send_telegram(msg)

    def notify_error(self, error_msg: str):
        """Notifica errore critico"""
        msg = f"⚠️ <b>ERRORE CRITICO</b>\n{error_msg}"
        self.send_telegram(msg)
        self.send_email("[Hermes] Errore Critico", error_msg)

# Istanza globale
notifier = Notifier()
