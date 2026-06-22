import os
from PIL import Image

def crop_p1_end_regions():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    crops_dir = os.path.join(static_dir, 'crops')
    os.makedirs(crops_dir, exist_ok=True)
    
    p_path = os.path.join(static_dir, 'inscripcion_p1.png')
    img = Image.open(p_path).convert('RGB')
    w, h = img.size
    
    # We want to crop from y=250 to y=898
    # Let's crop Regions 16, 17, 18, 19, 20, 21 with a generous left margin of 150px
    from find_regions import get_bounding_boxes
    comps, _, _ = get_bounding_boxes(p_path)
    
    for idx in range(15, len(comps)):
        c = comps[idx]
        left = max(0, c['min_x'] - 150)
        top = max(0, c['min_y'] - 8)
        right = min(w, c['max_x'] + 10)
        bottom = min(h, c['max_y'] + 8)
        
        crop_img = img.crop((left, top, right, bottom))
        out_name = f'page1_region_{idx+1:02d}_labeled.png'
        crop_img.save(os.path.join(crops_dir, out_name))
        print(f"Saved: {out_name} (y={c['min_y']}..{c['max_y']}, x={c['min_x']}..{c['max_x']})")

if __name__ == '__main__':
    crop_p1_end_regions()
