import os
import fitz  # PyMuPDF

def convert_pdf_to_png(pdf_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    print(f"Opened PDF: {pdf_path}, Pages: {len(doc)}")
    
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=150)
        out_name = f"test_pdf_page{i+1}.png"
        out_path = os.path.join(output_dir, out_name)
        pix.save(out_path)
        print(f"Saved PDF Page {i+1} to {out_path}")

if __name__ == '__main__':
    workspace_dir = r"c:\Users\depor\Desktop\Intranet_Club"
    pdf = os.path.join(workspace_dir, "static", "hojas_inscripcion", "TEST_JUAN_PEREZ_ALEVIN_A.pdf")
    static_dir = os.path.join(workspace_dir, "static")
    convert_pdf_to_png(pdf, static_dir)
