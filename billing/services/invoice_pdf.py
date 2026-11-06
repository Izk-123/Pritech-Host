import io
import os
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from django.utils import timezone

def generate_invoice_pdf(invoice):
    """
    Generates a PDF for a given Invoice object using ReportLab.
    Includes the Pritech Host logo and matches the sample UI.
    Returns: BytesIO object containing the PDF.
    """
    buffer = io.BytesIO()
    # Set margins to give the invoice breathing room
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    elements = []
    styles = getSampleStyleSheet()

    # Custom Styles
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=24, textColor=colors.HexColor('#16365F'))
    h2_style = ParagraphStyle(name='H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor('#0E2238'))
    normal_style = ParagraphStyle(name='Normal', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#5B6B7C'), leading=14)
    bold_style = ParagraphStyle(name='Bold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#0E2238'))
    
    def fmt(amount):
        return f"MWK {amount:,.2f}"

    # --- 1. Header Section (Logo + Title) ---
    is_paid = invoice.status == 'paid'
    doc_title = "RECEIPT" if is_paid else "INVOICE"
    
    # Load the logo (ensure it exists to prevent crashes)
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'pritech_host_logo_transparent.png')
    if os.path.exists(logo_path):
        # Adjust width/height to fit nicely in the header
        logo = Image(logo_path, width=2.2*inch, height=0.7*inch)
    else:
        logo = Paragraph("<b>PRITECH HOST</b>", title_style) # Fallback if logo is missing

    header_data = [
        [logo, Paragraph(f"<b>{doc_title}</b>", ParagraphStyle(name='DocTitle', parent=title_style, fontSize=28, alignment=2)),],
        ['', Paragraph(f"Date: {invoice.issue_date.strftime('%d/%m/%Y')}<br/>Invoice No: {invoice.invoice_number}", ParagraphStyle(name='RightAlign', parent=normal_style, alignment=2))]
    ]
    
    header_table = Table(header_data, colWidths=[4*inch, 3.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 20))

    # --- 2. Addresses Section ---
    customer = invoice.customer
    customer_details = f"""
    <b>{customer.first_name} {customer.last_name}</b><br/>
    {customer.email}<br/>
    {customer.phone_number or 'N/A'}<br/>
    {customer.profile.company_name if hasattr(customer, 'profile') and customer.profile.company_name else ''}
    """
    
    pritech_details = """
    <b>Pritech ICT Solutions</b><br/>
    Area 51/24 Kanengo, Lilongwe, Malawi<br/>
    +265 996 110 336<br/>
    info@pritechmw.com
    """
    
    addr_data = [
        [Paragraph("RECEIVED FROM", h2_style), Paragraph("PAID TO", h2_style)],
        [Paragraph(customer_details, normal_style), Paragraph(pritech_details, normal_style)]
    ]
    addr_table = Table(addr_data, colWidths=[3.75*inch, 3.75*inch])
    addr_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,0), 5),
    ]))
    elements.append(addr_table)
    elements.append(Spacer(1, 25))

    # --- 3. Items Table ---
    item_desc = "Web Hosting Services - " + (invoice.customer.subscription_set.first().plan.name if invoice.customer.subscription_set.exists() else "Hosting Package")
    
    table_data = [['Description', 'Qty', 'Unit Price', 'Total']]
    table_data.append([
        Paragraph(item_desc, normal_style), 
        '1.00', 
        fmt(invoice.subtotal_mwk), 
        fmt(invoice.subtotal_mwk)
    ])

    items_table = Table(table_data, colWidths=[4.2*inch, 0.8*inch, 1.25*inch, 1.25*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F7FAFD')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#16365F')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor('#E1EAF2')),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 15))

    # --- 4. Totals Section ---
    totals_data = [
        ['Subtotal', fmt(invoice.subtotal_mwk)],
        [f'VAT (17.5%)', fmt(invoice.tax_mwk)],
        ['Total', fmt(invoice.total_mwk)]
    ]
    totals_table = Table(totals_data, colWidths=[5.8*inch, 1.7*inch])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('TEXTCOLOR', (0,0), (-1,1), colors.HexColor('#5B6B7C')),
        ('TEXTCOLOR', (0,2), (-1,2), colors.HexColor('#16365F')),
        ('LINEABOVE', (0,2), (-1,2), 1, colors.HexColor('#16365F')),
        ('TOPPADDING', (0,2), (-1,2), 10),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 30))

    # --- 5. Payment Details Section (Only if paid) ---
    if is_paid:
        payment = invoice.payment_set.filter(status='success').first()
        if payment:
            pay_data = [
                [Paragraph('<b>Amount Paid</b>', ParagraphStyle(name='AmountPaid', parent=title_style, fontSize=16, textColor=colors.HexColor('#0F5F9E'))), ''],
                [Paragraph('<b>PAID IN FULL</b>', bold_style), Paragraph(f"Payment received for {invoice.invoice_number} on {payment.created_at.strftime('%d/%m/%Y')}", normal_style)]
            ]
            pay_table = Table(pay_data, colWidths=[3*inch, 4.5*inch])
            pay_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#E6F1FB')),
                ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#1478C4')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 12),
                ('BOTTOMPADDING', (0,0), (-1,-1), 12),
                ('LEFTPADDING', (0,0), (-1,-1), 15),
            ]))
            elements.append(pay_table)
            elements.append(Spacer(1, 20))

            # Payment Meta Table
            meta_data = [
                ['Payment Method', payment.get_payment_method_display()],
                ['Transaction ID', payment.gateway_reference],
                ['Date & Time', payment.created_at.strftime('%d %b %Y, %H:%M')],
            ]
            meta_table = Table(meta_data, colWidths=[2*inch, 5.5*inch])
            meta_table.setStyle(TableStyle([
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('TEXTCOLOR', (0,0), (0,-1), colors.HexColor('#16365F')),
                ('TEXTCOLOR', (1,0), (1,-1), colors.HexColor('#5B6B7C')),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(meta_table)
            elements.append(Spacer(1, 30))

    # --- 6. Footer / Terms ---
    terms = """
    <b>Terms & Instructions</b><br/><br/>
    Bank: National Bank of Malawi - Account: 1234567890<br/>
    Mobile Money: +265 888 123 456<br/><br/>
    Thank you for your business! Payment due within 30 days.
    """
    elements.append(Paragraph(terms, ParagraphStyle(name='Terms', parent=normal_style, fontSize=8, textColor=colors.HexColor('#9CA3AF'))))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer