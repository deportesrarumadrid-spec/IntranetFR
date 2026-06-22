import os
from PIL import Image

def inspect_sepa_structure(image_path, target_color=(221, 228, 255)):
    img = Image.open(image_path).convert('RGB')
    width, height = img.size
    
    # Let's count how many matching pixels are in each row from y=500 to y=898
    matching_rows = []
    for y in range(500, height):
        row_pixels = []
        for x in range(width):
            r, g, b = img.getpixel((x, y))
            if abs(r - target_color[0]) <= 2 and abs(g - target_color[1]) <= 2 and abs(b - target_color[2]) <= 2:
                row_pixels.append(x)
        if row_pixels:
            # Let's group contiguous segments in this row
            segments = []
            start = row_pixels[0]
            for i in range(1, len(row_pixels)):
                if row_pixels[i] > row_pixels[i-1] + 1:
                    segments.append((start, row_pixels[i-1]))
                    start = row_pixels[i]
            segments.append((start, row_pixels[-1]))
            matching_rows.append((y, segments))
            
    print(f"\n--- SEPA Pixel Rows for {image_path} ---")
    for y, segs in matching_rows:
        # Print only if y changes significantly or just print them all
        # To avoid too much output, we will print ranges of rows with similar segments
        print(f"Row y={y:03d}: {segs}")

if __name__ == '__main__':
    static_dir = os.path.join('..', 'static')
    if not os.path.exists(static_dir):
        static_dir = 'static'
    inspect_sepa_structure(os.path.join(static_dir, 'inscripcion_p1.png'))
