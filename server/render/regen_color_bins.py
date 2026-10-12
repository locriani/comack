"""
Batch regenerate Spectra 6 color binary (.bin) files for all historical comic issues.
"""
import sys
import re
from pathlib import Path
from PIL import Image
from server.config import COMICS_DIR
from server.render.dither_spectra6 import dither_spectra6


def regenerate_all_color_bins():
    print(f"Scanning {COMICS_DIR} for comic issues...")
    
    # Find all issue numbers
    issues = set()
    for p in COMICS_DIR.glob("issue_*.png"):
        m = re.match(r"issue_(\d+)", p.stem)
        if m:
            issues.add(int(m.group(1)))
            
    sorted_issues = sorted(issues)
    print(f"Found {len(sorted_issues)} issues: {sorted_issues}")
    
    count = 0
    for issue in sorted_issues:
        color_png = COMICS_DIR / f"issue_{issue:04d}_color.png"
        bw_png = COMICS_DIR / f"issue_{issue:04d}.png"
        out_bin = COMICS_DIR / f"issue_{issue:04d}_color.bin"
        
        target_png = color_png if color_png.exists() else bw_png
        if not target_png.exists():
            continue
            
        is_color = color_png.exists()
        img = Image.open(target_png).convert("RGB")
        _, packed = dither_spectra6(img)
        
        with open(out_bin, "wb") as f:
            f.write(packed)
            
        tag = "COLOR" if is_color else "B&W"
        print(f"[{count+1}/{len(sorted_issues)}] Issue #{issue:04d} ({tag}) -> {len(packed)} bytes written to {out_bin.name}")
        count += 1
        
    print(f"Successfully regenerated {count} color binaries!")


if __name__ == "__main__":
    regenerate_all_color_bins()
