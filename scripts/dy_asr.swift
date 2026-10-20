// 本地语音转写(macOS Speech, 离线) —— 用法: swift asr.swift <audio.wav> [zh-CN]
import Foundation
import Speech

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: asr <audio>\n".data(using: .utf8)!)
    exit(1)
}
let path = args[1]
let localeId = args.count > 2 ? args[2] : "zh-CN"
let url = URL(fileURLWithPath: path)

let sem = DispatchSemaphore(value: 0)
var done = false
var best = ""

func finish(_ reason: String) {
    if done { return }
    done = true
    FileHandle.standardError.write("\n[asr done: \(reason)]\n".data(using: .utf8)!)
    sem.signal()
}

SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized else {
        print("NOT_AUTHORIZED status=\(status.rawValue)")
        finish("not-authorized")
        return
    }
    guard let rec = SFSpeechRecognizer(locale: Locale(identifier: localeId)) else {
        print("NO_RECOGNIZER")
        finish("no-recognizer")
        return
    }
    rec.supportsOnDeviceRecognition = true
    let req = SFSpeechURLRecognitionRequest(url: url)
    req.requiresOnDeviceRecognition = true
    req.shouldReportPartialResults = true
    req.taskHint = .dictation
    if #available(macOS 13.0, *) { req.addsPunctuation = true }

    rec.recognitionTask(with: req) { result, error in
        if let r = result {
            best = r.bestTranscription.formattedString
            if r.isFinal { print(best); finish("final") }
        }
        if let e = error {
            print(best.isEmpty ? "" : best)
            FileHandle.standardError.write("[asr error] \(e.localizedDescription)\n".data(using: .utf8)!)
            finish("error")
        }
    }
}

if sem.wait(timeout: .now() + 900) == .timedOut {
    print(best)
    FileHandle.standardError.write("[asr timeout]\n".data(using: .utf8)!)
}
