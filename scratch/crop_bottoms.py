import os
from PIL import Image

def crop_bottoms():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    for p_idx in [1, 2]:
        p_path = os.path.join(static_dir, f'inscripcion_p{p_idx}.png')
        img = Image.open(p_path).convert('RGB')
        w, h = img.size
        # Crop the bottom 250 pixels
        crop_img = img.crop((0, h - 250, w, h))
        crop_img.save(os.path.join(static_dir, f'bottom_p{p_idx}.png'))
        print(f"Saved bottom crop for Page {p_idx} (height={h})")

if __name__ == '__main__':
    crop_bottoms()
