"""
Audio Preprocessing Module — Lightweight, FFmpeg-first approach.

Strategy:
  - FFmpeg handles *all* heavy lifting (streaming, low RAM, any file size up to 500 MB).
  - Files >200 MB are split into time-based chunks, each chunk is enhanced
    independently, then chunks are concatenated back.
  - Files ≤200 MB are processed as a single FFmpeg pass.
  - Optional Python-level refinement (noisereduce / scipy) is applied on the
    WAV output for files ≤50 MB only, for extra noise reduction quality.

Processing levels:
  - minimal : FFmpeg loudnorm only (fastest)
  - standard: FFmpeg highpass + lowpass + loudnorm (light, fast)
  - advanced : FFmpeg full chain (noise gate, EQ, compressor, limiter, loudnorm)
               + Python noisereduce + scipy speech boost (for files ≤50 MB)
"""

import os
import io
import json
import tempfile
import subprocess
import time
import numpy as np
from typing import Tuple, Dict, Optional, List

import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHUNK_THRESHOLD_MB = 200          # files above this are split into chunks
CHUNK_DURATION_SECONDS = 600      # 10-minute chunks for FFmpeg splitting
MAX_FILE_MB = int(getattr(config, "MAX_AUDIO_PREPROCESS_MB", 500))
PYTHON_REFINE_THRESHOLD_MB = 50   # Python refinement only for files ≤ this


# =========================================================================
# Public API — enhance_audio_file
# =========================================================================

def enhance_audio_file(
    input_path: str,
    filename: str,
    settings: Optional[Dict] = None,
) -> Tuple[str, Dict]:
    """Enhance an audio file on disk.  Main entry point.

    Accepts any format FFmpeg can read (m4a, mp3, wav, ogg, mp4, …).
    Returns (output_wav_path, metadata).  Caller owns cleanup of output file.

    The output is always 16 kHz mono PCM WAV — optimal for Azure STT.
    """
    settings = settings or {}
    level = settings.get("audio_processing", getattr(config, "DEFAULT_AUDIO_PROCESSING", "standard"))
    metadata: Dict = {
        "processing_level": level,
        "applied_methods": [],
        "filename": filename,
    }

    file_size = os.path.getsize(input_path)
    file_size_mb = file_size / (1024 * 1024)
    print(f"[AUDIO] Enhancing {filename} ({file_size_mb:.1f} MB) — level={level}")

    if file_size_mb > MAX_FILE_MB:
        print(f"[WARN] File ({file_size_mb:.0f} MB) exceeds {MAX_FILE_MB} MB hard limit — skipping enhancement")
        metadata["skipped"] = "exceeds_max_size"
        return input_path, metadata

    try:
        if file_size_mb > CHUNK_THRESHOLD_MB:
            out_path = _enhance_chunked(input_path, level, metadata)
        else:
            out_path = _enhance_single(input_path, level, metadata)

        # Optional Python refinement for small-enough WAVs (advanced only)
        if file_size_mb <= PYTHON_REFINE_THRESHOLD_MB and level == "advanced":
            out_path = _python_refine(out_path, level, metadata)

        # Verify output exists and has content
        if not os.path.exists(out_path) or os.path.getsize(out_path) < 100:
            print("[ERROR] Enhancement produced empty/missing output — returning original")
            metadata["error"] = "empty_output"
            return input_path, metadata

        out_size_mb = os.path.getsize(out_path) / (1024 * 1024)
        metadata["output_size_mb"] = round(out_size_mb, 2)
        print(f"[OK] Audio enhanced: {filename} → {out_size_mb:.1f} MB | methods={metadata['applied_methods']}")
        return out_path, metadata

    except Exception as e:
        print(f"[ERROR] Audio enhancement failed for {filename}: {e}")
        metadata["error"] = str(e)
        return input_path, metadata


# =========================================================================
# Single-file FFmpeg enhancement (≤200 MB)
# =========================================================================

