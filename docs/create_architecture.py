from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap

OUT = Path(__file__).resolve().parent / "architecture.png"
W, H = 1600, 1050

img = Image.new("RGB", (W, H), "#F7F9FC")
draw = ImageDraw.Draw(img)

FONT_CANDIDATES = {
    False: [
        r"C:\Windows\Fonts\arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    True: [
        r"C:\Windows\Fonts\arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}
def get_font(size, bold=False):
    for path in FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

title_font = get_font(38, True)
subtitle_font = get_font(20)
box_title_font = get_font(24, True)
body_font = get_font(18)
small_font = get_font(15)

def centered(text, x1, y1, x2, y2, font, fill="#172033"):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x1 + (x2-x1-tw)/2, y1 + (y2-y1-th)/2),
              text, font=font, fill=fill)

def box(x1, y1, x2, y2, title, lines, fill, outline="#CBD5E1"):
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18,
                           fill=fill, outline=outline, width=2)
    centered(title, x1+12, y1+14, x2-12, y1+55, box_title_font)
    yy = y1 + 68
    for line in lines:
        centered(line, x1+14, yy, x2-14, yy+30, body_font, "#334155")
        yy += 31

def arrow(points, color="#64748B", width=4, head=12):
    draw.line(points, fill=color, width=width, joint="curve")
    x1, y1 = points[-2]
    x2, y2 = points[-1]
    import math
    angle = math.atan2(y2-y1, x2-x1)
    left = (x2-head*math.cos(angle-0.55), y2-head*math.sin(angle-0.55))
    right = (x2-head*math.cos(angle+0.55), y2-head*math.sin(angle+0.55))
    draw.polygon([(x2,y2), left, right], fill=color)

centered("ActionBridge AI \u2014 System Architecture", 40, 25, W-40, 80, title_font)
centered("Backend and frontend implemented and tested locally; live cloud integration requires verification",
         40, 78, W-40, 115, subtitle_font, "#475569")

# User and frontend
box(510, 145, 1090, 255, "User / Browser",
    ["Interacts with the assistant and reviews proposed changes"],
    "#E8F1FF")
box(510, 310, 1090, 445, "Frontend",
    ["React + Vite + TypeScript + plain CSS", "Chat, commitments, plan, conflicts, confirmation"],
    "#DFF7E8", "#8BC9A2")
arrow([(800,255),(800,310)])
centered("User interaction", 825, 267, 1010, 300, small_font, "#64748B")

# Backend
box(430, 500, 1170, 635, "FastAPI Backend",
    ["REST API: chat, commitments, plan, conflicts, change review",
     "main.py  •  services.py  •  agent.py"],
    "#DFF7E8", "#8BC9A2")
arrow([(800,445),(800,500)])
centered("HTTP / JSON", 820, 455, 960, 488, small_font, "#64748B")

# Three downstream components
box(55, 720, 500, 875, "Agent / LLM",
    ["Agent orchestration", "Amazon Bedrock Converse API", "Live access not yet verified"],
    "#EDE9FE", "#B6A6ED")
box(570, 720, 1030, 875, "MCP Tools",
    ["Python FastMCP server", "Exposes actions for the agent", "Tool flow needs live verification"],
    "#E0F2FE", "#8BC8E8")
box(1100, 720, 1545, 875, "SQLite Storage",
    ["Commitments and plan data", "Accessed by backend services", "DB_PATH selects database file"],
    "#FCE7F3", "#E6A9C7")

arrow([(550,635),(300,720)])
arrow([(800,635),(800,720)])
arrow([(1050,635),(1320,720)])

centered("LLM requests / responses", 250, 660, 565, 698, small_font)
centered("Tool execution", 665, 660, 930, 698, small_font)
centered("Read / write data", 1110, 660, 1350, 698, small_font)

# Legend / footer
draw.rounded_rectangle((55, 920, 1545, 1005), radius=14,
                       fill="#FFFFFF", outline="#D8DEE8", width=2)
draw.text((80, 938), "Status key", font=get_font(18, True), fill="#172033")
draw.rounded_rectangle((220, 941, 245, 963), radius=4,
                       fill="#DFF7E8", outline="#8BC9A2")
draw.text((255, 940), "Implemented / tested locally", font=small_font, fill="#334155")
draw.rounded_rectangle((540, 941, 565, 963), radius=4,
                       fill="#FFF4D6", outline="#E7C875")
draw.text((575, 940), "Live cloud integration / credentials pending verification", font=small_font, fill="#334155")
draw.text((80, 972),
          "Architecture reflects the repository state; frontend and backend are implemented and tested locally, while live cloud credentials and inference are not confirmed.",
          font=small_font, fill="#64748B")

img.save(OUT)
print(f"Created: {OUT}")
print(f"Image size: {img.size[0]} x {img.size[1]}")
print(f"File size: {OUT.stat().st_size:,} bytes")
