"""
lead_pdf_generator.py — Sovereign Advisory Lead Brief PDF Generator
Generates a formal PDF with SA letterhead from lead research + AI analysis.
Called by the lead_review_server via /api/generate-pdf.
"""
import os
import io
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, Image, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import json

# ── Brand colours ──────────────────────────────────────────────────────────────
NAVY     = colors.HexColor('#0b0f1c')
COPPER   = colors.HexColor('#d4924a')
GOLD     = colors.HexColor('#f0a535')
CREAM    = colors.HexColor('#f0ece4')
TEXT2    = colors.HexColor('#ddd5c8')
DARKGREY = colors.HexColor('#3a3a4a')
WHITE    = colors.white

LOGO_PATH = Path(__file__).parent.parent / 'sovereign_advisory' / 'logo.png'


def build_styles():
    base = getSampleStyleSheet()

    return {
        'title': ParagraphStyle(
            'Title', fontName='Helvetica-Bold', fontSize=22,
            textColor=CREAM, spaceAfter=2, leading=26
        ),
        'subtitle': ParagraphStyle(
            'Subtitle', fontName='Helvetica', fontSize=10,
            textColor=GOLD, spaceAfter=0, leading=14, letterSpacing=1.5
        ),
        'section_head': ParagraphStyle(
            'SectionHead', fontName='Helvetica-Bold', fontSize=9,
            textColor=GOLD, spaceBefore=14, spaceAfter=4,
            leading=12, letterSpacing=2.0
        ),
        'body': ParagraphStyle(
            'Body', fontName='Helvetica', fontSize=10,
            textColor=DARKGREY, spaceAfter=6, leading=15
        ),
        'body_light': ParagraphStyle(
            'BodyLight', fontName='Helvetica', fontSize=9,
            textColor=colors.HexColor('#555566'), spaceAfter=4, leading=13
        ),
        'bullet': ParagraphStyle(
            'Bullet', fontName='Helvetica', fontSize=10,
            textColor=DARKGREY, spaceAfter=5, leading=15,
            leftIndent=14, firstLineIndent=-14
        ),
        'label': ParagraphStyle(
            'Label', fontName='Helvetica-Bold', fontSize=8,
            textColor=colors.HexColor('#888899'), spaceAfter=2,
            leading=10, letterSpacing=1.5
        ),
        'value': ParagraphStyle(
            'Value', fontName='Helvetica', fontSize=10,
            textColor=DARKGREY, spaceAfter=8, leading=14
        ),
        'footer': ParagraphStyle(
            'Footer', fontName='Helvetica', fontSize=8,
            textColor=colors.HexColor('#999aaa'), alignment=TA_CENTER
        ),
    }


def copper_rule(width=None):
    return HRFlowable(
        width=width or '100%',
        thickness=1.5,
        color=COPPER,
        spaceAfter=8,
        spaceBefore=4
    )


def thin_rule():
    return HRFlowable(
        width='100%',
        thickness=0.5,
        color=colors.HexColor('#ccccdd'),
        spaceAfter=6,
        spaceBefore=6
    )


def numbered_items(items: list, style) -> list:
    """Render a list as numbered bullet paragraphs."""
    result = []
    for i, item in enumerate(items, 1):
        result.append(Paragraph(f'<b>{i}.</b>  {item}', style))
    return result


