"""
Audio Enhancement Module for Azure AI Transcript Summary

Tiered pipelines optimised for speech-to-text with an emphasis on
keeping quiet phrases audible:

  - minimal  : pass-through
  - standard : FFmpeg DSP — highpass/lowpass, light spectral denoise,
               makeup-gain compressor, EBU R128 loudness normalisation
               to -18 LUFS (fixes "some parts quiet, some parts fine").
  - advanced : adaptive profile selection (clean vs noisy room) +
               gentler noisereduce (prop_decrease ~0.55) +
               final LUFS target pass. Equivalent to the "เน้นคุณภาพ"
               profile — noticeably better than the standard chain.
"""

import os
import subprocess  # nosec B404 - ffmpeg invoked with fixed argv, no shell
import tempfile
from typing import Any, Optional, Tuple, List

import numpy as np

# --- Optional dependency detection ------------------------------------------------

_has_soundfile = False
_has_noisereduce = False
_has_resemble = False

# Defaults for optional imports (overwritten when available).
# pylint: disable=invalid-name  # these mirror third-party module names
sf: Any = None
nr: Any = None
torch: Any = None
denoise: Any = None
resemble_enhance_fn: Any = None
# pylint: enable=invalid-name

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
    # pylint: disable=import-error,no-name-in-module
    from resemble_enhance.enhancer.inference import (  # type: ignore[no-redef, import-not-found]
        denoise,
        enhance as resemble_enhance_fn,
    )
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

    def enhance(
        self,
        wav_bytes: bytes,
        method: str,
        original_filename: str = "",
    ) -> Tuple[bytes, Optional[str]]:
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

    def _enhance_advanced_pipeline(
        self, wav_bytes: bytes, fname: str
    ) -> Tuple[bytes, Optional[str]]:
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
                print(
                    f"[AUDIO] [{fname}]   Spectral gating failed: {err} "
                    f"— continuing with previous"
                )
            else:
                current = result
                size_mb = len(current) / 1024 / 1024
                print(
                    f"[AUDIO] [{fname}]   Spectral gating complete: {size_mb:.2f} MB"
                )
        else:
            print(f"[AUDIO] [{fname}]   Stage 2/3: SKIPPED (noisereduce not installed)")

        # Stage 3 — Resemble Enhance
        if self.has_resemble:
            print(f"[AUDIO] [{fname}]   Stage 3/3: Resemble Enhance (CPU)")
            result, err = self._enhance_resemble(current)
            if err:
                print(
                    f"[AUDIO] [{fname}]   Resemble Enhance failed: {err} "
                    f"— continuing with previous"
                )
            else:
                current = result
                size_mb = len(current) / 1024 / 1024
                print(
                    f"[AUDIO] [{fname}]   Resemble Enhance complete: {size_mb:.2f} MB"
                )
        else:
            print(f"[AUDIO] [{fname}]   Stage 3/3: SKIPPED (resemble-enhance not installed)")

        # Stage 4 — final EBU R128 loudness pass. Guarantees the "เน้นคุณภาพ"
        # tier ends at -18 LUFS regardless of what earlier stages did to
        # dynamics, so quiet phrases stay audible.
        print(f"[AUDIO] [{fname}]   Stage 4/4: EBU R128 loudness normalise (-18 LUFS)")
        result, err = self._apply_loudnorm(current)
        if err:
            print(
                f"[AUDIO] [{fname}]   Loudness normalise failed: {err} "
                f"— continuing with previous"
            )
        else:
            current = result
            size_mb = len(current) / 1024 / 1024
            print(
                f"[AUDIO] [{fname}]   Loudness normalise complete: {size_mb:.2f} MB"
            )

        print(f"[AUDIO] [{fname}] Advanced pipeline finished: {len(current) / 1024 / 1024:.2f} MB")
        return current, None

    # ------------------------------------------------------------------
    # Stage 1 — Classic DSP via FFmpeg
    # ------------------------------------------------------------------

    # EBU R128 loudness target for speech workflows. The analysis recommends
    # -20 to -18 LUFS; -18 LUFS keeps headroom while lifting quiet passages.
    _LUFS_TARGET = "-18"
    _TRUE_PEAK = "-1.5"
    _LRA = "11"

    # Speech-friendly bandpass. Keep more high-frequency energy than a phone
    # filter so weak consonants survive; still rolls off sibilance above 8 kHz.
    _BANDPASS = "highpass=f=80,lowpass=f=8000"

    # Light FFT denoise. Gentler than a hard noise gate so soft tails and
    # phrase starts are not chopped off.
    _SOFT_DENOISE = "afftdn=nr=12:nf=-40:tn=1"

    # Makeup-gain compressor replaces the old threshold-only compressor.
    # Lower threshold + lighter ratio + explicit makeup lifts soft phrases
    # without pumping the loud ones.
    _COMPRESSOR = (
        "acompressor=threshold=-22dB:ratio=2:attack=5:release=50:makeup=4"
    )

    def _loudnorm_chain(self) -> str:
        """EBU R128 two-pass-style single-pass loudnorm string."""
        return (
            f"loudnorm=I={self._LUFS_TARGET}:"
            f"TP={self._TRUE_PEAK}:LRA={self._LRA}"
        )

    def _enhance_dsp(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Speech-optimised FFmpeg chain with LUFS normalisation.

        Replaces the previous ``highpass=200, lowpass=3000, dynaudnorm,
        acompressor`` chain with:

        1. ``highpass=80, lowpass=8000``  — preserve consonants
        2. ``afftdn``                      — soft FFT denoise (instead of
           a hard noise gate that nibbles phrase starts)
        3. ``acompressor ... makeup=4``    — lift soft speech without
           pumping loud speech
        4. ``loudnorm I=-18``              — EBU R128 loudness target so
           the whole file sits at a consistent level
        """
        temp_in = None
        temp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_in = f.name
                f.write(wav_bytes)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_out = f.name

            af_chain = ",".join([
                self._BANDPASS,
                self._SOFT_DENOISE,
                self._COMPRESSOR,
                self._loudnorm_chain(),
            ])

            cmd = [
                "ffmpeg",
                "-i", temp_in,
                "-af", af_chain,
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                temp_out,
            ]

            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                cmd, capture_output=True, text=True, timeout=1800, check=False
            )
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
        except (OSError, ValueError) as e:
            return wav_bytes, f"FFmpeg DSP error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Adaptive profiling — decide clean vs noisy-room before denoise
    # ------------------------------------------------------------------

    def _estimate_noise_floor_db(self, data: np.ndarray) -> float:
        """Rough noise-floor estimate in dBFS from the 10th percentile RMS
        of 50 ms frames. Used to pick a denoise strength."""
        if data.size == 0:
            return -60.0
        if data.ndim > 1:
            data = data.mean(axis=1)
        # 50 ms frames @ 16 kHz ≈ 800 samples
        frame = 800
        n = (data.size // frame) * frame
        if n == 0:
            return -60.0
        frames = data[:n].reshape(-1, frame)
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        # Use the 10th percentile as the floor estimate
        floor = float(np.percentile(rms, 10))
        return 20.0 * float(np.log10(max(floor, 1e-7)))

    def _pick_denoise_strength(self, noise_floor_db: float) -> float:
        """Adaptive prop_decrease.

        Clean speech (floor <= -50 dB) -> 0.45 (barely touch it)
        Normal room (-50..-40 dB)      -> 0.55 (recommended default)
        Noisy room  (floor >  -40 dB)  -> 0.65 (still below old 0.75)
        """
        if noise_floor_db <= -50.0:
            return 0.45
        if noise_floor_db <= -40.0:
            return 0.55
        return 0.65

    # ------------------------------------------------------------------
    # Stage 2 — Spectral Gating via noisereduce
    # ------------------------------------------------------------------

    def _enhance_spectral(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Adaptive spectral gating.

        Replaces the old fixed two-pass noisereduce (stationary @ 0.75 +
        non-stationary @ 0.5) with an adaptive strategy that keeps weak
        consonants intact:

        * Estimate the noise floor and pick ``prop_decrease`` in
          ``[0.45, 0.55, 0.65]`` (clean / normal / noisy).
        * Run a single non-stationary pass — modern ``noisereduce``
          handles both noise types in one pass and a second stationary
          pass at 0.75 was over-attenuating phonemes during low-SNR
          moments.
        """
        temp_in = None
        temp_out = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_in = f.name
                f.write(wav_bytes)

            data, rate = sf.read(temp_in, dtype="float32")

            noise_floor_db = self._estimate_noise_floor_db(data)
            prop = self._pick_denoise_strength(noise_floor_db)
            print(
                f"[AUDIO]   Adaptive denoise: noise_floor={noise_floor_db:.1f} dBFS "
                f"-> prop_decrease={prop:.2f}"
            )

            cleaned = nr.reduce_noise(
                y=data,
                sr=rate,
                stationary=False,
                prop_decrease=prop,
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

        except (OSError, ValueError, RuntimeError) as e:
            return wav_bytes, f"noisereduce error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Final LUFS pass — fixes intra-file quiet/loud phases
    # ------------------------------------------------------------------

    def _apply_loudnorm(self, wav_bytes: bytes) -> Tuple[bytes, Optional[str]]:
        """Run an EBU R128 loudness-target pass. This is the single most
        important change for speech workflows: it replaces peak-based
        normalisation (which is blind to intra-file quiet phases)."""
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
                "-af", self._loudnorm_chain(),
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                temp_out,
            ]
            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                cmd, capture_output=True, text=True, timeout=1800, check=False
            )
            if result.returncode != 0:
                return wav_bytes, f"loudnorm failed: {result.stderr[:300]}"
            if not os.path.exists(temp_out):
                return wav_bytes, "loudnorm produced no output file"

            with open(temp_out, "rb") as f:
                enhanced = f.read()
            if len(enhanced) == 0:
                return wav_bytes, "loudnorm produced empty output"
            return enhanced, None
        except subprocess.TimeoutExpired:
            return wav_bytes, "loudnorm timed out"
        except (OSError, ValueError) as e:
            return wav_bytes, f"loudnorm error: {e}"
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
            subprocess.run(  # nosec B603 - fixed argv, no shell
                resample_cmd, capture_output=True, text=True, timeout=600, check=False
            )
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

        except (OSError, ValueError, RuntimeError) as e:
            return wav_bytes, f"Resemble Enhance error: {e}"
        finally:
            for p in (temp_in, temp_out):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
