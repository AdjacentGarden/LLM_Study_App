#!/usr/bin/env python3
"""Static integrity checks only: this never claims an Xcode or device build."""
from pathlib import Path
import plistlib
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
project = root / "CloudPath.xcodeproj/project.pbxproj"
info_path = root / "Config/Info.plist"
info = plistlib.loads(info_path.read_bytes())
assert info["NSMicrophoneUsageDescription"].strip()
assert info["NSLocalNetworkUsageDescription"].strip()
assert "UIBackgroundModes" not in info, "Recording must finalize when this app leaves the foreground"
assert info["NSAppTransportSecurity"]["NSAllowsArbitraryLoads"] is True
assert info["UISupportedInterfaceOrientations"] == ["UIInterfaceOrientationPortrait"]
base = (root / "Config/Base.xcconfig").read_text()
assert "TARGETED_DEVICE_FAMILY = 1" in base
assert "SWIFT_VERSION = 6.0" in base
text = project.read_text()
assert "com.apple.product-type.application" in text
assert "PBXFileSystemSynchronizedRootGroup" in text
assert "path = App;" in text
assert "path = Sources/LearningCore;" in text
assert (root / "App/AudioCapture.swift").is_file()
assert (root / "Sources/LearningCore").is_dir(), "LearningCore sources have not been created yet"
scheme = ET.parse(root / "CloudPath.xcodeproj/xcshareddata/xcschemes/CloudPath.xcscheme")
assert scheme.find(".//BuildableReference").attrib["BlueprintIdentifier"] in text
subprocess.run(["plutil", "-lint", str(project), str(info_path)], check=True)
print("Static project, shared scheme, privacy declarations, and source groups: PASS")
print("This validates source packaging only; it does not compile iOS code or exercise microphone hardware.")
