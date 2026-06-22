import os
from PIL import Image
from find_regions import get_bounding_boxes

def crop_regions_with_labels():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    crops_dir = os.path.join(static_dir, 'crops')
    os.makedirs(crops_dir, exist_ok=True)
    
    for p_idx in [1, 2]:
        p_path = os.path.join(static_dir, f'inscripcion_p{p_idx}.png')
        comps, w, h = get_bounding_boxes(p_path)
        
        img = Image.open(p_path).convert('RGB')
        
        for i, c in enumerate(comps):
            # Crop a region that starts 130 pixels to the left and extends to max_x
            # We also add 5 pixels of vertical margin
            left = max(0, c['min_x'] - 130)
            top = max(0, c['min_y'] - 6)
            right = min(w, c['max_x'] + 10)
            bottom = min(h, c['max_y'] + 6)
            
            crop_img = img.crop((left, top, right, bottom))
            out_name = f'page{p_idx}_region_{i+1:02d}.png'
            crop_img.save(os.path.join(crops_dir, out_name))
            print(f"Saved crop: {out_name} (left={left}, top={top}, right={right}, bottom={bottom})")

if __name__ == '__main__':
    crop_regions_with_labels()
