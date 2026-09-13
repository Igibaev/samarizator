// System audio capture for Samarizator, through Apple's ScreenCaptureKit.
//
// Protocol with the Python side:
//   stdout — raw PCM: 32-bit float, little endian, mono, 48000 Hz. Nothing else.
//   stderr — one JSON object per line: {"event": "...", ...}. Diagnostics only.
//   exit 0  — stopped on request (SIGINT/SIGTERM) after the stream was running.
//   exit 2  — macOS refused the permission; exit 3 — capture failed for another reason.
//
// The helper captures audio only: no screen image is written anywhere. The video
// side of the stream is configured to the smallest allowed frame and left unread,
// because ScreenCaptureKit has no audio-only stream.

import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

let sampleRate = 48000
let permissionDenied: Int32 = 2
let captureFailed: Int32 = 3

func emit(_ event: String, _ extra: [String: Any] = [:]) {
    var payload: [String: Any] = ["event": event]
    payload.merge(extra) { _, new in new }
    guard let data = try? JSONSerialization.data(withJSONObject: payload),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardError.write(Data((line + "\n").utf8))
}

func osVersion() -> String {
    let v = ProcessInfo.processInfo.operatingSystemVersion
    return "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
}

/// Blocking write to stdout. Backpressure from FFmpeg is deliberate: a stalled
/// reader must slow capture down, never silently drop captured audio.
func writeSamples(_ data: Data) -> Bool {
    var written = 0
    return data.withUnsafeBytes { raw -> Bool in
        guard let base = raw.baseAddress else { return true }
        while written < raw.count {
            let n = write(1, base.advanced(by: written), raw.count - written)
            if n > 0 {
                written += n
                continue
            }
            if n < 0 && errno == EINTR { continue }
            return false  // EPIPE: FFmpeg is gone, stop the session.
        }
        return true
    }
}

final class SystemAudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private let onFailure: (String, String) -> Void

    init(onFailure: @escaping (String, String) -> Void) {
        self.onFailure = onFailure
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, buffer.isValid, buffer.numSamples > 0 else { return }
        do {
            try buffer.withAudioBufferList { list, _ in
                guard let first = list.first else { return }
                let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size
                guard frames > 0 else { return }
                // ScreenCaptureKit delivers deinterleaved float channels; average them
                // into one track, the same shape the microphone path already feeds FFmpeg.
                var mono = [Float](repeating: 0, count: frames)
                var used = 0
                for channel in list {
                    guard let raw = channel.mData, Int(channel.mDataByteSize) >= frames * MemoryLayout<Float>.size
                    else { continue }
                    let samples = raw.assumingMemoryBound(to: Float.self)
                    for index in 0..<frames { mono[index] += samples[index] }
                    used += 1
                }
                guard used > 0 else { return }
                if used > 1 {
                    let scale = 1 / Float(used)
                    for index in 0..<frames { mono[index] *= scale }
                }
                let ok = mono.withUnsafeBufferPointer { samples -> Bool in
                    writeSamples(Data(buffer: samples))
                }
                if !ok { onFailure("pipe-closed", "Приёмник аудио закрылся.") }
            }
        } catch {
            onFailure("buffer", error.localizedDescription)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onFailure("stream-stopped", error.localizedDescription)
    }
}

func audioConfiguration() -> SCStreamConfiguration {
    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.sampleRate = sampleRate
    config.channelCount = 2
    config.excludesCurrentProcessAudio = true
    // No screen frames are consumed; keep the video side as small and as slow as allowed.
    config.width = 2
    config.height = 2
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
    config.queueDepth = 6
    return config
}

func probe() async -> Never {
    let granted = CGPreflightScreenCaptureAccess()
    var payload: [String: Any] = [
        "macos": osVersion(),
        "permission": granted ? "granted" : "denied",
        "sample_rate": sampleRate,
    ]
    if granted {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            payload["displays"] = content.displays.count
            payload["supported"] = !content.displays.isEmpty
        } catch {
            payload["supported"] = false
            payload["reason"] = error.localizedDescription
        }
    } else {
        payload["supported"] = false
    }
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
    exit(0)
}

func capture() async -> Never {
    if !CGPreflightScreenCaptureAccess() {
        // Shows the system prompt once; the grant applies to the next launch, so this
        // run still fails, with an explanation instead of silence.
        _ = CGRequestScreenCaptureAccess()
        emit("error", [
            "code": "permission",
            "message": "Нет разрешения на запись экрана и системного звука.",
        ])
        exit(permissionDenied)
    }
    let content: SCShareableContent
    do {
        content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    } catch {
        emit("error", ["code": "permission", "message": error.localizedDescription])
        exit(permissionDenied)
    }
    guard let display = content.displays.first else {
        emit("error", ["code": "no-display", "message": "macOS не вернула ни одного дисплея."])
        exit(captureFailed)
    }
    let failure = Failure()
    let output = SystemAudioOutput { code, message in failure.record(code, message) }
    let stream = SCStream(filter: SCContentFilter(display: display, excludingWindows: []), configuration: audioConfiguration(), delegate: output)
    do {
        try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "samarizator.system-audio"))
        try await stream.startCapture()
    } catch {
        emit("error", ["code": "start", "message": error.localizedDescription])
        exit(captureFailed)
    }
    emit("started", ["macos": osVersion(), "sample_rate": sampleRate, "channels": 1, "format": "f32le"])
    installStopHandlers()
    while !stopped.load() {
        if let (code, message) = failure.take() {
            try? await stream.stopCapture()
            emit("error", ["code": code, "message": message])
            exit(code == "pipe-closed" ? 0 : captureFailed)
        }
        try? await Task.sleep(nanoseconds: 100_000_000)
    }
    try? await stream.stopCapture()
    emit("stopped")
    exit(0)
}

final class Flag {
    private let lock = NSLock()
    private var value = false
    func set() { lock.lock(); value = true; lock.unlock() }
    func load() -> Bool { lock.lock(); defer { lock.unlock() }; return value }
}

final class Failure {
    private let lock = NSLock()
    private var value: (String, String)?
    func record(_ code: String, _ message: String) {
        lock.lock()
        if value == nil { value = (code, message) }
        lock.unlock()
    }
    func take() -> (String, String)? {
        lock.lock(); defer { lock.unlock() }; return value
    }
}

let stopped = Flag()
var sources: [DispatchSourceSignal] = []

func installStopHandlers() {
    for number in [SIGINT, SIGTERM] {
        signal(number, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
        source.setEventHandler { stopped.set() }
        source.resume()
        sources.append(source)
    }
}

signal(SIGPIPE, SIG_IGN)
let mode = CommandLine.arguments.dropFirst().first ?? "capture"
Task {
    switch mode {
    case "probe":
        await probe()
    case "capture":
        await capture()
    default:
        emit("error", ["code": "usage", "message": "Использование: samarizator-system-audio [probe|capture]"])
        exit(captureFailed)
    }
}
RunLoop.main.run()