def _enhance_single(input_path: str, level: str, metadata: Dict) -> str:
    """Run FFmpeg enhancement on a single file.  Returns path to output WAV."""
    out_fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out_path = out_fd.name
    out_fd.close()

    af_chain = _build_ffmpeg_filter_chain(level)
    metadata["applied_methods"].append("ffmpeg_enhance")

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn",                           # strip video
        "-af", af_chain,
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        out_path,
    ]
    print(f"[AUDIO] FFmpeg single-pass: {os.path.basename(input_path)}")
    _run_ffmpeg(cmd)
    return out_path


# =========================================================================
# Chunked FFmpeg enhancement (>200 MB)
# =========================================================================

def _enhance_chunked(input_path: str, level: str, metadata: Dict) -> str:
    """Split → enhance each chunk → concatenate.

    Uses FFmpeg segment muxer for splitting, processes each chunk with the
    same filter chain as single-pass, then uses FFmpeg concat demuxer to join.

    All temp files are cleaned up.  Returns path to final WAV.
    """
    chunk_dir = tempfile.mkdtemp(prefix="audio_chunks_")
    enhanced_chunks: List[str] = []

    try:
        # --- 1. Get total duration ------------------------------------------------
        duration = _get_duration(input_path)
        if duration <= 0:
            print("[WARN] Could not determine duration — falling back to single-pass")
            return _enhance_single(input_path, level, metadata)

        n_chunks = max(1, int(duration // CHUNK_DURATION_SECONDS) + (1 if duration % CHUNK_DURATION_SECONDS > 0 else 0))
        print(f"[AUDIO] Chunking {duration:.0f}s audio into {n_chunks} chunks of {CHUNK_DURATION_SECONDS}s")
        metadata["applied_methods"].append(f"chunked_{n_chunks}_parts")
        metadata["original_duration_s"] = round(duration, 1)

        # --- 2. Split & 3. Enhance chunks ----------------------------------------
        chunk_files = _split_into_chunks(input_path, chunk_dir)
        if not chunk_files:
            print("[ERROR] No chunks produced — falling back to single-pass")
            return _enhance_single(input_path, level, metadata)

        enhanced_chunks = _enhance_all_chunks(chunk_files, chunk_dir, level, metadata)
        if not enhanced_chunks:
            print("[ERROR] No enhanced chunks — falling back to single-pass")
            return _enhance_single(input_path, level, metadata)

        print(f"[AUDIO] {len(enhanced_chunks)} enhanced chunks ready for concatenation")

        # --- 4. Concatenate enhanced chunks ---------------------------------------
        final_path = _concatenate_chunks(enhanced_chunks, chunk_dir, metadata)

        # Verify concatenated duration is close to original
        final_duration = metadata.get("concatenated_duration_s", 0)
        if final_duration < duration * 0.8:
            print(f"[WARN] Concatenated duration ({final_duration:.0f}s) much shorter than original ({duration:.0f}s)")

        return final_path

    finally:
        # Cleanup chunk directory
        try:
            import shutil
            shutil.rmtree(chunk_dir, ignore_errors=True)
        except Exception:
            pass


def _split_into_chunks(input_path: str, chunk_dir: str) -> List[str]:
    """Split audio file into WAV chunks using FFmpeg segment muxer."""
    chunk_pattern = os.path.join(chunk_dir, "chunk_%03d.wav")
    split_cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn",
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        "-f", "segment",
        "-segment_time", str(CHUNK_DURATION_SECONDS),
        "-reset_timestamps", "1",
        chunk_pattern,
    ]
    print(f"[AUDIO] Splitting into {CHUNK_DURATION_SECONDS}s WAV chunks...")
    _run_ffmpeg(split_cmd)

    chunk_files = sorted(
        [os.path.join(chunk_dir, f) for f in os.listdir(chunk_dir) if f.startswith("chunk_") and f.endswith(".wav")]
    )
    print(f"[AUDIO] Split produced {len(chunk_files)} chunks")
    return chunk_files


def _enhance_all_chunks(chunk_files: List[str], chunk_dir: str, level: str, metadata: Dict) -> List[str]:
    """Enhance each chunk with FFmpeg filter chain."""
    af_chain = _build_ffmpeg_filter_chain(level, is_chunk=True)
    metadata["applied_methods"].append("ffmpeg_enhance")
    enhanced_chunks: List[str] = []

    for i, chunk_path in enumerate(chunk_files):
        enhanced_path = os.path.join(chunk_dir, f"enhanced_{i:03d}.wav")
        cmd = [
            "ffmpeg", "-y", "-i", chunk_path,
            "-af", af_chain,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            enhanced_path,
        ]
        _run_ffmpeg(cmd)

        if os.path.exists(enhanced_path) and os.path.getsize(enhanced_path) > 100:
            enhanced_chunks.append(enhanced_path)
            try:
                os.remove(chunk_path)
            except OSError:
                pass
        else:
            print(f"[WARN] Enhancement failed for chunk {i}, using original")
            enhanced_chunks.append(chunk_path)

        if (i + 1) % 5 == 0 or i == len(chunk_files) - 1:
            print(f"[AUDIO]   Enhanced {i+1}/{len(chunk_files)} chunks")

    return enhanced_chunks


def _concatenate_chunks(enhanced_chunks: List[str], chunk_dir: str, metadata: Dict) -> str:
    """Concatenate enhanced chunks into a single WAV file."""
    concat_list_path = os.path.join(chunk_dir, "concat_list.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for ep in enhanced_chunks:
            safe_path = ep.replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    print(f"[AUDIO] Concat list ({len(enhanced_chunks)} entries):")
    for ep in enhanced_chunks:
        sz = os.path.getsize(ep) / (1024 * 1024) if os.path.exists(ep) else 0
        print(f"[AUDIO]   {os.path.basename(ep)} — {sz:.1f} MB")

    out_fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    final_path = out_fd.name
    out_fd.close()

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list_path,
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        final_path,
    ]
    print(f"[AUDIO] Concatenating {len(enhanced_chunks)} enhanced chunks...")
    _run_ffmpeg(concat_cmd, critical=True)

    # Verify output
    out_duration = _get_duration(final_path)
    out_size_mb = os.path.getsize(final_path) / (1024 * 1024) if os.path.exists(final_path) else 0
    print(f"[AUDIO] Concatenated result: {out_size_mb:.1f} MB, {out_duration:.0f}s duration")

    if out_duration < 10:
        raise RuntimeError(f"Concatenated output too short ({out_duration:.0f}s) — expected full-length audio")

    metadata["applied_methods"].append("concat_chunks")
    metadata["concatenated_duration_s"] = round(out_duration, 1)
    return final_path


# =========================================================================
# Python-level refinement (small files only, ≤50 MB)
# =========================================================================

def _python_refine(wav_path: str, level: str, metadata: Dict) -> str:
    """Apply Python-based noisereduce and scipy speech enhancement on WAV."""
    try:
        import soundfile as sf
    except ImportError:
        print("[WARN] soundfile not installed — skipping Python refinement")
        return wav_path

    try:
        file_size = os.path.getsize(wav_path)
        if file_size > PYTHON_REFINE_THRESHOLD_MB * 1024 * 1024:
            return wav_path

        audio, sr = sf.read(wav_path, dtype="float32")
        if len(audio) == 0:
            return wav_path

        original_rms = float(np.sqrt(np.mean(audio ** 2)))
        metadata["original_rms"] = round(original_rms, 6)

        # Noise reduction via noisereduce
        if level in ("standard", "advanced"):
            audio = _nr_reduce_noise(audio, sr)
            metadata["applied_methods"].append("python_noisereduce")

        # Scipy speech band boost (advanced only)
        if level == "advanced":
            audio = _scipy_speech_enhance(audio, sr)
            metadata["applied_methods"].append("python_speech_enhance")

        # Normalize
        audio = _normalize_amplitude(audio)
        metadata["applied_methods"].append("python_normalize")

        processed_rms = float(np.sqrt(np.mean(audio ** 2)))
        metadata["processed_rms"] = round(processed_rms, 6)

        out_fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_path = out_fd.name
        out_fd.close()
        sf.write(out_path, audio, sr, format="WAV", subtype="PCM_16")
        del audio

        # Remove the FFmpeg output we no longer need
        try:
            if out_path != wav_path:
                os.remove(wav_path)
        except OSError:
            pass

        print(f"[OK] Python refinement complete | RMS {original_rms:.4f} → {processed_rms:.4f}")
        return out_path

    except Exception as e:
        print(f"[WARN] Python refinement failed: {e} — using FFmpeg output")
        return wav_path


# =========================================================================
# FFmpeg filter chain builder
# =========================================================================

def _build_ffmpeg_filter_chain(level: str, is_chunk: bool = False) -> str:
    """Build FFmpeg -af filter chain based on processing level.

    Processing tiers:
      minimal  — loudnorm only (fastest)
      standard — light cleanup: highpass + lowpass + loudnorm (fast)
      advanced — full treatment: noise gate, EQ, compressor, limiter, loudnorm

    When ``is_chunk=True`` we use ``linear=true`` on loudnorm (chunks are
    short enough).  For single-pass we use real-time one-pass loudnorm.
    """
    linear = "linear=true" if is_chunk else "linear=false"

    if level == "minimal":
        return f"loudnorm=I=-16:TP=-1.5:LRA=11:{linear}"

    if level == "standard":
        # Light improvement: just clean up rumble/hiss + normalize loudness
        filters = [
            "highpass=f=80",
            "lowpass=f=8000",
            f"loudnorm=I=-16:TP=-1.5:LRA=11:{linear}",
        ]
        return ",".join(filters)

    # Advanced — full heavy treatment
    filters = [
        # 1. Remove low-frequency rumble (HVAC, traffic, room resonance)
        "highpass=f=80",
        # 2. Remove ultrasonic / high-freq noise
        "lowpass=f=8000",
        # 3. Noise gate: reduces background hiss via spectral gating
        "afftdn=nf=-25:nt=w:om=o",
        # 4. De-esser: tame sibilance that makes speech harsh
        "equalizer=f=6000:t=q:w=2:g=-4",
        # 5. Speech presence boost (1kHz–4kHz) — makes voices clearer
        "equalizer=f=2500:t=q:w=1.5:g=3",
        # 6. Extra mid-range speech warmth boost
        "equalizer=f=800:t=q:w=1:g=2",
        # 7. Dynamic range compression: boost quiet speech, control peaks
        "acompressor=threshold=0.025:ratio=4:attack=5:release=80:makeup=3:knee=2.5",
        # 8. Limiter to prevent clipping from makeup gain
        "alimiter=limit=0.95:attack=0.5:release=10",
        # 9. EBU R128 broadcast loudness normalization
        f"loudnorm=I=-16:TP=-1.5:LRA=11:{linear}",
    ]

    return ",".join(filters)


# =========================================================================
# Python audio processing helpers
# =========================================================================

def _nr_reduce_noise(audio: np.ndarray, sr: int) -> np.ndarray:
    """Apply noisereduce spectral gating."""
    try:
        import noisereduce as nr
        strength = float(os.getenv("NOISE_REDUCTION_STRENGTH", "0.8"))
        return nr.reduce_noise(
            y=audio, sr=sr,
            prop_decrease=strength,
            stationary=True,
            n_fft=2048,
            hop_length=512,
        )
    except ImportError:
        print("[WARN] noisereduce not installed")
        return audio
    except Exception as e:
        print(f"[WARN] noisereduce failed: {e}")
        return audio


def _scipy_speech_enhance(audio: np.ndarray, sr: int) -> np.ndarray:
    """Boost speech frequencies with scipy bandpass filter."""
    try:
        from scipy.signal import butter, sosfilt
    except ImportError:
        print("[WARN] scipy not installed")
        return audio

    try:
        # Highpass 80Hz
        sos_hp = butter(4, 80, btype="high", fs=sr, output="sos")
        result = np.asarray(sosfilt(sos_hp, audio))

        # Speech band boost (300–3400 Hz)
        sos_bp = butter(2, [300, 3400], btype="band", fs=sr, output="sos")
        speech = np.asarray(sosfilt(sos_bp, result))
        result = result + 0.35 * speech

        # Lowpass 7500Hz
        sos_lp = butter(4, 7500, btype="low", fs=sr, output="sos")
        result = np.asarray(sosfilt(sos_lp, result))
        return result
    except Exception as e:
        print(f"[WARN] scipy speech enhance failed: {e}")
        return audio


def _normalize_amplitude(audio: np.ndarray, target_dbfs: int = -20) -> np.ndarray:
    """Peak-normalize then scale to target loudness."""
    if len(audio) == 0:
        return audio
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak
    target_linear = 10 ** (target_dbfs / 20)
    rms = np.sqrt(np.mean(audio ** 2))
    if rms > 0:
        audio = audio * (target_linear / rms)
    return np.clip(audio, -1.0, 1.0)


# =========================================================================
# FFmpeg utilities
# =========================================================================

def _get_duration(file_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception as e:
        print(f"[WARN] ffprobe duration check failed: {e}")
    return 0.0


def _run_ffmpeg(cmd: list, timeout: int = 3600, critical: bool = False) -> subprocess.CompletedProcess:
    """Run an FFmpeg command with error handling.
    
    If ``critical=True``, raises RuntimeError on non-zero exit code.
    """
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr_short = (result.stderr or "")[:300]
        print(f"[WARN] FFmpeg error: {stderr_short}")
        if critical:
            raise RuntimeError(f"FFmpeg command failed (exit {result.returncode}): {stderr_short}")
    return result


# =========================================================================
# Legacy compatibility — keep old class and function signatures working
# =========================================================================

class AudioPreprocessor:
    """Legacy wrapper — delegates to enhance_audio_file."""

    def __init__(self):
        self.noise_reduction_strength = float(os.getenv("NOISE_REDUCTION_STRENGTH", "0.8"))
        self.target_loudness_dbfs = int(os.getenv("TARGET_LOUDNESS_DBFS", "-20"))

    def preprocess_file(self, wav_path: str, filename: str, settings: Optional[Dict] = None) -> Tuple[str, Dict]:
        return enhance_audio_file(wav_path, filename, settings)

    def preprocess(self, wav_bytes: bytes, filename: str, settings: Optional[Dict] = None) -> Tuple[bytes, Dict]:
        """Bytes-based entry point — writes to temp, enhances, reads back."""
        tmp_in = None
        try:
            ext = os.path.splitext(filename)[1] or ".wav"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(wav_bytes)
                tmp_in = f.name

            out_path, metadata = enhance_audio_file(tmp_in, filename, settings)

            with open(out_path, "rb") as f:
                out_bytes = f.read()

            # Cleanup
            if out_path != tmp_in:
                try:
                    os.remove(out_path)
                except OSError:
                    pass

            return out_bytes, metadata
        except Exception as e:
            print(f"[ERROR] preprocess bytes failed: {e}")
            return wav_bytes, {"error": str(e)}
        finally:
            if tmp_in and os.path.exists(tmp_in):
                try:
                    os.remove(tmp_in)
                except OSError:
                    pass


def ffmpeg_enhance_audio(input_path: str, output_path: str, output_format: str = "wav") -> bool:
    """Legacy function — kept for backward compatibility."""
    try:
        af_chain = _build_ffmpeg_filter_chain("standard")
        if output_format == "mp3":
            codec_args = ["-acodec", "libmp3lame", "-b:a", "64k", "-ar", "16000", "-ac", "1"]
        else:
            codec_args = ["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"]

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vn", "-af", af_chain,
            *codec_args,
            output_path,
        ]
        result = _run_ffmpeg(cmd)
        return result.returncode == 0
    except Exception as e:
        print(f"[WARN] ffmpeg_enhance_audio error: {e}")
        return False
