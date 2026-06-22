import os
from PIL import Image

def get_bounding_boxes(image_path, target_color=(221, 228, 255)):
    img = Image.open(image_path).convert('RGB')
    width, height = img.size
    
    # Create a binary mask of matching pixels
    mask = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            r, g, b = img.getpixel((x, y))
            # Match target color exactly or with tiny tolerance
            if abs(r - target_color[0]) <= 2 and abs(g - target_color[1]) <= 2 and abs(b - target_color[2]) <= 2:
                mask[y][x] = 1
                
    # Find connected components (BFS/DFS)
    visited = [[False for _ in range(width)] for _ in range(height)]
    components = []
    
    for y in range(height):
        for x in range(width):
            if mask[y][x] == 1 and not visited[y][x]:
                # Start a new component
                queue = [(x, y)]
                visited[y][x] = True
                comp_pixels = []
                
                while queue:
                    curr_x, curr_y = queue.pop(0)
                    comp_pixels.append((curr_x, curr_y))
                    
                    # 4-connectivity
                    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nx, ny = curr_x + dx, curr_y + dy
                        if 0 <= nx < width and 0 <= ny < height:
                            if mask[ny][nx] == 1 and not visited[ny][nx]:
                                visited[ny][nx] = True
                                queue.append((nx, ny))
                                
                # Calculate bounding box
                xs = [p[0] for p in comp_pixels]
                ys = [p[1] for p in comp_pixels]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                
                # Filter out very small components (noise)
                w = max_x - min_x + 1
                h = max_y - min_y + 1
                if w >= 4 and h >= 4:
                    components.append({
                        'min_x': min_x,
                        'max_x': max_x,
                        'min_y': min_y,
                        'max_y': max_y,
                        'w': w,
                        'h': h,
                        'area': len(comp_pixels)
                    })
                    
    # Sort components by top, then left
    components.sort(key=lambda c: (c['min_y'], c['min_x']))
    return components, width, height

if __name__ == '__main__':
    static_dir = os.path.join('..', 'static')
    if not os.path.exists(static_dir):
        static_dir = 'static'
        
    for p_idx in [1, 2]:
        p_path = os.path.join(static_dir, f'inscripcion_p{p_idx}.png')
        comps, w, h = get_bounding_boxes(p_path)
        print(f"\n--- PAGE {p_idx} ({w}x{h}) ---")
        for i, c in enumerate(comps):
            left_pct = (c['min_x'] / w) * 100
            top_pct = (c['min_y'] / h) * 100
            width_pct = (c['w'] / w) * 100
            height_pct = (c['h'] / h) * 100
            print(f"Region {i+1}: "
                  f"x={c['min_x']}..{c['max_x']} (left: {left_pct:.2f}%, width: {width_pct:.2f}%), "
                  f"y={c['min_y']}..{c['max_y']} (top: {top_pct:.2f}%, height: {height_pct:.2f}%), "
                  f"size: {c['w']}x{c['h']}")
