#!/usr/bin/env python3
"""
Transcript Generator for Claude Summarization
----------------------------------------------
Two modes:
  1. YouTube mode  — downloads transcripts from a video or playlist URL.
                     Falls back to Whisper if no captions exist.
  2. Local mode    — transcribes every video/audio file in a folder using Whisper.

Output: numbered .txt files per item + _COMBINED.txt + a .zip, ready to upload to Claude.

Usage:
    # YouTube video or playlist:
    python yt_transcripts.py https://youtu.be/BDs57fHpfuA
    python yt_transcripts.py https://www.youtube.com/playlist?list=PLxxxxxx

    # Local folder of video/audio files:
    python yt_transcripts.py /path/to/my/videos

    # Options:
    python yt_transcripts.py <source> --out my_transcripts   # custom output folder
    python yt_transcripts.py <source> --model small          # whisper model size
    python yt_transcripts.py <source> --no-whisper           # skip whisper fallback

Background / overnight runs:
    # Run in background with low CPU priority, logging to file:
    nohup nice -n 10 python yt_transcripts.py <source> > transcript_run.log 2>&1 &

    # Watch progress:
    tail -f transcript_run.log      # Ctrl+C to stop watching without killing the job

    # Cancel the run:
    ps aux | grep yt_transcripts    # find the PID
    kill <PID>

Whisper model sizes (speed vs accuracy on CPU):
    tiny      — fastest, lower accuracy
    base      — good for clear speech
    small     — better accuracy, reasonable speed
    medium    — good for technical content or accents
    large-v3  — best accuracy, slowest (default — fine for overnight runs)

Requirements:
    pip install yt-dlp faster-whisper
    sudo apt install ffmpeg      # needed by both yt-dlp and faster-whisper
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

# Audio/video extensions recognised in local folder mode
LOCAL_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac",
}

WHISPER_DEFAULT_MODEL = "large-v3"


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def check_command(cmd: str) -> bool:
    try:
        subprocess.run([cmd, "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def ensure_ytdlp():
    if not check_command("yt-dlp"):
        print("yt-dlp not found — installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)
        print("yt-dlp installed.\n")


def ensure_ffmpeg():
    if not check_command("ffmpeg"):
        print(
            "\n⚠️  ffmpeg not found. Install it with:\n"
            "    sudo apt install ffmpeg\n"
            "Whisper and yt-dlp both require ffmpeg. Exiting.\n"
        )
        sys.exit(1)


def ensure_faster_whisper():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("faster-whisper not found — installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "faster-whisper"], check=True
        )
        print("faster-whisper installed.\n")


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:100]


# ---------------------------------------------------------------------------
# VTT → plain text
# ---------------------------------------------------------------------------

def vtt_to_text(vtt: str) -> str:
    """Strip timestamps, tags, and duplicate lines from a VTT transcript."""
    seen = []
    for line in vtt.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith("WEBVTT")
            or re.match(r"^\d{2}:\d{2}", line)
            or re.match(r"^\d+$", line)
            or line.startswith(("NOTE", "Kind:", "Language:"))
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and (not seen or seen[-1] != line):
            seen.append(line)
    text = " ".join(seen)
    return re.sub(r" {2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------

def transcribe_with_whisper(audio_path: Path, model_size: str, threads: int = 0) -> str:
    """Transcribe a local file using faster-whisper. Returns plain text."""
    ensure_faster_whisper()
    ensure_ffmpeg()

    from faster_whisper import WhisperModel

    # Use all available CPU cores if threads not specified
    cpu_threads = threads if threads > 0 else os.cpu_count() or 4

    # int8 works well for all model sizes on CPU and gives a nice speed boost
    # float16 would be faster but requires CUDA; float32 is slower with no quality gain
    compute_type = "int8"

    print(f"         ⏳ Transcribing with Whisper ({model_size}) "
          f"using {cpu_threads} threads — this may take a while...")

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type=compute_type,
        cpu_threads=cpu_threads,
    )
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,       # skip silent sections, speeds things up
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    text = " ".join(seg.text.strip() for seg in segments)
    return re.sub(r" {2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# YouTube mode
# ---------------------------------------------------------------------------

def get_playlist_info(url: str) -> list[dict]:
    print(f"Fetching video list from: {url}")
    result = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--print", "%(id)s\t%(title)s",
            "--no-warnings",
            url,
        ],
        capture_output=True,
        text=True,
    )
    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            videos.append({"id": parts[0], "title": parts[1]})
    return videos


def fetch_captions_ytdlp(video_id: str, out_dir: Path) -> str | None:
    """Try to download existing captions (manual then auto). Returns text or None."""
    tmp_base = out_dir / f"_tmp_{video_id}"
    url = f"https://www.youtube.com/watch?v={video_id}"

    for sub_flag in [["--write-subs"], ["--write-auto-subs"]]:
        subprocess.run(
            [
                "yt-dlp", *sub_flag,
                "--skip-download",
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "--output", str(tmp_base),
                "--no-warnings", "--quiet",
                url,
            ],
            capture_output=True,
            text=True,
        )
        vtt_files = list(out_dir.glob(f"_tmp_{video_id}*.vtt"))
        if vtt_files:
            break

    if not vtt_files:
        return None

    vtt_path = vtt_files[0]
    txt = vtt_to_text(vtt_path.read_text(encoding="utf-8", errors="ignore"))
    vtt_path.unlink()
    return txt or None


def download_audio_ytdlp(video_id: str, out_dir: Path) -> Path | None:
    """Download audio-only for Whisper fallback. Returns path to file or None."""
    tmp_path = out_dir / f"_audio_{video_id}"
    url = f"https://www.youtube.com/watch?v={video_id}"
    result = subprocess.run(
        [
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            "--output", str(tmp_path) + ".%(ext)s",
            "--no-warnings", "--quiet",
            url,
        ],
        capture_output=True,
        text=True,
    )
    matches = list(out_dir.glob(f"_audio_{video_id}.*"))
    return matches[0] if matches else None


def process_youtube(url: str, out_dir: Path, model_size: str, use_whisper: bool, threads: int = 0):
    ensure_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)

    videos = get_playlist_info(url)
    if not videos:
        print("No videos found. Check the URL and try again.")
        sys.exit(1)

    print(f"Found {len(videos)} video(s).\n")
    results, failed = [], []

    for i, video in enumerate(videos, 1):
        vid_id, title = video["id"], video["title"]
        safe_title = sanitize_filename(title)
        print(f"  [{i}/{len(videos)}] {title}")

        txt = fetch_captions_ytdlp(vid_id, out_dir)
        source = "captions"

        if not txt and use_whisper:
            print("         ↳ No captions found — downloading audio for Whisper...")
            audio_path = download_audio_ytdlp(vid_id, out_dir)
            if audio_path:
                txt = transcribe_with_whisper(audio_path, model_size, threads)
                audio_path.unlink()
                source = f"Whisper ({model_size})"

        if txt:
            out_path = out_dir / f"{i:03d}_{safe_title}.txt"
            out_path.write_text(
                f"VIDEO: {title}\nURL: https://youtu.be/{vid_id}\nSOURCE: {source}\n\n{txt}\n",
                encoding="utf-8",
            )
            results.append((title, out_path))
            print(f"         ✓ Saved via {source} ({len(txt):,} chars)")
        else:
            failed.append(title)
            print("         ✗ Could not get transcript")

    _finalise(out_dir, results, failed, total=len(videos))


# ---------------------------------------------------------------------------
# Local folder mode
# ---------------------------------------------------------------------------

def process_local(folder: Path, out_dir: Path, model_size: str, threads: int = 0):
    ensure_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in LOCAL_EXTENSIONS
    )

    if not files:
        print(f"No audio/video files found in {folder}")
        print(f"Supported formats: {', '.join(sorted(LOCAL_EXTENSIONS))}")
        sys.exit(1)

    print(f"Found {len(files)} file(s) to transcribe.\n")
    results, failed = [], []

    for i, file_path in enumerate(files, 1):
        title = file_path.stem
        safe_title = sanitize_filename(title)
        print(f"  [{i}/{len(files)}] {file_path.name}")

        try:
            txt = transcribe_with_whisper(file_path, model_size, threads)
        except Exception as e:
            print(f"         ✗ Error: {e}")
            failed.append(title)
            continue

        if txt:
            out_path = out_dir / f"{i:03d}_{safe_title}.txt"
            out_path.write_text(
                f"FILE: {file_path.name}\nSOURCE: Whisper ({model_size})\n\n{txt}\n",
                encoding="utf-8",
            )
            results.append((title, out_path))
            print(f"         ✓ Saved ({len(txt):,} chars)")
        else:
            failed.append(title)
            print("         ✗ Empty transcript")

    _finalise(out_dir, results, failed, total=len(files))


# ---------------------------------------------------------------------------
# Shared finalisation
# ---------------------------------------------------------------------------

def _finalise(out_dir: Path, results: list, failed: list, total: int):
    if results:
        combined_path = out_dir / "_COMBINED.txt"
        with combined_path.open("w", encoding="utf-8") as f:
            f.write(f"COMBINED TRANSCRIPTS ({len(results)} items)\n")
            f.write("=" * 60 + "\n")
            for title, path in results:
                f.write(f"\n{'=' * 60}\n")
                f.write(path.read_text(encoding="utf-8"))
        print(f"\n✓ Combined transcript → {combined_path}")

    zip_path = out_dir.parent / f"{out_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.glob("*.txt")):
            zf.write(path, path.name)
    print(f"✓ Zip archive → {zip_path}")

    print(f"\n{'=' * 60}")
    print(f"Done: {len(results)}/{total} transcripts generated.")
    if failed:
        print("\nFailed:")
        for f in failed:
            print(f"  - {f}")
    print(f"\nUpload {zip_path} to Claude and ask for a summary.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate transcripts from a YouTube URL or local folder for Claude."
    )
    parser.add_argument(
        "source",
        help="YouTube video/playlist URL, or path to a local folder of audio/video files",
    )
    parser.add_argument(
        "--out",
        default="transcripts",
        help="Output folder name (default: transcripts)",
    )
    parser.add_argument(
        "--model",
        default=WHISPER_DEFAULT_MODEL,
        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        help="Whisper model size (default: small)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="CPU threads for Whisper (default: use all available cores)",
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper fallback for YouTube videos with no captions",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    source = args.source

    # Decide mode: local folder or YouTube URL
    source_path = Path(source)
    if source_path.exists() and source_path.is_dir():
        print(f"Local folder mode: {source_path.resolve()}\n")
        process_local(source_path, out_dir, args.model, args.threads)
    elif source.startswith("http"):
        print(f"YouTube mode: {source}\n")
        process_youtube(source, out_dir, args.model, use_whisper=not args.no_whisper, threads=args.threads)
    else:
        print(f"Error: '{source}' is not a valid URL or existing folder path.")
        sys.exit(1)


if __name__ == "__main__":
    main()
