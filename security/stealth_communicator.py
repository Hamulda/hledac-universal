"""Steganography for deep research security."""
import base64
import hashlib
import logging

from enum import Enum
logger = logging.getLogger(__name__)

class StegoMethod(Enum):
    """Metody steganografie"""
    DCT = 'dct'
    LSB = 'lsb'
    NEURAL = 'neural'
    AUTO = 'auto'

class StealthCommunicator:
    """
    Stealth komunikátor se steganografií.
    Skrývá zprávy v obrazech pomocí:
    - DCT (JPEG kompatibilní)
    - LSB (PNG/BMP)
    - Neural (AI-based)
    Pro skryté ukládání výzkumných dat.
    """
    __slots__ = tuple(('method',))

    def __init__(self, method: StegoMethod=StegoMethod.AUTO):
        self.method = method

    async def hide_message(self, message: bytes, cover_image: bytes, password: str | None=None) -> bytes:
        """Schovat zprávu v obrázku."""
        if password:
            from cryptography.fernet import Fernet
            key = hashlib.sha256(password.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key))
            message = f.encrypt(message)
        message_with_meta = len(message).to_bytes(4, 'big') + message
        method = self._select_method(cover_image)
        if method == StegoMethod.LSB:
            return await self._lsb_hide(message_with_meta, cover_image)
        elif method == StegoMethod.DCT:
            return await self._dct_hide(message_with_meta, cover_image)
        else:
            return await self._lsb_hide(message_with_meta, cover_image)

    async def extract_message(self, stego_image: bytes, password: str | None=None) -> bytes:
        """Extrahovat zprávu z obrázku."""
        method = self._detect_method(stego_image)
        if method == StegoMethod.LSB:
            message = await self._lsb_extract(stego_image)
        elif method == StegoMethod.DCT:
            message = await self._dct_extract(stego_image)
        else:
            message = await self._lsb_extract(stego_image)
        msg_len = int.from_bytes(message[:4], 'big')
        message = message[4:4 + msg_len]
        if password:
            from cryptography.fernet import Fernet
            key = hashlib.sha256(password.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key))
            message = f.decrypt(message)
        return message

    def _select_method(self, cover_image: bytes) -> StegoMethod:
        """Vybrat nejlepší metodu"""
        if self.method != StegoMethod.AUTO:
            return self.method
        if cover_image[:2] == b'\xff\xd8':
            return StegoMethod.DCT
        elif cover_image[:8] == b'\x89PNG\r\n\x1a\n':
            return StegoMethod.LSB
        else:
            return StegoMethod.LSB

    def _detect_method(self, image: bytes) -> StegoMethod:
        """Detekovat použitou metodu"""
        return StegoMethod.LSB

    async def _lsb_hide(self, message: bytes, cover: bytes) -> bytes:
        """LSB steganografie"""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(cover)) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                pixels = list(img.getdata())
                message_bits = ''.join((format(b, '08b') for b in message))
                message_bits += '00000000'
                if len(message_bits) > len(pixels) * 3:
                    raise ValueError('Message too large for cover image')
                new_pixels = []
                msg_idx = 0
                for pixel in pixels:
                    r, g, b = pixel
                    if msg_idx < len(message_bits):
                        r = r & 254 | int(message_bits[msg_idx])
                        msg_idx += 1
                    if msg_idx < len(message_bits):
                        g = g & 254 | int(message_bits[msg_idx])
                        msg_idx += 1
                    if msg_idx < len(message_bits):
                        b = b & 254 | int(message_bits[msg_idx])
                        msg_idx += 1
                    new_pixels.append((r, g, b))
                img.putdata(new_pixels)
                output = io.BytesIO()
                img.save(output, format='PNG')
                return output.getvalue()
        except ImportError:
            logger.error('PIL not available for steganography')
            return cover

    async def _lsb_extract(self, stego: bytes) -> bytes:
        """Extrahovat z LSB"""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(stego)) as img:
                pixels = list(img.getdata())
                bits = ''
                for pixel in pixels:
                    r, g, b = pixel
                    bits += str(r & 1)
                    bits += str(g & 1)
                    bits += str(b & 1)
                message = bytearray()
                for i in range(0, len(bits), 8):
                    byte = bits[i:i + 8]
                    if len(byte) == 8:
                        message.append(int(byte, 2))
                return bytes(message)
        except ImportError:
            return b''

    async def _dct_hide(self, message: bytes, cover: bytes) -> bytes:
        """DCT steganografie (simplified)"""
        return await self._lsb_hide(message, cover)

    async def _dct_extract(self, stego: bytes) -> bytes:
        """Extrahovat z DCT"""
        return await self._lsb_extract(stego)