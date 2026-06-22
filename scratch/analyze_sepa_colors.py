import os
from PIL import Image

def analyze_sepa_pixels(image_path):
    img = Image.open(image_path).convert('RGB')
    width, height = img.size
    
    # We will print the colors of pixels in rows 600 to 850
    # to see if there are any specific shaded input fields.
    colors = {}
    for y in range(500, height):
        for x in range(width):
            r, g, b = img.getpixel((x, y))
            # Find colors that are not white/black/gray
            if abs(r - g) > 5 or abs(g - b) > 5:
                color_key = (r, g, b)
                colors[color_key] = colors.get(color_key, 0) + 1
                
    sorted_colors = sorted(colors.items(), key=lambda x: x[1], reverse=True)
    print(f"\n--- SEPA colors in {image_path} ---")
    for col, count in sorted_colors[:20]:
        print(f"  {col}: {count}")

if __name__ == '__main__':
    static_dir = os.path.join('..', 'static')
    if not os.path.exists(static_dir):
        static_dir = 'static'
    analyze_sepa_pixels(os.path.join(static_dir, 'inscripcion_p1.png'))
    analyze_sepa_pixels(os.path.join(static_dir, 'inscripcion_p2.png'))
