"""
企业微信消息加解密 (WXBizMsgCrypt)

基于企业微信官方加解密方案：
- 签名: sha1(sort(token, timestamp, nonce, msg_encrypt))
- AES-256-CBC, Key=Base64_Decode(EncodingAESKey+"="), IV=Key[:16], PKCS#7
- 明文: random(16B) + msg_len(4B big-endian) + msg + receive_id

依赖: pycryptodome
"""

import base64
import hashlib
import os
import struct
from typing import Tuple

logger = __import__("logging").getLogger(__name__)

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    AES = None
    pad = unpad = None


class WXBizMsgCryptError(Exception):
    """加解密/验签错误"""
    pass


class WXBizMsgCrypt:
    """
    企业微信消息加解密类。
    初始化参数: token, encoding_aes_key (43 字符), receive_id (企业应用回调为 corp_id)。
    """

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        self.token = token
        self.receive_id = receive_id or ""
        if len(encoding_aes_key) != 43:
            raise WXBizMsgCryptError("EncodingAESKey must be 43 characters")
        if AES is None or pad is None:
            raise WXBizMsgCryptError("pycryptodome required. Run: pip install pycryptodome")
        key_b64 = encoding_aes_key + "="
        self.aes_key = base64.b64decode(key_b64)
        if len(self.aes_key) != 32:
            raise WXBizMsgCryptError("Invalid EncodingAESKey: decode length != 32")
        self.iv = self.aes_key[:16]

    def _signature(self, *parts: str) -> str:
        """sort(parts) 按字典序拼接后 sha1，返回小写十六进制"""
        s = "".join(sorted(parts))
        return hashlib.sha1(s.encode()).hexdigest().lower()

    def verify_url(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> Tuple[bool, str]:
        """
        验证回调 URL。GET 请求时企业微信会带 msg_signature, timestamp, nonce, echostr。
        返回 (成功, 解密后的 echostr 明文)；失败时 (False, 错误信息)。
        """
        try:
            sig = self._signature(self.token, timestamp, nonce, echostr)
            if sig != msg_signature:
                return False, "signature mismatch"
            ok, msg = self._decrypt(echostr)
            if not ok:
                return False, msg
            return True, msg
        except Exception as e:
            logger.debug("verify_url error: %s", e)
            return False, str(e)

    def decrypt_msg(
        self, msg_signature: str, timestamp: str, nonce: str, post_data: str
    ) -> Tuple[bool, str]:
        """
        解密 POST 回调消息。post_data 为完整 POST body (XML 字符串)。
        从 XML 中解析出 <Encrypt> 后验签并解密。
        返回 (成功, 解密后的 XML 明文)；失败时 (False, 错误信息)。
        """
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(post_data)
            enc = root.find("Encrypt")
            if enc is None or enc.text is None:
                return False, "missing Encrypt in body"
            msg_encrypt = enc.text.strip()
            sig = self._signature(self.token, timestamp, nonce, msg_encrypt)
            if sig != msg_signature:
                return False, "signature mismatch"
            ok, msg = self._decrypt(msg_encrypt)
            if not ok:
                return False, msg
            return True, msg
        except Exception as e:
            logger.debug("decrypt_msg error: %s", e)
            return False, str(e)

    def encrypt_msg(self, reply_msg: str, timestamp: str, nonce: str) -> Tuple[bool, str]:
        """
        加密被动回复消息。返回 (成功, 加密后的完整 XML 响应体)；失败时 (False, 错误信息)。
        """
        try:
            ok, msg_encrypt = self._encrypt(reply_msg)
            if not ok:
                return False, msg_encrypt
            sig = self._signature(self.token, timestamp, nonce, msg_encrypt)
            xml = (
                f"<xml>"
                f"<Encrypt><![CDATA[{msg_encrypt}]]></Encrypt>"
                f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
                f"<TimeStamp>{timestamp}</TimeStamp>"
                f"<Nonce><![CDATA[{nonce}]]></Nonce>"
                f"</xml>"
            )
            return True, xml
        except Exception as e:
            logger.debug("encrypt_msg error: %s", e)
            return False, str(e)

    def _decrypt(self, msg_encrypt: str) -> Tuple[bool, str]:
        """Base64 解码后 AES 解密，去掉 16 随机字节和 4 字节长度，得到 msg，校验 receive_id。"""
        try:
            aes_msg = base64.b64decode(msg_encrypt)
        except Exception as e:
            return False, f"base64 decode error: {e}"
        try:
            cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
            rand_msg = cipher.decrypt(aes_msg)
            rand_msg = unpad(rand_msg, AES.block_size)
        except Exception as e:
            return False, f"aes decrypt error: {e}"
        if len(rand_msg) < 16 + 4:
            return False, "decrypt result too short"
        content = rand_msg[16:]
        msg_len = struct.unpack(">I", content[:4])[0]
        if msg_len < 0 or 16 + 4 + msg_len > len(rand_msg):
            return False, "invalid msg_len"
        msg = content[4 : 4 + msg_len].decode("utf-8", errors="replace")
        receive_id = content[4 + msg_len :].decode("utf-8", errors="replace")
        if self.receive_id and receive_id != self.receive_id:
            return False, "receive_id mismatch"
        return True, msg

    def _encrypt(self, msg: str) -> Tuple[bool, str]:
        """明文格式: random(16) + len(4 big-endian) + msg + receive_id，再 AES 加密并 Base64。"""
        try:
            raw = msg.encode("utf-8")
            receive_id = self.receive_id.encode("utf-8")
            rand_bytes = os.urandom(16)
            len_bytes = struct.pack(">I", len(raw))
            plain = rand_bytes + len_bytes + raw + receive_id
            plain = pad(plain, AES.block_size)
            cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
            encrypted = cipher.encrypt(plain)
            return True, base64.b64encode(encrypted).decode("ascii")
        except Exception as e:
            return False, str(e)
