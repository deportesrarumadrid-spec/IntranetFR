import os
from PIL import Image

def crop_sections():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        static_dir = '../static'
        
    p1 = Image.open(os.path.join(static_dir, 'inscripcion_p1.png'))
    p2 = Image.open(os.path.join(static_dir, 'inscripcion_p2.png'))
    
    # Page 1 top
    p1.crop((0, 0, 634, 300)).save(os.path.join(static_dir, 'crop_p1_top.png'))
    # Page 1 bottom
    p1.crop((0, 598, 634, 898)).save(os.path.join(static_dir, 'crop_p1_bottom.png'))
    
    # Page 2 top
    p2.crop((0, 0, 634, 300)).save(os.path.join(static_dir, 'crop_p2_top.png'))
    # Page 2 bottom
    p2.crop((0, 600, 634, 900)).save(os.path.join(static_dir, 'crop_p2_bottom.png'))
    
    print("Cropped sections saved.")

if __name__ == '__main__':
    crop_sections()
