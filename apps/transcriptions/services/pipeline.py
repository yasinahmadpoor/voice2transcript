"""
Transcribe + diarize audio/video with Gemini.

- Model default: gemini-3.1-pro
- Input formats: anything ffmpeg can decode
- Outputs:
  - <prefix>.json (structured segments)
  - <prefix>.txt  (readable transcript)
  - <prefix>.srt  (subtitles)
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from google import genai
from google.genai import types


PROMPT_BASE = """You are an expert transcription and diarization engine.

Tasks:
1) Transcribe spoken content accurately.
2) Diarize speakers as SPEAKER_1, SPEAKER_2, ...
3) Keep strict chronological order.
4) Include timestamps in seconds.
5) If overlap is obvious, split into short adjacent segments and include "[overlap]" in text.

Return ONLY valid JSON in this exact shape:
{
  "language": "string",
  "segments": [
    {"start": 0.0, "end": 1.23, "speaker": "SPEAKER_1", "text": "hello"}
  ]
}

No markdown fences.
No extra keys.
"""

DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_FALLBACK_MODEL = "gemini-2.5-flash"


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run_cmd(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.output}") from e


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        fail(f"`{name}` is required but not found in PATH.")


def get_duration_seconds(path: Path) -> float:
    out = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return float(out)
    except ValueError as e:
        raise RuntimeError(f"Could not parse duration for {path}: {out}") from e


def convert_to_wav(input_path: Path, output_wav: Path) -> None:
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
    )


def split_wav(wav_path: Path, chunk_minutes: float) -> List[Tuple[Path, float]]:
    if chunk_minutes <= 0:
        return [(wav_path, 0.0)]

    duration = get_duration_seconds(wav_path)
    chunk_sec = chunk_minutes * 60.0
    if duration <= chunk_sec:
        return [(wav_path, 0.0)]

    parts: List[Tuple[Path, float]] = []
    start = 0.0
    idx = 0
    while start < duration - 0.05:
        length = min(chunk_sec, duration - start)
        out = wav_path.parent / f"chunk_{idx:04d}.wav"
        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(wav_path),
                "-t",
                f"{length:.3f}",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(out),
            ]
        )
        parts.append((out, start))
        start += length
        idx += 1

    return parts


def extract_response_text(response: Any) -> str:
    direct = getattr(response, "text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                chunks.append(part_text.strip())

    return "\n".join(chunks).strip()


def extract_response_text_exact(response: Any) -> str:
    direct = getattr(response, "text", None)
    if isinstance(direct, str) and direct:
        return direct

    chunks: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                chunks.append(part_text)

    return "\n".join(chunks)


def generate_with_optional_json_mode(client: genai.Client, model: str, contents: List[Any]) -> Any:
    try:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        if "JSON mode is not enabled for this model" not in str(e):
            raise

        return client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
            ),
        )


def get_file_state_name(file_obj: Any) -> str:
    state = getattr(file_obj, "state", None)
    if state is None:
        return ""
    name = getattr(state, "name", None)
    if isinstance(name, str):
        return name
    return str(state)


def wait_for_uploaded_file_ready(
    client: genai.Client,
    file_name: str,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 1.5,
) -> Any:
    deadline = time.time() + timeout_seconds
    last_state = "UNKNOWN"

    while time.time() < deadline:
        current = client.files.get(name=file_name)
        state = (get_file_state_name(current) or "UNKNOWN").upper()
        last_state = state

        if state in {"ACTIVE", "READY", "SUCCEEDED"}:
            return current
        if state in {"FAILED", "ERROR", "DELETED", "EXPIRED"}:
            raise RuntimeError(f"Uploaded file entered non-usable state: {state}")

        time.sleep(poll_seconds)

    raise TimeoutError(
        f"Timed out waiting for uploaded file to become ready. Last state: {last_state}"
    )


def response_diagnostics(response: Any) -> str:
    parts: List[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        reasons = []
        for c in candidates:
            reason = getattr(c, "finish_reason", None)
            if reason is not None:
                reasons.append(str(reason))
        if reasons:
            parts.append(f"finish_reason={','.join(reasons)}")

    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is not None:
        parts.append(f"prompt_feedback={prompt_feedback}")

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        parts.append(f"usage={usage}")

    return " | ".join(parts) if parts else "no diagnostics available"


def generate_transcription_response(
    client: genai.Client,
    model: str,
    prompt: str,
    uploaded: Any,
) -> Any:
    attempts: List[List[Any]] = [
        [prompt, uploaded],
        [uploaded, prompt],
    ]

    last_response = None
    for contents in attempts:
        response = generate_with_optional_json_mode(
            client=client,
            model=model,
            contents=contents,
        )
        last_response = response
        if extract_response_text(response):
            return response

    plain_prompt = (
        "Transcribe this audio with speaker labels and timestamps in seconds. "
        "Return plain text only."
    )
    response = client.models.generate_content(
        model=model,
        contents=[uploaded, plain_prompt],
        config=types.GenerateContentConfig(temperature=0.0),
    )
    if extract_response_text(response):
        return response

    return last_response if last_response is not None else response


def generate_transcription_response_raw(
    client: genai.Client,
    model: str,
    prompt: str,
    uploaded: Any,
) -> Any:
    attempts: List[List[Any]] = [
        [prompt, uploaded],
        [uploaded, prompt],
    ]

    last_response = None
    for contents in attempts:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        last_response = response
        if extract_response_text(response):
            return response

    return last_response


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    raise ValueError("Gemini response was not valid JSON object.")


def reformat_to_target_schema(
    client: genai.Client,
    model: str,
    raw_text: str,
    language_hint: str,
) -> Dict[str, Any]:
    clipped = raw_text.strip()
    if len(clipped) > 120000:
        clipped = clipped[:120000]

    prompt = """Convert the input transcription/diarization output into valid JSON.

