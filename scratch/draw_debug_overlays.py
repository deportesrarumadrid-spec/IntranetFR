import os
from PIL import Image, ImageDraw, ImageFont
from find_regions import get_bounding_boxes

def draw_debug_regions():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    font_path = r"C:\Windows\Fonts\arial.ttf"
    if os.path.exists(font_path):
        font = ImageFont.truetype(font_path, 10)
    else:
        font = ImageFont.load_default()
        
    for p_idx in [1, 2]:
        p_path = os.path.join(static_dir, f'inscripcion_p{p_idx}.png')
        comps, w, h = get_bounding_boxes(p_path)
        
        # Load template
        img = Image.open(p_path).convert('RGB')
        draw = ImageDraw.Draw(img)
        
        for i, c in enumerate(comps):
            # Draw red rectangle
            draw.rectangle([c['min_x'], c['min_y'], c['max_x'], c['max_y']], outline=(255, 0, 0), width=1)
            # Draw text label inside or near it
            draw.text((c['min_x'] + 2, c['min_y'] + 2), str(i+1), fill=(255, 0, 0), font=font)
            
        out_path = os.path.join(static_dir, f'debug_p{p_idx}.png')
        img.save(out_path)
        print(f"Saved debug image: {out_path}")

if __name__ == '__main__':
    draw_debug_regions()
