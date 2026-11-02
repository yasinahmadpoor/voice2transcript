"""Regenerate the public test recording with the local eSpeak NG library.

Only regeneration requires eSpeak NG and ffmpeg; production uses the bundled WAV.
"""

import ctypes
import ctypes.util
import subprocess
import tempfile
import wave
from pathlib import Path

TEXT = "Hello, this is a transcription test."
DESTINATION = (
    Path(__file__).resolve().parents[1]
    / "apps/transcriptions/fixtures/connection-test.wav"
)


def main():
    library = ctypes.util.find_library("espeak-ng")
    if not library:
        raise SystemExit("Install eSpeak NG to regenerate the test recording.")
    engine = ctypes.CDLL(library)
    engine.espeak_Initialize.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    engine.espeak_Initialize.restype = ctypes.c_int
    rate = engine.espeak_Initialize(2, 0, None, 0)
    if rate <= 0:
        raise SystemExit("Could not initialize eSpeak NG.")
    samples = []
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
    )

    @callback_type
    def collect(audio, count, events):
        if audio and count:
            samples.append(ctypes.string_at(audio, count * 2))
        return 0

    engine.espeak_SetSynthCallback.argtypes = [callback_type]
    engine.espeak_SetSynthCallback(collect)
    engine.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    if engine.espeak_SetVoiceByName(b"en-us"):
        raise SystemExit("Could not load the English voice.")
    engine.espeak_Synth.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    text = TEXT.encode()
    if engine.espeak_Synth(text, len(text) + 1, 0, 1, 0, 1, None, None):
        raise SystemExit("Could not synthesize the test recording.")
    engine.espeak_Synchronize()
    engine.espeak_Terminate()
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "speech.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(samples))
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-map_metadata",
                "-1",
                str(DESTINATION),
            ],
            check=True,
        )
    print(DESTINATION)


if __name__ == "__main__":
    main()
