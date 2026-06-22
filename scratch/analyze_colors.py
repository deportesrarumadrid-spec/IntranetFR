import os
from PIL import Image

def find_colored_regions(image_path):
    img = Image.open(image_path).convert('RGBA')
    width, height = img.size
    print(f"Image: {image_path}, Size: {width}x{height}")
    
    # We want to identify the non-white/non-black/non-gray colors, specifically blues/purples
    # Let's count the frequency of colors to see what the blue/purple color values look like.
    colors = {}
    for y in range(height):
        for x in range(width):
            r, g, b, a = img.getpixel((x, y))
            # Filter out white (very bright) and gray/black (where r, g, b are very close)
            # Let's target blue/purple: b should be significantly higher than g, or r & b are both higher than g
            if a > 0:
                # If it's a blue/purple hue:
                if b > g + 20 or (r > g + 20 and b > g + 20):
                    color_key = (r, g, b)
                    colors[color_key] = colors.get(color_key, 0) + 1
                    
    sorted_colors = sorted(colors.items(), key=lambda x: x[1], reverse=True)
    print("Top colored pixel values (RGB, count):")
    for col, count in sorted_colors[:15]:
        print(f"  {col}: {count}")

if __name__ == '__main__':
    static_dir = os.path.join('..', 'static')
    if not os.path.exists(static_dir):
        static_dir = 'static'
    find_colored_regions(os.path.join(static_dir, 'inscripcion_p1.png'))
    find_colored_regions(os.path.join(static_dir, 'inscripcion_p2.png'))
