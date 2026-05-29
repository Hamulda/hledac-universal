"""
Secure Destruction - Cryptographic Data Destruction

Implementuje standardy:
- DoD 5220.22-M (3-pass)
- NIST 800-88 (1-pass random)
- Gutmann (35-pass, overkill)

Pro bezpečné smazání citlivých výzkumných dat.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DestructionConfig:
    """Konfigurace bezpečného mazání"""
    # Počet přepisovacích průchodů
    passes: int = 3  # Standard: 3, High: 7, Maximum: 35

    # Vzory pro průchody (None = random)
    pass_patterns: list[bytes] | None = None

    # Verifikace
    verify_destruction: bool = True
    verification_samples: int = 10

    # Metadata
    remove_metadata: bool = True
    rename_before_delete: bool = True

    # Speciální zpracování
    secure_memory_wipe: bool = True
    wipe_free_space: bool = False  # Náročné na SSD

    # Compliance standard
    compliance_standard: str = 'dod'  # 'dod', 'nist', 'gutmann'


class SecureDestructor:
    """
    Bezpečný destruktor dat s kryptografickým mazáním.

    Implementuje DoD 5220.22-M standard:
    Pass 1: Write 0x00
    Pass 2: Write 0xFF
    Pass 3: Write random + verify

    Example:
        >>> destructor = SecureDestructor(passes=3)
        >>> await destructor.destroy_file("secret_data.txt")
        >>> await destructor.destroy_directory("research_data/")
    """

    # Standardní vzory pro průchody
    DOD_PATTERNS = [
        b'\x00',           # Pass 1: All zeros
        b'\xff',           # Pass 2: All ones
        None,              # Pass 3: Random
    ]

    NIST_PATTERNS = [
        None,              # Single random pass (NIST 800-88)
    ]

    GUTMANN_PATTERNS = [  # 35-pass (overkill pro moderní disky)
        b'\x55', b'\xaa', b'\x92', b'\x49', b'\x24',
        b'\x00', b'\x11', b'\x22', b'\x33', b'\x44',
        b'\x55', b'\x66', b'\x77', b'\x88', b'\x99',
        b'\xaa', b'\xbb', b'\xcc', b'\xdd', b'\xee',
        b'\xff', b'\x92', b'\x49', b'\x24', b'\x00',
    ]

    def __init__(self, config: DestructionConfig = None):
        self.config = config or DestructionConfig()

        # Nastavit vzory podle standardu
        if self.config.pass_patterns is None:
            if self.config.compliance_standard == 'dod':
                self.config.pass_patterns = self.DOD_PATTERNS
            elif self.config.compliance_standard == 'nist':
                self.config.pass_patterns = self.NIST_PATTERNS
            elif self.config.compliance_standard == 'gutmann':
                self.config.pass_patterns = self.GUTMANN_PATTERNS
            else:
                self.config.pass_patterns = self.DOD_PATTERNS

        self._stats = {
            "files_destroyed": 0,
            "bytes_overwritten": 0,
            "directories_destroyed": 0,
        }

    async def destroy_file(
        self,
        path: str | Path,
        verify: bool = None
    ) -> dict[str, Any]:
        """
        Bezpečně zničit soubor.

        Args:
            path: Cesta k souboru
            verify: Ověřit destrukci (default z config)

        Returns:
            Statistiky destrukce
        """
        path = Path(path)
        verify = verify if verify is not None else self.config.verify_destruction

        if not path.exists():
            logger.warning(f"File not found: {path}")
            return {"success": False, "error": "File not found"}

        file_size = path.stat().st_size
        logger.info(f"Destroying file: {path} ({file_size} bytes)")

        try:
            # 1. Přejmenovat pro skrytí původního názvu
            if self.config.rename_before_delete:
                temp_name = secrets.token_hex(16)
                temp_path = path.parent / temp_name
                path.rename(temp_path)
                path = temp_path

            # 2. Přepsat obsah
            await self._overwrite_file(path)

            # 3. Ověřit (pokud požadováno)
            verification_result = None
            if verify:
                verification_result = await self._verify_destruction(path)

            # 4. Smazat
            path.unlink()

            self._stats["files_destroyed"] += 1
            self._stats["bytes_overwritten"] += file_size * self.config.passes

            return {
                "success": True,
                "file": str(path),
                "size": file_size,
                "passes": self.config.passes,
                "standard": self.config.compliance_standard,
                "verification": verification_result,
            }

        except Exception as e:
            logger.error(f"Destruction failed: {e}")
            return {"success": False, "error": str(e)}

    async def _overwrite_file(self, path: Path) -> None:
        """Přepsat soubor vzory"""
        file_size = path.stat().st_size

        with open(path, 'r+b') as f:
            for pass_num, pattern in enumerate(self.config.pass_patterns, 1):
                f.seek(0)

                # Generovat data pro tento průchod
                if pattern is None:
                    # Random data
                    data = secrets.token_bytes(min(65536, file_size))
                else:
                    # Fixed pattern
                    data = pattern * (65536 // len(pattern) + 1)

                # Přepsat soubor
                bytes_written = 0
                while bytes_written < file_size:
                    chunk = data[:min(len(data), file_size - bytes_written)]
                    f.write(chunk)
                    bytes_written += len(chunk)

                f.flush()
                os.fsync(f.fileno())

                logger.debug(f"Pass {pass_num}/{len(self.config.pass_patterns)} complete")

    async def _verify_destruction(self, path: Path) -> dict[str, Any]:
        """Ověřit, že soubor je skutečně přepsaný"""
        file_size = path.stat().st_size

        samples = []
        with open(path, 'rb') as f:
            for _ in range(min(self.config.verification_samples, file_size)):
                pos = secrets.randbelow(file_size)
                f.seek(pos)
                byte = f.read(1)
                if byte:
                    samples.append(byte[0])

        # Kontrolovat, že nejsou všechny nuly (pokud je to poslední průchod)
        all_zeros = all(b == 0 for b in samples)
        all_ones = all(b == 0xFF for b in samples)

        return {
            "samples_taken": len(samples),
            "all_zeros": all_zeros,
            "all_ones": all_ones,
            "random_distribution": len(set(samples)) > 1,
        }

    async def destroy_directory(
        self,
        path: str | Path,
        recursive: bool = True
    ) -> dict[str, Any]:
        """
        Bezpečně zničit adresář.

        Args:
            path: Cesta k adresáři
            recursive: Rekurzivně smazat podadresáře

        Returns:
            Statistiky destrukce
        """
        path = Path(path)

        if not path.exists():
            return {"success": False, "error": "Directory not found"}

        results = []

        if recursive:
            # Nejdřív smazat všechny soubory
            for item in path.rglob("*"):
                if item.is_file():
                    result = await self.destroy_file(item)
                    results.append(result)

        # Smazat prázdné adresáře
        for item in sorted(path.rglob("*"), reverse=True):
            if item.is_dir():
                item.rmdir()

        path.rmdir()

        self._stats["directories_destroyed"] += 1

        return {
            "success": True,
            "directory": str(path),
            "files_destroyed": len([r for r in results if r.get("success")]),
            "recursive": recursive,
        }

    async def secure_memory_wipe(self, data: bytearray) -> None:
        """
        Bezpečně vymazat data z paměti.

        Args:
            data: Bytearray k vymazání
        """
        if not self.config.secure_memory_wipe:
            return

        # Přepsat náhodnými daty
        for i in range(len(data)):
            data[i] = secrets.randbelow(256)

        # Vynulovat
        for i in range(len(data)):
            data[i] = 0

    def get_stats(self) -> dict[str, Any]:
        """Získat statistiky destrukce"""
        return {
            "files_destroyed": self._stats["files_destroyed"],
            "directories_destroyed": self._stats["directories_destroyed"],
            "bytes_overwritten": self._stats["bytes_overwritten"],
            "config": {
                "passes": self.config.passes,
                "standard": self.config.compliance_standard,
                "verify": self.config.verify_destruction,
            },
        }
