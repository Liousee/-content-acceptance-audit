// bbox.swift — 带归一化坐标的 OCR token 转储,用于裁决争议数值(标签-值像素邻接验证)
// 用法: swift bbox.swift <图片>   (输出: "x y w h\t文本", 坐标Vision归一化原点在左下)
// 先 swiftc -O bbox.swift -o /tmp/bboxbin 编译一次,再对多图 /tmp/bboxbin <图> 秒级复用
// 裁决法: 找标签(如"评论量")token,取同x列(±0.06)且 标签y-值y∈(0.006,0.05) 的数值token即真值;
//         布局为值在标签上方时反向窗口。全页OCR两次结果一致(确定性),无新增信息,需裁剪放大(2~5x LANCZOS)或bbox邻接裁决
import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path) else { exit(1) }
var rect = NSRect(origin: .zero, size: img.size)
guard let cg = img.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { exit(1) }
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en"]
let h = VNImageRequestHandler(cgImage: cg)
try? h.perform([req])
for obs in req.results ?? [] {
    guard let c = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox
    print(String(format: "%.3f %.3f %.3f %.3f\t%@", b.origin.x, b.origin.y, b.size.width, b.size.height, c.string))
}
