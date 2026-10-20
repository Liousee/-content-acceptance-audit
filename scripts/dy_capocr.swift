// 视频内嵌字幕通读: 抽帧 -> 裁字幕带(底部 15%~32%) -> 3x 放大 -> Vision OCR
// 用法: swift capocr.swift <video> [n] [lo] [hi]   (lo/hi 为归一化底部起算比例)
import AVFoundation
import AppKit
import Foundation
import Vision

let a = CommandLine.arguments
guard a.count >= 2 else { print("usage: capocr <video> [n] [lo] [hi]"); exit(1) }
let url = URL(fileURLWithPath: a[1])
let n = a.count > 2 ? (Int(a[2]) ?? 80) : 80
let lo = a.count > 3 ? (Double(a[3]) ?? 0.15) : 0.15
let hi = a.count > 4 ? (Double(a[4]) ?? 0.32) : 0.32

let asset = AVURLAsset(url: url)
let sem = DispatchSemaphore(value: 0)
var dur: Double = 0
Task {
    if let d = try? await asset.load(.duration) { dur = CMTimeGetSeconds(d) }
    sem.signal()
}
sem.wait()
guard dur > 0 else { print("ERR duration"); exit(2) }
FileHandle.standardError.write("duration=\(String(format: "%.1f", dur))s frames=\(n)\n".data(using: .utf8)!)

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero

func upscale(_ cg: CGImage, _ f: Int) -> CGImage {
    let w = cg.width * f, h = cg.height * f
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return cg }
    ctx.interpolationQuality = .high
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    return ctx.makeImage() ?? cg
}

for i in 0..<n {
    let t = dur * (Double(i) + 0.5) / Double(n)
    let time = CMTime(seconds: t, preferredTimescale: 600)
    guard let full = try? gen.copyCGImage(at: time, actualTime: nil) else { continue }
    let H = Double(full.height), W = Double(full.width)
    // Vision 归一化 y 自下而上; 裁底部 lo~hi 带
    // CGImage 原点在左上: 底部带 lo~hi 对应顶部偏移 (1-hi)*H
    let rect = CGRect(x: 0, y: Int((1 - hi) * H), width: Int(W), height: Int((hi - lo) * H))
    guard let band = full.cropping(to: rect) else { continue }
    let big = upscale(band, 3)
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = true
    let h = VNImageRequestHandler(cgImage: big)
    try? h.perform([req])
    var lines: [String] = []
    for o in (req.results ?? []).sorted(by: { $0.boundingBox.origin.y > $1.boundingBox.origin.y }) {
        if let c = o.topCandidates(1).first { lines.append(c.string) }
    }
    print(String(format: "%.1f\t%@", t, lines.joined(separator: " / ")))
}
