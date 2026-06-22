import os
from PIL import Image, ImageDraw, ImageFont

def draw_grid():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    p2_path = os.path.join(static_dir, 'inscripcion_p2.png')
    img = Image.open(p2_path).convert('RGB')
    width, height = img.size
    
    draw = ImageDraw.Draw(img)
    
    # Load font
    font_path = r"C:\Windows\Fonts\arial.ttf"
    if os.path.exists(font_path):
        font = ImageFont.truetype(font_path, 10)
    else:
        font = ImageFont.load_default()
        
    # Draw vertical grid lines
    for x in range(0, width, 50):
        draw.line([(x, 0), (x, height)], fill=(255, 0, 0), width=1)
        draw.text((x + 2, 5), str(x), fill=(255, 0, 0), font=font)
        draw.text((x + 2, height - 15), str(x), fill=(255, 0, 0), font=font)
        
    # Draw horizontal grid lines
    for y in range(0, height, 50):
        draw.line([(0, y), (width, y)], fill=(255, 0, 0), width=1)
        draw.text((5, y + 2), str(y), fill=(255, 0, 0), font=font)
        draw.text((width - 30, y + 2), str(y), fill=(255, 0, 0), font=font)
        
    out_path = os.path.join(static_dir, 'grid_p2.png')
    img.save(out_path)
    print(f"Saved grid image: {out_path} ({width}x{height})")

if __name__ == '__main__':
    draw_grid()