Return ONLY JSON in this exact shape:
{
  "language": "string",
  "segments": [
    {"start": 0.0, "end": 1.23, "speaker": "SPEAKER_1", "text": "hello"}
  ]
}

Rules:
- Ensure start/end are numbers in seconds.
- Preserve chronological order.
- If timestamps are missing, infer rough timing with non-overlapping segments.
- Keep speaker labels as SPEAKER_N.
- No markdown fences.
- No extra keys.
"""
    if language_hint.strip():
        prompt += f"\nPreferred spoken language hint: {language_hint.strip()}."

    response = generate_with_optional_json_mode(
        client=client,
        model=model,
        contents=[prompt, clipped],
    )
    text = extract_response_text(response)
    parsed = extract_json_object(text)
    return normalize_result(parsed)


def normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    language = str(data.get("language", "unknown")).strip() or "unknown"
    raw_segments = data.get("segments", [])
    if not isinstance(raw_segments, list):
        raw_segments = []

    segments: List[Dict[str, Any]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            continue

        if end < start:
            end = start

        speaker = str(item.get("speaker", "SPEAKER_1")).strip() or "SPEAKER_1"
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        segments.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "speaker": speaker,
                "text": text,
            }
        )

    segments.sort(key=lambda s: (s["start"], s["end"]))
    return {"language": language, "segments": segments}


def merge_adjacent(segments: List[Dict[str, Any]], max_gap: float = 0.35) -> List[Dict[str, Any]]:
    if not segments:
        return segments

    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        gap = seg["start"] - prev["end"]

        can_merge = (
            seg["speaker"] == prev["speaker"]
            and 0.0 <= gap <= max_gap
            and "[overlap]" not in seg["text"]
            and "[overlap]" not in prev["text"]
        )

        if can_merge:
            prev["end"] = max(prev["end"], seg["end"])
            prev["text"] = f"{prev['text']} {seg['text']}".strip()
        else:
            merged.append(seg.copy())

    return merged


def transcribe_chunk(
    client: genai.Client,
    model: str,
    chunk_path: Path,
    language_hint: str = "",
    fallback_model: str = "",
    prompt: str = PROMPT_BASE,
) -> Dict[str, Any]:
    effective_prompt = prompt.strip() or PROMPT_BASE
    if language_hint.strip():
        effective_prompt += f"\nPreferred spoken language hint: {language_hint.strip()}."

    uploaded = client.files.upload(file=str(chunk_path))
    try:
        wait_for_uploaded_file_ready(client=client, file_name=uploaded.name)

        response = generate_transcription_response(
            client=client,
            model=model,
            prompt=effective_prompt,
            uploaded=uploaded,
        )

        text = extract_response_text(response)
        if not text and fallback_model and fallback_model != model:
            print(
                f"Primary model '{model}' returned empty output. "
                f"Retrying with fallback '{fallback_model}'..."
            )
            response = generate_transcription_response(
                client=client,
                model=fallback_model,
                prompt=effective_prompt,
                uploaded=uploaded,
            )
            text = extract_response_text(response)

        if not text:
            diag = response_diagnostics(response)
            raise ValueError(f"Model returned an empty response. Diagnostics: {diag}")

        try:
            parsed = extract_json_object(text)
            return normalize_result(parsed)
        except ValueError:
            try:
                return reformat_to_target_schema(
                    client=client,
                    model=model,
                    raw_text=text,
                    language_hint=language_hint,
                )
            except Exception as e:
                sample = text[:400].replace("\n", " ")
                raise ValueError(
                    "Could not coerce model output to JSON. "
                    f"Sample response: {sample}"
                ) from e
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


def transcribe_chunk_raw(
    client: genai.Client,
    model: str,
    chunk_path: Path,
    language_hint: str = "",
    fallback_model: str = "",
    prompt: str = PROMPT_BASE,
) -> str:
    effective_prompt = prompt.strip() or PROMPT_BASE
    if language_hint.strip():
        effective_prompt += f"\nPreferred spoken language hint: {language_hint.strip()}."

    uploaded = client.files.upload(file=str(chunk_path))
    try:
        wait_for_uploaded_file_ready(client=client, file_name=uploaded.name)

        response = generate_transcription_response_raw(
            client=client,
            model=model,
            prompt=effective_prompt,
            uploaded=uploaded,
        )

        text = extract_response_text_exact(response)
        if not text.strip() and fallback_model and fallback_model != model:
            print(
                f"Primary model '{model}' returned empty output. "
                f"Retrying with fallback '{fallback_model}'..."
            )
            response = generate_transcription_response_raw(
                client=client,
                model=fallback_model,
                prompt=effective_prompt,
                uploaded=uploaded,
            )
            text = extract_response_text_exact(response)

        if not text.strip():
            diag = response_diagnostics(response)
            raise ValueError(f"Model returned an empty response. Diagnostics: {diag}")

        return text
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


def sec_to_hms_ms(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms_total = int(round(sec * 1000))
    h = ms_total // 3_600_000
    ms_total %= 3_600_000
    m = ms_total // 60_000
    ms_total %= 60_000
    s = ms_total // 1000
    ms = ms_total % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def sec_to_srt_time(sec: float) -> str:
    return sec_to_hms_ms(sec).replace(".", ",")


def segments_to_text(segments: List[Dict[str, Any]]) -> str:
    lines = []
    for seg in segments:
        lines.append(
            f"[{sec_to_hms_ms(seg['start'])} - {sec_to_hms_ms(seg['end'])}] "
            f"{seg['speaker']}: {seg['text']}"
        )
    return "\n".join(lines)


def segments_to_srt(segments: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, seg in enumerate(segments, start=1):
        blocks.append(
            f"{i}\n"
            f"{sec_to_srt_time(seg['start'])} --> {sec_to_srt_time(seg['end'])}\n"
            f"{seg['speaker']}: {seg['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def run_transcription_pipeline(
    client: genai.Client,
    input_path: Path,
    model: str = DEFAULT_MODEL,
    chunk_minutes: float = 20.0,
    language_hint: str = "",
    fallback_model: str = DEFAULT_FALLBACK_MODEL,
    prompt: str = PROMPT_BASE,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="gemini_diarize_") as tmpdir:
        tmp = Path(tmpdir)
        normalized_wav = tmp / "normalized.wav"
        print("Converting input to normalized WAV...")
        convert_to_wav(input_path, normalized_wav)

        print("Splitting audio (if needed)...")
        chunks = split_wav(normalized_wav, chunk_minutes)
        print(f"Chunks: {len(chunks)}")

        all_segments: List[Dict[str, Any]] = []
        detected_language = "unknown"

        for i, (chunk_path, offset) in enumerate(chunks, start=1):
            print(f"[{i}/{len(chunks)}] Transcribing: {chunk_path.name}")
            result = transcribe_chunk(
                client=client,
                model=model,
                chunk_path=chunk_path,
                language_hint=language_hint,
                fallback_model=fallback_model,
                prompt=prompt,
            )

            if detected_language == "unknown" and result.get("language"):
                detected_language = result["language"]

            for seg in result["segments"]:
                seg2 = dict(seg)
                seg2["start"] = round(seg2["start"] + offset, 3)
                seg2["end"] = round(seg2["end"] + offset, 3)
                all_segments.append(seg2)

        all_segments.sort(key=lambda s: (s["start"], s["end"]))
        all_segments = merge_adjacent(all_segments)

        return {"language": detected_language, "segments": all_segments}


def run_transcription_pipeline_raw(
    client: genai.Client,
    input_path: Path,
    model: str = DEFAULT_MODEL,
    chunk_minutes: float = 20.0,
    language_hint: str = "",
    fallback_model: str = DEFAULT_FALLBACK_MODEL,
    prompt: str = PROMPT_BASE,
    checkpoint=None,
) -> str:
    from .checkpoints import transcribe_chunks

    with tempfile.TemporaryDirectory(prefix="gemini_diarize_") as tmpdir:
        tmp = Path(tmpdir)
        normalized_wav = tmp / "normalized.wav"
        print("Converting input to normalized WAV...")
        convert_to_wav(input_path, normalized_wav)

        print("Splitting audio (if needed)...")
        chunks = split_wav(normalized_wav, chunk_minutes)
        print(f"Chunks: {len(chunks)}")

        def transcribe(chunk_path):
            print(f"Transcribing: {chunk_path.name}")
            return transcribe_chunk_raw(
                client=client,
                model=model,
                chunk_path=chunk_path,
                language_hint=language_hint,
                fallback_model=fallback_model,
                prompt=prompt,
            )
        return transcribe_chunks(chunks, transcribe, checkpoint)


def write_outputs(prefix: Path, language: str, segments: List[Dict[str, Any]]) -> None:
    json_path = prefix.with_suffix(".json")
    txt_path = prefix.with_suffix(".txt")
    srt_path = prefix.with_suffix(".srt")

    payload = {"language": language, "segments": segments}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(segments_to_text(segments) + ("\n" if segments else ""), encoding="utf-8")
    srt_path.write_text(segments_to_srt(segments), encoding="utf-8")

    print(f"Saved: {json_path}")
    print(f"Saved: {txt_path}")
    print(f"Saved: {srt_path}")
