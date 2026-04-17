"""
Audio Enhancement Module for Azure AI Transcript Summary
Provides tiered audio quality improvement before transcription:
  - minimal:  No enhancement (pass-through)
  - standard: FFmpeg DSP filters (highpass, lowpass, dynaudnorm, acompressor)
  - advanced: Full pipeline — FFmpeg DSP → noisereduce spectral gating → Resemble Enhance
"""

import os
import subprocess
import tempfile
import time
from typing import Any, Optional, Tuple, List

import numpy as np

# --- Optional dependency detection ------------------------------------------------

_has_soundfile = False
_has_noisereduce = False
_has_resemble = False

# Defaults for optional imports (overwritten when available)
sf: Any = None
nr: Any = None
torch: Any = None
denoise: Any = None
resemble_enhance_fn: Any = None

try:
    import soundfile as sf  # type: ignore[no-redef]
    _has_soundfile = True
except ImportError:
    pass

try:
    import noisereduce as nr  # type: ignore[no-redef]
    _has_noisereduce = True
except ImportError:
    pass

try:
    from resemble_enhance.enhancer.inference import denoise, enhance as resemble_enhance_fn  # type: ignore[no-redef, import-not-found]
    import torch  # type: ignore[no-redef]
    _has_resemble = True
except ImportError:
    pass