def _parse(val):
    """Return val parsed from JSON if it's a string, otherwise return as-is."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return val
    return val


def generate_lead_pdf(lead: dict, draft: dict, output_path: str) -> str:
    """
    Generate a PDF lead brief and save to output_path.
    Returns the path on success.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = build_styles()
    story = []

    # ── Header bar (navy background table) ────────────────────────────────────
    logo_cell = ''
    if LOGO_PATH.exists():
        logo_img = Image(str(LOGO_PATH), width=1.1 * inch, height=1.1 * inch)
        logo_cell = logo_img

    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
    domain = lead.get('domain', lead.get('email', ''))
    date_str = datetime.now().strftime('%B %d, %Y')

    header_text = Table(
        [[
            logo_cell,
            [
                Paragraph('SOVEREIGN ADVISORY', ParagraphStyle(
                    'HdrTitle', fontName='Helvetica-Bold', fontSize=16,
                    textColor=CREAM, leading=20, letterSpacing=2
                )),
                Paragraph('LEAD INTELLIGENCE BRIEF', ParagraphStyle(
                    'HdrSub', fontName='Helvetica', fontSize=9,
                    textColor=GOLD, leading=12, letterSpacing=3
                )),
                Spacer(1, 6),
                Paragraph(f'Prepared: {date_str}', ParagraphStyle(
                    'HdrDate', fontName='Helvetica', fontSize=8,
                    textColor=TEXT2, leading=10
                )),
            ]
        ]],
        colWidths=[1.3 * inch, 5.7 * inch]
    )
    header_text.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    story.append(header_text)
    story.append(Spacer(1, 14))

    # ── Contact info row ───────────────────────────────────────────────────────
    contact_data = [
        [
            [
                Paragraph('PROSPECT', styles['label']),
                Paragraph(name or 'Unknown', styles['value']),
            ],
            [
                Paragraph('EMAIL', styles['label']),
                Paragraph(lead.get('email', '—'), styles['value']),
            ],
            [
                Paragraph('DOMAIN / COMPANY', styles['label']),
                Paragraph(domain or '—', styles['value']),
            ],
            [
                Paragraph('SERVICE INTEREST', styles['label']),
                Paragraph(lead.get('service_area', '—'), styles['value']),
            ],
        ]
    ]
    contact_table = Table(contact_data, colWidths=[1.7 * inch] * 4)
    contact_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#ddddee')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(contact_table)
    story.append(copper_rule())

    # ── Original Message ───────────────────────────────────────────────────────
    story.append(Paragraph('ORIGINAL MESSAGE', styles['section_head']))
    story.append(Paragraph(lead.get('message', '(no message provided)'), styles['body']))
    story.append(thin_rule())

    # ── Research Summary ───────────────────────────────────────────────────────
    research = _parse(lead.get('company_research') or lead.get('person_research') or {})
    research_text = research.get('summary', '') if isinstance(research, dict) else str(research)
    if research_text:
        story.append(Paragraph('COMPANY / PROSPECT RESEARCH', styles['section_head']))
        story.append(Paragraph(research_text, styles['body']))
        story.append(thin_rule())

    # ── Strategic Summary + Approach ──────────────────────────────────────────
    if lead.get('summary'):
        story.append(Paragraph('STRATEGIC SUMMARY', styles['section_head']))
        story.append(Paragraph(lead['summary'], styles['body']))

    if lead.get('approach'):
        story.append(Paragraph('RECOMMENDED APPROACH', styles['section_head']))
        story.append(Paragraph(lead['approach'], styles['body']))
        story.append(thin_rule())

    # ── Conversation Starters / Questions / Scenarios ─────────────────────────
    three_cols = []

    starters = _parse(lead.get('conversation_starters')) or []
    questions = _parse(lead.get('questions')) or []
    scenarios = _parse(lead.get('scenarios')) or []

    def col_block(heading, items, style):
        block = [Paragraph(heading, styles['section_head'])]
        block += numbered_items(items, styles['bullet'])
        return block

    if starters or questions or scenarios:
        col_data = [[
            col_block('CONVERSATION STARTERS', starters, styles['bullet']),
            col_block('KEY QUESTIONS TO ASK',  questions, styles['bullet']),
            col_block('SUPPORT SCENARIOS',      scenarios, styles['bullet']),
        ]]
        col_table = Table(col_data, colWidths=[2.3 * inch] * 3)
        col_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('LINEAFTER', (0, 0), (1, -1), 0.5, colors.HexColor('#ddddee')),
        ]))
        story.append(KeepTogether([copper_rule(), col_table]))

    # ── Proposed Email Draft ───────────────────────────────────────────────────
    if draft.get('body_text'):
        story.append(Spacer(1, 10))
        story.append(copper_rule())
        story.append(Paragraph('PROPOSED OUTREACH EMAIL', styles['section_head']))
        story.append(Paragraph(f"<b>Subject:</b> {draft.get('subject', '')}", styles['body']))
        story.append(Spacer(1, 4))
        # Render email body preserving line breaks
        for line in draft.get('body_text', '').split('\n'):
            story.append(Paragraph(line or '&nbsp;', styles['body_light']))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width='100%', thickness=1, color=NAVY, spaceAfter=6))
    story.append(Paragraph(
        'Sovereign Advisory · contact@sovereignadvisory.ai · +1 276 880 5651 · sovereignadvisory.ai',
        styles['footer']
    ))
    story.append(Paragraph(
        f'CONFIDENTIAL — For internal use only · Generated {date_str}',
        styles['footer']
    ))

    doc.build(story)
    return output_path


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    sample_lead = {
        'first_name': 'Jane', 'last_name': 'Smith',
        'email': 'jane@acmecorp.com', 'domain': 'acmecorp.com',
        'service_area': 'Fractional CTO',
        'message': 'We are a 40-person SaaS company struggling with our engineering roadmap after rapid growth. We need strategic technology leadership.',
        'summary': 'Acme Corp is a high-growth SaaS company facing classic scaling challenges: technical debt, team structure, and roadmap prioritisation. They have the revenue to invest in leadership but need someone who can move fast.',
        'approach': 'Lead with the technical debt angle — frame it as a growth enabler rather than a liability. Offer a rapid 2-week assessment as a low-risk entry point.',
        'conversation_starters': [
            'What does your current engineering team structure look like, and where are the biggest bottlenecks?',
            'Has technical debt started affecting your ability to ship new features on schedule?',
            'What does a successful technology transformation look like for Acme Corp in 12 months?',
        ],
        'questions': [
            'Who is currently making technology architecture decisions, and what is their background?',
            'What is the ratio of new feature work to maintenance/firefighting in a typical sprint?',
            'Are there any upcoming funding rounds or acquisitions that create a technology readiness deadline?',
        ],
        'scenarios': [
            'Fractional CTO engagement (10 hrs/week) to own roadmap and guide the engineering team.',
            'Technical due diligence ahead of a Series B raise — ensuring the stack and team are investor-ready.',
            'Engineering team restructure and hiring strategy to support 3x headcount growth over 18 months.',
        ],
        'company_research': {'summary': 'Acme Corp is a B2B SaaS platform founded in 2019, ~$8M ARR, 40 employees. Serves mid-market logistics companies with route optimisation software. Known for rapid product iteration but reported technical challenges post-Series A. CTO departed in Q3 2025.'},
    }
    sample_draft = {
        'subject': 'Re: Technology Leadership for Acme Corp',
        'body_text': 'Dear Jane,\n\nThank you for reaching out to Sovereign Advisory.\n\nYour message resonated immediately — the challenges you described around engineering roadmap clarity and technology leadership during rapid growth are exactly the situations we are built to address.\n\nI would welcome the opportunity for a brief 30-minute conversation to understand where things stand and explore whether there is a fit. No agenda, no pitch — just a direct, practical conversation about what Acme Corp needs technology to do next.\n\nWould any time this week or next work for you?\n\nWarm regards,\nRobert Elder\nCEO, Sovereign Advisory',
    }
    out = generate_lead_pdf(sample_lead, sample_draft, '/tmp/test_lead_brief.pdf')
    print(f'PDF written to: {out}')
