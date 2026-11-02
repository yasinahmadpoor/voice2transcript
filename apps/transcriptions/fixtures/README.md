# Model connection test audio

`connection-test.wav` is a bundled synthetic voice saying:

> Hello, this is a transcription test.

It was generated locally with eSpeak NG's `en-us` voice, then normalized with
ffmpeg to mono 16 kHz, 16-bit PCM WAV (about 2.2 seconds, 70 kB). It contains no
user recordings, personal information, or external media. Admin connection
tests send this sample only; the sample transcript is discarded.

To regenerate it on a machine with the eSpeak NG shared library and ffmpeg:

```sh
python3 scripts/generate_connection_test_audio.py
```

Speech generation is not required at runtime and adds no production dependency.