class AudioEnhancer:
    """Tiered audio enhancement that operates on 16 kHz mono WAV bytes."""

    def __init__(self):
        self.has_soundfile = _has_soundfile
        self.has_noisereduce = _has_noisereduce and _has_soundfile
        self.has_resemble = _has_resemble

        methods = self.get_available_methods()
        print(f"[AUDIO] AudioEnhancer initialized — available methods: {methods}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_available_methods(self) -> List[str]:
        """Return list of currently usable enhancement methods."""
        methods = ["minimal", "standard"]  # always available (FFmpeg)
        if self.has_noisereduce:
            methods.append("noisereduce")
        if self.has_resemble:
            methods.append("resemble")
        return methods

    def enhance(self, wav_bytes: bytes, method: str, original_filename: str = "") -> Tuple[bytes, Optional[str]]:
        """
        Enhance WAV audio bytes according to *method*.

        Parameters
        ----------
        wav_bytes : bytes
            16 kHz, mono, 16-bit PCM WAV data.
        method : str
            One of ``"minimal"``, ``"standard"``, ``"advanced"``.
        original_filename : str
            Used only for logging.

        Returns
        -------
        (enhanced_bytes, error_message)
            *error_message* is ``None`` on success.
        """
        fname = original_filename or "audio"

        if method == "minimal":
            print(f"[AUDIO] [{fname}] Skipping enhancement (minimal mode)")
            return wav_bytes, None

        if method == "standard":
            print(f"[AUDIO] [{fname}] Applying DSP filters (standard mode)")
            return self._enhance_dsp(wav_bytes)

        if method == "advanced":
            print(f"[AUDIO] [{fname}] Applying full enhancement pipeline (advanced mode)")
            return self._enhance_advanced_pipeline(wav_bytes, fname)

        # Unknown method — fall back to standard
        print(f"[AUDIO] [{fname}] Unknown method '{method}', falling back to standard DSP")
        return self._enhance_dsp(wav_bytes)

    # ------------------------------------------------------------------
    # Advanced pipeline — chains DSP → noisereduce → Resemble Enhance
    # ------------------------------------------------------------------

    def _enhance_advanced_pipeline(self, wav_bytes: bytes, fname: str) -> Tuple[bytes, Optional[str]]:
        """Run all three enhancement stages sequentially."""
        current = wav_bytes

        # Stage 1 — FFmpeg DSP
        print(f"[AUDIO] [{fname}]   Stage 1/3: FFmpeg DSP filters")
        result, err = self._enhance_dsp(current)
        if err:
            print(f"[AUDIO] [{fname}]   DSP stage failed: {err} — continuing with original")
        else:
            current = result
            print(f"[AUDIO] [{fname}]   DSP complete: {len(current) / 1024 / 1024:.2f} MB")

        # Stage 2 — noisereduce spectral gating
        if self.has_noisereduce:
            print(f"[AUDIO] [{fname}]   Stage 2/3: noisereduce spectral gating")
            result, err = self._enhance_spectral(current)
            if err:
                print(f"[AUDIO] [{fname}]   Spectral gating failed: {err} — continuing with previous")
            else:
                current = result
                print(f"[AUDIO] [{fname}]   Spectral gating complete: {len(current) / 1024 / 1024:.2f} MB")
        else:
            print(f"[AUDIO] [{fname}]   Stage 2/3: SKIPPED (noisereduce not installed)")

        # Stage 3 — Resemble Enhance
        if self.has_resemble:
            print(f"[AUDIO] [{fname}]   Stage 3/3: Resemble Enhance (CPU)")
            result, err = self._enhance_resemble(current)
            if err:
                print(f"[AUDIO] [{fname}]   Resemble Enhance failed: {err} — continuing with previous")
            else:
                current = result
                print(f"[AUDIO] [{fname}]   Resemble Enhance complete: {len(current) / 1024 / 1024:.2f} MB")
        else:
            print(f"[AUDIO] [{fname}]   Stage 3/3: SKIPPED (resemble-enhance not installed)")

        print(f"[AUDIO] [{fname}] Advanced pipeline finished: {len(current) / 1024 / 1024:.2f} MB")
        return current, None

    # ------------------------------------------------------------------
    # Stage 1 — Classic DSP via FFmpeg
    # ------------------------------------------------------------------

    def _enhance_dsp(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Apply FFmpeg highpass + lowpass + dynaudnorm + acompressor filters."""
        temp_in = None
        temp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_in = f.name
                f.write(wav_bytes)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_out = f.name

            cmd = [
                "ffmpeg",
                "-i", temp_in,
                "-af", "highpass=f=200,lowpass=f=3000,dynaudnorm,acompressor",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                temp_out,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                return wav_bytes, f"FFmpeg DSP failed: {result.stderr[:500]}"

            if not os.path.exists(temp_out):
                return wav_bytes, "FFmpeg DSP produced no output file"

            with open(temp_out, "rb") as f:
                enhanced = f.read()

            if len(enhanced) == 0:
                return wav_bytes, "FFmpeg DSP produced empty output"

            return enhanced, None

        except subprocess.TimeoutExpired:
            return wav_bytes, "FFmpeg DSP timed out"
        except Exception as e:
            return wav_bytes, f"FFmpeg DSP error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Stage 2 — Spectral Gating via noisereduce
    # ------------------------------------------------------------------

    def _enhance_spectral(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Two-pass noisereduce: stationary noise then non-stationary noise."""
        temp_in = None
        temp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_in = f.name
                f.write(wav_bytes)

            data, rate = sf.read(temp_in, dtype="float32")

            # Pass 1 — stationary noise (constant hum / hiss)
            cleaned = nr.reduce_noise(
                y=data,
                sr=rate,
                stationary=True,
                prop_decrease=0.75,
                n_fft=2048,
                n_jobs=1,
            )

            # Pass 2 — non-stationary noise (intermittent background)
            cleaned = nr.reduce_noise(
                y=cleaned,
                sr=rate,
                stationary=False,
                prop_decrease=0.5,
                n_fft=2048,
                n_jobs=1,
            )

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_out = f.name

            sf.write(temp_out, cleaned, rate, subtype="PCM_16")

            with open(temp_out, "rb") as f:
                enhanced = f.read()

            if len(enhanced) == 0:
                return wav_bytes, "noisereduce produced empty output"

            return enhanced, None

        except Exception as e:
            return wav_bytes, f"noisereduce error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Stage 3 — Resemble Enhance (AI denoiser + enhancer, CPU)
    # ------------------------------------------------------------------

    def _resample_to_16k(self, wav_path: str) -> None:
        """Resample WAV file to 16 kHz in-place using FFmpeg."""
        temp_resampled = wav_path + ".16k.wav"
        try:
            resample_cmd = [
                "ffmpeg", "-i", wav_path,
                "-ar", "16000", "-ac", "1",
                "-acodec", "pcm_s16le", "-y",
                temp_resampled,
            ]
            subprocess.run(resample_cmd, capture_output=True, text=True, timeout=600)
            if os.path.exists(temp_resampled) and os.path.getsize(temp_resampled) > 0:
                os.replace(temp_resampled, wav_path)
        finally:
            if os.path.exists(temp_resampled):
                try:
                    os.remove(temp_resampled)
                except OSError:
                    pass

    def _enhance_resemble(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Run Resemble Enhance denoiser + enhancer on CPU."""
        temp_in = None
        temp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_in = f.name
                f.write(wav_bytes)

            data, rate = sf.read(temp_in, dtype="float32")
            audio_tensor = torch.from_numpy(data).unsqueeze(0)  # (1, samples)
            device = torch.device("cpu")

            denoised = denoise(audio_tensor, rate, device)
            enhanced_tensor, new_rate = resemble_enhance_fn(
                denoised, rate, device, nfe=32
            )

            enhanced_np = enhanced_tensor.squeeze(0).cpu().numpy()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_out = f.name
            sf.write(temp_out, enhanced_np, new_rate, subtype="PCM_16")

            if new_rate != 16000:
                self._resample_to_16k(temp_out)

            with open(temp_out, "rb") as f:
                enhanced = f.read()

            if len(enhanced) == 0:
                return wav_bytes, "Resemble Enhance produced empty output"

            return enhanced, None

        except Exception as e:
            return wav_bytes, f"Resemble Enhance error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
