import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle, PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from PIL import Image as PILImage

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BASE_DIR = "."                         # المجلد الرئيسي الذي يحتوي على المجاميع/المجلدات الفرعية
OUTPUT_PDF = "Images_Catalog.pdf"     # اسم ملف الـ PDF الناتج
GRID_COLS = 3                         # عدد العمود في كل صف بالكتالوج
IMAGES_PER_PAGE = 6                   # عدد الصور في كل صفحة (3 columns x 2 rows)

# ----------------------------------------------------------------------
# Numbered Canvas for Page Budgeting and Dynamic Footers
# ----------------------------------------------------------------------
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748b"))
        
        # Draw header / footer separators
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        
        # Header line & text
        self.line(18 * mm, 282 * mm, 192 * mm, 282 * mm)
        self.drawString(18 * mm, 284 * mm, "Product Catalog & Visual Index")
        
        # Footer line & page numbers
        self.line(18 * mm, 15 * mm, 192 * mm, 15 * mm)
        self.drawString(18 * mm, 10 * mm, "Confidential & Proprietary")
        
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(192 * mm, 10 * mm, page_str)
        self.restoreState()

# ----------------------------------------------------------------------
# Main Script Execution
# ----------------------------------------------------------------------
def scan_directory_for_images(base_path):
    """
    يقوم بفحص المجلدات الفرعية والبحث عن الصورة 1.jpg أو أي امتداد صورة رئيسي
    """
    catalog_items = []
    
    if not os.path.exists(base_path):
        print(f"المجلد {base_path} غير موجود.")
        return catalog_items

    folders = sorted([f for f in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, f))])
    
    for folder_name in folders:
        folder_path = os.path.join(base_path, folder_name)
        
        # البحث عن 1.jpg أو 1.png أو 1.jpeg
        target_image = None
        for ext in ["1.jpg", "1.jpeg", "1.png", "1.JPG", "1.PNG"]:
            img_path = os.path.join(folder_path, ext)
            if os.path.exists(img_path):
                target_image = img_path
                break
                
        if target_image:
            catalog_items.append({
                'title': folder_name,
                'image_path': target_image
            })
            
    return catalog_items

def build_pdf_catalog(catalog_items, output_filename):
    """
    إنشاء الكتالوج بتنسيق شبكي وتوزيع للصفحات متناسق ودقيق
    """
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        'CatalogTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=4,
        alignment=0
    )
    
    subtitle_style = ParagraphStyle(
        'CatalogSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#475569'),
        spaceAfter=15
    )
    
    card_title_style = ParagraphStyle(
        'CardTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12,
        textColor=colors.HexColor('#1e293b'),
        alignment=1 # Centered
    )

    story = []

    # Banner Title Masthead
    story.append(Paragraph("IMAGE CATALOGUE", title_style))
    story.append(Paragraph(f"Extracted Directory Collection • Total Items: {len(catalog_items)}", subtitle_style))

    if not catalog_items:
        story.append(Paragraph("لم يتم العثور على أي مجلدات تحتوي على الملف 1.jpg", subtitle_style))
        doc.build(story, canvasmaker=NumberedCanvas)
        return

    # Build Grid Cards
    cards = []
    card_width = (210 - 36 - 12) / 3 * mm  # A4 width (210) - margins (36) - gaps / 3
    card_height = 80 * mm

    for item in catalog_items:
        # Process Image Aspect Ratio using PIL safely
        try:
            with PILImage.open(item['image_path']) as PIL_img:
                img_w, img_h = PIL_img.size
                aspect = img_h / float(img_w)
        except Exception as e:
            print(f"خطأ في قراءة الصورة {item['image_path']}: {e}")
            continue

        # Constrain inside cell box
        max_img_w = card_width - (10 * mm)
        max_img_h = 55 * mm
        
        display_w = max_img_w
        display_h = display_w * aspect
        
        if display_h > max_img_h:
            display_h = max_img_h
            display_w = display_h / aspect

        img_element = RLImage(item['image_path'], width=display_w, height=display_h)
        title_element = Paragraph(item['title'], card_title_style)

        # Inner Card Table Box
        card_content = [
            [img_element],
            [Spacer(1, 4 * mm)],
            [title_element]
        ]
        
        card_table = Table(card_content, colWidths=[card_width])
        card_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
            ('ROUNDEDCORNERS', [4, 4, 4, 4]),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 4),
            ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ]))
        
        cards.append(card_table)

    # Chunk Cards into rows of 3
    rows = [cards[i:i + GRID_COLS] for i in range(0, len(cards), GRID_COLS)]
    
    # Process pages (2 rows per page = 6 items max)
    grid_cells = []
    for row in rows:
        # Fill empty cells if last row has less than 3 items
        while len(row) < GRID_COLS:
            row.append("")
        grid_cells.append(row)

    # Chunk grid rows into sets of 2 rows per page
    pages_grid = [grid_cells[i:i + 2] for i in range(0, len(grid_cells), 2)]

    for page_idx, page_rows in enumerate(pages_grid):
        table_grid = Table(page_rows, colWidths=[card_width + 4*mm]*GRID_COLS)
        table_grid.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10 * mm),
        ]))
        
        story.append(table_grid)
        
        # Add Page Break between pages
        if page_idx < len(pages_grid) - 1:
            story.append(PageBreak())

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"تم إنشاء الكتالوج بنجاح: {output_filename}")

if __name__ == "__main__":
    # 1. فحص المجلدات واستخراج الصور
    items = scan_directory_for_images(BASE_DIR)
    
    # 2. بناء ملف الـ PDF
    if items:
        build_pdf_catalog(items, OUTPUT_PDF)
    else:
        print("لم يتم العثور على أي مجلد يحتوي على الصورة المطلوب (1.jpg)")