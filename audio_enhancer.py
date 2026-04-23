"""
Audio Enhancement Module for Azure AI Transcript Summary

Tiered pipelines optimised for speech-to-text with an emphasis on
keeping quiet phrases audible AND staying within a constrained memory
budget (3 vCPU / 6 GiB container) for long files (200-500 MB / 1-4 h).

  - minimal  : pass-through
  - standard : FFmpeg DSP — highpass/lowpass, light spectral denoise,
               makeup-gain compressor, EBU R128 loudness normalisation
               to -18 LUFS.
  - advanced : adaptive profile + chunked noisereduce (5-min segments,
               concat back) + optional Resemble Enhance (skipped on
               very long / very large inputs) + final LUFS pass.

All internal stages operate on file paths so the full waveform never
sits in the Python heap as ``bytes``.  The legacy ``enhance(wav_bytes)``
entry point is preserved as a thin wrapper that writes/reads temp files.
"""

import gc
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


# --- Module-level tunables --------------------------------------------------------

# Chunk size for memory-bounded spectral denoise (seconds).
_CHUNK_SECONDS = 300                 # 5 min per chunk
# Files longer than this trigger chunked spectral denoise.
_CHUNK_THRESHOLD_SECONDS = 300
# Use a smaller FFT for very long inputs to cap RAM during noisereduce.
_LONG_AUDIO_THRESHOLD_SECONDS = 1800  # 30 min
# Skip Resemble Enhance entirely when inputs are this large/long
# (Resemble Enhance loads the full float32 tensor into RAM and is the
# single biggest OOM trigger).
_RESEMBLE_MAX_MB = 250
_RESEMBLE_MAX_SECONDS = 1800          # 30 min
# Above this duration, auto-degrade ``advanced`` → ``standard`` to keep
# the worker well clear of the 6 GiB ceiling.
_ADVANCED_MAX_SECONDS = 7200          # 2 h

# FFmpeg input args used by every shell-out — caps thread fan-out and
# tolerates corrupt frames in user uploads instead of failing outright.
_FFMPEG_INPUT_FLAGS = ["-fflags", "+discardcorrupt", "-threads", "2"]


