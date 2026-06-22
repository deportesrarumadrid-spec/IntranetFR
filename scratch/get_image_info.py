import os
from PIL import Image

def get_image_info():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    for p_idx in [1, 2]:
        p_path = os.path.join(static_dir, f'inscripcion_p{p_idx}.png')
        img = Image.open(p_path)
        print(f"Page {p_idx}: {p_path}, size={img.size}")
        
if __name__ == '__main__':
    get_image_info()
