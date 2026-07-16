import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

if CommandLine.arguments.count != 7 {
    fputs("Usage: crop-image.swift <input> <output> <x> <y> <width> <height>\n", stderr)
    exit(2)
}

let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])

guard
    let x = Int(CommandLine.arguments[3]),
    let y = Int(CommandLine.arguments[4]),
    let width = Int(CommandLine.arguments[5]),
    let height = Int(CommandLine.arguments[6])
else {
    fputs("Crop coordinates must be integers.\n", stderr)
    exit(2)
}

guard
    let source = CGImageSourceCreateWithURL(inputURL as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    fputs("Could not read input image.\n", stderr)
    exit(1)
}

guard let cropped = image.cropping(to: CGRect(x: x, y: y, width: width, height: height)) else {
    fputs("Could not crop image.\n", stderr)
    exit(1)
}

guard let destination = CGImageDestinationCreateWithURL(outputURL as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    fputs("Could not create output image.\n", stderr)
    exit(1)
}

CGImageDestinationAddImage(destination, cropped, nil)
if !CGImageDestinationFinalize(destination) {
    fputs("Could not write output image.\n", stderr)
    exit(1)
}