def _ffmpeg_run(cmd: List[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command with a fixed argv (no shell)."""
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _safe_remove(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


class AudioEnhancer:
    """Tiered audio enhancement that operates on 16 kHz mono WAV files."""

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

    def enhance_path(
        self,
        in_path: str,
        out_path: str,
        method: str,
        original_filename: str = "",
    ) -> Optional[str]:
        """Enhance a WAV file on disk and write the result to *out_path*.

        Returns ``None`` on success or an error message string on failure.
        On any internal-stage failure the input is copied to *out_path*
        unchanged so the caller can always proceed with transcription.
        """
        fname = original_filename or os.path.basename(in_path) or "audio"

        if method == "minimal":
            print(f"[AUDIO] [{fname}] Skipping enhancement (minimal mode)")
            return self._copy_file(in_path, out_path)

        if method == "standard":
            print(f"[AUDIO] [{fname}] Applying DSP filters (standard mode)")
            err = self._enhance_dsp_path(in_path, out_path)
            if err:
                self._copy_file(in_path, out_path)
            return err

        if method == "advanced":
            print(f"[AUDIO] [{fname}] Applying full enhancement pipeline (advanced mode)")
            return self._enhance_advanced_path(in_path, out_path, fname)

        print(f"[AUDIO] [{fname}] Unknown method '{method}', falling back to standard DSP")
        err = self._enhance_dsp_path(in_path, out_path)
        if err:
            self._copy_file(in_path, out_path)
        return err

    def enhance(
        self,
        wav_bytes: bytes,
        method: str,
        original_filename: str = "",
    ) -> Tuple[bytes, Optional[str]]:
        """Backward-compatible bytes API.

        Internally writes *wav_bytes* to a temp file, runs
        :py:meth:`enhance_path`, then reads the result back. New callers
        should prefer :py:meth:`enhance_path` to keep large audio off the
        Python heap.
        """
        in_path = None
        out_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                in_path = f.name
                f.write(wav_bytes)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out_path = f.name

            err = self.enhance_path(in_path, out_path, method, original_filename)

            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return wav_bytes, err or "enhancement produced no output"

            with open(out_path, "rb") as f:
                enhanced = f.read()
            return enhanced, err
        finally:
            _safe_remove(in_path)
            _safe_remove(out_path)

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_file(src: str, dst: str) -> Optional[str]:
        try:
            with open(src, "rb") as fin, open(dst, "wb") as fout:
                while True:
                    buf = fin.read(1024 * 1024)
                    if not buf:
                        break
                    fout.write(buf)
            return None
        except OSError as e:
            return f"copy failed: {e}"

    @staticmethod
    def _probe_duration(path: str) -> float:
        """Return media duration in seconds via ``ffprobe``; 0.0 on failure."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        try:
            result = _ffmpeg_run(cmd, timeout=60)
            if result.returncode != 0:
                return 0.0
            return float(result.stdout.strip() or 0.0)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return 0.0

    # ------------------------------------------------------------------
    # Advanced pipeline — chains DSP → (chunked) noisereduce → Resemble
    # ------------------------------------------------------------------

    def _enhance_advanced_path(
        self, in_path: str, out_path: str, fname: str
    ) -> Optional[str]:
        """Run the advanced enhancement chain on disk, gating expensive
        stages by duration / size to stay within the 6 GiB worker."""
        duration = self._probe_duration(in_path)
        size_mb = os.path.getsize(in_path) / 1024 / 1024
        print(
            f"[AUDIO] [{fname}]   Source: {size_mb:.1f} MB, "
            f"{duration:.1f} s ({duration / 60:.1f} min)"
        )

        if duration > _ADVANCED_MAX_SECONDS:
            return self._auto_degrade_to_standard(in_path, out_path, fname)

        scratch_a = scratch_b = None
        try:
            scratch_a = self._scratch_wav()
            scratch_b = self._scratch_wav()
            current = in_path

            # Stage 1 — FFmpeg DSP
            current = self._run_stage(
                fname, "1/4", "FFmpeg DSP filters", current, scratch_a, scratch_b,
                self._enhance_dsp_path,
                "DSP complete",
            )

            # Stage 2 — noisereduce spectral gating (chunked for long files)
            current = self._run_stage_gated(
                fname, "2/4", "noisereduce spectral gating",
                current, scratch_a, scratch_b,
                enabled=self.has_noisereduce,
                disabled_reason="noisereduce not installed",
                runner=lambda src, dst: self._spectral_path(src, dst, duration, fname),
                done_label="Spectral gating complete",
            )

            # Stage 3 — Resemble Enhance (gated by install + size + duration)
            resemble_skip = self._resemble_skip_reason(size_mb, duration)
            current = self._run_stage_gated(
                fname, "3/4", "Resemble Enhance (CPU)",
                current, scratch_a, scratch_b,
                enabled=resemble_skip is None,
                disabled_reason=resemble_skip or "",
                runner=self._resemble_path,
                done_label="Resemble Enhance complete",
            )

            # Stage 4 — final loudness normalisation, written straight to out_path
            print(f"[AUDIO] [{fname}]   Stage 4/4: EBU R128 loudnorm (-18 LUFS)")
            err = self._loudnorm_path(current, out_path)
            if err:
                print(
                    f"[AUDIO] [{fname}]   Loudness normalise failed: {err} "
                    f"— writing previous stage output to final"
                )
                self._copy_file(current, out_path)

            self._log_size(fname, out_path, "Advanced pipeline finished")
            return None
        finally:
            _safe_remove(scratch_a)
            _safe_remove(scratch_b)
            gc.collect()

    def _auto_degrade_to_standard(
        self, in_path: str, out_path: str, fname: str
    ) -> Optional[str]:
        """Drop from advanced → standard for very long files."""
        print(
            f"[AUDIO] [{fname}]   Duration > {_ADVANCED_MAX_SECONDS}s — "
            f"auto-degrading 'advanced' → 'standard' to protect memory"
        )
        err = self._enhance_dsp_path(in_path, out_path)
        if err:
            self._copy_file(in_path, out_path)
        return err

    @staticmethod
    def _resemble_skip_reason(size_mb: float, duration: float) -> Optional[str]:
        """Return None if Resemble Enhance should run; else the human-readable
        reason it is being skipped."""
        if not _has_resemble:
            return "resemble-enhance not installed"
        if size_mb > _RESEMBLE_MAX_MB or duration > _RESEMBLE_MAX_SECONDS:
            return (
                f"file {size_mb:.0f} MB / {duration:.0f} s exceeds "
                f"Resemble limits {_RESEMBLE_MAX_MB} MB / "
                f"{_RESEMBLE_MAX_SECONDS} s — would OOM"
            )
        return None

    def _run_stage(
        self, fname, label, title, current, scratch_a, scratch_b, runner, done_label
    ):
        """Execute a stage; on success swap to the alternate scratch, on
        failure keep *current*. Returns the new current path."""
        print(f"[AUDIO] [{fname}]   Stage {label}: {title}")
        next_out = scratch_b if current is scratch_a else scratch_a
        err = runner(current, next_out)
        if err:
            print(f"[AUDIO] [{fname}]   Stage {label} failed: {err} — continuing")
            return current
        self._log_size(fname, next_out, done_label)
        return next_out

    def _run_stage_gated(
        self, fname, label, title, current, scratch_a, scratch_b,
        enabled, disabled_reason, runner, done_label,
    ):
        """Wrapper for `_run_stage` that prints a SKIPPED line when the
        stage is gated off by capability/size/duration checks."""
        if not enabled:
            print(f"[AUDIO] [{fname}]   Stage {label}: SKIPPED ({disabled_reason})")
            return current
        return self._run_stage(
            fname, label, title, current, scratch_a, scratch_b, runner, done_label
        )

    @staticmethod
    def _scratch_wav() -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            return f.name

    @staticmethod
    def _log_size(fname: str, path: str, label: str) -> None:
        try:
            mb = os.path.getsize(path) / 1024 / 1024
            print(f"[AUDIO] [{fname}]   {label}: {mb:.2f} MB")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Stage 1 — Classic DSP via FFmpeg
    # ------------------------------------------------------------------

    _LUFS_TARGET = "-18"
    _TRUE_PEAK = "-1.5"
    _LRA = "11"

    _BANDPASS = "highpass=f=80,lowpass=f=8000"
    _SOFT_DENOISE = "afftdn=nr=12:nf=-40:tn=1"
    _COMPRESSOR = (
        "acompressor=threshold=-22dB:ratio=2:attack=5:release=50:makeup=4"
    )

    def _loudnorm_chain(self) -> str:
        return (
            f"loudnorm=I={self._LUFS_TARGET}:"
            f"TP={self._TRUE_PEAK}:LRA={self._LRA}"
        )

    def _enhance_dsp_path(self, in_path: str, out_path: str) -> Optional[str]:
        """Speech-optimised FFmpeg chain with LUFS normalisation, on disk."""
        af_chain = ",".join([
            self._BANDPASS,
            self._SOFT_DENOISE,
            self._COMPRESSOR,
            self._loudnorm_chain(),
        ])
        cmd = [
            "ffmpeg",
            *_FFMPEG_INPUT_FLAGS,
            "-i", in_path,
            "-af", af_chain,
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            out_path,
        ]
        try:
            result = _ffmpeg_run(cmd)
            if result.returncode != 0:
                return f"FFmpeg DSP failed: {result.stderr[-500:]}"
            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return "FFmpeg DSP produced empty output"
            return None
        except subprocess.TimeoutExpired:
            return "FFmpeg DSP timed out"
        except (OSError, ValueError) as e:
            return f"FFmpeg DSP error: {e}"

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
        frame = 800  # 50 ms @ 16 kHz
        n = (data.size // frame) * frame
        if n == 0:
            return -60.0
        frames = data[:n].reshape(-1, frame)
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        floor = float(np.percentile(rms, 10))
        return 20.0 * float(np.log10(max(floor, 1e-7)))

    def _pick_denoise_strength(self, noise_floor_db: float) -> float:
        if noise_floor_db <= -50.0:
            return 0.45
        if noise_floor_db <= -40.0:
            return 0.55
        return 0.65

    # ------------------------------------------------------------------
    # Stage 2 — Spectral Gating via noisereduce (chunked for long inputs)
    # ------------------------------------------------------------------

    def _spectral_path(
        self,
        in_path: str,
        out_path: str,
        duration: float,
        fname: str,
    ) -> Optional[str]:
        """Dispatch single-pass vs chunked spectral denoise based on duration."""
        if duration <= _CHUNK_THRESHOLD_SECONDS:
            return self._spectral_single(in_path, out_path, duration)
        return self._spectral_chunked(in_path, out_path, duration, fname)

    def _spectral_single(
        self, in_path: str, out_path: str, duration: float
    ) -> Optional[str]:
        """Single-pass adaptive spectral gating (short files)."""
        try:
            data, rate = sf.read(in_path, dtype="float32")
            noise_floor_db = self._estimate_noise_floor_db(data)
            prop = self._pick_denoise_strength(noise_floor_db)
            n_fft = 1024 if duration > _LONG_AUDIO_THRESHOLD_SECONDS else 2048
            print(
                f"[AUDIO]   Adaptive denoise: "
                f"noise_floor={noise_floor_db:.1f} dBFS "
                f"prop_decrease={prop:.2f} n_fft={n_fft}"
            )
            cleaned = nr.reduce_noise(
                y=data,
                sr=rate,
                stationary=False,
                prop_decrease=prop,
                n_fft=n_fft,
                n_jobs=1,
            )
            sf.write(out_path, cleaned, rate, subtype="PCM_16")
            del data, cleaned
            gc.collect()
            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return "noisereduce produced empty output"
            return None
        except (OSError, ValueError, RuntimeError) as e:
            return f"noisereduce error: {e}"

    def _spectral_chunked(
        self, in_path: str, out_path: str, duration: float, fname: str
    ) -> Optional[str]:
        """Chunked spectral denoise for long files.

        Strategy:
          1. ``ffmpeg -f segment -segment_time 300`` to split into 5 min
             WAV chunks (no re-encode).
          2. Run noisereduce on each chunk independently, freeing memory
             between chunks.
          3. Concat the cleaned chunks back with ``ffmpeg -f concat``.
        """
        chunk_dir = tempfile.mkdtemp(prefix="ae_chunks_")
        try:
            chunks, err = self._split_into_chunks(in_path, chunk_dir)
            if err:
                return err

            n_fft = 1024 if duration > _LONG_AUDIO_THRESHOLD_SECONDS else 2048
            print(
                f"[AUDIO] [{fname}]   Chunked denoise: {len(chunks)} × "
                f"{_CHUNK_SECONDS}s, n_fft={n_fft}"
            )

            prop = self._estimate_chunk_prop(chunks[0], fname)
            cleaned_paths = self._denoise_each_chunk(
                chunks, chunk_dir, prop, n_fft, fname
            )
            return self._concat_chunks(cleaned_paths, chunk_dir, out_path)
        except (OSError, ValueError, RuntimeError) as e:
            return f"chunked spectral error: {e}"
        finally:
            self._cleanup_dir(chunk_dir)
            gc.collect()

    @staticmethod
    def _split_into_chunks(
        in_path: str, chunk_dir: str
    ) -> Tuple[List[str], Optional[str]]:
        """Run the ffmpeg segmenter and list the resulting chunk paths."""
        seg_pattern = os.path.join(chunk_dir, "in_%04d.wav")
        split_cmd = [
            "ffmpeg",
            *_FFMPEG_INPUT_FLAGS,
            "-i", in_path,
            "-f", "segment",
            "-segment_time", str(_CHUNK_SECONDS),
            "-c", "copy",
            "-reset_timestamps", "1",
            "-y",
            seg_pattern,
        ]
        split_res = _ffmpeg_run(split_cmd)
        if split_res.returncode != 0:
            return [], f"chunk split failed: {split_res.stderr[-300:]}"
        chunks = sorted(
            os.path.join(chunk_dir, f)
            for f in os.listdir(chunk_dir)
            if f.startswith("in_") and f.endswith(".wav")
        )
        if not chunks:
            return [], "chunk split produced no segments"
        return chunks, None

    def _estimate_chunk_prop(self, first_chunk: str, fname: str) -> float:
        """Estimate noise floor from the first chunk only — speech recordings
        rarely change room/noise mid-file, and probing once saves RAM."""
        first_data, first_rate = sf.read(first_chunk, dtype="float32")
        noise_floor_db = self._estimate_noise_floor_db(first_data)
        prop = self._pick_denoise_strength(noise_floor_db)
        del first_data
        gc.collect()
        print(
            f"[AUDIO] [{fname}]   Adaptive (from chunk 0): "
            f"noise_floor={noise_floor_db:.1f} dBFS "
            f"prop_decrease={prop:.2f} sample_rate={first_rate}"
        )
        return prop

    def _denoise_one_chunk(
        self, chunk_in: str, chunk_out: str, prop: float, n_fft: int
    ) -> Optional[str]:
        """Run noisereduce on a single chunk; return error string or None."""
        try:
            data, rate = sf.read(chunk_in, dtype="float32")
            cleaned = nr.reduce_noise(
                y=data,
                sr=rate,
                stationary=False,
                prop_decrease=prop,
                n_fft=n_fft,
                n_jobs=1,
            )
            sf.write(chunk_out, cleaned, rate, subtype="PCM_16")
            del data, cleaned
            return None
        except (OSError, ValueError, RuntimeError) as e:
            return str(e)
        finally:
            gc.collect()

    def _denoise_each_chunk(
        self,
        chunks: List[str],
        chunk_dir: str,
        prop: float,
        n_fft: int,
        fname: str,
    ) -> List[str]:
        """Iterate chunk-by-chunk; collect cleaned paths (raw on failure)."""
        cleaned_paths: List[str] = []
        for idx, chunk_in in enumerate(chunks):
            chunk_out = os.path.join(chunk_dir, f"out_{idx:04d}.wav")
            err = self._denoise_one_chunk(chunk_in, chunk_out, prop, n_fft)
            if err is None:
                cleaned_paths.append(chunk_out)
                _safe_remove(chunk_in)
            else:
                print(
                    f"[AUDIO] [{fname}]   Chunk {idx} denoise failed: {err} "
                    f"— using raw chunk"
                )
                cleaned_paths.append(chunk_in)
        return cleaned_paths

    @staticmethod
    def _concat_chunks(
        cleaned_paths: List[str], chunk_dir: str, out_path: str
    ) -> Optional[str]:
        """Build the ffmpeg concat list and stitch the cleaned chunks back."""
        list_path = os.path.join(chunk_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in cleaned_paths:
                safe = p.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        concat_cmd = [
            "ffmpeg",
            *_FFMPEG_INPUT_FLAGS,
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-y",
            out_path,
        ]
        concat_res = _ffmpeg_run(concat_cmd)
        if concat_res.returncode != 0:
            return f"chunk concat failed: {concat_res.stderr[-300:]}"
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            return "chunk concat produced empty output"
        return None

    @staticmethod
    def _cleanup_dir(chunk_dir: str) -> None:
        """Best-effort cleanup of a scratch directory."""
        try:
            for f in os.listdir(chunk_dir):
                _safe_remove(os.path.join(chunk_dir, f))
            os.rmdir(chunk_dir)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Final LUFS pass
    # ------------------------------------------------------------------

    def _loudnorm_path(self, in_path: str, out_path: str) -> Optional[str]:
        """Run an EBU R128 loudness-target pass on disk."""
        cmd = [
            "ffmpeg",
            *_FFMPEG_INPUT_FLAGS,
            "-i", in_path,
            "-af", self._loudnorm_chain(),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            out_path,
        ]
        try:
            result = _ffmpeg_run(cmd)
            if result.returncode != 0:
                return f"loudnorm failed: {result.stderr[-300:]}"
            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return "loudnorm produced empty output"
            return None
        except subprocess.TimeoutExpired:
            return "loudnorm timed out"
        except (OSError, ValueError) as e:
            return f"loudnorm error: {e}"

    # ------------------------------------------------------------------
    # Stage 3 — Resemble Enhance (AI denoiser + enhancer, CPU)
    # ------------------------------------------------------------------

    def _resample_to_16k(self, wav_path: str) -> None:
        """Resample WAV file to 16 kHz in-place using FFmpeg."""
        temp_resampled = wav_path + ".16k.wav"
        try:
            cmd = [
                "ffmpeg",
                *_FFMPEG_INPUT_FLAGS,
                "-i", wav_path,
                "-ar", "16000", "-ac", "1",
                "-acodec", "pcm_s16le", "-y",
                temp_resampled,
            ]
            _ffmpeg_run(cmd, timeout=600)
            if os.path.exists(temp_resampled) and os.path.getsize(temp_resampled) > 0:
                os.replace(temp_resampled, wav_path)
        finally:
            _safe_remove(temp_resampled)

    def _resemble_path(self, in_path: str, out_path: str) -> Optional[str]:
        """Run Resemble Enhance denoiser + enhancer on CPU, on disk."""
        try:
            data, rate = sf.read(in_path, dtype="float32")
            audio_tensor = torch.from_numpy(data).unsqueeze(0)  # (1, samples)
            del data
            device = torch.device("cpu")

            denoised = denoise(audio_tensor, rate, device)
            del audio_tensor
            gc.collect()

            enhanced_tensor, new_rate = resemble_enhance_fn(
                denoised, rate, device, nfe=32
            )
            del denoised
            gc.collect()

            enhanced_np = enhanced_tensor.squeeze(0).cpu().numpy()
            del enhanced_tensor
            gc.collect()

            sf.write(out_path, enhanced_np, new_rate, subtype="PCM_16")
            del enhanced_np
            gc.collect()

            if new_rate != 16000:
                self._resample_to_16k(out_path)

            if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return "Resemble Enhance produced empty output"
            return None
        except (OSError, ValueError, RuntimeError) as e:
            return f"Resemble Enhance error: {e}"
