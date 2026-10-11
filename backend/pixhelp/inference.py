"""llama-server process supervision: spawn, /health gate, restart with backoff."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

if sys.platform == "win32":
    _LOCALLAMA = Path(os.environ.get("LOCALAPPDATA", "")) / (
        "llama-b11552-bin-win-cpu-x64"
    )
else:
    _LOCALLAMA = Path("/nonexistent")

# Pinned filenames (specs.md §3.2) resolved lazily from the HF cache.
_VISION_WEIGHTS = "Qwen3VL-2B-Instruct-Q4_K_M.gguf"
_VISION_MMPROJ = "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"
_TEXT_WEIGHTS = "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def resolve_server_path(configured: str | None) -> Path:
    if configured:
        p = Path(configured)
        if not p.is_file():
            raise FileNotFoundError(f"inference.server_path not found: {p}")
        return p
    if sys.platform == "win32":
        candidate = _LOCALLAMA / "llama-server.exe"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "llama-server.exe not found; set inference.server_path in config.yaml"
    )


def resolve_hf_gguf(filename: str) -> Path:
    """Locate a pinned GGUF in the local HF cache (~/.cache/huggingface/hub)."""
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hub.is_dir():
        for snap in hub.glob(f"models--Qwen--*/snapshots/*/{filename}"):
            return snap
    # Windows-native path (the .exe side of a WSL launch).
    win_hub = Path(os.environ.get("USERPROFILE", "")) / ".cache" / "huggingface" / "hub"
    if win_hub.is_dir():
        for snap in win_hub.glob(f"models--Qwen--*/snapshots/*/{filename}"):
            return snap
    raise FileNotFoundError(
        f"{filename} not in the HF cache; download it or set the model path "
        "explicitly in config.yaml"
    )


class InferenceServer:
    """Supervises one llama-server.exe. ready() gates on GET /health == 200."""

    def __init__(
        self,
        server_path: Path,
        model: Path,
        mmproj: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        threads: int = 4,
        context: int = 4096,
    ):
        self.server_path = server_path
        self.model = model
        self.mmproj = mmproj
        self.host = host
        self.port = port
        self.threads = threads
        self.context = context
        self._proc: asyncio.subprocess.Process | None = None
        self._restart_delay = 1.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _args(self) -> list[str]:
        args = [
            str(self.server_path),
            "-m", str(self.model),
            "--host", self.host,
            "--port", str(self.port),
            "-t", str(self.threads),
            "-c", str(self.context),
            "--parallel", "1",
        ]
        if self.mmproj is not None:
            args += ["--mmproj", str(self.mmproj)]
        return args

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._args(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if not await self.wait_healthy(timeout_s=60.0):
            await self.stop()
            raise RuntimeError("llama-server failed /health within 60s")
        self._restart_delay = 1.0
        log.info("llama-server ready on %s (model=%s)", self.base_url, self.model.name)

    async def wait_healthy(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        async with aiohttp.ClientSession() as session:
            while asyncio.get_running_loop().time() < deadline:
                if self._proc is not None and self._proc.returncode is not None:
                    return False
                try:
                    async with session.get(
                        f"{self.base_url}/health",
                        timeout=aiohttp.ClientTimeout(total=2.0),
                    ) as resp:
                        if resp.status == 200:
                            return True
                except (aiohttp.ClientError, TimeoutError):
                    # Connection refused while the server boots / restarts;
                    # aiohttp 3.11+ raises the builtin TimeoutError on stalls.
                    pass
                await asyncio.sleep(0.5)
        return False

    async def ensure_running(self) -> bool:
        """Restart with exponential backoff if the process died. False = down."""
        if self._proc is not None and self._proc.returncode is None:
            return True
        await asyncio.sleep(self._restart_delay)
        self._restart_delay = min(self._restart_delay * 2, 60.0)
        try:
            await self.start()
            return True
        except (FileNotFoundError, RuntimeError) as exc:
            log.warning("llama-server restart failed: %s", exc)
            return False

    async def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
