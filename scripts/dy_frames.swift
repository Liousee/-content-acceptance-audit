// 从视频均匀抽帧存 PNG —— 用法: swift frames.swift <video> <outdir> <n> [maxw]
import AVFoundation
import Foundation
import CoreImage
import AppKit

let a = CommandLine.arguments
guard a.count >= 4 else { print("usage: frames <video> <outdir> <n> [maxw]"); exit(1) }
let src = URL(fileURLWithPath: a[1])
let outDir = a[2]
let n = Int(a[3]) ?? 8
let maxw = a.count > 4 ? (Int(a[4]) ?? 1280) : 1280
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

let asset = AVURLAsset(url: src)
let sem = DispatchSemaphore(value: 0)
var dur: Double = 0
Task {
    if let d = try? await asset.load(.duration) { dur = CMTimeGetSeconds(d) }
    sem.signal()
}
sem.wait()
guard dur > 0 else { print("ERR: no duration"); exit(2) }
print("duration=\(String(format: "%.2f", dur))s")

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
gen.maximumSize = CGSize(width: maxw, height: maxw * 2)

for i in 0..<n {
    let t = dur * (Double(i) + 0.5) / Double(n)
    let time = CMTime(seconds: t, preferredTimescale: 600)
    do {
        let cg = try gen.copyCGImage(at: time, actualTime: nil)
        let rep = NSBitmapImageRep(cgImage: cg)
        if let data = rep.representation(using: .png, properties: [:]) {
            let p = "\(outDir)/f\(String(format: "%03d", i))_\(String(format: "%.1f", t)).png"
            try data.write(to: URL(fileURLWithPath: p))
            print("saved \(p)")
        }
    } catch {
        print("frame \(i) @\(String(format: "%.1f", t))s FAILED: \(error.localizedDescription)")
    }
}
