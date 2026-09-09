"""Separate process: release ONNX sessions completely before loading Whisper."""

import json
import sys
from pathlib import Path


def run(wav, segmentation, embedding, speakers, threads, output):
    import sherpa_onnx
    import soundfile as sf

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation),
            num_threads=int(threads),
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=embedding, num_threads=int(threads), provider="cpu"
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(speakers), threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise ValueError("Некорректные модели определения собеседников.")
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    audio, rate = sf.read(wav, dtype="float32")
    if rate != sd.sample_rate or audio.ndim != 1:
        raise ValueError("Ожидается mono WAV 16 кГц.")
    turns = [
        dict(start=float(s.start), end=float(s.end), speaker=f"Собеседник {s.speaker + 1}")
        for s in sd.process(audio).sort_by_start_time()
    ]
    Path(output).write_text(json.dumps(turns, ensure_ascii=False))


if __name__ == "__main__":
    run(*sys.argv[1:])
